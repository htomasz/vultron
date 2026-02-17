import json
import os
import time
import re
from websocket import create_connection

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [UI-SETUP] {message}")

token = os.getenv("SUPERVISOR_TOKEN")
url = "ws://supervisor/core/websocket"

# Pobranie wersji bezpośrednio z config.yaml
def get_version():
    try:
        config_path = "config.yaml" if os.path.exists("config.yaml") else "/app/config.yaml"
        with open(config_path, "r") as f:
            content = f.read()
            # Punkt 5: Zabezpieczony regex
            match = re.search(r'version:\s*["\']?([^"\']+)["\']?', content)
            return match.group(1) if match else "1.0"
    except:
        return "1.0"

def setup_resources():
    version = get_version()
    ws = None
    max_retries = 10

    # Pętla RETRY: Czekaj aż WebSocket będzie gotowy
    for attempt in range(max_retries):
        try:
            ws = create_connection(url, timeout=10)
            ws.recv() # Powitanie
            ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth_res = json.loads(ws.recv())

            if auth_res.get("type") == "auth_ok":
                log(f"Połączono z API Home Assistant (próba {attempt + 1})")
                time.sleep(1) # Stabilizacja sesji przed wysłaniem komend (Punkt 7)
                break
            else:
                log("Błąd autoryzacji tokena.")
                if ws:
                    ws.close()
                return
        except Exception as e:
            if attempt < max_retries - 1:
                log(f"Oczekiwanie na API Home Assistant... (Próba {attempt + 1}/{max_retries})")
                time.sleep(5)
            else:
                log(f"BŁĄD KRYTYCZNY: Nie udało się połączyć z API po {max_retries} próbach: {e}")
                return

    # Jeśli doszliśmy tutaj, mamy otwarte i autoryzowane połączenie ws
    try:
        # Pobieramy obecne zasoby
        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        raw_res_data = json.loads(ws.recv())
        raw_res = raw_res_data.get("result", [])

        # Mapujemy URL (bez wersji) na ID i pełny URL (Punkt 5 - .get())
        existing_resources = {re.sub(r'\?v=.*', '', r.get('url', '')): r.get('id') for r in raw_res}
        existing_full_urls = {re.sub(r'\?v=.*', '', r.get('url', '')): r.get('url') for r in raw_res}

        # Szukamy plików vultron-*.js
        path = "." if os.path.exists("vultron-card.js") else "/app"
        cards = [f for f in os.listdir(path) if f.startswith('vultron-') and f.endswith('.js')]

        msg_id = 2
        for card_file in cards:
            base_url = f"/local/vultron/{card_file}"
            versioned_url = f"{base_url}?v={version}"

            if base_url in existing_resources:
                # Aktualizacja jeśli wersja w HA jest inna
                if versioned_url != existing_full_urls.get(base_url):
                    log(f"Aktualizacja wersji zasobu: {card_file} -> {version}")
                    ws.send(json.dumps({
                        "id": msg_id,
                        "type": "lovelace/resources/update",
                        "resource_id": existing_resources[base_url],
                        "url": versioned_url
                    }))
                    ws.recv()
            else:
                # Rejestracja nowej karty
                log(f"Rejestrowanie nowej karty: {card_file} (v{version})")
                ws.send(json.dumps({
                    "id": msg_id,
                    "type": "lovelace/resources/create",
                    "res_type": "module",
                    "url": versioned_url
                }))
                ws.recv()
            msg_id += 1

        log("Konfiguracja UI zakończona sukcesem.")
    except Exception as e:
        log(f"Błąd podczas rejestracji zasobów: {e}")
    finally:
        # Punkt 7: Zawsze zamykamy WebSocket
        if ws:
            ws.close()
            log("Połączenie WebSocket zamknięte.")

if __name__ == "__main__":
    setup_resources()
