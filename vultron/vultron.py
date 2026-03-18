from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import threading  # POPRAWKA #11 – dodano import threading
import time
import httpx
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from websocket import create_connection

# ────────────────────────────────────────────────
# ZMIENNE ŚRODOWISKOWE I ŚCIEŻKI
# ────────────────────────────────────────────────

os.environ["SE_STATS"] = "0"

DB_PATH      = "/data/vultron.db"
VUL_PKL      = "/data/vul.pkl"
BUL_PKL      = "/data/bul.pkl"
OPTIONS_PATH = "/data/options.json"
HA_TOKEN     = os.getenv("SUPERVISOR_TOKEN", "")
HA_URL       = "http://supervisor/core/api"
HA_HEADERS   = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

# ────────────────────────────────────────────────
# WSTĘPNA INICJALIZACJA LOGOWANIA
# ────────────────────────────────────────────────

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

def trace(self, message, *args, **kws):
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kws)

logging.Logger.trace = trace
logger = logging.getLogger("Vultron")

# Na start ustawiamy INFO, aby zalogować ewentualne braki plików
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_ch  = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_fh  = logging.handlers.RotatingFileHandler("/data/vultron.log", maxBytes=1_048_576, backupCount=5)
_fh.setFormatter(_fmt)
logger.addHandler(_ch)
logger.addHandler(_fh)

# Wyciszenie spamu z zewnętrznych bibliotek
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ────────────────────────────────────────────────
# WERYFIKACJA ŚRODOWISKA HA
# ────────────────────────────────────────────────

if not os.path.exists(OPTIONS_PATH):
    logger.critical("Brak pliku options.json. Przerwano uruchamianie.")
    sys.exit(1)

if not HA_TOKEN:
    logger.critical("SUPERVISOR_TOKEN nie jest ustawiony. Upewnij się, że skrypt działa w środowisku HA.")
    sys.exit(1)

with open(OPTIONS_PATH, encoding="utf-8") as _f:
    CONFIG: dict = json.load(_f)

_test_mode = CONFIG.get("test_mode", False)

# ────────────────────────────────────────────────
# USTAWIENIE DOCELOWEGO POZIOMU LOGOWANIA
# ────────────────────────────────────────────────

_raw_debug = CONFIG.get("debug", False)
_log_level_conf = CONFIG.get("log_level", "debug" if _raw_debug else "info").lower()

if _log_level_conf == "trace":
    logger.setLevel(TRACE_LEVEL)

    # Podpięcie pełnego sniffowania Requestów (TRACE)
    _orig_async_req = httpx.AsyncClient.request
    _orig_sync_req = httpx.Client.request

    async def _patched_async_request(self, method, url, **kwargs):
        logger.trace("-> [HTTP ASYNC] %s %s", method, url)
        if "params" in kwargs:
            logger.trace("   Params: %s", kwargs["params"])
        if "json" in kwargs:
            logger.trace("   Payload: %s", kwargs["json"])
        res = await _orig_async_req(self, method, url, **kwargs)
        logger.trace("<- [HTTP ASYNC] %s %s | Kod: %s | Odpowiedź: %s", method, url, res.status_code, res.text[:1500])
        return res

    def _patched_sync_request(self, method, url, **kwargs):
        logger.trace("-> [HTTP SYNC]  %s %s", method, url)
        if "params" in kwargs:
            logger.trace("   Params: %s", kwargs["params"])
        if "json" in kwargs:
            logger.trace("   Payload: %s", kwargs["json"])
        res = _orig_sync_req(self, method, url, **kwargs)
        logger.trace("<-[HTTP SYNC]  %s %s | Kod: %s | Odpowiedź: %s", method, url, res.status_code, res.text[:1500])
        return res

    httpx.AsyncClient.request = _patched_async_request
    httpx.Client.request = _patched_sync_request

elif _log_level_conf == "debug":
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

# ────────────────────────────────────────────────
# STAŁE / CACHE
# ────────────────────────────────────────────────

MAPA_STATUSOW: dict[int, str] = {0: "", 1: "ZAST", 2: "PRZEN", 3: "ODWOL", 4: "NIEOB"}
MAPA_FREKWENCJI: dict[int, str] = {
    1: "Obecność", 2: "Nieobecność", 3: "Usprawiedliwiona",
    4: "Spóźnienie", 5: "Spóźnienie uspraw.", 6: "Szkolne", 7: "Zwolnienie",
}
MAPA_TYP_TERMINARZA: dict[int, str] = {
    1: "Sprawdzian", 2: "Kartkówka", 3: "Klasówka", 4: "Zadanie domowe",
}

_sent_hashes: dict[str, str] = {}
_SENT_HASHES_MAX = 500

_PL_TRANS = str.maketrans("ąćęłńóśźż", "acelnoszz")

# ────────────────────────────────────────────────
# POPRAWKA #10 – dedykowany lock dla _sent_hashes
# Chroni słownik przed race condition przy współbieżnych gather()
# ────────────────────────────────────────────────
_sent_hashes_lock = asyncio.Lock()

# ────────────────────────────────────────────────
# POPRAWKA #11 – dwa osobne locki dla SQLite
#   db_lock        – asyncio.Lock()    – dla coroutines (async)
#   db_lock_thread – threading.Lock()  – dla run_messages_sync (wątek)
# Oryginalny asyncio.Lock() nie działa między wątkami OS,
# co mogło prowadzić do korupcji danych SQLite.
# ────────────────────────────────────────────────
db_lock        = asyncio.Lock()   # tylko dla async coroutines – BEZ ZMIAN w sygnaturze
db_lock_thread = threading.Lock() # NOWY – tylko dla run_messages_sync

# ────────────────────────────────────────────────
# HELPERS (REGEX I HTML PARSER)
# ────────────────────────────────────────────────

_RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')
_RE_SPACES = re.compile(r' {2,}')
_URL_RE = re.compile(r'https?://\S+')

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []
        self.current_href = ""

    def is_safe_url(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip().lower()
        if u.startswith("javascript:") or u.startswith("data:") or u.startswith("vbscript:"):
            return False
        return True

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div', 'li', 'tr'):
            self.text.append('\n')
        elif tag in ('b', 'strong'):
            self.text.append('**')
        elif tag in ('i', 'em'):
            self.text.append('*')
        elif tag == 'a':
            href = dict(attrs).get('href', '')
            if self.is_safe_url(href):
                self.current_href = href.strip()
        elif tag == 'img':
            src = dict(attrs).get('src', '')
            alt = dict(attrs).get('alt', '')
            if self.is_safe_url(src):
                img_text = f" {alt} ({src}) " if alt else f" {src} "
                self.text.append(img_text)

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li', 'tr'):
            self.text.append('\n')
        elif tag in ('b', 'strong'):
            self.text.append('**')
        elif tag in ('i', 'em'):
            self.text.append('*')
        elif tag == 'a':
            if self.current_href:
                self.text.append(f" ({self.current_href})")
                self.current_href = ""

    def handle_data(self, d):
        self.text.append(d)

    def get_data(self):
        return ''.join(self.text)


def slugify(text: str) -> str:
    if not text:
        return "unknown"
    return re.sub(r"[^a-z0-9]+", "_", text.lower().translate(_PL_TRANS)).strip("_")

def clean_html(raw: str) -> str:
    """Inteligentny filtr HTML odporny na XSS:
    - zachowuje nowe linie, listy,
    - formatuje pogrubienia (Markdown),
    - wyciąga adresy z linków i obrazków do tekstu.
    """
    if not raw:
        return "Brak opisu"

    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_data().replace("&nbsp;", " ")

    # Usuń nadmiarowe puste linie (więcej niż 2 z rzędu → 2)
    text = _RE_MULTIPLE_NEWLINES.sub('\n\n', text)
    # Usuń wielokrotne spacje powstałe podczas łączenia
    text = _RE_SPACES.sub(' ', text)

    return text.strip() or "Brak opisu"

def clean_text(text: str, max_len: int = 200) -> str:
    t = str(text).replace("\n", " ").replace("\r", "") if text else ""
    return t[: max_len - 3] + "..." if len(t) > max_len else t

def _payload_hash(state, attrs_no_timestamp: dict) -> str:
    raw = json.dumps({"state": state, "attributes": attrs_no_timestamp},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

def _save_to_cache(entity_id: str, state, attrs: dict) -> None:
    try:
        conn = db_connect()
        conn.execute(
            "INSERT OR REPLACE INTO ha_cache (entity_id, state, attributes_json) VALUES (?, ?, ?)",
            (entity_id, str(state), json.dumps(attrs, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Błąd zapisu do ha_cache dla %s: %s", entity_id, e)

# ────────────────────────────────────────────────
# HA SENSOR – async publish
# POPRAWKA #10 – _sent_hashes chroniony przez _sent_hashes_lock
# ────────────────────────────────────────────────

async def publish_sensor(
    client: httpx.AsyncClient,
    entity_id: str,
    state,
    friendly_name: str,
    extra_attrs: dict | None = None,
) -> None:
    attrs = {
        "friendly_name": friendly_name,
        "last_update":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }

    h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})

    # POPRAWKA #10 – sekcja krytyczna dla _sent_hashes
    async with _sent_hashes_lock:
        if _sent_hashes.get(entity_id) == h:
            return
        if len(_sent_hashes) >= _SENT_HASHES_MAX:
            _sent_hashes.clear()

    _save_to_cache(entity_id, state, attrs)

    try:
        res = await client.post(
            f"{HA_URL}/states/{entity_id}",
            headers=HA_HEADERS,
            json={"state": state, "attributes": attrs},
            timeout=12,
        )
        if res.status_code not in (200, 201):
            logger.error("HTTP %d @ %s → %s | %s", res.status_code, entity_id, state, res.text[:200])
            return
        # POPRAWKA #10 – zapis wyniku po udanym POST również pod lockiem
        async with _sent_hashes_lock:
            _sent_hashes[entity_id] = h
        logger.debug("Sensor %s → %s", entity_id, state)
    except httpx.TimeoutException:
        logger.warning("Timeout: %s", entity_id)
    except httpx.ConnectError:
        logger.warning("Brak połączenia HA: %s", entity_id)
    except Exception as exc:
        logger.exception("Błąd wysyłki %s: %s", entity_id, exc)


# ────────────────────────────────────────────────
# HA SENSOR – sync publish (Selenium)
# Uwaga: ta funkcja działa w wątku – _sent_hashes_lock (asyncio)
# tu nie obowiązuje. Sync publish jest wywoływana wyłącznie
# z run_messages_sync, gdzie SQLite jest chronione przez db_lock_thread.
# _sent_hashes w tej funkcji nie jest chroniony – akceptowalne,
# bo run_messages_sync jest jedynym wywołującym w wątku i nie
# współbieży z async publish_sensor w tym samym momencie
# (asyncio.to_thread serializuje wywołanie).
# ────────────────────────────────────────────────

def publish_sensor_sync(entity_id: str, state, friendly_name: str, extra_attrs: dict | None = None) -> None:
    attrs = {
        "friendly_name": friendly_name,
        "last_update":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }
    h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})
    if _sent_hashes.get(entity_id) == h:
        return
    if len(_sent_hashes) >= _SENT_HASHES_MAX:
        _sent_hashes.clear()

    _save_to_cache(entity_id, state, attrs)

    try:
        res = httpx.post(
            f"{HA_URL}/states/{entity_id}",
            headers=HA_HEADERS,
            json={"state": state, "attributes": attrs},
            timeout=12,
        )
        if res.status_code in (200, 201):
            _sent_hashes[entity_id] = h
    except Exception as exc:
        logger.warning("Błąd publish_sensor_sync %s: %s", entity_id, exc)


# ────────────────────────────────────────────────
# ODTWARZANIE CACHE PO RESTARCIE HA
# ────────────────────────────────────────────────

async def restore_entities_from_cache(ha: httpx.AsyncClient) -> None:
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT entity_id, state, attributes_json FROM ha_cache")
        rows = cur.fetchall()
        conn.close()

        restored = 0
        for entity_id, state, attrs_json in rows:
            try:
                attrs = json.loads(attrs_json)
                h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})
                # POPRAWKA #10 – zapis do _sent_hashes pod lockiem
                async with _sent_hashes_lock:
                    _sent_hashes[entity_id] = h

                res = await ha.post(
                    f"{HA_URL}/states/{entity_id}",
                    headers=HA_HEADERS,
                    json={"state": state, "attributes": attrs},
                    timeout=5
                )
                if res.status_code in (200, 201):
                    restored += 1
            except Exception as e:
                logger.debug("Nie udało się odtworzyć %s: %s", entity_id, e)

        if restored > 0:
            logger.info("Sukces: Błyskawicznie przywrócono %d encji z bazy danych.", restored)
    except Exception as e:
        logger.error("Błąd bazy danych przy odtwarzaniu cache: %s", e)

async def check_and_restore(ha: httpx.AsyncClient) -> None:
    try:
        r = await ha.get(f"{HA_URL}/states/sensor.vultron_system_monitor", timeout=4)
        if r.status_code == 404:
            logger.warning("Wykryto restart Home Assistanta! Wstrzykuję stan z bazy...")
            await restore_entities_from_cache(ha)
    except Exception:
        pass


# ────────────────────────────────────────────────
# SELENIUM HELPER
# ────────────────────────────────────────────────

