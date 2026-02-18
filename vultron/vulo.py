import pickle
import requests
import sqlite3
import json
import os
import time
from datetime import datetime

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [GRADES] {message}")

def clean_text(text):
    if not text:
        return ""
    # Zamieniamy cudzysłowy i inne znaki na bezpieczne dla HTML
    text = str(text).replace('"', '&quot;').replace("'", "&apos;").replace("\n", " ").replace("\r", "")
    # Limit długości opisu (Punkt 3 - Bezpieczeństwo atrybutów HA)
    if len(text) > 200:
        text = text[:197] + "..."
    return text

# HA Token pobieramy z systemu, resztę z pliku pkl
with open('/data/options.json') as f:
    config = json.load(f)
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl - zaloguj się najpierw.")
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
        session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    # Tabela rozszerzona o kolumnę 'period_id' w kluczu głównym, aby nie mieszać semestrów
    cursor.execute('CREATE TABLE IF NOT EXISTS grades (id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT, data TEXT, opis TEXT, period_id TEXT, PRIMARY KEY(id_kolumny, student_slug, period_id))')

    for s in bundle.get('students', []):
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')
        display_name = s.get('uczen', 'Nieznany')
        id_dziennik = s.get('idDziennik')
        active_period_id = s.get('periodId') # Aktualny okres z auth.py

        if not city or not app_key or not id_dziennik:
            log(f"Pominięto {display_name} - brak kompletnych danych")
            continue

        # Pobieramy listę wszystkich okresów dla ucznia
        res_periods = session.get(f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne",
                                  params={'key': app_key, 'idDziennik': id_dziennik}, timeout=25)

        if res_periods.status_code != 200:
            log(f"Nie udało się pobrać okresów dla {display_name}")
            continue

        periods = res_periods.json() # Lista okresów (zazwyczaj 2)

        for period in periods:
            p_id = str(period.get('id'))
            p_num = period.get('numerOkresu')

            log(f"Synchronizacja ocen dla: {display_name} (Okres {p_num})...")

            res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Oceny",
                              params={'key': app_key, 'idOkresKlasyfikacyjny': p_id}, timeout=25)

            new_grades = 0
            if res.status_code == 200:
                data_json = res.json()

                for p in data_json.get('ocenyPrzedmioty', []):
                    nazwa_p = p.get('przedmiotNazwa', 'Inne')

                    for kol in p.get('kolumnyOcenyCzastkowe', []):
                        id_k = str(kol.get('idKolumny', '0'))
                        kat_kol = clean_text(kol.get('kategoriaKolumny', ''))
                        nazwa_kol = clean_text(kol.get('nazwaKolumny', ''))
                        opis = f"{kat_kol}: {nazwa_kol}".strip(": ")

                        for o in kol.get('oceny', []):
                            wpis = str(o.get('wpis', ''))
                            dt = str(o.get('dataOceny', ''))

                            # Porównanie z bazą uwzględniając p_id
                            cursor.execute("SELECT ocena FROM grades WHERE id_kolumny=? AND student_slug=? AND period_id=?", (id_k, student_slug, p_id))
                            if cursor.fetchone() is None:
                                cursor.execute("INSERT INTO grades VALUES (?,?,?,?,?,?,?)", (id_k, student_slug, nazwa_p, wpis, dt, opis, p_id))
                                new_grades += 1
                            else:
                                cursor.execute("UPDATE grades SET ocena=?, data=?, opis=? WHERE id_kolumny=? AND student_slug=? AND period_id=?", (wpis, dt, opis, id_k, student_slug, p_id))

                conn.commit()

            # ODCZYT Z BAZY dla konkretnego okresu
            cursor.execute("SELECT przedmiot, ocena, data, opis FROM grades WHERE student_slug=? AND period_id=? ORDER BY przedmiot ASC", (student_slug, p_id))
            db_rows = cursor.fetchall()

            lista_ha = []
            subjects = {}
            for row in db_rows:
                p_name, o_val, d_val, i_val = row
                if p_name not in subjects:
                    subjects[p_name] = []

                subjects[p_name].append({
                    "w": o_val,
                    "d": d_val[:5],
                    "i": i_val
                })

            for sub_name, oceny_detale in subjects.items():
                lista_ha.append({"przedmiot": sub_name, "oceny": oceny_detale})

            # WYSYŁKA DO HOME ASSISTANT - osobna encja dla każdego okresu (_p1, _p2)
            requests.post(f"http://supervisor/core/api/states/sensor.vultron_oceny_{student_slug}_p{p_num}",
                headers={"Authorization": f"Bearer {HA_TOKEN}"},
                json={
                    "state": new_grades,
                    "attributes": {
                        "lista_przedmiotow": lista_ha,
                        "friendly_name": f"Oceny: {display_name} (Okres {p_num})",
                        "student_slug": student_slug,
                        "period_number": p_num,
                        "active_period": p_id == str(active_period_id),
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                }, timeout=10)
            log(f"Zaktualizowano oceny: {display_name} P{p_num} (Nowe: {new_grades})")

    conn.close()
except Exception as e:
    log(f"Błąd krytyczny: {e}")