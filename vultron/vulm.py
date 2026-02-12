import json, os, requests, pickle
from datetime import datetime

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [VULM] {message}")

HA_TOKEN = os.getenv('SUPERVISOR_TOKEN')
DATA_TEMP = '/data/messages_cache.json'
VUL_PKL = '/data/vul.pkl'

try:
    if not os.path.exists(DATA_TEMP) or not os.path.exists(VUL_PKL):
        exit(0)

    with open(DATA_TEMP, 'r', encoding='utf-8') as f:
        msg_cache = json.load(f)
    all_messages = msg_cache.get('all', [])

    with open(VUL_PKL, 'rb') as f:
        bundle = pickle.load(f)

    for student in bundle.get('students', []):
        slug = student.get('slug')
        display_name = student.get('uczen', 'Nieznany')
        first_name = display_name.split(' ')[0]

        student_messages = [
            m for m in all_messages 
            if first_name.lower() in m.get('skrzynka', '').lower()
        ]
        
        if not student_messages:
            student_messages = all_messages

        unread_msgs = [m for m in student_messages if m.get('przeczytana') is False]
        read_msgs = [m for m in student_messages if m.get('przeczytana') is True]
        
        read_msgs.sort(key=lambda x: x.get('data', ''), reverse=True)
        final_selection = unread_msgs + read_msgs[:10]
        final_selection.sort(key=lambda x: x.get('data', ''), reverse=True)

        formatted_list = []
        for m in final_selection:
            formatted_list.append({
                "data": m.get('data', '').replace('T', ' ')[:16],
                "nadawca": m.get('korespondenci', 'Nieznany'),
                "temat": m.get('temat', 'Brak tematu'),
                "tresc": m.get('tresc', 'Brak treści'), # <--- TO POLE MUSI BYĆ WYSŁANE
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
            "Authorization": f"Bearer {HA_TOKEN}", 
            "Content-Type": "application/json"
        }
        
        requests.post(ha_url, headers=headers, json=payload, timeout=10)
        log(f"Zaktualizowano {slug} ({display_name}). Nieprzeczytane: {len(unread_msgs)}")

except Exception as e:
    log(f"BŁĄD: {e}")

