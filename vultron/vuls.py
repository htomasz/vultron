import pickle, requests, json, os, re, time, sqlite3
from datetime import datetime, timedelta

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WORK] {message}")

def clean_html(raw):
    return re.sub('<.*?>', '', raw).replace('&nbsp;', ' ').strip() if raw else "Brak opisu"

# Pobieranie tokenu HA
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH = '/data/vul.pkl'
DB_PATH = '/data/vultron.db'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as f:
                bundle = pickle.load(f)
            if bundle: break
        except: time.sleep(1)

    if not bundle:
        log("Nie udało się odczytać pliku sesji (Race Condition).")
        exit(1)

    # 1. PRZYGOTOWANIE BAZY
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS timetable
                      (id TEXT, student_slug TEXT, data TEXT, przedmiot TEXT,
                       typ TEXT, opis TEXT, autor TEXT,
                       PRIMARY KEY(id, student_slug))''')

    session = requests.Session()
    for c in bundle.get('cookies', []):
        session.cookies.set(c.get('name'), c.get('value'))

    # Zakres dat: od dziś do 61 dni w przód
    now_dt = datetime.now()
    d_od = now_dt.strftime('%Y-%m-%dT00:00:00.000Z')
    d_do = (now_dt + timedelta(days=61)).strftime('%Y-%m-%dT23:59:59.999Z')

    for s in bundle.get('students', []):
        # DYNAMICZNE DANE Z PKL
        display_name = s.get('uczen', 'Nieznany')
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')

        if not city or not app_key:
            log(f"Pominięto {display_name} - brak miasta lub klucza.")
            continue

        log(f"Pobieram terminarz dla: {display_name} (Miasto: {city})...")

        # Pobieranie głównej listy wydarzeń
        api_main_url = f"https://uczen.eduvulcan.pl/{city}/api/SprawdzianyZadaniaDomowe"
        res = session.get(api_main_url, params={'key': app_key, 'dataOd': d_od, 'dataDo': d_do}, timeout=20)

        if res.status_code == 200:
            for i in res.json():
                # Wybór odpowiedniego endpointu dla szczegółów
                endpoint = "ZadanieDomoweSzczegoly" if i.get('typ') == 4 else "SprawdzianSzczegoly"
                d_url = f"https://uczen.eduvulcan.pl/{city}/api/{endpoint}"

                # Pobieranie szczegółów (opis, nauczyciel itd.)
                dr = session.get(d_url, params={'key': app_key, 'id': i.get('id')}, timeout=15)
                if dr.status_code == 200:
                    dj = dr.json()

                    # Mapowanie typów (zachowane oryginalne)
                    typ_txt = {
                        1: "Sprawdzian",
                        2: "Kartkówka",
                        3: "Klasówka",
                        4: "Zadanie domowe"
                    }.get(i.get('typ'), "Inne")

                    opis_czysty = clean_html(dj.get('opis') or dj.get('temat'))
                    autor_n = dj.get('nauczycielImieNazwisko', '')

                    # 3. ZAPIS DO BAZY
                    cursor.execute("INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
                        (str(i.get('id')), student_slug, dj.get('data',''), dj.get('przedmiotNazwa',''), typ_txt, opis_czysty, autor_n))

                # Punkt 6: Krótkie opóźnienie (Rate Limiting)
                time.sleep(0.5)

            conn.commit()

        # 4. ODCZYT Z BAZY (Budowanie listy dla HA)
        today_iso = now_dt.strftime('%Y-%m-%d')
        cursor.execute('''SELECT data, przedmiot, typ, opis, autor
                          FROM timetable
                          WHERE student_slug=? AND data >= ?
                          ORDER BY data ASC''', (student_slug, today_iso))
        db_rows = cursor.fetchall()

        proc = []
        for row in db_rows:
            proc.append({
                "data": row[0].split('T')[0],
                "przedmiot": row[1],
                "typ": row[2],
                "opis": row[3],
                "autor": row[4]
            })

        # 5. WYSYŁKA DO HOME ASSISTANT
        sensor_id = f"sensor.vultron_terminarz_{student_slug}"
        requests.post(
            f"http://supervisor/core/api/states/{sensor_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json={
                "state": len(proc),
                "attributes": {
                    "lista": proc,
                    "friendly_name": f"Terminarz: {display_name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            },
            timeout=10
        )
        log(f"Zaktualizowano terminarz: {display_name} (Wpisy: {len(proc)})")

    conn.close()
except Exception as e:
    log(f"Błąd krytyczny: {e}")
