from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import sqlite3
import sys
import threading
import time
import secrets
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
import aiosqlite
import httpx
from playwright.sync_api import sync_playwright
from websocket import create_connection

# ---------------------------------------------------------------------------
# Zmienne środowiskowe i ścieżki
# ---------------------------------------------------------------------------

os.environ["SE_STATS"] = "0"

DB_PATH = "/data/vultron.db"
VUL_PKL = "/data/vul.pkl"
BUL_PKL = "/data/bul.pkl"
OPTIONS_PATH = "/data/options.json"
HA_TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
HA_URL = "http://supervisor/core/api"
HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Wstępna inicjalizacja logowania
# ---------------------------------------------------------------------------

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


def trace(
    self: logging.Logger,
    message: str,
    *args: object,
    **kws: object,
) -> None:
    """Obsługa niestandardowego poziomu TRACE dla instancji Logger."""
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kws)  # type: ignore[attr-defined]  # noqa: E501


logging.Logger.trace = trace  # type: ignore[attr-defined]
logger = logging.getLogger("Vultron")

# Na start ustawiamy INFO, aby zalogować ewentualne braki plików
logger.setLevel(logging.INFO)

_fmt = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S",
)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_fh = logging.handlers.RotatingFileHandler(
    "/data/vultron.log",
    maxBytes=1_048_576,
    backupCount=5,
)
_fh.setFormatter(_fmt)
logger.addHandler(_ch)
logger.addHandler(_fh)

# Wyciszenie spamu z zewnętrznych bibliotek
logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Weryfikacja środowiska HA
# ---------------------------------------------------------------------------

if not os.path.exists(OPTIONS_PATH):
    logger.critical("Brak pliku options.json. Przerwano uruchamianie.")
    sys.exit(1)

if not HA_TOKEN:
    logger.critical(
        "SUPERVISOR_TOKEN nie jest ustawiony. "
        "Upewnij się, że skrypt działa w środowisku HA."
    )
    sys.exit(1)

with open(OPTIONS_PATH, encoding="utf-8") as _f:
    CONFIG: dict = json.load(_f)

if not CONFIG.get("username") or not CONFIG.get("password"):
    logger.critical(
        "Brak username lub password w options.json. Przerwano uruchamianie."
    )
    sys.exit(1)

_test_mode: bool = CONFIG.get("test_mode", False)

# ---------------------------------------------------------------------------
# Ustawienie docelowego poziomu logowania
# ---------------------------------------------------------------------------

_raw_debug: bool = CONFIG.get("debug", False)
_log_level_conf: str = CONFIG.get(
    "log_level", "debug" if _raw_debug else "info"
).lower()

_SENSITIVE_KEYS = frozenset({"password", "key", "access_token", "Authorization"})


