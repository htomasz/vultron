from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import pickle
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta
import httpx
import requests as _req_sync          # tylko do Selenium-sekcji (sync wątki)
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from websocket import create_connection

# ────────────────────────────────────────────────
# KONFIGURACJA
# ────────────────────────────────────────────────

os.environ["SE_STATS"] = "0"

DB_PATH      = "/data/vultron.db"
VUL_PKL      = "/data/vul.pkl"
BUL_PKL      = "/data/bul.pkl"
OPTIONS_PATH = "/data/options.json"
HA_TOKEN     = os.getenv("SUPERVISOR_TOKEN", "")
HA_URL       = "http://supervisor/core/api"
HA_HEADERS   = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

if not os.path.exists(OPTIONS_PATH):
    print("BŁĄD KRYTYCZNY: Brak pliku options.json")
    sys.exit(1)

if not HA_TOKEN:
    print("BŁĄD KRYTYCZNY: SUPERVISOR_TOKEN nie jest ustawiony")
    sys.exit(1)

with open(OPTIONS_PATH, encoding="utf-8") as _f:
    CONFIG: dict = json.load(_f)

# ────────────────────────────────────────────────
# LOGOWANIE
# ────────────────────────────────────────────────

logger = logging.getLogger("Vultron")
logger.setLevel(logging.DEBUG if CONFIG.get("debug") else logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_ch  = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_fh  = logging.handlers.RotatingFileHandler("/data/vultron.log", maxBytes=1_048_576, backupCount=5)
_fh.setFormatter(_fmt)
logger.addHandler(_ch)
logger.addHandler(_fh)

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

# Deduplicator wysyłek do HA (klucz: entity_id → MD5 ostatnio wysłanego payloadu)
# Ograniczony do 500 wpisów – przy przekroczeniu czyszczony w całości (entity_id są stabilne,
# więc po wyczyszczeniu kolejny cykl i tak wyśle wszystko ponownie i odbuduje cache).
_sent_hashes: dict[str, str] = {}
_SENT_HASHES_MAX = 500

# Tabela transliteracji polskich znaków – tworzona raz na poziomie modułu
_PL_TRANS = str.maketrans("ąćęłńóśźż", "acelnoszz")

# ────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────

def slugify(text: str) -> str:
    if not text:
        return "unknown"
    return re.sub(r"[^a-z0-9]", "_", text.lower().translate(_PL_TRANS)).strip("_")


def clean_html(raw: str) -> str:
    return re.sub(r"<.*?>", "", raw or "").replace("&nbsp;", " ").strip() or "Brak opisu"


def clean_text(text: str, max_len: int = 200) -> str:
    t = str(text).replace("\n", " ").replace("\r", "") if text else ""
    return t[: max_len - 3] + "..." if len(t) > max_len else t


def _payload_hash(state, attrs_no_timestamp: dict) -> str:
    raw = json.dumps({"state": state, "attributes": attrs_no_timestamp},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


# ────────────────────────────────────────────────
# HA SENSOR – async publish
# ────────────────────────────────────────────────

async def publish_sensor(
    client: httpx.AsyncClient,
    entity_id: str,
    state,
    friendly_name: str,
    extra_attrs: dict | None = None,
) -> None:
    """Wysyła sensor do HA. Pomija jeśli dane niezmienione (MD5 dedup)."""
    attrs = {
        "friendly_name": friendly_name,
        "last_update":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }
    # Hash bez last_update – żeby identyczne dane nie były ponawiane co cykl
    h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})
    if _sent_hashes.get(entity_id) == h:
        return
    if len(_sent_hashes) >= _SENT_HASHES_MAX:
        _sent_hashes.clear()

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
        _sent_hashes[entity_id] = h
        logger.debug("Sensor %s → %s", entity_id, state)
    except httpx.TimeoutException:
        logger.warning("Timeout: %s", entity_id)
    except httpx.ConnectError:
        logger.warning("Brak połączenia HA: %s", entity_id)
    except Exception as exc:
        logger.exception("Błąd wysyłki %s: %s", entity_id, exc)


# ────────────────────────────────────────────────
# HA SENSOR – sync publish (tylko dla wątków Selenium)
# ────────────────────────────────────────────────

