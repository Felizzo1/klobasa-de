#!/bin/bash
# Morgendliche Prüfung (Timer klobasa-de-pruefung.timer, 08:00, Persistent=true): ntfy, wenn der tägliche Lauf seit über
# 20 h nichts veröffentlicht hat. Greift auch, wenn der Lauf durch einen Absturz gar nicht zu Ende kam (dann meldet sonst niemand).
cd "$(dirname "$0")/.." || exit 1
LOG=~/klobasa-de-data/logs/taeglich.log
GRENZE=$((20 * 3600))
LETZT=$(grep ' veröffentlicht$' "$LOG" 2>/dev/null | tail -1 | cut -c1-19)
T=$(date -d "$LETZT" +%s 2>/dev/null || echo 0)
ALTER=$(( $(date +%s) - T ))
[ "$ALTER" -le "$GRENZE" ] && exit 0
STAND=${LETZT:-unbekannt}
if systemctl --user is-active -q klobasa-de.service || [ "$(systemctl --user is-active klobasa-de.service)" = activating ]; then
  TEXT="Der tägliche Lauf läuft noch. Letzte Veröffentlichung: $STAND."; PRIO=default
else
  TEXT="Seit über 20 h nichts veröffentlicht (zuletzt $STAND). Letzte Zeile: $(tail -1 "$LOG" | cut -c1-200)"; PRIO=high
fi
exec .venv/bin/python -I scripts/melde.py "Klobasa-Planer: Seite nicht aktuell" "$TEXT" "$PRIO"
