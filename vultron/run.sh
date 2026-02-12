#!/bin/bash
export TZ=Europe/Warsaw

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [SYSTEM] $1"; }

# Sprawdzenie czy folder docelowy istnieje, jeśli nie - stwórz
mkdir -p /config/www/vultron

# Kopiowanie plików kart - używamy ścieżek relatywnych z /app
cp vultron*.js /config/www/vultron/
log "Karty skopiowane do /config/www/vultron/"

# AUTOMATYCZNA REJESTRACJA W HA
log "Uruchamiam automatyczną konfigurację zasobów Lovelace..."
python3 setup_ui.py

while true; do
    # Pobranie godziny w formacie 0-23 (bez wiodącego zera)
    H=$(date +%-H)

    if [ "$H" -ge 1 ] && [ "$H" -le 5 ]; then
        log "Przerwa nocna (01:00-05:59). Czekam..."
        sleep 1800
        continue
    fi

    log "--- START CYKLU SYNCHRONIZACJI ---"
    
    # Uruchamiamy vul.py (AUTH)
    python3 vul.py
    if [ $? -ne 0 ]; then
        log "!!! Wykryto błąd krytyczny (prawdopodobnie logowanie) w vul.py. Zgodnie z instrukcją: SAMOBÓJSTWO KONTENERA. !!!"
        exit 1
    fi

    # Pozostałe skrypty (wykonają się tylko jeśli vul.py przeszedł pomyślnie)
    python3 vulo.py
    python3 vulp.py
    python3 vuls.py
    python3 vuluw.py
    python3 vulf.py
    python3 vulos.py
    python3 vul-for-mess.py
    python3 vulm.py

    # Losowanie czasu oczekiwania 40-60 min (2400-3600 sek)
    WAIT=$(( 2400 + RANDOM % 1201 ))
    log "Cykl zakończony. Następny za $(( WAIT / 60 )) min."
    sleep $WAIT
done