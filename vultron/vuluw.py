import pickle, requests, sqlite3, json, os, time
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [REMARKS] {message}")

# HA Token i ścieżki
HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
DB_PATH = '/data/vultron.db'
COOKIE_PATH = '/data/vul.pkl'

try:
    if not os.path.exists(COOKIE_PATH):
        log("Brak pliku sesji vul.pkl. Czekam na proces logowania...")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(COOKIE_PATH, 'rb') as f: bundle = pickle.load(f)
            if bundle: break
        except: time.sleep(1)
    if not bundle: exit(0)

    session = requests.Session()
    for c in bundle.get('cookies', []):
        session.cookies.set(c.get('name'), c.get('value'))

    # 1. PRZYGOTOWANIE BAZY
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    # Tabela przechowująca ID uwag, aby wykrywać nowe
    cursor.execute('''CREATE TABLE IF NOT EXISTS remarks
                      (remark_id TEXT, student_slug TEXT, data TEXT, tresc TEXT,
                       autor TEXT, kategoria TEXT, punkty TEXT, typ TEXT,
                       PRIMARY KEY(remark_id, student_slug))''')

    for s in bundle.get('students', []):
        display_name = s.get('uczen', 'Nieznany')
        city = s.get('city')
        app_key = s.get('key')
        student_slug = s.get('slug')

        if not city or not app_key:
            log(f"Pominięto {display_name} - brak miasta lub klucza.")
            continue

        log(f"Pobieram uwagi dla: {display_name} (Miasto: {city})")

        # 2. ODCZYT ZE STRONY (API)
        api_url = f"https://uczen.eduvulcan.pl/{city}/api/Uwagi"
        res = session.get(api_url, params={'key': app_key}, timeout=20)

        new_count = 0
        nowe_uwagi_txt = []

        if res.status_code == 200:
            json_data = res.json()
            for item in json_data:
                r_id = str(item.get('id', ''))
                tresc = item.get('tresc', '')
                autor = item.get('autor', '')
                data_w = item.get('data', '').split('T')[0]
                kat = item.get('kategoria', '')
                punkty = str(item.get('liczbaPunktow') or "")

                # Określenie typu (Heurystyka)
                typ_wpisu = "informacja"
                tresc_lower = tresc.lower()
                if "pochwała" in tresc_lower or "pochwala" in tresc_lower:
                    typ_wpisu = "pozytywna"
                elif "uwaga" in tresc_lower or "adnotacja" in tresc_lower:
                    typ_wpisu = "negatywna"

                # 3. PORÓWNANIE I ZAPIS DO BAZY
                cursor.execute("SELECT remark_id FROM remarks WHERE remark_id=? AND student_slug=?", (r_id, student_slug))
                if cursor.fetchone() is None:
                    # Nowy wpis w bazie SQL
                    cursor.execute("INSERT INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                                   (r_id, student_slug, data_w, tresc, autor, kat, punkty, typ_wpisu))
                    new_count += 1
                    nowe_uwagi_txt.append(f"{kat}: {tresc[:50]}...")
                else:
                    # Aktualizacja danych
                    cursor.execute("UPDATE remarks SET data=?, tresc=?, autor=?, kategoria=?, punkty=?, typ=? WHERE remark_id=? AND student_slug=?",
                                   (data_w, tresc, autor, kat, punkty, typ_wpisu, r_id, student_slug))

            conn.commit()

        # 4. ODCZYT Z BAZY
        cursor.execute("SELECT data, tresc, autor, kategoria, punkty, typ, remark_id FROM remarks WHERE student_slug=? ORDER BY data DESC", (student_slug,))
        db_rows = cursor.fetchall()

        lista_ha = []
        for row in db_rows:
            lista_ha.append({"data": row[0], "tresc": row[1], "autor": row[2], "kategoria": row[3], "punkty": row[4], "typ": row[5], "id": row[6]})

        # 5. WYSYŁKA DO HOME ASSISTANT
        ha_url = f"http://supervisor/core/api/states/sensor.vultron_uwagi_{student_slug}"
        payload = {
            "state": new_count,
            "attributes": {
                "uwagi": lista_ha,
                "nowe_uwagi_lista": nowe_uwagi_txt,
                "friendly_name": f"Uwagi: {display_name}",
                "student_name": display_name,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "icon": "mdi:alert-decagram"
            }
        }

        requests.post(ha_url, headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}, json=payload, timeout=10)
        log(f"Zakończono dla {display_name}. Znaleziono nowych: {new_count}")

    conn.close()
except Exception as e:
    log(f"Błąd krytyczny: {e}")
