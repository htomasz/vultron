"""edu_db.db_session / db_session_sync: lock, połączenie, zamknięcie — i nic więcej.

Sesja nie może robić commit ani rollback: kod robi je jawnie w środku bloku
(commit, potem odczyt do publikacji). Automatyczny commit przy wyjściu zmieniłby,
co zostaje zapisane przy błędzie."""
import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vultron"))
import edu_db


class _Conn:
    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append("close")

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")


def test_async_session_closes_and_releases_lock():
    lock, conn = asyncio.Lock(), _Conn()

    async def run():
        async with edu_db.db_session(lock, lambda: conn) as c:
            assert c is conn and lock.locked()
        return lock.locked()
    assert asyncio.run(run()) is False
    assert conn.calls == ["close"]


def test_async_session_closes_on_error_without_rollback():
    lock, conn = asyncio.Lock(), _Conn()

    async def run():
        async with edu_db.db_session(lock, lambda: conn):
            raise ValueError("błąd w środku")
    with pytest.raises(ValueError):
        asyncio.run(run())
    assert conn.calls == ["close"] and not lock.locked()


def test_sync_session_closes_and_releases_lock():
    lock, conn = threading.Lock(), _Conn()
    with edu_db.db_session_sync(lock, lambda: conn) as c:
        assert c is conn and lock.locked()
    assert conn.calls == ["close"] and not lock.locked()


def test_sync_session_closes_on_error_without_rollback():
    lock, conn = threading.Lock(), _Conn()
    with pytest.raises(KeyError):
        with edu_db.db_session_sync(lock, lambda: conn):
            raise KeyError("x")
    assert conn.calls == ["close"] and not lock.locked()


def test_failed_connect_releases_lock():
    lock = threading.Lock()

    def boom():
        raise OSError("baza niedostępna")
    with pytest.raises(OSError):
        with edu_db.db_session_sync(lock, boom):
            pass
    assert not lock.locked()