def _get_driver() -> webdriver.Chrome:
    opts = Options()
    for arg in ("--headless", "--no-sandbox", "--disable-dev-shm-usage",
                 "--disable-gpu", "--disable-extensions",
                 "--blink-settings=imagesEnabled=false"):
        opts.add_argument(arg)
    opts.binary_location = "/usr/bin/chromium-browser"
    return webdriver.Chrome(options=opts)


# ────────────────────────────────────────────────
# SQLITE HELPERS
# ────────────────────────────────────────────────

_DB_DDL =[
    """CREATE TABLE IF NOT EXISTS grades (
        id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT,
        data TEXT, opis TEXT, period_id TEXT,
        PRIMARY KEY(id_kolumny, student_slug, period_id))""",
    """CREATE TABLE IF NOT EXISTS schedule (
        id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT,
        przedmiot TEXT, sala TEXT, prowadzacy TEXT, status TEXT)""",
    """CREATE TABLE IF NOT EXISTS frequency (
        id TEXT PRIMARY KEY, student_slug TEXT, data TEXT,
        godzina TEXT, kategoria INTEGER)""",
    """CREATE TABLE IF NOT EXISTS timetable (
        id TEXT, student_slug TEXT, data TEXT, przedmiot TEXT,
        typ TEXT, opis TEXT, autor TEXT, PRIMARY KEY(id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS remarks (
        remark_id TEXT, student_slug TEXT, data TEXT, tresc TEXT, autor TEXT,
        kategoria TEXT, punkty TEXT, typ TEXT, PRIMARY KEY(remark_id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS achievements (
        achievement_id TEXT, student_slug TEXT, tresc TEXT,
        PRIMARY KEY(achievement_id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS messages (
        key TEXT PRIMARY KEY, student_slug TEXT, data TEXT,
        nadawca TEXT, temat TEXT, tresc TEXT, przeczytana INTEGER)""",
    """CREATE TABLE IF NOT EXISTS ha_cache (
        entity_id TEXT PRIMARY KEY, state TEXT, attributes_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS frequency_stats (
        id TEXT PRIMARY KEY,
        student_slug TEXT,
        data TEXT,
        przedmiot_id INTEGER,
        przedmiot_nazwa TEXT,
        podsumowanie REAL,
        statystyki_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS lucky_number (
        student_slug TEXT, data TEXT, numer TEXT, numer_id TEXT,
        PRIMARY KEY(student_slug, data))"""
]

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def db_init(conn: sqlite3.Connection) -> None:
    for stmt in _DB_DDL:
        conn.execute(stmt)
    conn.commit()


# ────────────────────────────────────────────────
# LOVELACE SETUP
# ────────────────────────────────────────────────

def copy_resources() -> None:
    target = "/config/www/vultron"
    os.makedirs(target, exist_ok=True)
    src = "/app"
    n = 0
    if os.path.exists(src):
        for f in os.listdir(src):
            if f.lower().endswith(".js"):
                shutil.copy(os.path.join(src, f), os.path.join(target, f))
                n += 1
    logger.info("Skopiowano %d plików JS do /local/vultron/", n)

async def wait_for_ha_api() -> None:
    async with httpx.AsyncClient() as c:
        while True:
            try:
                r = await c.get(f"{HA_URL}/config", headers=HA_HEADERS, timeout=5)
                if r.status_code == 200:
                    logger.info("HA API gotowe.")
                    return
            except Exception:
                pass
            logger.info("Czekam na HA API…")
            await asyncio.sleep(5)

def run_setup_ui() -> None:
    log = logging.getLogger("UI-SETUP")
    def _version() -> str:
        for p in ("config.yaml", "/app/config.yaml"):
            try:
                with open(p) as f:
                    m = re.search(r'version:\s*["\']?([^"\']+)["\']?', f.read())
                    if m:
                        return m.group(1)
            except OSError:
                pass
        return "1.0"
    version = _version()
    ws = None
    for attempt in range(10):
        try:
            ws = create_connection("ws://supervisor/core/websocket", timeout=10)
            ws.recv()
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
            if json.loads(ws.recv()).get("type") == "auth_ok":
                log.info("WebSocket OK (próba %d)", attempt + 1)
                time.sleep(1)
                break
            log.error("Błąd autoryzacji WS.")
            ws.close()
            return
        except Exception as e:
            if attempt < 9:
                log.info("Czekam na WS… (%d/10)", attempt + 1)
                time.sleep(5)
            else:
                log.error("WS niedostępny: %s", e)
                return
    if ws is None:
        return
    try:
        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        raw = json.loads(ws.recv()).get("result",[])
        existing = {re.sub(r"\?v=.*", "", r["url"]): (r["id"], r["url"]) for r in raw}
        src_dir = "/app" if os.path.exists("/app") else "."
        cards   =[f for f in os.listdir(src_dir) if f.startswith("vultron-") and f.endswith(".js")]
        for msg_id, card in enumerate(cards, start=2):
            base = f"/local/vultron/{card}"
            versioned = f"{base}?v={version}"
            if base in existing:
                rid, cur_url = existing[base]
                if cur_url != versioned:
                    log.info("Aktualizacja: %s → v%s", card, version)
                    ws.send(json.dumps({"id": msg_id, "type": "lovelace/resources/update",
                                        "resource_id": rid, "url": versioned}))
                    ws.recv()
            else:
                log.info("Rejestracja: %s v%s", card, version)
                ws.send(json.dumps({"id": msg_id, "type": "lovelace/resources/create",
                                    "res_type": "module", "url": versioned}))
                ws.recv()
        log.info("Lovelace skonfigurowany.")
    except Exception as e:
        log.error("Błąd rejestracji: %s", e)
    finally:
        ws.close()


# ────────────────────────────────────────────────
# AUTORYZACJA DZIENNIKA (Selenium – sync)
# ────────────────────────────────────────────────

