#!/bin/bash
# Täglicher Lauf (systemd-Benutzer-Timer klobasa-de.timer, 01:30, Persistent=true):
#   crawl.py -> venues.py (nur neue Spielstätten) -> deploy.sh --push
# Veröffentlicht NICHT, wenn der Abruf abbricht (403/429, 5 Fehler in Folge) oder über 5 % der Abrufe fehlschlagen;
# dann bleibt der alte Stand online. Log: ~/klobasa-de-data/logs/taeglich.log
# MANDANTEN: Sachsen (63) und DFB-Ebene (89: Bundesliga bis Regionalliga, Frauen-Bundesligen), bundesweit später mit
# "alle"; dieselbe Liste bestimmt, was veröffentlicht wird.
# Sperren (scripts/sperren.py): vorrang.lock (Vorrang vor der Erstbefüllung, die dann pausiert), pipeline.lock (nie
# gleichzeitig mit der Erstbefüllung, wartet bis 6 h) und in crawl.py fussballde.lock. Bei 403/429/Prüfseite legt
# crawl.py fussballde_gesperrt.txt an; bis zur Freigabe (erstbefuellung.py --freigabe) wird nichts abgerufen.
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
DATA=~/klobasa-de-data
LOG=$DATA/logs/taeglich.log
MANDANTEN=${MANDANTEN:-63,89}
PY="$REPO/.venv/bin/python -I"
mkdir -p "$DATA/logs"
exec 8>"$DATA/vorrang.lock"
flock 8   # Vorrang anmelden: die Erstbefüllung sichert ihren Stand und gibt pipeline.lock frei
exec 9>"$DATA/pipeline.lock"
flock -w 21600 9 || { echo "$(date '+%F %T') Sperre nach 6 h nicht frei, übersprungen" >> "$LOG"; exit 1; }
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
log "Start (Mandanten $MANDANTEN)"

$PY "$REPO/scripts/crawl.py" --mandanten "$MANDANTEN" --warten 60 >> "$DATA/logs/taeglich_crawl.log" 2>&1; RC=$?
if [ $RC -eq 2 ]; then
  log "fussball.de GESPERRT ($(cat "$DATA/fussballde_gesperrt.txt" 2>/dev/null)) – nichts abgerufen/veröffentlicht, warte auf Freigabe"; exit 1
elif [ $RC -ne 0 ]; then
  log "crawl.py abgebrochen (Code $RC) – nichts veröffentlicht, alter Stand bleibt"; exit 1
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

# Ligennamen, die nach dem Kürzen noch nach Sponsor aussehen (Liste: logs/sponsoren_verdacht.txt); Fehler hier stoppen nichts
log "$( ( set -a; . ~/.config/klobasa-de/env; set +a; $PY "$REPO/scripts/sponsor_pruefung.py" ) 2>&1 | tail -1 | cut -c1-300)"
