import pickle, requests, sqlite3, json, os
from datetime import datetime

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [GRADES] {message}")

with open('/data/options.json') as f: config = json.load(f)
CITY, PERIOD_ID, HA_TOKEN = config.get('city_slug'), config.get('period_id'), os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'

try:
    if not os.path.exists(COOKIE_PATH): exit(0)
    with open(COOKIE_PATH, 'rb') as f: bundle = pickle.load(f)
    session = requests.Session()
    for c in bundle['cookies']: session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS grades (id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT, data TEXT, PRIMARY KEY(id_kolumny, student_slug))')

    for s in bundle['students']:
        res = session.get(f"https://uczen.eduvulcan.pl/{CITY}/api/Oceny", params={'key': s['key'], 'idOkresKlasyfikacyjny': PERIOD_ID})
        if res.status_code == 200:
            new_grades = 0
            lista_ha = []
            for p in res.json().get('ocenyPrzedmioty', []):
                nazwa_p = p['przedmiotNazwa']
                oceny_str = []
                for kol in p.get('kolumnyOcenyCzastkowe', []):
                    id_k = str(kol.get('idKolumny'))
                    for o in kol.get('oceny', []):
                        wpis, dt = o.get('wpis'), o.get('dataOceny')
                        oceny_str.append(f"{wpis} ({dt[:5]})")
                        cursor.execute("SELECT ocena FROM grades WHERE id_kolumny=? AND student_slug=?", (id_k, s['slug']))
                        if cursor.fetchone() is None:
                            cursor.execute("INSERT INTO grades VALUES (?,?,?,?,?)", (id_k, s['slug'], nazwa_p, wpis, dt))
                            new_grades += 1
                if oceny_str: lista_ha.append({"przedmiot": nazwa_p, "oceny_ciag": "  ".join(oceny_str)})
            
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_oceny_{s['slug']}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={"state": new_grades, "attributes": {"lista_przedmiotow": lista_ha, "friendly_name": f"Oceny: {s['name']}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}})
            log(f"Zaktualizowano oceny: {s['name']}")
    conn.commit()
    conn.close()
except Exception as e: log(f"Błąd: {e}")
