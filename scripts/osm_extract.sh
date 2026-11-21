#!/bin/bash
# Filtert den Deutschland-Auszug von Geofabrik auf das, was venues.py braucht. Selten ausführen (z. B. einmal pro Halbserie).
#   scripts/osm_extract.sh [PBF]
# Ergebnis in ~/klobasa-de-data/osm/ (nicht im Repo):
#   pitches.geojsonseq   leisure=pitch|stadium|sports_centre (Flächen und Punkte, mit sport, surface, name)
#   streets.geojsonseq   benannte Straßen (highway=* mit name)
#   plz.geojsonseq       boundary=postal_code
#   places.geojsonseq    Ortsknoten (place=city|town|village|suburb|hamlet|quarter|isolated_dwelling|locality)
# Daten © OpenStreetMap-Mitwirkende, ODbL.
set -euo pipefail
D=~/klobasa-de-data/osm
PBF=${1:-$D/germany-latest.osm.pbf}
cd "$D"
echo "$(date +%T) Plätze"
osmium tags-filter -O -o pitches.osm.pbf "$PBF" nwr/leisure=pitch,stadium,sports_centre
osmium export -O -f geojsonseq -o pitches.geojsonseq pitches.osm.pbf
echo "$(date +%T) Straßen"
osmium tags-filter -O -o streets.osm.pbf "$PBF" w/highway w/name
# Zweiter Filter: nur Wege mit highway UND name (tags-filter verknüpft mehrere Ausdrücke mit ODER)
osmium tags-filter -O -o streets2.osm.pbf streets.osm.pbf w/highway
osmium tags-filter -O -o streets3.osm.pbf streets2.osm.pbf w/name
osmium export -O -f geojsonseq --geometry-types=linestring -a "" -o streets.geojsonseq streets3.osm.pbf
echo "$(date +%T) PLZ"
osmium tags-filter -O -o plz.osm.pbf "$PBF" r/boundary=postal_code
osmium export -O -f geojsonseq --geometry-types=polygon -o plz.geojsonseq plz.osm.pbf
echo "$(date +%T) Orte"
osmium tags-filter -O -o places.osm.pbf "$PBF" n/place=city,town,village,suburb,hamlet,quarter,isolated_dwelling,locality
osmium export -O -f geojsonseq -o places.geojsonseq places.osm.pbf
rm -f streets.osm.pbf streets2.osm.pbf
ls -la "$D"
echo "$(date +%T) fertig"
