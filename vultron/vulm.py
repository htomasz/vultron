import json, os, requests, pickle, time, sqlite3
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [VULM] {message}")

HA_TOKEN, DB_PATH, VUL_PKL = os.getenv('SUPERVISOR_TOKEN'), '/data/vultron.db', '/data/vul.pkl'

try:
    if not os.path.exists(VUL_PKL): exit(0)
    bundle = None
    for _ in range(5):
        try:
            with open(VUL_PKL, 'rb') as f: bundle = pickle.load(f); break
        except: time.sleep(1)
    if not bundle: exit(0)

    conn = sqlite3.connect(DB_PATH, timeout=20); cursor = conn.cursor()

    for student in bundle.get('students', []):
        slug, display_name = student.get('slug'), student.get('uczen', 'Nieznany')

        # Pobranie wiadomości dla konkretnego sluga
        cursor.execute("SELECT data, nadawca, temat, tresc, przeczytana FROM messages WHERE student_slug=? ORDER BY data DESC", (slug,))
        db_rows = cursor.fetchall()

        # Jeśli pusto, spróbuj pobrać wszystkie jako fallback (żeby nie było pusto w HA)
        if not db_rows:
            cursor.execute("SELECT data, nadawca, temat, tresc, przeczytana FROM messages ORDER BY data DESC LIMIT 50")
            db_rows = cursor.fetchall()

        student_messages = [{"data": r[0], "nadawca": r[1], "temat": r[2], "tresc": r[3], "przeczytana": bool(r[4])} for r in db_rows]

        unread = [m for m in student_messages if not m['przeczytana']]
        read = [m for m in student_messages if m['przeczytana']]

        # Przywrócenie limitu 50 ostatnich wiadomości
        final = (unread + read[:50])
        final.sort(key=lambda x: x['data'], reverse=True)

        formatted = []
        for m in final:
            tresc = m['tresc'] if len(m['tresc']) <= 2000 else m['tresc'][:1997] + "..."
            formatted.append({
                "data": m['data'].replace('T', ' ')[:16],
                "nadawca": m['nadawca'],
                "temat": m['temat'],
                "tresc": tresc,
                "przeczytana": m['przeczytana']
            })

        requests.post(f"http://supervisor/core/api/states/sensor.vultron_wiadomosci_{slug}",
            headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
            json={
                "state": len(unread),
                "attributes": {
                    "wiadomosci": formatted,
                    "friendly_name": f"Wiadomości: {display_name}",
                    "student_name": display_name,
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "icon": "mdi:email-outline" if len(unread) == 0 else "mdi:email-alert"
                }
            }, timeout=10)
    conn.close()
except Exception as e: log(f"BŁĄD: {e}")
