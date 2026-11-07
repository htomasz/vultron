"""Testy czystych funkcji vultron.py (eduVULCAN): przycinanie atrybutów, daty,
kasowanie wpisów usuniętych w dzienniku, retencja, czyszczenie HTML.

vultron.py przy imporcie czyta /data/options.json, pisze log do /data i wymaga
tokena HA. W CI plik przygotowuje gdansk-tests.yml. Testy NIGDY nie zapisują
/data/options.json (w prawdziwym kontenerze to konfiguracja użytkownika), a
każdy test bazy działa na własnym pliku tymczasowym (podmieniony DB_PATH)."""
import asyncio
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

