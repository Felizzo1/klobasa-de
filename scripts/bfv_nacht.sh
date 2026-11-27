#!/bin/bash
# Nächtliche Auffrischung der bfv.de-Mannschaftsseiten (Timer klobasa-de-bfv.timer, 22:05, ab 28.11.2026; einmalig
# klobasa-de-bfv-nacht3.timer am 27.11. mit 4.000 Seiten): bfv.py nach Fälligkeit (alle 4 Wochen, wöchentlich bei Spiel in
# den nächsten 14 Tagen), höchstens MAX Seiten, danach venues.py für neue Spielstätten (wartet auf pipeline.lock).
# Timer mit Persistent=true: wird ein verpasster Lauf tagsüber nachgeholt (nach Neustart), endet er spätestens 21:55,
# damit er dem nächsten regulären Lauf um 22:05 nicht im Weg steht. Exit ≠ 0, wenn bfv.py oder venues.py scheitern (ntfy per OnFailure).
MAX=${1:-1000}
cd "$(dirname "$0")/.." || exit 1
L=~/klobasa-de-data/logs/bfv.log
H=$((10#$(date +%H)))
if [ "$H" -ge 7 ] && [ "$H" -lt 22 ]; then ENDE=21:55; echo "$(date '+%F %T') Nachhollauf tagsüber, Ende spätestens $ENDE" >> "$L"; else ENDE=07:00; fi
.venv/bin/python -I scripts/bfv.py --mandanten 31 --ende "$ENDE" --max "$MAX" >> "$L" 2>&1; RC1=$?
echo "$(date '+%F %T') bfv.py beendet (Code $RC1), venues.py wartet auf pipeline.lock" >> "$L"
flock -w 14400 ~/klobasa-de-data/pipeline.lock .venv/bin/python -I scripts/venues.py --ende 09:00 >> ~/klobasa-de-data/logs/bfv_venues.log 2>&1; RC2=$?
echo "$(date '+%F %T') venues.py beendet (Code $RC2)" >> "$L"
[ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ]
