#!/bin/bash
export TZ=Europe/Warsaw

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [SYSTEM] $1"; }

# Kopiowanie plików kart
mkdir -p /config/www/vultron
cp /app/vultron*.js /config/www/vultron/
log "Karty skopiowane do /config/www/vultron/"

# AUTOMATYCZNA REJESTRACJA W HA
log "Uruchamiam automatyczną konfigurację zasobów Lovelace..."
python3 /app/setup_ui.py

while true; do
    H=$(date +%H | sed 's/^0//')
    [ -z "$H" ] && H=0

    if [ "$H" -ge 1 ] && [ "$H" -le 5 ]; then
        log "Przerwa nocna (01:00-05:59). Czekam..."
        sleep 1800
        continue
    fi

    log "--- START CYKLU SYNCHRONIZACJI ---"
    python3 /app/vul.py
    python3 /app/vulo.py
    python3 /app/vulp.py
    python3 /app/vuls.py

    WAIT=$(( 2400 + RANDOM % 1201 ))
    log "Cykl zakończony. Następny za $(( WAIT / 60 )) min."
    sleep $WAIT
done
