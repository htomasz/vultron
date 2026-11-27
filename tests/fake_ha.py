"""Atrapa Home Assistant dla tests/test_startup.py: REST i WebSocket.

Użycie: python fake_ha.py PORT PLIK_ZDARZEŃ TOKEN
Odpowiada jak HA wprost (/api, /api/websocket) i przez Supervisor
(/core/api, /core/websocket). Każde zdarzenie dopisuje jako linię JSON do
PLIK_ZDARZEŃ; po starcie tworzy PLIK_ZDARZEŃ.ready.
"""
import fcntl
import json
import re
import socket
import struct
import sys
from pathlib import Path

from aiohttp import web

PORT, EVENTS, TOKEN = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1


def _loopback_up() -> None:
    # Świeża przestrzeń sieci (unshare -n) ma wyłączone lo; bez zależności od iproute2.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ifreq = struct.pack("16sH14x", b"lo", 0)
        flags = struct.unpack("16sH14x", fcntl.ioctl(s, SIOCGIFFLAGS, ifreq))[1]
        fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack("16sH14x", b"lo", flags | IFF_UP))
STATES: dict[str, dict] = {}


def _event(**kw) -> None:
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


async def rest(request: web.Request) -> web.Response:
    authorized = request.headers.get("Authorization") == f"Bearer {TOKEN}"
    _event(kind="rest", method=request.method, path=request.path, authorized=authorized)
    if not authorized:
        return web.json_response({"message": "401: Unauthorized"}, status=401)
    path = re.sub(r"^/(core/)?api", "", request.path)
    if path == "/config":
        return web.json_response({"version": "2026.11.0", "time_zone": "Europe/Warsaw"})
    if path.startswith("/states/"):
        entity = path.split("/", 2)[2]
        if request.method == "POST":
            STATES[entity] = await request.json()
            return web.json_response(STATES[entity])
        if entity in STATES:
            return web.json_response(STATES[entity])
    return web.json_response({"message": "Entity not found."}, status=404)


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
    async for msg in ws:
        data = json.loads(msg.data)
        if data.get("type") == "auth":
            ok = data.get("access_token") == TOKEN
            _event(kind="ws_auth", path=request.path, ok=ok)
            await ws.send_json({"type": "auth_ok" if ok else "auth_invalid"})
            if not ok:
                break
            continue
        _event(kind="ws", path=request.path, type=data.get("type", ""))
        result = [] if data.get("type") == "lovelace/resources" else None
        await ws.send_json({"id": data.get("id"), "type": "result", "success": True, "result": result})
    return ws


async def _ready(app: web.Application) -> None:
    Path(f"{EVENTS}.ready").touch()


_loopback_up()
app = web.Application()
app.router.add_get("/api/websocket", websocket)
app.router.add_get("/core/websocket", websocket)
app.router.add_route("*", "/{tail:.*}", rest)
app.on_startup.append(_ready)
web.run_app(app, host="127.0.0.1", port=PORT, print=None, handle_signals=True)
