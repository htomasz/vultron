import json, os, requests, pickle, time, sqlite3
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [VULM] {message}")

HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
VUL_PKL = '/data/vul.pkl'
DB_PATH = '/data/vultron.db'

try:
    if not os.path.exists(VUL_PKL):
        log("Brak pliku sesji vul.pkl.")
        exit(0)

    bundle = None
    for _ in range(5):
        try:
            with open(VUL_PKL, 'rb') as f:
                bundle = pickle.load(f)
            if bundle:
                break
        except:
            time.sleep(1)

    if not bundle:
        exit(0)

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()

    for student in bundle.get('students', []):
        slug = student.get('slug')
        display_name = student.get('uczen', 'Nieznany')

        # FIX LICZNIKA: Uwzględniamy unknown, bo tak masz w bazie
        cursor.execute("SELECT COUNT(*) FROM messages WHERE student_slug=? OR student_slug='unknown'", (slug,))
        total_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages WHERE (student_slug=? OR student_slug='unknown') AND przeczytana=0", (slug,))
        unread_total_in_db = cursor.fetchone()[0]

        # ODCZYT OSTATNICH 10 WIADOMOŚCI
        cursor.execute("SELECT data, nadawca, temat, tresc, przeczytana FROM messages WHERE student_slug=? OR student_slug='unknown' ORDER BY data DESC LIMIT 10", (slug,))
        db_rows = cursor.fetchall()

        student_messages = []
        for row in db_rows:
            # row[4] to INTEGER (1=przeczytana, 0=nieprzeczytana)
            is_unread = int(row[4]) == 0
            tresc_raw = row[3] or "Brak treści"

            # LOGIKA TREŚCI: Tylko nieodczytane dostają tekst do encji (FIX 16KB)
            if is_unread:
                tresc_safe = tresc_raw if (len(tresc_raw) <= 2000) else tresc_raw[:1997] + "..."
            else:
                tresc_safe = "" # Przeczytane nie mają treści w encji

            student_messages.append({
                "data": row[0].replace('T', ' ')[:16],
                "nadawca": row[1],
                "temat": row[2],
                "tresc": tresc_safe,
                "przeczytana": not is_unread
            })

        ha_url = f"http://supervisor/core/api/states/sensor.vultron_wiadomosci_{slug}"
        payload = {
            "state": unread_total_in_db,
            "attributes": {
                "wiadomosci": student_messages,
                "friendly_name": f"Wiadomości: {display_name}",
                "stats": f"{unread_total_in_db} / {total_count}",
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "icon": "mdi:email-outline" if unread_total_in_db == 0 else "mdi:email-alert"
            }
        }

        headers = {
            "Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"
        }

        requests.post(ha_url, headers=headers, json=payload, timeout=10)
        log(f"Zaktualizowano: {display_name} (Licznik: {unread_total_in_db}/{total_count})")

    conn.close()

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {e}")