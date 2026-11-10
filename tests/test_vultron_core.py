"""Testy czystych funkcji vultron.py (eduVULCAN): przycinanie atrybutów, daty,
kasowanie wpisów usuniętych w dzienniku, retencja, czyszczenie HTML.

vultron.py przy imporcie czyta /data/options.json, pisze log do /data i wymaga
tokena HA. W CI plik przygotowuje gdansk-tests.yml. Testy NIGDY nie zapisują
/data/options.json (w prawdziwym kontenerze to konfiguracja użytkownika), a
każdy test bazy działa na własnym pliku tymczasowym (podmieniony DB_PATH)."""
import asyncio
import base64
import importlib.util
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

SRC = Path(__file__).resolve().parents[1] / "vultron" / "vultron.py"


@pytest.fixture(scope="module")
def v():
    if not (os.path.isfile("/data/options.json") and os.access("/data", os.W_OK)):
        msg = "brak /data/options.json lub /data niezapisywalne (CI: krok w gdansk-tests.yml)"
        if os.getenv("GITHUB_ACTIONS"):
            pytest.fail(msg)
        pytest.skip(msg)
    env = {"HA_TOKEN": "test-token", "HA_URL": "http://127.0.0.1:9/api",
           "HA_WS_URL": "ws://127.0.0.1:9/api/websocket"}
    saved = {k: os.environ.get(k) for k in [*env, "SUPERVISOR_TOKEN"]}
    os.environ.update(env)
    os.environ.pop("SUPERVISOR_TOKEN", None)
    try:
        spec = importlib.util.spec_from_file_location("vultron_under_test", SRC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
    return mod


@pytest.fixture
def db(v, tmp_path, monkeypatch):
    """Świeża baza z pełnym schematem; DB_PATH modułu wskazuje na nią."""
    path = str(tmp_path / "vultron.db")
    monkeypatch.setattr(v, "DB_PATH", path)
    monkeypatch.setattr(v, "_cache_conn", None)
    conn = sqlite3.connect(path)
    v.db_init(conn)
    conn.commit()
    yield conn
    conn.close()
    if v._cache_conn is not None:
        v._cache_conn.close()


# Stałe zapytania (bez SQL sklejanego w locie, żeby skanery nie wymagały wyjątków).
_INSERT_SQL = {
    "remarks": "INSERT INTO remarks VALUES (?,?,?,?,?,?,?,?)",
    "messages": "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
    "achievements": "INSERT INTO achievements VALUES (?,?,?)",
    "grades": "INSERT INTO grades VALUES (?,?,?,?,?,?,?)",
}


def _insert(conn, table, values_by_col):
    cols = [r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))]
    sql = _INSERT_SQL[table]
    assert sql.count("?") == len(cols), f"schemat {table} się zmienił: {cols}"
    conn.execute(sql, [values_by_col.get(c, "x") for c in cols])


# ── proste funkcje pomocnicze ──────────────────────────────────────────────

def test_slugify(v):
    assert v.slugify("Zażółć Gęślą Jaźń") == "zazolc_gesla_jazn"
    assert v.slugify("  Jan-Kowalski 2b ") == "jan_kowalski_2b"
    assert v.slugify("") == "unknown"
    assert v.slugify(None) == "unknown"


def test_safe_int(v):
    assert v._safe_int("5") == 5
    assert v._safe_int(None) == 0
    assert v._safe_int("abc") == 0
    assert v._safe_int(None, default=-1) == -1


@pytest.mark.parametrize("raw, expected", [
    ("2026-09-09", "2026-09-09"),
    ("2026-10-29T07:38:50.85+01:00", "2026-10-29"),
    ("9.9.2026", "2026-09-09"),
    ("09.09.26", "2026-09-09"),
    ("29.02.2024", "2024-02-29"),
    ("29.02.2025", None),      # nie ma takiego dnia
    ("2026-13-45", None),      # niepoprawna kalendarzowo
    ("09.09.", None),          # brak roku - nie zgadujemy
    ("zła data", None),
    ("", None),
    (None, None),
    (20260909, None),
])
def test_normalize_date_prefix(v, raw, expected):
    assert v._normalize_date_prefix(raw) == expected


def test_min_api_date(v):
    items = [{"data": "2026-11-05T10:00"}, {"data": "3.11.2026"}, {"data": None},
             {"data": "śmieci"}, "nie-słownik"]
    assert v._min_api_date(items, "data") == "2026-11-03"
    assert v._min_api_date([{"data": None}], "data") is None
    assert v._min_api_date([], "data") is None


def test_date_or_empty(v):
    assert v._date_or_empty("2026-11-05T10:00:00") == "2026-11-05"
    assert v._date_or_empty("5.11.2026") == "5.11.2026"
    assert v._date_or_empty("<b>x</b>") == ""
    assert v._date_or_empty(None) == ""


def test_clean_text(v):
    assert v.clean_text("a\nb\r") == "a b"
    assert v.clean_text("x" * 300) == "x" * 197 + "..."
    assert v.clean_text(None) == ""


def test_clean_html_text_and_unsafe_links(v):
    out = v.clean_html("<p>Pierwszy</p><p>Drugi</p>")
    assert "Pierwszy" in out and "Drugi" in out and "\n" in out
    out = v.clean_html('<a href="javascript:alert(1)">klik</a> <img src="data:image/png;base64,AAA">')
    assert "klik" in out
    assert "javascript:" not in out and "data:" not in out
    out = v.clean_html('<a href="https://example.com/x">link</a>')
    assert "https://example.com/x" in out
    assert v.clean_html("") == "Brak opisu"
    assert v.clean_html("<p> </p>") == "Brak opisu"


# ── limity rozmiaru atrybutów HA ───────────────────────────────────────────

