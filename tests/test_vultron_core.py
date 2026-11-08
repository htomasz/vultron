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
import os
import sqlite3
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


def _shadow_device(v, tmp_path, registrations):
    import hebe
    device = hebe.Device.new()
    device.registrations.update(registrations)
    device.save(v.HEBE_DEVICE_PATH)
    return device


def test_mobile_shadow_logs_counts_only(v, tmp_path, monkeypatch, caplog):
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    _shadow_device(v, tmp_path, {"warszawa": "https://lekcjaplus.vulcan.net.pl/warszawa/api"})
    rest = "https://lekcjaplus.vulcan.net.pl/warszawa/000123/api"
    account = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"},
               "Unit": {"Id": 3, "RestURL": rest}, "ConstituentUnit": {"Id": 5},
               "Periods": [{"Id": 11, "Current": True}], "MessageBox": {"GlobalKey": "box-1"},
               "Journal": {"Id": 1}, "State": 0}

    def handler(request):
        if request.url.path.endswith("mobile/register/hebe"):
            return _env(envelope=[account, {"Pupil": None}])
        if request.url.path.endswith("mobile/school/lucky"):
            return _env(envelope={"Number": 13})
        return _env(envelope=[{"Id": 1, "Tresc": "Jan Kowalski dostał 5"}])

    real_client = httpx.Client
    monkeypatch.setattr(v.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    with caplog.at_level("DEBUG", logger=v.logger.name):
        v._mobile_api_shadow()
    assert "warszawa konto 1/2 (dziennik: tak, bieżący okres: tak, stan: 0): plan=1," in caplog.text
    assert "oceny=1" in caplog.text and "numerek=jest" in caplog.text
    assert "warszawa konto 2/2: konto bez Pupil/Unit/ConstituentUnit" in caplog.text
    assert 'Kształt rekordów: {"plan": {"Id": "int", "Tresc": "aaa aaaaaaaa aaaaaa 9"}' in caplog.text
    _no_secrets(caplog.text)
    assert "box-1" not in caplog.text and "dostał" not in caplog.text


def test_mobile_shadow_without_device_or_server(v, tmp_path, monkeypatch, caplog):
    monkeypatch.syspath_prepend(str(SRC.parent))
    monkeypatch.setattr(v, "HEBE_DEVICE_PATH", str(tmp_path / "hebe_device.json"))
    with caplog.at_level("INFO", logger=v.logger.name):
        v._mobile_api_shadow()                                  # brak pliku urządzenia
    assert "Brak zarejestrowanego urządzenia" in caplog.text

    _shadow_device(v, tmp_path, {"warszawa": "https://lekcjaplus.vulcan.net.pl/warszawa/api"})
    real_client = httpx.Client
    monkeypatch.setattr(v.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: _env(code=154))))
    caplog.clear()
    with caplog.at_level("INFO", logger=v.logger.name):
        v._mobile_api_shadow()                                  # urządzenie unieważnione
    assert "warszawa: lista kont nieudana: GET mobile/register/hebe: kod 154" in caplog.text

    (tmp_path / "hebe_device.json").write_text("{}")
    caplog.clear()
    with caplog.at_level("INFO", logger=v.logger.name):
        v._mobile_api_shadow()                                  # uszkodzony plik
    assert "uszkodzony plik urządzenia" in caplog.text


def test_remark_row(v):
    assert v._remark_row({"id": 0}, "jan") is None
    row = v._remark_row({"id": 7, "data": "2026-10-01T00:00:00", "tresc": "<b>Pochwała</b> za pomoc",
                         "autor": "A", "kategoria": "K", "liczbaPunktow": 5}, "jan")
    assert row == ("7", "jan", "2026-10-01", "**Pochwała** za pomoc", "A", "K", "5", "pozytywna")


def test_compare_rows(v):
    cols = ("id", "slug", "data", "tresc", "autor")
    web = [("1", "s", "2026-10-01", "a", "X"), ("2", "s", "2026-10-02", "b", "Y")]
    mobile = [("901", "s", "2026-10-01", "a", "X Z"), ("2", "s", "2026-10-02", "b", "Y"), ("3", "s", "d", "c", "")]
    assert v._compare_rows(web, mobile, cols, (2, 3)) == \
        "WWW=2, mobilne=3, te same id=1, zgodne po data+tresc=2, różne pola: autor"


def test_mobile_compare_with_web_data(v, db, monkeypatch, caplog):
    import hebe
    monkeypatch.syspath_prepend(str(SRC.parent))
    slug = "jan_kowalski"
    _insert(db, "grades", {"student_slug": slug, "period_id": "11"})
    _insert(db, "grades", {"student_slug": slug, "period_id": "10"})
    db.execute("INSERT INTO remarks VALUES (?,?,?,?,?,?,?,?)",
               ("77", slug, "2026-10-01", "Pochwała za pomoc", "Anna Nowak", "Zachowanie", "5", "pozytywna"))
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", ("k-1", slug, "2026-11-01", "N", "T", "", 1))
    db.commit()
    account = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"}}
    pupil = SimpleNamespace(period_id=11)
    results = {
        "uwagi": [{"Id": 5, "ValidAt": "2026-10-01", "Content": "Pochwała za pomoc", "Points": 5,
                   "Creator": {"DisplayName": "Nowak Anna"}, "Category": {"Name": "Zachowanie"}}],
        "oceny": [{"Id": 1}, {"Id": 2}, {"Id": 3}],
        "wiadomości": [{"GlobalKey": "k-1"}, {"GlobalKey": "k-2"}],
    }
    with caplog.at_level("INFO", logger=v.logger.name):
        v._mobile_compare(hebe, account, pupil, results)
    text = caplog.text
    assert "uczeń w danych WWW znaleziony" in text
    assert ("Porównanie uwag: WWW=1, mobilne=1, te same id=0, zgodne po data+tresc=1, "
            "różne pola: autor (WWW 'aaaa aaaaa' / mobilne 'aaaaa aaaa')") in text
    assert "Porównanie ocen: WWW=1, mobilne=3, te same id=0, zgodne po id_kolumny=0, różne pola: brak" in text
    assert ("Porównanie wiadomości: WWW w bazie=1, mobilne (30 dni)=2, "
            "wspólny klucz: Id=0, GlobalKey=1, ThreadKey=0") in text
    for secret in ("Jan", "Kowalski", "jan_kowalski", "Nowak", "Pochwała", "k-1"):
        assert secret not in text

    caplog.clear()
    with caplog.at_level("INFO", logger=v.logger.name):
        v._mobile_compare(hebe, {"Pupil": {"FirstName": "Ola", "Surname": "Nowak"}}, pupil, results)
    assert "NIE znaleziony" in caplog.text and "Porównanie uwag" not in caplog.text


