import pickle, requests, sqlite3, json, os, time
from datetime import datetime

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [GRADES] {message}")

def clean_text(text):
    if not text: return ""
    # Zamieniamy cudzysłowy i inne znaki na bezpieczne dla HTML
    text = str(text).replace('"', '&quot;').replace("'", "&apos;").replace("\n", " ").replace("\r", "")
    # Limit długości opisu (Punkt 3 - Bezpieczeństwo atrybutów HA)
    if len(text) > 200: text = text[:197] + "..."
    return text

# HA Token pobieramy z systemu, resztę z pliku pkl
with open('/data/options.json') as f: config = json.load(f)
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
COOKIE_PATH, DB_PATH = '/data/vul.pkl', '/data/vultron.db'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl - zaloguj się najpierw.")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as f: bundle = pickle.load(f)
            if bundle: break
        except: time.sleep(1)
    if not bundle: exit(0)

    session = requests.Session()
    for c in bundle.get('cookies', []): session.cookies.set(c['name'], c['value'])

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    # Tabela rozszerzona o kolumnę 'opis'
    cursor.execute('CREATE TABLE IF NOT EXISTS grades (id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT, data TEXT, opis TEXT, PRIMARY KEY(id_kolumny, student_slug))')

    for s in bundle.get('students', []):
        city = s.get('city')
        app_key = s.get('key')
        period_id = s.get('periodId')
        student_slug = s.get('slug')
        display_name = s.get('uczen', 'Nieznany')

        if not city or not period_id or not app_key:
            log(f"Pominięto {display_name} - brak kompletnych danych (city/periodId/key)")
            continue

        log(f"Synchronizacja ocen dla: {display_name}...")

        res = session.get(f"https://uczen.eduvulcan.pl/{city}/api/Oceny",
                          params={'key': app_key, 'idOkresKlasyfikacyjny': period_id}, timeout=25)

        new_grades = 0
        if res.status_code == 200:
            data_json = res.json()

            for p in data_json.get('ocenyPrzedmioty', []):
                nazwa_p = p.get('przedmiotNazwa', 'Inne')

                for kol in p.get('kolumnyOcenyCzastkowe', []):
                    id_k = str(kol.get('idKolumny', '0'))
                    # POBIERAMY DODATKOWE DANE (Punkt 5 - .get())
                    kat_kol = clean_text(kol.get('kategoriaKolumny', ''))
                    nazwa_kol = clean_text(kol.get('nazwaKolumny', ''))
                    opis = f"{kat_kol}: {nazwa_kol}".strip(": ")

                    for o in kol.get('oceny', []):
                        wpis = str(o.get('wpis', ''))
                        dt = str(o.get('dataOceny', ''))

                        # Porównanie z bazą i zapis
                        cursor.execute("SELECT ocena FROM grades WHERE id_kolumny=? AND student_slug=?", (id_k, student_slug))
                        if cursor.fetchone() is None:
                            cursor.execute("INSERT INTO grades VALUES (?,?,?,?,?,?)", (id_k, student_slug, nazwa_p, wpis, dt, opis))
                            new_grades += 1
                        else:
                            cursor.execute("UPDATE grades SET ocena=?, data=?, opis=? WHERE id_kolumny=? AND student_slug=?", (wpis, dt, opis, id_k, student_slug))

            conn.commit()

        # ODCZYT Z BAZY (Budowanie listy dla HA na podstawie bazy)
        cursor.execute("SELECT przedmiot, ocena, data, opis FROM grades WHERE student_slug=? ORDER BY przedmiot ASC", (student_slug,))
        db_rows = cursor.fetchall()

        lista_ha = []
        subjects = {}
        for row in db_rows:
            p_name, o_val, d_val, i_val = row
            if p_name not in subjects:
                subjects[p_name] = []

            # Zapisujemy jako obiekt, a nie string
            subjects[p_name].append({
                "w": o_val,
                "d": d_val[:5],
                "i": i_val
            })

        for sub_name, oceny_detale in subjects.items():
            lista_ha.append({"przedmiot": sub_name, "oceny": oceny_detale})

        # WYSYŁKA DO HOME ASSISTANT
        requests.post(f"http://supervisor/core/api/states/sensor.vultron_oceny_{student_slug}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json={
                "state": new_grades,
                "attributes": {
                    "lista_przedmiotow": lista_ha,
                    "friendly_name": f"Oceny: {display_name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            }, timeout=10)
        log(f"Zaktualizowano oceny: {display_name} (Nowe: {new_grades})")

    conn.close()
except Exception as e:
    log(f"Błąd krytyczny: {e}")