def _size(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def test_fit_attr_list_small_list_unchanged(v):
    items = [{"id": i, "t": "krótko"} for i in range(5)]
    kept, skipped = v._fit_attr_list(items)
    assert kept == items and skipped == 0


def test_fit_attr_list_keeps_order_and_limit(v):
    items = [{"id": i, "t": "ż" * 500} for i in range(60)]
    kept, skipped = v._fit_attr_list(items)
    assert kept == items[:len(kept)]          # obcina od końca (najmniej ważne)
    assert skipped == len(items) - len(kept) > 0
    assert _size(kept) <= v.ATTR_LIST_MAX_BYTES


def _msgs(n_unread):
    body = ("Zażółć gęślą jaźń " * 120)[:1997] + "..."
    return [{"data": f"2026-11-{10 - i:02d} 08:00", "nadawca": "N", "temat": f"T{i}",
             "tresc": body if i < n_unread else "", "przeczytana": i >= n_unread}
            for i in range(10)]


def _headers(msgs):
    return [(m["data"], m["nadawca"], m["temat"], m["przeczytana"]) for m in msgs]


def test_fit_messages_small_inbox_unchanged(v):
    msgs = _msgs(2)
    kept, cut = v._fit_messages(json.loads(json.dumps(msgs)))
    assert kept == msgs and cut == 0


def test_fit_messages_clears_oldest_bodies_keeps_headers(v):
    msgs = _msgs(10)
    kept, cut = v._fit_messages(json.loads(json.dumps(msgs)))
    assert len(kept) == 10
    assert _size(kept) <= v.ATTR_LIST_MAX_BYTES
    # Nagłówki bez zmian (po nich automatyzacje wykrywają nowe wiadomości).
    assert _headers(kept) == _headers(msgs)
    # Czyszczone od najstarszych: najpierw pełne treści, potem puste.
    has_body = [bool(m["tresc"]) for m in kept]
    assert has_body == sorted(has_body, reverse=True)
    assert cut == has_body.count(False) > 0 and has_body[0]


# ── kasowanie wpisów usuniętych w dzienniku ────────────────────────────────

def test_delete_missing(v, db):
    for slug in ("jan", "ola"):
        for rid, d in [("r1", "2026-10-01"), ("r2", "2026-10-15"), ("r3", "2026-09-01"),
                       ("r4", "zła data"), ("r5", "2026-11-20")]:
            _insert(db, "remarks", {"remark_id": f"{slug}{rid}", "student_slug": slug, "data": d})
    db.commit()
    cur = db.cursor()
    # API zwróciło tylko r1 w zakresie 2026-10-01..2026-10-31.
    n = v._delete_missing(cur, "remarks", "remark_id", "jan", {"janr1"}, "2026-10-01", "2026-10-31")
    db.commit()
    assert n == 1
    left = {r[0] for r in db.execute("SELECT remark_id FROM remarks WHERE student_slug='jan'")}
    # r2 skasowana; r3/r5 poza zakresem dat i r4 (nierozpoznana data) zostają.
    assert left == {"janr1", "janr3", "janr4", "janr5"}
    assert db.execute("SELECT COUNT(*) FROM remarks WHERE student_slug='ola'").fetchone()[0] == 5


def test_delete_missing_unknown_table(v, db):
    with pytest.raises(KeyError):
        v._delete_missing(db.cursor(), "grades", "id", "jan", set())


# ── retencja ───────────────────────────────────────────────────────────────

def test_prune_old_data(v, db):
    # Lokalna data, jak w _prune_old_data (astimezone: ta sama data, ale ze strefą).
    now = datetime.now().astimezone()
    old = (now - timedelta(days=v.RETENTION_DAYS + 30)).strftime("%Y-%m-%d")
    new = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    y, m, d = old.split("-")
    old_dotted = f"{d}.{m}.{y}"
    for i, d in enumerate([old, new, old_dotted, "zła data"]):
        _insert(db, "remarks", {"remark_id": f"r{i}", "student_slug": "jan", "data": d})
        _insert(db, "messages", {"key": f"m{i}", "student_slug": "jan", "data": d + "T08:00:00"})
    _insert(db, "achievements", {"achievement_id": "a1", "student_slug": "jan"})
    _insert(db, "grades", {"student_slug": "jan", "data": old})   # grades: retencja pomija
    db.commit()
    grades_before = db.execute("SELECT COUNT(*) FROM grades").fetchone()[0]

    v._prune_old_data()

    remarks = {r[0] for r in db.execute("SELECT remark_id FROM remarks")}
    assert remarks == {"r1", "r3"}            # stare (ISO i kropkowe) skasowane, nierozpoznana zostaje
    messages = {r[0] for r in db.execute("SELECT key FROM messages")}
    assert messages == {"m1", "m3"}
    assert db.execute("SELECT COUNT(*) FROM achievements").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == grades_before


# ── fetchery na nietypowych danych z API (null, zły typ) ───────────────────
# Atrapa klienta zwraca odpowiedź po fragmencie adresu; publish_sensor jest
# przechwycony, więc nic nie wychodzi do HA.


class _Res:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._data


class _Client:
    def __init__(self, routes):
        self.routes = routes

    async def get(self, url, params=None, **kwargs):
        for part, data in self.routes.items():
            if part in url:
                return data if isinstance(data, _Res) else _Res(data)
        return _Res({}, 404)


@pytest.fixture
def published(v, monkeypatch, db):
    out = {}

    async def fake_publish(ha, entity, state, name, attrs):
        out[entity] = (state, attrs)
    monkeypatch.setattr(v, "publish_sensor", fake_publish)
    return out


STUDENT = {"slug": "jan", "key": "k", "uczen": "Jan Kowalski", "idDziennik": 1}


def _today_iso():
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def test_schedule_null_hours_skip_only_that_lesson(v, published):
    day = _today_iso() + "T00:00:00"
    lessons = [
        {"data": day, "godzinaOd": None, "godzinaDo": day[:11] + "08:45:00", "przedmiot": "Zły"},
        {"data": day, "godzinaOd": day[:11] + "09:00:00", "godzinaDo": None, "przedmiot": "Zły2"},
        {"data": day, "godzinaOd": day[:11] + "10:00:00", "godzinaDo": day[:11] + "10:45:00",
         "przedmiot": "Matematyka", "prowadzacy": "N", "sala": "1"},
    ]
    free = [{"dataOd": day, "dataDo": day, "nazwa": "Wolne bez jednostek",
             "wszystkieSkladowe": False, "jednostkiSkladowe": None},
            {"dataOd": day, "dataDo": day, "nazwa": "Wolne dla wszystkich",
             "wszystkieSkladowe": True, "jednostkiSkladowe": None}]
    client = _Client({"/api/PlanZajec": lessons, "/api/DniWolne": free})
    asyncio.run(v._fetch_schedule(client, _Client({}), "https://x", STUDENT))
    _, attrs = published["sensor.vultron_plan_jan_curr"]
    assert [lekcja["p"] for lekcja in attrs["lekcje"]] == ["Matematyka"]
    assert [d["n"] for d in attrs["dni_wolne"]] == ["Wolne dla wszystkich"]


def test_preschool_plan_null_hours_skip_only_that_entry(v, published):
    day = _today_iso() + "T00:00:00"
    zajecia = [{"data": day, "godzinaOd": None, "godzinaDo": None, "przedmiot": "Zły"},
               {"data": day, "godzinaOd": day[:11] + "08:00:00", "godzinaDo": day[:11] + "09:00:00",
                "przedmiot": "Rytmika", "prowadzacy": "N"}]
    asyncio.run(v._fetch_przedszkole_plan(_Client({"/api/PlanZajec": zajecia}), None, "https://x", STUDENT))
    _, attrs = published["sensor.vultron_przedszkole_plan_jan_curr"]
    assert [z["z"] for z in attrs["zajecia"]] == ["Rytmika"]


def test_achievements_unexpected_shapes(v, published):
    asyncio.run(v._fetch_achievements(_Client({"/api/Osiagniecia": {"blad": "x"}}), None, "https://x", STUDENT))
    assert "sensor.vultron_osiagniecia_jan" not in published   # nie lista: bez publikacji, bez wyjątku
    items = ["tekst", None, {"id": 7, "tresc": "Konkurs"}]
    asyncio.run(v._fetch_achievements(_Client({"/api/Osiagniecia": items}), None, "https://x", STUDENT))
    state, attrs = published["sensor.vultron_osiagniecia_jan"]
    assert state == 1 and attrs["osiagniecia"] == [{"id": "7", "tresc": "Konkurs"}]


def test_menu_without_preschool_diet_is_logged(v, published, caplog):
    day = _today_iso() + "T00:00:00"
    menu = [{"id": 1, "data": day, "dieta": "Dzieci szkoła", "posilki": [{"nazwa": "Obiad"}]}]
    with caplog.at_level("INFO", logger=v.logger.name):
        asyncio.run(v._fetch_przedszkole_jadlospis(_Client({"/api/Jadlospis": menu}), None, "https://x", STUDENT))
    assert "dzieci szkoła" in caplog.text
    state, attrs = published["sensor.vultron_przedszkole_jadlospis_jan"]
    assert state == 0 and attrs["dni"] == {}


def test_menu_with_preschool_diet_not_logged(v, published, caplog):
    day = _today_iso() + "T00:00:00"
    menu = [{"id": 1, "data": day, "dieta": "Dzieci przedszkole", "posilki": [{"nazwa": "Obiad"}]},
            {"id": 2, "data": day, "dieta": "Dzieci szkoła", "posilki": [{"nazwa": "Obiad2"}]}]
    with caplog.at_level("INFO", logger=v.logger.name):
        asyncio.run(v._fetch_przedszkole_jadlospis(_Client({"/api/Jadlospis": menu}), None, "https://x", STUDENT))
    assert "żadna dieta" not in caplog.text
    state, attrs = published["sensor.vultron_przedszkole_jadlospis_jan"]
    assert state == 1 and [p["nazwa"] for p in attrs["dni"][_today_iso()]] == ["Obiad"]


# ── terminarz: błąd szczegółów nie nadpisuje zapisanego wpisu ─────────────


class _Raise:
    """Trasa atrapy, która zamiast odpowiedzi rzuca błąd sieci."""


class _FlakyClient(_Client):
    async def get(self, url, params=None, **kwargs):
        for part, data in self.routes.items():
            if part in url and isinstance(data, _Raise):
                raise httpx.ConnectError("brak sieci")
        return await super().get(url, params, **kwargs)



def _timetable_run(v, published, details):
    day = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT00:00:00")
    lista = [{"id": 11, "typ": 1, "data": day, "przedmiotNazwa": "Fizyka", "nauczycielImieNazwisko": "Anna Nowak"}]
    client = _FlakyClient({"/api/SprawdzianyZadaniaDomowe": lista, "/api/SprawdzianSzczegoly": details})
    asyncio.run(v._fetch_timetable(client, None, "https://x", STUDENT))
    return published["sensor.vultron_terminarz_jan"][1]["lista"]


def test_timetable_detail_error_keeps_saved_entry(v, published):
    ok = {"opis": "Dział 3: Ruch", "przedmiotNazwa": "Fizyka", "nauczycielImieNazwisko": "Anna Nowak"}
    assert [w["opis"] for w in _timetable_run(v, published, ok)] == ["Dział 3: Ruch"]
    # Odpowiedź inna niż 200 i błąd sieci: wpis zostaje z pełnym opisem.
    assert [w["opis"] for w in _timetable_run(v, published, _Res({}, 500))] == ["Dział 3: Ruch"]
    assert [w["opis"] for w in _timetable_run(v, published, _Raise())] == ["Dział 3: Ruch"]
    # Szczegóły znów działają i nauczyciel zmienił opis: zmiana trafia do encji.
    ok2 = dict(ok, opis="Dział 3 i 4")
    assert [w["opis"] for w in _timetable_run(v, published, ok2)] == ["Dział 3 i 4"]


def test_timetable_new_entry_without_details_still_listed(v, published):
    # Nowy wpis bez szczegółów pojawia się od razu (z danych listy), a pełny opis
    # dochodzi, gdy szczegóły się pobiorą.
    assert [(w["przedmiot"], w["opis"]) for w in _timetable_run(v, published, _Raise())] == [("Fizyka", "Brak opisu")]
    ok = {"opis": "Dział 3: Ruch"}
    assert [w["opis"] for w in _timetable_run(v, published, ok)] == ["Dział 3: Ruch"]


# ── GPE: odrzucone logowanie (Docker nie może logować się w kółko) ──

def _run_gpe_loop(v, monkeypatch, published, *, is_addon, outcomes, expect_calls):
    """Uruchamia gdansk_main_loop z podmienionym logowaniem GPE. outcomes:
    kolejne wyniki authenticate() ("auth" = AuthenticationError, "ok" = sukces).
    Zwraca (liczba wywołań authenticate, czy pętla skończyła się sama)."""
    monkeypatch.syspath_prepend(str(SRC.parent))
    import gdansk
    real_asyncio = asyncio
    events = []
    calls = []

    def fake_auth(config, driver_factory, cleanup):
        calls.append(1)
        if outcomes[len(calls) - 1] == "auth":
            raise gdansk.AuthenticationError("Logowanie GPE nie powiodło się.")
        return [], None

    class _Event(real_asyncio.Event):
        def __init__(self):
            super().__init__()
            events.append(self)

    async def fake_wait_for(aw, timeout):
        # Odliczanie przerwy między cyklami (timeout=10) bez czekania 40-60 min.
        if timeout == 10:
            aw.close()
            await real_asyncio.sleep(0)
            if events and events[0].is_set():
                return None
            raise real_asyncio.TimeoutError
        return await real_asyncio.wait_for(aw, timeout)

    proxy = SimpleNamespace(Event=_Event, wait_for=fake_wait_for, to_thread=real_asyncio.to_thread,
                            get_running_loop=real_asyncio.get_running_loop,
                            TimeoutError=real_asyncio.TimeoutError)

    async def anoop(*a, **k):
        return None

    monkeypatch.setattr(gdansk, "authenticate", fake_auth)
    monkeypatch.setattr(gdansk, "snapshot", lambda modules, messages, config: ([], []))
    monkeypatch.setattr(v, "asyncio", proxy)
    monkeypatch.setattr(v, "IS_ADDON", is_addon)
    monkeypatch.setattr(v, "_test_mode", True)
    for name in ("_become_child_subreaper", "_log_timezone_info", "copy_resources", "run_setup_ui"):
        monkeypatch.setattr(v, name, lambda *a, **k: None)
    for name in ("wait_for_ha_api", "restore_entities_from_cache", "_run_size_monitor", "check_and_restore"):
        monkeypatch.setattr(v, name, anoop)

    async def scenario():
        task = real_asyncio.create_task(v.gdansk_main_loop())
        for _ in range(300):
            await real_asyncio.sleep(0.01)
            if task.done() or len(calls) >= expect_calls:
                break
        await real_asyncio.sleep(0.2)   # czas na ewentualną kolejną (błędną) próbę
        finished = task.done()
        if not finished:
            events[0].set()             # SIGTERM / docker compose restart
            await real_asyncio.wait_for(task, 5)
        return len(calls), finished

    return real_asyncio.run(scenario())


def test_gpe_addon_stops_after_first_rejection(v, monkeypatch, published):
    # Dodatek HA: bez zmian - pierwsze odrzucenie kończy pętlę (Supervisor nie restartuje).
    n, finished = _run_gpe_loop(v, monkeypatch, published, is_addon=True,
                                outcomes=["auth"] * 5, expect_calls=1)
    assert (n, finished) == (1, True)
    assert published["sensor.vultron_gdansk_status"][0] == "authentication_error"


def test_gpe_docker_waits_after_two_rejections(v, monkeypatch, published):
    # Docker: druga próba w następnym cyklu, potem czekanie na SIGTERM zamiast
    # wyjścia z procesu (restart: unless-stopped logowałby złym hasłem w kółko).
    n, finished = _run_gpe_loop(v, monkeypatch, published, is_addon=False,
                                outcomes=["auth"] * 5, expect_calls=2)
    assert (n, finished) == (2, False)
    assert published["sensor.vultron_gdansk_status"][0] == "authentication_error"


def test_gpe_docker_success_resets_rejection_counter(v, monkeypatch, published):
    # Udane logowanie pomiędzy zeruje licznik: pojedynczy błąd nie zatrzymuje kontenera.
    n, finished = _run_gpe_loop(v, monkeypatch, published, is_addon=False,
                                outcomes=["auth", "ok", "auth", "auth", "auth"], expect_calls=4)
    assert (n, finished) == (4, False)


def test_ha_restart_republishes_entity_missing_from_cache(v, db, monkeypatch):
    # Encja bez zmian przez HA_CACHE_RETENTION_DAYS znika z ha_cache. Po restarcie HA
    # nie ma jej czym odtworzyć, więc następny cykl musi ją opublikować mimo
    # niezmienionych danych (wcześniej blokował to zapamiętany hash).
    posts = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    class _HA:
        async def post(self, url, **kwargs):
            posts.append(url.rsplit("/", 1)[-1])
            return _Resp(200)

        async def get(self, url, **kwargs):
            return _Resp(404)  # HA po restarcie nie zna encji

    monkeypatch.setattr(v, "_sent_hashes", {})
    ha = _HA()
    entity, attrs = "sensor.vultron_uwagi_jan", {"uwagi": []}
    asyncio.run(v.publish_sensor(ha, entity, 0, "Uwagi", attrs))

    stale = json.dumps({"friendly_name": "Uwagi", "last_update": "2020-01-01 00:00:00", **attrs})
    db.execute("UPDATE ha_cache SET attributes_json=? WHERE entity_id=?", (stale, entity))
    db.commit()
    assert v._prune_stale_ha_cache(db.cursor()) == 1
    db.commit()

    posts.clear()
    asyncio.run(v.check_and_restore(ha))
    assert posts == []  # nie było czego odtworzyć
    asyncio.run(v.publish_sensor(ha, entity, 0, "Uwagi", attrs))
    assert posts == [entity]



# --- eksperyment API mobilnego (_mobile_api_probe) ---

def _ap_jwt(claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


_AP_TOKEN = _ap_jwt({"name": "Jan Kowalski", "tenant": "warszawa", "service": "False",
                     "caps": "[]", "iat": 1000, "exp": 4600})
_AP_RAW = json.dumps({"Tokens": [_AP_TOKEN], "Email": "jan@example.com", "Surname": "Kowalski",
                      "IsConsentAccepted": True, "Success": True})


class _ApDriver:
    def __init__(self, value):
        self.value, self.visited = value, []

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, value):
        assert value == "ap"
        return SimpleNamespace(get_attribute=lambda name: self.value)


@pytest.fixture
def probe(v, tmp_path, monkeypatch):
    """Uruchamia _mobile_api_probe z atrapą serwera; zwraca (wywołania, logi)."""
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    real_client = httpx.Client

    def run(handler, raw=_AP_RAW):
        calls = []

        def recording(request):
            calls.append(request.url.path.rsplit("/", 2)[-2:])
            return handler(request, len(calls))

        monkeypatch.setattr(v.httpx, "Client",
                            lambda **kw: real_client(transport=httpx.MockTransport(recording)))
        v._mobile_api_probe(_ApDriver(raw))
        return calls
    return run


def _env(code=0, envelope=None):
    return httpx.Response(200, json={"Status": {"Code": code, "Message": "m"}, "Envelope": envelope})


def _no_secrets(text):
    for secret in ("Jan Kowalski", "Kowalski", "jan@example.com", _AP_TOKEN, "PRIVATE KEY"):
        assert secret not in text


def test_mobile_probe_registers_once_and_lists_accounts(v, probe, tmp_path, caplog):
    def handler(request, n):
        if request.method == "POST":
            return _env(envelope={})
        return _env(envelope=[{"Unit": {"RestURL": "https://lekcjaplus.vulcan.net.pl/warszawa/1/api"}}])

    with caplog.at_level("INFO", logger=v.logger.name):
        assert probe(handler) == [["register", "jwt"], ["register", "hebe"]]
        assert probe(handler) == [["register", "hebe"]]          # klucz zapisany: bez ponownej rejestracji
    assert "kont w API mobilnym: 1 (hosty danych: lekcjaplus.vulcan.net.pl)" in caplog.text
    assert "nieudany" not in caplog.text
    _no_secrets(caplog.text)
    path = tmp_path / "hebe_device.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert json.loads(path.read_text())["registrations"] == {
        "warszawa": "https://lekcjaplus.vulcan.net.pl/warszawa/api"}


