#!/bin/bash
# Nächtliche Auffrischung der bfv.de-Mannschaftsseiten (Timer klobasa-de-bfv.timer, 22:05, ab 28.11.2026):
# bfv.py nach Fälligkeit (alle 4 Wochen, wöchentlich bei Spiel in den nächsten 14 Tagen), höchstens MAX Seiten,
# danach venues.py für neue Spielstätten (wartet auf pipeline.lock).
MAX=${1:-1000}
cd "$(dirname "$0")/.." || exit 1
L=~/klobasa-de-data/logs/bfv.log
.venv/bin/python -I scripts/bfv.py --mandanten 31 --ende 07:00 --max "$MAX" >> "$L" 2>&1; RC=$?
echo "$(date '+%F %T') bfv.py beendet (Code $RC), venues.py wartet auf pipeline.lock" >> "$L"
flock -w 14400 ~/klobasa-de-data/pipeline.lock .venv/bin/python -I scripts/venues.py --ende 09:00 >> ~/klobasa-de-data/logs/bfv_venues.log 2>&1; RC=$?
echo "$(date '+%F %T') venues.py beendet (Code $RC)" >> "$L"
