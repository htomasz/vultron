import json, os, time
from websocket import create_connection

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [UI-SETUP] {message}")

token = os.getenv("SUPERVISOR_TOKEN")
url = "ws://supervisor/core/websocket"

def setup_resources():
    try:
        ws = create_connection(url)
        ws.recv()
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok": return

        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        existing = [r['url'] for r in json.loads(ws.recv()).get("result", [])]

        cards = [
            "/local/vultron/vultron-card.js",
            "/local/vultron/vultron-grades-card.js",
            "/local/vultron/vultron-work-card.js",
            "/local/vultron/vultron-uwagi-card.js",
            "/local/vultron/vultron-messages-card.js"
        ]

        msg_id = 2
        for card in cards:
            if card not in existing:
                log(f"Rejestrowanie: {card}")
                ws.send(json.dumps({"id": msg_id, "type": "lovelace/resources/create", "res_type": "module", "url": card}))
                ws.recv()
                msg_id += 1
        ws.close()
    except Exception as e: log(f"Błąd: {e}")

if __name__ == "__main__": setup_resources()