def test_mobile_probe_reregisters_once_when_certificate_gone(v, probe, caplog):
    probe(lambda r, n: _env(envelope={} if r.method == "POST" else []))   # urządzenie zarejestrowane

    def handler(request, n):
        if request.method == "POST":
            return _env(envelope={})
        return _env(code=154) if n == 1 else _env(envelope=[])
    with caplog.at_level("INFO", logger=v.logger.name):
        assert probe(handler) == [["register", "hebe"], ["register", "jwt"], ["register", "hebe"]]
    assert "rejestruję ponownie" in caplog.text and "nieudany" not in caplog.text


@pytest.mark.parametrize("raw,handler", [
    ("<html>logowanie</html>", None),                                       # sesja wygasła
    (json.dumps({"Success": True, "IsConsentAccepted": False, "Tokens": [_AP_TOKEN]}), None),
    (_AP_RAW, lambda r, n: httpx.Response(500)),                            # serwer padł
    (_AP_RAW, lambda r, n: _env(code=202)),                                 # zły token
])
def test_mobile_probe_never_breaks_login(v, probe, tmp_path, caplog, raw, handler):
    with caplog.at_level("INFO", logger=v.logger.name):
        calls = probe(handler or (lambda r, n: pytest.fail("bez rejestracji")), raw=raw)
    assert "Eksperyment API mobilnego nieudany (logowanie działa dalej)" in caplog.text
    _no_secrets(caplog.text)
    if handler is None:
        assert calls == [] and not (tmp_path / "hebe_device.json").exists()


