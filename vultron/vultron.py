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
logger.propagate = False

# ────────────────────────────────────────────────
# TRYB "ANONIM" - podstawianie danych osobowych w logu
# ────────────────────────────────────────────────
# Cel: użytkownik może wysłać deweloperowi log do diagnozy, bez ujawniania
# imienia/nazwiska dziecka, miasta ani domeny szkoły. Działa na dwóch
# filarach:
#   1. Poziom logowania NIGDY nie jest podnoszony do TRACE w tym trybie -
#      surowe odpowiedzi API (mogące zawierać cokolwiek, łącznie z np. adresem
#      e-mail zalogowanego rodzica widocznym w kodzie strony wiadomości) nigdy
#      nie trafiają do logu. To jest ważniejsze zabezpieczenie niż samo
#      podstawianie poniżej - bez tego punktu podstawianie dawałoby fałszywe
#      poczucie bezpieczeństwa.
#   2. _AnonymizingFilter podstawia w KAŻDYM komunikacie (na poziomie do
#      DEBUG włącznie) zarejestrowane wcześniej wartości (imię i nazwisko,
#      slug, miasto, domena) na generyczne etykiety typu "Uczeń 1", "miasto1".
#      Mapowanie jest budowane w locie, w miejscu gdzie dodatek sam odkrywa
#      te wartości (patrz _anon_register_student) - nie zgadujemy wzorcem,
#      tylko podstawiamy DOKŁADNIE te stringi, które sam dodatek zna.
_anon_map: dict[str, str] = {}
_anon_student_counter = 0
_anon_city_counter = 0
_anon_domain_counter = 0

def _anon_register_student(name: str, slug: str, city: str, domain: str) -> None:
    """Rejestruje mapowanie realny_string -> etykieta. Bezpieczne do
    wielokrotnego wywołania dla tego samego ucznia (idempotentne dzięki
    sprawdzeniu "czy już w mapie") - wołane zarówno przy świeżym logowaniu
    Selenium, jak i przy reużyciu zapisanej sesji, żeby obie ścieżki dawały
    identycznie zanonimizowany log. Mapowanie żyje tylko w pamięci procesu -
    resetuje się przy każdym restarcie dodatku, co jest zamierzone (nie ma
    potrzeby zachowywania tych samych etykiet między restartami).

    Domena "eduvulcan.pl" (współdzielona przez zdecydowaną większość
    użytkowników) NIE jest podstawiana - sama w sobie nikogo nie identyfikuje,
    a jej pozostawienie w logu jest diagnostycznie przydatne. Każda INNA
    domena (białoetykietowa, jak np. edu.lublin.eu) dostaje etykietę
    "selfhostN.przyklad" - widać więc od razu w logu, że to białoetykietowe
    wdrożenie (przydatne np. przy błędach podobnych do zgłoszenia z Lublina),
    bez ujawniania KTÓREGO konkretnie samorządu to dotyczy.
    """
    global _anon_student_counter, _anon_city_counter, _anon_domain_counter
    if name and name not in _anon_map:
        _anon_student_counter += 1
        _anon_map[name] = f"Uczeń {_anon_student_counter}"
        if slug:
            _anon_map[slug] = f"uczen_{_anon_student_counter}"
    if city and city not in _anon_map:
        _anon_city_counter += 1
        _anon_map[city] = f"miasto{_anon_city_counter}"
    if domain and domain not in _anon_map and domain.lower() != "eduvulcan.pl":
        # Tylko domeny INNE niż standardowa, współdzielona "eduvulcan.pl" są
        # podstawiane - patrz uzasadnienie w docstringu wyżej.
        _anon_domain_counter += 1
        _anon_map[domain] = f"selfhost{_anon_domain_counter}.przyklad"


class _AnonymizingFilter(logging.Filter):
    """Podstawia zarejestrowane w _anon_map wartości w KAŻDYM logowanym
    komunikacie, zanim trafi do konsoli/pliku. Modyfikuje rekord w miejscu
    i zawsze zwraca True (nic nie blokuje, tylko podmienia treść).

    Bezpieczne przy braku mapowań (pusta mapa na starcie, zanim dodatek
    zdąży się zalogować) i przy błędach formatowania komunikatu - w obu
    przypadkach po prostu przepuszcza log bez zmian, zamiast wywalić
    logowanie.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if not _anon_map:
            return True
        try:
            msg = record.getMessage()
        except Exception:
            return True
        # Najdłuższe wartości najpierw - zabezpieczenie przed częściowym
        # podstawieniem, gdyby jedna zarejestrowana wartość była podciągiem innej.
        for real, fake in sorted(_anon_map.items(), key=lambda kv: -len(kv[0])):
            if real and real in msg:
                msg = msg.replace(real, fake)
        record.msg = msg
        record.args = ()
        return True

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

    logger.warning(
        "UWAGA: tryb TRACE zapisuje pełne odpowiedzi API (oceny, uwagi, treści "
        "wiadomości) do /data/vultron.log - log zawiera DANE OSOBOWE. "
        "Przejrzyj go przed udostępnieniem komukolwiek i wyłącz trace po diagnozie."
    )

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

elif _log_level_conf == "wyslij_loga":
    # POPRAWKA: poziom szczegółowości = DEBUG, NIGDY TRACE (patrz komentarz
    # przy definicji _AnonymizingFilter wyżej - to jest kluczowe, nie samo
    # podstawianie nazwisk). Filtr dołączony do OBU handlerów (konsola i
    # plik), żeby żadna ścieżka logowania nie ominęła podstawienia.
    logger.setLevel(logging.DEBUG)
    _anon_filter = _AnonymizingFilter()
    _ch.addFilter(_anon_filter)
    _fh.addFilter(_anon_filter)
    logger.info("=" * 70)
    logger.info("WYSLIJ_LOGA AKTYWNY — WSZYSTKO PONIŻEJ TEJ LINII JEST BEZPIECZNE")
    logger.info("DO WKLEJENIA W ZGŁOSZENIU BŁĘDU (bez danych osobowych)")
    logger.info("=" * 70)
    logger.info(
        "Imiona, sluga, miasta i inne (niż eduvulcan.pl) domeny będą podstawiane "
        "generycznymi etykietami w logu. Surowe odpowiedzi API (TRACE) są "
        "wyłączone niezależnie od tego ustawienia."
    )
elif _log_level_conf == "debug":
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

# ────────────────────────────────────────────────
# STAŁE / CACHE
# ────────────────────────────────────────────────

# Encja kalendarza HA, z którego wczytywane są własne (ręcznie dodane) zajęcia.
# Format wydarzeń w kalendarzu: "{Imię}: Nazwa zajęć" (np. "Jan: Dodatkowy angielski").
# Puste/niepoprawne wartości w konfiguracji nie wysadzają dodatku - używamy bezpiecznego fallbacku.
_CAL_ENTITY_RAW = CONFIG.get("calendar_entity") or "calendar.local_szkola"
CALENDAR_ENTITY = str(_CAL_ENTITY_RAW).strip() or "calendar.local_szkola"
if not re.fullmatch(r"calendar\.[a-z0-9_]+", CALENDAR_ENTITY):
    logger.warning(
        "Nieprawidłowa nazwa encji kalendarza w konfiguracji (%r) - używam domyślnej calendar.local_szkola.",
        CALENDAR_ENTITY,
    )
    CALENDAR_ENTITY = "calendar.local_szkola"

# Status specjalny dla zajęć własnych (z kalendarza HA) - odróżnia je od statusów
# pochodzących z Vulcan (ZAST/PRZEN/ODWOL/NIEOB), które są liczbowe u źródła.
STATUS_WLASNE = "WLASNE"

# Maksymalny wiek zapisanej sesji (VUL_PKL), przy którym w ogóle próbujemy ją
# reużyć zamiast odpalać Selenium. Sesja i tak jest zawsze weryfikowana "na
# żywo" tanim zapytaniem httpx przed użyciem (patrz _try_reuse_cached_session) -
# ten limit to dodatkowe zabezpieczenie przed próbą reużycia bardzo starego
# stanu (np. po kilkudniowej przerwie/awarii), gdzie lepiej od razu zalogować
# się od nowa, niż polegać na czymś nietypowo długo nieaktywnym.
SESSION_CACHE_MAX_AGE_HOURS = 6

# ────────────────────────────────────────────────
# RETENCJA DANYCH (przycinanie starych wpisów z bazy)
# ────────────────────────────────────────────────
# Baza rosła bez końca - żadna tabela poza schedule/grades (a i te tylko
# częściowo, w obrębie rolującego okna) nie miała mechanizmu usuwania starych
# wpisów. Po latach użytkowania to coraz większy plik na karcie SD, wolniejsze
# zapytania i więcej I/O przy każdym cyklu.
#
# 1,5 roku ≈ 548 dni. Zaokrąglone w GÓRĘ (365*1.5 = 547.5) celowo - lepiej
# zostawić o jeden dzień więcej danych, niż przez zaokrąglenie w dół usunąć
# coś, co formalnie mieściło się jeszcze w progu.
RETENTION_DAYS = 548

# ha_cache to WYŁĄCZNIE pomocniczy cache do błyskawicznego przywracania
# sensorów po restarcie HA (patrz restore_entities_from_cache) - nie ma
# żadnego powodu trzymać w nim wpisy tak długo jak dane operacyjne. Krótszy,
# osobny próg: encja nieaktualizowana od tylu dni to niemal na pewno
# usunięte dziecko, zmieniony slug albo usunięta kategoria sensora - dalsze
# trzymanie takiego wpisu tylko zaśmieca bazę bez żadnej korzyści.
HA_CACHE_RETENTION_DAYS = 60

# Backoff po kolejnych NIEUDANYCH logowaniach z rzędu (Selenium lub reużycie
# sesji zawiodło i wpadliśmy w pełne logowanie, które też się nie powiodło).
# Bez tego dodatek próbowałby logować się co ~40-60 min bez końca nawet przy
# uporczywym problemie, zwiększając ryzyko trafienia na blokadę CAPTCHA przy
# każdej kolejnej próbie. +10 min za każde KOLEJNE nieudane logowanie
# (pierwsze niepowodzenie nie wydłuża przerwy - może być jednorazowym
# zacinkiem), z twardym limitem +60 min, żeby nie czekać w nieskończoność.
AUTH_BACKOFF_STEP_SECONDS = 600
AUTH_BACKOFF_MAX_SECONDS = 3600

# Jak często w ogóle SPRAWDZAMY, czy trzeba czyścić (main_loop robi cykl co
# ~40-60 min, więc sprawdzanie przy KAŻDYM cyklu byłoby zbędne - to tylko
# tania kontrola pliku-znacznika, realne czyszczenie odpala się z tego
# maksymalnie raz na tyle godzin).
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

# ────────────────────────────────────────────────
# POPRAWKA #10 – dedykowany lock dla _sent_hashes
# Chroni słownik przed race condition przy współbieżnych gather().
# POPRAWKA (druga runda): threading.Lock zamiast asyncio.Lock - _sent_hashes
# jest czytany/zapisywany zarówno z coroutines (publish_sensor,
# restore_entities_from_cache), jak i z wątku (publish_sensor_sync, wołane z
# run_messages_sync). asyncio.Lock nie nadaje się do ochrony między wątkiem
# a event loopem - działa tylko w obrębie jednej pętli asyncio. threading.Lock
# działa poprawnie w obu kontekstach (ten sam wzorzec co _cache_conn_lock
# niżej) - w coroutines używany jako zwykłe "with" (nie "async with"), bo to
# krótka, nieblokująca sekcja (pojedyncze odczyty/zapisy słownika).
#
# Wcześniejszy komentarz przy publish_sensor_sync zakładał, że
# "asyncio.to_thread serializuje wywołanie" - to nieprawda: to_thread sam w
# sobie niczego nie serializuje, jedynie sekwencyjne await w main_loop
# sprawiało, że w normalnych warunkach te wywołania się nie nakładały. Ale
# przy timeout=600 na asyncio.wait_for(...to_thread(run_messages_sync)...),
# porzucony (nie do zabicia) wątek może kontynuować pisanie do _sent_hashes
# RÓWNOLEGLE z async publish_sensor w KOLEJNYM cyklu - to jest realny,
# potwierdzony wyścig, nie tylko teoretyczny.
# ────────────────────────────────────────────────
_sent_hashes_lock = threading.Lock()

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


def _safe_int(value, default: int = 0) -> int:
    """Bezpieczna konwersja wartości z JSON na int.

    Uwaga: dict.get(klucz, 0) NIE chroni przed nullem - zwraca wartość
    domyślną tylko gdy klucza NIE MA. Gdy klucz istnieje i ma wartość null,
    zwracany jest None, a int(None) rzuca TypeError, który wywalał całą
    sekcję (plan lekcji / frekwencja) danego ucznia w tym cyklu.
    """
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
    """Normalizuje tekst do porównań odpornych na polskie znaki diakrytyczne
    i wielkość liter (np. "Huć" i "HUC" dają to samo "huc"). Zachowuje długość
    i kolejność znaków 1:1, dzięki czemu indeksy w tekście po zwinięciu
    odpowiadają dokładnie indeksom w tekście oryginalnym - to pozwala
    wyciągać oryginalny (z poprawnymi diakrytykami) fragment tekstu po
    dopasowaniu wzorca na wersji zwiniętej.
    """
    return (text or "").lower().translate(_PL_TRANS)

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

def _parse_cal_dt(raw) -> datetime | None:
    """Parsuje pole start/end wydarzenia kalendarza HA.

    Home Assistant REST API zwraca to pole w jednej z dwóch postaci:
      - zagnieżdżony obiekt {"dateTime": "2026-09-07T07:00:00+02:00"} (wydarzenie z godziną)
      - zagnieżdżony obiekt {"date": "2026-09-07"} (wydarzenie całodniowe)
      - lub (w niektórych wersjach/integracjach) gołe stringi w tych samych formatach
    Zwraca None dla wydarzeń całodniowych (brak komponentu czasu) lub dla
    wartości niepoprawnych/pustych - takie wydarzenia są pomijane, bo nie da
    się ich sensownie umieścić w konkretnym slocie planu.
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
# CACHE ENCJI – trwałe połączenie
# _save_to_cache jest wywoływane przy KAŻDEJ publikacji sensora (kilkadziesiąt
# razy na cykl). Otwieranie i zamykanie osobnego połączenia SQLite za każdym
# razem (wraz z dwoma PRAGMA) to zbędne operacje I/O - szczególnie kosztowne
# na karcie SD w Raspberry Pi. Trzymamy jedno połączenie i chronimy je własnym
# threading.Lock, bo funkcja jest wywoływana zarówno z coroutines (publish_sensor),
# jak i z wątku (publish_sensor_sync → run_messages_sync).
# ────────────────────────────────────────────────
_cache_conn: sqlite3.Connection | None = None
_cache_conn_lock = threading.Lock()

