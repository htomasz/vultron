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
import threading
import time
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import TypedDict, Any
from urllib.parse import urljoin
import aiosqlite
import httpx
from playwright.sync_api import sync_playwright
from websocket import create_connection

# ---------------------------------------------------------------------------
# Zmienne środowiskowe, ścieżki i stałe konfiguracyjne
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

MIN_WAIT_SECONDS = 2400
MAX_JITTER_SECONDS = 1301

# ---------------------------------------------------------------------------
# Definicje typów
# ---------------------------------------------------------------------------

CookieList = list[dict[str, Any]]

class StudentInfo(TypedDict, total=False):
    slug: str
    uczen: str
    city: str
    key: str
    idDziennik: str
    periodId: str | None
    klasa: str
    globalKeySkrzynka: str

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
        self._log(TRACE_LEVEL, message, args, **kws)  # type: ignore[attr-defined]
logging.Logger.trace = trace  # type: ignore[attr-defined]
logger = logging.getLogger("Vultron")

logger.setLevel(logging.INFO)
_fmt = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S",
)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)

_fh = logging.handlers.RotatingFileHandler(
    "/data/vultron.log",
    maxBytes=5_242_880,
    backupCount=5,
)
_fh.setFormatter(_fmt)
logger.addHandler(_ch)
logger.addHandler(_fh)

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
    CONFIG: dict[str, Any] = json.load(_f)
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
    """Maskuje wrażliwe klucze w słowniku przed zapisem do logów."""
    if isinstance(data, dict):
        return {
            k: "***" if k in _SENSITIVE_KEYS else _mask_payload(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_payload(item) for item in data]
    return data
if _log_level_conf == "trace":
    logger.setLevel(TRACE_LEVEL)
    async def _trace_async_request(request: httpx.Request) -> None:
        logger.trace("->[HTTP ASYNC] %s %s", request.method, request.url)  # type: ignore[attr-defined]
        if request.content:
            try:
                payload = json.loads(request.content)
                logger.trace("   Payload: %s", _mask_payload(payload))  # type: ignore[attr-defined]
            except ValueError:
                pass
    async def _trace_async_response(response: httpx.Response) -> None:
        await response.aread()
        logger.trace(  # type: ignore[attr-defined]
            "<- [HTTP ASYNC] %s %s | Kod: %s | Odpowiedź: %s",
            response.request.method,
            response.request.url,
            response.status_code,
            response.text[:1500],
        )
    def _trace_sync_request(request: httpx.Request) -> None:
        logger.trace("-> [HTTP SYNC]  %s %s", request.method, request.url)  # type: ignore[attr-defined]
        if request.content:
            try:
                payload = json.loads(request.content)
                logger.trace("   Payload: %s", _mask_payload(payload))  # type: ignore[attr-defined]
            except ValueError:
                pass
    def _trace_sync_response(response: httpx.Response) -> None:
        logger.trace(  # type: ignore[attr-defined]
            "<- [HTTP SYNC]  %s %s | Kod: %s | Odpowiedź: %s",
            response.request.method,
            response.request.url,
            response.status_code,
            response.text[:1500],
        )
    _ASYNC_TRACE_HOOKS: dict[str, list] = {
        "request": [_trace_async_request],
        "response": [_trace_async_response],
    }
    _SYNC_TRACE_HOOKS: dict[str, list] = {
        "request": [_trace_sync_request],
        "response": [_trace_sync_response],
    }
elif _log_level_conf == "debug":
    logger.setLevel(logging.DEBUG)
    _ASYNC_TRACE_HOOKS = {}
    _SYNC_TRACE_HOOKS = {}
else:
    logger.setLevel(logging.INFO)
    _ASYNC_TRACE_HOOKS = {}
    _SYNC_TRACE_HOOKS = {}
logger.info(
    "Log level ustawiony na: %s (%d)",
    logging.getLevelName(logger.level),
    logger.level,
)

# ---------------------------------------------------------------------------
# Stałe / cache / wyrażenia regularne
# ---------------------------------------------------------------------------

MAPA_STATUSOW: dict[int, str] = {
    0: "", 1: "ZAST", 2: "PRZEN", 3: "ODWOL", 4: "NIEOB",
}
MAPA_FREKWENCJI: dict[int, str] = {
    1: "Obecność", 2: "Nieobecność", 3: "Usprawiedliwiona",
    4: "Spóźnienie", 5: "Spóźnienie uspraw.", 6: "Szkolne", 7: "Zwolnienie",
}
MAPA_TYP_TERMINARZA: dict[int, str] = {
    1: "Sprawdzian", 2: "Kartkówka", 3: "Klasówka", 4: "Zadanie domowe",
}
_sent_hashes: OrderedDict[str, str] = OrderedDict()
_sent_hashes_lock = threading.Lock()

_in_flight: set[str] = set()
_SENT_HASHES_MAX = 2500
_PL_TRANS = str.maketrans("ąćęłńóśźż", "acelnoszz")
_RE_SLUG = re.compile(r"[^a-z0-9]+")
_RE_GRADE_IGNORE = re.compile(r"[A-F%]|NB|NP|BZ")
_RE_GRADE_VALUE = re.compile(r"(?<!\d)([1-6])(?:[.,](\d+))?(?!\d)")
_RE_CITY_URL = re.compile(r"uczen\.eduvulcan\.pl/([^/]+)")
_RE_VERSION = re.compile(r'version:\s*["\']?([^"\' ]+)')
_RE_LOVELACE_URL = re.compile(r"\?v=.*")
_RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')
_RE_SPACES = re.compile(r' {2,}')
db_lock_sync = threading.Lock()

# Licznik kolejnych nieudanych logowań + tablica backoffów (sekundy):
# 0s → 30min → 2h → 12h → 24h
_auth_fail_count: int = 0
_AUTH_BACKOFF: list[int] = [0, 1800, 7200, 43200, 86400]

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

def _reset_sync_ha_client() -> None:
    """Zamyka i usuwa singleton — wywoływane po błędzie połączenia."""
    global _sync_ha_client
    # FIX: reset klienta bez trzymania locka podczas close(),
    # by uniknąć blokady przy wolnym zamykaniu socketu.
    with _sync_ha_client_lock:
        client_to_close = _sync_ha_client
        _sync_ha_client = None
    if client_to_close is not None:
        try:
            client_to_close.close()
        except Exception:
            pass

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
        self._href_stack: list[str] = []
    def is_safe_url(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip().lower()
        if u.startswith(("javascript:", "data:", "vbscript:")):
            return False
        _ALLOWED_DOMAINS = ("eduvulcan.pl", "vulcan.net.pl")
        if not any(d in u for d in _ALLOWED_DOMAINS):
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
                self._href_stack.append(href.strip())
            else:
                self._href_stack.append("")
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
            if self._href_stack:
                href = self._href_stack.pop()
                if href:
                    self.text.append(f" ({href})")
    def handle_data(self, d: str) -> None:
        self.text.append(d)
    def get_data(self) -> str:
        return "".join(self.text)

def slugify(text: str) -> str:
    if not text:
        return "unknown"
    # FIX: dodano fallback z hashem aby uniknąć kolizji slugów dla uczniów
    # których imiona po transliteracji dają identyczny wynik.
    base = _RE_SLUG.sub("_", text.lower().translate(_PL_TRANS)).strip("_")
    if not base:
        fallback = hashlib.sha256(text.encode()).hexdigest()[:8]
        logger.warning("slugify: pusta podstawa dla '%s', używam hash '%s'", text, fallback)
        return fallback
    return base

def clean_html(raw: str) -> str:
    if not raw:
        return "Brak opisu"
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_data().replace("&nbsp;", " ")
    text = _RE_MULTIPLE_NEWLINES.sub('\n\n', text)
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

_ATTR_SIZE_LIMIT = 14_000
_TRIMMABLE_KEYS = ("lista", "lekcje", "wpisy", "uwagi", "wiadomosci", "zebrania", "osiagniecia", "rows", "szczegoly")

def _trim_attrs(attrs: dict, limit: int = _ATTR_SIZE_LIMIT) -> dict:
    """Skraca listy w atrybutach jeśli całość przekracza limit bajtów JSON.
    FIX: zastąpiono algorytm O(n²) podejściem z wyliczeniem docelowego rozmiaru,
    by uniknąć wielokrotnej serializacji przy dużych listach.
    """
    serialized = json.dumps(attrs, ensure_ascii=False)
    if len(serialized.encode()) <= limit:
        return attrs
    attrs = dict(attrs)
    for key in _TRIMMABLE_KEYS:
        if key not in attrs or not isinstance(attrs[key], list):
            continue
        lst = list(attrs[key])
        if not lst:
            continue
        # Szacujemy średni rozmiar elementu i obliczamy docelową długość
        # zamiast iterować po jednym elemencie naraz.
        total_bytes = len(json.dumps(attrs, ensure_ascii=False).encode())
        if total_bytes <= limit:
            return attrs
        per_item_bytes = total_bytes / max(len(lst), 1)
        overhead = total_bytes - len(json.dumps(lst, ensure_ascii=False).encode())
        target_count = max(1, int((limit - overhead) / per_item_bytes))
        if target_count < len(lst):
            attrs[key] = lst[:target_count]
            actual = len(json.dumps(attrs, ensure_ascii=False).encode())
            # Korekta w dół jeśli szacunek był zbyt optymistyczny
            while len(attrs[key]) > 1 and actual > limit:
                attrs[key] = attrs[key][:-1]
                actual = len(json.dumps(attrs, ensure_ascii=False).encode())
            logger.warning(
                "Przycięto atrybut '%s' do %d elementów (limit rozmiaru encji HA).",
                key, len(attrs[key]),
            )
            if actual <= limit:
                return attrs
    logger.warning("Nie udało się zmniejszyć atrybutów do limitu %d B.", limit)
    return attrs

async def robust_get(
    client: httpx.AsyncClient,
    url: str,
    **kwargs: object,
) -> httpx.Response:
    """Wrapper dla client.get z retry i exponential backoff."""
    for attempt in range(1, 4):
        try:
            resp = await client.get(url, **kwargs)
            if resp.status_code == 429:
                wait = attempt * 5 + secrets.randbelow(3)
                logger.warning("HTTP 429 (throttling) %s — czekam %ds (próba %d/3)", url, wait, attempt)
                await asyncio.sleep(wait)
                continue
            if resp.status_code in (500, 502, 503, 504):
                wait = attempt * 8
                logger.warning("HTTP %d %s — czekam %ds (próba %d/3)", resp.status_code, url, wait, attempt)
                await asyncio.sleep(wait)
                continue
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt == 3:
                raise
            wait = attempt * 3 + secrets.randbelow(3)
            logger.warning("Błąd sieci %s: %s — czekam %ds (próba %d/3)", url, e, wait, attempt)
            await asyncio.sleep(wait)
    raise httpx.RequestError(f"Max retries exceeded: {url}")

# ---------------------------------------------------------------------------
# Zatrzymywanie awaryjne dodatku (Graceful Stop dla Supervisora)
# ---------------------------------------------------------------------------

async def fatal_error_stop(ha_client: httpx.AsyncClient, reason: str) -> None:
    """Zatrzymuje kontener za pośrednictwem API Supervisora."""
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
# SQLite — schemat i operacje bazy (Pojedynczy Context Manager na Task)
# ---------------------------------------------------------------------------

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
    """CREATE TABLE IF NOT EXISTS meetings (
        id TEXT, student_slug TEXT, data TEXT, godzina TEXT,
        sala TEXT, opis TEXT, online TEXT,
        PRIMARY KEY(id, student_slug))""",
    "CREATE INDEX IF NOT EXISTS idx_timetable_slug_data ON timetable(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_slug_data ON schedule(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_frequency_slug_data ON frequency(student_slug, data)",
    "CREATE INDEX IF NOT EXISTS idx_grades_slug ON grades(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_remarks_slug ON remarks(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_messages_slug ON messages(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_freq_stats_slug ON frequency_stats(student_slug)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_slug ON meetings(student_slug)",
]

async def init_global_db(path: str = DB_PATH) -> None:
    """Tworzy strukturę tabel przy starcie usługi."""
    async with aiosqlite.connect(path, timeout=30.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        for stmt in _DB_DDL:
            await db.execute(stmt)
        await db.commit()

class AsyncDB:
    """Bezpieczny Context Manager dla operacji SQLite zapobiegający wyciekom transakcji."""
    async def __aenter__(self) -> aiosqlite.Connection:
        self.conn = await aiosqlite.connect(DB_PATH, timeout=30.0)
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        return self.conn
    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if exc_type is None:
            try:
                await self.conn.commit()
            except Exception as e:
                logger.error("Błąd podczas commity bazy danych (async): %s", e)
        else:
            try:
                await self.conn.rollback()
            except Exception as e:
                logger.error("Błąd podczas rollback bazy danych: %s", e)
        await self.conn.close()

def db_connect() -> sqlite3.Connection:
    """Połączenie synchroniczne dla wątków (np. dla messages)"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn

async def _save_to_cache(entity_id: str, state: object, attrs: dict) -> None:
    try:
        async with AsyncDB() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO ha_cache (entity_id, state, attributes_json) VALUES (?, ?, ?)",
                (entity_id, str(state), json.dumps(attrs, ensure_ascii=False)),
            )
    except Exception as e:
        logger.error("Błąd zapisu cache %s: %s", entity_id, e)

def _save_to_cache_sync(entity_id: str, state: object, attrs: dict) -> None:
    with db_lock_sync:
        try:
            with closing(db_connect()) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ha_cache (entity_id, state, attributes_json) VALUES (?, ?, ?)",
                    (entity_id, str(state), json.dumps(attrs, ensure_ascii=False)),
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
        if _sent_hashes.get(entity_id) == h or entity_id in _in_flight:
            return
        _in_flight.add(entity_id)
    attrs = _trim_attrs(attrs)
    try:
        res: httpx.Response | None = None
        for attempt in range(1, 4):
            try:
                res = await client.post(
                    f"{HA_URL}/states/{entity_id}",
                    headers=HA_HEADERS,
                    json={"state": state, "attributes": attrs},
                    timeout=12,
                )
                if res.status_code in (401, 403):
                    logger.error(
                        "HTTP %d @ %s — brak autoryzacji HA. Sprawdź SUPERVISOR_TOKEN.",
                        res.status_code, entity_id,
                    )
                    return
                if res.status_code == 429:
                    wait = attempt * 5 + secrets.randbelow(3)
                    logger.warning("HA 429 @ %s — czekam %ds (próba %d/3)", entity_id, wait, attempt)
                    await asyncio.sleep(wait)
                    continue
                if res.status_code in (500, 502, 503, 504):
                    wait = attempt * 8
                    logger.warning("HA %d @ %s — czekam %ds (próba %d/3)", res.status_code, entity_id, wait, attempt)
                    await asyncio.sleep(wait)
                    continue
                break
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == 3:
                    raise
                wait = attempt * 3 + secrets.randbelow(3)
                logger.warning("Błąd sieci @ %s: %s — czekam %ds (próba %d/3)", entity_id, e, wait, attempt)
                await asyncio.sleep(wait)
        if res is None or res.status_code not in (200, 201):
            logger.error(
                "HTTP %d @ %s → %s | %s",
                res.status_code if res else 0, entity_id, state,
                res.text[:200] if res else "brak odpowiedzi",
            )
            return
        with _sent_hashes_lock:
            _sent_hashes[entity_id] = h
            _sent_hashes.move_to_end(entity_id)
            if len(_sent_hashes) > _SENT_HASHES_MAX:
                _sent_hashes.popitem(last=False)
        await _save_to_cache(entity_id, state, attrs)
        logger.debug("Sensor %s → %s", entity_id, state)
    except httpx.TimeoutException:
        logger.warning("Timeout: %s", entity_id)
    except httpx.ConnectError:
        logger.warning("Brak połączenia HA: %s", entity_id)
    except Exception as exc:
        logger.exception("Błąd wysyłki %s: %s", entity_id, exc)
    finally:
        with _sent_hashes_lock:
            _in_flight.discard(entity_id)

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
        if _sent_hashes.get(entity_id) == h or entity_id in _in_flight:
            return
        _in_flight.add(entity_id)
    attrs = _trim_attrs(attrs)
    try:
        res: httpx.Response | None = None
        for attempt in range(1, 4):
            try:
                client = _get_sync_ha_client()
                res = client.post(
                    f"{HA_URL}/states/{entity_id}",
                    json={"state": state, "attributes": attrs},
                )
                if res.status_code in (401, 403):
                    logger.error(
                        "SYNC HTTP %d @ %s — brak autoryzacji HA. Sprawdź SUPERVISOR_TOKEN.",
                        res.status_code, entity_id,
                    )
                    return
                if res.status_code == 429:
                    wait = attempt * 5 + secrets.randbelow(3)
                    logger.warning("SYNC HA 429 @ %s — czekam %ds (próba %d/3)", entity_id, wait, attempt)
                    time.sleep(wait)
                    continue
                if res.status_code in (500, 502, 503, 504):
                    wait = attempt * 8
                    logger.warning("SYNC HA %d @ %s — czekam %ds (próba %d/3)", res.status_code, entity_id, wait, attempt)
                    time.sleep(wait)
                    continue
                break
            except Exception as exc:
                if attempt == 3:
                    raise
                logger.warning("SYNC błąd sieci @ %s: %s — próba %d/3", entity_id, exc, attempt)
                _reset_sync_ha_client()
                time.sleep(attempt * 3)
        if res is None or res.status_code not in (200, 201):
            logger.error(
                "SYNC HTTP %d @ %s → %s | %s",
                res.status_code if res else 0, entity_id, state,
                res.text[:200] if res else "brak odpowiedzi",
            )
            return
        with _sent_hashes_lock:
            _sent_hashes[entity_id] = h
            _sent_hashes.move_to_end(entity_id)
            if len(_sent_hashes) > _SENT_HASHES_MAX:
                _sent_hashes.popitem(last=False)
        _save_to_cache_sync(entity_id, state, attrs)
        logger.debug("Sensor sync %s → %s", entity_id, state)
    except Exception as exc:
        logger.warning("Błąd publish_sensor_sync %s: %s", entity_id, exc)
        _reset_sync_ha_client()
    finally:
        with _sent_hashes_lock:
            _in_flight.discard(entity_id)

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
                res = await ha.post(
                    f"{HA_URL}/states/{entity_id}",
                    headers=HA_HEADERS,
                    json={"state": state, "attributes": attrs},
                    timeout=5,
                )
                if res.status_code in (200, 201):
                    with _sent_hashes_lock:
                        _sent_hashes[entity_id] = h
                        _sent_hashes.move_to_end(entity_id)
                    restored += 1
            except Exception as e:
                logger.debug("Nie udało się odtworzyć %s: %s", entity_id, e)
        if restored > 0:
            logger.info("Sukces: Błyskawicznie przywrócono %d encji z bazy.", restored)
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
# Playwright — kontekst przeglądarki (stealth) z ochroną przed zombie
# ---------------------------------------------------------------------------

# FIX: globalny rejestr aktywnych procesów Playwright do czyszczenia przy wyjściu.
_active_playwright_instances: list[Any] = []
_playwright_registry_lock = threading.Lock()

def _get_browser_context(headless: bool = True) -> tuple:
    pw = sync_playwright().start()
    with _playwright_registry_lock:
        _active_playwright_instances.append(pw)
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

def _cleanup_playwright(pw: Any) -> None:
    """Zatrzymuje instancję Playwright i usuwa ją z globalnego rejestru."""
    try:
        pw.stop()
    except Exception as e:
        logger.debug("Błąd zatrzymania Playwright: %s", e)
    with _playwright_registry_lock:
        try:
            _active_playwright_instances.remove(pw)
        except ValueError:
            pass

def cleanup_all_playwright() -> None:
    """Wywoływane przy shutdown — zabija wszystkie pozostałe instancje Playwright."""
    with _playwright_registry_lock:
        instances = list(_active_playwright_instances)
    for pw in instances:
        try:
            pw.stop()
            logger.debug("Playwright zombie zatrzymany przy shutdown.")
        except Exception:
            pass

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
                shutil.copy(os.path.join(src, f), os.path.join(target, f))
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
            except httpx.RequestError:
                pass
            logger.info("Czekam na HA API… (%d/%d)", attempt, max_retries)
            await asyncio.sleep(5)
    logger.critical("HA API niedostępne po %d próbach. Zatrzymuję.", max_retries)
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
        existing = {
            _RE_LOVELACE_URL.sub("", r["url"]): (r["id"], r["url"])
            for r in raw
        }
        src_dir = "/app" if os.path.exists("/app") else "."
        cards = [
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

def _vulcan_login(page: Any, log_prefix: str = "[AUTH]") -> str:
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
        raise ex
    link = page.get_attribute("a[href*='dziennik']", "href") or ""
    if link.startswith("/"):
        link = urljoin(page.url, link)
    logger.debug("%s Pełny link do dziennika: %s", log_prefix, link)
    page.goto(link, timeout=45000)
    page.wait_for_load_state("networkidle", timeout=30000)
    return link

def run_diary_auth() -> tuple[list[StudentInfo] | None, CookieList | None]:
    page, context, browser, pw = _get_browser_context(headless=True)
    try:
        logger.info("[AUTH] Logowanie…")
        _vulcan_login(page, log_prefix="[AUTH]")
        m = _RE_CITY_URL.search(page.url)
        if not m:
            logger.error("[AUTH] Brak nazwy miasta w URL: %s", page.url)
            return None, None
        city = m.group(1)
        page.goto(f"https://uczen.eduvulcan.pl/{city}/api/Context", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        context_raw = page.inner_text("body")
        _raw = context_raw.strip().lower()
        if (_raw.startswith("<!doctype") or _raw.startswith("<html")) and not _raw.startswith("{"):
            logger.warning(
                "[AUTH] /api/Context zwrócił HTML zamiast JSON "
                "(prawdopodobnie 502/503 lub CAPTCHA): %s", context_raw[:300]
            )
            raise ConnectionError("SERVER_ERROR_OR_CAPTCHA")
        # Detekcja CAPTCHA/bana przez słowa kluczowe — nawet gdy serwer zwraca
        # poprawny JSON z kodem błędu lub redirect z tekstem blokady.
        _CAPTCHA_SIGNALS = ("captcha", "robot", "blocked", "zbyt wiele", "too many", "ban", "zablokowany")
        if any(sig in _raw for sig in _CAPTCHA_SIGNALS):
            logger.critical("[AUTH] Wykryto sygnał CAPTCHA/blokady w odpowiedzi serwera.")
            logger.debug("[AUTH] Surowa odpowiedź: %s", context_raw[:500])
            raise PermissionError("CAPTCHA_BLOKADA")
        try:
            context_data = json.loads(context_raw)
        except json.JSONDecodeError as e:
            logger.critical(
                "[AUTH] Krytyczny błąd: Nie można sparsować /api/Context "
                "(Prawdopodobnie CAPTCHA lub trwała blokada serwera). "
                "Wymuszam całkowite wyłączenie dodatku!"
            )
            logger.debug("[AUTH] Surowa odpowiedź: %s", context_raw[:500])
            raise PermissionError("CAPTCHA_BLOKADA") from e

        # FIX: Walidacja danych uczniów przed użyciem — str(None) dawało "None"
        # jako ID dziennika, co powodowało ciche błędy 400/404 w późniejszych requestach.
        raw_students = context_data.get("uczniowie") or []
        if not isinstance(raw_students, list):
            logger.error("[AUTH] Nieoczekiwany format 'uczniowie' w /api/Context")
            return None, None

        with httpx.Client(timeout=15) as session:
            for cookie in context.cookies():
                session.cookies.set(cookie["name"], cookie["value"])
            students: list[StudentInfo] = []
            for u in raw_students:
                key = u.get("key")
                id_dz_raw = u.get("idDziennik")
                uczen_name = u.get("uczen") or ""

                # FIX: jawna walidacja pól wymaganych przed wejściem do logiki
                if not key:
                    logger.warning("[AUTH] Pominięto ucznia '%s' — brak pola 'key'", uczen_name)
                    continue
                if id_dz_raw is None:
                    logger.warning("[AUTH] Pominięto ucznia '%s' — brak pola 'idDziennik'", uczen_name)
                    continue
                id_dz = str(id_dz_raw)

                res = session.get(
                    f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne",
                    params={"key": key, "idDziennik": id_dz},
                )
                if res.status_code != 200:
                    logger.warning("[AUTH] Brak okresów dla: %s (HTTP %d)", uczen_name, res.status_code)
                    continue
                okresy = res.json()
                curr_p = okresy[-1]["id"] if okresy else None
                for o in okresy:
                    try:
                        if (
                            datetime.strptime(o["dataOd"][:19], "%Y-%m-%dT%H:%M:%S")
                            <= datetime.now()
                            <= datetime.strptime(o["dataDo"][:19], "%Y-%m-%dT%H:%M:%S")
                        ):
                            curr_p = o["id"]
                            break
                    except (ValueError, KeyError):
                        continue

                # FIX: slugify z fallbackiem na hash — dwa uczniowie z identycznym
                # slugiem (np. same polskie znaki specjalne) nie kolidują w bazie.
                slug = slugify(uczen_name)
                existing_slugs = [s["slug"] for s in students]
                if slug in existing_slugs:
                    suffix = hashlib.sha256(f"{uczen_name}{id_dz}".encode()).hexdigest()[:6]
                    slug = f"{slug}_{suffix}"
                    logger.warning(
                        "[AUTH] Kolizja slug dla '%s' — używam '%s'", uczen_name, slug
                    )

                students.append(StudentInfo(
                    slug=slug,
                    uczen=uczen_name,
                    city=city,
                    key=key,
                    idDziennik=id_dz,
                    periodId=curr_p,
                    klasa=u.get("oddzial", ""),
                    globalKeySkrzynka=u.get("globalKeySkrzynka", ""),
                ))
            cookies: CookieList = context.cookies()
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
                path=os.path.join(err_dir, "vultron_auth_error_fatal.png"),
                full_page=True,
            )
        except Exception:
            logger.debug("Zrzut ekranu po błędzie nie powiódł się.")
        raise
    finally:
        # FIX: używamy _cleanup_playwright zamiast pw.stop() bezpośrednio,
        # by instancja była usuwana z globalnego rejestru zombie.
        try:
            context.close()
            browser.close()
        except Exception as cl_exc:
            logger.debug("Ignorowany błąd zamykania browser/context: %s", cl_exc)
        _cleanup_playwright(pw)

# ---------------------------------------------------------------------------
# Fetch helpers — async sekcje danych
# ---------------------------------------------------------------------------

async def _fetch_grades(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: StudentInfo,
) -> None:
    slug, key, id_dz, name = (s["slug"], s["key"], s["idDziennik"], s["uczen"])
    logger.info("--> [%s] Pobieram oceny...", name)
    res_per = await robust_get(
        client,
        f"{base}/api/OkresyKlasyfikacyjne",
        params={"key": key, "idDziennik": id_dz},
    )
    if res_per.status_code != 200:
        logger.warning("[%s] błąd okresów: %d", name, res_per.status_code)
        return
    for period in res_per.json():
        p_id = str(period.get("id", ""))
        p_num = period.get("numerOkresu", 0)
        res_g = await robust_get(
            client,
            f"{base}/api/Oceny",
            params={"key": key, "idOkresKlasyfikacyjny": p_id},
        )
        if res_g.status_code != 200:
            continue
        subjects: dict[str, list] = {}
        new_g = 0
        async with AsyncDB() as conn:
            for p_item in (res_g.json().get("ocenyPrzedmioty") or []):
                subj = p_item.get("przedmiotNazwa", "Inne")
                for kol in (p_item.get("kolumnyOcenyCzastkowe") or []):
                    id_k = str(kol.get("idKolumny", "0"))
                    desc = (
                        f"{kol.get('kategoriaKolumny', '')}: "
                        f"{kol.get('nazwaKolumny', '')}"
                    ).strip(": ")
                    for o in (kol.get("oceny") or []):
                        v = str(o.get("wpis", ""))
                        dt = str(o.get("dataOceny", ""))
                        cur = await conn.execute(
                            """INSERT INTO grades (id_kolumny, student_slug, przedmiot, ocena, data, opis, period_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?)
                               ON CONFLICT(id_kolumny, student_slug, period_id) DO UPDATE SET
                                   ocena=excluded.ocena,
                                   data=excluded.data,
                                   opis=excluded.opis
                               WHERE ocena!=excluded.ocena
                                  OR data!=excluded.data
                                  OR opis!=excluded.opis""",
                            (id_k, slug, subj, v, dt, desc, p_id),
                        )
                        if cur.rowcount > 0:
                            new_g += 1
                        subjects.setdefault(subj, []).append(
                            {"w": v, "d": dt[:5], "i": clean_text(desc)}
                        )
        lista = []
        for subj_name, grades in subjects.items():
            vals: list[float] = []
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
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram plan lekcji...", name)
    now = datetime.now()
    res = await robust_get(
        client,
        f"{base}/api/PlanZajec",
        params={
            "key": key,
            "dataOd": (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": (now + timedelta(days=21)).strftime("%Y-%m-%dT23:59:59.999Z"),
            "zakresDanych": "2",
        },
    )
    if res.status_code != 200:
        logger.warning("[%s] błąd planu: %d", name, res.status_code)
        return
    tasks = []
    async with AsyncDB() as conn:
        for lesson in res.json():
            st = MAPA_STATUSOW.get(int(lesson.get("adnotacja", 0)), "")
            inf = " ".join(
                (c.get("informacjeNieobecnosc") or "").lower()
                for c in (lesson.get("zmiany") or [])
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
                    f"{godz_od.split('T')[-1][:5]}-{godz_do.split('T')[-1][:5]}",
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
                "SELECT data,godzina,przedmiot,sala,prowadzacy,status "
                "FROM schedule WHERE student_slug=? AND data BETWEEN ? AND ? ORDER BY data,godzina",
                (slug, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")),
            )
            rows = await cursor.fetchall()
            proc = [
                {"d": r[0], "g": r[1], "p": r[2], "s": r[3], "n": r[4], "st": r[5]}
                for r in rows
            ]
            today = now.strftime("%Y-%m-%d")
            state = len([e for e in proc if e["d"] == today]) if suf == "curr" else len(proc)
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
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram terminarz...", name)
    now = datetime.now()
    last_day_prev_month = now.replace(day=1) - timedelta(days=1)
    res = await robust_get(
        client,
        f"{base}/api/SprawdzianyZadaniaDomowe",
        params={
            "key": key,
            "dataOd": last_day_prev_month.strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": (now + timedelta(days=61)).strftime("%Y-%m-%dT23:59:59.999Z"),
        },
    )
    if res.status_code != 200:
        logger.warning("[%s] błąd terminarza: %d", name, res.status_code)
        return
    items = res.json()
    async def _detail(item: dict) -> None:
        item_id_raw = item.get("id")
        if item_id_raw is None or str(item_id_raw) == "":
            return
        item_id = str(item_id_raw)
        ep = "ZadanieDomoweSzczegoly" if item.get("typ") == 4 else "SprawdzianSzczegoly"
        dj = {}
        try:
            dr = await robust_get(client, f"{base}/api/{ep}", params={"key": key, "id": item_id})
            if dr.status_code == 200:
                dj = dr.json()
        except httpx.RequestError as exc:
            logger.warning("[%s] błąd szczegółów terminarza %s: %s", name, item_id, exc)
        data_str = dj.get("data") or item.get("data", "")
        termin_str = dj.get("terminOdpowiedzi") or item.get("terminOdpowiedzi") or ""
        data = termin_str if termin_str else data_str
        raw_opis = (
            dj.get("opis") or dj.get("temat") or dj.get("tresc") or
            item.get("opis") or item.get("temat") or ""
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
                    item_id, slug, data, przedmiot,
                    MAPA_TYP_TERMINARZA.get(item.get("typ"), "Inne"),
                    czysty_opis, autor,
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
            "SELECT data,przedmiot,typ,opis,autor FROM timetable "
            "WHERE student_slug=? AND data>=? ORDER BY data",
            (slug, now.strftime("%Y-%m-%d")),
        )
        rows = await cursor.fetchall()
    await publish_sensor(
        ha,
        f"sensor.vultron_terminarz_{slug}",
        len(rows),
        f"Terminarz: {name}",
        {
            "lista": [
                {
                    "data": r[0].split("T")[0],
                    "przedmiot": r[1], "typ": r[2],
                    "opis": r[3], "autor": r[4],
                }
                for r in rows
            ]
        },
    )

async def _fetch_remarks(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram uwagi...", name)
    res = await robust_get(client, f"{base}/api/Uwagi", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd uwag: %d", name, res.status_code)
        return
    async with AsyncDB() as conn:
        for item in res.json():
            item_id_raw = item.get("id")
            if item_id_raw is None or str(item_id_raw) == "":
                continue
            item_id = str(item_id_raw)
            kat = str(item.get("kategoria") or "").lower()
            typ_u = "pozytywna" if "pochwa" in kat else ("negatywna" if "uwaga" in kat else "informacja")
            await conn.execute(
                "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                (
                    item_id, slug, item.get("data", "").split("T")[0],
                    item.get("tresc", ""), item.get("autor", ""), item.get("kategoria", ""),
                    str(item.get("liczbaPunktow") or ""), typ_u,
                ),
            )
        cursor = await conn.execute(
            "SELECT data,tresc,autor,kategoria,punkty,typ,remark_id "
            "FROM remarks WHERE student_slug=? ORDER BY data DESC",
            (slug,),
        )
        lista = [
            {
                "data": r[0], "tresc": r[1], "autor": r[2],
                "kategoria": r[3], "punkty": r[4], "typ": r[5], "id": r[6],
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
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram frekwencję...", name)
    now = datetime.now()
    try:
        res_f, res_p, res_fs = await asyncio.gather(
            robust_get(
                client,
                f"{base}/api/Frekwencja",
                params={
                    "key": key,
                    "dataOd": (now - timedelta(14)).strftime("%Y-%m-%dT00:00:00.000Z"),
                    "dataDo": now.strftime("%Y-%m-%dT23:59:59.999Z"),
                },
            ),
            robust_get(client, f"{base}/api/Przedmioty", params={"key": key}),
            robust_get(client, f"{base}/api/FrekwencjaStatystyki", params={"key": key, "idPrzedmiot": -1}),
        )
    except httpx.RequestError as e:
        logger.warning("[%s] błąd pobierania frekwencji (sieć): %s", name, e)
        return
    przedmioty: list = []
    if res_p.status_code == 200:
        try:
            przedmioty = res_p.json()
        except ValueError:
            pass
    else:
        logger.warning("[%s] błąd pobierania przedmiotów: %d", name, res_p.status_code)
    per_subject_list = [p for p in przedmioty if p.get("id", -1) != -1]
    _freq_sem = asyncio.Semaphore(5)
    async def _fetch_subject_stats(p: dict) -> httpx.Response:
        async with _freq_sem:
            return await robust_get(
                client,
                f"{base}/api/FrekwencjaStatystyki",
                params={"key": key, "idPrzedmiot": p["id"]},
            )
    per_subject_results = await asyncio.gather(
        *[_fetch_subject_stats(p) for p in per_subject_list],
        return_exceptions=True,
    )
    def _parse_rows(fsd: dict) -> list:
        result = []
        for row in (fsd.get("statystyki") or []):
            okresy = row.get("okresy") or [0, 0]
            result.append({
                "k": MAPA_FREKWENCJI.get(row.get("kategoriaFrekwencji"), "Inna"),
                "m": {str(m.get("miesiac", "")): m.get("wartosc", 0) for m in (row.get("miesiace") or [])},
                "s1": okresy[0] if len(okresy) > 0 else 0,
                "s2": okresy[1] if len(okresy) > 1 else 0,
                "r": row.get("razem", 0),
            })
        return result
    freq_wpisy: list = []
    freq_ok = False
    stats_global: dict = {}
    stats_per_subject: list = []
    index_subjects: list = []
    async with AsyncDB() as conn:
        today = now.strftime("%Y-%m-%d")
        if res_f.status_code == 200:
            recs = res_f.json()
            if isinstance(recs, dict):
                recs = recs.get("oddzialy") or []
            for fi in recs:
                fi_data = fi.get("data", "")
                fi_godz = fi.get("godzinaOd", "")
                if fi_data and fi_godz:
                    await conn.execute(
                        "INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                        (
                            f"{slug}_{fi_data}_{fi_godz}", slug,
                            fi_data.split("T")[0], fi_godz.split("T")[-1][:5],
                            int(fi.get("kategoriaFrekwencji", 0)),
                        ),
                    )
            since = (now - timedelta(14)).strftime("%Y-%m-%d")
            cursor = await conn.execute(
                "SELECT data,godzina,kategoria FROM frequency "
                "WHERE student_slug=? AND data>=? ORDER BY data DESC",
                (slug, since),
            )
            freq_wpisy = [{"d": r[0], "t": r[1], "k": int(r[2])} for r in await cursor.fetchall()]
            freq_ok = True
        else:
            logger.warning("[%s] błąd frekwencji: %d", name, res_f.status_code)
        if res_fs.status_code == 200:
            fsd_all = res_fs.json()
            rows_all = _parse_rows(fsd_all)
            pct_all = fsd_all.get("podsumowanie", 0)
            await conn.execute(
                "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                (
                    f"{slug}_-1_{today}", slug, today, -1, "Wszystkie",
                    pct_all, json.dumps(rows_all, ensure_ascii=False),
                ),
            )
            index_subjects = (
                [{"id": -1, "nazwa": "Wszystkie"}] +
                [{"id": p["id"], "nazwa": p["nazwa"]} for p in per_subject_list]
            )
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
                        continue
                    rows_p = _parse_rows(fsd_p)
                    await conn.execute(
                        "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                        (
                            f"{slug}_{p['id']}_{today}", slug, today,
                            p["id"], p["nazwa"], pct_p,
                            json.dumps(rows_p, ensure_ascii=False),
                        ),
                    )
                    stats_per_subject.append({
                        "slug_p": slugify(p["nazwa"]), "pct_p": pct_p, "rows_p": rows_p,
                        "pid": p["id"], "pnazwa": p["nazwa"],
                    })
                except Exception as e:
                    logger.warning("[%s] błąd parsowania %s: %s", name, p.get("nazwa"), e)
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
            *[publish_sensor(
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
            ) for sp in stats_per_subject],
            return_exceptions=True,
        )

async def _fetch_achievements(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram osiągnięcia...", name)
    res = await robust_get(client, f"{base}/api/Osiagniecia", params={"key": key})
    if res.status_code != 200:
        logger.warning("[%s] błąd osiągnięć: %d", name, res.status_code)
        return
    async with AsyncDB() as conn:
        for item in res.json():
            item_id_raw = item.get("id")
            if item_id_raw is None or str(item_id_raw) == "":
                continue
            item_id = str(item_id_raw)
            await conn.execute(
                "INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                (item_id, slug, item.get("tresc", "")),
            )
        cursor = await conn.execute(
            "SELECT achievement_id,tresc FROM achievements WHERE student_slug=?", (slug,)
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
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram szczęśliwy numerek...", name)
    now_str = datetime.now().strftime("%Y-%m-%d")
    api_numer: str | None = None
    api_id: str | None = None
    try:
        res = await robust_get(client, f"{base}/api/SzczesliwyNumerTablica", params={"key": key})
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, dict):
                api_numer = str(data.get("numer", "Brak"))
                api_id = str(data.get("id", ""))
        else:
            logger.warning("[%s] błąd szczęśliwego numerka API: %d", name, res.status_code)
    except Exception as e:
        logger.error("[%s] błąd pobierania szczęśliwego numerka: %s", name, e)
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
                "SELECT numer, numer_id FROM lucky_number WHERE student_slug=? AND data=?",
                (slug, now_str),
            )
            row = await cursor.fetchone()
            if row:
                db_numer, db_id = row
    except Exception as e:
        logger.error("Błąd bazy danych dla szczęśliwego numerka [%s]: %s", name, e)
    logger.debug("[%s] Publikuję sensor szczęśliwego numerka: %s", name, db_numer)
    state_val = 1 if db_numer != "Brak" else 0
    await publish_sensor(
        ha,
        f"sensor.vultron_szczesliwy_numerek_{slug}",
        state_val,
        f"Szczęśliwy Numerek: {name}",
        {"numer": db_numer, "id_numerku": db_id, "icon": "mdi:clover"},
    )

async def _fetch_meetings(
    client: httpx.AsyncClient,
    ha: httpx.AsyncClient,
    base: str,
    s: StudentInfo,
) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram zebrania z rodzicami...", name)
    try:
        res = await robust_get(client, f"{base}/api/Zebrania", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd zebrań: %d", name, res.status_code)
            return
        async with AsyncDB() as conn:
            for item in res.json():
                item_id_raw = item.get("id")
                if item_id_raw is None or str(item_id_raw) == "":
                    continue
                item_id = str(item_id_raw)
                dt_raw = item.get("dataCzas") or ""
                data_str = dt_raw.split("T")[0] if "T" in dt_raw else dt_raw
                godz_str = dt_raw.split("T")[1][:5] if "T" in dt_raw else ""
                sala = item.get("sala") or ""
                opis = item.get("opis") or ""
                online_raw = item.get("zebranieOnline")
                online = str(online_raw) if online_raw and not isinstance(online_raw, str) else (online_raw or "")
                await conn.execute(
                    "INSERT OR REPLACE INTO meetings VALUES (?,?,?,?,?,?,?)",
                    (item_id, slug, data_str, godz_str, sala, opis, online),
                )
            cursor = await conn.execute(
                "SELECT data, godzina, sala, opis, online, id "
                "FROM meetings WHERE student_slug=? ORDER BY data DESC, godzina DESC",
                (slug,),
            )
            rows = await cursor.fetchall()
            lista = [
                {
                    "data": r[0], "godzina": r[1], "sala": r[2],
                    "opis": r[3], "online": r[4], "id": r[5]
                }
                for r in rows
            ]
        now_date = datetime.now().strftime("%Y-%m-%d")
        nadchodzace = sum(1 for r in lista if r["data"] >= now_date)
        await publish_sensor(
            ha,
            f"sensor.vultron_zebrania_{slug}",
            nadchodzace,
            f"Zebrania: {name}",
            {"zebrania": lista, "icon": "mdi:account-group"},
        )
    except httpx.RequestError as e:
        logger.warning("[%s] błąd pobierania zebrań (sieć): %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd parsowania zebrań: %s", name, e)

# ---------------------------------------------------------------------------
# Synchronizacja dziennika
# ---------------------------------------------------------------------------

async def sync_diary_data(students: list[StudentInfo], cookies: CookieList) -> None:
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
                _fetch_meetings(client, ha, base, s),
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
# FIX: HTTP requesty przeniesione POZA transakcję SQLite.
# Poprzednio session.get() był wywoływany wewnątrz bloku "with conn:" co
# trzymało transakcję otwartą przez cały czas trwania requestów sieciowych,
# blokując db_lock_sync i uniemożliwiając asynchroniczne zapisy z AsyncDB.
# Teraz: najpierw pobieramy wszystkie dane przez HTTP (faza 1),
# potem zapisujemy do bazy jedną transakcją (faza 2).
# ---------------------------------------------------------------------------

def run_messages_sync(city: str, students_list: list[StudentInfo]) -> None:
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
                except OSError:
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

            # --- FAZA 1: Pobieranie przez HTTP (POZA transakcją SQLite) ---
            # Zbieramy wszystkie rekordy w pamięci; żadnych locków ani db tu.
            fetched_records: list[tuple] = []  # (key, slug, data, nadawca, temat, tresc, przeczytana)
            slugs_to_process: list[str] = []

            for st in students_list:
                gk = st.get("globalKeySkrzynka")
                slug = st["slug"]
                if not gk:
                    logger.warning("[MESS] Brak globalKeySkrzynka dla ucznia %s", st["uczen"])
                    continue
                slugs_to_process.append(slug)
                res_m = session.get(
                    f"https://wiadomosci.eduvulcan.pl/{city}/api/OdebraneSkrzynka"
                    f"?globalKeySkrzynka={gk}&idLastWiadomosc=0&pageSize=50"
                )
                if res_m.status_code != 200:
                    logger.warning("[MESS] błąd pobierania dla %s: %d", st["uczen"], res_m.status_code)
                    if res_m.status_code == 400:
                        logger.warning(
                            "[MESS] Wykryto przepełnienie ciasteczek (błąd 400). Usuwam bul.pkl..."
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
                    if det.status_code != 200:
                        continue
                    kor_raw = m.get("korespondenci", "")
                    kor_str = (
                        json.dumps(kor_raw, ensure_ascii=False)
                        if isinstance(kor_raw, (list, dict))
                        else str(kor_raw)
                    )
                    fetched_records.append((
                        m_k, slug, m.get("data", ""),
                        kor_str, m.get("temat", ""),
                        det.json().get("tresc", "Brak"),
                        1 if m.get("przeczytana") else 0,
                    ))

            # --- FAZA 2: Zapis do bazy (krótka transakcja, bez HTTP) ---
            with db_lock_sync:
                with closing(db_connect()) as conn:
                    with conn:
                        cur = conn.cursor()
                        for record in fetched_records:
                            cur.execute(
                                "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                                record,
                            )

            # --- FAZA 3: Odczyt i publikacja sensorów ---
            # Odczyt i publish również poza db_lock_sync — publish_sensor_sync
            # ma własny wewnętrzny lock i nie potrzebuje zewnętrznej ochrony.
            for st in students_list:
                slug = st["slug"]
                if slug not in slugs_to_process:
                    continue
                with closing(db_connect()) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT data,nadawca,temat,tresc,przeczytana "
                        "FROM messages WHERE student_slug=? ORDER BY data DESC LIMIT 10",
                        (slug,),
                    )
                    rows = cur.fetchall()
                    unread_count = cur.execute(
                        "SELECT COUNT(*) FROM messages WHERE student_slug=? AND przeczytana=0",
                        (slug,),
                    ).fetchone()[0]
                    total_count = cur.execute(
                        "SELECT COUNT(*) FROM messages WHERE student_slug=?",
                        (slug,),
                    ).fetchone()[0]
                msgs = []
                for r in rows:
                    is_u = int(r["przeczytana"]) == 0
                    if is_u:
                        body = clean_html(r["tresc"])
                        if len(body) > 2000:
                            body = body[:1997] + "..."
                    else:
                        body = ""
                    msgs.append({
                        "data": r["data"].replace("T", " ")[:16],
                        "nadawca": r["nadawca"],
                        "temat": r["temat"],
                        "tresc": body,
                        "przeczytana": not is_u,
                    })
                publish_sensor_sync(
                    f"sensor.vultron_wiadomosci_{slug}",
                    unread_count,
                    f"Wiadomości: {st['uczen']}",
                    {"wiadomosci": msgs, "stats": f"{unread_count} / {total_count}"},
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
        except Exception as e:
            logger.debug("Błąd zamykania browser/context [MESS]: %s", e)
        # FIX: używamy _cleanup_playwright zamiast pw.stop() bezpośrednio
        _cleanup_playwright(pw)

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
            logger.warning("Monitor template: nieoczekiwany format odpowiedzi")
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
                    "szczegoly": " | ".join(f"{e['id']}: {e['size']}B" for e in ents),
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
    except httpx.RequestError as e:
        logger.warning("Monitor rozmiaru błąd połączenia: %s", e)
    except Exception as e:
        logger.error("Monitor rozmiaru: %s", e)

# ---------------------------------------------------------------------------
# Główna pętla
# ---------------------------------------------------------------------------

async def main_loop() -> None:
    global _auth_fail_count
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await asyncio.to_thread(copy_resources)
    await wait_for_ha_api()
    await asyncio.to_thread(run_setup_ui)
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
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=float(secs))
                except asyncio.TimeoutError:
                    pass
                continue

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
            except PermissionError as e:
                _auth_fail_count += 1
                backoff = _AUTH_BACKOFF[min(_auth_fail_count, len(_AUTH_BACKOFF) - 1)]
                logger.critical(
                    "CAPTCHA lub trwała blokada serwera (%s). "
                    "Błąd nr %d z rzędu. Czekam %ds przed ponowną próbą.", e, _auth_fail_count, backoff
                )
                await publish_sensor(
                    ha, "sensor.vultron_status", "captcha",
                    "Vultron Status",
                    {"bledy_z_rzedu": _auth_fail_count, "szczegoly": str(e)[:200],
                     "ostatnia_proba": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=float(backoff))
                except asyncio.TimeoutError:
                    pass
                continue
            except Exception as e:
                logger.critical(
                    "KRYTYCZNY BŁĄD PODCZAS GŁÓWNEGO LOGOWANIA: %s. Zatrzymuję dodatek!", e
                )
                await fatal_error_stop(ha, str(e))
                break
            if not students or not cookies:
                _auth_fail_count += 1
                backoff = _AUTH_BACKOFF[min(_auth_fail_count, len(_AUTH_BACKOFF) - 1)]
                logger.warning(
                    "Logowanie zwróciło pusty wynik (błąd nr %d z rzędu). Czekam %ds.",
                    _auth_fail_count, backoff,
                )
                await publish_sensor(
                    ha, "sensor.vultron_status", "blad_logowania",
                    "Vultron Status",
                    {"bledy_z_rzedu": _auth_fail_count,
                     "ostatnia_proba": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=float(backoff))
                except asyncio.TimeoutError:
                    pass
                continue
            # Logowanie udane — zerujemy licznik i publikujemy status OK
            _auth_fail_count = 0
            await publish_sensor(
                ha, "sensor.vultron_status", "ok",
                "Vultron Status",
                {"bledy_z_rzedu": 0,
                 "ostatnia_synchronizacja": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            )
            if students and cookies:
                await sync_diary_data(students, cookies)
                try:
                    if len(students) > 0:
                        await asyncio.wait_for(
                            asyncio.to_thread(run_messages_sync, students[0]["city"], students),
                            timeout=120.0
                        )
                except asyncio.TimeoutError:
                    await fatal_error_stop(ha, "Timeout (120s) - proces Playwright zawiesił się podczas logowania do wiadomości")
                    break
                except Exception as e:
                    logger.critical(
                        "KRYTYCZNY BŁĄD PODCZAS LOGOWANIA DO WIADOMOŚCI: %s. Zatrzymuję dodatek!", e
                    )
                    await fatal_error_stop(ha, str(e))
                    break
            await _run_size_monitor(ha)

            now_after = datetime.now()
            wd_after = now_after.weekday()
            wait_time: int
            if not _test_mode:
                if wd_after == 5:
                    next_h = next((h for h in (8, 16, 23) if h > now_after.hour), None)
                    wake_at = (
                        now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                        if next_h
                        else (now_after + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                    )
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("Cykl OK (Sobota) → następne pobieranie o %s (za ~%d min)", wake_at.strftime("%H:%M"), wait_time // 60)
                elif wd_after == 6:
                    next_h = next((h for h in (8, 12, 20) if h > now_after.hour), None)
                    wake_at = (
                        now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                        if next_h
                        else (now_after + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
                    )
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("Cykl OK (Niedziela) → następne pobieranie o %s (za ~%d min)", wake_at.strftime("%H:%M"), wait_time // 60)
                else:
                    wait_time = secrets.randbelow(MAX_JITTER_SECONDS) + MIN_WAIT_SECONDS
                    next_run = now_after + timedelta(seconds=wait_time)
                    logger.info("Cykl OK → następny za ~%d min (o %s)", wait_time // 60, next_run.strftime("%H:%M"))
            else:
                wait_time = secrets.randbelow(241) + 60
                next_run = now_after + timedelta(seconds=wait_time)
                logger.info("[TEST MODE] Cykl OK → następny za ~%d s (o %s)", wait_time, next_run.strftime("%H:%M:%S"))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(wait_time))
            except asyncio.TimeoutError:
                pass

        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
    logger.info("Vultron zatrzymany (graceful shutdown).")

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamykanie…")
    finally:
        # FIX: cleanup zombie Playwright przy każdym wyjściu z procesu
        cleanup_all_playwright()
    sys.exit(0)