def test_mobile_api_option_requires_explicit_true(v):
    assert v.MOBILE_API is False   # w testowym options.json opcji nie ma


def test_remark_row(v):
    assert v._remark_row({"id": 0}, "jan") is None
    row = v._remark_row({"id": 7, "data": "2026-10-01T00:00:00", "tresc": "<b>Pochwała</b> za pomoc",
                         "autor": "A", "kategoria": "K", "liczbaPunktow": 5}, "jan")
    assert row == ("7", "jan", "2026-10-01", "**Pochwała** za pomoc", "A", "K", "5", "pozytywna")


# --- faza 3: dane z API mobilnego przez istniejące fetchery (hebe_web) ---

class _FakeHebe:
    def __init__(self, data):
        self.data = data

    def get_all(self, rest_url, endpoint, pupil_id, *, last_sync=None, **query):
        rows = self.data.get(endpoint, [])
        if endpoint == "mobile/grade/byPupil":   # oceny tylko z żądanego okresu, jak serwer
            rows = [g for g in rows if g["Column"]["PeriodId"] == query.get("periodId")]
        return rows

    def call(self, method, rest_url, endpoint, **kwargs):
        return self.data.get(endpoint)


class _NoCalendar:
    async def get(self, url, **kwargs):
        return _Res({}, 404)


def _mobile_bridge(v, monkeypatch, data, known=None):
    monkeypatch.syspath_prepend(str(SRC.parent))
    import hebe
    import hebe_web
    hebe_web._year_lessons_cache.clear()   # pamięć między cyklami nie może przeciekać między testami
    account = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"},
               "Unit": {"Id": 3, "RestURL": "https://lekcjaplus.vulcan.net.pl/warszawa/1/api"},
               "ConstituentUnit": {"Id": 5}, "Journal": {"Id": 1}, "State": 0,
               "MessageBox": {"GlobalKey": "box-1"},
               "Periods": [{"Id": 10, "Number": 1, "Level": 2, "Current": True,
                            "Start": f"{datetime.now().year - 1}-09-01", "End": f"{datetime.now().year + 1}-06-25"},
                           {"Id": 11, "Number": 2, "Level": 2, "Current": False,
                            "Start": f"{datetime.now().year + 1}-06-26", "End": f"{datetime.now().year + 1}-08-31"}]}
    bridge = hebe_web.PupilBridge(_FakeHebe(data), hebe.pupil_from_account(account), account,
                                  datetime.now().date(), known)
    student = {"slug": "jan_kowalski", "key": "", "uczen": "Jan Kowalski", "idDziennik": 0, "periodId": 10,
               "city": "warszawa", "domain": "eduvulcan.pl", "globalKeySkrzynka": "box-1"}
    return hebe_web, bridge, student


def test_mobile_data_through_web_fetchers(v, published, db, monkeypatch):
    today = datetime.now().date()
    d = lambda n: (today + timedelta(days=n)).isoformat()   # noqa: E731
    teacher = {"Name": "Anna", "Surname": "Nowak", "DisplayName": "Anna Nowak"}
    data = {
        "mobile/note/byPupil": [{"Id": 77, "ValidAt": d(-3), "Content": "Pochwała za pomoc", "Points": 5,
                                 "Creator": teacher, "Category": {"Name": "Zachowanie"}}],
        "mobile/grade/byPupil": [{"Id": 1, "ContentRaw": "47 (%)", "Content": "47", "ModifiedAt": f"{d(-2)}T10:00:00",
                                  "Column": {"Id": 100, "PeriodId": 10, "Name": "Kartkówka 1", "Weight": 1.0,
                                             "Subject": {"Id": 1, "Name": "Chemia"}, "Category": {"Name": "Kartkówki"}}}],
        "mobile/grade/summary/byPupil": [{"Subject": {"Name": "Chemia"}, "Entry_1": "4", "Entry_2": None}],
        "mobile/schedule/withchanges/byPupil": [{"Id": 5, "DateAt": d(1), "TimeSlot": {"Start": "08:00", "End": "08:45"},
                                                 "Subject": {"Name": "Matematyka"}, "Room": {"Code": "12"},
                                                 "TeacherPrimary": teacher, "Substitution": None}],
        "mobile/exam/byPupil": [{"Id": 12345678, "TypeId": 2, "DeadlineAt": d(4), "Subject": {"Name": "Biologia"},
                                 "Content": "Dział 3", "Creator": teacher}],
        "mobile/lesson/byPupil": [{"Id": 9, "DayAt": d(-1), "TimeSlot": {"Start": "08:00"},
                                   "Subject": {"Id": 1, "Name": "Chemia"}, "PresenceType": {"CategoryId": 2}},
                                  {"Id": 10, "DayAt": d(-1), "TimeSlot": {"Start": "08:55"},
                                   "Subject": {"Id": 1, "Name": "Chemia"}, "PresenceType": {"CategoryId": 1}}],
        "mobile/school/lucky": {"Number": 13},
        "mobile/meetings/byPupil": [{"Id": 3, "DateAt": f"{d(5)}T17:00:00", "Where": "s. 12", "Why": "Wywiadówka"}],
    }
    # Wpis terminarza zapisany wcześniej ze stron WWW (inne id): ma je zachować.
    db.execute("INSERT INTO timetable VALUES (?,?,?,?,?,?,?)",
               ("1234567", "jan_kowalski", f"{d(4)}T00:00:00+01:00", "Biologia", "Kartkówka", "Dział 3", "Anna Nowak"))
    db.commit()
    known = asyncio.run(v._mobile_known_timetable_ids("jan_kowalski"))
    hebe_web, bridge, s = _mobile_bridge(v, monkeypatch, data, known)
    client, ha, base = hebe_web.AsyncBridgeClient(bridge), _NoCalendar(), "https://uczen.eduvulcan.pl/warszawa"

    async def run():
        await asyncio.gather(v._fetch_remarks(client, ha, base, s), v._fetch_grades(client, ha, base, s),
                             v._fetch_schedule(client, ha, base, s, set()), v._fetch_timetable(client, ha, base, s),
                             v._fetch_frequency(client, ha, base, s), v._fetch_lucky_number(client, ha, base, s),
                             v._fetch_meetings(client, ha, base, s))
    asyncio.run(run())

    q = lambda sql: db.execute(sql).fetchall()   # noqa: E731
    assert q("SELECT * FROM remarks") == [("77", "jan_kowalski", d(-3), "Pochwała za pomoc", "Anna Nowak",
                                           "Zachowanie", "5", "pozytywna")]
    dotted = f"{d(-2)[8:10]}.{d(-2)[5:7]}.{d(-2)[:4]}"
    assert q("SELECT * FROM grades") == [("100", "jan_kowalski", "Chemia", "47 (%)", dotted,
                                          "Kartkówki: Kartkówka 1", "10")]
    assert q("SELECT data, godzina, przedmiot, sala, prowadzacy, status FROM schedule") == [
        (d(1), "08:00-08:45", "Matematyka", "12", "Nowak Anna", "")]
    tt = q("SELECT id, substr(data, 1, 10), przedmiot, typ, opis, autor FROM timetable")
    assert tt == [("1234567", d(4), "Biologia", "Kartkówka", "Dział 3", "Anna Nowak")]   # stare id zachowane
    assert q("SELECT data, godzina, kategoria FROM frequency ORDER BY godzina") == [(d(-1), "08:00", 2),
                                                                                  (d(-1), "08:55", 1)]
    assert q("SELECT numer FROM lucky_number") == [("13",)]
    assert q("SELECT data, godzina, sala, opis FROM meetings") == [(d(5), "17:00", "s. 12", "Wywiadówka")]

    oceny = published["sensor.vultron_oceny_jan_kowalski_p1"][1]["lista_przedmiotow"][0]
    assert (oceny["przedmiot"], oceny["proponowana"], oceny["srednia_proc"]) == ("Chemia", "4", 47.0)
    assert published["sensor.vultron_uwagi_jan_kowalski"][0] == 1
    assert published["sensor.vultron_freq_jan_kowalski"][0] == 1                # 1 nieobecność
    assert published["sensor.vultron_stats_jan_kowalski"][0] == 50.0
    assert published["sensor.vultron_stats_jan_kowalski_chemia"][0] == 50.0
    assert published["sensor.vultron_szczesliwy_numerek_jan_kowalski"][0] == "13"
    assert published["sensor.vultron_terminarz_jan_kowalski"][0] >= 0


