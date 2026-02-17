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
    if not text: return "unknown"
    chars = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z','Ą':'a','Ć':'c','Ę':'e','Ł':'l','Ń':'n','Ó':'o','Ś':'s','Ź':'z','Ż':'z'}
    text = text.lower()
    for k, v in chars.items(): text = text.replace(k, v)
    return re.sub(r'[^a-z0-9_]', '_', text).strip('_')

COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def get_dates_range():
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    return (start.strftime('%Y-%m-%dT00:00:00.000Z'), end.strftime('%Y-%m-%dT23:59:59.999Z'))

try:
    if not os.path.exists(COOKIE_PATH): exit(1)
    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as f: bundle = pickle.load(f); break
        except: time.sleep(1)
    if not bundle: exit(1)

    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
    session = requests.Session()
    for c in cookies: session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH, timeout=20); cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS frequency (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT, kategoria TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS frequency_stats (student_slug TEXT PRIMARY KEY, podsumowanie REAL, rows_json TEXT)')
except Exception as e: log(f"Błąd sesji: {e}"); exit(1)

date_od, date_do = get_dates_range()

for student in students:
    display_name, city, app_key = student.get('uczen', 'Nieznany'), student.get('city'), student.get('key')
    slug = clean_slug(student.get('slug', 'unknown'))
    if not city or not app_key: continue

    log(f"--- Synchronizacja: {display_name} ---")

    # 1. FREKWENCJA
    try:
        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja", params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do}, timeout=25)
        if res.status_code == 200:
            freq_raw = res.json()
            # POBIERANIE REKORDÓW Z KLUCZA 'oddzialy'
            records = freq_raw.get('oddzialy', []) if isinstance(freq_raw, dict) else freq_raw

            for f in records:
                d_raw, t_raw = f.get('data', ''), f.get('godzinaOd', '')
                if d_raw and t_raw:
                    f_id = f"{slug}_{d_raw}_{t_raw}"
                    # ZAPIS DO BAZY (czas formatowany do HH:MM dla planu)
                    cursor.execute("INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                        (f_id, slug, d_raw.split('T')[0], t_raw.split('T')[1][:5], f.get('kategoriaFrekwencji')))
            conn.commit()
    except Exception as e: log(f"Błąd frekwencji: {e}")

    # 2. STATYSTYKI
    try:
        res_s = session.get(f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki", params={'key': app_key, 'idPrzedmiot': -1}, timeout=25)
        if res_s.status_code == 200:
            sj = res_s.json()
            cat_m = {1:"Obecność", 2:"Nieobecność", 3:"Usprawiedliwiona", 4:"Spóźnienie", 5:"Spóźnienie uspraw.", 6:"Szkolne", 7:"Zwolnienie"}
            proc_s = []
            for row in sj.get('statystyki', []):
                m_m = {str(m.get('miesiac')): m.get('wartosc') for m in row.get('miesiace', [])}
                proc_s.append({"k": cat_m.get(row.get('kategoriaFrekwencji'), "Inna"), "m": m_m, "s1": row.get('okresy', [0,0])[0], "s2": row.get('okresy', [0,0])[1], "r": row.get('razem', 0)})
            cursor.execute("INSERT OR REPLACE INTO frequency_stats VALUES (?,?,?)", (slug, sj.get('podsumowanie', 0), json.dumps(proc_s)))
            conn.commit()
    except Exception as e: log(f"Błąd statystyk: {e}")

    # ODCZYT I WYSYŁKA
    cursor.execute("SELECT data, godzina, kategoria FROM frequency WHERE student_slug=? ORDER BY data DESC, godzina DESC", (slug,))
    freq_data_ha = [{"d": r[0], "t": r[1], "k": r[2]} for r in cursor.fetchall()]

    cursor.execute("SELECT podsumowanie, rows_json FROM frequency_stats WHERE student_slug=?", (slug,))
    db_s = cursor.fetchone()

    requests.post(f"http://supervisor/core/api/states/sensor.vultron_freq_{slug}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        json={"state": len(freq_data_ha), "attributes": {"wpisy": freq_data_ha, "friendly_name": f"Frekwencja: {display_name}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}, timeout=10)

    if db_s:
        requests.post(f"http://supervisor/core/api/states/sensor.vultron_stats_{slug}",
            headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
            json={"state": db_s[0], "attributes": {"unit_of_measurement": "%", "rows": json.loads(db_s[1]), "friendly_name": f"Statystyki: {display_name}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}, timeout=10)

conn.close()