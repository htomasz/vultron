import pickle
import requests
import json
import os
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [STATS] {message}")

try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    log(f"Błąd konfiguracji: {e}")
    exit(1)

COOKIE_PATH = '/data/vul.pkl'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def get_dates_range():
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    return (start.strftime('%Y-%m-%dT00:00:00.000Z'), end.strftime('%Y-%m-%dT23:59:59.999Z'))

try:
    with open(COOKIE_PATH, 'rb') as file:
        bundle = pickle.load(file)
    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
    session = requests.Session()
    for c in cookies: session.cookies.set(c['name'], c['value'])
except: exit(1)

date_od, date_do = get_dates_range()

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city, app_key, slug = student.get('city'), student.get('key'), student.get('slug', 'unknown')
    if not city or not app_key: continue

    log(f"Synchronizacja frekwencji: {display_name}...")
    
    # --- 1. FREKWENCJA (MARKERY NA KARCIE) ---
    try:
        res_freq = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja", 
                               params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do}, timeout=25)
        if res_freq.status_code == 200:
            freq_raw = res_freq.json()
            freq_data = [{"d": f['data'].split('T')[0], "t": f['godzinaOd'].split('T')[1][:5], "k": f['kategoriaFrekwencji']} for f in freq_raw]
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_freq_{slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, 
                json={"state": len(freq_data), "attributes": {"wpisy": freq_data, "friendly_name": f"Frekwencja: {display_name}"}}, timeout=10)
    except: pass

    # --- 2. STATYSTYKI FREKWENCJI (%) ---
    try:
        res_stats = session.get(f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki", 
                                params={'key': app_key, 'idPrzedmiot': -1}, timeout=25)
        if res_stats.status_code == 200:
            stats_json = res_stats.json()
            cat_map = {1: "Obecność", 2: "Nieobecność", 3: "Nieobecność usprawiedliwiona", 4: "Spóźnienie", 5: "Spóźnienie usprawiedliwione", 6: "Nieobecność z przyczyn szkolnych", 7: "Zwolnienie"}
            processed_stats = []
            for row in stats_json.get('statystyki', []):
                m_map = { m['miesiac']: m['wartosc'] for m in row.get('miesiace', []) }
                processed_stats.append({"k": cat_map.get(row.get('kategoriaFrekwencji'), "Inna"), "m": m_map, "s1": row.get('okresy', [0,0])[0], "s2": row.get('okresy', [0,0])[1], "r": row.get('razem', 0)})

            requests.post(f"http://supervisor/core/api/states/sensor.vultron_stats_{slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, 
                json={"state": stats_json.get('podsumowanie', 0), "attributes": {"unit_of_measurement": "%", "rows": processed_stats, "friendly_name": f"Statystyki: {display_name}"}}, timeout=10)
    except: pass

log("Frekwencja i statystyki zaktualizowane.")