def test_mobile_messages_through_web_sync(v, db, monkeypatch):
    msgs = [{"Id": "m-1", "SentAt": "2026-11-01T10:00:00", "ReadAt": None, "Subject": "T",
             "Sender": {"Name": "Nowak Anna"}, "Content": "<p>Treść</p>"}]
    hebe_web, bridge, s = _mobile_bridge(v, monkeypatch, {"mobile/messages/received/byBox": msgs})
    sent = {}
    monkeypatch.setattr(v, "publish_sensor_sync", lambda e, st, n, a: sent.update({e: (st, a)}))
    v.run_messages_sync([s], lambda: hebe_web.MessagesBridgeSession({"box-1": bridge}))
    assert db.execute("SELECT key, nadawca, temat, tresc, przeczytana FROM messages").fetchall() == [
        ("m-1", "Nowak Anna", "T", "<p>Treść</p>", 0)]
    state, attrs = sent["sensor.vultron_wiadomosci_jan_kowalski"]
    assert state == 1 and attrs["wiadomosci"][0]["tresc"] == "Treść"


def test_mobile_relogin_is_rate_limited(v, monkeypatch):
    monkeypatch.setattr(v, "_mobile_relogin_at", None)
    assert v._mobile_relogin() == "relogin"
    assert v._mobile_relogin() == "web"
    monkeypatch.setattr(v, "_mobile_relogin_at", v.time.monotonic() - v.MOBILE_RELOGIN_EVERY - 1)
    assert v._mobile_relogin() == "relogin"


def test_mobile_cycle_end_to_end(v, published, db, monkeypatch, caplog):
    hebe_web, bridge, s = _mobile_bridge(v, monkeypatch, {
        "mobile/note/byPupil": [{"Id": 1, "ValidAt": _today_iso(), "Content": "Uwaga", "Creator": None}],
        "mobile/messages/received/byBox": [{"Id": "m-1", "SentAt": "2026-11-01T10:00:00", "ReadAt": None,
                                            "Subject": "T", "Sender": {"Name": "N"}, "Content": "x"}]})
    pupils = [(bridge.client, bridge.pupil, bridge.account, "warszawa")]
    monkeypatch.setattr(v, "_mobile_prepare", lambda http, *_: ("ok", pupils))
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **_: None)
    monkeypatch.setattr(v, "_mobile_web_refresh_at", v.time.monotonic())   # przeglądarka już była dziś
    monkeypatch.setattr(v, "_mobile_web_had_session", None)
    sent = {}
    monkeypatch.setattr(v, "publish_sensor_sync", lambda e, st, n, a: sent.update({e: st}))
    with caplog.at_level("INFO", logger=v.logger.name):
        assert asyncio.run(v._mobile_cycle(None)) == "ok"
    assert published["sensor.vultron_uwagi_jan_kowalski"][0] == 1
    assert "sensor.vultron_osiagniecia_jan_kowalski" not in published     # bez sesji WWW: bez osiągnięć
    assert sent["sensor.vultron_wiadomosci_jan_kowalski"] == 1
    assert "Sesja stron WWW wygasła" in caplog.text and "za ok. 24 h" in caplog.text
    assert "Sekcja" not in caplog.text


@pytest.mark.parametrize("state", ["web", "relogin"])
def test_mobile_cycle_passes_through_fallback_states(v, monkeypatch, state):
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "_mobile_prepare", lambda http, *_: (state, []))
    assert asyncio.run(v._mobile_cycle(None)) == state


def test_mobile_prepare_states(v, tmp_path, monkeypatch):
    import hebe
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    monkeypatch.setattr(v, "_mobile_relogin_at", None)
    assert v._mobile_prepare(None)[0] == "relogin"                  # brak urządzenia
    assert v._mobile_prepare(None)[0] == "web"                      # drugi raz w 6 h: bez Chromium
    device = hebe.Device.new()
    device.registrations["warszawa"] = "https://lekcjaplus.vulcan.net.pl/warszawa/api"
    device.save(v.HEBE_DEVICE_PATH)
    active = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"},
              "Unit": {"Id": 3, "RestURL": "https://lekcjaplus.vulcan.net.pl/warszawa/1/api"},
              "ConstituentUnit": {"Id": 5}, "Journal": {"Id": 1}, "Periods": [{"Id": 1, "Current": True}]}
    inactive = {**active, "Journal": None, "Periods": []}

    def http_with(handler):
        return httpx.Client(transport=httpx.MockTransport(handler))
    state, pupils = v._mobile_prepare(http_with(lambda r: _env(envelope=[inactive, active, {"Pupil": None}])))
    assert state == "ok" and [p[1].pupil_id for p in pupils] == [7]
    assert v._mobile_prepare(http_with(lambda r: _env(envelope=[inactive])))[0] == "web"
    assert v._mobile_prepare(http_with(lambda r: httpx.Response(500)))[0] == "web"
    monkeypatch.setattr(v, "_mobile_relogin_at", None)
    assert v._mobile_prepare(http_with(lambda r: _env(code=154)))[0] == "relogin"


def test_mobile_web_session_refreshes_at_most_daily(v, monkeypatch):
    calls = []
    fresh = ([{"slug": "jan_kowalski"}], [], [{"name": "c", "value": "1"}])
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **_: None)
    monkeypatch.setattr(v, "run_diary_auth", lambda: (calls.append(1), fresh)[1])
    monkeypatch.setattr(v, "_mobile_web_refresh_at", None)
    assert asyncio.run(v._mobile_web_session()) == fresh
    assert asyncio.run(v._mobile_web_session()) is None          # w ciągu doby: bez przeglądarki
    assert len(calls) == 1
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **_: ("zapisana",))
    assert asyncio.run(v._mobile_web_session()) == ("zapisana",)   # ważna sesja: zawsze, bez logowania
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["rejected", "error", "empty"])
def test_mobile_web_session_failures_do_not_stop(v, monkeypatch, outcome):
    def auth():
        if outcome == "rejected":
            raise v.LoginRejected("złe hasło")
        if outcome == "error":
            raise RuntimeError("chromedriver")
        return None, None, None
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **_: None)
    monkeypatch.setattr(v, "run_diary_auth", auth)
    monkeypatch.setattr(v, "_mobile_web_refresh_at", None)
    assert asyncio.run(v._mobile_web_session()) is None


def test_mobile_web_session_expired_file_probed_once(v, tmp_path, monkeypatch):
    # Wygasłe ciasteczka sprawdzamy raz; kolejne cykle bez zapytań, aż plik sesji się zmieni.
    pkl = tmp_path / "vul.json"
    pkl.write_text("{}")
    probes = []
    monkeypatch.setattr(v, "VUL_PKL", str(pkl))
    monkeypatch.setattr(v, "_mobile_web_expired", None)
    monkeypatch.setattr(v, "_mobile_web_refresh_at", v.time.monotonic())
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **kw: probes.append(kw) and None)
    for _ in range(3):
        assert asyncio.run(v._mobile_web_session()) is None
    assert probes == [{"quiet": True}]
    pkl.write_text('{"nowy": 1}')                       # nowe logowanie zapisało plik
    assert asyncio.run(v._mobile_web_session()) is None
    assert len(probes) == 2


def test_mobile_web_refresh_disabled_never_starts_browser(v, monkeypatch, caplog):
    monkeypatch.setattr(v, "MOBILE_WEB_REFRESH", False)
    monkeypatch.setattr(v, "_mobile_web_refresh_at", None)
    monkeypatch.setattr(v, "_mobile_web_had_session", None)
    monkeypatch.setattr(v, "_try_reuse_cached_session", lambda **_: None)
    monkeypatch.setattr(v, "run_diary_auth", lambda: pytest.fail("przeglądarka mimo mobile_web_refresh: false"))
    assert asyncio.run(v._mobile_web_session()) is None
    with caplog.at_level("INFO", logger=v.logger.name):
        v._log_mobile_web_state(False)
        v._log_mobile_web_state(False)                  # bez zmiany stanu: bez drugiego wpisu
    assert caplog.text.count("mobile_web_refresh: false") == 1


