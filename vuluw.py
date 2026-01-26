import pickle, requests, sqlite3, json, os
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [REMARKS] {message}")

# HA Token i ścieżki
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
DB_PATH = '/data/vultron.db'
COOKIE_PATH = '/data/vul.pkl'

def sync_remarks(student_slug, json_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tabela przechowująca ID uwag, aby wykrywać nowe
    cursor.execute('''CREATE TABLE IF NOT EXISTS remarks 
                      (remark_id TEXT, student_slug TEXT, PRIMARY KEY(remark_id, student_slug))''')

    new_count = 0
    lista_ha = []
    nowe_uwagi_txt = []

    for item in json_data:
        r_id = str(item.get('id'))
        tresc = item.get('tresc', '')
        autor = item.get('autor', '')
        data_w = item.get('data', '').split('T')[0]
        kat = item.get('kategoria', '')
        punkty = item.get('liczbaPunktow')

        # Określenie typu (Heurystyka)
        typ_wpisu = "informacja"
        tresc_lower = tresc.lower()
        if "pochwała" in tresc_lower or "pochwala" in tresc_lower:
            typ_wpisu = "pozytywna"
        elif "uwaga" in tresc_lower or "adnotacja" in tresc_lower:
            typ_wpisu = "negatywna"

        wpis = {
            "id": r_id,
            "data": data_w,
            "tresc": tresc,
            "autor": autor,
            "kategoria": kat,
            "punkty": punkty,
            "typ": typ_wpisu
        }
        lista_ha.append(wpis)

        # Sprawdzenie czy wpis jest nowy w bazie SQL
        cursor.execute("SELECT remark_id FROM remarks WHERE remark_id=? AND student_slug=?", (r_id, student_slug))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO remarks VALUES (?,?)", (r_id, student_slug))
            new_count += 1
            nowe_uwagi_txt.append(f"{kat}: {tresc[:50]}...")

    conn.commit()
    conn.close()
    return lista_ha, new_count, nowe_uwagi_txt

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl. Czekam na proces logowania...")
        exit(0)

    with open(COOKIE_PATH, 'rb') as f:
        bundle = pickle.load(f)

    session = requests.Session()
    for c in bundle['cookies']: 
        session.cookies.set(c['name'], c['value'])

    for s in bundle['students']:
        # DYNAMICZNE DANE Z NOWEGO PKL
        display_name = s.get('uczen', 'Nieznany')
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')

        if not city or not app_key:
            log(f"Pominięto {display_name} - brak miasta lub klucza.")
            continue

        log(f"Pobieram uwagi dla: {display_name} (Miasto: {city})")
        
        # URL budowany dynamicznie
        api_url = f"https://uczen.eduvulcan.pl/{city}/api/Uwagi"
        res = session.get(api_url, params={'key': app_key}, timeout=20)
        
        if res.status_code == 200:
            # Przetwarzanie uwag i sprawdzanie nowości w DB
            lista, nowe_qty, nowe_txt = sync_remarks(student_slug, res.json())
            
            # Sortowanie: najnowsze na górze
            lista.sort(key=lambda x: x['data'], reverse=True)

            # WYSYŁKA DO HOME ASSISTANT
            ha_url = f"http://supervisor/core/api/states/sensor.vultron_uwagi_{student_slug}"
            payload = {
                "state": nowe_qty,
                "attributes": {
                    "uwagi": lista,
                    "nowe_uwagi_lista": nowe_txt,
                    "friendly_name": f"Uwagi: {display_name}",
                    "student_name": display_name,
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "icon": "mdi:alert-decagram"
                }
            }
            
            requests.post(
                ha_url, 
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, 
                json=payload,
                timeout=10
            )
            log(f"Zakończono dla {display_name}. Znaleziono nowych: {nowe_qty}")
        else:
            log(f"Błąd API ({res.status_code}) dla {display_name}")

except Exception as e:
    log(f"Błąd krytyczny: {e}")
