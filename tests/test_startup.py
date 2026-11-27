"""Start vultron.py jako prawdziwy proces z atrapą HA (plan refaktoru, faza 0, punkt 3).

Tryby: dodatek HA (SUPERVISOR_TOKEN, HA pod http://supervisor), samodzielny Docker
(HA_URL/HA_WS_URL/HA_TOKEN) i GPE Gdańsk. Każdy przebieg działa w osobnych
przestrzeniach nazw (unshare -rmn):
- sieć: tylko pętla zwrotna, a na niej atrapa HA (tests/fake_ha.py), więc bez internetu,
- montowania: tymczasowe /data, /config i /etc/hosts (supervisor -> 127.0.0.1);
  prawdziwe /data nie jest czytane ani nadpisywane.
Przeglądarka się nie uruchamia (tests/startup_launcher.py).

Sprawdzamy: start i wersję, gotowość HA API, autoryzację REST i WebSocket tym
tokenem, rejestrację kart Lovelace, wejście w pierwszy cykl, brak sekretów
w logu i zakończenie po SIGTERM.

Wymaga: aiohttp, unshare, istniejących punktów montowania /data
i /config (CI: krok "Prepare /data" w workflowach; lokalnie raz: sudo mkdir /config).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "vultron"
TESTS = Path(__file__).resolve().parent
# Losowe przy każdym przebiegu: sprawdzenie "sekret w logu" działa na wartości,
# której nikt nie wpisze do kodu przypadkiem.
TOKEN = secrets.token_hex(16)
PASSWORD = secrets.token_hex(12)
UNSHARE = shutil.which("unshare")
CARDS = sorted(p.name for p in APP_DIR.glob("vultron-*.js"))
_DROP_ENV = ("SUPERVISOR_TOKEN", "HA_TOKEN", "HA_TOKEN_FILE", "HA_URL", "HA_WS_URL")


def _version() -> str:
    m = re.search(r'^version:\s*"([^"]+)"', (APP_DIR / "config.yaml").read_text(encoding="utf-8"), re.M)
    return m.group(1)


def _isolation_problem() -> str | None:
    if importlib.util.find_spec("aiohttp") is None:
        return "brak aiohttp (.github/requirements/tests.txt)"
    if not UNSHARE:
        return "brak programu unshare"
    for mount_point in ("/data", "/config"):
        if not os.path.isdir(mount_point):
            return f"brak punktu montowania {mount_point} (lokalnie raz: sudo mkdir {mount_point})"
    probe = subprocess.run([UNSHARE, "-rmn", "true"], capture_output=True, check=False)
    if probe.returncode != 0:
        return "unshare -rmn niedostępne (przestrzenie nazw użytkownika wyłączone)"
    return None


@pytest.fixture(scope="module", autouse=True)
def _isolation():
    problem = _isolation_problem()
    if problem:
        if os.getenv("GITHUB_ACTIONS"):
            pytest.fail(problem)
        pytest.skip(problem)


def _run(tmp_path: Path, mode: str, wait_for: str, timeout: float = 60) -> dict:
    data, config = tmp_path / "data", tmp_path / "config"
    data.mkdir()
    config.mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost supervisor\n", encoding="utf-8")
    events = data / "fake_ha.jsonl"

    options = {"username": "test@example.com", "password": PASSWORD, "log_level": "info", "test_mode": True}
    if mode == "gdansk":
        options["provider"] = "gdansk"
    (data / "options.json").write_text(json.dumps(options), encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV and "proxy" not in k.lower()}
    env["TZ"] = "Europe/Warsaw"
    if mode == "docker":
        port = 8123
        env.update(HA_TOKEN=TOKEN, HA_URL="http://127.0.0.1:8123/api", HA_WS_URL="ws://127.0.0.1:8123/api/websocket")
    else:
        port = 80   # tryb dodatku: http://supervisor/core/api na porcie domyślnym
        env["SUPERVISOR_TOKEN"] = TOKEN

    script = (
        "set -e; "
        f"mount --bind '{data}' /data; mount --bind '{config}' /config; mount --bind '{hosts}' /etc/hosts; "
        f"'{sys.executable}' '{TESTS / 'fake_ha.py'}' {port} /data/fake_ha.jsonl {TOKEN} & "
        "i=0; while [ ! -e /data/fake_ha.jsonl.ready ]; do i=$((i+1)); [ $i -gt 100 ] && exit 97; sleep 0.1; done; "
        f"exec '{sys.executable}' '{TESTS / 'startup_launcher.py'}'"
    )
    proc = subprocess.Popen([UNSHARE, "-rmn", "sh", "-c", script], cwd=APP_DIR, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    out: list[str] = []
    reader = threading.Thread(target=lambda: out.extend(proc.stdout), daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and wait_for not in "".join(out) and proc.poll() is None:
        time.sleep(0.2)
    reached = wait_for in "".join(out)
    stopped_by_sigterm = False
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
            stopped_by_sigterm = True
        except subprocess.TimeoutExpired:
            pass
    os.killpg(proc.pid, signal.SIGKILL)   # atrapa HA i ewentualne resztki
    code = proc.wait()
    reader.join(5)
    err = proc.stderr.read()
    log = "".join(out)
    assert reached, f"[{mode}] nie doszło do: {wait_for}\n--- stdout ---\n{log[-3000:]}\n--- stderr ---\n{err[-3000:]}"
    ev = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
    return {"log": log, "err": err, "code": code, "sigterm": stopped_by_sigterm, "events": ev}


def _check(r: dict, rest_prefix: str, ws_path: str, status: str, stopped: str) -> None:
    log, err, ev = r["log"], r["err"], r["events"]
    assert f"Uruchamianie Vultron v{_version()}" in log or "Uruchamianie Vultron: GPE" in log
    assert "HA API gotowe." in log
    rest = [e for e in ev if e["kind"] == "rest"]
    assert rest and all(e["authorized"] for e in rest), "REST bez poprawnego tokena"
    assert all(e["path"].startswith(rest_prefix) for e in rest), {e["path"] for e in rest}
    auth = [e for e in ev if e["kind"] == "ws_auth"]
    assert auth and all(e["ok"] and e["path"] == ws_path for e in auth), auth
    types = [e["type"] for e in ev if e["kind"] == "ws"]
    assert "lovelace/resources" in types
    assert types.count("lovelace/resources/create") == len(CARDS)
    assert ("POST", f"{rest_prefix}states/{status}") in {(e["method"], e["path"]) for e in rest}, "brak statusu w HA"
    assert stopped in log, "brak komunikatu o zamknięciu"
    for secret in (TOKEN, PASSWORD):
        assert secret not in log and secret not in err, "sekret w logu"
    assert "Traceback" not in err, err[-3000:]
    assert r["sigterm"] and r["code"] == 0, f"SIGTERM: {r['sigterm']}, kod wyjścia {r['code']}\n{err[-2000:]}"


# Logowanie nie może się udać (brak przeglądarki, brak sieci): czekamy na koniec
# pierwszego cyklu po stronie błędu, potem SIGTERM.
def test_startup_addon(tmp_path):
    r = _run(tmp_path, "addon", "Cykl bez danych (logowanie nieudane)")
    _check(r, "/core/api/", "/core/websocket", "sensor.vultron_status", "Vultron zatrzymany (graceful shutdown).")


def test_startup_docker(tmp_path):
    r = _run(tmp_path, "docker", "Cykl bez danych (logowanie nieudane)")
    _check(r, "/api/", "/api/websocket", "sensor.vultron_status", "Vultron zatrzymany (graceful shutdown).")


def test_startup_gpe(tmp_path):
    r = _run(tmp_path, "gdansk", "[GPE] Następny odczyt")
    _check(r, "/core/api/", "/core/websocket", "sensor.vultron_gdansk_status", "Vultron Gdańsk zatrzymany")