def publish_sensor_sync(entity_id: str, state, friendly_name: str, extra_attrs: dict | None = None) -> None:
    """Synchroniczny odpowiednik publish_sensor – używany wyłącznie w wątkach Selenium.
    Zawiera ten sam mechanizm dedup (MD5 hash) co wersja async.
    """
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
    try:
        res = _req_sync.post(
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

_DB_DDL = [
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
]


def db_connect() -> sqlite3.Connection:
    """Otwiera połączenie z WAL mode (lepszy concurrent access)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    """Rejestruje karty Lovelace przez WebSocket (sync – biblioteka websocket)."""
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
        raw = json.loads(ws.recv()).get("result", [])
        existing = {re.sub(r"\?v=.*", "", r["url"]): (r["id"], r["url"]) for r in raw}

        src_dir = "/app" if os.path.exists("/app") else "."
        cards   = [f for f in os.listdir(src_dir) if f.startswith("vultron-") and f.endswith(".js")]

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
    """Loguje do eduvulcan.pl, zwraca (students, raw_cookies). Musi być sync."""
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = _get_driver()
    wait   = WebDriverWait(driver, 25)

    try:
        logger.info("[AUTH] Logowanie…")
        driver.get("https://eduvulcan.pl/logowanie")
        wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
            CONFIG.get("username", "") + Keys.ENTER
        )
        time.sleep(1.5)
        wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
            CONFIG.get("password", "") + Keys.ENTER
        )
        time.sleep(3)

        link = driver.find_element(By.XPATH, "//a[contains(@href,'dziennik')]").get_attribute("href")
        driver.get(link)
        time.sleep(5)

        m = re.search(r"uczen\.eduvulcan\.pl/([^/]+)", driver.current_url)
        if not m:
            logger.error("[AUTH] Brak nazwy miasta w URL: %s", driver.current_url)
            return None, None
        city = m.group(1)

        driver.get(f"https://uczen.eduvulcan.pl/{city}/api/Context")
        time.sleep(2)
        context = json.loads(driver.execute_script("return document.body.innerText"))

        session = _req_sync.Session()
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])

        students: list[dict] = []
        for u in context.get("uczniowie", []):
            key   = u.get("key")
            id_dz = str(u.get("idDziennik"))
            res   = session.get(
                f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne",
                params={"key": key, "idDziennik": id_dz}, timeout=10
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
            })

        cookies = driver.get_cookies()
        with open(VUL_PKL, "wb") as f:
            pickle.dump({"cookies": cookies, "students": students}, f)

        logger.info("[AUTH] OK – %d uczniów", len(students))
        return students, cookies

    except Exception as e:
        logger.error("[AUTH] Błąd: %s", e, exc_info=True)
        return None, None
    finally:
        driver.quit()
        display.stop()


# ────────────────────────────────────────────────
# FETCH HELPERS – async sekcje danych
# Wspólna sygnatura: (client, ha, base_url, s, conn)
# ────────────────────────────────────────────────

async def _fetch_grades(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                        base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, id_dz, name = s["slug"], s["key"], s["idDziennik"], s["uczen"]
    logger.info("[%s] oceny…", name)

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
        cur   = conn.cursor()

        for p_item in res_g.json().get("ocenyPrzedmioty", []):
            subj = p_item.get("przedmiotNazwa", "Inne")
            for kol in p_item.get("kolumnyOcenyCzastkowe", []):
                id_k = str(kol.get("idKolumny", "0"))
                desc = f"{kol.get('kategoriaKolumny','')}: {kol.get('nazwaKolumny','')}".strip(": ")
                for o in kol.get("oceny", []):
                    v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
                    # INSERT OR REPLACE: aktualizuje zmienione oceny (np. korekta przez nauczyciela)
                    # rowcount > 0 oznacza nowy lub zmieniony wpis → zalicza się do new_g
                    cur.execute(
                        "INSERT OR REPLACE INTO grades VALUES (?,?,?,?,?,?,?)",
                        (id_k, slug, subj, v, dt, desc, p_id),
                    )
                    if cur.rowcount > 0:
                        new_g += 1
                    subjects.setdefault(subj, []).append({"w": v, "d": dt[:5], "i": clean_text(desc)})
        conn.commit()

        lista = []
        for subj_name, grades in subjects.items():
            vals: list[float] = []
            for g in grades:
                m = re.search(r"\d+(?:[.,]\d+)?", g["w"])
                if m:
                    v = float(m.group().replace(",", "."))
                    if 1.0 <= v <= 6.0:
                        vals.append(v)
            lista.append({"przedmiot": subj_name, "oceny": grades,
                          "srednia": round(sum(vals)/len(vals), 2) if vals else None})

        await publish_sensor(ha, f"sensor.vultron_oceny_{slug}_p{p_num}", new_g,
                             f"Oceny: {name} (P{p_num})",
                             {"lista_przedmiotow": lista, "period_number": int(p_num),
                              "student_slug": slug,
                              "active_period": p_id == str(s["periodId"])})


async def _fetch_schedule(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                          base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("[%s] plan…", name)
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

    cur = conn.cursor()
    for lesson in res.json():
        st  = MAPA_STATUSOW.get(int(lesson.get("adnotacja", 0)), "")
        inf = " ".join((c.get("informacjeNieobecnosc") or "").lower() for c in lesson.get("zmiany", []))
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
    tasks = []
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
    await asyncio.gather(*tasks)


async def _fetch_timetable(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                           base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("[%s] terminarz…", name)
    now = datetime.now()

    res = await client.get(f"{base}/api/SprawdzianyZadaniaDomowe", params={
        "key": key,
        "dataOd": now.strftime("%Y-%m-%dT00:00:00.000Z"),
        "dataDo": (now + timedelta(days=61)).strftime("%Y-%m-%dT23:59:59.999Z"),
    })
    if res.status_code != 200:
        logger.warning("[%s] błąd terminarza: %d", name, res.status_code)
        return

    async def _detail(item: dict) -> None:
        item_id = item.get("id")
        if not item_id:
            return
        ep = "ZadanieDomoweSzczegoly" if item.get("typ") == 4 else "SprawdzianSzczegoly"
        try:
            dr = await client.get(f"{base}/api/{ep}", params={"key": key, "id": item_id})
        except httpx.RequestError as exc:
            logger.warning("[%s] błąd szczegółów terminarza %s: %s", name, item.get("id"), exc)
            return
        if dr.status_code != 200:
            return
        dj  = dr.json()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
            (str(item_id), slug, dj.get("data",""),
             dj.get("przedmiotNazwa",""),
             MAPA_TYP_TERMINARZA.get(item.get("typ"), "Inne"),
             clean_html(dj.get("opis") or dj.get("temat")),
             dj.get("nauczycielImieNazwisko","")),
        )

    items = res.json()
    await asyncio.gather(*[_detail(i) for i in items])
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        "SELECT data,przedmiot,typ,opis,autor FROM timetable "
        "WHERE student_slug=? AND data>=? ORDER BY data",
        (slug, now.strftime("%Y-%m-%d")),
    )
    rows = cur.fetchall()
    await publish_sensor(ha, f"sensor.vultron_terminarz_{slug}", len(rows),
                         f"Terminarz: {name}",
                         {"lista": [{"data": r[0].split("T")[0], "przedmiot": r[1],
                                     "typ": r[2], "opis": r[3], "autor": r[4]} for r in rows]})


async def _fetch_remarks(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                         base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("[%s] uwagi…", name)

    res = await client.get(f"{base}/api/Uwagi", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd uwag: %d", name, res.status_code)
        return

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
    await publish_sensor(ha, f"sensor.vultron_uwagi_{slug}", len(lista),
                         f"Uwagi: {name}", {"uwagi": lista})


async def _fetch_frequency(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                           base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("[%s] frekwencja…", name)
    now = datetime.now()

    res_f, res_fs = await asyncio.gather(
        client.get(f"{base}/api/Frekwencja", params={
            "key": key,
            "dataOd": (now - timedelta(14)).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": now.strftime("%Y-%m-%dT23:59:59.999Z"),
        }),
        client.get(f"{base}/api/FrekwencjaStatystyki", params={"key": key, "idPrzedmiot": -1}),
    )

    cur = conn.cursor()
    if res_f.status_code == 200:
        recs = res_f.json()
        if isinstance(recs, dict):
            recs = recs.get("oddzialy", [])
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
        await publish_sensor(ha, f"sensor.vultron_freq_{slug}", 0, f"Frekwencja: {name}",
                             {"wpisy": [{"d": r[0], "t": r[1], "k": int(r[2])} for r in cur.fetchall()]})
    else:
        logger.warning("[%s] błąd frekwencji: %d", name, res_f.status_code)

    if res_fs.status_code == 200:
        fsd = res_fs.json()
        proc = [
            {"k": MAPA_FREKWENCJI.get(row.get("kategoriaFrekwencji"), "Inna"),
             "m": {str(m["miesiac"]): m["wartosc"] for m in row.get("miesiace", [])},
             "s1": row.get("okresy", [0,0])[0], "s2": row.get("okresy", [0,0])[1],
             "r": row.get("razem", 0)}
            for row in fsd.get("statystyki", [])
        ]
        await publish_sensor(ha, f"sensor.vultron_stats_{slug}",
                             fsd.get("podsumowanie", 0), f"Statystyki: {name}",
                             {"unit_of_measurement": "%", "rows": proc})
    else:
        logger.warning("[%s] błąd statystyk: %d", name, res_fs.status_code)


async def _fetch_achievements(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                              base: str, s: dict, conn: sqlite3.Connection) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("[%s] osiągnięcia…", name)

    res = await client.get(f"{base}/api/Osiagniecia", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd osiągnięć: %d", name, res.status_code)
        return

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
    await publish_sensor(ha, f"sensor.vultron_osiagniecia_{slug}", len(rows),
                         f"Osiągnięcia: {name}",
                         {"osiagniecia": [{"id": r[0], "tresc": r[1]} for r in rows]})

# ────────────────────────────────────────────────
# SYNCHRONIZACJA DZIENNIKA – pełna async
# ────────────────────────────────────────────────

async def sync_diary_data(students: list, cookies: list) -> None:
    """
    Dla każdego ucznia uruchamia 6 sekcji równolegle (asyncio.gather).
    Jeden wspólny AsyncClient na całą synchronizację.
    SQLite: jedno połączenie współdzielone między coroutines (WAL mode).
    """
    httpx_cookies = {c["name"]: c["value"] for c in cookies}

    conn = db_connect()
    db_init(conn)

    try:
        async with (
            httpx.AsyncClient(cookies=httpx_cookies, timeout=20) as client,
            httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha,
        ):
            for s in students:
                logger.info("=== Sync: %s ===", s["uczen"])
                base = f"https://uczen.eduvulcan.pl/{s['city']}"
                results = await asyncio.gather(
                    _fetch_grades(client, ha, base, s, conn),
                    _fetch_schedule(client, ha, base, s, conn),
                    _fetch_timetable(client, ha, base, s, conn),
                    _fetch_remarks(client, ha, base, s, conn),
                    _fetch_frequency(client, ha, base, s, conn),
                    _fetch_achievements(client, ha, base, s, conn),
                    return_exceptions=True,
                )
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        logger.error("Sekcja %d błąd dla %s: %s", i, s["uczen"], r, exc_info=r)
                logger.info("=== Koniec sync: %s ===", s["uczen"])
    finally:
        conn.close()


# ────────────────────────────────────────────────
# WIADOMOŚCI (Selenium – sync, uruchamiana w wątku)
# ────────────────────────────────────────────────

def run_messages_sync(city: str, students_list: list) -> None:
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = _get_driver()
    wait   = WebDriverWait(driver, 25)

    conn = None  # zdefiniuj PRZED try: żeby finally zawsze miało dostęp
    try:
        logger.info("[MESS] Logowanie…")
        driver.get("https://eduvulcan.pl/logowanie")

        # Próba reużycia ciasteczek
        if os.path.exists(BUL_PKL):
            try:
                with open(BUL_PKL, "rb") as f:
                    for c in pickle.load(f):
                        driver.add_cookie(c)
                driver.get("https://eduvulcan.pl/logowanie")
                time.sleep(2)
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

        session = _req_sync.Session()
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])
        session.headers.update({
            "User-Agent":        driver.execute_script("return navigator.userAgent"),
            "Referer":           app_url,
            "X-Requested-With":  "XMLHttpRequest",
        })

        res_m = session.get(f"https://wiadomosci.eduvulcan.pl/{city}/api/Odebrane?idLastWiadomosc=0&pageSize=15", timeout=10)
        if res_m.status_code != 200:
            logger.warning("[MESS] błąd pobierania: %d", res_m.status_code)
            return

        conn = db_connect()
        db_init(conn)
        cur  = conn.cursor()

        try:
            for m in res_m.json():
                m_k = m.get("apiGlobalKey")
                if not m_k:
                    continue
                box      = m.get("skrzynka", "").lower()
                assigned = next((st["slug"] for st in students_list
                                 if st["uczen"].lower() in box), "unknown")
                det = session.get(
                    f"https://wiadomosci.eduvulcan.pl/{city}/api/WiadomoscSzczegoly"
                    f"?apiGlobalKey={m_k}", timeout=10
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

        # Publikacja sensorów wiadomości
        for st in students_list:
            slug = st["slug"]
            cur.execute(
                "SELECT data,nadawca,temat,tresc,przeczytana FROM messages "
                "WHERE student_slug=? OR student_slug='unknown' ORDER BY data DESC LIMIT 10",
                (slug,),
            )
            rows = cur.fetchall()
            unread = cur.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE (student_slug=? OR student_slug='unknown') AND przeczytana=0",
                (slug,),
            ).fetchone()[0]
            total = cur.execute(
                "SELECT COUNT(*) FROM messages WHERE student_slug=? OR student_slug='unknown'",
                (slug,),
            ).fetchone()[0]

            msgs = []
            for r in rows:
                is_u = int(r[4]) == 0
                body = clean_text(r[3], 2000) if is_u else ""
                msgs.append({"data": r[0].replace("T"," ")[:16], "nadawca": r[1],
                             "temat": r[2], "tresc": body, "przeczytana": not is_u})

            publish_sensor_sync(
                f"sensor.vultron_wiadomosci_{slug}", unread, f"Wiadomości: {st['uczen']}",
                {"wiadomosci": msgs, "stats": f"{unread} / {total}"},
            )

        with open(BUL_PKL, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
        logger.info("[MESS] Gotowe.")

    except Exception as e:
        logger.error("[MESS] Błąd krytyczny: %s", e, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
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
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, loop.stop)

    copy_resources()
    await wait_for_ha_api()
    run_setup_ui()

    # Jeden długożyjący klient HA – reużywany przez cały czas pracy
    async with httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha:
        while True:
            now = datetime.now()
            if 1 <= now.hour <= 5:
                # Śpij dokładnie do 06:00 – nie dłużej niż potrzeba
                wake_at = now.replace(hour=6, minute=0, second=0, microsecond=0)
                secs = max(60, (wake_at - now).total_seconds())
                logger.info("Przerwa nocna – wznowienie o 06:00 (za %.0f min)", secs / 60)
                await asyncio.sleep(secs)
                continue

            logger.info("=== CYKL START ===")

            # Sprawdź czy HA nie zrestartował (sensor zniknął)
            try:
                r = await ha.get(f"{HA_URL}/states/sensor.vultron_system_monitor", timeout=8)
                if r.status_code == 404:
                    logger.warning("Monitor zniknął → restart HA? Czyszczę cache.")
                    _sent_hashes.clear()
            except Exception as e:
                logger.warning("Sprawdzenie monitora: %s", e)

            # Auth (Selenium → wątek)
            students, cookies = await asyncio.to_thread(run_diary_auth)

            if students and cookies:
                await sync_diary_data(students, cookies)
                await asyncio.to_thread(run_messages_sync, students[0]["city"], students)

            await _run_size_monitor(ha)

            wait_time = secrets.SystemRandom().randint(2400, 3600)
            logger.info("Cykl OK → następny za ~%d min", wait_time // 60)

            # Sen z co-minutowym pingiem HA
            for elapsed in range(0, wait_time, 10):
                await asyncio.sleep(10)
                if (elapsed + 10) % 60 == 0:
                    try:
                        r = await ha.get(f"{HA_URL}/states/sensor.vultron_system_monitor", timeout=4)
                        if r.status_code == 404:
                            logger.warning("Monitor zniknął w śnie → czyszczę cache i startuję cykl.")
                            _sent_hashes.clear()
                            break
                    except httpx.RequestError as exc:
                        logger.debug("Chwilowy problem HA: %s", exc)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamykanie…")
        sys.exit(0)
