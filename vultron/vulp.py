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
    return (start.strftime('%Y-%m-%dT00:00:00.000Z'),
            end.strftime('%Y-%m-%dT23:59:59.999Z'))

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji.")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as file:
                bundle = pickle.load(file)
            if bundle:
                break
        except:
            time.sleep(1)

    if not bundle:
        log("Nie udało się odczytać pliku sesji (Race Condition).")
        exit(1)

    # Inicjalizacja bazy
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS schedule
                      (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT,
                       przedmiot TEXT, sala TEXT, prowadzacy TEXT, status TEXT)''')

    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
    session = requests.Session()
    for c in cookies:
        session.cookies.set(c['name'], c['value'])

except Exception as e:
    log(f"Błąd sesji: {e}")
    exit(1)

date_od, date_do = get_dates_range()
date_od_short = date_od.split('T')[0]
date_do_short = date_do.split('T')[0]

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city = student.get('city')
    app_key = student.get('key')
    slug = student.get('slug', 'unknown')

    if not city or not app_key:
        continue

    log(f"Synchronizacja planu: {display_name}...")

    try:
        res = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec",
            params={
                'key': app_key,
                'dataOd': date_od,
                'dataDo': date_do,
                'zakresDanych': '2'
            },
            timeout=25
        )

        if res.status_code == 200:
            plan_raw = res.json()

            for lekcja in plan_raw:
                nr_adn = int(lekcja.get('adnotacja', 0))
                st_code = MAPA_STATUSOW.get(nr_adn, "")

                changes = lekcja.get('zmiany', [])
                info_text = " ".join(
                    [(c.get('informacjeNieobecnosc') or "").lower() for c in changes]
                )

                if "zwolnieni" in info_text or "okienko" in info_text:
                    st_code = "ODWOL"

                przedmiot = lekcja.get('przedmiot')
                if not przedmiot:
                    przedmiot = "Lekcja odwołana" if st_code == "ODWOL" else "Zajęcia"

                data_lekcji = lekcja.get('data', '').split('T')[0]

                # ✅ POPRAWIONE WYCIĄGANIE GODZIN
                godz_od_raw = lekcja.get('godzinaOd')
                godz_do_raw = lekcja.get('godzinaDo')

                if godz_od_raw and "T" in godz_od_raw:
                    godz_od = godz_od_raw.split("T")[1][:5]
                else:
                    godz_od = ""

                if godz_do_raw and "T" in godz_do_raw:
                    godz_do = godz_do_raw.split("T")[1][:5]
                else:
                    godz_do = ""

                godz_str = f"{godz_od}-{godz_do}" if godz_od and godz_do else ""

                l_id = f"{slug}_{lekcja.get('data')}_{lekcja.get('godzinaOd')}"

                cursor.execute(
                    "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                    (
                        l_id,
                        slug,
                        data_lekcji,
                        godz_str,
                        przedmiot,
                        lekcja.get('sala', ''),
                        lekcja.get('prowadzacy', ''),
                        st_code
                    )
                )

            conn.commit()

    except Exception as e:
        log(f"Błąd API {display_name}: {e}")

    cursor.execute('''SELECT data, godzina, przedmiot, sala, prowadzacy, status
                      FROM schedule
                      WHERE student_slug=? AND data >= ? AND data <= ?
                      ORDER BY data ASC, godzina ASC''',
                   (slug, date_od_short, date_do_short))

    db_rows = cursor.fetchall()

    processed = []
    for row in db_rows:
        processed.append({
            "d": row[0],
            "g": row[1],
            "p": row[2],
            "s": row[3],
            "n": row[4],
            "st": row[5]
        })

    today_str = datetime.now().strftime('%Y-%m-%d')

    requests.post(
        f"http://supervisor/core/api/states/sensor.vultron_plan_{slug}",
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "state": len([l for l in processed if l['d'] == today_str]),
            "attributes": {
                "lekcje": processed,
                "friendly_name": f"Plan: {display_name}",
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        },
        timeout=10
    )

conn.close()
log("Plan zaktualizowany.")
