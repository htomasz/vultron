import pickle
import requests
import sqlite3
import json
import os
import time
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [ACHIEVEMENTS] {message}")

HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
DB_PATH = '/data/vultron.db'
COOKIE_PATH = '/data/vul.pkl'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl.")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as f:
                bundle = pickle.load(f)
            if bundle:
                break
        except:
            time.sleep(1)
    if not bundle:
        exit(0)

    session = requests.Session()
    for c in bundle.get('cookies', []):
        session.cookies.set(c.get('name'), c.get('value'))

    # 1. PRZYGOTOWANIE BAZY
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    # Tabela dla osiągnięć
    cursor.execute('''CREATE TABLE IF NOT EXISTS achievements
                      (achievement_id TEXT, student_slug TEXT, tresc TEXT,
                       PRIMARY KEY(achievement_id, student_slug))''')

    for s in bundle.get('students', []):
        display_name = s.get('uczen', 'Nieznany')
        city, app_key, student_slug = s.get('city'), s.get('key'), s.get('slug')

        if not city or not app_key:
            continue

        log(f"Pobieram osiągnięcia dla: {display_name}")
        api_url = f"https://uczen.eduvulcan.pl/{city}/api/Osiagniecia"
        res = session.get(api_url, params={'key': app_key}, timeout=20)

        new_count = 0
        if res.status_code == 200:
            json_data = res.json()
            for item in json_data:
                a_id, tresc = str(item.get('id', '')), item.get('tresc', '')

                # 3. PORÓWNANIE I ZAPIS
                cursor.execute("SELECT achievement_id FROM achievements WHERE achievement_id=? AND student_slug=?", (a_id, student_slug))
                if cursor.fetchone() is None:
                    cursor.execute("INSERT INTO achievements VALUES (?,?,?)", (a_id, student_slug, tresc))
                    new_count += 1
                else:
                    cursor.execute("UPDATE achievements SET tresc=? WHERE achievement_id=? AND student_slug=?", (tresc, a_id, student_slug))
            conn.commit()

        # 4. ODCZYT Z BAZY
        cursor.execute("SELECT achievement_id, tresc FROM achievements WHERE student_slug=?", (student_slug,))
        db_rows = cursor.fetchall()
        lista_ha = [{"id": row[0], "tresc": row[1]} for row in db_rows]

        # 5. WYSYŁKA DO SENSORA
        ha_url = f"http://supervisor/core/api/states/sensor.vultron_osiagniecia_{student_slug}"
        requests.post(ha_url, headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
            json={
                "state": len(lista_ha),
                "attributes": {
                    "osiagniecia": lista_ha,
                    "nowe_osiagniecia": new_count,
                    "friendly_name": f"Osiągnięcia: {display_name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "icon": "mdi:trophy-variant"
                }
            }, timeout=10)
    conn.close()
except Exception as e:
    log(f"Błąd krytyczny: {e}")
