import pickle, requests, sqlite3, json, os
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [REMARKS] {message}")

with open('/data/options.json') as f:
    config = json.load(f)

CITY = config.get('city_slug')
DB_PATH = '/data/vultron.db'
COOKIE_PATH = '/data/vul.pkl'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def sync_remarks(student, json_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tabela przechowująca ID uwag, aby wykrywać nowe
    cursor.execute('''CREATE TABLE IF NOT EXISTS remarks 
                      (remark_id TEXT, student_slug TEXT, PRIMARY KEY(remark_id, student_slug))''')

    new_count = 0
    lista_ha = []
    nowe_uwagi = []

    for item in json_data:
        r_id = str(item.get('id'))
        tresc = item.get('tresc', '')
        autor = item.get('autor', '')
        data_w = item.get('data', '').split('T')[0]
        kat = item.get('kategoria', '')
        punkty = item.get('liczbaPunktow')

        # Określenie typu (Heurystyka na podstawie treści, jeśli Vulcan nie podaje wprost)
        typ_wpisu = "informacja"
        if "pochwała" in tresc.lower() or "pochwala" in tresc.lower():
            typ_wpisu = "pozytywna"
        elif "uwaga" in tresc.lower() or "adnotacja" in tresc.lower():
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

        # Sprawdzenie czy wpis jest nowy
        cursor.execute("SELECT remark_id FROM remarks WHERE remark_id=? AND student_slug=?", (r_id, student['slug']))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO remarks VALUES (?,?)", (r_id, student['slug']))
            new_count += 1
            nowe_uwagi.append(f"{kat}: {tresc[:50]}...")

    conn.commit()
    conn.close()
    return lista_ha, new_count, nowe_uwagi

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji. Czekam na vul.py...")
        exit(0)

    with open(COOKIE_PATH, 'rb') as f:
        bundle = pickle.load(f)

    session = requests.Session()
    for c in bundle['cookies']: session.cookies.set(c['name'], c['value'])

    for s in bundle['students']:
        log(f"Pobieram uwagi dla: {s['name']}")
        res = session.get(f"https://uczen.eduvulcan.pl/{CITY}/api/Uwagi", params={'key': s['key']})
        
        if res.status_code == 200:
            lista, nowe_qty, nowe_txt = sync_remarks(s, res.json())
            
            # Sortowanie: najnowsze na górze
            lista.sort(key=lambda x: x['data'], reverse=True)

            ha_url = f"http://supervisor/core/api/states/sensor.vultron_uwagi_{s['slug']}"
            requests.post(ha_url, headers={"Authorization": f"Bearer {HA_TOKEN}"}, json={
                "state": nowe_qty,
                "attributes": {
                    "uwagi": lista,
                    "nowe_uwagi_lista": nowe_txt,
                    "friendly_name": f"Uwagi: {s['name']}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            })
            log(f"Zakończono dla {s['name']}. Znaleziono nowych: {nowe_qty}")

except Exception as e:
    log(f"Błąd krytyczny: {e}")