# ────────────────────────────────────────────────
# POPRAWKA (WYCOFANA OPTYMALIZACJA): commit natychmiast po KAŻDYM zapisie.
# ────────────────────────────────────────────────
# Wcześniej (w wersji 7.0.3) commit był batchowany co 20 zapisów lub co 5s -
# okazało się to niebezpieczne w praktyce: sprawdzenie "czy minęło już 5s"
# działo się WYŁĄCZNIE przy nadejściu NOWEGO zapisu. Gdy reszta pipeline'u
# utknęła (np. czekając akurat na TĘ SAMĄ blokadę pliku SQLite, którą trzymała
# niezacommitowana partia _cache_conn), żaden nowy zapis nie nadchodził, więc
# nic nie wymuszało commitu - transakcja zostawała otwarta na dziesiątki
# sekund, blokując inne połączenia (sqlite3.OperationalError: database is
# locked w _fetch_schedule/_fetch_frequency/run_messages_sync, zaobserwowane
# na produkcji). Zysk z batchowania (mniej I/O na kartę SD) nie jest wart
# ryzyka takiego zakleszczenia - wracamy do prostego, w pełni przewidywalnego
# zachowania: każdy zapis to osobna, natychmiast zatwierdzona transakcja.
# ────────────────────────────────────────────────

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
                # Połączenie mogło zostać zerwane - zamykamy je i ponawiamy raz
                # na świeżym połączeniu, zanim uznamy zapis za nieudany.
                try:
                    if _cache_conn is not None:
                        _cache_conn.close()
                except Exception:
                    pass
                _cache_conn = None
                if attempt == 1:
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
        # POPRAWKA #10 – zapis wyniku po udanym POST również pod lockiem
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
# HA SENSOR – sync publish (Selenium/wątek wiadomości)
# POPRAWKA: wcześniejszy komentarz zakładał, że "asyncio.to_thread
# serializuje wywołanie" - to nieprawda, to_thread sam w sobie niczego nie
# serializuje. W normalnych warunkach main_loop faktycznie nie nakłada tych
# wywołań (sekwencyjne await), ale przy timeout=600 na
# asyncio.wait_for(...to_thread(run_messages_sync)...) porzucony wątek (nie
# da się go zabić z zewnątrz) może kontynuować pisanie do _sent_hashes
# RÓWNOLEGLE z async publish_sensor w kolejnym cyklu. _sent_hashes_lock jest
# teraz threading.Lock (patrz deklaracja), więc działa poprawnie w obu
# kontekstach - używany tu jako zwykłe "with".
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
                    # POPRAWKA: hash zapisujemy DOPIERO po udanym POST. Wcześniej
                    # trafiał do _sent_hashes przed wysyłką, więc nieudane
                    # odtworzenie (HA jeszcze wstaje, timeout, za duży payload)
                    # trwale blokowało publikację tej encji - publish_sensor
                    # uznawał ją za już wysłaną i pomijał aż do zmiany danych.
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
    except Exception:
        pass


# ────────────────────────────────────────────────
# SELENIUM HELPER
# ────────────────────────────────────────────────

def _log_timezone_info() -> None:
    """Loguje raz, na starcie, jaką strefę czasową i przesunięcie UTC widzi
    Python w tym kontenerze - czysto diagnostyczne, nic nie zmienia w
    działaniu dodatku.

    Cały kod porównujący daty (np. "czy ta lekcja jest dzisiaj") zakłada, że
    datetime.now() zwraca polski czas lokalny - co jest prawdą, o ile
    Home Assistant Supervisor poprawnie wstrzyknął zmienną TZ do kontenera
    (standardowe zachowanie dla instalacji HA OS/Supervised). Przy
    nietypowych środowiskach (np. HA uruchomione poza standardową
    instalacją) to założenie może się nie sprawdzić, a kontener startuje
    wtedy w UTC. Ten log daje twardy dowód przy kolejnych zgłoszeniach,
    zamiast zgadywania.
    """
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
    """Rejestruje ten proces jako "subreaper" (PR_SET_CHILD_SUBREAPER) -
    jądro Linuksa będzie mu automatycznie przypinać dowolne osierocone
    procesy potomne (np. proces chrome, którego bezpośredni rodzic -
    chromedriver - zginął w tej samej chwili co on przy _hard_kill_service),
    NIEZALEŻNIE od tego, czy ten proces jest akurat PID 1 kontenera.

    Bez tego, przekierowanie osieroconych procesów do vultron.py działałoby
    TYLKO dlatego, że dzisiejszy Dockerfile nie ma osobnego systemu init
    (obraz bazowy startuje "python3 vultron.py" bezpośrednio jako PID 1).
    Gdyby to się kiedyś zmieniło (np. dodanie tini/s6-overlay z innych,
    niezwiązanych powodów), sprzątanie osieroconych procesów Chromium
    przestałoby cicho działać, bez żadnego widocznego błędu. To wywołanie
    czyni tę gwarancję jawną i niezależną od takich przyszłych zmian.

    Działa wyłącznie na Linuksie (jedyna platforma, na której działa ten
    dodatek) - błąd jest tylko logowany, nigdy nie przerywa startu dodatku.
    """
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
    """Odbiera (wait()) dowolne już zakończone procesy potomne czekające na
    odebranie - w tym procesy Chromium osierocone i przekierowane do nas
    przez jądro (patrz _become_child_subreaper) po _hard_kill_service().

    Bezpieczne do wywołania w dowolnym momencie: vultron.py nigdzie indziej
    nie tworzy własnych procesów potomnych (jedyne pochodzą z Selenium/
    chromedrivera), więc "odbierz cokolwiek aktualnie czeka" nigdy nie trafi
    w proces niezwiązany z Selenium. Nieblokujące (WNOHANG) - kończy się od
    razu, jeśli nic nie ma do odebrania.
    """
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break  # brak jakichkolwiek procesów potomnych do odebrania
        except Exception as e:
            # Czysto pomocnicza funkcja sprzątająca - błąd tutaj (np. rzadki
            # errno inny niż ECHILD) nigdy nie może zamaskować prawdziwego
            # błędu w kodzie wołającym, więc tylko logujemy i przerywamy.
            logger.debug("[AUTH] Błąd przy odbieraniu procesów potomnych: %s", e)
            break
        if pid == 0:
            break  # są jeszcze żywe dzieci, ale żadne nowe się nie zakończyło
        logger.debug("[AUTH] Odebrano zakończony proces potomny PID %d.", pid)


