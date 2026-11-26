"""Wspólne fikstury testów vultron.py (eduVULCAN): moduł pod testem, baza, publikacja.

vultron.py przy imporcie czyta /data/options.json, pisze log do /data i wymaga
tokena HA. W CI plik przygotowuje gdansk-tests.yml, lokalnie scripts/ci-local.sh.
Testy NIGDY nie zapisują /data/options.json."""
import importlib.util
import os
import sqlite3
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


@pytest.fixture
def published(v, monkeypatch, db):
    out = {}

    async def fake_publish(ha, entity, state, name, attrs):
        out[entity] = (state, attrs)
    monkeypatch.setattr(v, "publish_sensor", fake_publish)
    return out
