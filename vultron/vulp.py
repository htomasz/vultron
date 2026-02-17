import pickle
import requests
import json
import os
import time
import sqlite3
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [PLAN] {message}")

# Mapowanie statusów (3 i 4 zgodnie z Twoją listą)
MAPA_STATUSOW = {
    0: "",       # Brak zmian
    1: "ZAST",   # Zastępstwo
    2: "PRZEN",  # Przeniesienie
    3: "ODWOL",  # Odwołane
    4: "NIEOB"   # Zajęcia nieobecne (np. wycieczka)
}

try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    log(f"Błąd konfiguracji: {e}")
    exit(1)

COOKIE_PATH = '/data/vul.pkl'
DB_PATH = '/data/vultron.db'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def get_dates_range():
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    return (start.strftime('%Y-%m-%dT00:00:00.000Z'), end.strftime('%Y-%m-%dT23:59:59.999Z'))

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji.")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as file:
                bundle = pickle.load(file)
            if bundle: break
        except: time.sleep(1)

    if not bundle:
        exit(1)

    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
except Exception as e:
    log(f"Błąd sesji: {e}"); exit(1)

session = requests.Session()
for c in cookies: session.cookies.set(c['name'], c['value'])

# Inicjalizacja bazy
conn = sqlite3.connect(DB_PATH, timeout=20)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS schedule
                      (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT,
                       przedmiot TEXT, sala TEXT, prowadzacy TEXT, status TEXT)''')

date_od, date_do = get_dates_range()

# Zakres filtra dla HA: od poniedziałku poprzedniego tygodnia
today_dt = datetime.now()
limit_date = (today_dt - timedelta(days=today_dt.weekday() + 7)).strftime('%Y-%m-%d')

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city, app_key, slug = student.get('city'), student.get('key'), student.get('slug', 'unknown')
    if not city or not app_key: continue

    log(f"Synchronizacja planu: {display_name}...")

    try:
        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec",
                          params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do, 'zakresDanych': '2'}, timeout=25)
        if res.status_code == 200:
            plan_raw = res.json()
            for lekcja in plan_raw:
                nr_adn = int(lekcja.get('adnotacja', 0))
                st_code = MAPA_STATUSOW.get(nr_adn, "")
                changes = lekcja.get('zmiany', [])
                info_text = " ".join([(c.get('informacjeNieobecnosc') or "").lower() for c in changes])
                if "zwolnieni" in info_text or "okienko" in info_text: st_code = "ODWOL"

                przedmiot = lekcja.get('przedmiot')
                if not przedmiot: przedmiot = "Lekcja odwołana" if st_code == "ODWOL" else "Zajęcia"

                # Oryginalne formatowanie godziny dla karty JS
                g_od = lekcja['godzinaOd'].split('T')[1][:5]
                g_do = lekcja['godzinaDo'].split('T')[1][:5]
                godz_l = f"{g_od}-{g_do}"
                data_l = lekcja['data'].split('T')[0]

                l_id = f"{slug}_{lekcja['data']}_{lekcja['godzinaOd']}"
                cursor.execute("INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                    (l_id, slug, data_l, godz_l, przedmiot, lekcja.get('sala', ''), lekcja.get('prowadzacy', ''), st_code))
            conn.commit()

        # Pobieranie z bazy z limitem 2 tygodni wstecz
        cursor.execute("SELECT data, godzina, przedmiot, sala, prowadzacy, status FROM schedule WHERE student_slug=? AND data >= ? ORDER BY data ASC, godzina ASC", (slug, limit_date))
        db_rows = cursor.fetchall()

        processed = [{"d": r[0], "g": r[1], "p": r[2], "s": r[3], "n": r[4], "st": r[5]} for r in db_rows]

        today_str = datetime.now().strftime('%Y-%m-%d')
        requests.post(f"http://supervisor/core/api/states/sensor.vultron_plan_{slug}",
            headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
            json={
                "state": len([l for l in processed if l['d'] == today_str]),
                "attributes": {
                    "lekcje": processed,
                    "friendly_name": f"Plan: {display_name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            }, timeout=10)
    except Exception as e: log(f"Błąd {display_name}: {e}")

conn.close()