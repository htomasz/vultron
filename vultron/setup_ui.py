import json, os, time, re
from websocket import create_connection

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [UI-SETUP] {message}")

token = os.getenv("SUPERVISOR_TOKEN")
url = "ws://supervisor/core/websocket"

# Pobranie wersji z config.yaml
def get_version():
    try:
        with open("config.yaml", "r") as f:
            content = f.read()
            match = re.search(r'version:\s*["\']?([^"\']+)["\']?', content)
            return match.group(1) if match else "1.0"
    except: return "1.0"

def setup_resources():
    version = get_version()
    try:
        ws = create_connection(url)
        ws.recv()
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok": return

        # Pobieramy obecne zasoby
        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        raw_res = json.loads(ws.recv()).get("result", [])
        
        # Tworzymy listę URLi bez parametrów ?v= do porównania
        existing_urls = [re.sub(r'\?v=.*', '', r['url']) for r in raw_res]
        existing_ids = {re.sub(r'\?v=.*', '', r['url']): r['id'] for r in raw_res}

        # Szukamy plików vultron-*.js w bieżącym folderze
        cards = [f for f in os.listdir('.') if f.startswith('vultron-') and f.endswith('.js')]
        
        msg_id = 2
        for card_file in cards:
            base_url = f"/local/vultron/{card_file}"
            versioned_url = f"{base_url}?v={version}"
            
            if base_url in existing_urls:
                # Jeśli wersja w HA jest inna niż obecna - aktualizujemy
                current_full_url = next((r['url'] for r in raw_res if base_url in r['url']), "")
                if versioned_url != current_full_url:
                    log(f"Aktualizacja wersji: {card_file} -> {version}")
                    ws.send(json.dumps({
                        "id": msg_id,
                        "type": "lovelace/resources/update",
                        "resource_id": existing_ids[base_url],
                        "url": versioned_url
                    }))
                    ws.recv()
            else:
                # Jeśli nie ma - dodajemy
                log(f"Rejestrowanie nowej karty: {card_file}")
                ws.send(json.dumps({
                    "id": msg_id, 
                    "type": "lovelace/resources/create", 
                    "res_type": "module", 
                    "url": versioned_url
                }))
                ws.recv()
            msg_id += 1
            
        ws.close()
        log("Konfiguracja UI zakończona pomyślnie.")
    except Exception as e: log(f"Błąd: {e}")

if __name__ == "__main__": setup_resources()