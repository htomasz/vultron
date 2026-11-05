from __future__ import annotations
import asyncio
import ctypes
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
import httpx
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
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
OPTIONS_PATH = "/data/options.json"

# Dodatek HA (Supervisor ustawia SUPERVISOR_TOKEN) albo samodzielny kontener
# Docker (HA_TOKEN, HA_URL i HA_WS_URL podaje użytkownik).
IS_ADDON = bool(os.getenv("SUPERVISOR_TOKEN"))

def _read_secret(env_name: str, file_env_name: str, default_file: str) -> str:
    """Sekret z pliku (Docker secret; ścieżkę zmienia `file_env_name`, np.
    HA_TOKEN_FILE), a gdy pliku brak - ze zmiennej `env_name`. Plik jest
    lepszy, bo zmiennych środowiskowych nie widać w `docker inspect`."""
    path = os.getenv(file_env_name, default_file)
    if path and os.path.isfile(path):
        return open(path, encoding="utf-8").read().strip()
    return os.getenv(env_name, "")

if IS_ADDON:
    HA_TOKEN  = os.getenv("SUPERVISOR_TOKEN", "")
    HA_URL    = "http://supervisor/core/api"
    HA_WS_URL = "ws://supervisor/core/websocket"
else:
    HA_TOKEN  = _read_secret("HA_TOKEN", "HA_TOKEN_FILE", "/run/secrets/ha_token")
    HA_URL    = os.getenv("HA_URL", "")
    HA_WS_URL = os.getenv("HA_WS_URL", "")

HA_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

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

# Do wczytania konfiguracji wystarczy INFO; docelowy poziom ustawiamy niżej
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_ch  = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_fh  = logging.handlers.RotatingFileHandler("/data/vultron.log", maxBytes=1_048_576, backupCount=5)
_fh.setFormatter(_fmt)
logger.addHandler(_ch)
logger.addHandler(_fh)
logger.propagate = False

# Wyciszenie logów bibliotek
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# /data (options.json z hasłem, vul.pkl = aktywna sesja, baza, log) tylko dla
# właściciela. W Dockerze ./data na hoście nie jest wtedy dostępne dla innych
# użytkowników, także dla plików utworzonych przed tą zmianą.
try:
    os.chmod("/data", 0o700)
except OSError as _e:
    logger.warning("Nie udało się ograniczyć uprawnień /data: %s", _e)

# ────────────────────────────────────────────────
# WERYFIKACJA ŚRODOWISKA HA
# ────────────────────────────────────────────────

if not os.path.exists(OPTIONS_PATH):
    logger.critical("Brak pliku options.json. Przerwano uruchamianie.")
    sys.exit(1)

if IS_ADDON:
    if not HA_TOKEN:
        logger.critical("SUPERVISOR_TOKEN nie jest ustawiony. Upewnij się, że skrypt działa w środowisku HA.")
        sys.exit(1)
else:
    if not HA_TOKEN:
        logger.critical(
            "Brak tokena Home Assistant: ani SUPERVISOR_TOKEN (tryb dodatku HA), "
            "ani HA_TOKEN (tryb standalone) nie jest ustawiony. Jeśli to ma być "
            "dodatek HA, sprawdź czy Supervisor poprawnie go uruchomił. Jeśli to "
            "samodzielny kontener Docker, ustaw HA_TOKEN albo zamontuj sekret pod "
            "/run/secrets/ha_token (inną ścieżkę można wskazać zmienną HA_TOKEN_FILE)."
        )
        sys.exit(1)
    if not HA_URL or not HA_WS_URL:
        logger.critical(
            "Tryb standalone wymaga zmiennych środowiskowych HA_URL i HA_WS_URL "
            "(adresy REST API i WebSocket Home Assistanta, np. "
            "http://homeassistant:8123/api i ws://homeassistant:8123/api/websocket)."
        )
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

    logger.warning(
        "UWAGA: tryb TRACE zapisuje pełne odpowiedzi API (oceny, uwagi, treści "
        "wiadomości) do /data/vultron.log - log zawiera DANE OSOBOWE. "
        "Przejrzyj go przed udostępnieniem komukolwiek i wyłącz trace po diagnozie."
    )

    # TRACE loguje każde zapytanie HTTP i początek odpowiedzi
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

# Kalendarz HA z własnymi zajęciami, wydarzenia w formacie "{Imię}: Nazwa zajęć"
# (np. "Jan: Dodatkowy angielski"). Pusta lub błędna wartość -> domyślna encja.
_CAL_ENTITY_RAW = CONFIG.get("calendar_entity") or "calendar.local_szkola"
CALENDAR_ENTITY = str(_CAL_ENTITY_RAW).strip() or "calendar.local_szkola"
if not re.fullmatch(r"calendar\.[a-z0-9_]+", CALENDAR_ENTITY):
    logger.warning(
        "Nieprawidłowa nazwa encji kalendarza w konfiguracji (%r) - używam domyślnej calendar.local_szkola.",
        CALENDAR_ENTITY,
    )
    CALENDAR_ENTITY = "calendar.local_szkola"

# Status zajęć własnych (z kalendarza HA); statusy z Vulcana to ZAST/PRZEN/ODWOL/NIEOB.
STATUS_WLASNE = "WLASNE"

# Zapisaną sesję (VUL_PKL) starszą niż tyle godzin pomijamy i logujemy się od nowa.
# Młodsza sesja i tak jest sprawdzana zapytaniem httpx (_try_reuse_cached_session).
SESSION_CACHE_MAX_AGE_HOURS = 6

# ────────────────────────────────────────────────
# RETENCJA DANYCH (przycinanie starych wpisów z bazy)
# ────────────────────────────────────────────────
# Wpisy starsze niż 1,5 roku (547,5 dnia, zaokrąglone w górę) są usuwane, żeby
# baza nie rosła bez końca.
RETENTION_DAYS = 548

# ha_cache służy tylko do przywracania sensorów po restarcie HA (patrz
# restore_entities_from_cache), więc ma krótszy próg: encja nieaktualizowana
# tyle dni to zwykle usunięte dziecko albo zmieniony slug.
HA_CACHE_RETENTION_DAYS = 60

# Backoff po kolejnych nieudanych pełnych logowaniach (ogranicza ryzyko CAPTCHA):
# +10 min za każde kolejne niepowodzenie (pierwsze nie wydłuża przerwy), maks. +60 min.
AUTH_BACKOFF_STEP_SECONDS = 600
AUTH_BACKOFF_MAX_SECONDS = 3600

# Retencja uruchamia się najwyżej raz na tyle godzin (sprawdza plik-znacznik).
RETENTION_CHECK_INTERVAL_HOURS = 20
RETENTION_MARKER_PATH = "/data/.vultron_retention_last_run"

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

# threading.Lock (nie asyncio.Lock), bo słownik używają i coroutines
# (publish_sensor), i wątki (publish_sensor_sync).
_sent_hashes_lock = threading.Lock()

# Dwa niezależne locki SQLite (nie wykluczają się wzajemnie; spójność zapewnia
# WAL + timeout=30, a wiadomości i tak startują po fetcherach):
#   db_lock        – asyncio.Lock()   – zapisy z coroutines
#   db_lock_thread – threading.Lock() – zapisy z wątku wiadomości
# Retencja z VACUUM bierze oba (_run_retention_if_due i _prune_old_data).
db_lock        = asyncio.Lock()
db_lock_thread = threading.Lock()

# ────────────────────────────────────────────────
# HELPERS (REGEX I HTML PARSER)
# ────────────────────────────────────────────────

_RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')
_RE_SPACES = re.compile(r' {2,}')

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


def _safe_int(value, default: int = 0) -> int:
    """int z wartości JSON; null i śmieci dają `default`.
    (dict.get(klucz, 0) nie wystarcza: przy kluczu z wartością null zwraca None.)"""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def slugify(text: str) -> str:
    if not text:
        return "unknown"
    return re.sub(r"[^a-z0-9]+", "_", text.lower().translate(_PL_TRANS)).strip("_")


def _fold_pl(text: str) -> str:
    """Małe litery bez polskich znaków, do porównań. Długość zostaje taka sama,
    więc indeksy dopasowania w wersji zwiniętej pasują do tekstu oryginalnego."""
    return (text or "").lower().translate(_PL_TRANS)

def clean_html(raw: str) -> str:
    """HTML -> zwykły tekst: zachowuje nowe linie i listy, pogrubienia zamienia
    na Markdown, adresy linków i obrazków dopisuje do tekstu (tylko bezpieczne
    adresy, bez javascript: i data:)."""
    if not raw:
        return "Brak opisu"

    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_data().replace("&nbsp;", " ")

    text = _RE_MULTIPLE_NEWLINES.sub('\n\n', text)
    text = _RE_SPACES.sub(' ', text)

    return text.strip() or "Brak opisu"

_RE_DATE_ISO    = re.compile(r"\d{4}-\d{2}-\d{2}")
_RE_DATE_DOTTED = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")


def _date_or_empty(raw) -> str:
    """Data z API do atrybutu sensora: "YYYY-MM-DD" lub "D.M.RRRR" (część przed
    "T"), cokolwiek innego -> "". Nie przepuszczamy dowolnego tekstu z API
    (druga warstwa obok _esc() w kartach)."""
    value = str(raw or "").split("T")[0].strip()
    return value if (_RE_DATE_ISO.fullmatch(value) or _RE_DATE_DOTTED.fullmatch(value)) else ""


def _txt(value) -> str:
    """Pole z API do SQLite: None -> "", nie-tekst -> str(), żeby jedna dziwna
    wartość nie przerwała zapisu całej partii."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def clean_text(text: str, max_len: int = 200) -> str:
    t = str(text).replace("\n", " ").replace("\r", "") if text else ""
    return t[: max_len - 3] + "..." if len(t) > max_len else t

def _parse_cal_dt(raw) -> datetime | None:
    """Pole start/end wydarzenia kalendarza HA -> datetime.

    HA zwraca obiekt {"dateTime": "2026-09-07T07:00:00+02:00"} (z godziną),
    obiekt {"date": "2026-09-07"} (całodniowe) albo gołe stringi w tych formatach.
    Dla całodniowych i niepoprawnych zwraca None - nie mieszczą się w slocie planu.
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        raw = raw.get("dateTime") or None  # brak "dateTime" (np. tylko "date") -> całodniowe, None
    if not raw or not isinstance(raw, str):
        return None
    if len(raw) <= 10:  # samo "YYYY-MM-DD" -> wydarzenie całodniowe
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payload_hash(state, attrs_no_timestamp: dict) -> str:
    raw = json.dumps({"state": state, "attributes": attrs_no_timestamp},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

# ────────────────────────────────────────────────
# CACHE ENCJI – jedno trwałe połączenie
# _save_to_cache woła każda publikacja sensora (dziesiątki razy na cykl), więc
# trzymamy jedno połączenie SQLite zamiast otwierać nowe (kosztowne na karcie
# SD). Chroni je threading.Lock, bo piszą i coroutines, i wątki.
# ────────────────────────────────────────────────
_cache_conn: sqlite3.Connection | None = None
_cache_conn_lock = threading.Lock()

# Każdy zapis jest od razu zatwierdzany. Odkładanie commitów (batch) sprawdzono i
# wycofano: otwarta transakcja blokowała inne połączenia
# ("database is locked").

def _save_to_cache(entity_id: str, state, attrs: dict) -> None:
    global _cache_conn
    payload = (entity_id, str(state), json.dumps(attrs, ensure_ascii=False))
    with _cache_conn_lock:
        for attempt in range(2):
            try:
                if _cache_conn is None:
                    _cache_conn = db_connect()
                _cache_conn.execute(
                    "INSERT OR REPLACE INTO ha_cache (entity_id, state, attributes_json) VALUES (?, ?, ?)",
                    payload,
                )
                _cache_conn.commit()
                return
            except Exception as e:
                # Połączenie mogło paść - otwieramy nowe i próbujemy raz jeszcze.
                try:
                    if _cache_conn is not None:
                        _cache_conn.close()
                except Exception as close_err:
                    logger.debug("Błąd zamykania połączenia ha_cache: %s", close_err)
                _cache_conn = None
                if attempt == 1:
                    logger.error("Błąd zapisu do ha_cache dla %s: %s", entity_id, e)

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
    attrs = {
        "friendly_name": friendly_name,
        "last_update":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }

    h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})

    with _sent_hashes_lock:
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
        with _sent_hashes_lock:
            _sent_hashes[entity_id] = h
        logger.debug("Sensor %s → %s", entity_id, state)
    except httpx.TimeoutException:
        logger.warning("Timeout: %s", entity_id)
    except httpx.ConnectError:
        logger.warning("Brak połączenia HA: %s", entity_id)
    except Exception as exc:
        logger.exception("Błąd wysyłki %s: %s", entity_id, exc)


# ────────────────────────────────────────────────
# HA SENSOR – sync publish (wątek wiadomości)
# Wątek porzucony po timeoucie (600 s) może pisać do _sent_hashes równolegle
# z kolejnym cyklem; chroni to ten sam threading.Lock co w publish_sensor.
# ────────────────────────────────────────────────

def publish_sensor_sync(entity_id: str, state, friendly_name: str, extra_attrs: dict | None = None) -> None:
    attrs = {
        "friendly_name": friendly_name,
        "last_update":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **(extra_attrs or {}),
    }
    h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})

    with _sent_hashes_lock:
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
            with _sent_hashes_lock:
                _sent_hashes[entity_id] = h
    except Exception as exc:
        logger.warning("Błąd publish_sensor_sync %s: %s", entity_id, exc)


# ────────────────────────────────────────────────
# ODTWARZANIE CACHE PO RESTARCIE HA
# ────────────────────────────────────────────────

