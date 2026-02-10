import pickle, requests, sqlite3, json, os
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [ACHIEVEMENTS] {message}")

HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
DB_PATH = '/data/vultron.db'
COOKIE_PATH = '/data/vul.pkl'

def sync_achievements(student_slug, json_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tabela dla osiągnięć
    cursor.execute('''CREATE TABLE IF NOT EXISTS achievements 
                      (achievement_id TEXT, student_slug TEXT, PRIMARY KEY(achievement_id, student_slug))''')

    new_count = 0
    lista_ha = []
    nowe_txt = []

    for item in json_data:
        a_id = str(item.get('id'))
        tresc = item.get('tresc', '')

        wpis = {
            "id": a_id,
            "tresc": tresc
        }
        lista_ha.append(wpis)

        # Sprawdzenie czy osiągnięcie jest nowe
        cursor.execute("SELECT achievement_id FROM achievements WHERE achievement_id=? AND student_slug=?", (a_id, student_slug))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO achievements VALUES (?,?)", (a_id, student_slug))
            new_count += 1
            nowe_txt.append(f"Nowe osiągnięcie: {tresc[:50]}...")

    conn.commit()
    conn.close()
    return lista_ha, new_count, nowe_txt

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl.")
        exit(0)

    with open(COOKIE_PATH, 'rb') as f:
        bundle = pickle.load(f)

    session = requests.Session()
    for c in bundle['cookies']: 
        session.cookies.set(c['name'], c['value'])

    for s in bundle['students']:
        display_name = s.get('uczen', 'Nieznany')
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')

        if not city or not app_key:
            continue

        log(f"Pobieram osiągnięcia dla: {display_name}")
        
        api_url = f"https://uczen.eduvulcan.pl/{city}/api/Osiagniecia"
        res = session.get(api_url, params={'key': app_key}, timeout=20)
        
        if res.status_code == 200:
            lista, nowe_qty, nowe_txt = sync_achievements(student_slug, res.json())
            
            # WYSYŁKA DO HOME ASSISTANT
            ha_url = f"http://supervisor/core/api/states/sensor.vultron_osiagniecia_{student_slug}"
            payload = {
                "state": len(lista), # Stanem jest łączna liczba osiągnięć
                "attributes": {
                    "osiagniecia": lista,
                    "nowe_osiagniecia": nowe_qty,
                    "friendly_name": f"Osiągnięcia: {display_name}",
                    "student_name": display_name,
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "icon": "mdi:trophy-variant"
                }
            }
            
            requests.post(
                ha_url, 
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, 
                json=payload,
                timeout=10
            )
            log(f"Zakończono dla {display_name}. Łącznie: {len(lista)}")
        else:
            log(f"Błąd API ({res.status_code}) dla {display_name}")

except Exception as e:
    log(f"Błąd krytyczny: {e}")