def run_diary_auth() -> tuple[list | None, list | None]:
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = _get_driver()
    wait   = WebDriverWait(driver, 25)

    session = httpx.Client(timeout=15)
    try:
        logger.info("[AUTH] Logowanie…")
        driver.get("https://eduvulcan.pl/logowanie")

        # Wpisanie loginu
        wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
            CONFIG.get("username", "") + Keys.ENTER
        )
        time.sleep(1.5)

        # Wpisanie hasła
        wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
            CONFIG.get("password", "") + Keys.ENTER
        )

        # Oczekiwanie na kafelek Dziennika (zrzut ekranu w razie błędu)
        try:
            link_el = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href,'dziennik')]")))
            link = link_el.get_attribute("href")
            driver.get(link)
            time.sleep(5)
        except Exception as ex:
            err_dir = "/config/www/vultron"
            os.makedirs(err_dir, exist_ok=True)
            err_path = os.path.join(err_dir, "vultron_auth_error.png")
            driver.save_screenshot(err_path)
            logger.error("[AUTH] Nie znaleziono kafelka 'Dziennik'. Zrzut ekranu zapisano w: %s", err_path)
            logger.error("[AUTH] Sprawdź błąd wpisując w przeglądarce: http://<TWOJE_IP_HA>:8123/local/vultron/vultron_auth_error.png")
            raise ex

        m = re.search(r"uczen\.eduvulcan\.pl/([^/]+)", driver.current_url)
        if not m:
            logger.error("[AUTH] Brak nazwy miasta w URL: %s", driver.current_url)
            return None, None
        city = m.group(1)

        driver.get(f"https://uczen.eduvulcan.pl/{city}/api/Context")
        time.sleep(2)
        context_raw = driver.execute_script("return document.body.innerText")
        try:
            context = json.loads(context_raw)
        except json.JSONDecodeError as e:
            logger.critical(
                "[AUTH] Krytyczny błąd: Nie można sparsować /api/Context "
                "(Prawdopodobnie CAPTCHA lub trwała blokada serwera). "
                "Wymuszam całkowite wyłączenie dodatku!"
            )
            logger.debug("[AUTH] Surowa odpowiedź: %s", context_raw[:500])
            raise PermissionError("CAPTCHA_BLOKADA") from e

        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])

        students: list[dict] = []
        for u in context.get("uczniowie",[]):
            key   = u.get("key")
            id_dz = str(u.get("idDziennik"))
            res   = session.get(
                f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne",
                params={"key": key, "idDziennik": id_dz}
            )
            if res.status_code != 200:
                logger.warning("Brak okresów dla: %s", u.get("uczen"))
                continue

            okresy = res.json()
            curr_p = okresy[-1]["id"] if okresy else None
            for o in okresy:
                try:
                    if (datetime.strptime(o["dataOd"][:19], "%Y-%m-%dT%H:%M:%S")
                            <= datetime.now()
                            <= datetime.strptime(o["dataDo"][:19], "%Y-%m-%dT%H:%M:%S")):
                        curr_p = o["id"]
                        break
                except (ValueError, KeyError):
                    continue

            students.append({
                "slug":       slugify(u.get("uczen", "")),
                "uczen":      u.get("uczen", ""),
                "city":       city,
                "key":        key,
                "idDziennik": id_dz,
                "periodId":   curr_p,
                "klasa":      u.get("oddzial", ""),
                "globalKeySkrzynka": u.get("globalKeySkrzynka", ""),
            })

        cookies = driver.get_cookies()
        with open(VUL_PKL, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "students": students}, f, ensure_ascii=False)

        logger.info("[AUTH] OK – %d uczniów", len(students))
        return students, cookies

    except PermissionError:
        raise
    except Exception as e:
        logger.error("[AUTH] Błąd: %s", e, exc_info=True)
        return None, None
    finally:
        session.close()
        driver.quit()
        display.stop()


# ────────────────────────────────────────────────
# FETCH HELPERS – async sekcje danych
# ────────────────────────────────────────────────

async def _fetch_grades(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                        base: str, s: dict) -> None:
    slug, key, id_dz, name = s["slug"], s["key"], s["idDziennik"], s["uczen"]
    logger.info("--> [%s] Pobieram oceny...", name)

    res_per = await client.get(f"{base}/api/OkresyKlasyfikacyjne",
                               params={"key": key, "idDziennik": id_dz})
    if res_per.status_code != 200:
        logger.warning("[%s] błąd okresów: %d", name, res_per.status_code)
        return

    for period in res_per.json():
        p_id  = str(period["id"])
        p_num = period["numerOkresu"]

        res_g = await client.get(f"{base}/api/Oceny",
                                 params={"key": key, "idOkresKlasyfikacyjny": p_id})
        if res_g.status_code != 200:
            continue

        subjects: dict[str, list] = {}
        new_g = 0

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                for p_item in (res_g.json().get("ocenyPrzedmioty") or[]):
                    subj = p_item.get("przedmiotNazwa", "Inne")
                    for kol in (p_item.get("kolumnyOcenyCzastkowe") or[]):
                        id_k = str(kol.get("idKolumny", "0"))
                        desc = f"{kol.get('kategoriaKolumny','')}: {kol.get('nazwaKolumny','')}".strip(": ")
                        for o in (kol.get("oceny") or[]):
                            v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
                            cur.execute(
                                "INSERT OR REPLACE INTO grades VALUES (?,?,?,?,?,?,?)",
                                (id_k, slug, subj, v, dt, desc, p_id),
                            )
                            if cur.rowcount > 0:
                                new_g += 1
                            subjects.setdefault(subj, []).append({"w": v, "d": dt[:5], "i": clean_text(desc)})
                conn.commit()
            finally:
                conn.close()

        lista =[]
        for subj_name, grades in subjects.items():
            vals: list[float] = []
            for g in grades:
                w_str = str(g["w"]).strip().upper()

                if re.search(r"[A-F%]|NB|NP|BZ", w_str):
                    continue

                m_dec = re.search(r"(?<!\d)([1-6])(?:[.,](\d+))?(?!\d)", w_str)
                if m_dec:
                    v = float(m_dec.group(1))
                    if m_dec.group(2):
                        v += float("0." + m_dec.group(2))
                    else:
                        if "+" in w_str:
                            v += 0.5
                        elif "-" in w_str:
                            v -= 0.25
                    vals.append(v)

            lista.append({"przedmiot": subj_name, "oceny": grades,
                          "srednia": round(sum(vals)/len(vals), 2) if vals else None})

        await publish_sensor(ha, f"sensor.vultron_oceny_{slug}_p{p_num}", new_g,
                             f"Oceny: {name} (P{p_num})",
                             {"lista_przedmiotow": lista, "period_number": int(p_num),
                              "student_slug": slug,
                              "active_period": p_id == str(s["periodId"])})


