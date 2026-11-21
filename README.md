# Klobása-Planer DE

Ansetzungen der Herren und Frauen in Deutschland (fussball.de) auf Karte und Liste, mit Europlan-Links.
Bauplan und Regeln: `CLAUDE.md`. Daten liegen außerhalb des Repos in `~/klobasa-de-data/`.

Täglicher Ablauf: `crawl.py` → `venues.py` → `deploy.sh` (ruft `preprocess.py` auf).

- `scripts/crawl.py` – Abruf fussball.de → SQLite (`.venv/bin/python scripts/crawl.py --mandanten 63`; `--nur-vereine` nur Vereinsspielpläne)
- `scripts/europlan.py` – Stadien eines Landes von Europlan (`python3 scripts/europlan.py 1 data/europlan_de.json`, fortsetzbar)
- `scripts/osm_extract.sh`, `scripts/osm_reduce.py` – OSM-Auszug (Plätze, Straßen, PLZ, Orte, Gemeinden) für venues.py
- `scripts/venues.py` – Spielstätten verorten, Europlan zuordnen (Ortsregel, Anlage-Link)
- `scripts/grenzen.py` – Länder-/Kreisgrenzen aus BKG VG250 für die Seite
- `scripts/ligaebenen_entwurf.py` – Entwurf für `data/ligaebenen.json` (von Felix zu prüfen)
- `scripts/preprocess.py` – Datenpakete je Bundesland (6 Wochen / Rest), verschlüsselt mit `fczcrypt.py`
- `scripts/deploy.sh` – Seite bauen (`--lokal` Klartext zum Testen), `--push` auf gh-pages (verwaister Zweig)
- `scripts/phase0.py` – Erreichbarkeitstest

Quellen: fussball.de, Europlan, © OpenStreetMap-Mitwirkende (ODbL), © BKG (2026) dl-de/by-2-0.
