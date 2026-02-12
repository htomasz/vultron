import pickle
import requests
import json
import os
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
    with open(COOKIE_PATH, 'rb') as file:
        bundle = pickle.load(file)
    students, cookies = bundle.get('students', []), bundle.get('cookies', [])
except Exception as e:
    log(f"Błąd sesji: {e}"); exit(1)

session = requests.Session()
for c in cookies: session.cookies.set(c['name'], c['value'])

date_od, date_do = get_dates_range()

for student in students:
    display_name = student.get('uczen', 'Nieznany')
    city, app_key, slug = student.get('city'), student.get('key'), student.get('slug', 'unknown')
    if not city or not app_key: continue

    log(f"Synchronizacja planu: {display_name}...")
    
    try:
        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec", 
                          params={'key': app_key, 'dataOd': date_od, 'dataDo': date_do, 'zakresDanych': '2'}, timeout=25)
        if res.status_code != 200: continue
        
        plan_raw = res.json()
        processed = []

        for lekcja in plan_raw:
            # 1. Pobranie statusu z pola adnotacja
            nr_adn = int(lekcja.get('adnotacja', 0))
            st_code = MAPA_STATUSOW.get(nr_adn, "")
            
            # 2. Analiza tekstu zmian (zwolnienia/okienka traktujemy jako odwołane)
            changes = lekcja.get('zmiany', [])
            info_text = " ".join([(c.get('informacjeNieobecnosc') or "").lower() for c in changes])
            
            if "zwolnieni" in info_text or "okienko" in info_text:
                st_code = "ODWOL"

            # 3. Nazwa przedmiotu
            przedmiot = lekcja.get('przedmiot')
            if not przedmiot:
                przedmiot = "Lekcja odwołana" if st_code == "ODWOL" else "Zajęcia"

            processed.append({
                "d": lekcja['data'].split('T')[0],
                "g": f"{lekcja['godzinaOd'].split('T')[1][:5]}-{lekcja['godzinaDo'].split('T')[1][:5]}",
                "p": przedmiot,
                "s": lekcja.get('sala', ''),
                "n": lekcja.get('prowadzacy', ''),
                "st": st_code
            })

        # Wysyłka do Home Assistant
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

log("Plan zaktualizowany.")