def _hard_kill_service(service) -> None:
    """Ostateczne zabezpieczenie przed osieroconymi procesami Chromium.

    `driver.quit()`/`service.stop()` proszą chromedrivera o zamknięcie się i
    - w najgorszym razie - wysyłają mu SIGTERM/SIGKILL, ale WYŁĄCZNIE jemu
    samemu. Chromium ma wieloprocesową architekturę nawet w trybie headless
    (główny proces chrome, zygote, renderer, proces GPU) - zabicie samego
    chromedrivera NIE zabija jego dzieci, które zostają osierocone i nadal
    zajmują RAM. To tłumaczy narastające zużycie pamięci między kolejnymi
    awariami logowania na Raspberry Pi 4 (zwłaszcza przy 2GB RAM bez swapu/
    zram) - każdy kolejny cykl ma mniej wolnej pamięci niż poprzedni, aż
    w końcu dochodzi do OOM na poziomie całego hosta.

    WYMAGA, żeby `service` był utworzony z `popen_kw={"start_new_session":
    True}` (patrz _get_driver) - dzięki temu chromedriver i WSZYSCY jego
    potomkowie dzielą jedną, odrębną grupę procesów (PGID == PID
    chromedrivera), więc jedno os.killpg() usuwa całe drzewo naraz,
    niezależnie od tego, ile dokładnie procesów potomnych akurat istnieje
    w danym momencie.

    Bezpieczna do wywołania w KAŻDEJ sytuacji - jeśli proces już nie żyje
    (normalne zamknięcie się powiodło), nie robi nic poza próbą odebrania
    (patrz _reap_orphaned_children) ewentualnych już zakończonych dzieci.
    """
    try:
        process = getattr(service, "process", None)
        if process is None:
            return
        if process.poll() is not None:
            return  # proces już się zakończył - nic do roboty

        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            return  # zdążył umrzeć między poll() a teraz

        # Bezpiecznik: NIGDY nie zabijaj własnej grupy procesów dodatku. Przy
        # popen_kw={"start_new_session": True} PGID chromedrivera zawsze różni
        # się od naszego (setsid() nadaje mu PGID równy jego własnemu PID), więc
        # ten warunek w normalnych warunkach nigdy nie powinien być prawdziwy -
        # to tylko tania asekuracja na wypadek błędu w konfiguracji w przyszłości.
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
            pass  # grupa zdążyła zniknąć sama między sprawdzeniem a killpg - OK
        except Exception as e:
            logger.error("[AUTH] Błąd przy wymuszonym zabiciu grupy procesów chromedrivera: %s", e)
    finally:
        # Zawsze próbujemy odebrać, niezależnie od tego, którą ścieżką funkcja
        # się zakończyła - w tym procesy Chromium osierocone w momencie
        # killpg (gdy chromedriver i chrome giną "jednocześnie", chrome trafia
        # do nas jako subreapera - patrz _become_child_subreaper).
        _reap_orphaned_children()


def _log_available_memory(context: str = "") -> None:
    """Loguje aktualny stan pamięci systemu (z /proc/meminfo) - czysto
    diagnostyczne, nie wpływa w żaden sposób na działanie dodatku.

    Pomaga potwierdzić lub wykluczyć brak RAM-u jako przyczynę zawieszeń
    Selenium na słabszym sprzęcie (np. Raspberry Pi 4 2GB bez swapu/zram),
    zamiast zgadywać na podstawie samego typu błędu w logu - przy kolejnych
    zgłoszeniach będzie można od razu zobaczyć, ile faktycznie wolnej pamięci
    było dostępne w momencie startu Chromium.

    "MemAvailable" (nie "MemFree") to liczba, która realnie odpowiada na
    pytanie "ile pamięci mogę jeszcze bezpiecznie wykorzystać" - uwzględnia
    stronicowanie/cache, który jądro może w razie potrzeby natychmiast oddać.

    Działa wyłącznie na Linuksie (jedyna platforma tego dodatku) - brak
    pliku/błąd parsowania jest tylko cicho logowany (DEBUG), nigdy nie
    przerywa działania dodatku.
    """
    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                if key in ("MemTotal", "MemFree", "MemAvailable"):
                    # Wartości w /proc/meminfo są w kB, z sufiksem " kB".
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
    opts.page_load_strategy = 'eager'  # Oszczędność czasu - ignoruje ładowanie skryptów/obrazków pobocznych

    # Agresywne flagi oszczędzające pamięć RAM i CPU
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
        # Ograniczenie zużycia RAM - Chromium konkuruje o pamięć z samym
        # Home Assistantem, co jest odczuwalne na Raspberry Pi.
        "--renderer-process-limit=1",
        "--js-flags=--max-old-space-size=128",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--disable-background-timer-throttling",
        "--disable-breakpad",
        "--log-level=3",  # Wycisza śmieciowe logi ChromeDrivera w konsoli
        # Dodatkowe wyłączenie procesów pobocznych Chrome, zbędnych przy
        # jednorazowym, headlessowym użyciu (kolejne kilka-kilkanaście % RAM):
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-backgrounding-occluded-windows",
    )
    for arg in flags:
        opts.add_argument(arg)

    opts.binary_location = "/usr/bin/chromium-browser"

    # Przekazujemy logi do os.devnull, aby nie obciążały IO na karcie SD/dysku
    service = Service(
        executable_path="/usr/bin/chromedriver",
        log_path=os.devnull,
        # POPRAWKA: nadaje chromedriverowi (i wszystkim jego potomkom -
        # chrome, zygote, renderer, GPU) WŁASNĄ, odrębną grupę procesów
        # (setsid() -> PGID == PID chromedrivera), zamiast domyślnego
        # dziedziczenia grupy procesu Pythona. Bez tego, po zawieszeniu
        # Chromium, nie da się posprzątać CAŁEGO drzewa procesów jednym
        # sygnałem - zabicie samego chromedrivera zostawia jego dzieci
        # osierocone. Patrz _hard_kill_service.
        popen_kw={"start_new_session": True},
    )

    try:
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception:
        # POPRAWKA: jeśli sama sesja przeglądarki nie powstanie (np. zawieszenie
        # w trakcie tworzenia sesji - dokładnie ten scenariusz z ~120s
        # timeoutów w zgłoszeniach), Selenium wewnętrznie i tak próbuje
        # posprzątać (patrz ChromiumDriver.__init__), ale WYŁĄCZNIE proces
        # chromedrivera - nigdy jego dzieci. Dobijamy więc całą grupę procesów
        # na wszelki wypadek, zanim wyjątek poleci dalej.
        _hard_kill_service(service)
        raise

    try:
        driver.set_page_load_timeout(45)  # Limit 45 sekund zamiast 120
    except Exception:
        # POPRAWKA: jeśli konfiguracja timeoutu zawiedzie już PO wystartowaniu
        # procesu chromium/chromedriver, trzeba go jawnie zamknąć - inaczej
        # zostaje zombie proces (referencja do niego ginie wraz z wyjątkiem).
        try:
            driver.quit()
        except Exception:
            pass
        _hard_kill_service(service)
        raise
    return driver

# ────────────────────────────────────────────────
# SQLITE HELPERS
# ────────────────────────────────────────────────

