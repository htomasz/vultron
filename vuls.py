import pickle, requests, json, os, re
from datetime import datetime, timedelta

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WORK] {message}")

def clean_html(raw): 
    return re.sub('<.*?>', '', raw).replace('&nbsp;', ' ').strip() if raw else "Brak opisu"

# Pobieranie tokenu HA
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH = '/data/vul.pkl'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl")
        exit(0)

    with open(COOKIE_PATH, 'rb') as f: 
        bundle = pickle.load(f)
    
    session = requests.Session()
    for c in bundle['cookies']: 
        session.cookies.set(c['name'], c['value'])

    # Zakres dat: od dziś do 21 dni w przód
    now_dt = datetime.now()
    d_od = now_dt.strftime('%Y-%m-%dT00:00:00.000Z')
    d_do = (now_dt + timedelta(days=21)).strftime('%Y-%m-%dT23:59:59.999Z')

    for s in bundle['students']:
        # DYNAMICZNE DANE Z PKL
        display_name = s.get('uczen', 'Nieznany')
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')

        if not city or not app_key:
            log(f"Pominięto {display_name} - brak miasta lub klucza.")
            continue

        log(f"Pobieram terminarz dla: {display_name} (Miasto: {city})...")

        # Pobieranie głównej listy wydarzeń
        api_main_url = f"https://uczen.eduvulcan.pl/{city}/api/SprawdzianyZadaniaDomowe"
        res = session.get(api_main_url, params={'key': app_key, 'dataOd': d_od, 'dataDo': d_do}, timeout=20)
        
        if res.status_code == 200:
            proc = []
            for i in res.json():
                # Wybór odpowiedniego endpointu dla szczegółów
                endpoint = "ZadanieDomoweSzczegoly" if i['typ'] == 4 else "SprawdzianSzczegoly"
                d_url = f"https://uczen.eduvulcan.pl/{city}/api/{endpoint}"
                
                # Pobieranie szczegółów (opis, nauczyciel itd.)
                dr = session.get(d_url, params={'key': app_key, 'id': i['id']}, timeout=15)
                if dr.status_code == 200:
                    dj = dr.json()
                    proc.append({
                        "data": dj['data'].split('T')[0], 
                        "przedmiot": dj['przedmiotNazwa'], 
                        "typ": {
                            1: "Sprawdzian", 
                            2: "Kartkówka", 
                            3: "Klasówka", 
                            4: "Zadanie domowe"
                        }.get(i['typ'], "Inne"), 
                        "opis": clean_html(dj.get('opis') or dj.get('temat')), 
                        "autor": dj.get('nauczycielImieNazwisko')
                    })
            
            # WYSYŁKA DO HOME ASSISTANT
            sensor_id = f"sensor.vultron_terminarz_{student_slug}"
            requests.post(
                f"http://supervisor/core/api/states/{sensor_id}", 
                headers={"Authorization": f"Bearer {HA_TOKEN}"}, 
                json={
                    "state": len(proc), 
                    "attributes": {
                        "lista": proc, 
                        "friendly_name": f"Terminarz: {display_name}",
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                },
                timeout=10
            )
            log(f"Zaktualizowano terminarz: {display_name} (Wpisy: {len(proc)})")
        else:
            log(f"Błąd API ({res.status_code}) dla {display_name}")

except Exception as e: 
    log(f"Błąd krytyczny: {e}")
