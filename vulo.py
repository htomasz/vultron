import pickle, requests, sqlite3, json, os
from datetime import datetime

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [GRADES] {message}")

# HA Token pobieramy z systemu, resztę z pliku pkl
with open('/data/options.json') as f: config = json.load(f)
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'

try:
    if not os.path.exists(COOKIE_PATH): 
        log("Brak pliku vul.pkl - zaloguj się najpierw.")
        exit(0)
        
    with open(COOKIE_PATH, 'rb') as f: bundle = pickle.load(f)
    
    session = requests.Session()
    for c in bundle['cookies']: session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS grades (id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT, data TEXT, PRIMARY KEY(id_kolumny, student_slug))')

    for s in bundle['students']:
        # POBIERANIE DANYCH Z PKL (DYNAMICZNIE DLA KAŻDEGO DZIECKA)
        city = s.get('city')
        app_key = s.get('key')
        period_id = s.get('periodId')
        student_slug = s.get('slug')
        display_name = s.get('uczen', 'Nieznany') # W Context pole to 'uczen'

        if not city or not period_id or not app_key:
            log(f"Pominięto {display_name} - brak kompletnych danych (city/periodId/key)")
            continue

        log(f"Synchronizacja ocen dla: {display_name}...")

        # Wywołanie API z poprawnymi parametrami
        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Oceny", 
                          params={'key': app_key, 'idOkresKlasyfikacyjny': period_id})
        
        if res.status_code == 200:
            new_grades = 0
            lista_ha = []
            data_json = res.json()
            
            for p in data_json.get('ocenyPrzedmioty', []):
                nazwa_p = p['przedmiotNazwa']
                oceny_str = []
                for kol in p.get('kolumnyOcenyCzastkowe', []):
                    id_k = str(kol.get('idKolumny'))
                    for o in kol.get('oceny', []):
                        wpis, dt = o.get('wpis'), o.get('dataOceny')
                        oceny_str.append(f"{wpis} ({dt[:5]})")
                        
                        cursor.execute("SELECT ocena FROM grades WHERE id_kolumny=? AND student_slug=?", (id_k, student_slug))
                        if cursor.fetchone() is None:
                            cursor.execute("INSERT INTO grades VALUES (?,?,?,?,?)", (id_k, student_slug, nazwa_p, wpis, dt))
                            new_grades += 1
                
                if oceny_str: 
                    lista_ha.append({"przedmiot": nazwa_p, "oceny_ciag": "  ".join(oceny_str)})
            
            # WYSYŁKA DO HOME ASSISTANT
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_oceny_{student_slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={
                    "state": new_grades, 
                    "attributes": {
                        "lista_przedmiotow": lista_ha, 
                        "friendly_name": f"Oceny: {display_name}", 
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                })
            log(f"Zaktualizowano oceny: {display_name} (Nowe: {new_grades})")
        else:
            log(f"Błąd API ({res.status_code}) dla {display_name}")

    conn.commit()
    conn.close()
except Exception as e: 
    log(f"Błąd krytyczny: {e}")
