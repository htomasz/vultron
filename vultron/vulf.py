import pickle
import requests
import json
import os
import re
import time
import sqlite3
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [STATS] {message}")

def clean_slug(text):
    if not text:
        return "unknown"
    chars = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z','Ą':'a','Ć':'c','Ę':'e','Ł':'l','Ń':'n','Ó':'o','Ś':'s','Ź':'z','Ż':'z'}
    text = text.lower()
    for k, v in chars.items():
        text = text.replace(k, v)
    return re.sub(r'[^a-z0-9_]', '_', text).strip('_')

COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def get_dates_range():
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    return (start.strftime('%Y-%m-%dT00:00:00.000Z'), end.strftime('%Y-%m-%dT23:59:59.999Z'))

try:
    if not os.path.exists(COOKIE_PATH):
        log("Błąd: Brak pliku sesji vul.pkl.")
        exit(1)

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
        exit(1)

    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
    session = requests.Session()
    for c in cookies:
        session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS frequency (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT, kategoria INTEGER)')
    cursor.execute('CREATE TABLE IF NOT EXISTS frequency_stats (student_slug TEXT PRIMARY KEY, podsumowanie REAL, rows_json TEXT)')
except Exception as e:
    log(f"Błąd sesji: {e}")
    exit(1)

date_od, date_do = get_dates_range()
today_dt = datetime.now()
limit_date = (today_dt - timedelta(days=today_dt.weekday() + 7)).strftime('%Y-%m-%d')

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city, app_key = student.get('city'), student.get('key')
    slug = clean_slug(student.get('slug', 'unknown'))
    if not city or not app_key:
        continue

    log(f"--- Synchronizacja: {display_name} (ID: {slug}) ---")

    # 1. FREKWENCJA
    try:
        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja", params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do}, timeout=25)
        if res.status_code == 200:
            records = res.json().get('oddzialy', []) if isinstance(res.json(), dict) else res.json()
            for f in records:
                d_raw, t_raw = f.get('data', ''), f.get('godzinaOd', '')
                if d_raw and t_raw:
                    # KLUCZOWE FORMATOWANIE
                    d_val, t_val = d_raw.split('T')[0], t_raw.split('T')[1][:5]
                    # Konwersja kategorii na INT przed zapisem
                    cat_val = int(f.get('kategoriaFrekwencji', 0))
                    f_id = f"{slug}_{d_raw}_{t_raw}"
                    cursor.execute("INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)", (f_id, slug, d_val, t_val, cat_val))
            conn.commit()
    except Exception as e:
        log(f"Błąd frekwencji: {e}")

    # 2. STATYSTYKI
    try:
        res_stats = session.get(f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki", params={'key': app_key, 'idPrzedmiot': -1}, timeout=25)
        if res_stats.status_code == 200:
            stats_json = res_stats.json()
            cat_map = {1:"Obecność", 2:"Nieobecność", 3:"Usprawiedliwiona", 4:"Spóźnienie", 5:"Spóźnienie uspraw.", 6:"Szkolne", 7:"Zwolnienie"}
            processed_stats = [{"k": cat_map.get(row.get('kategoriaFrekwencji'), "Inna"), "m": { m['miesiac']: m['wartosc'] for m in row.get('miesiace', []) }, "s1": row.get('okresy', [0,0])[0], "s2": row.get('okresy', [0,0])[1], "r": row.get('razem', 0)} for row in stats_json.get('statystyki', [])]
            cursor.execute("INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?)", (slug, stats_json.get('podsumowanie', 0), json.dumps(processed_stats)))
            conn.commit()
    except Exception as e:
        log(f"Błąd statystyk: {e}")

    # ODCZYT Z BAZY - WYMUSZENIE TYPU INT DLA POLA 'k'
    cursor.execute("SELECT data, godzina, kategoria FROM frequency WHERE student_slug=? AND data >= ? ORDER BY data DESC, godzina DESC", (slug, limit_date))
    freq_data_ha = []
    for r in cursor.fetchall():
        freq_data_ha.append({
            "d": r[0],
            "t": r[1],
            "k": int(r[2]) # TU WYMUSZAMY LICZBĘ (Integer)
        })

    cursor.execute("SELECT podsumowanie, rows_json FROM frequency_stats WHERE student_slug=?", (slug,))
    db_s = cursor.fetchone()

    # WYSYŁKA
    requests.post(f"http://supervisor/core/api/states/sensor.vultron_freq_{slug}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        json={"state": len(freq_data_ha), "attributes": {"wpisy": freq_data_ha, "friendly_name": f"Frekwencja: {display_name}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}, timeout=10)

    if db_s:
        raw_stats = json.loads(db_s[1])
        for s in raw_stats:
            s['m'] = {int(k): v for k, v in s['m'].items()}
        requests.post(f"http://supervisor/core/api/states/sensor.vultron_stats_{slug}", headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, json={"state": db_s[0], "attributes": {"unit_of_measurement": "%", "rows": raw_stats, "friendly_name": f"Statystyki: {display_name}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}, timeout=10)

conn.close()
log("Proces zakończony.")