_DB_DDL =[
    # UWAGA: brak PRIMARY KEY jest celowy. Wcześniejszy
    # PRIMARY KEY(id_kolumny, student_slug, period_id) nie pozwalał
    # przechować dwóch ocen w tej samej kolumnie (poprawa: 3 -> 5) - w bazie
    # zostawała tylko ostatnia, przez co licznik nowych ocen liczył tę samą
    # poprawę w kółko i nigdy nie wracał do zera. Oceny danego okresu są
    # teraz podmieniane w całości (DELETE + INSERT) przy każdym cyklu, co
    # dodatkowo usuwa oceny wycofane po stronie dziennika.
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
        PRIMARY KEY(id, student_slug))"""
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


# Wersja schematu bazy. Podnieś przy każdej zmianie struktury tabel i dopisz
# odpowiedni krok w _db_migrate() - CREATE TABLE IF NOT EXISTS NIE zmienia
# tabeli, która już istnieje, więc bez migracji działające instalacje zostają
# na starym schemacie i zaczynają sypać błędami przy zapisie.
_DB_SCHEMA_VERSION = 1

def _db_migrate(conn: sqlite3.Connection) -> None:
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
    except Exception as e:
        logger.error("Nie udało się odczytać wersji schematu bazy: %s", e)
        return

    if current >= _DB_SCHEMA_VERSION:
        return

    # ── v1: tabela grades bez PRIMARY KEY(id_kolumny, student_slug, period_id) ──
    # Stary klucz gubił drugą ocenę w tej samej kolumnie (poprawy), przez co
    # licznik nowych ocen liczył tę samą poprawę w każdym cyklu.
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
            # Filtr prefiksu jest celowy i spójny z run_setup_ui: bez niego do
            # publicznego katalogu /config/www/vultron trafiał KAŻDY plik .js
            # z /app, a nie tylko karty dodatku.
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
            except Exception:
                pass
            logger.info("Czekam na HA API…")
            await asyncio.sleep(5)

def run_setup_ui() -> None:
    log = logging.getLogger("UI-SETUP")
    version = get_addon_version()
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
            # POPRAWKA: zamykamy gniazdo przed kolejną próbą / wyjściem -
            # inaczej każda nieudana próba handshake'u (po udanym connect())
            # zostawiała otwarte, porzucone gniazdo TCP.
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
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

def run_diary_auth() -> tuple[list | None, list | None]:
    driver = None
    session = httpx.Client(timeout=15)

    try:
        # Chromium działa w trybie --headless (patrz _get_driver), więc NIE
        # potrzebuje serwera X. Wcześniej uruchamiany tu Xvfb (pyvirtualdisplay)
        # był zbędnym procesem zjadającym RAM i CPU przy każdym cyklu - istotne
        # zwłaszcza na Raspberry Pi.
        _log_available_memory("przed Selenium")
        driver = _get_driver()
        try:
            wait = WebDriverWait(driver, 25)

            logger.info("[AUTH] Logowanie…")

            # Mechanizm Retry (maksymalnie 3 próby wczytania strony logowania).
            # POPRAWKA: rozróżniamy DWA różne rodzaje niepowodzenia, które
            # wcześniej były traktowane identycznie:
            #   - TimeoutException (Selenium) - chromedriver ODPOWIEDZIAŁ, po
            #     prostu strona nie zdążyła się załadować w set_page_load_timeout.
            #     To zwykły, przejściowy zacinek sieci/serwera Vulcan - sensowne
            #     ponowienie na TEJ SAMEJ przeglądarce (jak dotychczas).
            #   - dowolny INNY wyjątek (np. ReadTimeoutError na poziomie
            #     LOKALNEGO połączenia z chromedriverem) - to nie problem sieci
            #     do Vulcan, tylko sam proces przeglądarki najprawdopodobniej
            #     się zawiesił (zaobserwowane na RPi4 przy niskim RAM).
            #     Ponawianie na tej samej, martwej instancji marnowałoby kolejne
            #     ~120s x 2 próby zanim i tak skończy się porażką - poddajemy
            #     się od razu i pozwalamy zewnętrznemu finally (patrz niżej)
            #     wywołać _hard_kill_service i posprzątać.
            for attempt in range(3):
                try:
                    driver.get("https://eduvulcan.pl/logowanie")
                    break
                except TimeoutException as e:
                    if attempt < 2:
                        logger.warning("[AUTH] Timeout wczytywania strony. Ponawiam próbę (%d/3)...", attempt + 2)
                        time.sleep(3)
                    else:
                        logger.error("[AUTH] Nie udało się wczytać strony logowania po 3 próbach (timeout ładowania).")
                        raise e
                except Exception as e:
                    logger.error(
                        "[AUTH] Błąd komunikacji z przeglądarką podczas wczytywania strony logowania "
                        "(prawdopodobne zawieszenie Chromium, nie problem sieci) - rezygnuję z ponawiania "
                        "na tej samej instancji: %s", e,
                    )
                    raise

            # Wpisanie loginu (tylko jeśli formularz jest widoczny)
            if "UserName" in driver.page_source:
                wait.until(EC.presence_of_element_located((By.ID, "UserName"))).send_keys(
                    CONFIG.get("username", "") + Keys.ENTER
                )
                time.sleep(1.5)  # Pozostawione celowo na animację przejścia z loginu do hasła

                # Wpisanie hasła
                wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
                    CONFIG.get("password", "") + Keys.ENTER
                )

            # POPRAWKA: EduVulcan przestał automatycznie przekierowywać po
            # zalogowaniu na stronę z kafelkami dziennika - trzeba teraz
            # jawnie wejść na dedykowaną stronę wyboru profilu, na tej samej
            # sesji/ciasteczkach. Bez tego kroku wait.until() niżej czekał
            # w nieskończoność na elementy, których strona logowania po
            # prostu już nie zawiera - stąd "Timed out receiving message
            # from renderer" zamiast normalnego, czytelnego błędu.
            time.sleep(1.5)  # analogicznie do animacji logowania - dajemy sesji chwilę się ustabilizować
            driver.get("https://eduvulcan.pl/dostep-do-dziennika/")

            # Oczekiwanie na kafelki Dziennika
            try:
                link_elements = wait.until(EC.presence_of_all_elements_located(
                    # POPRAWKA: na stronie /dostep-do-dziennika/ jest też link
                    # menu konta "Dostęp do dziennika" (href zawiera samo
                    # słowo "dziennika" w "dostep-do-dziennika") - dopasowanie
                    # po samym @href złapałoby go jako fałszywy, trzeci
                    # "kafelek". Klasa "panel-access__profile" identyfikuje
                    # WYŁĄCZNIE prawdziwe profile uczniów.
                    (By.XPATH, "//a[contains(@class,'panel-access__profile')]")
                ))
                diary_links = [el.get_attribute("href") for el in link_elements]
            except Exception as ex:
                err_dir = "/config/www/vultron"
                os.makedirs(err_dir, exist_ok=True)
                err_path = os.path.join(err_dir, "vultron_auth_error.png")
                if driver:
                    driver.save_screenshot(err_path)
                logger.error("[AUTH] Nie znaleziono kafelka 'Dziennik'. Zrzut ekranu zapisano w: %s", err_path)
                logger.error("[AUTH] Sprawdź błąd wpisując: http://<TWOJE_IP_HA>:8123/local/vultron/vultron_auth_error.png")
                raise ex

            logger.info("[AUTH] Znaleziono %d kafelek/kafelków dziennika.", len(diary_links))

            students: list[dict] = []
            seen_slugs: set = set()

            for link in diary_links:
                driver.get(link)

                # Zamiast czekać 5 sekund, skrypt ruszy dalej natychmiast po zmianie URL.
                # Sprawdzamy tylko prefiks "uczen." - domena po nim może być zarówno
                # współdzieloną "eduvulcan.pl", jak i białoetykietową domeną własną
                # samorządu (patrz regex niżej i komentarz przy DOMAIN_RE).
                try:
                    wait.until(EC.url_contains("uczen."))
                except Exception:
                    logger.debug("[AUTH] Długie ładowanie strony dziennika, aktualny URL: %s", driver.current_url)

                # POPRAWKA: niektóre samorządy hostują Vulcan pod WŁASNĄ domeną
                # (białoetykietowo), np. "uczen.edu.lublin.eu/lublin/..." zamiast
                # współdzielonej "uczen.eduvulcan.pl/{miasto}/...". Wcześniejszy
                # regex zakładał na sztywno "eduvulcan.pl" i całkowicie pomijał
                # takich uczniów (0 uczniów, brak sensora). Teraz wyciągamy
                # OSOBNO domenę (wszystko po "uczen.") i miasto (pierwszy
                # segment ścieżki) - działa identycznie dla obu przypadków,
                # bo "eduvulcan.pl" to tylko jedna z możliwych wartości domeny.
                m = re.search(r"uczen\.([^/]+)/([^/]+)", driver.current_url)
                if not m:
                    logger.error("[AUTH] Brak nazwy domeny/miasta w URL: %s", driver.current_url)
                    continue
                domain, city = m.group(1), m.group(2)

                driver.get(f"https://uczen.{domain}/{city}/api/Context")

                # Czekamy tylko na wyświetlenie dokumentu (JSON), bez stałych przerw
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
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

                city_snapshot = {c["name"]: c["value"] for c in driver.get_cookies()}

                driver.get(f"https://wiadomosci.{domain}/{city}/App")

                # Zamiast czekać 3 sekundy, idziemy dalej od razu po wczytaniu aplikacji wiadomości
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                wiadomosci_snapshot = {c["name"]: c["value"] for c in driver.get_cookies()}

                for name, value in city_snapshot.items():
                    session.cookies.set(name, value)

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
                    res = session.get(
                        f"https://uczen.{domain}/{city}/api/OkresyKlasyfikacyjne",
                        params={"key": key, "idDziennik": id_dz}
                    )
                    # POPRAWKA: brak/pusta lista okresów klasyfikacyjnych NIE
                    # oznacza już pominięcia całego ucznia. Wcześniej `continue`
                    # w tym miejscu wyrzucało dziecko CAŁKOWICIE z synchronizacji
                    # (plan, frekwencja, wiadomości, uwagi) tylko dlatego, że
                    # sekcja ocen nie ma czego pokazać - a to normalny,
                    # oczekiwany stan np. dla dzienników przedszkolnych
                    # (pole "isPrzedszkolak" z /api/Context), które w Vulcanie
                    # nie mają okresów klasyfikacyjnych w ogóle. `_fetch_grades`
                    # już wcześniej bezpiecznie obsługuje pustą listę okresów
                    # (pętla `for period in ...` po prostu nic nie publikuje) -
                    # jedyne, czego brakowało, to żeby uczeń w ogóle dotarł do
                    # tego etapu.
                    curr_p = None
                    is_przedszkolak = bool(u.get("isPrzedszkolak"))

                    if res.status_code != 200:
                        logger.warning("Brak okresów dla: %s (HTTP %d)", u.get("uczen"), res.status_code)
                    else:
                        okresy = res.json()
                        if not isinstance(okresy, list) or not okresy:
                            log_fn = logger.info if is_przedszkolak else logger.warning
                            log_fn(
                                "%s okresów klasyfikacyjnych dla: %s%s - sekcja ocen będzie pusta, "
                                "reszta danych (plan/frekwencja/wiadomości/uwagi) zostanie zsynchronizowana normalnie.",
                                "Brak" if is_przedszkolak else "Nieoczekiwany format",
                                u.get("uczen"),
                                " (dziennik przedszkolny)" if is_przedszkolak else "",
                            )
                        else:
                            # Bezpieczny dostęp: okresy[-1]["id"] rzucał KeyError/TypeError,
                            # gdy ostatni wpis nie miał pola "id" lub nie był słownikiem -
                            # a ten fragment jest poza try, więc przerywał logowanie ucznia.
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
                    _anon_register_student(u.get("uczen") or "", slugify(u.get("uczen", "")), city, domain)
                    logger.info("[AUTH] Uczeń: %s (%s @ %s)", u.get("uczen"), city, domain)

            cookies = driver.get_cookies()
            with open(VUL_PKL, "w", encoding="utf-8") as f:
                # "saved_at" (UTC, ISO 8601) pozwala _try_reuse_cached_session
                # ocenić wiek cache bez zgadywania po mtime pliku (mtime psuje
                # np. przywracanie kopii zapasowej/migracja woluminu Docker).
                json.dump({
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "cookies": cookies,
                    "students": students,
                }, f, ensure_ascii=False)

            logger.info("[AUTH] OK – %d uczniów", len(students))
            return students, cookies
        finally:
            # Przeglądarkę zamykamy TUTAJ, natychmiast po zakończeniu pracy -
            # nie w zewnętrznym finally. Niepełne sprzątanie procesów kończy się
            # zombie chrome/chromedriver, co przy cyklu co ~40-60 min na
            # Raspberry Pi prowadzi do narastającego zużycia RAM/CPU i coraz
            # częstszych timeoutów (dokładnie taki objaw był widoczny w logach:
            # powtarzające się "Read timed out" po pewnym czasie działania).
            try:
                driver.quit()
            except Exception as e:
                logger.debug("[AUTH] Zignorowano błąd przy zamykaniu przeglądarki: %s", e)
            _hard_kill_service(driver.service)
            driver = None  # zapobiega ponownej próbie quit() w zewnętrznym finally

    except PermissionError:
        raise
    except Exception as e:
        logger.error("[AUTH] Błąd: %s", e, exc_info=True)
        return None, None
    finally:
        session.close()
        # Zabezpieczenie awaryjne: normalnie driver jest już zamknięty i ustawiony na None
        # w bloku powyżej. Ten fragment chroni wyłącznie przed skrajnym przypadkiem, gdy
        # wyjątek wystąpiłby zanim wewnętrzny try/finally zdążył się wykonać.
        if driver is not None:
            try:
                driver.quit()
            except Exception as e:
                logger.debug("[AUTH] Zignorowano błąd przy zamykaniu przeglądarki: %s", e)
            _hard_kill_service(driver.service)


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

        subjects: dict[str, list] = {}
        subj_periodic: dict[str, dict] = {}  # przedmiot → oceny okresowe
        new_g = 0

        async with db_lock:
            conn = db_connect()
            try:
                cur = conn.cursor()

                # POPRAWKA: porównujemy PEŁNE wpisy (kolumna, ocena, data), a nie
                # tylko ostatnią ocenę w kolumnie. Kolumna z poprawą zawiera dwie
                # oceny (np. 3 i 5) - przy porównaniu po samej kolumnie jedna z
                # nich zawsze różniła się od zapisanej, więc licznik nowych ocen
                # nigdy nie wracał do zera i automatyzacje "nowa ocena" kłamały.
                cur.execute(
                    "SELECT id_kolumny, ocena, data FROM grades WHERE student_slug=? AND period_id=?",
                    (slug, p_id),
                )
                existing_entries = {(r[0], r[1], r[2]) for r in cur.fetchall()}

                rows_to_insert: list[tuple] = []
                for p_item in (res_g.json().get("ocenyPrzedmioty") or[]):
                    subj = p_item.get("przedmiotNazwa", "Inne")
                    # Zbieramy oceny okresowe i proponowane dla każdego przedmiotu
                    subj_periodic[subj] = {
                        "proponowana": (p_item.get("proponowanaOcenaOkresowa") or "").strip() or None,
                        "okresowa":    (p_item.get("ocenaOkresowa") or "").strip() or None,
                    }
                    # Rejestrujemy przedmiot nawet jeśli nie ma ocen cząstkowych (np. Zachowanie)
                    subjects.setdefault(subj, [])
                    for kol in (p_item.get("kolumnyOcenyCzastkowe") or[]):
                        id_k = str(kol.get("idKolumny", "0"))
                        desc = f"{kol.get('kategoriaKolumny','')}: {kol.get('nazwaKolumny','')}".strip(": ")
                        for o in (kol.get("oceny") or[]):
                            v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
                            if (id_k, v, dt) not in existing_entries:
                                new_g += 1
                            rows_to_insert.append((id_k, slug, subj, v, dt, desc, p_id))
                            subjects.setdefault(subj, []).append({"w": v, "d": dt[:5], "i": clean_text(desc)})

                # Pełna podmiana ocen okresu - usuwa też oceny wycofane w dzienniku.
                # Kasujemy WYŁĄCZNIE gdy API faktycznie zwróciło jakieś oceny;
                # przy pustej lub uszkodzonej odpowiedzi zostawiamy stare dane
                # nietknięte, zamiast skasować całą historię ocen ucznia.
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
            """Mapuje ocenę słowną lub cyfrową na liczbę.
            Dla formatu cyfra/cyfra bierze mniejszą wartość.
            Zwraca None jeśli nie można zmapować. Dodano odpowiednie formy"""
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
            # cyfra 1-6, opcjonalnie z częścią dziesiętną (4.5 / 4,5) lub
            # modyfikatorem +/- (4+ / 5-) - spójne z parsowaniem ocen
            # cząstkowych niżej (regex m_dec dla zmiennej w_str).
            m_dec = re.fullmatch(r"([1-6])(?:[.,](\d+))?([+-])?", s)
            if m_dec:
                v = float(m_dec.group(1))
                if m_dec.group(2):
                    v += float("0." + m_dec.group(2))
                elif m_dec.group(3) == "+":
                    v += 0.5
                elif m_dec.group(3) == "-":
                    v -= 0.25
                # Modyfikator przy skrajnej ocenie (np. "6+", "1-") wyprowadzałby
                # wynik poza skalę 1-6 i zaburzał średnią - przycinamy do skali.
                return min(6.0, max(1.0, v))
            return None

        lista =[]
        prop_vals: list[float] = []   # do średniej proponowanych ocen okresowych
        okr_vals:  list[float] = []   # do średniej końcowych ocen okresowych

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

            periodic = subj_periodic.get(subj_name, {})

            # Mapujemy oceny końcowe – tylko gdy istnieją w odpowiedzi serwera
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
                "oceny":            grades,
                "srednia":          round(sum(vals)/len(vals), 2) if vals else None,
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

    # Zakres tygodni obsługiwany przez kartę: poprzedni + obecny + następny.
    # Ten sam zakres wykorzystujemy do pobierania własnych zajęć z kalendarza HA,
    # żeby okno synchronizacji było spójne z resztą planu.
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
        # Kalendarz HA jest opcjonalny (funkcja "własnych zajęć") - błędy/404
        # nie mogą przerywać pobierania właściwego planu z Vulcan, dlatego
        # obsługujemy go w pełni niezależnie od res_plan/res_free poniżej.
        # WAŻNE: API kalendarzy HA wymaga znaczników czasu ze strefą (RFC3339),
        # dlatego konwertujemy lokalny czas na UTC i dopisujemy 'Z' - dokładnie
        # tak, jak pokazuje dokumentacja Home Assistant.
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
    if not isinstance(res_free, Exception) and res_free.status_code == 200:
        _free_days_raw = res_free.json()
        if isinstance(_free_days_raw, list):
            _free_days = _free_days_raw
    elif isinstance(res_free, Exception):
        logger.warning("[%s] błąd dni wolnych (wyjątek): %s", name, res_free)
    else:
        logger.warning("[%s] błąd dni wolnych: %d", name, res_free.status_code)

    # ────────────────────────────────────────────────
    # WŁASNE ZAJĘCIA (kalendarz HA) - funkcja opcjonalna.
    # Format wydarzenia w kalendarzu: "{Imię}: Nazwa zajęć".
    # Dopasowujemy TYLKO wydarzenia, których tytuł zaczyna się od imienia
    # tego konkretnego ucznia + dwukropek - inaczej wydarzenie jest ignorowane
    # (może należeć do rodzeństwa albo być zwykłym wydarzeniem domowym).
    # Limit MAX_WLASNE_ZAJEC chroni przed nieograniczonym rozrostem encji
    # (np. przez pomyłkowo cykliczne wydarzenie co minutę).
    # ────────────────────────────────────────────────
    MAX_WLASNE_ZAJEC = 200
    cal_entries: list[tuple] = []
    _cal_raw_count = 0
    cal_fetch_ok = False  # True tylko przy pomyślnym pobraniu i sparsowaniu kalendarza -
                          # chroni przed skasowaniem istniejących własnych zajęć przy
                          # chwilowej awarii sieci/API (patrz komentarz przy DELETE niżej).

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
            # Bezwarunkowy zrzut surowych danych - niezależnie od dalszego dopasowania.
            # To jedyny w 100% pewny sposób zobaczenia, co faktycznie zwraca API HA,
            # bez zgadywania po nazwach pól.
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
                # Jeżeli imię jest niejednoznaczne (kolizja z innym uczniem - np. rodzeństwo
                # o tym samym imieniu), wymagamy WYŁĄCZNIE pełnego "Imię Nazwisko:" zamiast
                # samego imienia, żeby jedno wydarzenie nie trafiło przypadkiem do dwójki dzieci.
                # Gdy kolizji nie ma, akceptujemy OBA warianty - i samo imię ("Amelia:"),
                # i pełne imię z nazwiskiem ("Amelia Huć:") - rodzic może wpisać, jak mu wygodniej.
                is_ambiguous = bool(ambiguous_first_names) and _fold_pl(first_name) in ambiguous_first_names
                full_name = name.strip()
                accepted_variants = {full_name} if is_ambiguous else {first_name, full_name}
                # Dopasowanie robimy na znormalizowanej wersji (bez polskich znaków
                # diakrytycznych, bez wielkości liter) - rodzic wpisujący "Huc" zamiast
                # "Huć" (albo "amelia" zamiast "Amelia") wciąż trafi poprawnie. _fold_pl
                # zachowuje długość/indeksy 1:1, więc po dopasowaniu wycinamy właściwy
                # fragment z ORYGINALNEGO (nie zwiniętego) tytułu - z poprawnymi znakami.
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

                        # Wycinamy temat z ORYGINALNEGO tytułu (z poprawnymi diakrytykami),
                        # korzystając z indeksu końca dopasowania na wersji zwiniętej -
                        # translate() zachowuje długość 1:1, więc indeksy się pokrywają.
                        subject = clean_text(summary[pm.end():].strip(), 100) or "Zajęcia"
                        sala    = clean_text(ev.get("location") or "", 50)
                        notatka = clean_text(ev.get("description") or "", 300)
                        # WAŻNE: wydarzenia cykliczne w HA (np. "co wtorek") dzielą wspólne
                        # "uid" całej serii - różni je dopiero "recurrence_id" (lub start,
                        # gdy recurrence_id brak). Użycie samego "uid" jako klucza wiersza
                        # nadpisywałoby przez INSERT OR REPLACE każde kolejne wystąpienie
                        # tej samej serii, zostawiając w planie tylko jeden tydzień zamiast
                        # wszystkich. Dlatego doklejamy identyfikator konkretnego wystąpienia.
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

    # Zawsze widoczne podsumowanie (INFO) - kluczowe do diagnozowania bez
    # włączania trybu debug/trace. Pokazuje ile wydarzeń w ogóle jest w
    # kalendarzu w tym oknie dat i ile z nich dopasowano do tego ucznia.
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
                if "zwolnieni" in inf or "okienko" in inf:
                    st = "ODWOL"
                data_raw   = lesson.get("data", "")
                godz_od    = lesson.get("godzinaOd", "T00:00")
                godz_do    = lesson.get("godzinaDo", "T00:00")
                # Fallback "T00:00" powyżej chroni tylko przed BRAKIEM klucza -
                # gdy klucz istnieje, ale ma wartość bez separatora "T" (albo
                # pustą), .get() zwraca właśnie ją i split("T")[1] rzuca
                # IndexError, wywalając całą sekcję planu. Pomijamy wadliwą lekcję.
                if "T" not in godz_od or "T" not in godz_do:
                    logger.warning(
                        "[%s] pominięto lekcję - nieoczekiwany format godzin (od=%r, do=%r)",
                        name, godz_od, godz_do,
                    )
                    continue
                lessons_to_insert.append(
                    (
                        f"{slug}_{data_raw}_{godz_od}", slug,
                        data_raw.split("T")[0],
                        f"{godz_od.split('T')[1][:5]}-{godz_do.split('T')[1][:5]}",
                        lesson.get("przedmiot") or "Zajęcia",
                        lesson.get("sala") or "", lesson.get("prowadzacy") or "", st,
                    )
                )
            if lessons_to_insert:
                # POPRAWKA: pełna resynchronizacja lekcji z Vulcana w obsługiwanym
                # oknie dat. Wcześniej lekcje były wyłącznie wstawiane, nigdy
                # usuwane - odwołana lekcja zostawała w karcie jako "duch", a
                # przesunięta tworzyła duplikat, bo godzina wchodzi w skład klucza.
                # Kasujemy tylko wpisy z Vulcana (status != WLASNE), żeby nie
                # ruszyć zajęć własnych z kalendarza HA - te mają własną,
                # niezależną logikę resynchronizacji poniżej.
                # Warunek `if lessons_to_insert` jest tu zabezpieczeniem: gdy API
                # nie zwróciło żadnej lekcji, zostawiamy stare dane zamiast
                # wyczyścić cały plan.
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

            # Pełna resynchronizacja własnych zajęć w obrębie obsługiwanego okna dat -
            # WYŁĄCZNIE gdy pobranie kalendarza w tym cyklu się powiodło (cal_fetch_ok).
            # Bez tego warunku chwilowa awaria sieci/API przy pobieraniu kalendarza
            # skasowałaby wszystkie istniejące własne zajęcia bez wstawienia niczego
            # w zamian (cal_entries byłoby puste) - czyli utrata danych przy zwykłym
            # przejściowym błędzie. Gdy fetch się nie uda, zostawiamy stare wpisy
            # nietknięte (mogą być nieaktualne do następnego udanego cyklu, ale to
            # dużo bezpieczniejsze niż ich utrata).
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
                jednostki = fd.get("jednostkiSkladowe", [])
                valid = wszystkie
                if not valid:
                    for j in jednostki:
                        if j.get("id") in student_jednostki:
                            valid = True
                            break
                if valid:
                    # .get(k, "")[:10] nie chroni przed nullem - gdy klucz
                    # istnieje z wartością null, wycinek na None rzuca
                    # TypeError i wywala całą sekcję planu.
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

    # POPRAWKA: _detail wykonuje zapytanie HTTP, więc NIE może być uruchamiane
    # pod db_lock - wcześniej cały równoległy ruch sieciowy (jedno zapytanie na
    # każde zadanie/sprawdzian) odbywał się w sekcji krytycznej bazy, blokując
    # pozostałe sekcje (_fetch_grades, _fetch_remarks itd.) przed zapisem.
    # Teraz funkcja tylko zwraca gotowy wiersz, a zapis idzie jednym
    # executemany pod krótkim lockiem.
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

    # Ruch sieciowy POZA sekcją krytyczną bazy
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
            remarks_to_insert: list[tuple] = []
            for item in res.json():
                item_id = item.get("id")
                if not item_id:
                    continue
                # POPRAWKA: treść uwagi nie była w ogóle oczyszczana z HTML
                # (w przeciwieństwie np. do opisu w terminarzu) - druga warstwa
                # obrony obok escape'owania po stronie karty JS.
                tr    = clean_html(item.get("tresc") or "")
                typ_u = ("pozytywna" if "pochwa" in tr.lower()
                         else "negatywna" if "uwaga" in tr.lower()
                         else "informacja")
                remarks_to_insert.append(
                    (str(item_id), slug, (item.get("data") or "").split("T")[0],
                     tr, item.get("autor") or "", item.get("kategoria") or "",
                     str(item.get("liczbaPunktow") or ""), typ_u)
                )
            if remarks_to_insert:
                cur.executemany(
                    "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                    remarks_to_insert,
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
        # Uwaga: .get("okresy", [0, 0]) NIE chroni przed nullem - gdy klucz
        # istnieje z wartością null, zwracane jest None, a None[0] rzuca
        # TypeError. Analogicznie lista krótsza niż 2 elementy dawała
        # IndexError. Każdy z tych przypadków wywalał całą sekcję statystyk.
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

            if res_f.status_code == 200:
                recs = res_f.json()
                if isinstance(recs, dict):
                    recs = recs.get("oddzialy") or[]
                freq_to_insert: list[tuple] = []
                for fi in recs:
                    fi_data  = fi.get("data", "")
                    fi_godz  = fi.get("godzinaOd", "")
                    if fi_data and fi_godz:
                        # Zabezpieczenie na wypadek, gdyby API zwróciło godzinę
                        # bez separatora "T" (np. "08:00") - wcześniej
                        # split("T")[1] rzucał IndexError i wywalał CAŁĄ sekcję
                        # frekwencji tego ucznia w danym cyklu. Teraz pomijamy
                        # tylko wadliwy wpis. Pętla (a nie list comprehension)
                        # jest tu celowa - dzięki niej reszta wpisów się zapisze.
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
                logger.warning("[%s] błąd frekwencji: %d", name, res_f.status_code)

            if res_fs.status_code == 200:
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
                logger.warning("[%s] błąd statystyk: %d", name, res_fs.status_code)
        finally:
            conn.close()

    if freq_ok:
        # POPRAWKA: stan sensora był na sztywno ustawiony na 0, przez co każda
        # automatyzacja oparta na `state` tej encji była bezużyteczna.
        # Publikujemy liczbę nieobecności nieusprawiedliwionych (kategoria 2)
        # w pobranym oknie - dane są już w atrybucie "wpisy", więc nie wymaga
        # to żadnego dodatkowego zapytania.
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

    async with db_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            ach_to_insert: list[tuple] = []
            for item in res.json():
                item_id = item.get("id")
                if not item_id:
                    continue
                # Wymuszamy pusty string zamiast NULL - karta osiągnięć woła
                # item.tresc.split('\n') bez zabezpieczenia, więc NULL w bazie
                # wywaliłby renderowanie karty po stronie przeglądarki.
                ach_to_insert.append((str(item_id), slug, item.get("tresc") or ""))
            if ach_to_insert:
                cur.executemany("INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                                ach_to_insert)
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
                # POPRAWKA: .get("numer", "Brak") nie chroni przed nullem -
                # gdy klucz istnieje z wartością null, str(None) dawało
                # dosłowny napis "None" jako stan encji.
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
            {"zebrania": lista, "icon": "mdi:account-group"},
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

    # Wykrywanie kolizji imion (np. rodzeństwo/dzieci adoptowane o tym samym
    # imieniu) - dla takich uczniów dopasowanie własnych zajęć z kalendarza
    # musi wymagać PEŁNEGO imienia i nazwiska w tytule wydarzenia, inaczej
    # jedno wydarzenie "Amelia: ..." trafiłoby do obojga dzieci na raz.
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
            # Rejestracja mapowania anonimizacji (patrz _anon_register_student)
            # - musi polecieć TU, nie tylko w run_diary_auth, żeby uczniowie
            # z reużytej sesji (bez świeżego logowania Selenium w tym cyklu)
            # też byli poprawnie zanonimizowani w logu.
            _anon_register_student(s.get("uczen") or "", s.get("slug") or "",
                                    s.get("city") or "", s.get("domain") or "")
            logger.info("=== Synchronizacja: %s ===", s["uczen"])
            # POPRAWKA: domena bazowa nie jest już zakładana na sztywno jako
            # "eduvulcan.pl" - część samorządów hostuje Vulcan pod własną,
            # białoetykietową domeną (patrz run_diary_auth). Fallback na
            # "eduvulcan.pl" chroni WYŁĄCZNIE przed KeyError przy odczycie
            # starego cache sesji (VUL_PKL) zapisanego przed tą zmianą -
            # kolejne pełne logowanie i tak nadpisze go poprawną domeną.
            base = f"https://uczen.{s.get('domain') or 'eduvulcan.pl'}/{s['city']}"
            student_cookies = s.get("city_cookies") or fallback_cookies
            async with httpx.AsyncClient(cookies=student_cookies, timeout=20) as client:

                # --- POPRAWKA: Wymuszenie zmiany kontekstu miasta na serwerze ---
                # To zapobiega błędom 403, gdy sesja serwera utknęła na poprzednim uczniu.
                try:
                    await client.get(base)
                    await client.get(f"{base}/api/Context")
                except Exception as e:
                    logger.debug("Błąd przy odświeżaniu kontekstu: %s", e)
                # ----------------------------------------------------------------

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
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error("Sekcja %d błąd dla %s: %s", i, s["uczen"], r, exc_info=r)
            logger.info("=== Zakończono: %s ===", s["uczen"])


# ────────────────────────────────────────────────
# WIADOMOŚCI (httpx – sync, uruchamiana w wątku)
# POPRAWKA #11 – SQLite chronione przez db_lock_thread (threading.Lock)
# POPRAWKA #13 – Selenium usunięty z tej funkcji.
# Ciasteczka SSO zebrane przez run_diary_auth (city_cookies) działają
# na wszystkich subdomenach TEJ SAMEJ domeny głównej (np. .eduvulcan.pl,
# albo białoetykietowej domeny samorządu jak .edu.lublin.eu - patrz
# run_diary_auth), w tym na subdomenie wiadomości. Każdy uczeń dostaje
# własną sesję httpx z jego city_cookies.
# ────────────────────────────────────────────────

def _build_wiadomosci_session(domain: str, city: str, wiadomosci_cookies: dict) -> httpx.Client | None:
    """
    Buduje świeżą sesję dla wiadomosci.{domain} używając wildcard SSO cookies.
    GET /App powoduje że serwer sam generuje EduVulcan.Wiadomosci.Sso,
    ASP.NET_SessionId i świeże X-V-RequestVerificationToken.
    httpx zapisuje je automatycznie w jar.

    "domain" to domena główna odkryta dynamicznie przy logowaniu (patrz
    run_diary_auth) - zwykle "eduvulcan.pl", ale niektóre samorządy hostują
    Vulcan pod własną, białoetykietową domeną (np. "edu.lublin.eu").

    UWAGA: funkcja na poziomie modułu (nie zagnieżdżona w run_messages_sync),
    żeby móc jej użyć też jako tani "probe" ważności sesji w
    _try_reuse_cached_session, bez duplikowania logiki. Zwrócony obiekt
    httpx.Client trzeba samodzielnie zamknąć (session.close()) po użyciu.
    """
    session = httpx.Client(
        cookies=wiadomosci_cookies,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://wiadomosci.{domain}/{city}/App",
        },
        timeout=15,
    )
    try:
        r = session.get(
            f"https://wiadomosci.{domain}/{city}/App",
            follow_redirects=True,
        )
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
    """Tanie sprawdzenie (bez Selenium), czy zapisane ciasteczka SSO wciąż
    dają dostęp do dziennika (uczen.{domain}/{city}).

    Używane WYŁĄCZNIE do podjęcia decyzji "czy uruchamiać Selenium w tym
    cyklu" - błąd/niepowodzenie NIGDY nie oznacza tu blokady CAPTCHA (to
    rozpoznanie zostaje wyłącznie w run_diary_auth, po realnym logowaniu
    przez przeglądarkę) - zwracamy zwykły bool, nie rzucamy wyjątków.
    """
    if not city_cookies:
        return False
    try:
        r = httpx.get(
            f"https://uczen.{domain}/{city}/api/Context",
            cookies=city_cookies,
            timeout=8,
            follow_redirects=True,
        )
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


def _try_reuse_cached_session() -> tuple[list, list] | None:
    """Próbuje odtworzyć sesję z poprzedniego udanego logowania, bez
    uruchamiania Selenium.

    Wczytuje VUL_PKL (zapisywany po każdym udanym logowaniu w run_diary_auth)
    i - jeśli nie jest starszy niż SESSION_CACHE_MAX_AGE_HOURS - sprawdza
    tanimi zapytaniami httpx (bez przeglądarki), czy zapisane ciasteczka SSO
    wciąż działają OSOBNO dla dziennika i dla wiadomości, dla każdego miasta
    występującego w cache (rodzina może mieć dzieci w różnych miastach/
    szkołach, każde miasto ma własne, niezależnie wygasające ciasteczka).

    Zwraca (students, cookies) tylko gdy WSZYSTKIE miasta przejdą OBA testy.
    W przeciwnym razie zwraca None - wtedy main_loop wykonuje pełne logowanie
    Selenium, które i tak odświeży oba zestawy ciasteczek naraz (to jedna
    sesja SSO, patrz run_diary_auth) - brak ryzyka rozjazdu stanu między
    dziennikiem a wiadomościami.
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
    cookies  = cache.get("cookies") or []
    if not students or not cookies:
        return None

    # Jedna weryfikacja na parę (domena, miasto) - wszyscy uczniowie z tego
    # samego miasta i tej samej domeny mają te same (wildcard SSO) ciasteczka,
    # więc sprawdzanie per-uczeń byłoby tylko powtarzaniem tych samych
    # requestów. Klucz to PARA, nie samo miasto - dwie różne, białoetykietowe
    # domeny mogłyby teoretycznie mieć miasto o tej samej nazwie.
    pairs_seen: set[tuple[str, str]] = set()
    for st in students:
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
    return students, cookies


