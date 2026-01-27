import pickle
import requests
import json
import os
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [STATS] {message}")

# 1. Ładowanie konfiguracji
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    log(f"Błąd ładowania options.json: {e}")
    exit(1)

COOKIE_PATH = '/data/vul.pkl'
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')

def get_dates_range():
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    return (
        start.strftime('%Y-%m-%dT00:00:00.000Z'), 
        end.strftime('%Y-%m-%dT23:59:59.999Z')
    )

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl.")
        exit(0)
    with open(COOKIE_PATH, 'rb') as file:
        bundle = pickle.load(file)
    students = bundle.get('students', [])
    cookies = bundle.get('cookies', [])
except Exception as e:
    log(f"Błąd odczytu danych sesji: {e}")
    exit(1)

session = requests.Session()
for c in cookies:
    session.cookies.set(c['name'], c['value'])

date_od, date_do = get_dates_range()

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city = student.get('city')
    app_key = student.get('key')
    student_slug = student.get('slug', 'unknown')

    if not city or not app_key: continue

    log(f"Synchronizacja danych dla: {display_name}...")
    
    # URL-e API
    plan_url = f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec"
    freq_url = f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja"
    stats_url = f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki"

    # --- 1. PLAN LEKCJI ---
    try:
        res_plan = session.get(plan_url, params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do, 'zakresDanych': '2'}, timeout=25)
        if res_plan.status_code == 200:
            plan_raw = res_plan.json()
            processed_lessons = []
            for lekcja in plan_raw:
                d_iso = lekcja['data'].split('T')[0]
                start_time = lekcja['godzinaOd'].split('T')[1][:5]
                end_time = lekcja['godzinaDo'].split('T')[1][:5]
                changes = lekcja.get('zmiany', [])
                
                is_cancelled = any(c.get('typ') == 2 for c in changes)
                info_text = " ".join([(c.get('informacjeNieobecnosc') or "").lower() for c in changes])
                is_dismissed = "zwolnieni do domu" in info_text or "okienko" in info_text

                if is_dismissed: st_code = "ZWOL"
                elif is_cancelled: st_code = "ODWOL"
                elif changes: st_code = "ZAST"
                else: st_code = ""

                processed_lessons.append({
                    "d": d_iso, "g": f"{start_time}-{end_time}",
                    "p": lekcja.get('przedmiot') or ("Okienko" if is_dismissed else "Zajęcia"),
                    "s": lekcja.get('sala', ''), "n": lekcja.get('prowadzacy', ''), "st": st_code
                })
            
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_plan_{student_slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={"state": len([l for l in processed_lessons if l['d'] == datetime.now().strftime('%Y-%m-%d')]),
                      "attributes": {"lekcje": processed_lessons, "friendly_name": f"Plan: {display_name}"}})
    except Exception as e: log(f"Błąd planu {display_name}: {e}")

    # --- 2. FREKWENCJA (ZNACZNIKI) ---
    try:
        res_freq = session.get(freq_url, params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do}, timeout=25)
        if res_freq.status_code == 200:
            freq_raw = res_freq.json()
            freq_data = [{"d": f['data'].split('T')[0], "t": f['godzinaOd'].split('T')[1][:5], "k": f['kategoriaFrekwencji']} for f in freq_raw]
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_freq_{student_slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={"state": len(freq_data), "attributes": {"wpisy": freq_data, "friendly_name": f"Frekwencja: {display_name}"}})
    except Exception as e: log(f"Błąd frekwencji {display_name}: {e}")

    # --- 3. STATYSTYKI FREKWENCJI (DOSTOSOWANE DO NOWEGO JSON) ---
    try:
        res_stats = session.get(stats_url, params={'key': app_key, 'idPrzedmiot': -1}, timeout=25)
        if res_stats.status_code == 200:
            stats_json = res_stats.json()
            
            cat_map = {
                1: "Obecność", 2: "Nieobecność", 3: "Nieobecność usprawiedliwiona",
                4: "Spóźnienie", 5: "Spóźnienie usprawiedliwione",
                6: "Nieobecność z przyczyn szkolnych", 7: "Zwolnienie"
            }

            processed_stats = []
            for row in stats_json.get('statystyki', []):
                # Przerabiamy listę miesięcy na słownik {numer_miesiaca: wartosc}
                m_map = { m['miesiac']: m['wartosc'] for m in row.get('miesiace', []) }
                processed_stats.append({
                    "k": cat_map.get(row.get('kategoriaFrekwencji'), "Inna"),
                    "m": m_map,
                    "s1": row.get('okresy', [0,0])[0],
                    "s2": row.get('okresy', [0,0])[1],
                    "r": row.get('razem', 0)
                })

            requests.post(f"http://supervisor/core/api/states/sensor.vultron_stats_{student_slug}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={"state": stats_json.get('podsumowanie', 0), "attributes": {
                    "unit_of_measurement": "%", 
                    "rows": processed_stats, 
                    "friendly_name": f"Statystyki: {display_name}"
                }})
    except Exception as e: log(f"Błąd statystyk {display_name}: {e}")

log("Synchronizacja zakończona.")