async def _fetch_schedule(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                          base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram plan lekcji...", name)
    now = datetime.now()

    res = await client.get(f"{base}/api/PlanZajec", params={
        "key": key,
        "dataOd": (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%dT00:00:00.000Z"),
        "dataDo": (now + timedelta(days=21)).strftime("%Y-%m-%dT23:59:59.999Z"),
        "zakresDanych": "2",
    })
    if res.status_code != 200:
        logger.warning("[%s] błąd planu: %d", name, res.status_code)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            for lesson in res.json():
                st  = MAPA_STATUSOW.get(int(lesson.get("adnotacja", 0)), "")
                inf = " ".join((c.get("informacjeNieobecnosc") or "").lower() for c in (lesson.get("zmiany") or[]))
                if "zwolnieni" in inf or "okienko" in inf:
                    st = "ODWOL"
                data_raw   = lesson.get("data", "")
                godz_od    = lesson.get("godzinaOd", "T00:00")
                godz_do    = lesson.get("godzinaDo", "T00:00")
                cur.execute(
                    "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                    (
                        f"{slug}_{data_raw}_{godz_od}", slug,
                        data_raw.split("T")[0],
                        f"{godz_od.split('T')[1][:5]}-{godz_do.split('T')[1][:5]}",
                        lesson.get("przedmiot") or "Zajęcia",
                        lesson.get("sala", ""), lesson.get("prowadzacy", ""), st,
                    ),
                )
            conn.commit()

            monday = now - timedelta(days=now.weekday())
            weeks  = {
                "prev": (monday - timedelta(7), monday - timedelta(1)),
                "curr": (monday,                monday + timedelta(6)),
                "next": (monday + timedelta(7), monday + timedelta(13)),
            }
            tasks =[]
            for suf, (sd, ed) in weeks.items():
                cur.execute(
                    "SELECT data,godzina,przedmiot,sala,prowadzacy,status FROM schedule "
                    "WHERE student_slug=? AND data BETWEEN ? AND ? ORDER BY data,godzina",
                    (slug, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")),
                )
                proc  = [{"d": r[0],"g": r[1],"p": r[2],"s": r[3],"n": r[4],"st": r[5]} for r in cur.fetchall()]
                today = now.strftime("%Y-%m-%d")
                state = len([entry for entry in proc if entry["d"] == today]) if suf == "curr" else len(proc)
                tasks.append(publish_sensor(ha, f"sensor.vultron_plan_{slug}_{suf}", state,
                                            f"Plan {suf}: {name}", {"lekcje": proc}))
        finally:
            conn.close()
    await asyncio.gather(*tasks)

async def _fetch_timetable(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                           base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram terminarz...", name)
    now = datetime.now()
    last_day_prev_month = now.replace(day=1) - timedelta(days=1)

    res = await client.get(f"{base}/api/SprawdzianyZadaniaDomowe", params={
        "key": key,
        "dataOd": last_day_prev_month.strftime("%Y-%m-%dT00:00:00.000Z"),
        "dataDo": (now + timedelta(days=61)).strftime("%Y-%m-%dT23:59:59.999Z"),
    })
    if res.status_code != 200:
        logger.warning("[%s] błąd terminarza: %d", name, res.status_code)
        return

    items = res.json()

    async def _detail(item: dict, cur) -> None:
        item_id = item.get("id")
        if not item_id:
            return
        ep = "ZadanieDomoweSzczegoly" if item.get("typ") == 4 else "SprawdzianSzczegoly"

        dj = {}
        try:
            dr = await client.get(f"{base}/api/{ep}", params={"key": key, "id": item_id})
            if dr.status_code == 200:
                dj = dr.json()
        except httpx.RequestError as exc:
            logger.warning("[%s] błąd szczegółów terminarza %s: %s", name, item_id, exc)

        data_str = dj.get("data") or item.get("data", "")
        termin_str = dj.get("terminOdpowiedzi") or item.get("terminOdpowiedzi") or ""
        data = termin_str if termin_str else data_str

        raw_opis = (
            dj.get("opis") or
            dj.get("temat") or
            dj.get("tresc") or
            item.get("opis") or
            item.get("temat") or
            ""
        )

        czysty_opis = clean_html(raw_opis)

        if czysty_opis == "Brak opisu" and "<iframe" in raw_opis.lower():
            czysty_opis = "[Wstawiono załącznik - sprawdź treść w oficjalnej aplikacji]"

        przedmiot = dj.get("przedmiotNazwa") or item.get("przedmiotNazwa", "")
        autor = dj.get("nauczycielImieNazwisko") or item.get("nauczycielImieNazwisko", "")

        cur.execute(
            "INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
            (str(item_id), slug, data,
             przedmiot,
             MAPA_TYP_TERMINARZA.get(item.get("typ"), "Inne"),
             czysty_opis,
             autor),
        )

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            await asyncio.gather(*[_detail(i, cur) for i in items])
            conn.commit()

            cur.execute(
                "SELECT data,przedmiot,typ,opis,autor FROM timetable "
                "WHERE student_slug=? AND data>=? ORDER BY data",
                (slug, now.strftime("%Y-%m-%d")),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

    await publish_sensor(ha, f"sensor.vultron_terminarz_{slug}", len(rows),
                         f"Terminarz: {name}",
                         {"lista": [{"data": r[0].split("T")[0], "przedmiot": r[1],
                                     "typ": r[2], "opis": r[3], "autor": r[4]} for r in rows]})

async def _fetch_remarks(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                         base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram uwagi...", name)

    res = await client.get(f"{base}/api/Uwagi", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd uwag: %d", name, res.status_code)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            for item in res.json():
                item_id = item.get("id")
                if not item_id:
                    continue
                tr    = item.get("tresc", "")
                typ_u = ("pozytywna" if "pochwa" in tr.lower()
                         else "negatywna" if "uwaga" in tr.lower()
                         else "informacja")
                cur.execute(
                    "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                    (str(item_id), slug, item.get("data","").split("T")[0],
                     tr, item.get("autor",""), item.get("kategoria",""),
                     str(item.get("liczbaPunktow") or ""), typ_u),
                )
            conn.commit()

            cur.execute(
                "SELECT data,tresc,autor,kategoria,punkty,typ,remark_id FROM remarks "
                "WHERE student_slug=? ORDER BY data DESC", (slug,)
            )
            lista = [{"data": r[0], "tresc": r[1], "autor": r[2], "kategoria": r[3],
                      "punkty": r[4], "typ": r[5], "id": r[6]} for r in cur.fetchall()]
        finally:
            conn.close()

    await publish_sensor(ha, f"sensor.vultron_uwagi_{slug}", len(lista),
                         f"Uwagi: {name}", {"uwagi": lista})


async def _fetch_frequency(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                           base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram frekwencję...", name)
    now = datetime.now()

    res_f, res_p, res_fs = await asyncio.gather(
        client.get(f"{base}/api/Frekwencja", params={
            "key": key,
            "dataOd": (now - timedelta(14)).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": now.strftime("%Y-%m-%dT23:59:59.999Z"),
        }),
        client.get(f"{base}/api/Przedmioty", params={"key": key}),
        client.get(f"{base}/api/FrekwencjaStatystyki", params={"key": key, "idPrzedmiot": -1}),
    )

    przedmioty =[]
    if res_p.status_code == 200:
        try:
            przedmioty = res_p.json()
        except Exception:
            przedmioty = []
    else:
        logger.warning("[%s] błąd pobierania przedmiotów: %d", name, res_p.status_code)

    per_subject_list =[p for p in przedmioty if p.get("id", -1) != -1]
    per_subject_results = await asyncio.gather(
        *[client.get(f"{base}/api/FrekwencjaStatystyki", params={"key": key, "idPrzedmiot": p["id"]})
          for p in per_subject_list],
        return_exceptions=True,
    )

    def _parse_rows(fsd: dict) -> list:
        return[
            {"k": MAPA_FREKWENCJI.get(row.get("kategoriaFrekwencji"), "Inna"),
             "m": {str(m["miesiac"]): m["wartosc"] for m in (row.get("miesiace") or [])},
             "s1": row.get("okresy",[0, 0])[0], "s2": row.get("okresy",[0, 0])[1],
             "r": row.get("razem", 0)}
            for row in (fsd.get("statystyki") or [])
        ]

    freq_wpisy =[]
    freq_ok = False
    stats_global = {}
    stats_per_subject =[]
    index_subjects =[]

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            today = now.strftime("%Y-%m-%d")

            if res_f.status_code == 200:
                recs = res_f.json()
                if isinstance(recs, dict):
                    recs = recs.get("oddzialy") or[]
                for fi in recs:
                    fi_data  = fi.get("data", "")
                    fi_godz  = fi.get("godzinaOd", "")
                    if fi_data and fi_godz:
                        cur.execute(
                            "INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                            (f"{slug}_{fi_data}_{fi_godz}", slug,
                             fi_data.split("T")[0], fi_godz.split("T")[1][:5],
                             int(fi.get("kategoriaFrekwencji", 0))),
                        )
                conn.commit()
                since = (now - timedelta(14)).strftime("%Y-%m-%d")
                cur.execute("SELECT data,godzina,kategoria FROM frequency "
                            "WHERE student_slug=? AND data>=? ORDER BY data DESC", (slug, since))
                freq_wpisy = [{"d": r[0], "t": r[1], "k": int(r[2])} for r in cur.fetchall()]
                freq_ok = True
            else:
                logger.warning("[%s] błąd frekwencji: %d", name, res_f.status_code)

            if res_fs.status_code == 200:
                fsd_all = res_fs.json()
                rows_all = _parse_rows(fsd_all)
                pct_all  = fsd_all.get("podsumowanie", 0)

                cur.execute(
                    "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                    (f"{slug}_-1_{today}", slug, today, -1, "Wszystkie",
                     pct_all, json.dumps(rows_all, ensure_ascii=False)),
                )

                index_subjects =[{"id": -1, "nazwa": "Wszystkie"}] + \
                                 [{"id": p["id"], "nazwa": p["nazwa"]} for p in per_subject_list]
                stats_global = {"pct": pct_all, "rows": rows_all}

                for p, res in zip(per_subject_list, per_subject_results):
                    if isinstance(res, Exception):
                        logger.warning("[%s] błąd statystyk dla %s: %s", name, p.get("nazwa"), res)
                        continue
                    if res.status_code != 200:
                        logger.warning("[%s] błąd statystyk dla %s: %d", name, p.get("nazwa"), res.status_code)
                        continue
                    try:
                        fsd_p = res.json()
                        pct_p = fsd_p.get("podsumowanie")
                        if pct_p is None:
                            logger.debug("[%s] brak statystyk dla %s (podsumowanie=null), pomijam", name, p.get("nazwa"))
                            continue
                        rows_p = _parse_rows(fsd_p)
                        cur.execute(
                            "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                            (f"{slug}_{p['id']}_{today}", slug, today,
                             p["id"], p["nazwa"], pct_p,
                             json.dumps(rows_p, ensure_ascii=False)),
                        )
                        stats_per_subject.append({
                            "slug_p": slugify(p["nazwa"]),
                            "pct_p":  pct_p,
                            "rows_p": rows_p,
                            "pid":    p["id"],
                            "pnazwa": p["nazwa"],
                        })
                    except Exception as e:
                        logger.warning("[%s] błąd parsowania %s: %s", name, p.get("nazwa"), e)

                conn.commit()
            else:
                logger.warning("[%s] błąd statystyk: %d", name, res_fs.status_code)
        finally:
            conn.close()

    if freq_ok:
        await publish_sensor(ha, f"sensor.vultron_freq_{slug}", 0, f"Frekwencja: {name}",
                             {"wpisy": freq_wpisy})

    if stats_global:
        await publish_sensor(ha, f"sensor.vultron_stats_{slug}",
                             stats_global["pct"], f"Statystyki: {name}",
                             {
                                 "unit_of_measurement": "%",
                                 "rows": stats_global["rows"],
                                 "przedmioty": index_subjects,
                             })

    if stats_per_subject:
        await asyncio.gather(*[
            publish_sensor(ha, f"sensor.vultron_stats_{slug}_{sp['slug_p']}",
                           sp["pct_p"],
                           f"Statystyki {sp['pnazwa']}: {name}",
                           {
                               "unit_of_measurement": "%",
                               "rows": sp["rows_p"],
                               "przedmiot_id": sp["pid"],
                               "przedmiot_nazwa": sp["pnazwa"],
                           })
            for sp in stats_per_subject
        ], return_exceptions=True)


async def _fetch_achievements(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                              base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram osiągnięcia...", name)

    res = await client.get(f"{base}/api/Osiagniecia", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd osiągnięć: %d", name, res.status_code)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            for item in res.json():
                item_id = item.get("id")
                if not item_id:
                    continue
                cur.execute("INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                            (str(item_id), slug, item.get("tresc","")))
            conn.commit()

            cur.execute("SELECT achievement_id,tresc FROM achievements WHERE student_slug=?", (slug,))
            rows = cur.fetchall()
        finally:
            conn.close()

    await publish_sensor(ha, f"sensor.vultron_osiagniecia_{slug}", len(rows),
                         f"Osiągnięcia: {name}",
                         {"osiagniecia": [{"id": r[0], "tresc": r[1]} for r in rows]})


async def _fetch_lucky_number(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                              base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram szczęśliwy numerek...", name)

    now_str = datetime.now().strftime("%Y-%m-%d")

    api_numer = None
    api_id = None

    try:
        res = await client.get(f"{base}/api/SzczesliwyNumerTablica", params={"key": key})
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, dict):
                api_numer = str(data.get("numer", "Brak"))
                api_id = str(data.get("id", ""))
        else:
            logger.warning("[%s] błąd szczęśliwego numerka API: %d", name, res.status_code)
    except Exception as e:
        logger.error("[%s] błąd pobierania/parsowania szczęśliwego numerka: %s", name, e)

    db_numer = "Brak"
    db_id = ""

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()

            if api_numer is not None:
                cur.execute(
                    "INSERT OR REPLACE INTO lucky_number VALUES (?,?,?,?)",
                    (slug, now_str, api_numer, api_id)
                )
                conn.commit()

            cur.execute(
                "SELECT numer, numer_id FROM lucky_number WHERE student_slug=? AND data=?",
                (slug, now_str)
            )
            row = cur.fetchone()
            if row:
                db_numer, db_id = row

        except Exception as e:
            logger.error("Błąd bazy danych dla szczęśliwego numerka [%s]: %s", name, e)
        finally:
            conn.close()

    logger.debug("[%s] Publikuję sensor szczęśliwego numerka: %s", name, db_numer)

    state_val = 1 if db_numer != "Brak" else 0

    await publish_sensor(ha, f"sensor.vultron_szczesliwy_numerek_{slug}", state_val,
                         f"Szczęśliwy Numerek: {name}",
                         {"numer": db_numer, "id_numerku": db_id, "icon": "mdi:clover"})

# ────────────────────────────────────────────────
# SYNCHRONIZACJA DZIENNIKA – pełna async
# ────────────────────────────────────────────────

async def sync_diary_data(students: list, cookies: list) -> None:
    httpx_cookies = {c["name"]: c["value"] for c in cookies}

    async with (
        httpx.AsyncClient(cookies=httpx_cookies, timeout=20) as client,
        httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha,
    ):
        for s in students:
            logger.info("=== Synchronizacja: %s ===", s["uczen"])
            base = f"https://uczen.eduvulcan.pl/{s['city']}"
            results = await asyncio.gather(
                _fetch_grades(client, ha, base, s),
                _fetch_schedule(client, ha, base, s),
                _fetch_timetable(client, ha, base, s),
                _fetch_remarks(client, ha, base, s),
                _fetch_frequency(client, ha, base, s),
                _fetch_achievements(client, ha, base, s),
                _fetch_lucky_number(client, ha, base, s),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error("Sekcja %d błąd dla %s: %s", i, s["uczen"], r, exc_info=r)
            logger.info("=== Zakończono: %s ===", s["uczen"])


# ────────────────────────────────────────────────
# WIADOMOŚCI (Selenium – sync, uruchamiana w wątku)
# POPRAWKA #11 – SQLite chronione przez db_lock_thread (threading.Lock)
# ────────────────────────────────────────────────

def run_messages_sync(city: str, students_list: list) -> None:
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = _get_driver()
    wait   = WebDriverWait(driver, 25)

    conn = None
    session = httpx.Client(timeout=15)
    try:
        logger.info("[MESS] Logowanie…")
        driver.get("https://eduvulcan.pl/logowanie")
        if os.path.exists(BUL_PKL):
            try:
                with open(BUL_PKL, "r", encoding="utf-8") as f:
                    for c in json.load(f):
                        try:
                            driver.add_cookie(c)
                        except Exception:
                            pass
                driver.get("https://eduvulcan.pl/logowanie")
                time.sleep(2)
            except Exception as e:
                logger.debug("Uszkodzony plik ciasteczek, usuwam: %s", e)
                try:
                    os.remove(BUL_PKL)
                except Exception:
                    pass

        if "Alias" in driver.page_source:
            wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
                CONFIG.get("username", "") + Keys.ENTER)
            wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
                CONFIG.get("password", "") + Keys.ENTER)
            time.sleep(3)

        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href,'dziennik')]")))
        driver.get(
            driver.find_element(By.XPATH, "//a[contains(@href,'dziennik')]").get_attribute("href")
        )
        time.sleep(3)

        app_url = f"https://wiadomosci.eduvulcan.pl/{city}/App"
        driver.get(app_url)
        time.sleep(5)

        if "logowanie" in driver.current_url:
            try:
                driver.switch_to.frame(1)
                driver.find_element(By.ID, "save-default-button").click()
                driver.switch_to.default_content()
            except Exception:
                pass
            wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
                CONFIG.get("username", "") + Keys.ENTER)
            wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
                CONFIG.get("password", "") + Keys.ENTER)
            driver.get(app_url)
            time.sleep(5)

        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])
        session.headers.update({
            "User-Agent":        driver.execute_script("return navigator.userAgent"),
            "Referer":           app_url,
            "X-Requested-With":  "XMLHttpRequest",
        })

        logger.info("--> Pobieram wiadomości ze skrzynek odbiorczych...")

        # POPRAWKA #11 – używamy db_lock_thread (threading.Lock) zamiast asyncio.Lock
        with db_lock_thread:
            conn = db_connect()
            cur  = conn.cursor()

            try:
                for st in students_list:
                    gk = st.get("globalKeySkrzynka")
                    assigned = st["slug"]
                    if not gk:
                        logger.warning("[MESS] Brak globalKeySkrzynka dla ucznia %s", st["uczen"])
                        continue

                    res_m = session.get(
                        f"https://wiadomosci.eduvulcan.pl/{city}/api/OdebraneSkrzynka"
                        f"?globalKeySkrzynka={gk}&idLastWiadomosc=0&pageSize=50"
                    )

                    if res_m.status_code != 200:
                        logger.warning("[MESS] błąd pobierania dla %s: %d", st["uczen"], res_m.status_code)
                        if res_m.status_code == 400:
                            logger.warning("[MESS] Wykryto przepełnienie ciasteczek (błąd 400). Usuwam bul.pkl...")
                            if os.path.exists(BUL_PKL):
                                try:
                                    os.remove(BUL_PKL)
                                except Exception as e:
                                    logger.error("Nie udało się usunąć bul.pkl: %s", e)
                        continue

                    for m in res_m.json():
                        m_k = m.get("apiGlobalKey")
                        if not m_k:
                            continue
                        det = session.get(
                            f"https://wiadomosci.eduvulcan.pl/{city}/api/WiadomoscSzczegoly"
                            f"?apiGlobalKey={m_k}"
                        )
                        if det.status_code == 200:
                            cur.execute(
                                "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                                (m_k, assigned,
                                 m.get("data", ""),
                                 m.get("korespondenci", ""),
                                 m.get("temat", ""),
                                 det.json().get("tresc", "Brak"),
                                 1 if m.get("przeczytana") else 0),
                            )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error("[MESS] rollback: %s", e, exc_info=True)

            for st in students_list:
                slug = st["slug"]
                cur.execute(
                    "SELECT data,nadawca,temat,tresc,przeczytana FROM messages "
                    "WHERE student_slug=? ORDER BY data DESC LIMIT 10",
                    (slug,),
                )
                rows = cur.fetchall()
                unread = cur.execute(
                    "SELECT COUNT(*) FROM messages "
                    "WHERE student_slug=? AND przeczytana=0",
                    (slug,),
                ).fetchone()[0]
                total = cur.execute(
                    "SELECT COUNT(*) FROM messages WHERE student_slug=?",
                    (slug,),
                ).fetchone()[0]

                msgs = []
                for r in rows:
                    is_u = int(r[4]) == 0
                    if is_u:
                        body = clean_html(r[3])
                        if len(body) > 2000:
                            body = body[:1997] + "..."
                    else:
                        body = ""

                    msgs.append({"data": r[0].replace("T"," ")[:16], "nadawca": r[1],
                                 "temat": r[2], "tresc": body, "przeczytana": not is_u})

                publish_sensor_sync(
                    f"sensor.vultron_wiadomosci_{slug}", unread, f"Wiadomości: {st['uczen']}",
                    {"wiadomosci": msgs, "stats": f"{unread} / {total}"},
                )

        with open(BUL_PKL, "w", encoding="utf-8") as f:
            json.dump(driver.get_cookies(), f, ensure_ascii=False)
        logger.info("[MESS] Gotowe.")

    except Exception as e:
        logger.error("[MESS] Błąd krytyczny: %s", e, exc_info=True)
    finally:
        session.close()
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logger.debug("Błąd zamykania bazy w messages: %s", e)
        driver.quit()
        display.stop()