def run_messages_sync(students_list: list) -> None:
    conn = None

    def _fetch_inbox(session: httpx.Client, domain: str, city: str, gk: str, uczen: str) -> list | None:
        url = (
            f"https://wiadomosci.{domain}/{city}/api/OdebraneSkrzynka"
            f"?globalKeySkrzynka={gk}&idLastWiadomosc=0&pageSize=50"
        )
        for attempt in range(2):
            try:
                res = session.get(url)
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

        # Grupuj uczniów po (domena, miasto) – jedna sesja na parę. Klucz to
        # PARA, nie samo miasto - patrz komentarz w _try_reuse_cached_session
        # (dwie różne, białoetykietowe domeny mogłyby mieć miasto o tej samej
        # nazwie). Fallback "eduvulcan.pl" chroni wyłącznie przed KeyError na
        # starym cache VUL_PKL sprzed wprowadzenia wsparcia dla domen własnych.
        cities: dict[tuple[str, str], list] = {}
        for st in students_list:
            cities.setdefault((st.get("domain") or "eduvulcan.pl", st["city"]), []).append(st)

        # POPRAWKA: cały ruch sieciowy jest teraz POZA sekcją krytyczną bazy.
        # Wcześniej db_lock_thread (i otwarte połączenie SQLite) był trzymany
        # przez cały czas pobierania skrzynek i treści wiadomości - przy kilku
        # uczniach i wolnym łączu to dziesiątki sekund z otwartą transakcją,
        # co blokowało checkpointing WAL i rozdmuchiwało plik -wal.
        # Teraz: krótki lock na odczyt → sieć bez locka → krótki lock na zapis.

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
            # Bierzemy city_cookies od pierwszego ucznia w mieście
            # (wszyscy w tym samym mieście/domenie mają te same wildcard SSO cookies)
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

                    # Nie pobieramy od nowa treści wiadomości, które już mamy
                    # w bazie - tylko aktualizujemy status przeczytania.
                    # Ogranicza to liczbę requestów do serwera co cykl
                    # (ryzyko CAPTCHA) i przyspiesza sync.
                    existing_keys = existing_by_slug.get(assigned, set())

                    for m in messages:
                        m_k = m.get("apiGlobalKey")
                        if not m_k:
                            continue
                        read_flag = 1 if m.get("przeczytana") else 0

                        if m_k in existing_keys:
                            read_updates.append((read_flag, m_k))
                            continue

                        det = session.get(
                            f"https://wiadomosci.{domain}/{city}"
                            f"/api/WiadomoscSzczegoly?apiGlobalKey={m_k}"
                        )
                        if det.status_code == 200:
                            rows_to_insert.append(
                                (m_k, assigned,
                                 m.get("data", ""),
                                 m.get("korespondenci", ""),
                                 m.get("temat", ""),
                                 det.json().get("tresc", "Brak"),
                                 read_flag)
                            )
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

                # Przygotowanie danych sensorów – bez zmian w logice
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
                            "data":        r[0].replace("T", " ")[:16],
                            "nadawca":     r[1],
                            "temat":       r[2],
                            "tresc":       body,
                            "przeczytana": not is_u,
                        })

                    sensor_payloads.append((
                        f"sensor.vultron_wiadomosci_{slug}",
                        unread,
                        f"Wiadomości: {st['uczen']}",
                        {"wiadomosci": msgs, "stats": f"{unread} / {total}"},
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

# Tabele, w których wiek wpisu da się ustalić po kolumnie "data" i które mają
# sens do przycinania.
#
# "achievements" CELOWO pominięte - nie ma w ogóle kolumny z datą, więc nie
# da się określić wieku wpisu bez zgadywania.
#
# "grades" CELOWO WYŁĄCZONE (poprawka po przeglądzie kodu) - w przeciwieństwie
# do danych czysto operacyjnych (plan, frekwencja, wiadomości), oceny to dane,
# które rodzic prawdopodobnie chce mieć w wieloletniej historii (porównanie
# postępów między latami, świadectwa), nie tylko z ostatnich 1,5 roku.
# Włączenie ocen do tej samej retencji co dane operacyjne było błędną decyzją
# projektową - traktujemy je teraz tak samo świadomie jak "achievements".
_PRUNABLE_TABLES = (
    "schedule", "remarks", "timetable", "frequency",
    "free_days", "meetings", "frequency_stats", "lucky_number",
    "messages",
)


def _normalize_date_prefix(raw) -> str | None:
    """Zwraca datę w formacie 'YYYY-MM-DD' albo None, gdy formatu daty w
    danym wierszu nie da się rozpoznać z pewnością LUB gdy data - mimo
    poprawnego formatu - nie jest kalendarzowo poprawna (np. dzień 32,
    miesiąc 13, 29 lutego w roku nieprzestępnym).

    Dane w bazie pochodzą z różnych pól API Vulcan i NIE są jednolicie
    sformatowane - większość tabel ma ISO 8601 ("2026-09-09", czasem z 'T' i
    czasem doklejonym), ale np. "messages.data" bywa w polskim formacie
    kropkowym "09.09.2026" (dowód: normalizacja dat w kartach JS musi
    obsługiwać obie postacie).

    Celowo zwraca None zamiast zgadywać w niejasnych przypadkach (np. brak
    roku w dacie kropkowej, albo niepoprawna kalendarzowo data) - wiersz z
    nierozpoznaną/niepoprawną datą NIGDY nie jest kasowany przez
    _prune_old_data (patrz tam), więc błąd tutaj = "zostaw", nigdy "usuń".

    POPRAWKA: wcześniej sam regex ISO (^\\d{4}-\\d{2}-\\d{2}) był uznawany za
    wystarczające potwierdzenie poprawności - "2026-13-45" przechodziłby bez
    żadnej walidacji. Teraz KAŻDY kandydat (z obu gałęzi) jest dodatkowo
    zweryfikowany przez faktyczne skonstruowanie obiektu date - to jedyny w
    pełni niezawodny sposób walidacji kalendarza w Pythonie (poprawnie
    obsługuje też lata przestępne), zamiast ręcznego sprawdzania zakresów.
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

    # Walidacja kalendarzowa - jedyny niezawodny sposób to próba faktycznego
    # skonstruowania daty (łapie dzień 32, miesiąc 13, 29 lutego poza rokiem
    # przestępnym itd.), zamiast ręcznego sprawdzania zakresów liczbowych.
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None

    return candidate


def _prune_stale_ha_cache(cur: sqlite3.Cursor) -> int:
    """Usuwa z ha_cache wpisy encji nieaktualizowanych od HA_CACHE_RETENTION_DAYS.

    ha_cache nie ma osobnej kolumny z datą - "last_update" żyje wewnątrz
    attributes_json. To BEZPIECZNE do parsowania bez fuzzy-matchingu (jak w
    _normalize_date_prefix), bo w przeciwieństwie do dat z zewnętrznego API
    Vulcan, "last_update" jest generowane WYŁĄCZNIE przez nasz własny kod
    (publish_sensor/publish_sensor_sync) w jednym, stałym formacie
    "%Y-%m-%d %H:%M:%S" - proste porównanie tekstowe jest tu w pełni
    wiarygodne.

    Wiersz jest kasowany TYLKO gdy JSON się poprawnie parsuje I zawiera
    poprawne "last_update" - przy jakiejkolwiek niepewności (uszkodzony JSON,
    brak pola) wiersz zostaje, zgodnie z tą samą zasadą co reszta retencji.

    Zwraca liczbę usuniętych wierszy.
    """
    cutoff = (datetime.now() - timedelta(days=HA_CACHE_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT entity_id, attributes_json FROM ha_cache")

    to_delete: list[tuple[str]] = []
    for entity_id, attrs_json in cur.fetchall():
        try:
            attrs = json.loads(attrs_json)
        except Exception:
            continue  # uszkodzony JSON - nie zgadujemy, wiersz zostaje
        last_update = attrs.get("last_update")
        if not last_update or not isinstance(last_update, str):
            continue
        if last_update < cutoff:
            to_delete.append((entity_id,))

    if to_delete:
        cur.executemany("DELETE FROM ha_cache WHERE entity_id=?", to_delete)
    return len(to_delete)


def _prune_old_data() -> None:
    """Usuwa z bazy wpisy starsze niż RETENTION_DAYS (~1,5 roku) - plus osobno,
    znacznie krócej żyjące wpisy z ha_cache (patrz _prune_stale_ha_cache) - i
    odzyskuje zwolnione miejsce na dysku przez VACUUM.

    BEZPIECZEŃSTWO DANYCH - zabezpieczenia przed usunięciem zbyt wiele:
    1. Wiersz jest kasowany TYLKO gdy jego data da się jednoznacznie
       sparsować I jest kalendarzowo poprawna (patrz _normalize_date_prefix)
       I jest starsza niż próg - przy jakiejkolwiek niepewności wiersz zostaje.
    2. "achievements" jest świadomie POMINIĘTE - brak kolumny z datą
       uniemożliwia bezpieczne ustalenie wieku wpisu.
    3. "grades" jest świadomie WYŁĄCZONE z tej retencji (poprawka po
       przeglądzie kodu) - oceny to dane, które rodzic prawdopodobnie chce
       mieć w wieloletniej historii, w przeciwieństwie do danych czysto
       operacyjnych (plan, frekwencja, wiadomości).
    4. Funkcja jest wywoływana wyłącznie pod db_lock (async) I db_lock_thread
       + _cache_conn_lock (patrz _run_retention_if_due) - żaden inny
       fragment kodu nie zapisuje w tym czasie do bazy, więc VACUUM (który
       wymaga wyłącznego dostępu do pliku) nie koliduje z równoległym
       zapisem i nie ma ryzyka race condition/utraty świeżo zapisanych danych.
    """
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")

    with db_lock_thread, _cache_conn_lock:
        conn = db_connect()
        try:
            cur = conn.cursor()
            total_deleted = 0

            for table in _PRUNABLE_TABLES:
                cur.execute(f"SELECT rowid, data FROM {table}")
                rowids_to_delete = [
                    (rowid,) for rowid, raw_data in cur.fetchall()
                    if (norm := _normalize_date_prefix(raw_data)) is not None and norm < cutoff
                ]
                if rowids_to_delete:
                    cur.executemany(f"DELETE FROM {table} WHERE rowid=?", rowids_to_delete)
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
    """Odpala _prune_old_data maksymalnie raz na RETENTION_CHECK_INTERVAL_HOURS,
    pod db_lock - blokuje na czas czyszczenia WSZYSTKIE inne zapisy do bazy
    (async fetchery przez db_lock, wątek wiadomości i cache HA przez locki
    trzymane wewnątrz _prune_old_data), żeby VACUUM nie kolidował z żadnym
    równoległym zapisem. To krótka, rzadka (raz/dobę) pauza w zapisach, nie
    wpływa odczuwalnie na resztę działania dodatku.
    """
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

async def main_loop() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    mess_timeouts = 0   # licznik kolejnych timeoutów synchronizacji wiadomości
    auth_fail_streak = 0   # licznik kolejnych NIEUDANYCH logowań z rzędu (backoff)

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

            # Zanim odpalimy kosztowne (CPU/RAM) logowanie przez Selenium,
            # sprawdzamy tanio przez httpx, czy sesja z poprzedniego udanego
            # logowania (VUL_PKL) wciąż działa - zarówno dla dziennika, jak
            # i dla wiadomości. To pozwala pominąć całe Chromium w cyklach,
            # w których sesja jeszcze żyje (patrz _try_reuse_cached_session).
            # POPRAWKA: _try_reuse_cached_session() zwraca albo (students,
            # cookies), albo samo None (brak/za stary/nieważny cache) -
            # bezpośrednie rozpakowanie "students, cookies = ..." wywalało
            # TypeError przy None. Rozpakowujemy dopiero po sprawdzeniu.
            cached_session = await asyncio.to_thread(_try_reuse_cached_session)
            students, cookies = cached_session if cached_session else (None, None)

            if students and cookies:
                logger.info("--> Logowanie poprzez COOKIES - OK")
                logger.info("=== Reużyto zapisanej sesji – logowanie Selenium pominięte w tym cyklu ===")
            else:
                logger.info("--> Logowanie poprzez COOKIES - NO - USE CHROMIUM")
                try:
                    students, cookies = await asyncio.wait_for(
                        asyncio.to_thread(run_diary_auth), timeout=600
                    )
                except PermissionError as e:
                    if "CAPTCHA_BLOKADA" in str(e):
                        logger.critical("!!! ZATRZYMUJĘ DODATEK Z POWODU BLOKADY (CAPTCHA) !!!")
                        # POPRAWKA #12 – graceful shutdown zamiast sys.exit() w coroutine
                        # sys.exit() przerywał event loop bez czyszczenia zasobów
                        stop_event.set()
                        break
                    students, cookies = None, None
                except asyncio.TimeoutError:
                    # POPRAWKA: wątku wykonującego Selenium nie da się bezpiecznie
                    # przerwać z zewnątrz - jeśli chromedriver się zawiesił, ten
                    # wątek już nigdy się nie zakończy. Zwykłe sys.exit()/return
                    # też nie pomoże, bo Python przy zamykaniu i tak czeka na
                    # dołączenie (join) tego wątku. Jedyne wyjście to natychmiastowe,
                    # twarde zakończenie procesu - Supervisor HA (boot: auto)
                    # zrestartuje kontener od zera.
                    logger.critical(
                        "!!! Logowanie (Selenium) nie zakończyło się w ciągu 10 minut - "
                        "prawdopodobne zawieszenie chromedrivera. Wymuszam twarde "
                        "zakończenie procesu, aby Supervisor zrestartował dodatek. !!!"
                    )
                    os._exit(1)
                except Exception as e:
                    logger.error("Nieoczekiwany błąd podczas logowania: %s", e)
                    students, cookies = None, None

            # Backoff: liczymy TYLKO nieudane pełne logowania (Selenium) -
            # sukces przez reużycie cookies i sukces świeżego logowania
            # jednakowo zerują licznik, bo oba oznaczają "sesja działa".
            if students and cookies:
                auth_fail_streak = 0
            else:
                auth_fail_streak += 1

            if students and cookies:
                # Sprawdzenie sygnału zatrzymania między etapami cyklu - bez tego
                # SIGTERM otrzymany w trakcie pobierania danych był ignorowany aż
                # do końca całego cyklu (Supervisor po chwili wysyłał SIGKILL,
                # czyli deklarowany graceful shutdown w praktyce nie działał).
                if stop_event.is_set():
                    logger.info("Otrzymano sygnał zatrzymania – przerywam cykl.")
                    break

                await sync_diary_data(students, cookies)

                if stop_event.is_set():
                    logger.info("Otrzymano sygnał zatrzymania – pomijam wiadomości.")
                    break

                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(run_messages_sync, students), timeout=600
                    )
                    mess_timeouts = 0
                except asyncio.TimeoutError:
                    # Wątku nie da się przerwać z zewnątrz - po timeoucie działa
                    # dalej i na stałe zajmuje slot w puli asyncio.to_thread
                    # (domyślnie min(32, liczba_rdzeni+4), czyli 8 na RPi 4).
                    # Wyczerpanie puli zablokowałoby kolejne to_thread na zawsze,
                    # więc po kilku z rzędu wymuszamy restart dodatku, zanim do
                    # tego dojdzie. Pojedynczy timeout tylko logujemy i lecimy dalej.
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

            if stop_event.is_set():
                break

            await _run_size_monitor(ha)
            await _run_retention_if_due()

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

            # Backoff po kolejnych nieudanych logowaniach z rzędu - pierwsze
            # niepowodzenie nie wydłuża przerwy (może być jednorazowym
            # zacinkiem), każde KOLEJNE dokłada +10 min, do twardego limitu
            # +60 min - zamiast dobijać się co ~40-60 min bez końca przy
            # uporczywym problemie, zwiększając ryzyko kolejnej CAPTCHA.
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

    # Domknięcie trwałego połączenia cache - pozwala SQLite wykonać checkpoint
    # WAL i nie zostawić niedomkniętego pliku przy zatrzymaniu dodatku.
    # Każdy zapis jest już zatwierdzany natychmiast (patrz _save_to_cache),
    # więc nie ma tu żadnej niezacommitowanej partii do wymuszenia.
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