def test_mobile_web_state_logged_on_change_only(v, monkeypatch, caplog):
    monkeypatch.setattr(v, "MOBILE_WEB_REFRESH", True)
    monkeypatch.setattr(v, "_mobile_web_had_session", None)
    monkeypatch.setattr(v, "_mobile_web_refresh_at", v.time.monotonic() - 20 * 3600)
    with caplog.at_level("INFO", logger=v.logger.name):
        for state in (True, True, False, False, True):
            v._log_mobile_web_state(state)
    assert caplog.text.count("Sesja stron WWW ważna") == 2
    assert caplog.text.count("Sesja stron WWW wygasła") == 1 and "za ok. 4 h" in caplog.text


def test_try_reuse_cached_session_quiet_logs_debug(v, tmp_path, monkeypatch, caplog):
    pkl = tmp_path / "vul.json"
    pkl.write_text(json.dumps({"saved_at": "2020-01-01T00:00:00+00:00", "students": [{"slug": "x"}],
                               "cookies": [{"name": "c", "value": "1"}]}))
    monkeypatch.setattr(v, "VUL_PKL", str(pkl))
    with caplog.at_level("INFO", logger=v.logger.name):
        assert v._try_reuse_cached_session(quiet=True) is None
        assert "wymuszam" not in caplog.text
        assert v._try_reuse_cached_session() is None
    assert "wymuszam pełne logowanie" in caplog.text


def test_cached_kindergarten_slugs(v, tmp_path, monkeypatch):
    pkl = tmp_path / "vul.json"
    monkeypatch.setattr(v, "VUL_PKL", str(pkl))
    assert v._cached_kindergarten_slugs() == frozenset()                 # brak pliku
    pkl.write_text("nie json")
    assert v._cached_kindergarten_slugs() == frozenset()
    pkl.write_text(json.dumps({"przedszkolaki": [{"slug": "ala_kowalska"}, {"slug": 5}, "x"]}))
    assert v._cached_kindergarten_slugs() == frozenset({"ala_kowalska"})


def test_mobile_prepare_moves_kindergarten_accounts_aside(v, tmp_path, monkeypatch):
    import hebe
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    device = hebe.Device.new()
    device.registrations["warszawa"] = "https://lekcjaplus.vulcan.net.pl/warszawa/api"
    device.save(v.HEBE_DEVICE_PATH)
    unit = {"Unit": {"Id": 3, "RestURL": "https://lekcjaplus.vulcan.net.pl/warszawa/1/api"},
            "ConstituentUnit": {"Id": 5}, "Journal": {"Id": 1}, "Periods": [{"Id": 1, "Current": True}]}
    school = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"}, **unit}
    kid = {"Pupil": {"Id": 8, "FirstName": "Ala", "Surname": "Kowalska"}, **unit, "Periods": []}
    http = httpx.Client(transport=httpx.MockTransport(lambda r: _env(envelope=[school, kid])))
    kids = []
    state, pupils = v._mobile_prepare(http, frozenset({"ala_kowalska"}), kids)
    assert state == "ok" and [p[1].pupil_id for p in pupils] == [7]
    assert [k[1].pupil_id for k in kids] == [8]


def test_mobile_prepare_skips_second_account_with_same_name(v, tmp_path, monkeypatch, caplog):
    # To samo dziecko w dwóch szkołach: drugie konto pomijane jak na stronach WWW (bez mieszania danych).
    import hebe
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    device = hebe.Device.new()
    device.registrations["warszawa"] = "https://lekcjaplus.vulcan.net.pl/warszawa/api"
    device.save(v.HEBE_DEVICE_PATH)

    def account(pid, unit_id):
        return {"Pupil": {"Id": pid, "FirstName": "Jan", "Surname": "Kowalski"},
                "Unit": {"Id": unit_id, "RestURL": "https://lekcjaplus.vulcan.net.pl/warszawa/1/api"},
                "ConstituentUnit": {"Id": unit_id}, "Journal": {"Id": 1}, "Periods": [{"Id": 1, "Current": True}]}
    http = httpx.Client(transport=httpx.MockTransport(lambda r: _env(envelope=[account(7, 3), account(9, 4)])))
    with caplog.at_level("WARNING", logger=v.logger.name):
        state, pupils = v._mobile_prepare(http)
    assert state == "ok" and [p[1].pupil_id for p in pupils] == [7]
    assert "pomijam drugie aktywne konto" in caplog.text and "Kowalski" not in caplog.text


def test_school_match_report_logs_only_yes_no(v, monkeypatch, caplog):
    monkeypatch.setattr(v, "_school_match_reported", False)
    account = {"Pupil": {"FirstName": "Jan", "Surname": "Kowalski"}, "Journal": {"Id": 987654},
               "ConstituentUnit": {"Name": "Szkoła Podstawowa nr 1", "Short": "SP1"},
               "Unit": {"Name": "Zespół Szkół", "Short": "ZS"}, "ClassDisplay": "3a"}
    web = {"jan_kowalski": {"idDziennik": "987654", "jednostka": "szkoła podstawowa nr 1", "klasa": "3b"}}
    with caplog.at_level("INFO", logger=v.logger.name):
        v._school_match_report([(None, None, account, "warszawa")], web)
        v._school_match_report([(None, None, account, "warszawa")], web)    # raz na uruchomienie
    assert caplog.text.count("[MOBILE][SZKOŁA]") == 1
    assert "idDziennik = Journal.Id: tak" in caplog.text
    assert "ConstituentUnit.Name: podobne" in caplog.text and "Unit.Short: nie" in caplog.text
    assert "ClassDisplay: nie" in caplog.text
    for value in ("Kowalski", "SP1", "Podstawowa", "3a", "987654"):
        assert value not in caplog.text


def test_kindergarten_shadow_once_a_day(v, monkeypatch, caplog):
    monkeypatch.syspath_prepend(str(SRC.parent))
    import hebe_web
    monkeypatch.setattr(v, "_kindergarten_shadow_at", None)
    monkeypatch.setattr(hebe_web, "kindergarten_shadow", lambda client, pupil, today: ["obecność: 2 rek."])
    monkeypatch.setattr(hebe_web, "account_shape", lambda account, pupil: "dziennik: tak")
    kid = (None, None, {}, "warszawa")
    with caplog.at_level("INFO", logger=v.logger.name):
        v._kindergarten_shadow([kid], 2)
        v._kindergarten_shadow([kid], 2)
    assert caplog.text.count("przedszkolak 1: obecność: 2 rek.") == 1
    assert "Przedszkolaków ze stron WWW: 2, w kontach API mobilnego: 1" in caplog.text


@pytest.mark.parametrize("value,expected", [(None, 15), ("", 15), (15, 15), ("30", 30), (5, 10), (0, 10),
                                            ("abc", 15), (120, 120)])
def test_mobile_interval(v, value, expected):
    assert v._mobile_interval(value) == expected


def test_cycle_wait(v, monkeypatch):
    monkeypatch.setattr(v, "MOBILE_INTERVAL", 15)
    waits = {v._cycle_wait(True) for _ in range(300)}
    assert min(waits) >= 15 * 60 - 120 and max(waits) <= 15 * 60 + 120
    monkeypatch.setattr(v, "MOBILE_INTERVAL", 10)
    assert min(v._cycle_wait(True) for _ in range(300)) >= 600          # nigdy poniżej minimum
    web = {v._cycle_wait(False) for _ in range(300)}
    assert min(web) >= 2400 and max(web) <= 3600


# --- oceny: wartość liczbowa i średnie (_grade_value, _subject_summary) ---

@pytest.mark.parametrize("wpis,expected", [
    ("5", ("num", 5.0)),
    ("4+", ("num", 4.5)),
    ("3-", ("num", 2.75)),
    ("4,5", ("num", 4.5)),
    ("1 (23%)", ("num", 1.0)),        # liczy się ocena, procent to tylko informacja
    ("47 (%)", ("pct", 47.0)),
    ("47,5%", ("pct", 47.5)),
    ("150%", None),                    # procent spoza 0-100
    ("np", None), ("nb", None), ("bz", None),
    ("A", None), ("+", None), ("0", None), ("7", None), ("10", None),
])
def test_grade_value(v, wpis, expected):
    assert v._grade_value(wpis) == expected


def test_grade_value_long_input_is_fast(v):
    # Przed 7.2.2 wyrażenia miały koszt kwadratowy: ok. 16 s dla tych trzech wpisów.
    pad = " " * 50000
    start = time.monotonic()
    for wpis in (f"1{pad}x", f"3{pad}(", f"1{pad}(x"):
        v._grade_value(wpis)
    assert time.monotonic() - start < 1.0


