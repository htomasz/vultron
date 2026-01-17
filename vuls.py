import pickle, requests, json, os, re
from datetime import datetime, timedelta

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WORK] {message}")

def clean_html(raw): return re.sub('<.*?>', '', raw).replace('&nbsp;', ' ').strip() if raw else "Brak opisu"

with open('/data/options.json') as f: config = json.load(f)
CITY, HA_TOKEN = config.get('city_slug'), os.getenv('SUPERVISOR_TOKEN')

try:
    with open('/data/vul.pkl', 'rb') as f: bundle = pickle.load(f)
    session = requests.Session()
    for c in bundle['cookies']: session.cookies.set(c['name'], c['value'])
    d_od, d_do = datetime.now().strftime('%Y-%m-%dT00:00:00.000Z'), (datetime.now() + timedelta(days=21)).strftime('%Y-%m-%dT23:59:59.999Z')

    for s in bundle['students']:
        res = session.get(f"https://uczen.eduvulcan.pl/{CITY}/api/SprawdzianyZadaniaDomowe", params={'key': s['key'], 'dataOd': d_od, 'dataDo': d_do})
        if res.status_code == 200:
            proc = []
            for i in res.json():
                d_url = f"https://uczen.eduvulcan.pl/{CITY}/api/" + ("ZadanieDomoweSzczegoly" if i['typ'] == 4 else "SprawdzianSzczegoly")
                dr = session.get(d_url, params={'key': s['key'], 'id': i['id']})
                if dr.status_code == 200:
                    dj = dr.json()
                    proc.append({"data": dj['data'].split('T')[0], "przedmiot": dj['przedmiotNazwa'], "typ": {1:"Sprawdzian", 2:"Kartkówka", 3:"Klasówka", 4:"Zadanie domowe"}.get(i['typ'], "Inne"), "opis": clean_html(dj.get('opis') or dj.get('temat')), "autor": dj.get('nauczycielImieNazwisko')})
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_terminarz_{s['slug']}", headers={"Authorization": f"Bearer {HA_TOKEN}"}, json={"state": len(proc), "attributes": {"lista": proc, "friendly_name": f"Terminarz: {s['name']}"}})
            log(f"Zaktualizowano terminarz: {s['name']}")
except Exception as e: log(f"Błąd: {e}")
