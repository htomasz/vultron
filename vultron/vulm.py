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
        first_name = display_name.split(' ')[0]

        # ODCZYT Z BAZY
        cursor.execute("SELECT data, nadawca, temat, tresc, przeczytana FROM messages WHERE student_slug=? ORDER BY data DESC", (slug,))
        db_rows = cursor.fetchall()

        # Fallback jeśli baza nie ma przypisanego student_slug (np. błąd dopasowania w vul-for-mess)
        if not db_rows:
            cursor.execute("SELECT data, nadawca, temat, tresc, przeczytana FROM messages ORDER BY data DESC LIMIT 50")
            db_rows = cursor.fetchall()

        student_messages = []
        for row in db_rows:
            student_messages.append({
                "data": row[0],
                "nadawca": row[1],
                "temat": row[2],
                "tresc": row[3],
                "przeczytana": bool(row[4])
            })

        unread_msgs = [m for m in student_messages if m.get('przeczytana') is False]
        read_msgs = [m for m in student_messages if m.get('przeczytana') is True]

        read_msgs.sort(key=lambda x: x.get('data', ''), reverse=True)
        # POWRÓT DO 50 WIADOMOŚCI
        final_selection = unread_msgs + read_msgs[:50]
        final_selection.sort(key=lambda x: x.get('data', ''), reverse=True)

        formatted_list = []
        for m in final_selection:
            tresc_raw = m.get('tresc', 'Brak treści')
            tresc_safe = tresc_raw if len(tresc_raw) <= 2000 else tresc_raw[:1997] + "..."

            formatted_list.append({
                "data": m.get('data', '').replace('T', ' ')[:16],
                "nadawca": m.get('nadawca', 'Nieznany'),
                "temat": m.get('temat', 'Brak tematu'),
                "tresc": tresc_safe,
                "przeczytana": m.get('przeczytana', True)
            })

        ha_url = f"http://supervisor/core/api/states/sensor.vultron_wiadomosci_{slug}"
        payload = {
            "state": len(unread_msgs),
            "attributes": {
                "wiadomosci": formatted_list,
                "friendly_name": f"Wiadomości: {display_name}",
                "student_name": display_name,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "icon": "mdi:email-outline" if len(unread_msgs) == 0 else "mdi:email-alert"
            }
        }

        headers = {
            "Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"
        }

        requests.post(ha_url, headers=headers, json=payload, timeout=10)
        log(f"Zaktualizowano: {display_name} (Nieprzeczytane: {len(unread_msgs)})")

    conn.close()

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {e}")