def _przedmiot(**extra):
    return {
        "kolumnyOcenyCzastkowe": [
            {"kategoriaKolumny": "Sprawdzian", "nazwaKolumny": "Dział 1", "oceny": [
                {"wpis": "2", "dataOceny": "01.10.2026", "waga": 2, "idOcenaPoprawiona": 7},
                {"wpis": "5", "dataOceny": "08.10.2026", "waga": 2},
            ]},
            {"kategoriaKolumny": "Kartkówka", "nazwaKolumny": "", "oceny": [
                {"wpis": "4+", "dataOceny": "02.10.2026", "waga": 1},
            ]},
            {"kategoriaKolumny": "", "nazwaKolumny": "Test", "oceny": [
                {"wpis": "47 (%)", "dataOceny": "03.10.2026", "waga": 1},
                {"wpis": "np", "dataOceny": "04.10.2026", "waga": 1},
            ]},
        ],
        **extra,
    }


def test_subject_summary_grades_and_correction(v):
    s = v._subject_summary(_przedmiot())
    assert s["oceny"] == [
        {"w": "5", "d": "08.10", "i": "Sprawdzian: Dział 1", "pop": ["2"]},
        {"w": "4+", "d": "02.10", "i": "Kartkówka"},
        {"w": "47 (%)", "d": "03.10", "i": "Test"},
        {"w": "np", "d": "04.10", "i": "Test"},
    ]
    assert s["srednia_proc"] == 47.0


def test_subject_summary_average_from_api(v):
    s = v._subject_summary(_przedmiot(srednia="4,25"))
    assert (s["srednia"], s["srednia_zrodlo"]) == (4.25, "vulcan")


def test_subject_summary_api_zero_means_no_average(v):
    # 0.0 z API przy włączonych średnich = brak ocen liczonych; nie liczymy własnej.
    s = v._subject_summary(_przedmiot(srednia=0.0))
    assert (s["srednia"], s["srednia_zrodlo"]) == (None, None)


def test_subject_summary_weighted_without_api_field(v):
    # Poprawiona "2" nie liczy się: (5*2 + 4.5*1) / 3 = 4.83.
    s = v._subject_summary(_przedmiot())
    assert (s["srednia"], s["srednia_zrodlo"]) == (4.83, "wyliczona")


def test_subject_summary_averages_disabled_ignores_weights(v):
    # Szkoła bez średnich: API daje 0.0, liczymy zwykłą średnią: (5 + 4.5) / 2.
    s = v._subject_summary(_przedmiot(srednia=0.0), srednie_wlaczone=False)
    assert (s["srednia"], s["srednia_zrodlo"]) == (4.75, "wyliczona")


def test_subject_summary_bad_and_zero_weights(v):
    p = {"kolumnyOcenyCzastkowe": [{"kategoriaKolumny": "K", "nazwaKolumny": "", "oceny": [
        {"wpis": "6", "dataOceny": "01.10.2026", "waga": "abc"},   # zła waga -> 1.0
        {"wpis": "1", "dataOceny": "02.10.2026", "waga": 0},       # waga 0 -> pomijana
        {"wpis": "3", "dataOceny": "03.10.2026", "waga": None},    # brak wagi -> 1.0
    ]}]}
    s = v._subject_summary(p)
    assert (s["srednia"], s["srednia_zrodlo"]) == (4.5, "wyliczona")
    assert s["srednia_proc"] is None


# --- adres dziennika: domena trafia do adresów z ciasteczkami sesji ---

@pytest.mark.parametrize("url,expected", [
    ("https://uczen.eduvulcan.pl/warszawa/App/x", ("eduvulcan.pl", "warszawa")),
    ("https://uczen.edu.lublin.eu/lublin", ("edu.lublin.eu", "lublin")),
    ("https://uczen.eduvulcan.pl:443/gdynia/", ("eduvulcan.pl", "gdynia")),
    ("http://uczen.eduvulcan.pl/warszawa", None),           # tylko https
    ("https://uczen.eduvulcan.pl:8443/warszawa", None),     # obcy port
    ("https://uczen.eduvulcan.pl:99999/warszawa", None),    # port nie do sparsowania
    ("https://u:p@uczen.eduvulcan.pl/warszawa", None),      # dane logowania w URL
    ("https://eduvulcan.pl/warszawa", None),                # bez "uczen."
    ("https://uczen.eduvulcan.pl/", None),                  # brak miasta
    ("https://uczen.eduvulcan.pl/war%20szawa", None),       # niedozwolone znaki
    ("", None),
    (None, None),
])
def test_diary_location(v, url, expected):
    assert v._diary_location(url) == expected


def test_zebrania_attrs(v):
    small = [{"data": "2026-11-20", "temat": "Wywiadówka"}]
    assert v._zebrania_attrs(small) == {"zebrania": small, "icon": "mdi:account-group"}
    big = [{"data": "2026-11-20", "temat": "ż" * 500} for _ in range(60)]
    attrs = v._zebrania_attrs(big)
    assert attrs["pominiete"] == len(big) - len(attrs["zebrania"]) > 0


# ── wiadomości na żądanie karty (input_text → Vultron → sensor) ─────────

_MSG_SZKOLA = "klucz-szkola-1"
_MSG_PRZEDSZKOLE = "klucz-przedszkole-1"


@pytest.fixture
def kanal(v, db, monkeypatch):
    """Baza z wiadomością szkolną i przedszkolną; zbiera publikacje sensorów."""
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
               (_MSG_SZKOLA, "jan_kowalski", "2026-11-09T10:00:00", "Nauczyciel A",
                "Wycieczka", "<p>Zbiórka o <b>8:00</b></p>", 1))
    db.execute("INSERT INTO przedszkole_wiadomosci VALUES (?,?,?,?,?,?,?)",
               (_MSG_PRZEDSZKOLE, "jan_kowalski", "2026-11-08T09:00:00", "Wychowawczyni",
                "Bal", "Stroje na piątek", 0))
    db.commit()
    sent = []
    monkeypatch.setattr(v, "publish_sensor_sync",
                        lambda e, st, n, a: sent.append((e, st, n, a)))
    return sent


@pytest.mark.parametrize("value", [
    None, 5, "", "otworz:abc:12345678", "otworz:" + "a" * 16 + ":1234567",
    "OTWORZ:" + "a" * 16 + ":12345678", "otworz:" + "g" * 16 + ":12345678",
    "status:" + "a" * 16 + ":2:12345678", "status:" + "a" * 16 + ":1",
    "otworz:" + "a" * 16 + ":12345678\n", "otworz:" + "a" * 16 + ":12345678;DROP",
])
def test_parse_zadanie_rejects_bad_format(v, value):
    assert v.parse_zadanie(value) is None


def test_parse_zadanie_accepts_open_and_status(v):
    mid = "0123456789abcdef"
    assert v.parse_zadanie(f"otworz:{mid}:a1b2c3d4") == ("otworz", mid, "a1b2c3d4", None)
    assert v.parse_zadanie(f"status:{mid}:0:a1b2c3d4") == ("status", mid, "a1b2c3d4", 0)


def test_message_list_has_hashed_id_not_raw_key(v, db):
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
               (_MSG_SZKOLA, "jan_kowalski", "2026-11-09T10:00:00", "A", "T", "x", 1))
    unread, attrs = v._messages_sensor(db.cursor(), "messages", "jan_kowalski")
    (msg,) = attrs["wiadomosci"]
    assert msg["id"] == v.message_id(_MSG_SZKOLA) and len(msg["id"]) == 16
    assert _MSG_SZKOLA not in json.dumps(attrs)
    assert unread == 0 and msg["tresc"] == "" and msg["przeczytana"] is True


def test_open_read_message_from_local_db(v, kanal):
    v.handle_zadanie(f"otworz:{v.message_id(_MSG_SZKOLA)}:a1b2c3d4")
    ((entity, state, _, attrs),) = kanal
    assert entity == "sensor.vultron_wiadomosc_otwarta" and state == v.message_id(_MSG_SZKOLA)
    assert attrs["nonce"] == "a1b2c3d4" and attrs["temat"] == "Wycieczka"
    assert "8:00" in attrs["tresc"] and "<p>" not in attrs["tresc"]
    assert attrs["przeczytana"] is True and "blad" not in attrs


def test_open_kindergarten_message(v, kanal):
    v.handle_zadanie(f"otworz:{v.message_id(_MSG_PRZEDSZKOLE)}:00000000")
    ((_, _, _, attrs),) = kanal
    assert attrs["tresc"] == "Stroje na piątek" and attrs["przeczytana"] is False


def test_open_unknown_id_answers_with_error(v, kanal):
    v.handle_zadanie("otworz:" + "f" * 16 + ":12345678")
    ((entity, _, _, attrs),) = kanal
    assert entity == "sensor.vultron_wiadomosc_otwarta"
    assert attrs == {"nonce": "12345678", "id": "f" * 16, "blad": "brak wiadomości"}


def test_bad_request_publishes_nothing(v, kanal):
    v.handle_zadanie("otworz:../../etc:12345678")
    assert kanal == []


def test_open_long_body_fits_attribute_limit(v, db, kanal):
    db.execute("UPDATE messages SET tresc=? WHERE key=?", ("żółć " * 10000, _MSG_SZKOLA))
    db.commit()
    v.handle_zadanie(f"otworz:{v.message_id(_MSG_SZKOLA)}:12345678")
    ((_, _, _, attrs),) = kanal
    assert len(json.dumps(attrs, ensure_ascii=False).encode("utf-8")) < 16384
    assert attrs["tresc"].endswith("...")


