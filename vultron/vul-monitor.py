import requests
import json
import os
from datetime import datetime


def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [MONITOR] {message}")


log("Start monitorowania rozmiaru encji Vultron.")

SUPERVISOR_URL = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

LIMIT_WARNING = 14000
LIMIT_CRITICAL = 15500

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# =========================
# POBIERANIE STANÓW
# =========================
try:
    log("Pobieranie stanów z Home Assistant...")
    response = requests.get(f"{SUPERVISOR_URL}/states", headers=headers, timeout=15)
    log(f"Status odpowiedzi HA: {response.status_code}")

    entities = response.json()
    log(f"Pobrano {len(entities)} encji ogółem.")

except Exception as e:
    log(f"BŁĄD pobierania stanów: {e}")
    exit(1)


# =========================
# ANALIZA ENCJI
# =========================
total_size = 0
details = []
warnings = []
critical = False
vultron_count = 0

for entity in entities:
    entity_id = entity.get("entity_id", "")

    if entity_id.startswith("sensor.vultron_"):
        vultron_count += 1

        attributes = entity.get("attributes", {})
        attr_size = len(json.dumps(attributes))

        log(f"Analiza {entity_id} → {attr_size} B")

        total_size += attr_size
        name = entity_id.replace("sensor.vultron_", "")
        details.append(f"{name}: {attr_size} B")

        if attr_size > LIMIT_WARNING:
            warnings.append(entity_id)
            log(f"⚠ Przekroczony próg WARNING ({LIMIT_WARNING}) → {entity_id}")

        if attr_size > LIMIT_CRITICAL:
            critical = True
            log(f"🔥 Przekroczony próg CRITICAL ({LIMIT_CRITICAL}) → {entity_id}")

log(f"Znaleziono {vultron_count} encji Vultron.")
log(f"Łączny rozmiar atrybutów: {total_size} B")


# =========================
# WYSYŁKA SENSOR
# =========================
try:
    log("Wysyłanie sensora vultron_system_monitor...")
    res_sensor = requests.post(
        f"{SUPERVISOR_URL}/states/sensor.vultron_system_monitor",
        headers=headers,
        json={
            "state": total_size,
            "attributes": {
                "unit_of_measurement": "B",
                "icon": "mdi:database-check",
                "szczegoly": " | ".join(details) if details else "Brak danych",
                "ostrzezenia": ", ".join(warnings) if warnings else "Brak",
            },
        },
        timeout=10
    )
    log(f"Status zapisu sensor: {res_sensor.status_code}")

except Exception as e:
    log(f"BŁĄD zapisu sensor.vultron_system_monitor: {e}")


# =========================
# WYSYŁKA BINARY SENSOR
# =========================
try:
    log("Wysyłanie binary_sensor vultron_rozmiar_alert...")
    res_binary = requests.post(
        f"{SUPERVISOR_URL}/states/binary_sensor.vultron_rozmiar_alert",
        headers=headers,
        json={
            "state": "on" if critical else "off",
            "attributes": {
                "device_class": "problem",
            },
        },
        timeout=10
    )
    log(f"Status zapisu binary_sensor: {res_binary.status_code}")

except Exception as e:
    log(f"BŁĄD zapisu binary_sensor.vultron_rozmiar_alert: {e}")


log("Monitor zakończył działanie.")
