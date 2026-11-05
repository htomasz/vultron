"""Testy czystych funkcji vultron.py (eduVULCAN): przycinanie atrybutów, daty,
kasowanie wpisów usuniętych w dzienniku, retencja, czyszczenie HTML.

vultron.py przy imporcie czyta /data/options.json, pisze log do /data i wymaga
tokena HA. W CI plik przygotowuje gdansk-tests.yml. Testy NIGDY nie zapisują
/data/options.json (w prawdziwym kontenerze to konfiguracja użytkownika), a
każdy test bazy działa na własnym pliku tymczasowym (podmieniony DB_PATH)."""
import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