async def restore_entities_from_cache(ha: httpx.AsyncClient) -> None:
    conn = None
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT entity_id, state, attributes_json FROM ha_cache")
        rows = cur.fetchall()
        conn.close()
        conn = None

        restored = 0
        for entity_id, state, attrs_json in rows:
            try:
                attrs = json.loads(attrs_json)
                h = _payload_hash(state, {k: v for k, v in attrs.items() if k != "last_update"})

                res = await ha.post(
                    f"{HA_URL}/states/{entity_id}",
                    headers=HA_HEADERS,
                    json={"state": state, "attributes": attrs},
                    timeout=5
                )
                if res.status_code in (200, 201):
                    restored += 1
                    # Hash zapisujemy dopiero po udanym POST; inaczej nieudane
                    # odtworzenie blokowałoby publikację encji do zmiany danych.
                    with _sent_hashes_lock:
                        _sent_hashes[entity_id] = h
                else:
                    logger.debug("Odtworzenie %s: HTTP %d", entity_id, res.status_code)
            except Exception as e:
                logger.debug("Nie udało się odtworzyć %s: %s", entity_id, e)

        if restored > 0:
            logger.info("Sukces: Błyskawicznie przywrócono %d encji z bazy danych.", restored)
    except Exception as e:
        logger.error("Błąd bazy danych przy odtwarzaniu cache: %s", e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logger.debug("Błąd zamykania bazy przy odtwarzaniu cache: %s", e)

async def check_and_restore(ha: httpx.AsyncClient) -> None:
    try:
        r = await ha.get(f"{HA_URL}/states/sensor.vultron_system_monitor", timeout=4)
        if r.status_code == 404:
            logger.warning("Wykryto restart Home Assistanta! Wstrzykuję stan z bazy...")
            await restore_entities_from_cache(ha)
    except Exception as e:
        logger.debug("Sprawdzenie restartu HA nieudane: %s", e)


# ────────────────────────────────────────────────
# SELENIUM HELPER
# ────────────────────────────────────────────────

def _log_timezone_info() -> None:
    """Loguje na starcie strefę czasową widzianą przez Pythona (diagnostyka).
    Kod zakłada, że datetime.now() zwraca czas lokalny; bez zmiennej TZ
    (np. poza HA OS/Supervised) kontener działa w UTC, i ten log to pokaże."""
    try:
        local_now = datetime.now().astimezone()
        logger.info(
            "[STREFA CZASOWA] Python widzi: %s (UTC%s) - aktualny czas lokalny: %s",
            local_now.tzname(), local_now.strftime("%z"),
            local_now.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except Exception as e:
        logger.debug("[STREFA CZASOWA] Nie udało się odczytać strefy czasowej: %s", e)


def _become_child_subreaper() -> None:
    """Ustawia ten proces jako "subreaper" (PR_SET_CHILD_SUBREAPER): jądro
    przekazuje mu osierocone procesy potomne (np. chrome po zabiciu
    chromedrivera w _hard_kill_service), także gdy nie jest PID 1. Dzięki temu
    sprzątanie Chromium nie zależy od tego, że Dockerfile startuje
    `python3 vultron.py` bez systemu init. Błąd jest tylko logowany."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        result = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        if result != 0:
            err = ctypes.get_errno()
            logger.warning(
                "[INIT] Nie udało się ustawić PR_SET_CHILD_SUBREAPER (errno=%d) - "
                "sprzątanie osieroconych procesów Chromium będzie działać tylko "
                "dopóki dodatek pozostaje PID 1 kontenera.", err,
            )
        else:
            logger.debug("[INIT] PR_SET_CHILD_SUBREAPER ustawiony poprawnie.")
    except Exception as e:
        logger.warning("[INIT] Błąd przy ustawianiu PR_SET_CHILD_SUBREAPER: %s", e)


def _reap_orphaned_children() -> None:
    """Odbiera zakończone procesy potomne (w tym osierocone Chromium, patrz
    _become_child_subreaper). Można wołać w każdej chwili: dodatek nie tworzy
    innych procesów niż te z Selenium, a WNOHANG nie blokuje."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break  # brak procesów potomnych
        except Exception as e:
            # Sprzątanie nie może maskować błędów wołającego - tylko logujemy.
            logger.debug("[AUTH] Błąd przy odbieraniu procesów potomnych: %s", e)
            break
        if pid == 0:
            break  # żyjące dzieci są, ale żadne się nie zakończyło
        logger.debug("[AUTH] Odebrano zakończony proces potomny PID %d.", pid)


def _hard_kill_service(service) -> None:
    """Zabija całe drzewo procesów chromedrivera (SIGKILL na grupę procesów).

    driver.quit()/service.stop() zamykają tylko sam chromedriver; jego dzieci
    (chrome, zygote, renderer, GPU) zostają osierocone i zajmują RAM, co po
    kilku nieudanych logowaniach kończyło się OOM na Raspberry Pi z 2 GB.
    Wymaga `popen_kw={"start_new_session": True}` w _get_driver: wtedy cała
    rodzina ma jedną grupę procesów (PGID == PID chromedrivera).
    Bezpieczna w każdej sytuacji; jeśli proces już nie żyje, tylko sprząta
    (_reap_orphaned_children).
    """
    try:
        process = getattr(service, "process", None)
        if process is None:
            return
        if process.poll() is not None:
            return  # już zakończony

        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            return  # zdążył umrzeć między poll() a teraz

        # Bezpiecznik: nigdy nie zabijamy własnej grupy procesów. Przy
        # start_new_session ten warunek nie powinien się spełnić.
        if pgid == os.getpgrp():
            logger.critical(
                "[AUTH] Bezpiecznik: PGID chromedrivera (%d) pokrywa się z grupą "
                "procesów dodatku - pomijam killpg, żeby nie zabić samego siebie.",
                pgid,
            )
            return

        try:
            os.killpg(pgid, signal.SIGKILL)
            logger.warning(
                "[AUTH] Proces chromedrivera (PID %d) nie zakończył się po quit() - "
                "wymuszono zabicie całej grupy procesów (PGID %d, chromedriver + "
                "Chromium + jego procesy potomne), żeby nie zostawić osieroconych "
                "procesów zajmujących RAM.",
                process.pid, pgid,
            )
        except ProcessLookupError:
            pass  # grupa zniknęła sama
        except Exception as e:
            logger.error("[AUTH] Błąd przy wymuszonym zabiciu grupy procesów chromedrivera: %s", e)
    finally:
        # Odbieramy zawsze, także procesy osierocone przez killpg.
        _reap_orphaned_children()


def _log_available_memory(context: str = "") -> None:
    """Loguje stan pamięci z /proc/meminfo (diagnostyka zawieszeń Chromium na
    słabszym sprzęcie). "MemAvailable" uwzględnia cache, który jądro może
    oddać, więc lepiej niż "MemFree" pokazuje realnie wolną pamięć."""
    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                if key in ("MemTotal", "MemFree", "MemAvailable"):
                    # wartości w kB, z sufiksem " kB"
                    value_kb = int(parts[1].strip().split()[0])
                    meminfo[key] = value_kb // 1024  # -> MB

        if "MemAvailable" in meminfo:
            logger.info(
                "[PAMIĘĆ]%s Dostępne: %d MB | Wolne: %d MB | Razem: %d MB",
                f" [{context}]" if context else "",
                meminfo.get("MemAvailable", -1),
                meminfo.get("MemFree", -1),
                meminfo.get("MemTotal", -1),
            )
        else:
            logger.debug("[PAMIĘĆ] /proc/meminfo nie zawiera oczekiwanych pól.")
    except Exception as e:
        logger.debug("[PAMIĘĆ] Nie udało się odczytać /proc/meminfo: %s", e)


def _get_driver() -> webdriver.Chrome:
    opts = Options()
    opts.page_load_strategy = 'eager'  # nie czeka na skrypty i obrazki poboczne

    # Flagi oszczędzające RAM i CPU
    flags = (
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-software-rasterizer",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--blink-settings=imagesEnabled=false",
        "--disable-site-isolation-trials",
        # Nie ustawiamy --js-flags=--max-old-space-size=128: limit sterty V8 na
        # każdy proces renderera (też iframe) jest podejrzewany o timeouty
        # renderera na słabszym sprzęcie.
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--disable-background-timer-throttling",
        "--disable-breakpad",
        "--log-level=3",  # mniej logów w konsoli
        # procesy poboczne zbędne w headlessie (kilka-kilkanaście % RAM):
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-backgrounding-occluded-windows",
    )
    for arg in flags:
        opts.add_argument(arg)

    opts.binary_location = "/usr/bin/chromium-browser"

    # Logi chromedrivera idą do /dev/null (oszczędność I/O na karcie SD)
    service = Service(
        executable_path="/usr/bin/chromedriver",
        log_path=os.devnull,
        # Własna grupa procesów chromedrivera i jego dzieci: pozwala zabić całe
        # drzewo jednym sygnałem (patrz _hard_kill_service).
        popen_kw={"start_new_session": True},
    )

    try:
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception:
        # Gdy sesja przeglądarki nie powstanie (np. zawieszenie, ~120 s timeoutu),
        # Selenium sprząta tylko sam chromedriver - dobijamy więc całą grupę.
        _hard_kill_service(service)
        raise

    try:
        # 75 s: strona logowania Vulcana (zwłaszcza wariant "historyczna") ładuje
        # się u części użytkowników dłużej niż wcześniejsze 45 s.
        driver.set_page_load_timeout(75)
    except Exception:
        # Proces już wystartował, więc trzeba go zamknąć, inaczej zostanie zombie.
        try:
            driver.quit()
        except Exception as e:
            logger.debug("[AUTH] Zignorowano błąd przy zamykaniu przeglądarki: %s", e)
        _hard_kill_service(service)
        raise
    return driver

# ────────────────────────────────────────────────
# SQLITE HELPERS
# ────────────────────────────────────────────────

_DB_DDL =[
    # Brak PRIMARY KEY jest celowy: kolumna może mieć dwie oceny (poprawa 3 -> 5).
    # Oceny okresu są podmieniane w całości (DELETE + INSERT) w każdym cyklu,
    # co usuwa też oceny wycofane w dzienniku.
    """CREATE TABLE IF NOT EXISTS grades (
        id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT,
        data TEXT, opis TEXT, period_id TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_grades_student_period
        ON grades(student_slug, period_id)""",
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
        PRIMARY KEY(student_slug, data))""",
    """CREATE TABLE IF NOT EXISTS free_days (
        student_slug TEXT, data TEXT, nazwa TEXT,
        PRIMARY KEY(student_slug, data))""",
    """CREATE TABLE IF NOT EXISTS meetings (
        id TEXT, student_slug TEXT, data TEXT, godzina TEXT,
        sala TEXT, opis TEXT, online TEXT,
        PRIMARY KEY(id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS przedszkole_obecnosc (
        student_slug TEXT, data TEXT, obecnosc INTEGER,
        godzina_wejscia TEXT, godzina_wyjscia TEXT,
        PRIMARY KEY(student_slug, data))""",
    """CREATE TABLE IF NOT EXISTS przedszkole_plan (
        id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT,
        zajecia TEXT, prowadzacy TEXT)""",
    """CREATE TABLE IF NOT EXISTS przedszkole_jadlospis (
        id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, nazwa_posilku TEXT,
        sklad_json TEXT, alergeny_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS przedszkole_zebrania (
        id TEXT, student_slug TEXT, data TEXT, godzina TEXT, sala TEXT,
        temat TEXT, agenda TEXT, online TEXT,
        PRIMARY KEY(id, student_slug))""",
    """CREATE TABLE IF NOT EXISTS przedszkole_oplaty (
        platnosc_global_key TEXT, student_slug TEXT, numer_konta TEXT,
        tytul_platnosci TEXT, kwota_do_zaplaty REAL, aktywne INTEGER,
        status_platnosci INTEGER, kwota_upomnien REAL, kwota_odsetek REAL,
        kwota_umorzenia REAL, data TEXT,
        PRIMARY KEY(platnosc_global_key, student_slug))""",
    """CREATE TABLE IF NOT EXISTS przedszkole_informacje (
        student_slug TEXT PRIMARY KEY, nazwa TEXT, dyrektor TEXT,
        miejscowosc TEXT, ulica TEXT, nr_domu TEXT, nr_mieszkania TEXT,
        kod_pocztowy TEXT, tel_sluzbowy TEXT, tel_komorkowy TEXT,
        tel_domowy TEXT, mail TEXT, strona_www TEXT)""",
    """CREATE TABLE IF NOT EXISTS przedszkole_nauczyciele (
        student_slug TEXT, imie TEXT, nazwisko TEXT, przedmiot TEXT,
        wychowawca INTEGER, global_key_skrzynka TEXT)""",
    """CREATE TABLE IF NOT EXISTS przedszkole_wiadomosci (
        key TEXT PRIMARY KEY, student_slug TEXT, data TEXT,
        nadawca TEXT, temat TEXT, tresc TEXT, przeczytana INTEGER)"""
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
    _db_migrate(conn)


# Wersja schematu bazy. Przy zmianie tabel podnieś ją i dopisz krok w
# _db_migrate(); CREATE TABLE IF NOT EXISTS nie zmienia istniejących tabel.
_DB_SCHEMA_VERSION = 3

def _db_migrate(conn: sqlite3.Connection) -> None:
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
    except Exception as e:
        logger.error("Nie udało się odczytać wersji schematu bazy: %s", e)
        return

    if current >= _DB_SCHEMA_VERSION:
        return

    # ── v1: grades bez PRIMARY KEY (klucz gubił drugą ocenę w kolumnie, czyli poprawę) ──
    if current < 1:
        try:
            cols = conn.execute("PRAGMA index_list('grades')").fetchall()
            has_pk = any(row[3] == "pk" for row in cols) if cols else False
            if has_pk:
                logger.info("Migracja bazy: przebudowa tabeli grades (usunięcie ograniczającego klucza głównego)...")
                conn.execute("ALTER TABLE grades RENAME TO grades_old")
                conn.execute("""CREATE TABLE grades (
                    id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT,
                    data TEXT, opis TEXT, period_id TEXT)""")
                conn.execute("""INSERT INTO grades
                    SELECT id_kolumny, student_slug, przedmiot, ocena, data, opis, period_id
                    FROM grades_old""")
                conn.execute("DROP TABLE grades_old")
                conn.execute("""CREATE INDEX IF NOT EXISTS idx_grades_student_period
                    ON grades(student_slug, period_id)""")
                logger.info("Migracja bazy: tabela grades przebudowana.")
        except Exception as e:
            conn.rollback()
            logger.error("Migracja bazy (grades) nie powiodła się: %s", e)
            return

    # ── v2: usunięcie kolumny szczegoly_json z przedszkole_jadlospis ──
    # Wartości odżywcze (~20 pól na posiłek) przekraczały limit HA na atrybuty
    # encji (16384 B): HA po cichu odrzucał atrybuty i karta była pusta. Skład
    # i alergeny zostają (ważne dla dzieci z alergiami).
    if current < 2:
        try:
            cols = conn.execute("PRAGMA table_info('przedszkole_jadlospis')").fetchall()
            has_szczegoly = any(row[1] == "szczegoly_json" for row in cols) if cols else False
            if has_szczegoly:
                logger.info("Migracja bazy: usuwam kolumnę szczegoly_json z przedszkole_jadlospis...")
                conn.execute("ALTER TABLE przedszkole_jadlospis RENAME TO przedszkole_jadlospis_old")
                conn.execute("""CREATE TABLE przedszkole_jadlospis (
                    id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, nazwa_posilku TEXT,
                    sklad_json TEXT, alergeny_json TEXT)""")
                conn.execute("""INSERT INTO przedszkole_jadlospis
                    SELECT id, student_slug, data, nazwa_posilku, sklad_json, alergeny_json
                    FROM przedszkole_jadlospis_old""")
                conn.execute("DROP TABLE przedszkole_jadlospis_old")
                logger.info("Migracja bazy: tabela przedszkole_jadlospis przebudowana.")
        except Exception as e:
            conn.rollback()
            logger.error("Migracja bazy (przedszkole_jadlospis) nie powiodła się: %s", e)
            return

    # ── v3: godziny wejścia/wyjścia w przedszkole_obecnosc (endpoint EwidencjaObecnosci) ──
    # ADD COLUMN: dla istniejących wierszy nowe kolumny są NULL, dane zostają.
    if current < 3:
        try:
            cols = conn.execute("PRAGMA table_info('przedszkole_obecnosc')").fetchall()
            col_names = {row[1] for row in cols} if cols else set()
            if "godzina_wejscia" not in col_names:
                logger.info("Migracja bazy: dodaję kolumny godzin do przedszkole_obecnosc...")
                conn.execute("ALTER TABLE przedszkole_obecnosc ADD COLUMN godzina_wejscia TEXT")
                conn.execute("ALTER TABLE przedszkole_obecnosc ADD COLUMN godzina_wyjscia TEXT")
                logger.info("Migracja bazy: kolumny godzin dodane.")
        except Exception as e:
            conn.rollback()
            logger.error("Migracja bazy (przedszkole_obecnosc) nie powiodła się: %s", e)
            return

    try:
        conn.execute(f"PRAGMA user_version = {_DB_SCHEMA_VERSION}")
        conn.commit()
        logger.info("Schemat bazy w wersji %d.", _DB_SCHEMA_VERSION)
    except Exception as e:
        logger.error("Nie udało się zapisać wersji schematu bazy: %s", e)


# ────────────────────────────────────────────────
# LOVELACE SETUP
# ────────────────────────────────────────────────
def get_addon_version() -> str:
    """Odczytuje wersję z pliku config.yaml."""
    for p in ("config.yaml", "/app/config.yaml"):
        try:
            with open(p, encoding="utf-8") as f:
                m = re.search(r'version:\s*["\']?([^"\']+)["\']?', f.read())
                if m:
                    return m.group(1)
        except OSError:
            pass
    return "Nieznana"

def copy_resources() -> None:
    target = "/config/www/vultron"
    os.makedirs(target, exist_ok=True)
    src = "/app"
    n = 0
    if os.path.exists(src):
        for f in os.listdir(src):
            # Tylko karty dodatku (prefiks jak w run_setup_ui), nie każdy .js z /app.
            if f.startswith("vultron-") and f.lower().endswith(".js"):
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
            except Exception as e:
                logger.debug("HA API jeszcze niedostępne: %s", e)
            logger.info("Czekam na HA API…")
            await asyncio.sleep(5)

def run_setup_ui() -> None:
    log = logging.getLogger("UI-SETUP")
    version = get_addon_version()
    ws = None
    for attempt in range(10):
        try:
            ws = create_connection(HA_WS_URL, timeout=10)
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
            # Zamykamy gniazdo przed kolejną próbą, żeby go nie porzucać otwartego.
            if ws is not None:
                try:
                    ws.close()
                except Exception as close_err:
                    log.debug("Błąd zamykania WebSocket: %s", close_err)
                ws = None
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

_RE_DIARY_DOMAIN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_RE_DIARY_CITY   = re.compile(r"[A-Za-z0-9_-]+")
# Domeny znane ze zgłoszeń; inna domena nie jest blokowana (samorządy z własną
# domeną), tylko trafia do logu jako ostrzeżenie.
KNOWN_DIARY_DOMAINS = frozenset({"eduvulcan.pl", "edu.lublin.eu"})


def _session_cookies(cookies, domain: str) -> httpx.Cookies:
    """Ciasteczka sesji przypisane wyłącznie do *.{domain}. Przyjmuje słownik
    albo listę z Selenium.

    Zwykły słownik przekazany do httpx nie ma domeny i poszedłby do każdego
    hosta, także po przekierowaniu na obcą domenę. Ścieżka to zawsze "/":
    węższa odcięłaby ciasteczka od żądania .../{miasto} bez końcowego "/",
    które przełącza kontekst ucznia na serwerze.
    """
    jar = httpx.Cookies()
    pairs = cookies.items() if isinstance(cookies, dict) else ((c["name"], c["value"]) for c in (cookies or []))
    for name, value in pairs:
        jar.set(name, value, domain=f".{domain}", path="/")
    return jar


def _get_same_site(client: httpx.Client, url: str, domain: str, max_redirects: int = 5) -> httpx.Response:
    """GET, który przechodzi przekierowania tylko w obrębie https://*.{domain}.
    Przekierowanie gdzie indziej nie jest wykonywane: zwracamy 3xx, co
    wywołujący traktuje jak nieważną sesję."""
    response = client.get(url)
    for _ in range(max_redirects):
        if not response.is_redirect:
            break
        target = response.url.join(response.headers.get("location", ""))
        if target.scheme != "https" or not (target.host == domain or target.host.endswith("." + domain)):
            logger.warning("[SESJA] Pominięto przekierowanie poza https://*.%s: %s://%s", domain, target.scheme, target.host)
            break
        response = client.get(target)
    return response


def _diary_location(url: str) -> tuple[str, str] | None:
    """(domena, miasto) z adresu dziennika https://uczen.{domena}/{miasto}/...

    Host parsowany ściśle (tylko https, bez portu i danych logowania), bo domena
    trafia do adresów, na które wysyłamy ciasteczka sesji.
    """
    try:
        p = urlsplit(url or "")
        port = p.port
    except ValueError:
        return None
    host = p.hostname or ""
    if p.scheme != "https" or port not in (None, 443) or p.username or p.password \
            or not host.startswith("uczen."):
        return None
    domain = host[len("uczen."):]
    segments = p.path.split("/")
    city = segments[1] if len(segments) > 1 else ""
    if not _RE_DIARY_DOMAIN.fullmatch(domain) or not _RE_DIARY_CITY.fullmatch(city):
        return None
    return domain, city


# ReturnUrl zakodowany, z wiodącym "/", jak w linkach samej strony eduVULCAN
# (bez tego ASP.NET go ignoruje).
LOGIN_URL = "https://eduvulcan.pl/logowanie?ReturnUrl=%2Fdostep-do-dziennika%2F"
# Zapas, gdy po zalogowaniu nie ma kafelków (np. ReturnUrl zignorowany).
DIARY_ACCESS_URL = "https://eduvulcan.pl/dostep-do-dziennika/"
# Dodatkowy czas (s) na opuszczenie formularza po wysłaniu hasła: pasywne
# „Zabezpieczenie przed robotami" bywa na słabym sprzęcie wolne.
LOGIN_REJECTED_WAIT = 30
# Tyle odrzuconych logowań z rzędu (zeruje je tylko udane logowanie, nie błąd
# przejściowy jak 502) zatrzymuje dodatek. Jedno odrzucenie może być
# przypadkiem (wolny sprzęt, weryfikacja), dwa jeszcze nie grożą blokadą konta.
LOGIN_REJECTED_STOP_AFTER = 2


class LoginRejected(Exception):
    """Po wysłaniu danych przeglądarka nadal stoi na formularzu logowania (złe
    dane albo weryfikacja). Nie szukamy CAPTCHA po treści strony: napis
    „Zabezpieczenie przed robotami" jest na niej zawsze."""


def _on_login_form(driver) -> bool:
    """True, gdy przeglądarka jest na eduvulcan.pl/logowanie z widocznym polem loginu lub hasła."""
    try:
        p = urlsplit(driver.current_url)
        if p.hostname != "eduvulcan.pl" or not p.path.lower().startswith("/logowanie"):
            return False
        for field_id in ("Password", "UserName"):
            for el in driver.find_elements(By.ID, field_id):
                if el.is_displayed():
                    return True
    except Exception:
        return False
    return False


def run_diary_auth() -> tuple[list | None, list | None, list | None]:
    driver = None
    session = httpx.Client(timeout=15)

    try:
        # Chromium działa headless (_get_driver), więc serwer X nie jest potrzebny.
        _log_available_memory("przed Selenium")
        driver = _get_driver()
        try:
            wait = WebDriverWait(driver, 25)

            logger.info("[AUTH] Logowanie…")

            # Do 3 prób wczytania strony logowania. Dwa rodzaje niepowodzenia:
            #   - TimeoutException: chromedriver odpowiedział, strona po prostu
            #     się nie załadowała (zacięcie sieci lub Vulcana) - ponawiamy
            #     na tej samej przeglądarce.
            #   - inny wyjątek (np. ReadTimeoutError lokalnego połączenia z
            #     chromedriverem): proces przeglądarki się zawiesił (RPi z małą
            #     ilością RAM). Ponawianie na martwej instancji marnowałoby
            #     ~120 s na próbę, więc rezygnujemy od razu, a finally sprząta
            #     (_hard_kill_service).
            for attempt in range(3):
                try:
                    driver.get(LOGIN_URL)

                    # --- Baner cookies (iframe) ---
                    try:
                        # Baner pojawia się dopiero po chwili: iframe jest w DOM od
                        # razu, ale ukryty, dopóki JS strony go nie odkryje.
                        # Sprawdzenie is_displayed() zaraz po driver.get() pomijało go
                        # więc bezpowrotnie. Najpierw czekamy (do
                        # IFRAME_VISIBILITY_TIMEOUT s), aż którykolwiek iframe będzie
                        # widoczny, potem przechodzimy przez wszystkie.
                        IFRAME_VISIBILITY_TIMEOUT = 6

                        def _any_iframe_visible(d):
                            # is_displayed() może rzucić StaleElementReferenceException,
                            # którego WebDriverWait.until() nie łapie - bez try/except
                            # jeden niestabilny iframe przerwałby czekanie.
                            for fr in d.find_elements(By.TAG_NAME, "iframe"):
                                try:
                                    if fr.is_displayed():
                                        return True
                                except Exception as e:
                                    # Wołane co ~0,5 s przez WebDriverWait - tylko TRACE.
                                    logger.trace("[AUTH] iframe niedostępny (pomijam): %s", e)
                                    continue
                            return False

                        try:
                            WebDriverWait(driver, IFRAME_VISIBILITY_TIMEOUT).until(_any_iframe_visible)
                        except TimeoutException:
                            logger.info(
                                "[AUTH] Brak widocznego okna ciasteczek w ciągu %ds - idziemy dalej.",
                                IFRAME_VISIBILITY_TIMEOUT,
                            )

                        # Ramki zliczamy po czekaniu
                        all_iframes = driver.find_elements(By.TAG_NAME, "iframe")
                        logger.info("[AUTH] Znaleziono %d ramek (iframe) na stronie logowania.", len(all_iframes))

                        for i, fr in enumerate(all_iframes):
                            try:
                                fr_id = fr.get_attribute("id") or ""
                                fr_title = fr.get_attribute("title") or ""

                                try:
                                    displayed = fr.is_displayed()
                                except Exception:
                                    displayed = False

                                logger.info(
                                    "[AUTH] iframe[%d]: id=%r, title=%r, displayed=%s",
                                    i, fr_id, fr_title, displayed
                                )

                                # tylko widoczne
                                if not displayed:
                                    continue

                                logger.info("[AUTH] Przełączam się do widocznej ramki iframe[%d] (id=%r)...", i, fr_id)
                                driver.switch_to.frame(fr)

                                # Przycisk akceptacji (może go nie być w każdej ramce)
                                try:
                                    btn = WebDriverWait(driver, 3).until(
                                        EC.element_to_be_clickable((By.ID, "save-default-button"))
                                    )
                                    logger.info("[AUTH] Znaleziono przycisk akceptacji w iframe[%d]. Klikam...", i)
                                    driver.execute_script("arguments[0].click();", btn)
                                    logger.info("[AUTH] Sukces: Kliknięto przycisk akceptacji ciasteczek (JS) w iframe[%d].", i)
                                except TimeoutException:
                                    logger.debug("[AUTH] Brak przycisku 'save-default-button' w iframe[%d] – pomijam.", i)
                                except Exception as e:
                                    logger.warning("[AUTH] Błąd przy klikaniu przycisku w iframe[%d]: %s", i, e)

                            except Exception as e:
                                logger.warning("[AUTH] Błąd przy obsłudze iframe[%d]: %s", i, e)
                            finally:
                                # Zawsze wracamy do głównej zawartości
                                try:
                                    driver.switch_to.default_content()
                                except Exception as e:
                                    logger.debug("[AUTH] Nie udało się wrócić z iframe: %s", e)

                        time.sleep(1.0)  # krótka pauza po obsłudze wszystkich ramek

                    except Exception as e:
                        logger.warning("[AUTH] Błąd przy akceptacji ciasteczek: %s", e)
                        try:
                            driver.switch_to.default_content()
                        except Exception as e2:
                            logger.debug("[AUTH] Nie udało się wrócić z iframe: %s", e2)
                    # -------------------------------------

                    # Strona się załadowała - koniec prób
                    break

                except TimeoutException as e:
                    if attempt < 2:
                        logger.warning("[AUTH] Timeout strony lub ramki. Ponawiam próbę (%d/3)...", attempt + 2)
                        time.sleep(3)
                    else:
                        logger.error("[AUTH] Nie udało się wczytać strony logowania po 3 próbach (timeout ładowania).")
                        raise e
                except Exception as e:
                    logger.error(
                        "[AUTH] Błąd komunikacji z przeglądarką podczas wczytywania strony logowania: %s", e
                    )
                    raise

            def _save_error_screenshot() -> str:
                # /local/ jest dostępne bez logowania, więc nazwa jest losowa
                # (podana tylko w logu), a poprzednie zrzuty są usuwane.
                # Błąd zrzutu nie może podmienić wyjątku wołającego (np. LoginRejected).
                try:
                    err_dir = "/config/www/vultron"
                    os.makedirs(err_dir, exist_ok=True)
                    for old in os.listdir(err_dir):
                        if old.startswith("vultron_auth_error") and old.endswith(".png"):
                            try:
                                os.remove(os.path.join(err_dir, old))
                            except OSError as e:
                                logger.debug("[AUTH] Nie usunięto starego zrzutu %s: %s", old, e)
                    err_name = f"vultron_auth_error_{secrets.token_urlsafe(16)}.png"
                    err_path = os.path.join(err_dir, err_name)
                    if not driver.save_screenshot(err_path):
                        return "(nie udało się zapisać zrzutu)"
                    return f"{err_path} (w przeglądarce: /local/vultron/{err_name})"
                except Exception as e:
                    logger.debug("[AUTH] Zrzut ekranu nieudany: %s", e)
                    return "(nie udało się zapisać zrzutu)"

            # Wpisanie loginu (tylko jeśli formularz jest widoczny)
            credentials_sent = False
            if "UserName" in driver.page_source:
                wait.until(EC.presence_of_element_located((By.ID, "UserName"))).send_keys(
                    CONFIG.get("username", "") + Keys.ENTER
                )
                time.sleep(1.5)  # Pozostawione celowo na animację przejścia z loginu do hasła

                # Pole "Password" jest w DOM od razu, ale ukryte do przejścia z kroku
                # loginu; presence_of_element_located dawało
                # ElementNotInteractableException. Czekamy, aż będzie klikalne.
                try:
                    password_field = wait.until(EC.element_to_be_clickable((By.ID, "Password")))
                except TimeoutException:
                    err_path = _save_error_screenshot()
                    logger.error("[AUTH] Po wpisaniu loginu nie pojawiło się pole hasła. Zrzut ekranu: %s", err_path)
                    raise
                password_field.send_keys(CONFIG.get("password", "") + Keys.ENTER)
                credentials_sent = True

            # Oczekiwanie na kafelki Dziennika. Vulcan serwuje różnym użytkownikom
            # dwa różne szablony strony wyboru profilu (przez CDN), więc jeden
            # selektor nie wystarczy:
            #   - "nowa" (WordPress): <a class="panel-access__profile">
            #   - "stara" (ASP.NET):  <a class="connected-account access-row">
            #   - "historyczna":      <a title="Przejdź do Dziennika">
            # Warianty sprawdzamy po kolei z krótkim timeoutem, żeby użytkownik
            # starej wersji nie czekał całych 25 s na selektor, którego nie ma.
            _PROFILE_LINK_SELECTORS = (
                ("nowa (WordPress/panel-access)", By.XPATH, "//a[contains(concat(' ', normalize-space(@class), ' '), ' panel-access__profile ')]"),
                ("stara (ASP.NET/connected-account)", By.XPATH, "//a[contains(concat(' ', normalize-space(@class), ' '), ' connected-account ') and contains(concat(' ', normalize-space(@class), ' '), ' access-row ')]"),
                ("historyczna (title='Przejdź do Dziennika')", By.XPATH, "//a[@title='Przejdź do Dziennika']"),
            )

            def _find_diary_links() -> tuple[list[str], str, Exception | None]:
                last: Exception | None = None
                for version_label, by, selector in _PROFILE_LINK_SELECTORS:
                    try:
                        link_elements = WebDriverWait(driver, 10).until(
                            EC.presence_of_all_elements_located((by, selector))
                        )
                        return [el.get_attribute("href") for el in link_elements], version_label, None
                    except Exception as e:
                        last = e
                        logger.debug("[AUTH] Wariant strony '%s' nie pasuje: %s", version_label, e)
                return [], "NIEZNANA", last

            diary_links, detected_version, last_exc = _find_diary_links()

            # Nadal formularz po wysłaniu hasła: czekamy jeszcze chwilę, potem
            # uznajemy logowanie za odrzucone.
            if not diary_links and credentials_sent and _on_login_form(driver):
                try:
                    WebDriverWait(driver, LOGIN_REJECTED_WAIT).until(lambda d: not _on_login_form(d))
                except TimeoutException:
                    err_path = _save_error_screenshot()
                    logger.error("[AUTH] Logowanie odrzucone - po wysłaniu hasła nadal widać formularz "
                                 "logowania (złe dane albo weryfikacja). Zrzut ekranu: %s", err_path)
                    raise LoginRejected("Logowanie odrzucone przez eduVULCAN") from None
                diary_links, detected_version, last_exc = _find_diary_links()

            if not diary_links:
                logger.info("[AUTH] Brak kafelków po logowaniu - próbuję bezpośrednio %s", DIARY_ACCESS_URL)
                driver.get(DIARY_ACCESS_URL)
                diary_links, detected_version, last_exc = _find_diary_links()

            logger.info("[AUTH] Wykryta wersja strony wyboru profilu: %s", detected_version)

            if not diary_links:
                err_path = _save_error_screenshot()
                logger.error(
                    "[AUTH] Nie znaleziono kafelka 'Dziennik' - żaden ze znanych wariantów "
                    "strony (nowa/stara/historyczna) nie pasował. Zrzut ekranu zapisano w: %s",
                    err_path,
                )
                raise last_exc if last_exc is not None else RuntimeError("Nie znaleziono kafelka dziennika")

            logger.info("[AUTH] Znaleziono %d kafelek/kafelków dziennika (wersja strony: %s).",
                       len(diary_links), detected_version)

            students: list[dict] = []
            przedszkolaki: list[dict] = []
            seen_slugs: set = set()

            for link in diary_links:
                # href pochodzi ze strony Vulcana, więc przed nawigacją wymagamy
                # https (Selenium wykonałby "javascript:" jako kod).
                if urlsplit(link or "").scheme != "https":
                    logger.warning("[AUTH] Pomijam link o niedozwolonym schemacie: %r", link)
                    continue

                driver.get(link)

                # Czekamy na host "uczen.*"; domena po nim to "eduvulcan.pl" albo własna
                # domena samorządu (patrz _diary_location).
                try:
                    wait.until(EC.url_contains("uczen."))
                except Exception:
                    logger.debug("[AUTH] Długie ładowanie strony dziennika, aktualny URL: %s", driver.current_url)

                # Dziennik, który "nie rozpoczął jeszcze nauki lub jest już absolwentem"
                # (np. stary dostęp po przejściu do kolejnej szkoły), Vulcan kieruje na
                # ".../End/NieaktywnyUczen". Pomijamy tylko ten znany przypadek; resztę
                # przetwarzamy dalej, żeby nie gubić danych po cichu.
                if "/End/NieaktywnyUczen" in driver.current_url:
                    logger.info(
                        "[AUTH] Pominięto nieaktywny dostęp do dziennika (uczeń nie rozpoczął "
                        "nauki lub jest absolwentem) - URL: %s",
                        driver.current_url,
                    )
                    continue

                # Niektóre samorządy mają własną domenę (np. "uczen.edu.lublin.eu/lublin/...")
                # zamiast "uczen.eduvulcan.pl/{miasto}/...", więc domenę (po "uczen.")
                # i miasto (pierwszy segment ścieżki) wyciągamy osobno.
                location = _diary_location(driver.current_url)
                if not location:
                    logger.error("[AUTH] Brak nazwy domeny/miasta w URL: %s", driver.current_url)
                    continue
                domain, city = location
                if domain not in KNOWN_DIARY_DOMAINS:
                    logger.warning("[AUTH] Nieznana domena dziennika: %s - jeśli to Twój samorząd, zgłoś ją autorowi.", domain)

                driver.get(f"https://uczen.{domain}/{city}/api/Context")

                # Czekamy tylko na dokument (JSON)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                context_raw = driver.execute_script("return document.body.innerText")
                try:
                    context = json.loads(context_raw)
                except json.JSONDecodeError as e:
                    # Przerwa techniczna lub 5xx to błąd przejściowy, nie blokada:
                    # ponawiamy w następnym cyklu z backoffem.
                    logger.error("[AUTH] /api/Context nie zwróciło JSON (przerwa techniczna lub błąd serwera) "
                                 "- ponowię w następnym cyklu.")
                    logger.debug("[AUTH] Surowa odpowiedź: %s", context_raw[:500])
                    raise RuntimeError("Niepoprawna odpowiedź /api/Context") from e

                city_snapshot = {c["name"]: c["value"] for c in driver.get_cookies()}

                driver.get(f"https://wiadomosci.{domain}/{city}/App")

                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                wiadomosci_snapshot = {c["name"]: c["value"] for c in driver.get_cookies()}

                session.cookies.update(_session_cookies(city_snapshot, domain))

                for u in context.get("uczniowie", []):
                    key = u.get("key")
                    student_slug = slugify(u.get("uczen", ""))
                    if student_slug in seen_slugs:
                        logger.warning(
                            "[AUTH] Pomijam ucznia '%s' (slug=%s, key=%s) - identyczny slug już "
                            "zarejestrowany w tym cyklu. Jeśli to DWOJE RÓŻNYCH dzieci o takim "
                            "samym imieniu i nazwisku, drugie z nich zostanie całkowicie "
                            "pominięte (plan/oceny/frekwencja) - skontaktuj się z autorem dodatku.",
                            u.get("uczen", ""), student_slug, key,
                        )
                        continue
                    seen_slugs.add(student_slug)

                    id_dz = str(u.get("idDziennik"))

                    # Przedszkolaki (isPrzedszkolak w /api/Context) nie mają ocen ani
                    # okresów klasyfikacyjnych, więc pomijamy OkresyKlasyfikacyjne.
                    # Mają osobne listy, fetchery, tabele, sensory i karty.
                    if u.get("isPrzedszkolak"):
                        przedszkolaki.append({
                            "slug":              slugify(u.get("uczen", "")),
                            "uczen":             u.get("uczen") or "",
                            "city":              city,
                            "domain":            domain,
                            "key":               key,
                            "idDziennik":        id_dz,
                            "jednostka":         u.get("jednostka", ""),
                            "globalKeySkrzynka": u.get("globalKeySkrzynka", ""),
                            "city_cookies":      city_snapshot,
                            "wiadomosci_cookies": wiadomosci_snapshot,
                        })
                        logger.info("[AUTH] Przedszkolak: %s (%s @ %s)", u.get("uczen"), city, domain)
                        continue

                    res = session.get(
                        f"https://uczen.{domain}/{city}/api/OkresyKlasyfikacyjne",
                        params={"key": key, "idDziennik": id_dz}
                    )
                    if res.status_code != 200:
                        logger.warning("Brak okresów dla: %s", u.get("uczen"))
                        continue

                    okresy = res.json()
                    if not isinstance(okresy, list) or not okresy:
                        logger.warning("Nieoczekiwany format okresów dla: %s", u.get("uczen"))
                        continue
                    # Ostatni wpis może nie mieć "id" albo nie być słownikiem; ten
                    # fragment jest poza try, więc nie wolno rzucać wyjątku.
                    _last = okresy[-1]
                    curr_p = _last.get("id") if isinstance(_last, dict) else None
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
                        "slug":              slugify(u.get("uczen", "")),
                        "uczen":             u.get("uczen") or "",
                        "city":              city,
                        "domain":            domain,
                        "key":               key,
                        "idDziennik":        id_dz,
                        "periodId":          curr_p,
                        "klasa":             u.get("oddzial", ""),
                        "globalKeySkrzynka": u.get("globalKeySkrzynka", ""),
                        "city_cookies":      city_snapshot,
                        "wiadomosci_cookies": wiadomosci_snapshot,
                    })
                    logger.info("[AUTH] Uczeń: %s (%s @ %s)", u.get("uczen"), city, domain)

            cookies = driver.get_cookies()
            with open(VUL_PKL, "w", encoding="utf-8") as f:
                # "saved_at" (UTC) pozwala _try_reuse_cached_session ocenić wiek
                # cache; mtime pliku psuje np. przywracanie kopii zapasowej.
                json.dump({
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "cookies": cookies,
                    "students": students,
                    "przedszkolaki": przedszkolaki,
                }, f, ensure_ascii=False)

            logger.info("[AUTH] Szkoła uczniów: %d", len(students))
            logger.info("[AUTH] Przedszkolaków: %d", len(przedszkolaki))
            return students, przedszkolaki, cookies
        finally:
            # Przeglądarkę zamykamy od razu po pracy: zombie chrome/chromedriver
            # zjadają RAM i dają coraz częstsze timeouty ("Read timed out").
            try:
                driver.quit()
            except Exception as e:
                logger.debug("[AUTH] Zignorowano błąd przy zamykaniu przeglądarki: %s", e)
            _hard_kill_service(driver.service)
            driver = None  # zapobiega ponownej próbie quit() w zewnętrznym finally

    except LoginRejected:
        raise
    except Exception as e:
        logger.error("[AUTH] Błąd: %s", e, exc_info=True)
        return None, None, None
    finally:
        session.close()
        # Awaryjnie: driver jest zwykle już zamknięty (None); to na wypadek wyjątku
        # przed wewnętrznym try/finally.
        if driver is not None:
            try:
                driver.quit()
            except Exception as e:
                logger.debug("[AUTH] Zignorowano błąd przy zamykaniu przeglądarki: %s", e)
            _hard_kill_service(driver.service)


# ────────────────────────────────────────────────
# FETCH HELPERS – async sekcje danych
# ────────────────────────────────────────────────

def _grade_value(wpis) -> tuple[str, float] | None:
    """Wartość liczbowa oceny cząstkowej: ("pct", 47.0) dla ocen procentowych,
    ("num", 4.5) dla skali 1-6, None dla wpisów nieliczonych (np, nb, bz, litery).

    Oceny procentowe nie są mieszane ze skalą 1-6 (progi ustala szkoła).
    Formaty: "47 (%)", "47%", "47,5 (%)"; wartość spoza 0-100 jest odrzucana.
    """
    w_str = str(wpis).strip().upper()
    m_pct = re.fullmatch(r"(\d{1,3}(?:[.,]\d+)?)\s*\(?\s*%\s*\)?", w_str)
    if m_pct:
        pv = float(m_pct.group(1).replace(",", "."))
        return ("pct", pv) if 0.0 <= pv <= 100.0 else None
    # "1 (23%)": liczy się ocena 1-6, procent jest tylko informacją.
    w_str = re.sub(r"\s*\(\s*\d{1,3}(?:[.,]\d+)?\s*%\s*\)$", "", w_str)
    if re.search(r"[A-F%]|NB|NP|BZ", w_str):
        return None
    m_dec = re.search(r"(?<!\d)([1-6])(?:[.,](\d+))?(?!\d)", w_str)
    if not m_dec:
        return None
    v = float(m_dec.group(1))
    if m_dec.group(2):
        v += float("0." + m_dec.group(2))
    elif "+" in w_str:
        v += 0.5
    elif "-" in w_str:
        v -= 0.25
    return ("num", v)


def _subject_summary(p_item: dict, srednie_wlaczone: bool = True) -> dict:
    """Oceny cząstkowe przedmiotu do atrybutu sensora + średnie.

    Ocena z "idOcenaPoprawiona" została poprawiona: nie liczy się do średniej
    i jest doklejana jako "pop" do oceny obowiązującej z tej samej kolumny.
    Pole "w" zostaje oceną obowiązującą, bo automatyzacje wykrywają nowe oceny
    po w|d|i (zmiana "w" dawałaby fałszywe powiadomienia).

    Średnia (szkoła z włączonymi średnimi, ustawienia.isSredniaAndPunkty):
    pole "srednia" z API - jedyne zgodne z Vulcanem (poprawki, wagi, wartości
    +/- ustawione przez szkołę); 0.0 = brak ocen liczonych. Gdy pola brak -
    własna średnia ważona, bez ocen poprawionych.
    Szkoła z WYŁĄCZONYMI średnimi: API daje 0.0, a wagi są zwykle 0.00
    (nieustawiane, bo nieużywane) - liczymy zwykłą średnią bez wag, bez ocen
    poprawionych, jak dawniej. Brak ocen liczonych -> None.
    """
    oceny: list[dict] = []
    liczone: list[tuple] = []   # (wpis, waga) ocen obowiązujących
    for kol in (p_item.get("kolumnyOcenyCzastkowe") or []):
        desc = f"{kol.get('kategoriaKolumny') or ''}: {kol.get('nazwaKolumny') or ''}".strip(": ")
        obowiazujace: list[dict] = []
        poprawione: list[dict] = []
        for o in (kol.get("oceny") or []):
            v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
            wpis = {"w": v, "d": dt[:5], "i": clean_text(desc)}
            if o.get("idOcenaPoprawiona") is not None:
                poprawione.append(wpis)
            else:
                obowiazujace.append(wpis)
                liczone.append((v, o.get("waga")))
        if obowiazujace:
            if poprawione:
                # Oceny nie mają własnego id, więc parujemy po kolumnie (poprawa
                # jest w kolumnie oryginału). Kolejność jak w API.
                obowiazujace[0]["pop"] = [p["w"] for p in poprawione]
            oceny.extend(obowiazujace)
        else:
            # Teoretycznie niemożliwe: sama ocena poprawiona bez poprawy.
            for p in poprawione:
                p["poprawiona"] = True
            oceny.extend(poprawione)

    vals: list[float] = []
    wagi: list[float] = []
    pvals: list[float] = []
    for v, waga in liczone:
        gv = _grade_value(v)
        if gv is None:
            continue
        kind, x = gv
        if kind == "pct":
            pvals.append(x)
            continue
        try:
            w = 1.0 if waga is None or not srednie_wlaczone else float(waga)
        except (TypeError, ValueError):
            w = 1.0
        if w > 0:
            vals.append(x)
            wagi.append(w)

    api_avg = p_item.get("srednia")
    try:
        api_avg = float(str(api_avg).replace(",", ".")) if api_avg is not None else None
    except ValueError:
        api_avg = None

    srednia, zrodlo = None, None
    if srednie_wlaczone and api_avg is not None:
        if api_avg > 0:
            srednia, zrodlo = round(api_avg, 2), "vulcan"
    elif vals:
        srednia = round(sum(x * w for x, w in zip(vals, wagi)) / sum(wagi), 2)
        zrodlo = "wyliczona"

    return {
        "oceny":          oceny,
        "srednia":        srednia,
        "srednia_zrodlo": zrodlo,
        "srednia_proc":   round(sum(pvals) / len(pvals), 1) if pvals else None,
    }


# Limit listy w atrybucie sensora: HA po cichu odrzuca atrybuty > 16384 B
# (patrz migracja v2). Zapas na pozostałe pola (friendly_name, last_update...).
ATTR_LIST_MAX_BYTES = 14000


def _fit_attr_list(items: list) -> tuple[list, int]:
    """Przycina listę (posortowaną od najważniejszych) do ATTR_LIST_MAX_BYTES.
    Zwraca (zachowane, liczba_pominiętych); pominięte trafiają do atrybutu "pominiete"."""
    kept: list = []
    size = 2
    for item in items:
        item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 1
        if size + item_size > ATTR_LIST_MAX_BYTES:
            break
        kept.append(item)
        size += item_size
    return kept, len(items) - len(kept)


def _fit_messages(msgs: list) -> tuple[list, int]:
    """Wiadomości do atrybutu sensora w limicie ATTR_LIST_MAX_BYTES. Lista
    zostaje cała (automatyzacje porównują nagłówki: skrócenie listy dałoby
    fałszywe "nowe" wiadomości), a treść jest czyszczona od najstarszych.
    Zwraca (lista, liczba_wyczyszczonych_treści)."""
    def _size() -> int:
        return len(json.dumps(msgs, ensure_ascii=False).encode("utf-8"))
    skrocone = 0
    for m in reversed(msgs):  # lista od najnowszych
        if _size() <= ATTR_LIST_MAX_BYTES:
            break
        if m.get("tresc"):
            m["tresc"] = ""
            skrocone += 1
    return msgs, skrocone


# Zapytania _delete_missing jako stałe: SQLite nie przyjmuje nazw tabel i
# kolumn jako parametrów "?", a SQL sklejany w locie wymagałby wyłączenia
# skanerów (bandit B608 / ruff S608). Nowa para (tabela, kolumna id) = nowy wpis.
_DELETE_MISSING_SQL: dict[tuple[str, str], tuple[str, str]] = {
    ("free_days", "data"): (
        "SELECT data, data FROM free_days WHERE student_slug=?",
        "DELETE FROM free_days WHERE student_slug=? AND data=?",
    ),
    ("timetable", "id"): (
        "SELECT id, data FROM timetable WHERE student_slug=?",
        "DELETE FROM timetable WHERE student_slug=? AND id=?",
    ),
    ("remarks", "remark_id"): (
        "SELECT remark_id, data FROM remarks WHERE student_slug=?",
        "DELETE FROM remarks WHERE student_slug=? AND remark_id=?",
    ),
    ("meetings", "id"): (
        "SELECT id, data FROM meetings WHERE student_slug=?",
        "DELETE FROM meetings WHERE student_slug=? AND id=?",
    ),
    ("przedszkole_zebrania", "id"): (
        "SELECT id, data FROM przedszkole_zebrania WHERE student_slug=?",
        "DELETE FROM przedszkole_zebrania WHERE student_slug=? AND id=?",
    ),
}


def _delete_missing(cur, table: str, id_col: str, slug: str, keep_ids: set,
                    date_from: str | None = None, date_to: str | None = None) -> int:
    """Usuwa wiersze ucznia skasowane w dzienniku: nieobecne w odpowiedzi API, ale
    tylko w zakresie dat tej odpowiedzi (starsze wpisy zostają). Wiersz z datą
    nierozpoznaną zostaje (niepewność = nie kasuj).
    Para (table, id_col) musi być w _DELETE_MISSING_SQL (inaczej KeyError)."""
    select_sql, delete_sql = _DELETE_MISSING_SQL[(table, id_col)]
    stale = []
    for rid, raw in cur.execute(select_sql, (slug,)).fetchall():
        if rid in keep_ids:
            continue
        day = _normalize_date_prefix(raw)
        if day is None or (date_from and day < date_from) or (date_to and day > date_to):
            continue
        stale.append((slug, rid))
    if stale:
        cur.executemany(delete_sql, stale)
    return len(stale)


def _min_api_date(items, field: str) -> str | None:
    """Najwcześniejsza data w odpowiedzi API (dolna granica dla _delete_missing).
    None = brak dat, wtedy nic nie kasujemy."""
    days = [d for d in (_normalize_date_prefix(_txt(i.get(field))) for i in items if isinstance(i, dict)) if d]
    return min(days) if days else None


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
        try:
            p_id  = str(period["id"])
            p_num = period["numerOkresu"]
        except (KeyError, TypeError) as e:
            logger.warning("[%s] pominięto niepoprawny okres klasyfikacyjny: %s", name, e)
            continue

        res_g = await client.get(f"{base}/api/Oceny",
                                 params={"key": key, "idOkresKlasyfikacyjny": p_id})
        if res_g.status_code != 200:
            continue

        subjects: dict[str, dict] = {}       # przedmiot → _subject_summary()
        subj_periodic: dict[str, dict] = {}  # przedmiot → oceny okresowe
        new_g = 0

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()

                # Porównujemy pełne wpisy (kolumna, ocena, data): kolumna z poprawą
                # ma dwie oceny, więc samo porównanie kolumn zawyżałoby licznik nowych.
                cur.execute(
                    "SELECT id_kolumny, ocena, data FROM grades WHERE student_slug=? AND period_id=?",
                    (slug, p_id),
                )
                existing_entries = {(r[0], r[1], r[2]) for r in cur.fetchall()}

                rows_to_insert: list[tuple] = []
                oceny_json = res_g.json()
                # Brak klucza = traktujemy jak włączone (średnia z API wiążąca).
                srednie_wlaczone = (oceny_json.get("ustawienia") or {}).get("isSredniaAndPunkty") is not False
                for p_item in (oceny_json.get("ocenyPrzedmioty") or[]):
                    subj = p_item.get("przedmiotNazwa", "Inne")
                    # oceny okresowe i proponowane przedmiotu
                    subj_periodic[subj] = {
                        "proponowana": (p_item.get("proponowanaOcenaOkresowa") or "").strip() or None,
                        "okresowa":    (p_item.get("ocenaOkresowa") or "").strip() or None,
                    }
                    # przedmiot bez ocen cząstkowych (np. Zachowanie) też trafia na listę
                    subjects[subj] = _subject_summary(p_item, srednie_wlaczone)
                    for kol in (p_item.get("kolumnyOcenyCzastkowe") or[]):
                        id_k = str(kol.get("idKolumny", "0"))
                        desc = f"{kol.get('kategoriaKolumny') or ''}: {kol.get('nazwaKolumny') or ''}".strip(": ")
                        for o in (kol.get("oceny") or[]):
                            v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
                            if (id_k, v, dt) not in existing_entries:
                                new_g += 1
                            rows_to_insert.append((id_k, slug, subj, v, dt, desc, p_id))

                # Pełna podmiana ocen okresu (usuwa też wycofane). Tylko gdy API
                # zwróciło oceny; pusta lub uszkodzona odpowiedź nie kasuje historii.
                if rows_to_insert:
                    cur.execute(
                        "DELETE FROM grades WHERE student_slug=? AND period_id=?",
                        (slug, p_id),
                    )
                    cur.executemany(
                        "INSERT INTO grades VALUES (?,?,?,?,?,?,?)",
                        rows_to_insert,
                    )
                conn.commit()
            finally:
                conn.close()

        def _map_grade_to_num(raw):
            """Ocena słowna lub cyfrowa -> liczba; "cyfra/cyfra" daje mniejszą.
            None, gdy nie da się zmapować."""
            if not raw:
                return None
            s = raw.strip().lower()
            # słowne → cyfra
            _WORD_MAP = {
                "celujący": 6, "celująca": 6, "wzorowe": 6,
                "bardzo dobry": 5, "bardzo dobra": 5, "bardzo dobre": 5,
                "dobry": 4, "dobra": 4, "dobre": 4,
                "dostateczny": 3, "dostateczna": 3, "poprawne": 3,
                "mierny": 2, "mierna": 2, "nieodpowiednie": 2,
                "niedostateczny": 1, "niedostateczna": 1, "naganne": 1,
            }
            if s in _WORD_MAP:
                return float(_WORD_MAP[s])
            # format cyfra/cyfra → bierz mniejszą
            m_slash = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
            if m_slash:
                return float(min(int(m_slash.group(1)), int(m_slash.group(2))))
            # cyfra 1-6, opcjonalnie z częścią dziesiętną lub +/- (jak w _grade_value)
            m_dec = re.fullmatch(r"([1-6])(?:[.,](\d+))?([+-])?", s)
            if m_dec:
                v = float(m_dec.group(1))
                if m_dec.group(2):
                    v += float("0." + m_dec.group(2))
                elif m_dec.group(3) == "+":
                    v += 0.5
                elif m_dec.group(3) == "-":
                    v -= 0.25
                # "6+" i "1-" wychodziłyby poza skalę 1-6 - przycinamy.
                return min(6.0, max(1.0, v))
            return None

        lista =[]
        prop_vals: list[float] = []   # do średniej proponowanych ocen okresowych
        okr_vals:  list[float] = []   # do średniej końcowych ocen okresowych

        for subj_name, summary in subjects.items():
            periodic = subj_periodic.get(subj_name, {})

            # oceny końcowe (tylko gdy serwer je zwrócił)
            proponowana_raw = periodic.get("proponowana")
            okresowa_raw    = periodic.get("okresowa")
            proponowana_num = _map_grade_to_num(proponowana_raw)
            okresowa_num    = _map_grade_to_num(okresowa_raw)

            # Do średnich nie liczymy Zachowania
            if subj_name.strip().lower() != "zachowanie":
                if proponowana_num is not None:
                    prop_vals.append(proponowana_num)
                if okresowa_num is not None:
                    okr_vals.append(okresowa_num)

            lista.append({
                "przedmiot":        subj_name,
                "oceny":            summary["oceny"],
                "srednia":          summary["srednia"],
                "srednia_zrodlo":   summary["srednia_zrodlo"],
                "srednia_proc":     summary["srednia_proc"],
                "proponowana":      proponowana_raw,
                "proponowana_num":  proponowana_num,
                "okresowa":         okresowa_raw,
                "okresowa_num":     okresowa_num,
            })

        srednia_proponowanych = round(sum(prop_vals)/len(prop_vals), 3) if prop_vals else None
        srednia_okresowych    = round(sum(okr_vals) /len(okr_vals),  3) if okr_vals  else None

        await publish_sensor(ha, f"sensor.vultron_oceny_{slug}_p{p_num}", new_g,
                             f"Oceny: {name} (P{p_num})",
                             {"lista_przedmiotow": lista, "period_number": int(p_num),
                              "student_slug": slug,
                              "active_period": p_id == str(s["periodId"]),
                              "srednia_proponowanych": srednia_proponowanych,
                              "srednia_okresowych":    srednia_okresowych})


async def _fetch_schedule(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                          base: str, s: dict, ambiguous_first_names: set[str] | None = None) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram plan lekcji...", name)
    now = datetime.now()

    # Zakres obsługiwany przez kartę: poprzedni, obecny i następny tydzień
    # (ten sam dla własnych zajęć z kalendarza HA).
    _range_od = now - timedelta(days=now.weekday() + 7)
    _range_do = now + timedelta(days=21)

    res_plan, res_free, res_cal = await asyncio.gather(
        client.get(f"{base}/api/PlanZajec", params={
            "key": key,
            "dataOd": _range_od.strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": _range_do.strftime("%Y-%m-%dT23:59:59.999Z"),
            "zakresDanych": "2",
        }),
        client.get(f"{base}/api/DniWolne", params={
            "key": key,
            "dataOd": _range_od.strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": _range_do.strftime("%Y-%m-%dT23:59:59.999Z"),
        }),
        # Kalendarz HA jest opcjonalny; jego błędy (np. 404) nie mogą przerywać
        # pobierania planu z Vulcana. API kalendarza wymaga czasu RFC3339, więc
        # konwertujemy lokalny czas na UTC z 'Z'.
        ha.get(f"{HA_URL}/calendars/{CALENDAR_ENTITY}", params={
            "start": _range_od.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end":   _range_do.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        }, timeout=10),
        return_exceptions=True
    )

    if isinstance(res_plan, Exception):
        logger.warning("[%s] błąd planu (wyjątek): %s", name, res_plan)
        return
    if res_plan.status_code != 200:
        logger.warning("[%s] błąd planu: %d", name, res_plan.status_code)
        return

    _lessons = res_plan.json()
    if not isinstance(_lessons, list):
        logger.warning("[%s] Nieoczekiwany format planu zajęć", name)
        return

    student_jednostki = set()
    for lesson in _lessons:
        jid = lesson.get("idJednostkaSkladowa")
        if jid is not None:
            student_jednostki.add(jid)

    _free_days = []
    free_fetch_ok = False
    if not isinstance(res_free, Exception) and res_free.status_code == 200:
        _free_days_raw = res_free.json()
        if isinstance(_free_days_raw, list):
            _free_days = _free_days_raw
            free_fetch_ok = True
    elif isinstance(res_free, Exception):
        logger.warning("[%s] błąd dni wolnych (wyjątek): %s", name, res_free)
    else:
        logger.warning("[%s] błąd dni wolnych: %d", name, res_free.status_code)

    # ────────────────────────────────────────────────
    # WŁASNE ZAJĘCIA (kalendarz HA), opcjonalne. Tytuł wydarzenia: "{Imię}: Nazwa zajęć".
    # Bierzemy tylko wydarzenia z imieniem tego ucznia i dwukropkiem (reszta może
    # należeć do rodzeństwa albo być domowa). MAX_WLASNE_ZAJEC chroni przed rozrostem
    # encji (np. przy omyłkowym wydarzeniu cyklicznym co minutę).
    # ────────────────────────────────────────────────
    MAX_WLASNE_ZAJEC = 200
    cal_entries: list[tuple] = []
    _cal_raw_count = 0
    cal_fetch_ok = False  # True tylko po udanym pobraniu kalendarza (patrz DELETE niżej)

    if isinstance(res_cal, Exception):
        logger.warning("--> [%s] kalendarz %s niedostępny (wyjątek): %s", name, CALENDAR_ENTITY, res_cal)
    elif res_cal.status_code == 404:
        logger.warning("--> [%s] encja kalendarza %s nie istnieje w Home Assistant - pomijam własne zajęcia.",
                       name, CALENDAR_ENTITY)
    elif res_cal.status_code != 200:
        logger.warning("--> [%s] błąd kalendarza %s: HTTP %d | %s", name, CALENDAR_ENTITY,
                       res_cal.status_code, res_cal.text[:200])
    else:
        try:
            cal_events = res_cal.json()
        except Exception as e:
            cal_events = []
            logger.warning("--> [%s] błąd parsowania JSON kalendarza: %s", name, e)

        if isinstance(cal_events, list):
            cal_fetch_ok = True
            _cal_raw_count = len(cal_events)
            # Surowe wydarzenia do logu debug, żeby było widać, co zwraca API HA.
            for _raw_ev in cal_events:
                try:
                    logger.debug("--> [%s] RAW wydarzenie z kalendarza: %s",
                               name, json.dumps(_raw_ev, ensure_ascii=False)[:500])
                except Exception:
                    logger.debug("--> [%s] RAW wydarzenie z kalendarza (nie-JSON): %r", name, _raw_ev)

            first_name = (name or "").strip().split(" ")[0] if name else ""
            if not first_name:
                logger.warning("[%s] brak imienia ucznia - nie można dopasować własnych zajęć.", name)
            else:
                # Gdy imię powtarza się u innego ucznia (np. rodzeństwo), wymagamy pełnego
                # "Imię Nazwisko:", żeby wydarzenie nie trafiło do dwojga dzieci. W
                # pozostałych przypadkach wystarczy "Jan:" albo "Jan Kowalski:".
                is_ambiguous = bool(ambiguous_first_names) and _fold_pl(first_name) in ambiguous_first_names
                full_name = name.strip()
                accepted_variants = {full_name} if is_ambiguous else {first_name, full_name}
                # Dopasowanie na wersji bez polskich znaków i wielkości liter
                # ("kowalski" = "Kowalski"). _fold_pl zachowuje indeksy, więc temat
                # wycinamy z oryginalnego tytułu.
                folded_variants = sorted({_fold_pl(v) for v in accepted_variants if v}, key=len, reverse=True)
                alt = "|".join(re.escape(v) for v in folded_variants)
                prefix_pattern = re.compile(rf"^\s*(?:{alt})\s*:\s*")
                for ev in cal_events:
                    if len(cal_entries) >= MAX_WLASNE_ZAJEC:
                        logger.warning("[%s] osiągnięto limit %d własnych zajęć - kolejne pomijam.",
                                       name, MAX_WLASNE_ZAJEC)
                        break
                    try:
                        summary_raw = ev.get("summary") or ""
                        summary = summary_raw.replace("\uFF1A", ":")  # pełnoszerokie ":" -> zwykłe
                        pm = prefix_pattern.match(_fold_pl(summary))
                        if not pm:
                            logger.debug(
                                "[%s] Wydarzenie nie pasuje do wzorca. Tytuł (repr): %r | Oczekiwane prefiksy: %r",
                                name, summary_raw, [f"{v}:" for v in accepted_variants],
                            )
                            continue  # wydarzenie nie dotyczy tego ucznia

                        start_dt = _parse_cal_dt(ev.get("start"))
                        end_dt   = _parse_cal_dt(ev.get("end"))
                        if not start_dt or not end_dt:
                            logger.warning(
                                "[%s] Wydarzenie '%s' dopasowane, ale pominięte - brak/zły format daty "
                                "(start=%r, end=%r). Prawdopodobnie wydarzenie całodniowe.",
                                name, summary, ev.get("start"), ev.get("end"),
                            )
                            continue  # wydarzenie całodniowe lub niepoprawne dane - pomijamy

                        subject = clean_text(summary[pm.end():].strip(), 100) or "Zajęcia"
                        sala    = clean_text(ev.get("location") or "", 50)
                        notatka = clean_text(ev.get("description") or "", 300)
                        # Wydarzenia cykliczne mają wspólny "uid" serii; do klucza dokładamy
                        # "recurrence_id" (lub start), inaczej INSERT OR REPLACE zostawiłby
                        # tylko jedno wystąpienie.
                        occurrence_key = ev.get("recurrence_id") or ev.get("start")
                        uid_raw = f"{ev.get('uid') or summary}|{occurrence_key}"
                        uid     = hashlib.sha256(uid_raw.encode()).hexdigest()[:16]

                        cal_entries.append((
                            f"cal_{slug}_{uid}", slug,
                            start_dt.strftime("%Y-%m-%d"),
                            f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}",
                            subject, sala, notatka, STATUS_WLASNE,
                        ))
                    except Exception as e:
                        logger.warning("[%s] błąd parsowania wydarzenia kalendarza: %s", name, e)
        elif cal_events:
            logger.warning("[%s] nieoczekiwany format odpowiedzi kalendarza", name)

    # Podsumowanie na poziomie INFO, żeby dało się diagnozować bez trybu debug.
    logger.info("--> [%s] Kalendarz %s: pobrano %d wydarzeń, dopasowano %d.",
               name, CALENDAR_ENTITY, _cal_raw_count, len(cal_entries))

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            lessons_to_insert: list[tuple] = []
            for lesson in _lessons:
                st  = MAPA_STATUSOW.get(_safe_int(lesson.get("adnotacja")), "")
                inf = " ".join((c.get("informacjeNieobecnosc") or "").lower() for c in (lesson.get("zmiany") or[]))
                # ODWOL tylko, gdy adnotacja nie dała już dokładniejszego statusu
                # (ZAST/PRZEN/NIEOB).
                if not st and ("zwolnieni" in inf or "okienko" in inf):
                    st = "ODWOL"
                data_raw   = lesson.get("data", "")
                godz_od    = lesson.get("godzinaOd", "T00:00")
                godz_do    = lesson.get("godzinaDo", "T00:00")
                # Domyślna wartość chroni tylko przed brakiem klucza; wartość bez "T"
                # dałaby IndexError w split("T")[1], a null (None) TypeError, który
                # przerwałby cały plan - więc pomijamy tylko taką lekcję.
                if (not isinstance(godz_od, str) or not isinstance(godz_do, str)
                        or "T" not in godz_od or "T" not in godz_do):
                    logger.warning(
                        "[%s] pominięto lekcję - nieoczekiwany format godzin (od=%r, do=%r)",
                        name, godz_od, godz_do,
                    )
                    continue
                lessons_to_insert.append(
                    (
                        # W kluczu są też przedmiot i prowadzący: przy zamianach API zwraca
                        # dwie lekcje z tą samą godziną startu, a jedna by nadpisała drugą.
                        f"{slug}_{data_raw}_{godz_od}_{lesson.get('przedmiot') or ''}_{lesson.get('prowadzacy') or ''}",
                        slug,
                        data_raw.split("T")[0],
                        f"{godz_od.split('T')[1][:5]}-{godz_do.split('T')[1][:5]}",
                        lesson.get("przedmiot") or "Zajęcia",
                        lesson.get("sala") or "", lesson.get("prowadzacy") or "", st,
                    )
                )
            if lessons_to_insert:
                # Pełna resynchronizacja lekcji z Vulcana w oknie dat: odwołana lekcja
                # znika, a przesunięta nie robi duplikatu. Własne zajęcia (WLASNE)
                # zostają, mają osobną resynchronizację niżej. Przy pustej odpowiedzi
                # API stary plan zostaje.
                cur.execute(
                    "DELETE FROM schedule WHERE student_slug=? AND status IS NOT ? "
                    "AND data BETWEEN ? AND ?",
                    (slug, STATUS_WLASNE,
                     _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d")),
                )
                cur.executemany(
                    "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                    lessons_to_insert,
                )

            # Resynchronizacja własnych zajęć w oknie dat, tylko po udanym pobraniu
            # kalendarza (cal_fetch_ok): przejściowy błąd nie może skasować wpisów.
            if cal_fetch_ok:
                cur.execute(
                    "DELETE FROM schedule WHERE student_slug=? AND status=? AND data BETWEEN ? AND ?",
                    (slug, STATUS_WLASNE, _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d")),
                )
                if cal_entries:
                    cur.executemany(
                        "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                        cal_entries,
                    )

            free_to_insert: list[tuple] = []
            for fd in _free_days:
                wszystkie = fd.get("wszystkieSkladowe", False)
                jednostki = fd.get("jednostkiSkladowe") or []  # null = brak jednostek
                valid = wszystkie
                if not valid:
                    for j in jednostki:
                        if j.get("id") in student_jednostki:
                            valid = True
                            break
                if valid:
                    # `or ""` zamiast domyślnej wartości: klucz z null dałby TypeError.
                    dt_od = (fd.get("dataOd") or "")[:10]
                    dt_do = (fd.get("dataDo") or "")[:10]
                    if dt_od and dt_do:
                        try:
                            curr_d = datetime.strptime(dt_od, "%Y-%m-%d")
                            end_d = datetime.strptime(dt_do, "%Y-%m-%d")
                            nazwa = fd.get("nazwa") or ""
                            while curr_d <= end_d:
                                free_to_insert.append(
                                    (slug, curr_d.strftime("%Y-%m-%d"), nazwa)
                                )
                                curr_d += timedelta(days=1)
                        except ValueError:
                            pass
            if free_to_insert:
                cur.executemany(
                    "INSERT OR REPLACE INTO free_days VALUES (?,?,?)",
                    free_to_insert,
                )
            # Odwołany dzień wolny znika; pusta lista jest normalna, więc wystarczy
            # udane pobranie.
            if free_fetch_ok:
                _delete_missing(cur, "free_days", "data", slug, {r[1] for r in free_to_insert},
                                _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d"))

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

                cur.execute(
                    "SELECT data,nazwa FROM free_days WHERE student_slug=? AND data BETWEEN ? AND ? ORDER BY data",
                    (slug, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")),
                )
                proc_fd = [{"d": r[0], "n": r[1]} for r in cur.fetchall()]

                today = now.strftime("%Y-%m-%d")
                state = len([entry for entry in proc if entry["d"] == today]) if suf == "curr" else len(proc)
                tasks.append(publish_sensor(ha, f"sensor.vultron_plan_{slug}_{suf}", state,
                                            f"Plan {suf}: {name}", {"lekcje": proc, "dni_wolne": proc_fd}))
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

    # _detail robi zapytanie HTTP, więc działa poza db_lock; zwraca wiersz, a zapis
    # idzie jednym executemany pod krótkim lockiem.
    async def _detail(item: dict) -> tuple | None:
        item_id = item.get("id")
        if not item_id:
            return None
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

        return (str(item_id), slug, data,
                przedmiot,
                MAPA_TYP_TERMINARZA.get(item.get("typ"), "Inne"),
                czysty_opis,
                autor)

    detail_results = await asyncio.gather(
        *[_detail(i) for i in items], return_exceptions=True
    )
    rows_to_insert: list[tuple] = []
    for r in detail_results:
        if isinstance(r, Exception):
            logger.warning("[%s] błąd pozycji terminarza: %s", name, r)
            continue
        if r is not None:
            rows_to_insert.append(r)

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            if rows_to_insert:
                cur.executemany(
                    "INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
                    rows_to_insert,
                )
            # Odwołany sprawdzian/zadanie znika. Id bierzemy z listy z API (nie ze
            # szczegółów), żeby błąd szczegółów nie kasował wpisu. Pusta lista = bez kasowania.
            if isinstance(items, list) and items:
                _delete_missing(cur, "timetable", "id", slug,
                                {str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")},
                                last_day_prev_month.strftime("%Y-%m-%d"),
                                (now + timedelta(days=61)).strftime("%Y-%m-%d"))
            conn.commit()

            cur.execute(
                "SELECT data,przedmiot,typ,opis,autor FROM timetable "
                "WHERE student_slug=? AND data>=? ORDER BY data",
                (slug, now.strftime("%Y-%m-%d")),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

    lista, pominiete = _fit_attr_list([{"data": _date_or_empty(r[0]), "przedmiot": r[1],
                                        "typ": r[2], "opis": r[3], "autor": r[4]} for r in rows])
    attrs = {"lista": lista}
    if pominiete:
        attrs["pominiete"] = pominiete
    await publish_sensor(ha, f"sensor.vultron_terminarz_{slug}", len(rows),
                         f"Terminarz: {name}", attrs)

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
            remarks_to_insert: list[tuple] = []
            remarks_api = res.json()
            if not isinstance(remarks_api, list):
                remarks_api = []
            for item in remarks_api:
                item_id = item.get("id")
                if not item_id:
                    continue
                # Treść uwagi czyścimy z HTML (druga warstwa obok escape'owania w karcie).
                tr    = clean_html(item.get("tresc") or "")
                typ_u = ("pozytywna" if "pochwa" in tr.lower()
                         else "negatywna" if "uwaga" in tr.lower()
                         else "informacja")
                remarks_to_insert.append(
                    (str(item_id), slug, _date_or_empty(item.get("data")),
                     tr, item.get("autor") or "", item.get("kategoria") or "",
                     str(item.get("liczbaPunktow") or ""), typ_u)
                )
            if remarks_to_insert:
                cur.executemany(
                    "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                    remarks_to_insert,
                )
                # Uwaga usunięta w dzienniku znika (w zakresie dat odpowiedzi).
                _delete_missing(cur, "remarks", "remark_id", slug, {r[0] for r in remarks_to_insert},
                                _min_api_date(remarks_api, "data"))
            conn.commit()

            cur.execute(
                "SELECT data,tresc,autor,kategoria,punkty,typ,remark_id FROM remarks "
                "WHERE student_slug=? ORDER BY data DESC", (slug,)
            )
            lista = [{"data": r[0], "tresc": r[1], "autor": r[2], "kategoria": r[3],
                      "punkty": r[4], "typ": r[5], "id": r[6]} for r in cur.fetchall()]
        finally:
            conn.close()

    uwagi, pominiete = _fit_attr_list(lista)
    attrs = {"uwagi": uwagi}
    if pominiete:
        attrs["pominiete"] = pominiete
    await publish_sensor(ha, f"sensor.vultron_uwagi_{slug}", len(lista),
                         f"Uwagi: {name}", attrs)


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
        # Błąd jednego z trzech zapytań nie przerywa pozostałych.
        return_exceptions=True,
    )

    def _http_ok(res) -> bool:
        return not isinstance(res, Exception) and res.status_code == 200

    def _http_err(res):
        return res if isinstance(res, Exception) else res.status_code

    przedmioty =[]
    if _http_ok(res_p):
        try:
            przedmioty = res_p.json()
        except Exception:
            przedmioty = []
    else:
        logger.warning("[%s] błąd pobierania przedmiotów: %s", name, _http_err(res_p))
    if not isinstance(przedmioty, list):
        przedmioty = []

    per_subject_list =[p for p in przedmioty if isinstance(p, dict) and p.get("id", -1) != -1]
    per_subject_results = await asyncio.gather(
        *[client.get(f"{base}/api/FrekwencjaStatystyki", params={"key": key, "idPrzedmiot": p["id"]})
          for p in per_subject_list],
        return_exceptions=True,
    )

    def _parse_rows(fsd: dict) -> list:
        # `or []` i sprawdzanie długości, bo "okresy" może być null albo krótsze niż 2.
        out = []
        for row in (fsd.get("statystyki") or []):
            try:
                okresy = row.get("okresy") or []
                out.append({
                    "k": MAPA_FREKWENCJI.get(row.get("kategoriaFrekwencji"), "Inna"),
                    "m": {str(m.get("miesiac")): m.get("wartosc")
                          for m in (row.get("miesiace") or []) if m.get("miesiac") is not None},
                    "s1": okresy[0] if len(okresy) > 0 else 0,
                    "s2": okresy[1] if len(okresy) > 1 else 0,
                    "r": row.get("razem", 0),
                })
            except Exception as e:
                logger.warning("[%s] pominięto niepoprawny wiersz statystyk: %s", name, e)
        return out

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

            if _http_ok(res_f):
                recs = res_f.json()
                if isinstance(recs, dict):
                    recs = recs.get("oddzialy") or[]
                freq_to_insert: list[tuple] = []
                for fi in recs:
                    fi_data  = fi.get("data", "")
                    fi_godz  = fi.get("godzinaOd", "")
                    if fi_data and fi_godz:
                        # Godzina bez "T" (np. "08:00") dałaby IndexError w split("T")[1];
                        # pomijamy tylko ten wpis.
                        if "T" not in fi_godz:
                            logger.warning(
                                "[%s] pominięto wpis frekwencji - nieoczekiwany format godziny: %r",
                                name, fi_godz,
                            )
                            continue
                        freq_to_insert.append(
                            (f"{slug}_{fi_data}_{fi_godz}", slug,
                             fi_data.split("T")[0], fi_godz.split("T")[1][:5],
                             _safe_int(fi.get("kategoriaFrekwencji")))
                        )
                if freq_to_insert:
                    cur.executemany(
                        "INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                        freq_to_insert,
                    )
                conn.commit()
                since = (now - timedelta(14)).strftime("%Y-%m-%d")
                cur.execute("SELECT data,godzina,kategoria FROM frequency "
                            "WHERE student_slug=? AND data>=? ORDER BY data DESC", (slug, since))
                freq_wpisy = [{"d": r[0], "t": r[1], "k": int(r[2])} for r in cur.fetchall()]
                freq_ok = True
            else:
                logger.warning("[%s] błąd frekwencji: %s", name, _http_err(res_f))

            if _http_ok(res_fs):
                fsd_all = res_fs.json()
                rows_all = _parse_rows(fsd_all)
                pct_all  = fsd_all.get("podsumowanie", 0)

                stats_to_insert: list[tuple] = [
                    (f"{slug}_-1_{today}", slug, today, -1, "Wszystkie",
                     pct_all, json.dumps(rows_all, ensure_ascii=False))
                ]

                index_subjects = [{"id": -1, "nazwa": "Wszystkie"}]
                for p in per_subject_list:
                    try:
                        index_subjects.append({"id": p["id"], "nazwa": p["nazwa"]})
                    except (KeyError, TypeError) as e:
                        logger.warning("[%s] pominięto niepoprawny przedmiot w statystykach frekwencji: %s", name, e)
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
                        stats_to_insert.append(
                            (f"{slug}_{p['id']}_{today}", slug, today,
                             p["id"], p["nazwa"], pct_p,
                             json.dumps(rows_p, ensure_ascii=False))
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

                if stats_to_insert:
                    cur.executemany(
                        "INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?,?,?,?,?)",
                        stats_to_insert,
                    )

                conn.commit()
            else:
                logger.warning("[%s] błąd statystyk: %s", name, _http_err(res_fs))
        finally:
            conn.close()

    if freq_ok:
        # Stan: liczba nieobecności nieusprawiedliwionych (kategoria 2) w oknie.
        nieobecnosci = sum(1 for w in freq_wpisy if w.get("k") == 2)
        await publish_sensor(ha, f"sensor.vultron_freq_{slug}", nieobecnosci,
                             f"Frekwencja: {name}",
                             {"wpisy": freq_wpisy,
                              "unit_of_measurement": "nieob."})

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
    try:
        ach_raw = res.json()
    except Exception as e:
        logger.warning("[%s] błąd parsowania JSON osiągnięć: %s", name, e)
        return
    if not isinstance(ach_raw, list):
        logger.warning("[%s] Nieoczekiwany format osiągnięć (nie lista)", name)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            ach_to_insert: list[tuple] = []
            for item in ach_raw:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if not item_id:
                    continue
                # Pusty string zamiast NULL: karta woła item.tresc.split('\n').
                ach_to_insert.append((str(item_id), slug, item.get("tresc") or ""))
            if ach_to_insert:
                cur.executemany("INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                                ach_to_insert)
            conn.commit()

            cur.execute("SELECT achievement_id,tresc FROM achievements WHERE student_slug=? ORDER BY rowid DESC", (slug,))
            rows = cur.fetchall()
        finally:
            conn.close()

    osiagniecia, pominiete = _fit_attr_list([{"id": r[0], "tresc": r[1]} for r in rows])
    attrs = {"osiagniecia": osiagniecia}
    if pominiete:
        attrs["pominiete"] = pominiete
    await publish_sensor(ha, f"sensor.vultron_osiagniecia_{slug}", len(rows),
                         f"Osiągnięcia: {name}", attrs)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – plan zajęć
# Jak _fetch_schedule (ten sam endpoint api/PlanZajec i pola), ale z własną
# tabelą i sensorem. Bez dni wolnych i kalendarza HA, bo przedszkole ich nie używa.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_plan(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                  base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram plan zajęć (przedszkole)...", name)
    now = datetime.now()

    # Zakres jak w _fetch_schedule; weekendy API zwraca puste.
    _range_od = now - timedelta(days=now.weekday() + 7)
    _range_do = now + timedelta(days=21)

    res = await client.get(f"{base}/api/PlanZajec", params={
        "key": key,
        "dataOd": _range_od.strftime("%Y-%m-%dT00:00:00.000Z"),
        "dataDo": _range_do.strftime("%Y-%m-%dT23:59:59.999Z"),
        "zakresDanych": "2",
    })
    if res.status_code != 200:
        logger.warning("[%s] błąd planu zajęć (przedszkole): %d", name, res.status_code)
        return

    try:
        zajecia_raw = res.json()
    except Exception as e:
        logger.warning("[%s] błąd parsowania JSON planu zajęć (przedszkole): %s", name, e)
        return

    if not isinstance(zajecia_raw, list):
        logger.warning("[%s] Nieoczekiwany format planu zajęć (przedszkole)", name)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            zajecia_to_insert: list[tuple] = []
            for z in zajecia_raw:
                data_raw = z.get("data", "")
                godz_od  = z.get("godzinaOd", "T00:00")
                godz_do  = z.get("godzinaDo", "T00:00")
                # Jak w _fetch_schedule: wartość bez "T" albo null pomijamy.
                if (not isinstance(godz_od, str) or not isinstance(godz_do, str)
                        or "T" not in godz_od or "T" not in godz_do):
                    logger.warning(
                        "[%s] pominięto zajęcia (przedszkole) - nieoczekiwany format godzin "
                        "(od=%r, do=%r)", name, godz_od, godz_do,
                    )
                    continue
                zajecia_to_insert.append((
                    # W kluczu przedmiot i prowadzący (jak w _fetch_schedule).
                    f"{slug}_{data_raw}_{godz_od}_{z.get('przedmiot') or ''}_{z.get('prowadzacy') or ''}",
                    slug,
                    data_raw.split("T")[0],
                    f"{godz_od.split('T')[1][:5]}-{godz_do.split('T')[1][:5]}",
                    z.get("przedmiot") or "Zajęcia",
                    z.get("prowadzacy") or "",
                ))

            if zajecia_to_insert:
                # Pełna resynchronizacja w oknie dat (jak w _fetch_schedule), żeby
                # odwołane i przesunięte zajęcia nie zostawały w karcie.
                cur.execute(
                    "DELETE FROM przedszkole_plan WHERE student_slug=? AND data BETWEEN ? AND ?",
                    (slug, _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d")),
                )
                cur.executemany(
                    "INSERT OR REPLACE INTO przedszkole_plan VALUES (?,?,?,?,?,?)",
                    zajecia_to_insert,
                )
                conn.commit()

            monday = now - timedelta(days=now.weekday())
            weeks = {
                "prev": (monday - timedelta(7), monday - timedelta(1)),
                "curr": (monday,                monday + timedelta(6)),
                "next": (monday + timedelta(7), monday + timedelta(13)),
            }
            tasks = []
            for suf, (sd, ed) in weeks.items():
                cur.execute(
                    "SELECT data, godzina, zajecia, prowadzacy FROM przedszkole_plan "
                    "WHERE student_slug=? AND data BETWEEN ? AND ? ORDER BY data, godzina",
                    (slug, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")),
                )
                proc = [{"d": r[0], "g": r[1], "z": r[2], "n": r[3]} for r in cur.fetchall()]

                today = now.strftime("%Y-%m-%d")
                state = len([entry for entry in proc if entry["d"] == today]) if suf == "curr" else len(proc)
                tasks.append(publish_sensor(ha, f"sensor.vultron_przedszkole_plan_{slug}_{suf}", state,
                                            f"Plan przedszkola {suf}: {name}", {"zajecia": proc}))
        finally:
            conn.close()
    await asyncio.gather(*tasks)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – jadłospis (endpoint api/Jadlospis)
#
# Odpowiedź zawiera posiłki wszystkich diet placówki danego dnia (np. "dzieci
# przedszkole" i "dzieci szkoła"), więc bierzemy tylko wpisy, w których "dieta"
# zawiera "przedszkol" (bez względu na wielkość liter, bo placówki nazywają
# diety różnie). Zakres to dziś i jutro, żeby encja nie rosła.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_jadlospis(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                       base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram jadłospis (przedszkole)...", name)
    now = datetime.now()

    _range_od = now
    _range_do = now + timedelta(days=1)

    res = await client.get(f"{base}/api/Jadlospis", params={
        "key": key,
        "dataOd": _range_od.strftime("%Y-%m-%dT00:00:00.000Z"),
        "dataDo": _range_do.strftime("%Y-%m-%dT23:59:59.999Z"),
    })
    if res.status_code != 200:
        logger.warning("[%s] błąd jadłospisu: %d", name, res.status_code)
        return

    try:
        dni_raw = res.json()
    except Exception as e:
        logger.warning("[%s] błąd parsowania JSON jadłospisu: %s", name, e)
        return

    if not isinstance(dni_raw, list):
        logger.warning("[%s] Nieoczekiwany format jadłospisu (nie lista)", name)
        return

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            posilki_to_insert: list[tuple] = []
            diety_pominiete: set[str] = set()
            for dzien in dni_raw:
                dieta = (dzien.get("dieta") or "").strip().lower()
                if "przedszkol" not in dieta:
                    diety_pominiete.add(dieta or "(brak nazwy)")
                    continue  # dieta innej grupy (np. "dzieci szkoła") - pomijamy

                data_raw = dzien.get("data") or ""
                if not data_raw:
                    continue
                data_dzien = data_raw.split("T")[0]
                dzien_id = dzien.get("id")

                for p_idx, posilek in enumerate(dzien.get("posilki") or []):
                    posilki_to_insert.append((
                        f"{slug}_{dzien_id}_{p_idx}",
                        slug,
                        data_dzien,
                        posilek.get("nazwa") or "Posiłek",
                        json.dumps(posilek.get("sklad") or [], ensure_ascii=False),
                        json.dumps(posilek.get("alergeny") or [], ensure_ascii=False),
                    ))

            if diety_pominiete and not posilki_to_insert:
                # Karta zostanie pusta - podajemy nazwy diet, żeby było widać dlaczego.
                logger.info(
                    "[%s] jadłospis: żadna dieta nie zawiera \"przedszkol\" (są: %s)",
                    name, ", ".join(sorted(diety_pominiete)),
                )

            if posilki_to_insert:
                # Pełna resynchronizacja w oknie dat, żeby zmieniony jadłospis nie zostawał.
                cur.execute(
                    "DELETE FROM przedszkole_jadlospis WHERE student_slug=? AND data BETWEEN ? AND ?",
                    (slug, _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d")),
                )
                cur.executemany(
                    "INSERT OR REPLACE INTO przedszkole_jadlospis VALUES (?,?,?,?,?,?)",
                    posilki_to_insert,
                )
                conn.commit()

            # Wartości odżywcze ("szczegoly") nie są zapisywane (limit atrybutów HA,
            # patrz migracja v2); skład i alergeny zostają.
            cur.execute(
                "SELECT data, nazwa_posilku, sklad_json, alergeny_json "
                "FROM przedszkole_jadlospis WHERE student_slug=? AND data BETWEEN ? AND ? "
                "ORDER BY data, id",
                (slug, _range_od.strftime("%Y-%m-%d"), _range_do.strftime("%Y-%m-%d")),
            )
            dni_wynik: dict[str, list] = {}
            for r in cur.fetchall():
                dni_wynik.setdefault(r[0], []).append({
                    "nazwa":     r[1],
                    "sklad":     json.loads(r[2]),
                    "alergeny":  json.loads(r[3]),
                })
        finally:
            conn.close()

    today_str = now.strftime("%Y-%m-%d")
    liczba_posilkow_dzis = len(dni_wynik.get(today_str, []))

    await publish_sensor(ha, f"sensor.vultron_przedszkole_jadlospis_{slug}", liczba_posilkow_dzis,
                         f"Jadłospis (przedszkole): {name}",
                         {"dni": dni_wynik, "icon": "mdi:food-apple"})


# ────────────────────────────────────────────────
# PRZEDSZKOLE – zebrania
# Ten sam endpoint (api/Zebrania) co dla szkoły, ale z osobnymi polami "temat"
# i "agenda" zamiast "opis", więc ma własną tabelę i sensor. Pole
# "obecniNaZebraniu" (obecność rodzica) jest pomijane.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_zebrania(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                      base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram zebrania z rodzicami (przedszkole)...", name)

    try:
        res = await client.get(f"{base}/api/Zebrania", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd zebrań (przedszkole): %d", name, res.status_code)
            return

        try:
            _zebrania = res.json()
        except Exception as e:
            logger.warning("[%s] błąd parsowania JSON zebrań (przedszkole): %s", name, e)
            return

        if not isinstance(_zebrania, list):
            logger.warning("[%s] Nieoczekiwany format zebrań (przedszkole, nie lista)", name)
            return

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                meetings_to_insert: list[tuple] = []
                for item in _zebrania:
                    item_id_raw = item.get("id")
                    if item_id_raw is None or str(item_id_raw) == "":
                        continue
                    item_id = str(item_id_raw)

                    dt_raw = item.get("dataCzas") or ""
                    data_str = dt_raw.split("T")[0] if "T" in dt_raw else dt_raw
                    godz_str = dt_raw.split("T")[1][:5] if "T" in dt_raw else ""

                    sala   = item.get("sala") or ""
                    temat  = item.get("temat") or ""
                    agenda = item.get("agenda") or ""
                    online_raw = item.get("zebranieOnline")
                    online = (
                        str(online_raw)
                        if online_raw and not isinstance(online_raw, str)
                        else (online_raw or "")
                    )

                    meetings_to_insert.append(
                        (item_id, slug, data_str, godz_str, sala, temat, agenda, online)
                    )

                if meetings_to_insert:
                    cur.executemany(
                        "INSERT OR REPLACE INTO przedszkole_zebrania VALUES (?,?,?,?,?,?,?,?)",
                        meetings_to_insert,
                    )
                    # Odwołane zebranie znika (w zakresie dat odpowiedzi).
                    _delete_missing(cur, "przedszkole_zebrania", "id", slug, {r[0] for r in meetings_to_insert},
                                    _min_api_date(_zebrania, "dataCzas"))

                conn.commit()

                cur.execute(
                    "SELECT data, godzina, sala, temat, agenda, online, id "
                    "FROM przedszkole_zebrania WHERE student_slug=? ORDER BY data DESC, godzina DESC",
                    (slug,),
                )
                lista = [
                    {
                        "data": r[0], "godzina": r[1], "sala": r[2],
                        "temat": r[3], "agenda": r[4], "online": r[5], "id": r[6],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

        now_date = datetime.now().strftime("%Y-%m-%d")
        nadchodzace = sum(1 for r in lista if r["data"] >= now_date)

        await publish_sensor(
            ha,
            f"sensor.vultron_przedszkole_zebrania_{slug}",
            nadchodzace,
            f"Zebrania (przedszkole): {name}",
            _zebrania_attrs(lista),
        )

    except httpx.RequestError as e:
        logger.warning("[%s] błąd sieci przy pobieraniu zebrań (przedszkole): %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd zebrań (przedszkole): %s", name, e)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – opłaty
#
# BEZPIECZEŃSTWO (świadoma decyzja): "numer_konta" i "tytul_platnosci" (zawiera
# imię i nazwisko dziecka) trafiają tylko do lokalnej bazy SQLite, nie do
# atrybutów sensora. Atrybuty idą do recordera HA, asystentów głosowych i
# zewnętrznych usług (Grafana/InfluxDB), co jest zbyt ryzykowne dla numeru konta.
# Kwota do zapłaty i status są mniej wrażliwe i trafiają do sensora.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_oplaty(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                    base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram opłaty (przedszkole)...", name)

    try:
        res = await client.get(f"{base}/api/Oplaty", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd opłat (przedszkole): %d", name, res.status_code)
            return

        try:
            _oplaty_raw = res.json()
        except Exception as e:
            logger.warning("[%s] błąd parsowania JSON opłat (przedszkole): %s", name, e)
            return

        if not isinstance(_oplaty_raw, dict):
            logger.warning("[%s] Nieoczekiwany format opłat (przedszkole, nie słownik)", name)
            return

        konta = _oplaty_raw.get("kontaBankowe") or []
        if not isinstance(konta, list):
            konta = []

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                konta_to_insert: list[tuple] = []
                for k in konta:
                    p_key = k.get("platnoscGlobalKey")
                    if not p_key:
                        continue

                    info = k.get("informacje") or {}
                    szczegoly = info.get("szczegoly") or []
                    # "szczegoly" to lista {"nazwa", "wartosc"} bez gwarantowanej
                    # kolejności, więc dopasowujemy po nazwie.
                    def _znajdz(nazwa_szukana: str) -> float:
                        for sz in szczegoly:
                            if sz.get("nazwa") == nazwa_szukana:
                                try:
                                    return float(sz.get("wartosc") or 0)
                                except (TypeError, ValueError):
                                    return 0.0
                        return 0.0

                    data_sync_raw = info.get("dataSynchronizacji") or ""
                    data_sync = data_sync_raw.split("T")[0] if "T" in data_sync_raw else data_sync_raw

                    konta_to_insert.append((
                        p_key, slug,
                        k.get("numerKonta") or "",
                        k.get("tytulPlatnosci") or "",
                        float(k.get("kwotaDoZaplaty") or 0),
                        1 if k.get("aktywne") else 0,
                        int(k.get("statusPlatnosci") or 0),
                        _znajdz("Kwota kosztów upomnień"),
                        _znajdz("Kwota odsetek"),
                        _znajdz("Kwota umorzenia"),
                        data_sync,
                    ))

                if konta_to_insert:
                    cur.executemany(
                        "INSERT OR REPLACE INTO przedszkole_oplaty VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        konta_to_insert,
                    )
                    conn.commit()

                # Tylko pola bezpieczne dla atrybutów sensora (bez numer_konta i
                # tytul_platnosci, patrz uwaga wyżej).
                cur.execute(
                    "SELECT kwota_do_zaplaty, aktywne, status_platnosci, "
                    "kwota_upomnien, kwota_odsetek, kwota_umorzenia, data "
                    "FROM przedszkole_oplaty WHERE student_slug=?",
                    (slug,),
                )
                konta_bezpieczne = [
                    {
                        "kwota_do_zaplaty": r[0], "aktywne": bool(r[1]),
                        "status_platnosci": r[2], "kwota_upomnien": r[3],
                        "kwota_odsetek": r[4], "kwota_umorzenia": r[5],
                        "data_synchronizacji": r[6],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

        suma_do_zaplaty = sum(k["kwota_do_zaplaty"] for k in konta_bezpieczne if k["aktywne"])

        await publish_sensor(
            ha,
            f"sensor.vultron_przedszkole_oplaty_{slug}",
            round(suma_do_zaplaty, 2),
            f"Opłaty (przedszkole): {name}",
            {"konta": konta_bezpieczne, "icon": "mdi:cash"},
        )

    except httpx.RequestError as e:
        logger.warning("[%s] błąd sieci przy pobieraniu opłat (przedszkole): %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd opłat (przedszkole): %s", name, e)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – informacje o placówce
# Dane statyczne (adres, dyrektor, kontakt): tabela bez kolumny "data" i poza
# _PRUNE_SQL (jak "achievements"), bo retencja czasowa nie ma tu sensu.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_informacje(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                        base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram informacje o placówce (przedszkole)...", name)

    try:
        res = await client.get(f"{base}/api/Informacje", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd informacji o placówce: %d", name, res.status_code)
            return

        try:
            info = res.json()
        except Exception as e:
            logger.warning("[%s] błąd parsowania JSON informacji o placówce: %s", name, e)
            return

        if not isinstance(info, dict):
            logger.warning("[%s] Nieoczekiwany format informacji o placówce (nie słownik)", name)
            return

        row = (
            slug,
            info.get("nazwa") or "",
            info.get("dyrektor") or "",
            info.get("miejscowosc") or "",
            info.get("ulica") or "",
            info.get("nrDomu") or "",
            info.get("nrMieszkania") or "",
            info.get("kodPocztowy") or "",
            info.get("telSluzbowy") or "",
            info.get("telKomorkowy") or "",
            info.get("telDomowy") or "",
            info.get("mail") or "",
            info.get("stronaWwwUrl") or "",
        )

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO przedszkole_informacje VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
                conn.commit()
            finally:
                conn.close()

        adres = f"{row[4]} {row[5]}".strip()
        if row[6]:
            adres += f"/{row[6]}"
        if row[3]:
            adres = f"{adres}, {row[7]} {row[3]}".strip(", ")

        await publish_sensor(
            ha,
            f"sensor.vultron_przedszkole_informacje_{slug}",
            row[1] or "Brak nazwy",
            f"Informacje o placówce (przedszkole): {name}",
            {
                "dyrektor": row[2], "adres": adres, "miejscowosc": row[3],
                "tel_sluzbowy": row[8], "tel_komorkowy": row[9], "tel_domowy": row[10],
                "mail": row[11], "strona_www": row[12], "icon": "mdi:school",
            },
        )

    except httpx.RequestError as e:
        logger.warning("[%s] błąd sieci przy pobieraniu informacji o placówce: %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd informacji o placówce: %s", name, e)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – nauczyciele
# Lista aktualna: DELETE+INSERT w każdym cyklu, żeby nauczyciel, który już nie
# uczy, zniknął. Bez deduplikacji po globalKeySkrzynka: jeden wiersz na każde
# przypisanie przedmiot+nauczyciel (ta sama osoba może być wychowawcą i uczyć
# religii), to jest zamierzone.
# ────────────────────────────────────────────────

async def _fetch_przedszkole_nauczyciele(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                         base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram nauczycieli (przedszkole)...", name)

    try:
        res = await client.get(f"{base}/api/Nauczyciele", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd nauczycieli (przedszkole): %d", name, res.status_code)
            return

        try:
            data = res.json()
        except Exception as e:
            logger.warning("[%s] błąd parsowania JSON nauczycieli (przedszkole): %s", name, e)
            return

        if not isinstance(data, dict):
            logger.warning("[%s] Nieoczekiwany format nauczycieli (przedszkole, nie słownik)", name)
            return

        nauczyciele_raw = data.get("nauczyciele") or []
        if not isinstance(nauczyciele_raw, list):
            nauczyciele_raw = []

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM przedszkole_nauczyciele WHERE student_slug=?", (slug,))

                rows = []
                for n in nauczyciele_raw:
                    rows.append((
                        slug,
                        n.get("imie") or "",
                        n.get("nazwisko") or "",
                        n.get("przedmiot") or "",
                        1 if n.get("wychowawca") else 0,
                        n.get("globalKeySkrzynka") or "",
                    ))

                if rows:
                    cur.executemany(
                        "INSERT INTO przedszkole_nauczyciele VALUES (?,?,?,?,?,?)",
                        rows,
                    )
                conn.commit()

                cur.execute(
                    "SELECT imie, nazwisko, przedmiot, wychowawca, global_key_skrzynka "
                    "FROM przedszkole_nauczyciele WHERE student_slug=? "
                    "ORDER BY wychowawca DESC, nazwisko, imie",
                    (slug,),
                )
                lista = [
                    {
                        "imie": r[0], "nazwisko": r[1],
                        "przedmiot": r[2] or "—", "wychowawca": bool(r[3]),
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

        liczba_wychowawcow = sum(1 for n in lista if n["wychowawca"])

        await publish_sensor(
            ha,
            f"sensor.vultron_przedszkole_nauczyciele_{slug}",
            liczba_wychowawcow,
            f"Nauczyciele (przedszkole): {name}",
            {"nauczyciele": lista, "icon": "mdi:account-tie"},
        )

    except httpx.RequestError as e:
        logger.warning("[%s] błąd sieci przy pobieraniu nauczycieli (przedszkole): %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd nauczycieli (przedszkole): %s", name, e)


# ────────────────────────────────────────────────
# PRZEDSZKOLE – wiadomości
#
# Jedna sesja wiadomosci.* obsługuje wiele skrzynek przez parametr
# "globalKeySkrzynka" endpointu "OdebraneSkrzynka" (jak dla uczniów szkół).
# "api/Odebrane" bez parametru zwraca skrzynkę wybraną przez sesję, a przy koncie
# z uczniem szkoły i przedszkolakiem jest to skrzynka ucznia. Dlatego pytamy
# o skrzynkę przedszkolaka jawnie, a "api/Odebrane" zostaje tylko na wypadek braku
# klucza. Osobna funkcja (jedna sesja = jedna skrzynka dziecka) zamiast pętli
# z run_messages_sync.
#
# Funkcja jest synchroniczna: używa _build_wiadomosci_session (httpx.Client) i
# jest wołana przez asyncio.to_thread z sync_przedszkole_data.
# ────────────────────────────────────────────────
def _fetch_przedszkole_wiadomosci(s: dict) -> None:
    slug, name = s["slug"], s["uczen"]
    domain = s.get("domain") or "eduvulcan.pl"
    city = s["city"]
    logger.info("--> [%s] Pobieram wiadomości (przedszkole)...", name)

    session = _build_wiadomosci_session(domain, city, s.get("wiadomosci_cookies") or {})
    if session is None:
        logger.warning("[%s] brak sesji wiadomości (przedszkole)", name)
        return

    gk = s.get("globalKeySkrzynka") or ""
    if gk:
        inbox_url = f"https://wiadomosci.{domain}/{city}/api/OdebraneSkrzynka"
        inbox_params = {"globalKeySkrzynka": gk, "idLastWiadomosc": 0, "pageSize": 50}
    else:
        logger.debug("[%s] brak globalKeySkrzynka (przedszkole) - używam api/Odebrane", name)
        inbox_url = f"https://wiadomosci.{domain}/{city}/api/Odebrane"
        inbox_params = {"idLastWiadomosc": 0, "pageSize": 50}

    conn = None
    try:
        try:
            res = session.get(inbox_url, params=inbox_params)
        except Exception as e:
            logger.warning("[%s] błąd sieciowy wiadomości (przedszkole): %s", name, e)
            return

        if res.status_code != 200:
            logger.warning("[%s] błąd wiadomości (przedszkole): HTTP %d", name, res.status_code)
            return

        try:
            messages = res.json()
        except Exception as e:
            logger.warning("[%s] błąd JSON wiadomości (przedszkole): %s", name, e)
            return

        if not isinstance(messages, list):
            logger.warning("[%s] Nieoczekiwany format wiadomości (przedszkole, nie lista)", name)
            return

        # Jak w run_messages_sync: sieć nigdy pod db_lock_thread (blokowałby inne
        # wątki i checkpointing WAL). Krótki lock na odczyt, sieć bez locka, krótki
        # lock na zapis.

        # ETAP 1 (krótki lock): klucze już zapisane w bazie
        with db_lock_thread:
            conn = db_connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT key FROM przedszkole_wiadomosci WHERE student_slug=?",
                    (slug,),
                )
                existing_keys = {row[0] for row in cur.fetchall()}
            finally:
                conn.close()
                conn = None

        # ETAP 2 (bez locka): treść nowych wiadomości z sieci
        rows_to_insert: list[tuple] = []
        read_updates: list[tuple] = []

        for m in messages:
            if not isinstance(m, dict):
                continue
            m_k = m.get("apiGlobalKey")
            if not m_k:
                continue
            read_flag = 1 if m.get("przeczytana") else 0

            if m_k in existing_keys:
                read_updates.append((read_flag, m_k))
                continue

            # Treść tylko dla nowych wiadomości; znane dostają samą aktualizację
            # statusu przeczytania.
            try:
                det = session.get(
                    f"https://wiadomosci.{domain}/{city}"
                    f"/api/WiadomoscSzczegoly",
                    params={"apiGlobalKey": m_k},
                )
            except Exception as e:
                logger.warning("[%s] błąd pobierania treści wiadomości (przedszkole): %s", name, e)
                continue

            if det.status_code == 200:
                try:
                    tresc = (det.json() or {}).get("tresc") or "Brak"
                except Exception:
                    tresc = "Brak"
                rows_to_insert.append((
                    m_k, slug,
                    _txt(m.get("data")),
                    _txt(m.get("korespondenci")),
                    _txt(m.get("temat")),
                    _txt(tresc),
                    read_flag,
                ))

        # ETAP 3 (krótki lock): zapis i odczyt danych do sensora
        with db_lock_thread:
            conn = db_connect()
            try:
                cur = conn.cursor()
                try:
                    if read_updates:
                        cur.executemany(
                            "UPDATE przedszkole_wiadomosci SET przeczytana=? WHERE key=?",
                            read_updates,
                        )
                    if rows_to_insert:
                        cur.executemany(
                            "INSERT OR REPLACE INTO przedszkole_wiadomosci VALUES (?,?,?,?,?,?,?)",
                            rows_to_insert,
                        )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error("[%s] rollback wiadomości (przedszkole): %s", name, e, exc_info=True)
                    return

                cur.execute(
                    "SELECT data,nadawca,temat,tresc,przeczytana FROM przedszkole_wiadomosci "
                    "WHERE student_slug=? ORDER BY data DESC LIMIT 10",
                    (slug,),
                )
                rows = cur.fetchall()
                unread = cur.execute(
                    "SELECT COUNT(*) FROM przedszkole_wiadomosci WHERE student_slug=? AND przeczytana=0",
                    (slug,),
                ).fetchone()[0]
                total = cur.execute(
                    "SELECT COUNT(*) FROM przedszkole_wiadomosci WHERE student_slug=?",
                    (slug,),
                ).fetchone()[0]
            finally:
                conn.close()
                conn = None

        # Treść (może być duża) publikujemy tylko dla nieprzeczytanych, oczyszczoną
        # i do 2000 znaków, żeby nie przekroczyć limitu atrybutów HA (16384 B).
        msgs = []
        for r in rows:
            is_unread = int(r[4]) == 0
            body = ""
            if is_unread:
                body = clean_html(r[3])
                if len(body) > 2000:
                    body = body[:1997] + "..."
            msgs.append({
                "data":        (r[0] or "").replace("T", " ")[:16],
                "nadawca":     r[1] or "",
                "temat":       r[2] or "",
                "tresc":       body,
                "przeczytana": not is_unread,
            })
        # 10 długich nieprzeczytanych treści przekracza limit HA (16384 B).
        msgs, skrocone = _fit_messages(msgs)

        publish_sensor_sync(
            f"sensor.vultron_przedszkole_wiadomosci_{slug}",
            unread,
            f"Wiadomości (przedszkole): {name}",
            {"wiadomosci": msgs, "stats": f"{unread} / {total}",
             **({"skrocone": skrocone} if skrocone else {})},
        )

    finally:
        session.close()
        if conn is not None:
            conn.close()
# ────────────────────────────────────────────────
# PRZEDSZKOLE – ewidencja obecności
# Niezależna od _fetch_frequency (inny endpoint i model danych, własna tabela
# i sensor).
#
# "EwidencjaObecnosciTablica" daje jawną flagę "dzień -> obecnosc: true/false"
# (widok "Tablica" z aplikacji mobilnej), a "EwidencjaObecnosci" tylko godziny
# wejścia/wyjścia, bez flagi dla dni nieobecnych. Tablica nie przyjmuje zakresu
# dat (tylko "key") i zwraca krótki, stały okres wstecz (bieżący miesiąc).
# ────────────────────────────────────────────────

async def _fetch_przedszkole_obecnosc(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                                      base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram ewidencję obecności (przedszkole)...", name)
    now = datetime.now()

    # Dwa endpointy naraz: Tablica (flaga obecności) i EwidencjaObecnosci (godziny)
    # dla bieżącego miesiąca kalendarzowego, jak w podglądzie miesięcznym aplikacji Vulcan.
    miesiac_od = now.replace(day=1)
    if now.month == 12:
        miesiac_do = now.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        miesiac_do = now.replace(month=now.month + 1, day=1) - timedelta(days=1)

    res_tablica, res_godziny = await asyncio.gather(
        client.get(f"{base}/api/EwidencjaObecnosciTablica", params={"key": key}),
        client.get(f"{base}/api/EwidencjaObecnosci", params={
            "key": key,
            "dataOd": miesiac_od.strftime("%Y-%m-%dT00:00:00.000Z"),
            "dataDo": miesiac_do.strftime("%Y-%m-%dT23:59:59.999Z"),
        }),
        return_exceptions=True,
    )

    if isinstance(res_tablica, Exception) or res_tablica.status_code != 200:
        logger.warning("[%s] błąd ewidencji obecności (Tablica): %s", name,
                       res_tablica if isinstance(res_tablica, Exception) else res_tablica.status_code)
        return

    try:
        wpisy_raw = res_tablica.json()
    except Exception as e:
        logger.warning("[%s] błąd parsowania JSON ewidencji obecności: %s", name, e)
        return

    if not isinstance(wpisy_raw, list):
        logger.warning("[%s] Nieoczekiwany format ewidencji obecności (nie lista)", name)
        return

    # Godziny są dodatkiem: gdy ten endpoint zawiedzie, działamy bez nich.
    godziny_by_data: dict[str, tuple[str, str]] = {}
    if not isinstance(res_godziny, Exception) and res_godziny.status_code == 200:
        try:
            godziny_raw = res_godziny.json()
            if isinstance(godziny_raw, list):
                for g in godziny_raw:
                    data_raw = g.get("data") or ""
                    if not data_raw:
                        continue
                    godz_od = g.get("godzinaOd") or ""
                    godz_do = g.get("godzinaDo") or ""
                    wej = godz_od.split("T")[1][:5] if "T" in godz_od else ""
                    wyj = godz_do.split("T")[1][:5] if "T" in godz_do else ""
                    godziny_by_data[data_raw.split("T")[0]] = (wej, wyj)
        except Exception as e:
            logger.debug("[%s] błąd parsowania godzin ewidencji obecności: %s", name, e)
    else:
        logger.debug(
            "[%s] błąd ewidencji obecności (godziny): %s", name,
            res_godziny if isinstance(res_godziny, Exception) else res_godziny.status_code,
        )

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            wpisy_to_insert: list[tuple] = []
            for w in wpisy_raw:
                data_raw = w.get("data") or ""
                if not data_raw:
                    continue
                data_dzien = data_raw.split("T")[0]
                # "obecnosc" to jawny bool z API; zapisujemy jako 1/0.
                obecny = 1 if w.get("obecnosc") else 0
                wej, wyj = godziny_by_data.get(data_dzien, ("", ""))
                wpisy_to_insert.append((slug, data_dzien, obecny, wej, wyj))

            if wpisy_to_insert:
                cur.executemany(
                    "INSERT OR REPLACE INTO przedszkole_obecnosc VALUES (?,?,?,?,?)",
                    wpisy_to_insert,
                )
                conn.commit()

            # Odczyt z bazy, nie z odpowiedzi API, żeby pokazać dłuższą historię.
            cur.execute(
                "SELECT data, obecnosc FROM przedszkole_obecnosc "
                "WHERE student_slug=? ORDER BY data DESC LIMIT 31",
                (slug,),
            )
            historia = [{"data": r[0], "obecnosc": bool(r[1])} for r in cur.fetchall()]

            # Bieżący miesiąc z godzinami, do zakładki "Godziny" w karcie
            # (dni nieobecności nie mają godzin).
            cur.execute(
                "SELECT data, obecnosc, godzina_wejscia, godzina_wyjscia "
                "FROM przedszkole_obecnosc WHERE student_slug=? AND data BETWEEN ? AND ? "
                "ORDER BY data",
                (slug, miesiac_od.strftime("%Y-%m-%d"), miesiac_do.strftime("%Y-%m-%d")),
            )
            miesiac_wpisy = [
                {
                    "data": r[0], "obecnosc": bool(r[1]),
                    "godzina_wejscia": r[2] or "", "godzina_wyjscia": r[3] or "",
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    dzisiaj = now.strftime("%Y-%m-%d")
    dzisiejszy_wpis = next((h for h in historia if h["data"] == dzisiaj), None)

    # Stan: "obecny"/"nieobecny" na dziś, jeśli API ma wpis. Bez wpisu (rano przed
    # przyjściem, weekend) jest "brak_danych"; nie zgadujemy "nieobecny".
    if dzisiejszy_wpis is None:
        stan = "brak_danych"
    else:
        stan = "obecny" if dzisiejszy_wpis["obecnosc"] else "nieobecny"

    await publish_sensor(ha, f"sensor.vultron_przedszkole_obecnosc_{slug}", stan,
                         f"Obecność (przedszkole): {name}",
                         {"historia": historia, "miesiac": miesiac_wpisy, "icon": "mdi:home-account"})


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
                # `or "Brak"`: klucz z null dawałby str(None), czyli napis "None".
                api_numer = str(data.get("numer") or "Brak")
                api_id = str(data.get("id") or "")
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

    state_val = db_numer if db_numer != "Brak" else 0

    await publish_sensor(ha, f"sensor.vultron_szczesliwy_numerek_{slug}", state_val,
                         f"Szczęśliwy Numerek: {name}",
                         {"numer": db_numer, "id_numerku": db_id, "icon": "mdi:clover"})


def _zebrania_attrs(lista: list) -> dict:
    """Atrybuty sensora zebrań z limitem rozmiaru (lista DESC - najpierw przyszłe)."""
    zebrania, pominiete = _fit_attr_list(lista)
    attrs = {"zebrania": zebrania, "icon": "mdi:account-group"}
    if pominiete:
        attrs["pominiete"] = pominiete
    return attrs


async def _fetch_meetings(client: httpx.AsyncClient, ha: httpx.AsyncClient,
                          base: str, s: dict) -> None:
    slug, key, name = s["slug"], s["key"], s["uczen"]
    logger.info("--> [%s] Pobieram zebrania z rodzicami...", name)

    try:
        res = await client.get(f"{base}/api/Zebrania", params={"key": key})
        if res.status_code != 200:
            logger.warning("[%s] błąd zebrań: %d", name, res.status_code)
            return

        try:
            _zebrania = res.json()
        except Exception as e:
            logger.warning("[%s] błąd parsowania JSON zebrań: %s", name, e)
            return

        if not isinstance(_zebrania, list):
            logger.warning("[%s] Nieoczekiwany format zebrań (nie lista)", name)
            return

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()
                meetings_to_insert: list[tuple] = []
                for item in _zebrania:
                    item_id_raw = item.get("id")
                    if item_id_raw is None or str(item_id_raw) == "":
                        continue
                    item_id = str(item_id_raw)

                    dt_raw = item.get("dataCzas") or ""
                    data_str = dt_raw.split("T")[0] if "T" in dt_raw else dt_raw
                    godz_str = dt_raw.split("T")[1][:5] if "T" in dt_raw else ""

                    sala   = item.get("sala") or ""
                    opis   = item.get("opis") or ""
                    online_raw = item.get("zebranieOnline")
                    online = (
                        str(online_raw)
                        if online_raw and not isinstance(online_raw, str)
                        else (online_raw or "")
                    )

                    meetings_to_insert.append(
                        (item_id, slug, data_str, godz_str, sala, opis, online)
                    )

                if meetings_to_insert:
                    cur.executemany(
                        "INSERT OR REPLACE INTO meetings VALUES (?,?,?,?,?,?,?)",
                        meetings_to_insert,
                    )
                    # Odwołane zebranie znika (w zakresie dat odpowiedzi).
                    _delete_missing(cur, "meetings", "id", slug, {r[0] for r in meetings_to_insert},
                                    _min_api_date(_zebrania, "dataCzas"))

                conn.commit()

                cur.execute(
                    "SELECT data, godzina, sala, opis, online, id "
                    "FROM meetings WHERE student_slug=? ORDER BY data DESC, godzina DESC",
                    (slug,),
                )
                lista = [
                    {
                        "data": r[0], "godzina": r[1], "sala": r[2],
                        "opis": r[3], "online": r[4], "id": r[5],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

        now_date = datetime.now().strftime("%Y-%m-%d")
        nadchodzace = sum(1 for r in lista if r["data"] >= now_date)

        await publish_sensor(
            ha,
            f"sensor.vultron_zebrania_{slug}",
            nadchodzace,
            f"Zebrania: {name}",
            _zebrania_attrs(lista),
        )

    except httpx.RequestError as e:
        logger.warning("[%s] błąd sieci przy pobieraniu zebrań: %s", name, e)
    except Exception as e:
        logger.warning("[%s] błąd zebrań: %s", name, e)

# ────────────────────────────────────────────────
# SYNCHRONIZACJA DZIENNIKA – pełna async
# ────────────────────────────────────────────────

async def sync_diary_data(students: list, cookies: list) -> None:
    fallback_cookies = {c["name"]: c["value"] for c in cookies}

    # Uczniowie o tym samym imieniu: własne zajęcia z kalendarza wymagają wtedy
    # pełnego "Imię Nazwisko:", inaczej "Jan: ..." trafiłoby do obojga.
    _first_name_counts: dict[str, int] = {}
    for _st in students:
        _fn = _fold_pl(((_st.get("uczen") or "").strip().split(" ") or [""])[0])
        if _fn:
            _first_name_counts[_fn] = _first_name_counts.get(_fn, 0) + 1
    ambiguous_first_names = {fn for fn, cnt in _first_name_counts.items() if cnt > 1}
    if ambiguous_first_names:
        logger.warning(
            "Wykryto uczniów o tym samym imieniu (%s) - dla własnych zajęć z kalendarza "
            "wymagany będzie pełny prefiks 'Imię Nazwisko:' zamiast samego imienia.",
            ", ".join(sorted(ambiguous_first_names)),
        )

    async with httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha:
        for s in students:
            logger.info("=== Synchronizacja: %s ===", s["uczen"])
            # Część samorządów hostuje Vulcan pod własną domeną (patrz run_diary_auth).
            # Fallback "eduvulcan.pl" dotyczy tylko starego cache sesji bez pola "domain".
            base = f"https://uczen.{s.get('domain') or 'eduvulcan.pl'}/{s['city']}"
            student_cookies = _session_cookies(s.get("city_cookies") or fallback_cookies,
                                               s.get("domain") or "eduvulcan.pl")
            async with httpx.AsyncClient(cookies=student_cookies, timeout=20) as client:

                # Wymuszenie kontekstu miasta na serwerze - bez tego sesja bywa
                # zablokowana na poprzednim uczniu (błędy 403).
                try:
                    await client.get(base)
                    await client.get(f"{base}/api/Context")
                except Exception as e:
                    logger.debug("Błąd przy odświeżaniu kontekstu: %s", e)

                results = await asyncio.gather(
                    _fetch_grades(client, ha, base, s),
                    _fetch_schedule(client, ha, base, s, ambiguous_first_names),
                    _fetch_timetable(client, ha, base, s),
                    _fetch_remarks(client, ha, base, s),
                    _fetch_frequency(client, ha, base, s),
                    _fetch_achievements(client, ha, base, s),
                    _fetch_lucky_number(client, ha, base, s),
                    _fetch_meetings(client, ha, base, s),
                    return_exceptions=True,
                )
            # Kolejność nazw = kolejność fetcherów w gather powyżej.
            section_names = ("oceny", "plan", "terminarz", "uwagi", "frekwencja",
                             "osiagniecia", "szczesliwy_numerek", "zebrania")
            for section, r in zip(section_names, results):
                if isinstance(r, Exception):
                    logger.error("Sekcja %s błąd dla %s: %s", section, s["uczen"], r, exc_info=r)
            logger.info("=== Zakończono: %s ===", s["uczen"])


# ────────────────────────────────────────────────
# PRZEDSZKOLE – synchronizacja danych
# Osobna od sync_diary_data (przedszkolaki mają własne fetchery, tabele, sensory
# i karty). Pętla jak w sync_diary_data, bez wykrywania kolizji imion.
# ────────────────────────────────────────────────

async def sync_przedszkole_data(przedszkolaki: list, cookies: list) -> None:
    fallback_cookies = {c["name"]: c["value"] for c in cookies}

    async with httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha:
        for s in przedszkolaki:
            logger.info("=== Synchronizacja (przedszkole): %s ===", s["uczen"])
            base = f"https://uczen.{s.get('domain') or 'eduvulcan.pl'}/{s['city']}"
            student_cookies = _session_cookies(s.get("city_cookies") or fallback_cookies,
                                               s.get("domain") or "eduvulcan.pl")
            async with httpx.AsyncClient(cookies=student_cookies, timeout=20) as client:

                # Wymuszenie kontekstu miasta - jak w sync_diary_data.
                try:
                    await client.get(base)
                    await client.get(f"{base}/api/Context")
                except Exception as e:
                    logger.debug("Błąd przy odświeżaniu kontekstu (przedszkole): %s", e)

                results = await asyncio.gather(
                    _fetch_przedszkole_plan(client, ha, base, s),
                    _fetch_przedszkole_jadlospis(client, ha, base, s),
                    _fetch_przedszkole_obecnosc(client, ha, base, s),
                    _fetch_przedszkole_zebrania(client, ha, base, s),
                    _fetch_przedszkole_oplaty(client, ha, base, s),
                    _fetch_przedszkole_informacje(client, ha, base, s),
                    _fetch_przedszkole_nauczyciele(client, ha, base, s),
                    return_exceptions=True,
                )
            # Kolejność nazw = kolejność fetcherów w gather powyżej.
            section_names = ("plan", "jadlospis", "obecnosc", "zebrania",
                             "oplaty", "informacje", "nauczyciele")
            for section, r in zip(section_names, results):
                if isinstance(r, Exception):
                    logger.error("Sekcja %s błąd dla %s (przedszkole): %s", section, s["uczen"], r, exc_info=r)

            # Funkcja synchroniczna (httpx.Client) - poza gather, w osobnym wątku.
            try:
                await asyncio.to_thread(_fetch_przedszkole_wiadomosci, s)
            except Exception as e:
                logger.error("Błąd wiadomości dla %s (przedszkole): %s", s["uczen"], e, exc_info=True)

            logger.info("=== Zakończono (przedszkole): %s ===", s["uczen"])


# ────────────────────────────────────────────────
# WIADOMOŚCI (httpx – sync, uruchamiana w wątku)
# SQLite chronione przez db_lock_thread (threading.Lock). Bez Selenium:
# ciasteczka SSO z run_diary_auth działają na wszystkich subdomenach tej samej
# domeny głównej (także białoetykietowej), w tym na subdomenie wiadomości.
# ────────────────────────────────────────────────

def _build_wiadomosci_session(domain: str, city: str, wiadomosci_cookies: dict) -> httpx.Client | None:
    """Buduje sesję dla wiadomosci.{domain} z ciasteczek SSO. GET /App sprawia,
    że serwer ustawia własne ciasteczka sesji i token (httpx zapisuje je w jar).
    "domain" to domena główna z logowania (zwykle "eduvulcan.pl", u części
    samorządów własna). Zwróconego klienta trzeba zamknąć (session.close()).
    """
    session = httpx.Client(
        cookies=_session_cookies(wiadomosci_cookies, domain),
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://wiadomosci.{domain}/{city}/App",
        },
        timeout=15,
    )
    try:
        r = _get_same_site(session, f"https://wiadomosci.{domain}/{city}/App", domain)
        if r.status_code != 200:
            logger.warning("[MESS] init sesji dla miasta %s@%s: HTTP %d", city, domain, r.status_code)
            session.close()
            return None
        if "logowanie" in str(r.url).lower() or "UserName" in r.text:
            logger.warning("[MESS] init sesji dla miasta %s@%s: przekierowano do logowania", city, domain)
            session.close()
            return None
        logger.info("[MESS] sesja dla miasta %s@%s gotowa", city, domain)
        return session
    except Exception as e:
        logger.warning("[MESS] błąd init sesji dla miasta %s@%s: %s", city, domain, e)
        session.close()
        return None


def _probe_dziennik_session(domain: str, city: str, city_cookies: dict) -> bool:
    """Tanio (bez Selenium) sprawdza, czy ciasteczka SSO dają dostęp do
    uczen.{domain}/{city}. Zwraca bool, nie rzuca; decyduje tylko, czy
    uruchamiać Selenium (CAPTCHA rozpoznaje wyłącznie run_diary_auth).
    """
    if not city_cookies:
        return False
    try:
        with httpx.Client(cookies=_session_cookies(city_cookies, domain), timeout=8) as probe:
            r = _get_same_site(probe, f"https://uczen.{domain}/{city}/api/Context", domain)
    except Exception as e:
        logger.debug("[SESJA] probe dziennika (%s@%s) nieudany: %s", city, domain, e)
        return False

    if r.status_code != 200:
        logger.debug("[SESJA] probe dziennika (%s@%s): HTTP %d", city, domain, r.status_code)
        return False
    if "logowanie" in str(r.url).lower() or "UserName" in r.text:
        logger.debug("[SESJA] probe dziennika (%s@%s): przekierowano do logowania", city, domain)
        return False
    try:
        data = r.json()
    except Exception:
        logger.debug("[SESJA] probe dziennika (%s@%s): odpowiedź nie jest poprawnym JSON", city, domain)
        return False
    return isinstance(data, dict) and "uczniowie" in data


def _try_reuse_cached_session() -> tuple[list, list, list] | None:
    """Odtwarza sesję z VUL_PKL bez Selenium. Jeśli cache nie jest starszy niż
    SESSION_CACHE_MAX_AGE_HOURS, sprawdza przez httpx dla każdego miasta
    (uczniowie i przedszkolaki razem), czy ciasteczka działają dla dziennika
    i dla wiadomości.

    Zwraca (students, przedszkolaki, cookies), gdy wszystkie miasta przejdą
    oba testy; inaczej None i main_loop robi pełne logowanie.
    """
    if not os.path.exists(VUL_PKL):
        return None

    try:
        with open(VUL_PKL, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        logger.debug("[SESJA] Nie udało się odczytać cache sesji (%s): %s", VUL_PKL, e)
        return None

    saved_at_raw = cache.get("saved_at")
    if not saved_at_raw:
        logger.debug("[SESJA] Cache bez znacznika czasu (starszy format pliku) - ignoruję.")
        return None
    try:
        saved_at = datetime.fromisoformat(saved_at_raw)
        if saved_at.tzinfo is None:   # ręcznie edytowany/stary plik - inaczej TypeError przy odejmowaniu
            saved_at = saved_at.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.debug("[SESJA] Niepoprawny znacznik czasu w cache: %r", saved_at_raw)
        return None

    age = datetime.now(timezone.utc) - saved_at
    if age > timedelta(hours=SESSION_CACHE_MAX_AGE_HOURS):
        logger.info(
            "[SESJA] Zapisana sesja ma %.1f h (limit %d h) - wymuszam pełne logowanie Selenium.",
            age.total_seconds() / 3600, SESSION_CACHE_MAX_AGE_HOURS,
        )
        return None

    students = cache.get("students") or []
    przedszkolaki = cache.get("przedszkolaki") or []
    cookies  = cache.get("cookies") or []
    # Sama pusta lista uczniów jest OK (rodzina może mieć tylko przedszkolaka);
    # cache jest bezużyteczny dopiero, gdy brak obu kategorii dzieci.
    if (not students and not przedszkolaki) or not cookies:
        return None

    # Jedna weryfikacja na parę (domena, miasto) - dzieci z tego samego miasta
    # współdzielą ciasteczka SSO. Przedszkolaki są w tej samej pętli co uczniowie,
    # inaczej miasto tylko z przedszkolakiem nigdy nie byłoby sprawdzone.
    pairs_seen: set[tuple[str, str]] = set()
    for st in (*students, *przedszkolaki):
        city = st.get("city")
        domain = st.get("domain") or "eduvulcan.pl"
        pair = (domain, city)
        if not city or pair in pairs_seen:
            continue
        pairs_seen.add(pair)

        if not _probe_dziennik_session(domain, city, st.get("city_cookies") or {}):
            logger.info(
                "[SESJA] Sesja dziennika dla miasta %s@%s wygasła - wymuszam logowanie Selenium.",
                city, domain,
            )
            return None

        wiad_session = _build_wiadomosci_session(domain, city, st.get("wiadomosci_cookies") or {})
        if wiad_session is None:
            logger.info(
                "[SESJA] Sesja wiadomości dla miasta %s@%s wygasła - wymuszam logowanie Selenium.",
                city, domain,
            )
            return None
        wiad_session.close()

    logger.info(
        "[SESJA] Zapisana sesja (%d miast, wiek %.1f h) wciąż ważna - pomijam Selenium w tym cyklu.",
        len(pairs_seen), age.total_seconds() / 3600,
    )
    return students, przedszkolaki, cookies


def run_messages_sync(students_list: list) -> None:
    conn = None

    def _fetch_inbox(session: httpx.Client, domain: str, city: str, gk: str, uczen: str) -> list | None:
        url = f"https://wiadomosci.{domain}/{city}/api/OdebraneSkrzynka"
        params = {"globalKeySkrzynka": gk, "idLastWiadomosc": 0, "pageSize": 50}
        for attempt in range(2):
            try:
                res = session.get(url, params=params)
            except Exception as e:
                logger.warning("[MESS] błąd sieciowy skrzynki %s: %s", uczen, e)
                return None

            if res.status_code == 200:
                try:
                    return res.json()
                except Exception as e:
                    logger.warning("[MESS] błąd JSON skrzynki %s: %s", uczen, e)
                    return None

            if res.status_code == 409 and attempt == 0:
                logger.warning("[MESS] 409 dla %s – ponawiam po 2s", uczen)
                time.sleep(2)
                continue

            logger.warning("[MESS] skrzynka %s: HTTP %d (próba %d)", uczen, res.status_code, attempt + 1)
            return None

        return None

    try:
        logger.info("[MESS] Pobieram wiadomości...")

        # Jedna sesja na parę (domena, miasto); fallback "eduvulcan.pl" dla starego cache.
        cities: dict[tuple[str, str], list] = {}
        for st in students_list:
            cities.setdefault((st.get("domain") or "eduvulcan.pl", st["city"]), []).append(st)

        # Sieć jest poza sekcją krytyczną bazy (długa transakcja blokuje checkpoint
        # WAL): krótki lock na odczyt → sieć bez locka → krótki lock na zapis.

        # ETAP 1 (krótki lock): które wiadomości już mamy w bazie
        existing_by_slug: dict[str, set] = {}
        with db_lock_thread:
            conn = db_connect()
            try:
                cur = conn.cursor()
                for st in students_list:
                    cur.execute(
                        "SELECT key FROM messages WHERE student_slug=?",
                        (st["slug"],),
                    )
                    existing_by_slug[st["slug"]] = {row[0] for row in cur.fetchall()}
            finally:
                conn.close()
                conn = None

        # ETAP 2 (BEZ locka): pobieranie po sieci
        rows_to_insert: list[tuple] = []
        read_updates: list[tuple] = []

        for (domain, city), students in cities.items():
            # Ciasteczka SSO są wspólne dla miasta - bierzemy od pierwszego ucznia.
            city_cookies = students[0].get("wiadomosci_cookies", {})

            session = _build_wiadomosci_session(domain, city, city_cookies)
            if session is None:
                logger.error("[MESS] pominięto miasto %s@%s – brak sesji", city, domain)
                continue

            try:
                for st in students:
                    gk       = st.get("globalKeySkrzynka")
                    assigned = st["slug"]

                    if not gk:
                        logger.warning("[MESS] brak globalKeySkrzynka dla %s", st["uczen"])
                        continue

                    logger.info("[MESS] pobieram skrzynkę: %s", st["uczen"])
                    messages = _fetch_inbox(session, domain, city, gk, st["uczen"])
                    if messages is None:
                        continue
                    if not isinstance(messages, list):
                        logger.warning("[MESS] nieoczekiwany format skrzynki %s - pomijam", st["uczen"])
                        continue

                    # Treści wiadomości już zapisanych nie pobieramy ponownie
                    # (mniej requestów, mniejsze ryzyko CAPTCHA) - tylko status przeczytania.
                    existing_keys = existing_by_slug.get(assigned, set())

                    for m in messages:
                        # Jedna wadliwa wiadomość nie może przerwać pozostałych.
                        try:
                            m_k = m.get("apiGlobalKey")
                            if not m_k:
                                continue
                            read_flag = 1 if m.get("przeczytana") else 0

                            if m_k in existing_keys:
                                read_updates.append((read_flag, m_k))
                                continue

                            det = session.get(
                                f"https://wiadomosci.{domain}/{city}/api/WiadomoscSzczegoly",
                                params={"apiGlobalKey": m_k},
                            )
                            if det.status_code != 200:
                                continue
                            rows_to_insert.append(
                                (m_k, assigned,
                                 _txt(m.get("data")),
                                 _txt(m.get("korespondenci")),
                                 _txt(m.get("temat")),
                                 _txt((det.json() or {}).get("tresc") or "Brak"),
                                 read_flag)
                            )
                        except Exception as e:
                            logger.warning("[MESS] pominięto wiadomość (%s): %s", st["uczen"], e)
            finally:
                session.close()

        # ETAP 3 (krótki lock): zapis do bazy + odczyt danych do sensorów
        sensor_payloads: list[tuple] = []
        with db_lock_thread:
            conn = db_connect()
            try:
                cur = conn.cursor()

                try:
                    if read_updates:
                        cur.executemany(
                            "UPDATE messages SET przeczytana=? WHERE key=?",
                            read_updates,
                        )
                    if rows_to_insert:
                        cur.executemany(
                            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                            rows_to_insert,
                        )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error("[MESS] rollback: %s", e, exc_info=True)

                # Dane dla sensorów
                for st in students_list:
                    slug = st["slug"]
                    cur.execute(
                        "SELECT data,nadawca,temat,tresc,przeczytana FROM messages "
                        "WHERE student_slug=? ORDER BY data DESC LIMIT 10",
                        (slug,),
                    )
                    rows = cur.fetchall()
                    unread = cur.execute(
                        "SELECT COUNT(*) FROM messages WHERE student_slug=? AND przeczytana=0",
                        (slug,),
                    ).fetchone()[0]
                    total = cur.execute(
                        "SELECT COUNT(*) FROM messages WHERE student_slug=?",
                        (slug,),
                    ).fetchone()[0]

                    msgs = []
                    for r in rows:
                        is_u = int(r[4]) == 0
                        body = ""
                        if is_u:
                            body = clean_html(r[3])
                            if len(body) > 2000:
                                body = body[:1997] + "..."
                        msgs.append({
                            "data":        (r[0] or "").replace("T", " ")[:16],
                            "nadawca":     r[1] or "",
                            "temat":       r[2] or "",
                            "tresc":       body,
                            "przeczytana": not is_u,
                        })
                    # 10 długich nieprzeczytanych treści przekracza limit HA (16384 B).
                    msgs, skrocone = _fit_messages(msgs)

                    sensor_payloads.append((
                        f"sensor.vultron_wiadomosci_{slug}",
                        unread,
                        f"Wiadomości: {st['uczen']}",
                        {"wiadomosci": msgs, "stats": f"{unread} / {total}",
                         **({"skrocone": skrocone} if skrocone else {})},
                    ))
            finally:
                conn.close()
                conn = None

        # ETAP 4 (BEZ locka): publikacja sensorów do Home Assistanta
        for entity_id, state_val, friendly, attrs in sensor_payloads:
            publish_sensor_sync(entity_id, state_val, friendly, attrs)

        logger.info("[MESS] Gotowe.")

    except Exception as e:
        logger.error("[MESS] Błąd krytyczny: %s", e, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logger.debug("[MESS] błąd zamykania bazy: %s", e)
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

_DATE_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_DATE_DOTTED_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")

# Tabele przycinane po kolumnie "data". Celowo pominięte: "achievements" (brak
# daty) i "grades" (historia ocen z lat ma wartość dla rodzica).
# Zapytania retencji jako stałe (powód jak przy _DELETE_MISSING_SQL).
_PRUNE_SQL: dict[str, tuple[str, str]] = {
    "schedule": ("SELECT rowid, data FROM schedule", "DELETE FROM schedule WHERE rowid=?"),
    "remarks": ("SELECT rowid, data FROM remarks", "DELETE FROM remarks WHERE rowid=?"),
    "timetable": ("SELECT rowid, data FROM timetable", "DELETE FROM timetable WHERE rowid=?"),
    "frequency": ("SELECT rowid, data FROM frequency", "DELETE FROM frequency WHERE rowid=?"),
    "free_days": ("SELECT rowid, data FROM free_days", "DELETE FROM free_days WHERE rowid=?"),
    "meetings": ("SELECT rowid, data FROM meetings", "DELETE FROM meetings WHERE rowid=?"),
    "frequency_stats": ("SELECT rowid, data FROM frequency_stats", "DELETE FROM frequency_stats WHERE rowid=?"),
    "lucky_number": ("SELECT rowid, data FROM lucky_number", "DELETE FROM lucky_number WHERE rowid=?"),
    "messages": ("SELECT rowid, data FROM messages", "DELETE FROM messages WHERE rowid=?"),
    "przedszkole_obecnosc": ("SELECT rowid, data FROM przedszkole_obecnosc", "DELETE FROM przedszkole_obecnosc WHERE rowid=?"),
    "przedszkole_plan": ("SELECT rowid, data FROM przedszkole_plan", "DELETE FROM przedszkole_plan WHERE rowid=?"),
    "przedszkole_jadlospis": ("SELECT rowid, data FROM przedszkole_jadlospis", "DELETE FROM przedszkole_jadlospis WHERE rowid=?"),
    "przedszkole_zebrania": ("SELECT rowid, data FROM przedszkole_zebrania", "DELETE FROM przedszkole_zebrania WHERE rowid=?"),
    "przedszkole_oplaty": ("SELECT rowid, data FROM przedszkole_oplaty", "DELETE FROM przedszkole_oplaty WHERE rowid=?"),
    "przedszkole_wiadomosci": ("SELECT rowid, data FROM przedszkole_wiadomosci", "DELETE FROM przedszkole_wiadomosci WHERE rowid=?"),
}


def _normalize_date_prefix(raw) -> str | None:
    """Zwraca datę w formacie 'YYYY-MM-DD' albo None, gdy formatu daty w
    danym wierszu nie da się rozpoznać z pewnością LUB gdy data - mimo
    poprawnego formatu - nie jest kalendarzowo poprawna (np. dzień 32,
    miesiąc 13, 29 lutego w roku nieprzestępnym).

    Dane w bazie pochodzą z różnych pól API Vulcan. Większość tabel ma ISO 8601
    ("2026-09-09", czasem z 'T', godziną i strefą, np. "messages.data":
    "2026-10-29T07:38:50.85+01:00" - sprawdzone na prawdziwej bazie). Format
    kropkowy "09.09.2026" jest obsługiwany na wszelki wypadek (karty JS też
    obsługują obie postacie).

    W niejasnych przypadkach (brak roku w dacie kropkowej, data niepoprawna
    kalendarzowo, np. "2026-13-45") zwraca None - _prune_old_data takiego
    wiersza nie kasuje. Każdy kandydat jest weryfikowany przez zbudowanie daty.
    """
    if not raw or not isinstance(raw, str):
        return None

    candidate: str | None = None

    m = _DATE_ISO_RE.match(raw.strip())
    if m:
        candidate = m.group(1)
    else:
        m = _DATE_DOTTED_RE.match(raw.strip())
        if m:
            day, month, year = m.groups()
            if not year:
                return None  # brak roku w dacie kropkowej - nie zgadujemy
            if len(year) == 2:
                year = "20" + year
            try:
                candidate = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            except ValueError:
                return None

    if candidate is None:
        return None

    # Walidacja kalendarzowa przez faktyczne zbudowanie daty (dzień 32, 29 lutego itp.).
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None

    return candidate


def _prune_stale_ha_cache(cur: sqlite3.Cursor) -> int:
    """Usuwa z ha_cache wpisy encji nieaktualizowanych od HA_CACHE_RETENTION_DAYS.

    "last_update" leży w attributes_json i jest zapisywane przez nasz kod
    (publish_sensor/publish_sensor_sync) zawsze w formacie "%Y-%m-%d %H:%M:%S",
    więc wystarcza porównanie tekstowe.

    Wiersz jest kasowany tylko, gdy JSON się parsuje i zawiera "last_update";
    przy jakiejkolwiek niepewności zostaje.

    Zwraca liczbę usuniętych wierszy.
    """
    cutoff = (datetime.now() - timedelta(days=HA_CACHE_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT entity_id, attributes_json FROM ha_cache")

    to_delete: list[tuple[str]] = []
    for entity_id, attrs_json in cur.fetchall():
        try:
            attrs = json.loads(attrs_json)
        except Exception as e:
            # uszkodzony JSON - nie zgadujemy, wiersz zostaje
            logger.debug("[RETENCJA] ha_cache: pomijam %s (uszkodzony JSON: %s)", entity_id, e)
            continue
        last_update = attrs.get("last_update")
        if not last_update or not isinstance(last_update, str):
            continue
        if last_update < cutoff:
            to_delete.append((entity_id,))

    if to_delete:
        cur.executemany("DELETE FROM ha_cache WHERE entity_id=?", to_delete)
    return len(to_delete)


def _prune_old_data() -> None:
    """Usuwa wpisy starsze niż RETENTION_DAYS (~1,5 roku) oraz stare wpisy
    ha_cache (_prune_stale_ha_cache), po czym robi VACUUM.

    Zabezpieczenia:
    1. Wiersz jest kasowany tylko, gdy jego data jest jednoznaczna, poprawna
       kalendarzowo (_normalize_date_prefix) i starsza niż próg.
    2. "achievements" (brak daty) i "grades" (długa historia) są pomijane.
    3. Wywoływana pod db_lock (w _run_retention_if_due), a w środku pod
       db_lock_thread i _cache_conn_lock - VACUUM nie koliduje z innymi zapisami.
    """
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")

    with db_lock_thread, _cache_conn_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            total_deleted = 0

            for table, (select_sql, delete_sql) in _PRUNE_SQL.items():
                cur.execute(select_sql)
                rowids_to_delete = [
                    (rowid,) for rowid, raw_data in cur.fetchall()
                    if (norm := _normalize_date_prefix(raw_data)) is not None and norm < cutoff
                ]
                if rowids_to_delete:
                    cur.executemany(delete_sql, rowids_to_delete)
                    total_deleted += len(rowids_to_delete)
                    logger.info(
                        "[RETENCJA] %s: usunięto %d wpis(ów) starszych niż %s.",
                        table, len(rowids_to_delete), cutoff,
                    )

            # ha_cache ma osobną logikę (brak kolumny "data") - patrz _prune_stale_ha_cache
            ha_cache_deleted = _prune_stale_ha_cache(cur)
            if ha_cache_deleted:
                total_deleted += ha_cache_deleted
                logger.info(
                    "[RETENCJA] ha_cache: usunięto %d nieaktualną(-ych) encję/encji "
                    "(brak aktualizacji od %d dni).",
                    ha_cache_deleted, HA_CACHE_RETENTION_DAYS,
                )

            conn.commit()

            if total_deleted > 0:
                logger.info(
                    "[RETENCJA] Łącznie usunięto %d wpis(ów). Odzyskiwanie miejsca na dysku (VACUUM)...",
                    total_deleted,
                )
                conn.execute("VACUUM")
                logger.info("[RETENCJA] VACUUM zakończony.")
            else:
                logger.debug("[RETENCJA] Brak wpisów starszych niż %s - nic do usunięcia.", cutoff)
        except Exception as e:
            logger.error("[RETENCJA] Błąd czyszczenia bazy: %s", e, exc_info=True)
        finally:
            conn.close()


def _retention_due() -> bool:
    """Tania kontrola pliku-znacznika - True, gdy minęło już
    RETENTION_CHECK_INTERVAL_HOURS od ostatniego (próby) czyszczenia bazy."""
    try:
        with open(RETENTION_MARKER_PATH, encoding="utf-8") as f:
            last_run = datetime.fromisoformat(f.read().strip())
    except Exception:
        return True  # brak znacznika = jeszcze nigdy nie uruchomiono na tym wolumenie
    return (datetime.now(timezone.utc) - last_run) > timedelta(hours=RETENTION_CHECK_INTERVAL_HOURS)


def _mark_retention_run() -> None:
    try:
        with open(RETENTION_MARKER_PATH, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.warning("[RETENCJA] Nie udało się zapisać znacznika czasu ostatniego czyszczenia: %s", e)


async def _run_retention_if_due() -> None:
    """Odpala _prune_old_data najwyżej raz na RETENTION_CHECK_INTERVAL_HOURS,
    pod db_lock (wstrzymuje na chwilę pozostałe zapisy do bazy)."""
    if not _retention_due():
        return
    async with db_lock:
        await asyncio.to_thread(_prune_old_data)
    _mark_retention_run()


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

async def gdansk_main_loop() -> None:
    """GPE uses a different login and MVC API; keep eduVULCAN unchanged."""
    from gdansk import AuthenticationError, GdanskError, authenticate, snapshot

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    _become_child_subreaper()
    _log_timezone_info()
    copy_resources()
    await wait_for_ha_api()
    run_setup_ui()
    conn = db_connect()
    db_init(conn)
    conn.close()
    failures = 0
    logger.info("Uruchamianie Vultron: GPE Gdańsk (odczyt UONET+)")
    async with httpx.AsyncClient(headers=HA_HEADERS, timeout=15) as ha:
        await restore_entities_from_cache(ha)
        while not stop.is_set():
            now = datetime.now()
            if not _test_mode and 1 <= now.hour < 6:
                delay = max(60, int((now.replace(hour=6, minute=0, second=0) - now).total_seconds()))
            else:
                try:
                    def collect():
                        modules, messages = authenticate(CONFIG, _get_driver, _hard_kill_service)
                        return snapshot(modules, messages, CONFIG)
                    readings, errors = await asyncio.wait_for(asyncio.to_thread(collect), timeout=600)
                    published = 0
                    for reading in readings:
                        if stop.is_set():
                            break
                        # Never silently truncate a description or send attributes
                        # which HA will discard. Report the affected entity instead.
                        attrs_size = len(json.dumps(reading.attrs, ensure_ascii=False).encode("utf-8"))
                        if attrs_size > 15000:
                            errors.append(reading.entity + ": atrybuty przekraczają limit HA")
                            continue
                        await publish_sensor(ha, reading.entity, reading.state, reading.name, reading.attrs)
                        published += 1
                    await publish_sensor(ha, "sensor.vultron_gdansk_status", "partial" if errors else "ok",
                                         "Vultron Gdańsk – synchronizacja",
                                         {"errors": errors, "entities": published,
                                          "last_success": datetime.now().isoformat(timespec="seconds")})
                    for error in errors:
                        logger.warning("[GPE] %s", error)
                    logger.info("[GPE] Odczyt zakończony: %d encji, %d problemów", published, len(errors))
                    failures = 0
                except AuthenticationError as exc:
                    logger.error("[GPE] %s", exc)
                    await publish_sensor(ha, "sensor.vultron_gdansk_status", "authentication_error",
                                         "Vultron Gdańsk – synchronizacja", {"error": str(exc)})
                    # Do not repeatedly submit an invalid password.
                    break
                except asyncio.TimeoutError:
                    logger.error("[GPE] Przekroczono limit 10 minut; przerywam proces przeglądarki")
                    os._exit(1)
                except Exception as exc:
                    failures += 1
                    safe_error = str(exc) if isinstance(exc, GdanskError) else type(exc).__name__
                    logger.error("[GPE] Odczyt nieudany: %s", safe_error)
                    await publish_sensor(ha, "sensor.vultron_gdansk_status", "error",
                                         "Vultron Gdańsk – synchronizacja", {"error": safe_error})
                await _run_size_monitor(ha)
                delay = 2400 + secrets.randbelow(1201) + min(failures * 600, 3600)
                logger.info("[GPE] Następny odczyt za około %d minut", delay // 60)
            for elapsed in range(0, delay, 10):
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                    break
                except asyncio.TimeoutError:
                    pass
                if (elapsed + 10) % 60 == 0:
                    await check_and_restore(ha)
    logger.info("Vultron Gdańsk zatrzymany")


async def _publish_status(ha: httpx.AsyncClient, state: str, error: str, last_success: str | None) -> None:
    """sensor.vultron_status: ok / error / authentication_error - odpowiednik
    sensor.vultron_gdansk_status. Komunikaty są stałe (bez treści serwera)."""
    await publish_sensor(ha, "sensor.vultron_status", state, "Vultron – synchronizacja",
                         {"error": error, "last_success": last_success, "icon": "mdi:sync"})


async def main_loop() -> None:
    if CONFIG.get("provider", "eduvulcan") == "gdansk":
        await gdansk_main_loop()
        return
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    mess_timeouts = 0   # licznik kolejnych timeoutów synchronizacji wiadomości
    auth_fail_streak = 0   # licznik kolejnych NIEUDANYCH logowań z rzędu (backoff)
    login_rejected_streak = 0   # odrzucone logowania z rzędu (LOGIN_REJECTED_STOP_AFTER)
    last_success: str | None = None

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # Rejestracja jako "subreaper" procesów potomnych - MUSI nastąpić przed
    # pierwszym uruchomieniem Selenium (patrz _become_child_subreaper), żeby
    # osierocone procesy Chromium po _hard_kill_service zawsze trafiały do
    # nas do odebrania, niezależnie od tego, czy dodatek jest akurat PID 1.
    _become_child_subreaper()
    _log_timezone_info()

    # Wypisywanie wersji z config.yaml
    addon_ver = get_addon_version()
    logger.info("=====================================")
    logger.info(" Uruchamianie Vultron v%s", addon_ver)
    logger.info("=====================================")

    copy_resources()
    await wait_for_ha_api()
    await asyncio.to_thread(run_setup_ui)   # synchroniczny websocket - poza pętlą zdarzeń

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

            # Najpierw tanio (httpx) sprawdzamy, czy zapisana sesja (VUL_PKL) działa -
            # wtedy pomijamy Chromium. Funkcja zwraca krotkę albo None.
            cached_session = await asyncio.to_thread(_try_reuse_cached_session)
            students, przedszkolaki, cookies = cached_session if cached_session else (None, None, None)

            # Sukces = cookies oraz uczniowie LUB przedszkolaki (rodzina może mieć
            # tylko przedszkolaka).
            login_ok = bool(cookies) and bool(students or przedszkolaki)
            rejected_now = False

            if login_ok:
                logger.info("--> Logowanie poprzez COOKIES - OK")
                logger.info("=== Reużyto zapisanej sesji – logowanie Selenium pominięte w tym cyklu ===")
            else:
                logger.info("--> Logowanie poprzez COOKIES - NO - USE CHROMIUM")
                try:
                    students, przedszkolaki, cookies = await asyncio.wait_for(
                        asyncio.to_thread(run_diary_auth), timeout=600
                    )
                except LoginRejected:
                    # Jedyny powód trwałego zatrzymania: nie wysyłamy w kółko złego hasła.
                    students, przedszkolaki, cookies = None, None, None
                    rejected_now = True
                    login_rejected_streak += 1
                    if login_rejected_streak >= LOGIN_REJECTED_STOP_AFTER:
                        logger.critical(
                            "!!! Logowanie odrzucone %d razy z rzędu - zatrzymuję dodatek, żeby nie "
                            "zablokować konta. Sprawdź login i hasło w konfiguracji i zaloguj się "
                            "ręcznie na eduvulcan.pl, potem uruchom dodatek ponownie. !!!",
                            login_rejected_streak,
                        )
                        await _publish_status(ha, "authentication_error",
                                              "Logowanie odrzucone przez eduVULCAN - sprawdź login i hasło",
                                              last_success)
                        if not IS_ADDON:
                            # Docker: wyjście z procesu = restart przez
                            # "restart: unless-stopped" i licznik od zera, czyli
                            # kolejne złe hasła. Czekamy bezczynnie na SIGTERM
                            # (docker compose restart/stop po poprawie danych).
                            logger.critical(
                                "!!! Docker: kontener zostaje uruchomiony, ale bez logowania. Popraw "
                                "data/options.json i zrób: docker compose restart !!!"
                            )
                            await stop_event.wait()
                        # Zatrzymanie przez stop_event (bez sys.exit() w coroutine).
                        stop_event.set()
                        break
                    logger.warning("[AUTH] Logowanie odrzucone (%d/%d) - jeszcze jedna próba w następnym cyklu.",
                                   login_rejected_streak, LOGIN_REJECTED_STOP_AFTER)
                except asyncio.TimeoutError:
                    # Wątku Selenium nie da się przerwać, a Python przy zamykaniu
                    # czeka na jego join - zostaje twarde zakończenie procesu
                    # (Supervisor HA zrestartuje dodatek).
                    logger.critical(
                        "!!! Logowanie (Selenium) nie zakończyło się w ciągu 10 minut - "
                        "prawdopodobne zawieszenie chromedrivera. Wymuszam twarde "
                        "zakończenie procesu, aby Supervisor zrestartował dodatek. !!!"
                    )
                    os._exit(1)
                except Exception as e:
                    logger.error("Nieoczekiwany błąd podczas logowania: %s", e)
                    students, przedszkolaki, cookies = None, None, None

                login_ok = bool(cookies) and bool(students or przedszkolaki)

            # Backoff liczy nieudane pełne logowania; każdy sukces zeruje liczniki.
            if login_ok:
                auth_fail_streak = 0
                login_rejected_streak = 0
            else:
                auth_fail_streak += 1
                await _publish_status(
                    ha, "error",
                    "Logowanie odrzucone - ponowna próba w następnym cyklu" if rejected_now
                    else "Logowanie nieudane - ponowna próba w następnym cyklu",
                    last_success,
                )

            if login_ok:
                # Sygnał zatrzymania sprawdzamy między etapami, żeby SIGTERM nie
                # czekał do końca cyklu.
                if stop_event.is_set():
                    logger.info("Otrzymano sygnał zatrzymania – przerywam cykl.")
                    break

                await sync_diary_data(students, cookies)

                if przedszkolaki:
                    await sync_przedszkole_data(przedszkolaki, cookies)

                if stop_event.is_set():
                    logger.info("Otrzymano sygnał zatrzymania – pomijam wiadomości.")
                    break

                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(run_messages_sync, students), timeout=600
                    )
                    mess_timeouts = 0
                except asyncio.TimeoutError:
                    # Wątek po timeoucie działa dalej i zajmuje slot puli to_thread;
                    # po 3 takich z rzędu wymuszamy restart, zanim pula się wyczerpie.
                    mess_timeouts += 1
                    logger.error(
                        "[MESS] Synchronizacja wiadomości przekroczyła 10 minut – "
                        "pomijam (%d z rzędu).", mess_timeouts
                    )
                    if mess_timeouts >= 3:
                        logger.critical(
                            "!!! Trzeci z rzędu timeout synchronizacji wiadomości – "
                            "wymuszam restart dodatku, aby nie wyczerpać puli wątków. !!!"
                        )
                        os._exit(1)

                last_success = datetime.now().isoformat(timespec="seconds")
                await _publish_status(ha, "ok", "", last_success)

            if stop_event.is_set():
                break

            await _run_size_monitor(ha)
            await _run_retention_if_due()

            now_after = datetime.now()
            wd_after = now_after.weekday()
            cykl = "Cykl OK" if login_ok else "Cykl bez danych (logowanie nieudane)"

            if not _test_mode:
                if wd_after == 5:
                    next_h = next((h for h in (8, 16, 23) if h > now_after.hour), None)
                    if next_h:
                        wake_at = now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now_after + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("%s (Sobota) → następne pobieranie o %s (za ~%d min)", cykl, wake_at.strftime("%H:%M"), wait_time // 60)

                elif wd_after == 6:
                    next_h = next((h for h in (8, 12, 20) if h > now_after.hour), None)
                    if next_h:
                        wake_at = now_after.replace(hour=next_h, minute=0, second=0, microsecond=0)
                    else:
                        wake_at = (now_after + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
                    wait_time = int(max(60, (wake_at - now_after).total_seconds()))
                    logger.info("%s (Niedziela) → następne pobieranie o %s (za ~%d min)", cykl, wake_at.strftime("%H:%M"), wait_time // 60)

                else:
                    # 40-60 min z losowym rozrzutem
                    wait_time = 2400 + secrets.randbelow(1201)
                    next_run = now_after + timedelta(seconds=wait_time)
                    logger.info("%s → następny za ~%d min (o %s)", cykl, wait_time // 60, next_run.strftime("%H:%M"))
            else:
                # j.w.
                wait_time = 2400 + secrets.randbelow(1201)
                next_run = now_after + timedelta(seconds=wait_time)
                logger.info("[TEST MODE] %s → następny za ~%d min (o %s)", cykl, wait_time // 60, next_run.strftime("%H:%M"))

            # Backoff: patrz AUTH_BACKOFF_*.
            if auth_fail_streak > 1:
                extra_backoff = min(
                    (auth_fail_streak - 1) * AUTH_BACKOFF_STEP_SECONDS,
                    AUTH_BACKOFF_MAX_SECONDS,
                )
                wait_time += extra_backoff
                logger.warning(
                    "[AUTH] %d nieudanych logowań z rzędu - wydłużam przerwę o %d min "
                    "(następna próba za ~%d min).",
                    auth_fail_streak, extra_backoff // 60, wait_time // 60,
                )

            for elapsed in range(0, wait_time, 10):
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=10)
                    break
                except asyncio.TimeoutError:
                    pass

                if (elapsed + 10) % 60 == 0:
                    await check_and_restore(ha)

    # Zamknięcie połączenia cache (checkpoint WAL); zapisy są commitowane na bieżąco.
    with _cache_conn_lock:
        if _cache_conn is not None:
            try:
                _cache_conn.close()
            except Exception as e:
                logger.debug("Błąd zamykania połączenia cache: %s", e)

    logger.info("Vultron zatrzymany (graceful shutdown).")

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamykanie…")
        sys.exit(0)
