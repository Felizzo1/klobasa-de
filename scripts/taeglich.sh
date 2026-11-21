#!/bin/bash
# Täglicher Lauf (systemd-Benutzer-Timer klobasa-de.timer, 01:30, Persistent=true):
#   crawl.py -> venues.py (nur neue Spielstätten) -> deploy.sh --push
# Veröffentlicht NICHT, wenn der Abruf abbricht (403/429, 5 Fehler in Folge) oder über 5 % der Abrufe fehlschlagen;
# dann bleibt der alte Stand online. Log: ~/klobasa-de-data/logs/taeglich.log
# MANDANTEN: zunächst nur Sachsen (63), bundesweit später mit "alle"; dieselbe Liste bestimmt, was veröffentlicht wird.
# Sperren: pipeline.lock (täglicher Lauf und Erstbefüllung nacheinander, wartet bis 6 h) und in crawl.py
# fussballde.lock (nie zwei Zugriffe auf fussball.de gleichzeitig, auch nicht bei Handaufrufen).
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
DATA=~/klobasa-de-data
LOG=$DATA/logs/taeglich.log
MANDANTEN=${MANDANTEN:-63}
PY="$REPO/.venv/bin/python -I"
mkdir -p "$DATA/logs"
exec 9>"$DATA/pipeline.lock"
flock -w 21600 9 || { echo "$(date '+%F %T') Sperre nach 6 h nicht frei, übersprungen" >> "$LOG"; exit 1; }
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
log "Start (Mandanten $MANDANTEN)"

if ! $PY "$REPO/scripts/crawl.py" --mandanten "$MANDANTEN" --warten 60 >> "$DATA/logs/taeglich_crawl.log" 2>&1; then
  log "crawl.py abgebrochen – nichts veröffentlicht, alter Stand bleibt"; exit 1
fi
read -r ABRUFE FEHLER < <(sqlite3 -separator ' ' "$DATA/klobasa.sqlite" "SELECT abrufe, fehler FROM laeufe ORDER BY id DESC LIMIT 1")
log "crawl.py fertig: $ABRUFE Abrufe, $FEHLER Fehler"
if [ "${ABRUFE:-0}" -gt 0 ] && [ $((FEHLER * 100)) -gt $((ABRUFE * 5)) ]; then
  log "zu viele Fehler (> 5 %) – nichts veröffentlicht, alter Stand bleibt"; exit 1
fi

if ! $PY "$REPO/scripts/venues.py" >> "$DATA/logs/taeglich_venues.log" 2>&1; then
  log "venues.py mit Fehler beendet (neue Spielstätten evtl. ohne Koordinaten) – veröffentliche trotzdem"
fi
log "venues.py: $(tail -1 "$DATA/logs/taeglich_venues.log" | cut -c1-300)"

if KLOBASA_MANDANTEN="$MANDANTEN" "$REPO/scripts/deploy.sh" --push >> "$DATA/logs/taeglich_deploy.log" 2>&1; then
  log "veröffentlicht"
else
  log "deploy.sh fehlgeschlagen"; exit 1
fi
