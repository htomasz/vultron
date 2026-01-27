import pickle
import requests
import json
import os
from datetime import datetime, timedelta

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [PLAN] {message}")

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
    """
    Pobiera zakres dat: od poniedziałku poprzedniego tygodnia 
    do niedzieli za 3 tygodnie (łącznie 5 tygodni danych).
    """
    today = datetime.now()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=34)
    
    return (
        start.strftime('%Y-%m-%dT00:00:00.000Z'), 
        end.strftime('%Y-%m-%dT23:59:59.999Z')
    )

# --- GŁÓWNA LOGIKA ---
try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl. Czekam na vul.py...")
        exit(0)

    with open(COOKIE_PATH, 'rb') as file:
        bundle = pickle.load(file)
    
    students = bundle.get('students', [])
    cookies = bundle.get('cookies', [])
    
    if not students:
        log("Błąd: Nie znaleziono danych dzieci w pliku sesji.")
        exit(0)

except Exception as e:
    log(f"Błąd odczytu danych sesji: {e}")
    exit(1)

session = requests.Session()
for c in cookies:
    session.cookies.set(c['name'], c['value'])

date_od, date_do = get_dates_range()

for student in students:
    # NOWE REALIA: Używamy 'uczen' zamiast 'name' oraz pobieramy 'city' z obiektu studenta
    display_name = student.get('uczen', 'Nieznany')
    city = student.get('city')
    app_key = student.get('key')
    student_slug = student.get('slug', 'unknown')

    if not city or not app_key:
        log(f"Pominięto {display_name} - brak miasta lub klucza w pliku sesji.")
        continue

    log(f"Synchronizacja planu dla: {display_name} (Miasto: {city})...")
    
    # URL budowany dynamicznie na podstawie miasta przypisanego do dziecka
    api_url = f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec"
    
    params = {
        'key': app_key, 
        'dataOd': date_od, 
        'dataDo': date_do, 
        'zakresDanych': '2'
    }

    try:
        response = session.get(api_url, params=params, timeout=25)
        
        if response.status_code != 200:
            log(f"Błąd API ({response.status_code}) dla {display_name}")
            continue
            
        plan_raw = response.json()
        processed_lessons = []

        for lekcja in plan_raw:
            d_iso = lekcja['data'].split('T')[0]
            start_time = lekcja['godzinaOd'].split('T')[1][:5]
            end_time = lekcja['godzinaDo'].split('T')[1][:5]
            
            changes = lekcja.get('zmiany', [])
            
            # 1. Czy lekcja jest odwołana (typ 2)
            is_cancelled = any(c.get('typ') == 2 for c in changes)
            
            # 2. Czy są informacje o zwolnieniu do domu w tekstach zmian
            info_text = " ".join([(c.get('informacjeNieobecnosc') or "").lower() for c in changes])
            is_dismissed = "zwolnieni do domu" in info_text or "okienko" in info_text

            if is_dismissed:
                st_code = "ZWOL"
            elif is_cancelled:
                st_code = "ODWOL"
            elif changes:
                st_code = "ZAST"
            else:
                st_code = ""

            przedmiot_name = lekcja.get('przedmiot')
            if not przedmiot_name:
                przedmiot_name = "Okienko" if is_dismissed else "Zajęcia"

            item = {
                "d": d_iso,
                "g": f"{start_time}-{end_time}",
                "p": przedmiot_name,
                "s": lekcja.get('sala', ''),
                "n": lekcja.get('prowadzacy', ''),
                "st": st_code
            }
            processed_lessons.append(item)

        sensor_name = f"vultron_plan_{student_slug}"
        ha_url = f"http://supervisor/core/api/states/sensor.{sensor_name}"
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        lessons_today = [l for l in processed_lessons if l['d'] == today_str]

        payload = {
            "state": len(lessons_today),
            "attributes": {
                "lekcje": processed_lessons,
                "friendly_name": f"Plan: {display_name}",
                "student_name": display_name,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "icon": "mdi:calendar-clock"
            }
        }

        headers = {
            "Authorization": f"Bearer {HA_TOKEN}", 
            "Content-Type": "application/json"
        }
        
        requests.post(ha_url, headers=headers, json=payload, timeout=10)
        log(f"Zaktualizowano plan dla {display_name} (łącznie {len(processed_lessons)} wpisów).")

    except Exception as e:
        log(f"Błąd podczas przetwarzania planu dla {display_name}: {e}")

log("Synchronizacja planów zakończona.")
