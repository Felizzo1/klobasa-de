# Klobása-Planer DE

Ansetzungen der Herren und Frauen in Deutschland (fussball.de) auf Karte und Liste, mit Europlan-Links.
Bauplan und Regeln: `CLAUDE.md`. Daten liegen außerhalb des Repos in `~/klobasa-de-data/`.

- `scripts/crawl.py` – Abruf fussball.de → SQLite (`.venv/bin/python scripts/crawl.py --mandanten 63`)
- `scripts/europlan.py` – Stadien eines Landes von Europlan (`python3 scripts/europlan.py 1 data/europlan_de.json`, fortsetzbar)
- `scripts/phase0.py` – Erreichbarkeitstest
