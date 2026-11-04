"""Zapytania SQL z nazwami tabel są stałymi (bez sklejania w locie), więc
bandit/ruff skanują je bez wyjątków. Test pilnuje, że stałe odpowiadają
dawnym f-stringom i że każde wywołanie _delete_missing ma swój wpis."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "vultron" / "vultron.py"
TREE = ast.parse(SRC.read_text(encoding="utf-8"))

# Lista tabel retencji z 7.1.13 (przed zamianą na stałe), w tej kolejności.
PRUNABLE_TABLES_7_1_13 = (
    "schedule", "remarks", "timetable", "frequency",
    "free_days", "meetings", "frequency_stats", "lucky_number",
    "messages", "przedszkole_obecnosc", "przedszkole_plan", "przedszkole_jadlospis",
    "przedszkole_zebrania", "przedszkole_oplaty", "przedszkole_wiadomosci",
)


def _constant(name):
    for node in TREE.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"Brak stałej {name}")


def test_delete_missing_sql_matches_old_fstrings():
    for (table, id_col), (select_sql, delete_sql) in _constant("_DELETE_MISSING_SQL").items():
        # Słowo po słowie zamiast f-stringa z SQL (ten też łapałby ruff S608).
        assert select_sql.split() == ["SELECT", id_col + ",", "data", "FROM", table,
                                      "WHERE", "student_slug=?"]
        assert delete_sql.split() == ["DELETE", "FROM", table, "WHERE", "student_slug=?",
                                      "AND", id_col + "=?"]


def test_prune_sql_matches_old_fstrings_and_tables():
    prune = _constant("_PRUNE_SQL")
    assert tuple(prune) == PRUNABLE_TABLES_7_1_13
    for table, (select_sql, delete_sql) in prune.items():
        assert select_sql.split() == ["SELECT", "rowid,", "data", "FROM", table]
        assert delete_sql.split() == ["DELETE", "FROM", table, "WHERE", "rowid=?"]


def test_every_delete_missing_call_has_sql():
    known = set(_constant("_DELETE_MISSING_SQL"))
    calls = [n for n in ast.walk(TREE) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_delete_missing"]
    assert calls
    for call in calls:
        table, id_col = (ast.literal_eval(a) for a in call.args[1:3])
        assert (table, id_col) in known, f"Brak SQL dla {table}/{id_col}"