def _mask_payload(data: object) -> object:
    """Maskuje wrażliwe klucze w słowniku przed zapisem do logów.

    Obsługuje zagnieżdżone struktury: dict, list, krotki.

    Args:
        data: Dowolny obiekt (zazwyczaj dict, list lub str).

    Returns:
        Kopia obiektu z wartościami wrażliwych kluczy zastąpionymi przez ``***``.
    """
    if isinstance(data, dict):
        return {
            k: "***" if k in _SENSITIVE_KEYS else _mask_payload(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return[_mask_payload(item) for item in data]
    return data


if _log_level_conf == "trace":
    logger.setLevel(TRACE_LEVEL)

    # Event Hooks — oficjalny mechanizm httpx, odporny na zmiany API biblioteki
    async def _trace_async_request(request: httpx.Request) -> None:
        """Hook: loguje wychodzące żądania async na poziomie TRACE."""
        logger.trace(  # type: ignore[attr-defined]
            "->[HTTP ASYNC] %s %s", request.method, request.url
        )
        if request.content:
            try:
                payload = json.loads(request.content)
                logger.trace(  # type: ignore[attr-defined]
                    "   Payload: %s", _mask_payload(payload)
                )
            except Exception:
                pass

    async def _trace_async_response(response: httpx.Response) -> None:
        """Hook: loguje przychodzące odpowiedzi async na poziomie TRACE."""
        await response.aread()
        logger.trace(  # type: ignore[attr-defined]
            "<- [HTTP ASYNC] %s %s | Kod: %s | Odpowiedź: %s",
            response.request.method,
            response.request.url,
            response.status_code,
            response.text[:1500],
        )

    def _trace_sync_request(request: httpx.Request) -> None:
        """Hook: loguje wychodzące żądania sync na poziomie TRACE."""
        logger.trace(  # type: ignore[attr-defined]
            "-> [HTTP SYNC]  %s %s", request.method, request.url
        )
        if request.content:
            try:
                payload = json.loads(request.content)
                logger.trace(  # type: ignore[attr-defined]
                    "   Payload: %s", _mask_payload(payload)
                )
            except Exception:
                pass

    def _trace_sync_response(response: httpx.Response) -> None:
        """Hook: loguje przychodzące odpowiedzi sync na poziomie TRACE."""
        logger.trace(  # type: ignore[attr-defined]
            "<- [HTTP SYNC]  %s %s | Kod: %s | Odpowiedź: %s",
            response.request.method,
            response.request.url,
            response.status_code,
            response.text[:1500],
        )

    # Globalne hooki dla klientów tworzonych w module
    _ASYNC_TRACE_HOOKS: dict = {
        "request": [_trace_async_request],
        "response": [_trace_async_response],
    }
    _SYNC_TRACE_HOOKS: dict = {
        "request":[_trace_sync_request],
        "response": [_trace_sync_response],
    }

elif _log_level_conf == "debug":
    logger.setLevel(logging.DEBUG)
    _ASYNC_TRACE_HOOKS = {}
    _SYNC_TRACE_HOOKS = {}

else:  # info lub cokolwiek innego
    logger.setLevel(logging.INFO)
    _ASYNC_TRACE_HOOKS = {}
    _SYNC_TRACE_HOOKS = {}

logger.info(
    "Log level ustawiony na: %s (%d)",
    logging.getLevelName(logger.level),
    logger.level,
)
if logger.level <= 5:  # TRACE_LEVEL = 5
    logger.trace("TRACE działa prawidłowo ✓")

# ---------------------------------------------------------------------------
# Stałe / cache
# ---------------------------------------------------------------------------

MAPA_STATUSOW: dict[int, str] = {
    0: "",
    1: "ZAST",
    2: "PRZEN",
    3: "ODWOL",
    4: "NIEOB",
}
MAPA_FREKWENCJI: dict[int, str] = {
    1: "Obecność",
    2: "Nieobecność",
    3: "Usprawiedliwiona",
    4: "Spóźnienie",
    5: "Spóźnienie uspraw.",
    6: "Szkolne",
    7: "Zwolnienie",
}
MAPA_TYP_TERMINARZA: dict[int, str] = {
    1: "Sprawdzian",
    2: "Kartkówka",
    3: "Klasówka",
    4: "Zadanie domowe",
}

_sent_hashes: OrderedDict[str, str] = OrderedDict()
_sent_hashes_lock = threading.Lock()
_SENT_HASHES_MAX = 2500

_PL_TRANS = str.maketrans("ąćęłńóśźż", "acelnoszz")

# Pre-kompilowane regexy
_RE_SLUG = re.compile(r"[^a-z0-9]+")
_RE_GRADE_IGNORE = re.compile(r"[A-F%]|NB|NP|BZ")
_RE_GRADE_VALUE = re.compile(r"(?<!\d)([1-6])(?:[.,](\d+))?(?!\d)")
_RE_CITY_URL = re.compile(r"uczen\.eduvulcan\.pl/([^/]+)")
_RE_VERSION = re.compile(r'version:\s*["\']?([^"\' ]+)')
_RE_LOVELACE_URL = re.compile(r"\?v=.*")

# Nowe regexy dla inteligentnego parsera HTML
_RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')
_RE_SPACES = re.compile(r' {2,}')

db_lock_sync = threading.Lock()

# ---------------------------------------------------------------------------
# Singleton klient sync dla publish_sensor_sync
# ---------------------------------------------------------------------------

_sync_ha_client: httpx.Client | None = None
_sync_ha_client_lock = threading.Lock()


def _get_sync_ha_client() -> httpx.Client:
    global _sync_ha_client
    with _sync_ha_client_lock:
        if _sync_ha_client is None or _sync_ha_client.is_closed:
            _sync_ha_client = httpx.Client(
                headers=HA_HEADERS,
                timeout=12,
                transport=httpx.HTTPTransport(retries=3),
                event_hooks=_SYNC_TRACE_HOOKS,
            )
    return _sync_ha_client

# ---------------------------------------------------------------------------
# Pomocnicze klasy i funkcje
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text: list[str] = []
        self.current_href = ""

    def is_safe_url(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip().lower()
        if u.startswith("javascript:") or u.startswith("data:") or u.startswith("vbscript:"):
            return False
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('br', 'p', 'div', 'li', 'tr'):
            self.text.append('\n')
        elif tag in ('b', 'strong'):
            self.text.append('**')
        elif tag in ('i', 'em'):
            self.text.append('*')
        elif tag == 'a':
            attr_dict = dict(attrs)
            href = attr_dict.get('href', '')
            if href and self.is_safe_url(href):
                self.current_href = href.strip()
        elif tag == 'img':
            attr_dict = dict(attrs)
            src = attr_dict.get('src', '')
            alt = attr_dict.get('alt', '')
            if src and self.is_safe_url(src):
                img_text = f" {alt} ({src}) " if alt else f" ({src}) "
                self.text.append(img_text)

    def handle_endtag(self, tag: str) -> None:
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

    def handle_data(self, d: str) -> None:
        self.text.append(d)

    def get_data(self) -> str:
        return "".join(self.text)


def slugify(text: str) -> str:
    if not text:
        return "unknown"
    return _RE_SLUG.sub("_", text.lower().translate(_PL_TRANS)).strip("_") or "unknown"


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


def _payload_hash(state: object, attrs_no_timestamp: dict) -> str:
    raw = json.dumps(
        {"state": state, "attributes": attrs_no_timestamp},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Zatrzymywanie awaryjne dodatku (Graceful Stop dla Supervisora)
# ---------------------------------------------------------------------------

async def fatal_error_stop(ha_client: httpx.AsyncClient, reason: str) -> None:
    """Zatrzymuje kontener za pośrednictwem API Supervisora, zapobiegając Crash Loopom."""
    logger.critical("KRYTYCZNY BŁĄD: %s. Wstrzymuję dodatek (Graceful Stop)...", reason)
    try:
        res = await ha_client.post(
            "http://supervisor/addons/self/stop",
            headers=HA_HEADERS,
            timeout=10,
        )
        if res.status_code == 200:
            logger.info("Zlecono zatrzymanie dodatku. Oczekuję na sygnał od Supervisora.")
        else:
            logger.warning("Nie udało się zatrzymać dodatku (Status %d): %s", res.status_code, res.text)
    except Exception as e:
        logger.error("Błąd podczas komunikacji z API Supervisora: %s", e)

# ---------------------------------------------------------------------------
# SQLite — schemat i operacje bazy
# ---------------------------------------------------------------------------

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
        typ TEXT, opis TEXT, autor TEXT,
        PRIMARY KEY(id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS remarks (
        remark_id TEXT, student_slug TEXT, data TEXT, tresc TEXT,
        autor TEXT, kategoria TEXT, punkty TEXT, typ TEXT,
        PRIMARY KEY(remark_id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS achievements (
        achievement_id TEXT, student_slug TEXT, tresc TEXT,
        PRIMARY KEY(achievement_id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS messages (
        key TEXT PRIMARY KEY, student_slug TEXT, data TEXT,
        nadawca TEXT, temat TEXT, tresc TEXT, przeczytana INTEGER)""",
    """CREATE TABLE IF NOT EXISTS ha_cache (
        entity_id TEXT PRIMARY KEY,
        state TEXT,
        attributes_json TEXT)""",
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
        PRIMARY KEY(student_slug, data))""",
    "CREATE INDEX IF NOT EXISTS idx_timetable_slug_data ON timetable(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_slug_data ON schedule(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_frequency_slug_data ON frequency(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_grades_slug ON grades(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_remarks_slug ON remarks(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_messages_slug ON messages(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_freq_stats_slug ON frequency_stats(student_slug)",
]

# Globalna instancja asynchronicznej bazy danych w celu optymalizacji I/O dysku
_GLOBAL_DB: aiosqlite.Connection | None = None
async def init_global_db(path: str = DB_PATH) -> None:
    global _GLOBAL_DB
    if _GLOBAL_DB is not None:
        await _GLOBAL_DB.close()
    _GLOBAL_DB = await aiosqlite.connect(path, timeout=30.0)
    await _GLOBAL_DB.execute("PRAGMA journal_mode=WAL")
    await _GLOBAL_DB.execute("PRAGMA synchronous=NORMAL")
    for stmt in _DB_DDL:
        await _GLOBAL_DB.execute(stmt)
    await _GLOBAL_DB.commit()

async def close_global_db() -> None:
    global _GLOBAL_DB
    if _GLOBAL_DB:
        await _GLOBAL_DB.close()
        _GLOBAL_DB = None

class AsyncDB:
    """Asynchroniczny Context Manager dla bazy SQLite.
    Zoptymalizowany pod pojedyncze połączenie _GLOBAL_DB.
    """
    async def __aenter__(self) -> aiosqlite.Connection:
        if _GLOBAL_DB is None:
            raise RuntimeError("Global DB not initialized")
        return _GLOBAL_DB

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if _GLOBAL_DB and exc_type is None:
            await _GLOBAL_DB.commit()


def db_connect() -> sqlite3.Connection:
    """Połączenie synchroniczne dla wątków (np. dla messages)"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


async def _save_to_cache(
    entity_id: str, state: object, attrs: dict
) -> None:
    try:
        async with AsyncDB() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO ha_cache"
                " (entity_id, state, attributes_json)"
                " VALUES (?, ?, ?)",
                (
                    entity_id,
                    str(state),
                    json.dumps(attrs, ensure_ascii=False),
                ),
            )
    except Exception as e:
        logger.error("Błąd zapisu cache %s: %s", entity_id, e)


def _save_to_cache_sync(
    entity_id: str, state: object, attrs: dict
) -> None:
    with db_lock_sync:
        try:
            with closing(db_connect()) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ha_cache"
                    " (entity_id, state, attributes_json)"
                    " VALUES (?, ?, ?)",
                    (
                        entity_id,
                        str(state),
                        json.dumps(attrs, ensure_ascii=False),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Błąd zapisu cache sync %s: %s", entity_id, e)

# ---------------------------------------------------------------------------
# Publikowanie sensorów HA
# ---------------------------------------------------------------------------


async def publish_sensor(
    client: httpx.AsyncClient,
    entity_id: str,
    state: object,
    friendly_name: str,
    extra_attrs: dict | None = None,
) -> None:
    attrs = {
        "friendly_name": friendly_name,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }

    h = _payload_hash(
        state, {k: v for k, v in attrs.items() if k != "last_update"}
    )
    with _sent_hashes_lock:
        if _sent_hashes.get(entity_id) == h:
            return

    try:
        res = await client.post(
            f"{HA_URL}/states/{entity_id}",
            headers=HA_HEADERS,
            json={"state": state, "attributes": attrs},
            timeout=12,
        )
        if res.status_code not in (200, 201):
            logger.error(
                "HTTP %d @ %s → %s | %s",
                res.status_code, entity_id, state, res.text[:200],
            )
            return

        await _save_to_cache(entity_id, state, attrs)

        with _sent_hashes_lock:
            if len(_sent_hashes) >= _SENT_HASHES_MAX:
                _sent_hashes.popitem(last=False)
            _sent_hashes[entity_id] = h
            _sent_hashes.move_to_end(entity_id)

        logger.debug("Sensor %s → %s", entity_id, state)
    except httpx.TimeoutException:
        logger.warning("Timeout: %s", entity_id)
    except httpx.ConnectError:
        logger.warning("Brak połączenia HA: %s", entity_id)
    except Exception as exc:
        logger.exception("Błąd wysyłki %s: %s", entity_id, exc)


def publish_sensor_sync(
    entity_id: str,
    state: object,
    friendly_name: str,
    extra_attrs: dict | None = None,
) -> None:
    attrs = {
        "friendly_name": friendly_name,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }
    h = _payload_hash(
        state, {k: v for k, v in attrs.items() if k != "last_update"}
    )
    with _sent_hashes_lock:
        if _sent_hashes.get(entity_id) == h:
            return

    try:
        res = _get_sync_ha_client().post(
            f"{HA_URL}/states/{entity_id}",
            json={"state": state, "attributes": attrs},
        )
        if res.status_code not in (200, 201):
            logger.error(
                "SYNC HTTP %d @ %s → %s | %s",
                res.status_code, entity_id, state, res.text[:200],
            )
            return

        _save_to_cache_sync(entity_id, state, attrs)

        with _sent_hashes_lock:
            if len(_sent_hashes) >= _SENT_HASHES_MAX:
                _sent_hashes.popitem(last=False)
            _sent_hashes[entity_id] = h
            _sent_hashes.move_to_end(entity_id)

        logger.debug("Sensor sync %s → %s", entity_id, state)
    except Exception as exc:
        logger.warning("Błąd publish_sensor_sync %s: %s", entity_id, exc)
        # FIX: Jeśli wystąpi błąd (np. padło gniazdo HTTPX), zamykamy i zrzucamy klienta,
        # by utworzył się na nowo podczas następnego wywołania.
        global _sync_ha_client
        with _sync_ha_client_lock:
            if _sync_ha_client is not None:
                try:
                    _sync_ha_client.close()
                except Exception:
                    pass
                _sync_ha_client = None

# ---------------------------------------------------------------------------
# Odtwarzanie cache po restarcie HA
# ---------------------------------------------------------------------------


async def restore_entities_from_cache(ha: httpx.AsyncClient) -> None:
    try:
        async with AsyncDB() as conn:
            cursor = await conn.execute(
                "SELECT entity_id, state, attributes_json FROM ha_cache"
            )
            rows = await cursor.fetchall()

        restored = 0
        for entity_id, state, attrs_json in rows:
            try:
                attrs = json.loads(attrs_json)
                h = _payload_hash(
                    state,
                    {k: v for k, v in attrs.items() if k != "last_update"},
                )
                with _sent_hashes_lock:
                    _sent_hashes[entity_id] = h
                    _sent_hashes.move_to_end(entity_id)

                res = await ha.post(
                    f"{HA_URL}/states/{entity_id}",
                    headers=HA_HEADERS,
                    json={"state": state, "attributes": attrs},
                    timeout=5,
                )
                if res.status_code in (200, 201):
                    restored += 1
            except Exception as e:
                logger.debug(
                    "Nie udało się odtworzyć %s: %s", entity_id, e
                )

        if restored > 0:
            logger.info(
                "Sukces: Błyskawicznie przywrócono %d encji z bazy.",
                restored,
            )
    except Exception as e:
        logger.error("Błąd bazy danych przy odtwarzaniu cache: %s", e)


_last_ha_installation_id: str | None = None


async def check_and_restore(ha: httpx.AsyncClient) -> None:
    global _last_ha_installation_id
    try:
        r = await ha.get(f"{HA_URL}/config", timeout=4)
        if r.status_code != 200:
            logger.debug("check_and_restore: HA API niedostępne (%d)", r.status_code)
            return
        ha_config = r.json()
        current_id = ha_config.get("installation_id") or ha_config.get("uuid")
        if current_id is None:
            r2 = await ha.get(
                f"{HA_URL}/states/sensor.vultron_system_monitor", timeout=4
            )
            if r2.status_code == 404:
                logger.warning("Wykryto restart HA (brak markera)! Wstrzykuję stan z bazy...")
                await restore_entities_from_cache(ha)
            return
        if _last_ha_installation_id is None:
            _last_ha_installation_id = current_id
            return
        if _last_ha_installation_id != current_id:
            logger.warning(
                "Wykryto restart Home Assistanta (installation_id zmienił się)! "
                "Wstrzykuję stan z bazy..."
            )
            _last_ha_installation_id = current_id
            await restore_entities_from_cache(ha)
    except httpx.TimeoutException:
        logger.debug("check_and_restore: timeout odpytywania HA")
    except httpx.ConnectError:
        logger.debug("check_and_restore: brak połączenia z HA")
    except Exception as e:
        logger.warning("check_and_restore: nieoczekiwany błąd: %s", e)

# ---------------------------------------------------------------------------
# Playwright — kontekst przeglądarki (stealth)
# ---------------------------------------------------------------------------


def _get_browser_context(headless: bool = True) -> tuple:
    pw = sync_playwright().start()
    chrome_path = os.getenv(
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/usr/bin/chromium"
    )
    browser = pw.chromium.launch(
        executable_path=chrome_path,
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--disable-infobars",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=site-per-process",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ],
    )

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        locale="pl-PL",
        timezone_id="Europe/Warsaw",
        color_scheme="light",
        reduced_motion="no-preference",
        bypass_csp=True,
        java_script_enabled=True,
        ignore_https_errors=True,
    )

    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = window.chrome || {};
        window.chrome.runtime = { PlatformOs: 'win', PlatformArch: 'x86-64', PlatformNaclArch: 'x86-64' };
        window.chrome.app = {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        };
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins =[];
                for (let i = 0; i < 5 + Math.floor(Math.random() * 5); i++) {
                    plugins.push({ length: Math.floor(Math.random() * 10) + 1 });
                }
                return plugins;
            }
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['pl-PL', 'pl', 'en-US', 'en'] });
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.apply(this, arguments);
        };
        const originalGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function () {
            const data = originalGetChannelData.apply(this, arguments);
            for (let i = 0; i < data.length; i += 100) {
                data[i] += (Math.random() * 0.00001) - 0.000005;
            }
            return data;
        };
    """)

    page = context.new_page()
    return page, context, browser, pw

# ---------------------------------------------------------------------------
# Lovelace — kopiowanie zasobów i rejestracja kart
# ---------------------------------------------------------------------------


def copy_resources() -> None:
    target = "/config/www/vultron"
    os.makedirs(target, exist_ok=True)
    src = "/app"
    n = 0
    if os.path.exists(src):
        for f in os.listdir(src):
            if f.lower().endswith(".js"):
                shutil.copy(
                    os.path.join(src, f), os.path.join(target, f)
                )
                n += 1
    logger.info("Skopiowano %d plików JS do /local/vultron/", n)


async def wait_for_ha_api(max_retries: int = 60) -> None:
    async with httpx.AsyncClient() as c:
        for attempt in range(1, max_retries + 1):
            try:
                r = await c.get(
                    f"{HA_URL}/config", headers=HA_HEADERS, timeout=5
                )
                if r.status_code == 200:
                    logger.info("HA API gotowe.")
                    return
            except Exception:
                pass
            logger.info(
                "Czekam na HA API… (%d/%d)", attempt, max_retries
            )
            await asyncio.sleep(5)
    logger.critical(
        "HA API niedostępne po %d próbach. Zatrzymuję.", max_retries
    )
    sys.exit(1)


def run_setup_ui() -> None:
    log = logging.getLogger("UI-SETUP")

    def _version() -> str:
        for p in ("config.yaml", "/app/config.yaml"):
            try:
                with open(p) as f:
                    m = _RE_VERSION.search(f.read())
                    if m:
                        return m.group(1)
            except OSError:
                pass
        return "1.0"

    version = _version()
    ws = None
    for attempt in range(10):
        try:
            ws = create_connection(
                "ws://supervisor/core/websocket", timeout=10
            )
            ws.recv()
            ws.send(
                json.dumps({"type": "auth", "access_token": HA_TOKEN})
            )
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
        existing = {
            _RE_LOVELACE_URL.sub("", r["url"]): (r["id"], r["url"])
            for r in raw
        }
        src_dir = "/app" if os.path.exists("/app") else "."
        cards =[
            f for f in os.listdir(src_dir)
            if f.startswith("vultron-") and f.endswith(".js")
        ]
        for msg_id, card in enumerate(cards, start=2):
            base = f"/local/vultron/{card}"
            versioned = f"{base}?v={version}"
            if base in existing:
                rid, cur_url = existing[base]
                if cur_url != versioned:
                    log.info("Aktualizacja: %s → v%s", card, version)
                    ws.send(json.dumps({
                        "id": msg_id,
                        "type": "lovelace/resources/update",
                        "resource_id": rid,
                        "url": versioned,
                    }))
                    ws.recv()
            else:
                log.info("Rejestracja: %s v%s", card, version)
                ws.send(json.dumps({
                    "id": msg_id,
                    "type": "lovelace/resources/create",
                    "res_type": "module",
                    "url": versioned,
                }))
                ws.recv()
        log.info("Lovelace skonfigurowany.")
    except Exception as e:
        log.error("Błąd rejestracji: %s", e)
    finally:
        ws.close()

# ---------------------------------------------------------------------------
# Autoryzacja dziennika (Playwright — sync)
# ---------------------------------------------------------------------------


def _vulcan_login(page: object, log_prefix: str = "[AUTH]") -> str:
    page.goto("https://eduvulcan.pl/logowanie", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)

    if page.locator("#Alias").count() > 0:
        logger.info("%s Wypełniam formularz logowania…", log_prefix)
        page.wait_for_selector("#Alias", timeout=30000)
        page.fill("#Alias", CONFIG.get("username", ""))
        page.press("#Alias", "Enter")
        page.wait_for_selector("#Password", timeout=30000)
        page.fill("#Password", CONFIG.get("password", ""))
        page.press("#Password", "Enter")

    try:
        page.wait_for_selector("a[href*='dziennik']", timeout=40000)
    except Exception as ex:
        err_dir = "/config/www/vultron"
        os.makedirs(err_dir, exist_ok=True)
        err_path = os.path.join(err_dir, "vultron_auth_error.png")
        page.screenshot(path=err_path, full_page=True)
        logger.error(
            "%s Nie znaleziono kafelka 'Dziennik'. Zrzut: %s", log_prefix, err_path
        )
        logger.error(
            "%s Sprawdź: http://<HA_IP>:8123/local/vultron/vultron_auth_error.png",
            log_prefix,
        )
        raise ex

    link = page.get_attribute("a[href*='dziennik']", "href") or ""
    if link.startswith("/"):
        link = urljoin(page.url, link)
    logger.debug("%s Pełny link do dziennika: %s", log_prefix, link)
    page.goto(link, timeout=45000)
    page.wait_for_load_state("networkidle", timeout=30000)
    return link


def run_diary_auth() -> tuple[list | None, list | None]:
    page, context, browser, pw = _get_browser_context(headless=True)

    try:
        logger.info("[AUTH] Logowanie…")
        _vulcan_login(page, log_prefix="[AUTH]")

        m = _RE_CITY_URL.search(page.url)
        if not m:
            logger.error(
                "[AUTH] Brak nazwy miasta w URL: %s", page.url
            )
            return None, None
        city = m.group(1)

        page.goto(
            f"https://uczen.eduvulcan.pl/{city}/api/Context",
            timeout=30000,
        )
        page.wait_for_load_state("networkidle", timeout=20000)
        context_raw = page.inner_text("body")
        _raw = context_raw.strip().lower()
        if (_raw.startswith("<!doctype") or _raw.startswith("<html")) and not _raw.startswith("{"):
            logger.warning(
                "[AUTH] /api/Context zwrócił HTML zamiast JSON "
                "(prawdopodobnie 502/503 lub CAPTCHA): %s", context_raw[:300]
            )
            raise ConnectionError("SERVER_ERROR_OR_CAPTCHA")

        try:
            context_data = json.loads(context_raw)
        except json.JSONDecodeError as e:
            logger.critical(
                "[AUTH] Krytyczny błąd: Nie można sparsować /api/Context "
                "(Prawdopodobnie CAPTCHA lub trwała blokada serwera). "
                "Wymuszam całkowite wyłączenie dodatku!"
            )
            logger.debug(
                "[AUTH] Surowa odpowiedź: %s", context_raw[:500]
            )
            raise PermissionError("CAPTCHA_BLOKADA") from e

        with httpx.Client(timeout=15) as session:
            for cookie in context.cookies():
                session.cookies.set(cookie["name"], cookie["value"])

            students: list[dict] =[]
            for u in context_data.get("uczniowie",[]):
                key = u.get("key")
                id_dz = str(u.get("idDziennik"))
                res = session.get(
                    f"https://uczen.eduvulcan.pl/{city}"
                    "/api/OkresyKlasyfikacyjne",
                    params={"key": key, "idDziennik": id_dz},
                )
                if res.status_code != 200:
                    logger.warning(
                        "Brak okresów dla: %s", u.get("uczen")
                    )
                    continue
                okresy = res.json()
                curr_p = okresy[-1]["id"] if okresy else None
                for o in okresy:
                    try:
                        if (
                            datetime.strptime(
                                o["dataOd"][:19], "%Y-%m-%dT%H:%M:%S"
                            )
                            <= datetime.now()
                            <= datetime.strptime(
                                o["dataDo"][:19], "%Y-%m-%dT%H:%M:%S"
                            )
                        ):
                            curr_p = o["id"]
                            break
                    except (ValueError, KeyError):
                        continue
                students.append({
                    "slug": slugify(u.get("uczen", "")),
                    "uczen": u.get("uczen", ""),
                    "city": city,
                    "key": key,
                    "idDziennik": id_dz,
                    "periodId": curr_p,
                    "klasa": u.get("oddzial", ""),
                    "globalKeySkrzynka": u.get("globalKeySkrzynka", ""),
                })

            cookies = context.cookies()
            with open(VUL_PKL, "w", encoding="utf-8") as f:
                json.dump(
                    {"cookies": cookies, "students": students},
                    f,
                    ensure_ascii=False,
                )
            logger.info("[AUTH] OK – %d uczniów", len(students))
            return students, cookies

    except Exception as e:
        logger.error("[AUTH] Błąd: %s", e, exc_info=True)
        try:
            err_dir = "/config/www/vultron"
            os.makedirs(err_dir, exist_ok=True)
            page.screenshot(
                path=os.path.join(
                    err_dir, "vultron_auth_error_fatal.png"
                ),
                full_page=True,
            )
        except Exception:
            pass
        raise

    finally:
        try:
            context.close()
            browser.close()
            pw.stop()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Fetch helpers — async sekcje danych
# ---------------------------------------------------------------------------


async def _fetch_grades(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, id_dz, name = (
        s["slug"], s["key"], s["idDziennik"], s["uczen"]
    )
    logger.info("--> [%s] Pobieram oceny...", name)

    res_per = await client.get(
        f"{base}/api/OkresyKlasyfikacyjne",
        params={"key": key, "idDziennik": id_dz},
    )
    if res_per.status_code != 200:
        logger.warning("[%s] błąd okresów: %d", name, res_per.status_code)
        return

    for period in res_per.json():
        p_id = str(period.get("id", ""))
        p_num = period.get("numerOkresu", 0)

        res_g = await client.get(
            f"{base}/api/Oceny",
            params={"key": key, "idOkresKlasyfikacyjny": p_id},
        )
        if res_g.status_code != 200:
            continue

        subjects: dict[str, list] = {}
        new_g = 0

        async with AsyncDB() as conn:
            for p_item in (res_g.json().get("ocenyPrzedmioty") or[]):
                subj = p_item.get("przedmiotNazwa", "Inne")
                for kol in (p_item.get("kolumnyOcenyCzastkowe") or[]):
                    id_k = str(kol.get("idKolumny", "0"))
                    desc = (
                        f"{kol.get('kategoriaKolumny', '')}: "
                        f"{kol.get('nazwaKolumny', '')}"
                    ).strip(": ")
                    for o in (kol.get("oceny") or[]):
                        v = str(o.get("wpis", ""))
                        dt = str(o.get("dataOceny", ""))
                        cur = await conn.execute(
                            "INSERT OR IGNORE INTO grades"
                            " VALUES (?,?,?,?,?,?,?)",
                            (id_k, slug, subj, v, dt, desc, p_id),
                        )
                        if cur.rowcount > 0:
                            new_g += 1
                        else:
                            await conn.execute(
                                "UPDATE grades SET ocena=?, data=?, opis=?"
                                " WHERE id_kolumny=? AND student_slug=? AND period_id=?",
                                (v, dt, desc, id_k, slug, p_id),
                            )
                        subjects.setdefault(subj, []).append(
                            {"w": v, "d": dt[:5], "i": clean_text(desc)}
                        )

        lista =[]
        for subj_name, grades in subjects.items():
            vals: list[float] =[]
            for g in grades:
                w_str = str(g["w"]).strip().upper()
                if _RE_GRADE_IGNORE.search(w_str):
                    continue
                m_dec = _RE_GRADE_VALUE.search(w_str)
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

            lista.append({
                "przedmiot": subj_name,
                "oceny": grades,
                "srednia": round(sum(vals) / len(vals), 2) if vals else None,
            })

        await publish_sensor(
            ha,
            f"sensor.vultron_oceny_{slug}_p{p_num}",
            new_g,
            f"Oceny: {name} (P{p_num})",
            {
                "lista_przedmiotow": lista,
                "period_number": int(p_num),
                "student_slug": slug,
                "active_period": p_id == str(s["periodId"]),
            },
        )


async def _fetch_schedule(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram plan lekcji...", name)
    now = datetime.now()

    res = await client.get(
        f"{base}/api/PlanZajec",
        params={
            "key": key,
            "dataOd": (
                now - timedelta(days=now.weekday() + 7)
            ).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": (
                now + timedelta(days=21)
            ).strftime("%Y-%m-%dT23:59:59.999Z"),
            "zakresDanych": "2",
        },
    )
    if res.status_code != 200:
        logger.warning("[%s] błąd planu: %d", name, res.status_code)
        return

    tasks =[]
    async with AsyncDB() as conn:
        for lesson in res.json():
            st = MAPA_STATUSOW.get(int(lesson.get("adnotacja", 0)), "")
            inf = " ".join(
                (c.get("informacjeNieobecnosc") or "").lower()
                for c in (lesson.get("zmiany") or[])
            )
            if "zwolnieni" in inf or "okienko" in inf:
                st = "ODWOL"
            data_raw = lesson.get("data", "")
            godz_od = lesson.get("godzinaOd", "T00:00")
            godz_do = lesson.get("godzinaDo", "T00:00")
            await conn.execute(
                "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"{slug}_{data_raw}_{godz_od}",
                    slug,
                    data_raw.split("T")[0],
                    (
                        f"{godz_od.split('T')[-1][:5]}"
                        f"-{godz_do.split('T')[-1][:5]}"
                    ),
                    lesson.get("przedmiot") or "Zajęcia",
                    lesson.get("sala", ""),
                    lesson.get("prowadzacy", ""),
                    st,
                ),
            )

        monday = now - timedelta(days=now.weekday())
        weeks = {
            "prev": (monday - timedelta(7), monday - timedelta(1)),
            "curr": (monday, monday + timedelta(6)),
            "next": (monday + timedelta(7), monday + timedelta(13)),
        }
        for suf, (sd, ed) in weeks.items():
            cursor = await conn.execute(
                "SELECT data,godzina,przedmiot,sala,prowadzacy,status"
                " FROM schedule"
                " WHERE student_slug=? AND data BETWEEN ? AND ?"
                " ORDER BY data,godzina",
                (slug, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")),
            )
            rows = await cursor.fetchall()
            proc = [
                {
                    "d": r[0], "g": r[1], "p": r[2],
                    "s": r[3], "n": r[4], "st": r[5],
                }
                for r in rows
            ]
            today = now.strftime("%Y-%m-%d")
            state = (
                len([e for e in proc if e["d"] == today])
                if suf == "curr"
                else len(proc)
            )
            tasks.append(publish_sensor(
                ha,
                f"sensor.vultron_plan_{slug}_{suf}",
                state,
                f"Plan {suf}: {name}",
                {"lekcje": proc},
            ))

    await asyncio.gather(*tasks, return_exceptions=True)


async def _fetch_timetable(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram terminarz...", name)
    now = datetime.now()
    last_day_prev_month = now.replace(day=1) - timedelta(days=1)

    res = await client.get(
        f"{base}/api/SprawdzianyZadaniaDomowe",
        params={
            "key": key,
            "dataOd": last_day_prev_month.strftime(
                "%Y-%m-%dT00:00:00.000Z"
            ),
            "dataDo": (now + timedelta(days=61)).strftime(
                "%Y-%m-%dT23:59:59.999Z"
            ),
        },
    )
    if res.status_code != 200:
        logger.warning("[%s] błąd terminarza: %d", name, res.status_code)
        return

    items = res.json()

    async def _detail(item: dict) -> None:
        item_id = item.get("id")
        if not item_id:
            return
        ep = (
            "ZadanieDomoweSzczegoly"
            if item.get("typ") == 4
            else "SprawdzianSzczegoly"
        )
        dj = {}
        try:
            dr = await client.get(
                f"{base}/api/{ep}",
                params={"key": key, "id": item_id},
            )
            if dr.status_code == 200:
                dj = dr.json()
        except httpx.RequestError as exc:
            logger.warning(
                "[%s] błąd szczegółów terminarza %s: %s",
                name, item_id, exc,
            )

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

        if czysty_opis == "Brak opisu" and "iframe" in raw_opis.lower():
            czysty_opis = "[Wstawiono załącznik - sprawdź treść w oficjalnej aplikacji]"

        przedmiot = dj.get("przedmiotNazwa") or item.get("przedmiotNazwa", "")
        autor = dj.get("nauczycielImieNazwisko") or item.get("nauczycielImieNazwisko", "")

        async with AsyncDB() as conn2:
            await conn2.execute(
                "INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
                (
                    str(item_id),
                    slug,
                    data,
                    przedmiot,
                    MAPA_TYP_TERMINARZA.get(item.get("typ"), "Inne"),
                    czysty_opis,
                    autor,
                ),
            )

    _sem = asyncio.Semaphore(5)

    async def _detail_safe(item: dict) -> None:
        async with _sem:
            await _detail(item)

    results = await asyncio.gather(*[_detail_safe(i) for i in items], return_exceptions=True)
    for idx, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("[%s] błąd szczegółów terminarza pozycja %d: %s", name, idx, r)

    async with AsyncDB() as conn:
        cursor = await conn.execute(
            "SELECT data,przedmiot,typ,opis,autor FROM timetable"
            " WHERE student_slug=? AND data>=? ORDER BY data",
            (slug, now.strftime("%Y-%m-%d")),
        )
        rows = await cursor.fetchall()

    await publish_sensor(
        ha,
        f"sensor.vultron_terminarz_{slug}",
        len(rows),
        f"Terminarz: {name}",
        {
            "lista":[
                {
                    "data": r[0].split("T")[0],
                    "przedmiot": r[1],
                    "typ": r[2],
                    "opis": r[3],
                    "autor": r[4],
                }
                for r in rows
            ]
        },
    )


async def _fetch_remarks(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram uwagi...", name)

    res = await client.get(f"{base}/api/Uwagi", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd uwag: %d", name, res.status_code)
        return

    async with AsyncDB() as conn:
        for item in res.json():
            item_id = item.get("id")
            if not item_id:
                continue
            tr = item.get("tresc", "")
            typ_u = (
                "pozytywna" if "pochwa" in tr.lower()
                else "negatywna" if "uwaga" in tr.lower()
                else "informacja"
            )
            await conn.execute(
                "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(item_id),
                    slug,
                    item.get("data", "").split("T")[0],
                    tr,
                    item.get("autor", ""),
                    item.get("kategoria", ""),
                    str(item.get("liczbaPunktow") or ""),
                    typ_u,
                ),
            )

        cursor = await conn.execute(
            "SELECT data,tresc,autor,kategoria,punkty,typ,remark_id"
            " FROM remarks WHERE student_slug=? ORDER BY data DESC",
            (slug,),
        )
        lista = [
            {
                "data": r[0], "tresc": r[1], "autor": r[2],
                "kategoria": r[3], "punkty": r[4], "typ": r[5],
                "id": r[6],
            }
            for r in await cursor.fetchall()
        ]

    await publish_sensor(
        ha,
        f"sensor.vultron_uwagi_{slug}",
        len(lista),
        f"Uwagi: {name}",
        {"uwagi": lista},
    )


async def _fetch_frequency(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram frekwencję...", name)
    now = datetime.now()

    try:
        res_f, res_p, res_fs = await asyncio.gather(
            client.get(
                f"{base}/api/Frekwencja",
                params={
                    "key": key,
                    "dataOd": (now - timedelta(14)).strftime(
                        "%Y-%m-%dT00:00:00.000Z"
                    ),
                    "dataDo": now.strftime("%Y-%m-%dT23:59:59.999Z"),
                },
            ),
            client.get(f"{base}/api/Przedmioty", params={"key": key}),
            client.get(
                f"{base}/api/FrekwencjaStatystyki",
                params={"key": key, "idPrzedmiot": -1},
            ),
        )
    except Exception as e:
        logger.warning("[%s] błąd pobierania frekwencji (sieć): %s", name, e)
        return

    przedmioty: list =[]
    if res_p.status_code == 200:
        try:
            przedmioty = res_p.json()
        except Exception:
            przedmioty =[]
    else:
        logger.warning(
            "[%s] błąd pobierania przedmiotów: %d", name, res_p.status_code
        )

    per_subject_list =[p for p in przedmioty if p.get("id", -1) != -1]
    _freq_sem = asyncio.Semaphore(5)

    async def _fetch_subject_stats(p: dict) -> httpx.Response:
        async with _freq_sem:
            return await client.get(
                f"{base}/api/FrekwencjaStatystyki",
                params={"key": key, "idPrzedmiot": p["id"]},
            )

    per_subject_results = await asyncio.gather(
        *[_fetch_subject_stats(p) for p in per_subject_list],
        return_exceptions=True,
    )

    def _parse_rows(fsd: dict) -> list:
        return[
            {
                "k": MAPA_FREKWENCJI.get(
                    row.get("kategoriaFrekwencji"), "Inna"
                ),
                "m": {
                    str(m.get("miesiac", "")): m.get("wartosc", 0)
                    for m in (row.get("miesiace") or [])
                },
                "s1": (lambda o: o[0] if len(o) > 0 else 0)(row.get("okresy", [0, 0])),
                "s2": (lambda o: o[1] if len(o) > 1 else 0)(row.get("okresy", [0, 0])),
                "r": row.get("razem", 0),
            }
            for row in (fsd.get("statystyki") or[])
        ]

    freq_wpisy: list =[]
    freq_ok = False
    stats_global: dict = {}
    stats_per_subject: list = []
    index_subjects: list =[]

    async with AsyncDB() as conn:
        today = now.strftime("%Y-%m-%d")

        if res_f.status_code == 200:
            recs = res_f.json()
            if isinstance(recs, dict):
                recs = recs.get("oddzialy") or[]
            for fi in recs:
                fi_data = fi.get("data", "")
                fi_godz = fi.get("godzinaOd", "")
                if fi_data and fi_godz:
                    await conn.execute(
                        "INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                        (
                            f"{slug}_{fi_data}_{fi_godz}",
                            slug,
                            fi_data.split("T")[0],
                            fi_godz.split("T")[-1][:5],
                            int(fi.get("kategoriaFrekwencji", 0)),
                        ),
                    )

            since = (now - timedelta(14)).strftime("%Y-%m-%d")
            cursor = await conn.execute(
                "SELECT data,godzina,kategoria FROM frequency"
                " WHERE student_slug=? AND data>=? ORDER BY data DESC",
                (slug, since),
            )
            freq_wpisy = [
                {"d": r[0], "t": r[1], "k": int(r[2])}
                for r in await cursor.fetchall()
            ]
            freq_ok = True
        else:
            logger.warning(
                "[%s] błąd frekwencji: %d", name, res_f.status_code
            )

        if res_fs.status_code == 200:
            fsd_all = res_fs.json()
            rows_all = _parse_rows(fsd_all)
            pct_all = fsd_all.get("podsumowanie", 0)

            await conn.execute(
                "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                (
                    f"{slug}_-1_{today}",
                    slug, today, -1, "Wszystkie",
                    pct_all,
                    json.dumps(rows_all, ensure_ascii=False),
                ),
            )

            index_subjects = ([{"id": -1, "nazwa": "Wszystkie"}]
                +[
                    {"id": p["id"], "nazwa": p["nazwa"]}
                    for p in per_subject_list
                ]
            )
            stats_global = {"pct": pct_all, "rows": rows_all}

            for p, res in zip(per_subject_list, per_subject_results):
                if isinstance(res, Exception):
                    logger.warning(
                        "[%s] błąd statystyk dla %s: %s",
                        name, p.get("nazwa"), res,
                    )
                    continue
                if res.status_code != 200:
                    logger.warning(
                        "[%s] błąd statystyk dla %s: %d",
                        name, p.get("nazwa"), res.status_code,
                    )
                    continue
                try:
                    fsd_p = res.json()
                    pct_p = fsd_p.get("podsumowanie")
                    if pct_p is None:
                        logger.debug(
                            "[%s] brak statystyk dla %s"
                            " (podsumowanie=null), pomijam",
                            name, p.get("nazwa"),
                        )
                        continue
                    rows_p = _parse_rows(fsd_p)
                    await conn.execute(
                        "INSERT OR REPLACE INTO frequency_stats"
                        " VALUES (?,?,?,?,?,?,?)",
                        (
                            f"{slug}_{p['id']}_{today}",
                            slug, today,
                            p["id"], p["nazwa"], pct_p,
                            json.dumps(rows_p, ensure_ascii=False),
                        ),
                    )
                    stats_per_subject.append({
                        "slug_p": slugify(p["nazwa"]),
                        "pct_p": pct_p,
                        "rows_p": rows_p,
                        "pid": p["id"],
                        "pnazwa": p["nazwa"],
                    })
                except Exception as e:
                    logger.warning(
                        "[%s] błąd parsowania %s: %s",
                        name, p.get("nazwa"), e,
                    )
        else:
            logger.warning(
                "[%s] błąd statystyk: %d", name, res_fs.status_code
            )

    if freq_ok:
        await publish_sensor(
            ha,
            f"sensor.vultron_freq_{slug}",
            0,
            f"Frekwencja: {name}",
            {"wpisy": freq_wpisy},
        )

    if stats_global:
        await publish_sensor(
            ha,
            f"sensor.vultron_stats_{slug}",
            stats_global["pct"],
            f"Statystyki: {name}",
            {
                "unit_of_measurement": "%",
                "rows": stats_global["rows"],
                "przedmioty": index_subjects,
            },
        )

    if stats_per_subject:
        await asyncio.gather(
            *[
                publish_sensor(
                    ha,
                    f"sensor.vultron_stats_{slug}_{sp['slug_p']}",
                    sp["pct_p"],
                    f"Statystyki {sp['pnazwa']}: {name}",
                    {
                        "unit_of_measurement": "%",
                        "rows": sp["rows_p"],
                        "przedmiot_id": sp["pid"],
                        "przedmiot_nazwa": sp["pnazwa"],
                    },
                )
                for sp in stats_per_subject
            ],
            return_exceptions=True,
        )


async def _fetch_achievements(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram osiągnięcia...", name)

    res = await client.get(f"{base}/api/Osiagniecia", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd osiągnięć: %d", name, res.status_code)
        return

    async with AsyncDB() as conn:
        for item in res.json():
            item_id = item.get("id")
            if not item_id:
                continue
            await conn.execute(
                "INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                (str(item_id), slug, item.get("tresc", "")),
            )

        cursor = await conn.execute(
            "SELECT achievement_id,tresc FROM achievements"
            " WHERE student_slug=?",
            (slug,),
        )
        rows = await cursor.fetchall()

    await publish_sensor(
        ha,
        f"sensor.vultron_osiagniecia_{slug}",
        len(rows),
        f"Osiągnięcia: {name}",
        {"osiagniecia": [{"id": r[0], "tresc": r[1]} for r in rows]},
    )


async def _fetch_lucky_number(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: dict,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram szczęśliwy numerek...", name)

    now_str = datetime.now().strftime("%Y-%m-%d")
    api_numer: str | None = None
    api_id: str | None = None

    try:
        res = await client.get(
            f"{base}/api/SzczesliwyNumerTablica", params={"key": key}
        )
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, dict):
                api_numer = str(data.get("numer", "Brak"))
                api_id = str(data.get("id", ""))
        else:
            logger.warning(
                "[%s] błąd szczęśliwego numerka API: %d",
                name, res.status_code,
            )
    except Exception as e:
        logger.error(
            "[%s] błąd pobierania szczęśliwego numerka: %s", name, e
        )

    db_numer = "Brak"
    db_id = ""

    try:
        async with AsyncDB() as conn:
            if api_numer is not None:
                await conn.execute(
                    "INSERT OR REPLACE INTO lucky_number VALUES (?,?,?,?)",
                    (slug, now_str, api_numer, api_id),
                )

            cursor = await conn.execute(
                "SELECT numer, numer_id FROM lucky_number"
                " WHERE student_slug=? AND data=?",
                (slug, now_str),
            )
            row = await cursor.fetchone()
            if row:
                db_numer, db_id = row
    except Exception as e:
        logger.error(
            "Błąd bazy danych dla szczęśliwego numerka [%s]: %s", name, e
        )

    logger.debug(
        "[%s] Publikuję sensor szczęśliwego numerka: %s", name, db_numer
    )
    state_val = 1 if db_numer != "Brak" else 0
    await publish_sensor(
        ha,
        f"sensor.vultron_szczesliwy_numerek_{slug}",
        state_val,
        f"Szczęśliwy Numerek: {name}",
        {"numer": db_numer, "id_numerku": db_id, "icon": "mdi:clover"},
    )

# ---------------------------------------------------------------------------
# Synchronizacja dziennika
# ---------------------------------------------------------------------------


async def sync_diary_data(students: list, cookies: list) -> None:
    httpx_cookies = {c["name"]: c["value"] for c in cookies}
    _retry_transport = httpx.AsyncHTTPTransport(retries=3)

    async with (
        httpx.AsyncClient(
            cookies=httpx_cookies,
            timeout=20,
            transport=_retry_transport,
            event_hooks=_ASYNC_TRACE_HOOKS,
        ) as client,
        httpx.AsyncClient(
            headers=HA_HEADERS,
            timeout=15,
            transport=_retry_transport,
            event_hooks=_ASYNC_TRACE_HOOKS,
        ) as ha,
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
                    logger.error(
                        "Sekcja %d błąd dla %s: %s",
                        i, s["uczen"], r, exc_info=r,
                    )
            logger.info("=== Zakończono: %s ===", s["uczen"])

# ---------------------------------------------------------------------------
# Wiadomości (Playwright — sync, uruchamiana w wątku)
# ---------------------------------------------------------------------------


def run_messages_sync(city: str, students_list: list) -> None:
    page, context, browser, pw = _get_browser_context(headless=True)

    try:
        logger.info("[MESS] Logowanie…")
        if os.path.exists(BUL_PKL):
            try:
                with open(BUL_PKL, "r", encoding="utf-8") as f:
                    cookies_list = json.load(f)
                for c in cookies_list:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        context.add_cookies([c])
                page.goto("https://eduvulcan.pl/logowanie", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                logger.debug("Uszkodzony plik ciasteczek, usuwam: %s", e)
                try:
                    os.remove(BUL_PKL)
                except Exception:
                    pass

        _vulcan_login(page, log_prefix="[MESS]")
        app_url = f"https://wiadomosci.eduvulcan.pl/{city}/App"
        page.goto(app_url, timeout=45000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if "logowanie" in page.url.lower():
            try:
                frame = page.frame_locator("iframe")
                frame.locator("#save-default-button").click()
            except Exception:
                pass

            page.wait_for_selector("#Alias", timeout=30000)
            page.fill("#Alias", CONFIG.get("username", ""))
            page.fill("#Password", CONFIG.get("password", ""))
            page.press("#Password", "Enter")
            page.goto(app_url, timeout=45000)
            page.wait_for_load_state("networkidle", timeout=20000)

        with httpx.Client(timeout=15) as session:
            for cookie in context.cookies():
                session.cookies.set(cookie["name"], cookie["value"])

            session.headers.update({
                "User-Agent": page.evaluate("() => navigator.userAgent"),
                "Referer": app_url,
                "X-Requested-With": "XMLHttpRequest",
            })

            logger.info("--> Pobieram wiadomości ze skrzynek odbiorczych...")

            with closing(db_connect()) as conn:
                with conn:
                    cur = conn.cursor()
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
                                logger.warning(
                                    "[MESS] Wykryto przepełnienie ciasteczek (błąd 400). "
                                    "Usuwam bul.pkl..."
                                )
                                if os.path.exists(BUL_PKL):
                                    os.remove(BUL_PKL)
                            continue

                        for m in res_m.json():
                            m_k = m.get("apiGlobalKey")
                            if not m_k:
                                continue
                            det = session.get(
                                f"https://wiadomosci.eduvulcan.pl/{city}"
                                f"/api/WiadomoscSzczegoly?apiGlobalKey={m_k}"
                            )
                            if det.status_code == 200:
                                cur.execute(
                                    "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                                    (
                                        m_k,
                                        assigned,
                                        m.get("data", ""),
                                        m.get("korespondenci", ""),
                                        m.get("temat", ""),
                                        det.json().get("tresc", "Brak"),
                                        1 if m.get("przeczytana") else 0,
                                    ),
                                )

                cur = conn.cursor()
                for st in students_list:
                    slug = st["slug"]
                    cur.execute(
                        "SELECT data,nadawca,temat,tresc,przeczytana"
                        " FROM messages"
                        " WHERE student_slug=?"
                        " ORDER BY data DESC LIMIT 10",
                        (slug,),
                    )
                    rows = cur.fetchall()
                    unread: int = cur.execute(
                        "SELECT COUNT(*) FROM messages"
                        " WHERE student_slug=?"
                        " AND przeczytana=0",
                        (slug,),
                    ).fetchone()[0]
                    total: int = cur.execute(
                        "SELECT COUNT(*) FROM messages"
                        " WHERE student_slug=?",
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
                        msgs.append({
                            "data": r[0].replace("T", " ")[:16],
                            "nadawca": r[1],
                            "temat": r[2],
                            "tresc": body,
                            "przeczytana": not is_u,
                        })
                    publish_sensor_sync(
                        f"sensor.vultron_wiadomosci_{slug}",
                        unread,
                        f"Wiadomości: {st['uczen']}",
                        {"wiadomosci": msgs, "stats": f"{unread} / {total}"},
                    )

            with open(BUL_PKL, "w", encoding="utf-8") as f:
                json.dump(context.cookies(), f, ensure_ascii=False)
            logger.info("[MESS] Gotowe.")

    except Exception as e:
        logger.error("[MESS] Błąd krytyczny: %s", e, exc_info=True)
        raise

    finally:
        try:
            page.close()
            context.clear_cookies()
            context.close()
            browser.close()
            pw.stop()
        except Exception as e:
            logger.debug("Błąd zamykania Playwright: %s", e)

# ---------------------------------------------------------------------------
# Monitor rozmiaru encji
# ---------------------------------------------------------------------------

_MONITOR_TEMPLATE = (
    "[{% for s in states.sensor"
    " if s.entity_id.startswith('sensor.vultron_')"
    " and s.entity_id != 'sensor.vultron_system_monitor' %}"
    "{\"id\":\"{{ s.entity_id }}\","
    "\"size\":{{ s.attributes|tojson|length }}}"
    "{{ \",\" if not loop.last }}{% endfor %}]"
)


async def _run_size_monitor(ha: httpx.AsyncClient) -> None:
    try:
        res = await ha.post(
            f"{HA_URL}/template",
            json={"template": _MONITOR_TEMPLATE},
            timeout=15,
        )
        if res.status_code != 200:
            logger.warning("Monitor template błąd: %d", res.status_code)
            return
        ents = res.json()
        if not isinstance(ents, list):
            logger.warning(
                "Monitor template: nieoczekiwany format odpowiedzi"
            )
            return
        tot = sum(e["size"] for e in ents)
        await asyncio.gather(
            publish_sensor(
                ha,
                "sensor.vultron_system_monitor",
                tot,
                "Vultron System Monitor",
                {
                    "unit_of_measurement": "B",
                    "szczegoly": " | ".join(
                        f"{e['id']}: {e['size']}B" for e in ents
                    ),
                },
            ),
            publish_sensor(
                ha,
                "binary_sensor.vultron_rozmiar_alert",
                "on" if any(e["size"] > 15_500 for e in ents) else "off",
                "Vultron Rozmiar Alert",
                {"device_class": "problem"},
            ),
            return_exceptions=True,
        )
    except Exception as e:
        logger.error("Monitor rozmiaru: %s", e)

# ---------------------------------------------------------------------------
# Główna pętla
# ---------------------------------------------------------------------------


async def main_loop() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    copy_resources()
    await wait_for_ha_api()
    run_setup_ui()

    await init_global_db()

    _retry_transport = httpx.AsyncHTTPTransport(retries=3)
    async with httpx.AsyncClient(
        headers=HA_HEADERS,
        timeout=15,
        transport=_retry_transport,
        event_hooks=_ASYNC_TRACE_HOOKS,
    ) as ha:
        await restore_entities_from_cache(ha)

        async def _watchdog() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                if not stop_event.is_set():
                    await check_and_restore(ha)

        watchdog_task = asyncio.create_task(_watchdog())

        while not stop_event.is_set():
            now = datetime.now()
            wd = now.weekday()
            wake_at = None

            if not _test_mode:
                if wd < 5 and 1 <= now.hour <= 5:
                    wake_at = now.replace(
                        hour=6, minute=0, second=0, microsecond=0
                    )
                    logger.info("Przerwa nocna (Pon-Pt) – wznowienie o 06:00")

                elif wd == 5 and now.hour not in (8, 16, 23):
                    next_h = next(
                        (h for h in (8, 16, 23) if h > now.hour), None
                    )
                    if next_h:
                        wake_at = now.replace(
                            hour=next_h, minute=0, second=0, microsecond=0
                        )
                    else:
                        wake_at = (now + timedelta(days=1)).replace(
                            hour=8, minute=0, second=0, microsecond=0
                        )
                    logger.info(
                        "Harmonogram weekendowy (Sobota) – czekam do %s",
                        wake_at.strftime("%H:%M"),
                    )

                elif wd == 6 and now.hour not in (8, 12, 20):
                    next_h = next(
                        (h for h in (8, 12, 20) if h > now.hour), None
                    )
                    if next_h:
                        wake_at = now.replace(
                            hour=next_h, minute=0, second=0, microsecond=0
                        )
                    else:
                        wake_at = (now + timedelta(days=1)).replace(
                            hour=6, minute=0, second=0, microsecond=0
                        )
                    logger.info(
                        "Harmonogram weekendowy (Niedziela) – czekam do %s",
                        wake_at.strftime("%H:%M"),
                    )
            else:
                logger.info(
                    "[TEST MODE] Filtr czasowy (noce/weekendy) pominięty."
                )

            if wake_at:
                secs = int(max(60, (wake_at - now).total_seconds()))
                logger.info(
                    "Czekam %d minut przed uruchomieniem pobierania.",
                    secs // 60,
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=float(secs))
                except asyncio.TimeoutError:
                    pass
                continue

            # ---------------------------------------------------------------
            # Główny cykl pobierania
            # ---------------------------------------------------------------
            logger.info("=== CYKL START ===")
            await check_and_restore(ha)

            try:
                students, cookies = await asyncio.wait_for(
                    asyncio.to_thread(run_diary_auth),
                    timeout=120.0
                )
            except asyncio.TimeoutError:
                await fatal_error_stop(ha, "Timeout (120s) - proces Playwright zawiesił się podczas głównego logowania")
                break
            except Exception as e:
                logger.critical(
                    "KRYTYCZNY BŁĄD PODCZAS GŁÓWNEGO LOGOWANIA: %s. "
                    "Zatrzymuję dodatek!", e
                )
                await fatal_error_stop(ha, str(e))
                break

            if students and cookies:
                await sync_diary_data(students, cookies)

                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(run_messages_sync, students[0]["city"], students),
                        timeout=120.0
                    )
                except asyncio.TimeoutError:
                    await fatal_error_stop(ha, "Timeout (120s) - proces Playwright zawiesił się podczas logowania do wiadomości")
                    break
                except Exception as e:
                    logger.critical(
                        "KRYTYCZNY BŁĄD PODCZAS LOGOWANIA DO WIADOMOŚCI: %s. "
                        "Zatrzymuję dodatek!", e
                    )
                    await fatal_error_stop(ha, str(e))
                    break

            await _run_size_monitor(ha)

            # ---------------------------------------------------------------
            # Obliczanie czasu oczekiwania do następnego cyklu
            # ---------------------------------------------------------------
            now_after = datetime.now()
            wd_after = now_after.weekday()
            wait_time: int

            if not _test_mode:
                if wd_after == 5:
                    next_h = next(
                        (h for h in (8, 16, 23) if h > now_after.hour), None
                    )
                    wake_at = (
                        now_after.replace(
                            hour=next_h, minute=0, second=0, microsecond=0
                        )
                        if next_h
                        else (now_after + timedelta(days=1)).replace(
                            hour=8, minute=0, second=0, microsecond=0
                        )
                    )
                    wait_time = int(
                        max(60, (wake_at - now_after).total_seconds())
                    )
                    logger.info(
                        "Cykl OK (Sobota) → następne pobieranie"
                        " o %s (za ~%d min)",
                        wake_at.strftime("%H:%M"), wait_time // 60,
                    )

                elif wd_after == 6:
                    next_h = next(
                        (h for h in (8, 12, 20) if h > now_after.hour), None
                    )
                    wake_at = (
                        now_after.replace(
                            hour=next_h, minute=0, second=0, microsecond=0
                        )
                        if next_h
                        else (now_after + timedelta(days=1)).replace(
                            hour=6, minute=0, second=0, microsecond=0
                        )
                    )
                    wait_time = int(
                        max(60, (wake_at - now_after).total_seconds())
                    )
                    logger.info(
                        "Cykl OK (Niedziela) → następne pobieranie"
                        " o %s (za ~%d min)",
                        wake_at.strftime("%H:%M"), wait_time // 60,
                    )

                else:
                    wait_time = secrets.randbelow(1301) + 2400
                    next_run = now_after + timedelta(seconds=wait_time)
                    logger.info(
                        "Cykl OK → następny za ~%d min (o %s)",
                        wait_time // 60, next_run.strftime("%H:%M"),
                    )
            else:
                wait_time = secrets.randbelow(1301) + 2400
                next_run = now_after + timedelta(seconds=wait_time)
                logger.info(
                    "[TEST MODE] Cykl OK → następny za ~%d min (o %s)",
                    wait_time // 60, next_run.strftime("%H:%M"),
                )

            # ---------------------------------------------------------------
            # Aktywne oczekiwanie (nasłuch na restart HA)
            # ---------------------------------------------------------------
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(wait_time))
            except asyncio.TimeoutError:
                pass

        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

    await close_global_db()
    logger.info("Vultron zatrzymany (graceful shutdown).")


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamykanie…")
        sys.exit(0)
