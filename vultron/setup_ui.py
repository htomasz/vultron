import json, os, time, re
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
            match = re.search(r'version:\s*["\']?([^"\']+)["\']?', content)
            return match.group(1) if match else "1.0"
    except:
        return "1.0"

def setup_resources():
    version = get_version()
    try:
        time.sleep(2)
        ws = create_connection(url)
        ws.recv()
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok": return

        # Pobieramy obecne zasoby
        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        raw_res = json.loads(ws.recv()).get("result", [])
        
        # Mapujemy URL (bez wersji) na ID i pełny URL
        existing_resources = {re.sub(r'\?v=.*', '', r['url']): r['id'] for r in raw_res}
        existing_full_urls = {re.sub(r'\?v=.*', '', r['url']): r['url'] for r in raw_res}

        # Szukamy plików vultron-*.js w /app lub folderze bieżącym
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
            
        ws.close()
        log("Konfiguracja UI zakończona.")
    except Exception as e: 
        log(f"Błąd setup_ui: {e}")

if __name__ == "__main__": 
    setup_resources()