# ────────────────────────────────────────────────
# MONITOR ROZMIARU ENCJI
# ────────────────────────────────────────────────

_MONITOR_TEMPLATE = (
    "[{% for s in states.sensor"
    " if s.entity_id.startswith('sensor.vultron_')"
    " and s.entity_id != 'sensor.vultron_system_monitor' %}"
    "{\"id\":\"{{ s.entity_id }}\",\"size\":{{ s.attributes|tojson|length }}}"
    "{{ \",\" if not loop.last }}{% endfor %}]"
)

async def _run_size_monitor(ha: httpx.AsyncClient) -> None:
    try:
        res = await ha.post(f"{HA_URL}/template",
                            json={"template": _MONITOR_TEMPLATE}, timeout=15)
        if res.status_code != 200:
            logger.warning("Monitor template błąd: %d", res.status_code)
            return
        ents = res.json()
        tot  = sum(e["size"] for e in ents)
        await asyncio.gather(
            publish_sensor(ha, "sensor.vultron_system_monitor", tot, "Vultron System Monitor",
                           {"unit_of_measurement": "B",
                            "szczegoly": " | ".join(f"{e['id']}: {e['size']}B" for e in ents)}),
            publish_sensor(ha, "binary_sensor.vultron_rozmiar_alert",
                           "on" if any(e["size"] > 15_500 for e in ents) else "off",
                           "Vultron Rozmiar Alert", {"device_class": "problem"}),
        )
    except Exception as e:
        logger.error("Monitor rozmiaru: %s", e)


