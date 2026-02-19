import requests
import json
import os

SUPERVISOR_URL = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

LIMIT_WARNING = 14000
LIMIT_CRITICAL = 15500

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Pobierz wszystkie stany
response = requests.get(f"{SUPERVISOR_URL}/states", headers=headers)
entities = response.json()

total_size = 0
details = []
warnings = []
critical = False

for entity in entities:
    entity_id = entity.get("entity_id", "")

    if entity_id.startswith("sensor.vultron_"):
        attributes = entity.get("attributes", {})
        attr_size = len(json.dumps(attributes))

        total_size += attr_size
        name = entity_id.replace("sensor.vultron_", "")
        details.append(f"{name}: {attr_size} B")

        if attr_size > LIMIT_WARNING:
            warnings.append(entity_id)

        if attr_size > LIMIT_CRITICAL:
            critical = True


# --- SENSOR ---
requests.post(
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
)

# --- BINARY SENSOR ---
requests.post(
    f"{SUPERVISOR_URL}/states/binary_sensor.vultron_rozmiar_alert",
    headers=headers,
    json={
        "state": "on" if critical else "off",
        "attributes": {
            "device_class": "problem",
        },
    },
)
