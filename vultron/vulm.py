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
            if bundle: break
        except: time.sleep(1)

    if not bundle: exit(0)

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()

    for student in bundle.get('students', []):
        slug = student.get('slug')
        display_name = student.get('uczen', 'Nieznany')

        # 1. LICZNIKI (Wszystkie i Nieprzeczytane w bazie)
        cursor.execute("SELECT COUNT(*) FROM messages WHERE student_slug=?", (slug,))
        total_in_db = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages WHERE student_slug=? AND przeczytana=0", (slug,))
        total_unread = cursor.fetchone()[0]

        # 2. POBIERANIE TOP 10 (Sortowanie: nieprzeczytane pierwsze, potem data)
        cursor.execute("""
            SELECT data, nadawca, temat, tresc, przeczytana
            FROM messages
            WHERE student_slug=?
            ORDER BY przeczytana ASC, data DESC
            LIMIT 10
        """, (slug,))
        db_rows = cursor.fetchall()

        formatted_list = []
        for row in db_rows:
            is_unread = not bool(row[4])
            tresc_raw = row[3] or "Brak treści"

            # LOGIKA OSZCZĘDZANIA MIEJSCA:
            # Treść wysyłamy TYLKO jeśli wiadomość jest nieprzeczytana
            if is_unread:
                # Ograniczamy treść do 2000 znaków (bezpiecznik)
                tresc_to_send = tresc_raw if len(tresc_raw) <= 2000 else tresc_raw[:1997] + "..."
            else:
                # Dla przeczytanych nie wysyłamy treści wcale
                tresc_to_send = None

            formatted_list.append({
                "data": row[0].replace('T', ' ')[:16],
                "nadawca": row[1],
                "temat": row[2],
                "tresc": tresc_to_send,
                "przeczytana": not is_unread
            })

        # 3. WYSYŁKA DO HA
        ha_url = f"http://supervisor/core/api/states/sensor.vultron_wiadomosci_{slug}"
        payload = {
            "state": total_unread, # Stanem jest liczba nieprzeczytanych
            "attributes": {
                "wiadomosci": formatted_list,
                "friendly_name": f"Wiadomości: {display_name}",
                "stats": f"{total_unread} / {total_in_db}", # Licznik na kartę
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "icon": "mdi:email-outline" if total_unread == 0 else "mdi:email-alert"
            }
        }

        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        requests.post(ha_url, headers=headers, json=payload, timeout=10)
        log(f"Zaktualizowano {display_name}: {total_unread}/{total_in_db}")

    conn.close()
except Exception as e:
    log(f"BŁĄD: {e}")