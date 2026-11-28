"""Warstwa bazy eduVULCAN (faza 2 refaktoru, claude/refaktor-plan.md).

Jeden wzorzec zamiast powtarzanego w każdym fetcherze
"lock -> db_connect() -> try/finally close". Lock i funkcja połączenia
przychodzą z wywołującego (vultron.py: db_lock / db_lock_thread, db_connect),
więc moduł nie importuje vultron i nie ma własnego stanu.

Sesja NIE robi commit ani rollback: kod robi je jawnie, tam gdzie dziś
(często commit w środku, a potem odczyt do publikacji w tym samym połączeniu).
Automatyczny commit na wyjściu zmieniłby, co zostaje zapisane przy błędzie.
"""
import contextlib

from edu_util import _normalize_date_prefix


@contextlib.asynccontextmanager
async def db_session(lock, connect):
    """async with db_session(db_lock, db_connect) as conn: ..."""
    async with lock:
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()


@contextlib.contextmanager
def db_session_sync(lock, connect):
    """with db_session_sync(db_lock_thread, db_connect) as conn: ... (wątki)"""
    with lock:
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()


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