def test_compare_rows_masks_differences(v):
    import hebe
    cols = ("id", "slug", "data", "autor")
    out = v._compare_rows([("1", "s", "08.11.2026", "Nowak Anna")], [("1", "s", "2026-11-08", "Anna Nowak")],
                          cols, (0,), hebe.shape)
    assert out == ("WWW=1, mobilne=1, te same id=1, zgodne po id=1, różne pola: "
                   "autor (WWW 'aaaaa aaaa' / mobilne 'aaaa aaaaa'), data (WWW '99.99.9999' / mobilne '9999-99-99')")


def _lesson(**over):
    raw = {"Id": 5, "DateAt": "2026-11-09", "TimeSlot": {"Start": "08:00", "End": "08:45"},
           "Subject": {"Name": "Matematyka"}, "Room": {"Code": "12"},
           "TeacherPrimary": {"DisplayName": "Anna Nowak"}, "Substitution": None}
    raw.update(over)
    return raw


def test_mobile_schedule_rows(v, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC.parent))
    sub = {"Subject": {"Name": "Fizyka"}, "TeacherPrimary": {"DisplayName": "Jan Nowak"}, "Room": {"Code": "7"}}
    rows = v._mobile_schedule_rows([
        _lesson(),
        _lesson(Id=6, Substitution=sub),
        _lesson(Id=7, Substitution={"ClassAbsence": True}),
        _lesson(Id=8, Substitution={**sub, "ChangeDateAt": "2026-11-10",
                                    "TimeSlot": {"Start": "09:00", "End": "09:45"}}),
        _lesson(Id=9, Substitution={"Subject": None, "TeacherPrimary": None}),
        _lesson(Id=10, TimeSlot=None),
        _lesson(Id=11, DateAt=None),
    ], "jan")
    assert rows == [
        ("m5", "jan", "2026-11-09", "08:00-08:45", "Matematyka", "12", "Anna Nowak", ""),
        ("m6", "jan", "2026-11-09", "08:00-08:45", "Fizyka", "7", "Jan Nowak", "ZAST"),
        ("m7", "jan", "2026-11-09", "08:00-08:45", "Matematyka", "12", "Anna Nowak", "ODWOL"),
        ("m8", "jan", "2026-11-10", "09:00-09:45", "Fizyka", "7", "Jan Nowak", "PRZEN"),
        ("m9", "jan", "2026-11-09", "08:00-08:45", "Matematyka", "12", "Anna Nowak", "ODWOL"),
    ]


def test_mobile_timetable_frequency_grade_rows(v, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC.parent))
    exams = [{"Id": 1, "TypeId": 2, "DeadlineAt": "2026-11-12", "Subject": {"Name": "Biologia"},
              "Content": "<p>Dział 3</p>", "Creator": {"DisplayName": "Anna Nowak"}}]
    homework = [{"Id": 2, "DeadlineAt": None, "DateAt": "2026-11-13", "Subject": {"Name": "Fizyka"},
                 "Content": "Zad. 1", "Creator": None}]
    assert v._mobile_timetable_rows(exams, homework, "jan") == [
        ("1", "jan", "2026-11-12", "Biologia", "Kartkówka", "Dział 3", "Anna Nowak"),
        ("2", "jan", "2026-11-13", "Fizyka", "Zadanie domowe", "Zad. 1", ""),
    ]
    attendance = [{"Id": 3, "DayAt": "2026-11-05", "TimeSlot": {"Start": "08:00:00"},
                   "PresenceType": {"CategoryId": 1}},
                  {"Id": 4, "DayAt": "2026-11-05", "TimeSlot": None, "PresenceType": None}]
    assert v._mobile_frequency_rows(attendance, "jan") == [("m3", "jan", "2026-11-05", "08:00", 1)]
    grades = [{"ContentRaw": "47 (%)", "Content": "47", "CreatedAt": "2026-10-03T10:11:12.123",
               "Column": {"Id": 9, "PeriodId": 11, "Name": "Kartkówka 1", "Subject": {"Name": "Chemia"},
                          "Category": {"Name": "Kartkówki"}}},
              {"Content": "5", "Column": {"Id": 10, "PeriodId": 11, "Name": "", "Category": None}}]
    assert v._mobile_grade_rows(grades, "jan") == [
        ("9", "jan", "Chemia", "47 (%)", "03.10.2026", "Kartkówki: Kartkówka 1", "11"),
        ("10", "jan", "", "5", "", "", "11"),
    ]