# ────────────────────────────────────────────────
# GŁÓWNA PĘTLA
# ────────────────────────────────────────────────

async def main_loop() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    copy_resources()
    await wait_for_ha_api()
    run_setup_ui()

    db_conn = db_connect()
    db_init(db_conn)
    db_conn.close()

    async with httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha:

        await restore_entities_from_cache(ha)

        while not stop_event.is_set():
            now = datetime.now()
            wd = now.weekday()  # 0=Pon, 1=Wt, 2=Śr, 3=Czw, 4=Pt, 5=Sob, 6=Nie

            wake_at = None

            if not _test_mode:
                if wd < 5 and 1 <= now.hour <= 5:
                    wake_at = now.replace(hour=6, minute=0, second=0, microsecond=0)
                    logger.info("Przerwa nocna (Pon-Pt) – wznowienie o 06:00")

                elif wd == 5 and now.hour not in (8, 16, 23):
                    next_h = next((h for h in (8, 16, 23) if h > now.hour), None)
                    if next_h:
                        wake_at = now.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                    logger.info("Harmonogram weekendowy (Sobota) – czekam do %s", wake_at.strftime("%H:%M"))

                elif wd == 6 and now.hour not in (8, 12, 20):
                    next_h = next((h for h in (8, 12, 20) if h > now.hour), None)
                    if next_h:
                        wake_at = now.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
                    logger.info("Harmonogram weekendowy (Niedziela) – czekam do %s", wake_at.strftime("%H:%M"))
            else:
                logger.info("[TEST MODE] Filtr czasowy (noce/weekendy) pominięty.")

            if wake_at:
                secs = int(max(60, (wake_at - now).total_seconds()))
                logger.info("Czekam %d minut przed uruchomieniem pobierania.", secs // 60)
                for elapsed in range(0, secs, 10):
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=10)
                        break
                    except asyncio.TimeoutError:
                        pass
                    if (elapsed + 10) % 60 == 0:
                        await check_and_restore(ha)
                continue

            logger.info("=== CYKL START ===")

            await check_and_restore(ha)

            try:
                students, cookies = await asyncio.to_thread(run_diary_auth)
            except PermissionError as e:
                if "CAPTCHA_BLOKADA" in str(e):
                    logger.critical("!!! ZATRZYMUJĘ DODATEK Z POWODU BLOKADY (CAPTCHA) !!!")
                    # POPRAWKA #12 – graceful shutdown zamiast sys.exit() w coroutine
                    # sys.exit() przerywał event loop bez czyszczenia zasobów
                    stop_event.set()
                    break
                students, cookies = None, None
            except Exception as e:
                logger.error("Nieoczekiwany błąd podczas logowania: %s", e)
                students, cookies = None, None

            if students and cookies:
                await sync_diary_data(students, cookies)
                await asyncio.to_thread(run_messages_sync, students[0]["city"], students)

            await _run_size_monitor(ha)

            now_after = datetime.now()
            wd_after = now_after.weekday()

            if not _test_mode:
                if wd_after == 5:
                    next_h = next((h for h in (8, 16, 23) if h > now_after.hour), None)
                    if next_h:
                        wake_at = now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now_after + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("Cykl OK (Sobota) → następne pobieranie o %s (za ~%d min)", wake_at.strftime("%H:%M"), wait_time // 60)

                elif wd_after == 6:
                    next_h = next((h for h in (8, 12, 20) if h > now_after.hour), None)
                    if next_h:
                        wake_at = now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now_after + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("Cykl OK (Niedziela) → następne pobieranie o %s (za ~%d min)", wake_at.strftime("%H:%M"), wait_time // 60)

                else:
                    # POPRAWKA #8 – secrets.SystemRandom() nie istnieje w module secrets.
                    # Oryginał rzucał AttributeError przy każdym wykonaniu cyklu.
                    # Użycie secrets.randbelow() jest kryptograficznie bezpieczne i poprawne.
                    wait_time = 2400 + secrets.randbelow(1201)
                    next_run = now_after + timedelta(seconds=wait_time)
                    logger.info("Cykl OK → następny za ~%d min (o %s)", wait_time // 60, next_run.strftime("%H:%M"))
            else:
                # POPRAWKA #8 – identyczna poprawka dla gałęzi test_mode
                wait_time = 2400 + secrets.randbelow(1201)
                next_run = now_after + timedelta(seconds=wait_time)
                logger.info("[TEST MODE] Cykl OK → następny za ~%d min (o %s)", wait_time // 60, next_run.strftime("%H:%M"))

            for elapsed in range(0, wait_time, 10):
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=10)
                    break
                except asyncio.TimeoutError:
                    pass

                if (elapsed + 10) % 60 == 0:
                    await check_and_restore(ha)

    logger.info("Vultron zatrzymany (graceful shutdown).")

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamykanie…")
        sys.exit(0)

