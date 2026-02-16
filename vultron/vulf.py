import pickle
import requests
import json
import os
import re
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [STATS] {message}")

def clean_slug(text):
    if not text: return "unknown"
    chars = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z',
             'Ą':'a','Ć':'c','Ę':'e','Ł':'l','Ń':'n','Ó':'o','Ś':'s','Ź':'z','Ż':'z'}
    text = text.lower()
    for k, v in chars.items():
        text = text.replace(k, v)
    text = re.sub(r'[^a-z0-9_]', '_', text)
    return text.strip('_')

COOKIE_PATH = '/data/vul.pkl'
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
    with open(COOKIE_PATH, 'rb') as file:
        bundle = pickle.load(file)
    students = bundle.get('students', [])
    cookies = bundle.get('cookies', [])
    session = requests.Session()
    for c in cookies: session.cookies.set(c['name'], c['value'])
except Exception as e:
    log(f"Błąd sesji: {e}")
    exit(1)

date_od, date_do = get_dates_range()

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city = student.get('city')
    app_key = student.get('key')
    slug = clean_slug(student.get('slug', 'unknown'))

    if not city or not app_key: continue

    log(f"--- Synchronizacja: {display_name} (ID: {slug}) ---")

    # --- 1. FREKWENCJA ---
    try:
        res_freq = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja",
                               params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do}, timeout=25)

        if res_freq.status_code == 200:
            freq_raw = res_freq.json()

            # POBIERANIE REKORDÓW Z KLUCZA 'oddzialy'
            records = []
            if isinstance(freq_raw, dict):
                # Twoje dane są w 'oddzialy'
                records = freq_raw.get('oddzialy', [])
            elif isinstance(freq_raw, list):
                records = freq_raw

            freq_data = []
            for f in records:
                try:
                    d_raw = f.get('data', '')
                    t_raw = f.get('godzinaOd', '')
                    if d_raw and t_raw:
                        freq_data.append({
                            "d": d_raw.split('T')[0],
                            "t": t_raw.split('T')[1][:5],
                            "k": f.get('kategoriaFrekwencji')
                        })
                except: continue

            entity_id = f"sensor.vultron_freq_{slug}"
            res_ha = requests.post(f"http://supervisor/core/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
                json={
                    "state": len(freq_data),
                    "attributes": {
                        "wpisy": freq_data,
                        "friendly_name": f"Frekwencja: {display_name}",
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                }, timeout=10)
            log(f"OK: Zaktualizowano {entity_id} ({len(freq_data)} wpisów)")
        else:
            log(f"Błąd Vulcan: {res_freq.status_code}")
    except Exception as e:
        log(f"Błąd krytyczny frekwencji: {e}")

    # --- 2. STATYSTYKI ---
    try:
        res_stats = session.get(f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki",
                                params={'key': app_key, 'idPrzedmiot': -1}, timeout=25)
        if res_stats.status_code == 200:
            stats_json = res_stats.json()
            cat_map = {1:"Obecność", 2:"Nieobecność", 3:"Usprawiedliwiona", 4:"Spóźnienie", 5:"Spóźnienie uspraw.", 6:"Szkolne", 7:"Zwolnienie"}
            processed_stats = []

            for row in stats_json.get('statystyki', []):
                m_map = { m['miesiac']: m['wartosc'] for m in row.get('miesiace', []) }
                processed_stats.append({
                    "k": cat_map.get(row.get('kategoriaFrekwencji'), "Inna"),
                    "m": m_map, "s1": row.get('okresy', [0,0])[0], "s2": row.get('okresy', [0,0])[1], "r": row.get('razem', 0)
                })

            entity_id_stats = f"sensor.vultron_stats_{slug}"
            requests.post(f"http://supervisor/core/api/states/{entity_id_stats}",
                headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
                json={
                    "state": stats_json.get('podsumowanie', 0),
                    "attributes": {"unit_of_measurement": "%", "rows": processed_stats, "friendly_name": f"Statystyki: {display_name}"}
                }, timeout=10)
            log(f"OK: Zaktualizowano statystyki {entity_id_stats}")
    except Exception as e:
        log(f"Błąd statystyk: {e}")

log("Proces zakończony.")
