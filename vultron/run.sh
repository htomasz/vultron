#!/bin/bash
export TZ=Europe/Warsaw

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [SYSTEM] $1"; }

# --- DODATEK: Obsługa sygnałów stopu ---
term_handler() {
  log "Złapano sygnał STOP (SIGTERM/SIGINT). Kończę procesy i wychodzę..."
  pkill -f python3
  exit 0
}
trap 'term_handler' SIGTERM SIGINT

# Sprawdzenie czy folder docelowy istnieje
mkdir -p /config/www/vultron

# Kopiowanie kart (uproszczone)
cp /app/vultron*.js /config/www/vultron/ 2>/dev/null
log "Karty skopiowane do /config/www/vultron/"

# --- DODATEK: Cierpliwy start ---
# Czasami HA startuje wolniej niż Add-on. Poczekajmy na Supervisor API.
log "Sprawdzam dostępność API Home Assistant..."
until curl -s -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/core/api/config > /dev/null; do
  log "Oczekiwanie na gotowość Home Assistant Core..."
  sleep 5
done

log "Uruchamiam automatyczną konfigurację zasobów Lovelace..."
python3 setup_ui.py

while true; do
    H=$(date +%-H)

    if [ "$H" -ge 3 ] && [ "$H" -le 5 ]; then
        log "Przerwa nocna (03:00-05:59). Czekam..."
        sleep 1800
        continue
    fi

    log "--- START CYKLU SYNCHRONIZACJI ---"

    # Uruchamiamy vul.py (AUTH)
    python3 vul.py
    if [ $? -ne 0 ]; then
        log "!!! Wykryto błąd krytyczny logowania w vul.py. Samobójstwo kontenera. !!!"
        exit 1
    fi

    # Pozostałe skrypty
    # Dodajmy '|| true', żeby jeden błąd (np. w uwagach) nie blokował reszty synchronizacji
    python3 vulo.py || log "Błąd w vulo.py (oceny)"
    python3 vulp.py || log "Błąd w vulp.py (plan)"
    python3 vuls.py || log "Błąd w vuls.py (terminarz)"
    python3 vuluw.py || log "Błąd w vuluw.py (uwagi)"
    python3 vulf.py || log "Błąd w vulf.py (frekwencja)"
    python3 vulos.py || log "Błąd w vulos.py (osiągnięcia)"
    python3 vul-for-mess.py || log "Błąd w vul-for-mess.py (auth-wiadomości)"
    python3 vulm.py || log "Błąd w vulm.py (wiadomości)"

    WAIT=$(( 2400 + RANDOM % 1201 ))
    log "Cykl zakończony. Następny za $(( WAIT / 60 )) min."

    # Używamy pętli sleep, żeby szybciej reagować na sygnał STOP
    for ((i=0; i<WAIT; i+=10)); do
        sleep 10
    done
done