def test_local_status_overrides_vulcan_and_survives_sync(v, db, kanal):
    mid = v.message_id(_MSG_SZKOLA)
    v.handle_zadanie(f"status:{mid}:0:12345678")
    ((entity, unread, _, attrs),) = kanal
    assert entity == "sensor.vultron_wiadomosci_jan_kowalski" and unread == 1
    assert attrs["wiadomosci"][0]["przeczytana"] is False
    assert attrs["wiadomosci"][0]["tresc"]          # nieprzeczytana: treść w sensorze
    # Synchronizacja z eduVULCAN nadpisuje tylko messages.przeczytana, nie status lokalny.
    db.execute("UPDATE messages SET przeczytana=1 WHERE key=?", (_MSG_SZKOLA,))
    db.commit()
    unread, _ = v._messages_sensor(db.cursor(), "messages", "jan_kowalski")
    assert unread == 1
    # Nic nie idzie do eduVULCAN: jedyna publikacja to sensor HA.
    assert [e for e, *_ in kanal] == ["sensor.vultron_wiadomosci_jan_kowalski"]


def test_status_change_keeps_friendly_name_from_cache(v, db, kanal):
    db.execute("INSERT INTO ha_cache VALUES (?,?,?)",
               ("sensor.vultron_przedszkole_wiadomosci_jan_kowalski", "1",
                json.dumps({"friendly_name": "Wiadomości (przedszkole): Jan Kowalski"})))
    db.commit()
    v.handle_zadanie(f"status:{v.message_id(_MSG_PRZEDSZKOLE)}:1:12345678")
    ((entity, unread, name, _),) = kanal
    assert entity == "sensor.vultron_przedszkole_wiadomosci_jan_kowalski" and unread == 0
    assert name == "Wiadomości (przedszkole): Jan Kowalski"


def test_status_unknown_id_changes_nothing(v, db, kanal):
    v.handle_zadanie("status:" + "f" * 16 + ":1:12345678")
    assert kanal == []
    assert db.execute("SELECT COUNT(*) FROM wiadomosci_status").fetchone()[0] == 0


def test_status_orphans_removed_with_message(v, db, kanal):
    v.handle_zadanie(f"status:{v.message_id(_MSG_SZKOLA)}:0:12345678")
    db.execute("DELETE FROM messages WHERE key=?", (_MSG_SZKOLA,))
    for sql in v._STATUS_ORPHANS_SQL:
        db.execute(sql)
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM wiadomosci_status").fetchone()[0] == 0


@pytest.mark.parametrize("msg,expected", [
    ({"type": "event", "event": {"c": {"input_text.vultron_zadanie": {"+": {"s": "x"}}}}}, "x"),
    ({"type": "event", "event": {"a": {"input_text.vultron_zadanie": {"s": "stare"}}}}, None),
    ({"type": "event", "event": {"c": {"input_text.inny": {"+": {"s": "x"}}}}}, None),
    ({"type": "event", "event": {"c": {"input_text.vultron_zadanie": {"+": {"lu": 1}}}}}, None),
    ({"type": "result", "success": True}, None),
])
def test_zadanie_from_event_only_changes(v, msg, expected):
    assert v._zadanie_from_event(msg) == expected


class _FakeWS:
    """Atrapa websocketu HA: auth, wynik komend, potem podane zdarzenia."""

    def __init__(self, v, events, create_ok=True):
        self.v, self.sent, self.create_ok = v, [], create_ok
        self.inbox = [json.dumps({"type": "auth_required"})]
        self.events = events

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg.get("type") == "auth":
            self.inbox.append(json.dumps({"type": "auth_ok"}))
        elif msg.get("type") == "input_text/create":
            self.inbox.append(json.dumps({"id": msg["id"], "type": "result", "success": self.create_ok,
                                          **({} if self.create_ok else {"error": {"code": "unauthorized"}})}))
        elif msg.get("type") == "subscribe_entities":
            self.inbox.append(json.dumps({"id": msg["id"], "type": "result", "success": True}))
            self.inbox.extend(json.dumps(e) for e in self.events)

    def recv(self):
        if not self.inbox:
            self.v._kanal_stop.set()
            raise ConnectionError("koniec atrapy")
        return self.inbox.pop(0)

    def ping(self):
        pass

    def close(self):
        pass


def _run_loop(v, monkeypatch, ws, helper_exists):
    monkeypatch.setattr(v, "create_connection", lambda url, timeout: ws)
    monkeypatch.setattr(v.httpx, "get", lambda *a, **k: SimpleNamespace(
        status_code=200 if helper_exists else 404))
    handled = []
    monkeypatch.setattr(v, "handle_zadanie", handled.append)
    v._kanal_stop.clear()
    try:
        v._kanal_loop()
    finally:
        v._kanal_stop.clear()
    return handled


def test_channel_loop_creates_helper_subscribes_and_handles_changes(v, monkeypatch, caplog):
    events = [{"id": 2, "type": "event", "event": {"a": {"input_text.vultron_zadanie": {"s": "stare"}}}},
              {"id": 2, "type": "event", "event": {"c": {"input_text.vultron_zadanie": {"+": {"s": "nowe"}}}}}]
    ws = _FakeWS(v, events)
    with caplog.at_level("INFO", logger=v.logger.name):
        handled = _run_loop(v, monkeypatch, ws, helper_exists=False)
    assert handled == ["nowe"]
    types_sent = [m["type"] for m in ws.sent]
    assert types_sent == ["auth", "input_text/create", "subscribe_entities"]
    assert ws.sent[2]["entity_ids"] == ["input_text.vultron_zadanie"]
    assert "Utworzono pomocnik" in caplog.text and "test-token" not in caplog.text


def test_channel_loop_existing_helper_and_missing_rights(v, monkeypatch, caplog):
    ws = _FakeWS(v, [])
    _run_loop(v, monkeypatch, ws, helper_exists=True)
    assert "input_text/create" not in [m["type"] for m in ws.sent]
    ws = _FakeWS(v, [], create_ok=False)
    with caplog.at_level("WARNING", logger=v.logger.name):
        _run_loop(v, monkeypatch, ws, helper_exists=False)
    assert "Utwórz go ręcznie" in caplog.text


# ── znacznik źródła w logu i ostrzeżenie o braku wiadomości w API mobilnym ──

def _record(v, msg="x"):
    return v.logger.makeRecord(v.logger.name, logging.INFO, __file__, 1, msg, (), None)


def test_log_source_tag(v):
    assert "] [INFO] x" in v._fmt.format(_record(v))
    with v._log_source("API"):
        assert "] [INFO/API] x" in v._fmt.format(_record(v))
        with v._log_source("WWW"):
            assert "] [INFO/WWW] x" in v._fmt.format(_record(v))
        assert "] [INFO/API] x" in v._fmt.format(_record(v))
    record = _record(v)
    with v._log_source("API"):
        v._fmt.format(record)
    assert record.levelname == "INFO"                 # rekord bez zmian dla innych handlerów


def test_in_source_tags_only_its_task(v):
    async def level():
        await asyncio.sleep(0)
        return v._LOG_SOURCE.get()

    async def cycle():
        with v._log_source("API"):
            return await asyncio.gather(level(), v._in_source("WWW", level()),
                                        asyncio.to_thread(v._LOG_SOURCE.get))
    assert asyncio.run(cycle()) == ["API", "WWW", "API"]
    assert v._LOG_SOURCE.get() == ""


def test_mobile_messages_warning_after_repeats_and_once_a_day(v, monkeypatch, caplog):
    monkeypatch.setattr(v, "_mobile_messages_failed_cycles", 0)
    monkeypatch.setattr(v, "_mobile_messages_warned_at", None)
    clock = [1000.0]
    monkeypatch.setattr(v.time, "monotonic", lambda: clock[0])
    with caplog.at_level("WARNING", logger=v.logger.name):
        v._note_mobile_messages(["Jan Kowalski"])
        v._note_mobile_messages([])                        # udany cykl zeruje licznik
        for _ in range(v.MOBILE_MESSAGES_WARN_AFTER - 1):
            v._note_mobile_messages(["Jan Kowalski"])
        assert "Wiadomości niedostępne" not in caplog.text
        v._note_mobile_messages(["Jan Kowalski"])
        assert caplog.text.count("Wiadomości niedostępne") == 1
        assert "Jan Kowalski" in caplog.text and "wyłącz API mobilne" in caplog.text
        clock[0] += 3600
        v._note_mobile_messages(["Jan Kowalski"])
        assert caplog.text.count("Wiadomości niedostępne") == 1
        clock[0] += v.MOBILE_MESSAGES_WARN_EVERY
        v._note_mobile_messages(["Jan Kowalski"])
        assert caplog.text.count("Wiadomości niedostępne") == 2
