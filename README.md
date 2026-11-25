# Bratwurst-Planer (klobasa-de)

Ansetzungen der Herren und Frauen in Deutschland (fussball.de) auf Karte und Liste, mit Europlan-Links.
Bauplan und Regeln: `CLAUDE.md`. Daten liegen außerhalb des Repos in `~/klobasa-de-data/`.

Täglicher Ablauf: `scripts/taeglich.sh` (systemd-Benutzer-Timer `klobasa-de.timer`, 01:30): `crawl.py` → `venues.py` → `deploy.sh --push`; bei Abbruch oder über 5 % Fehlern wird nicht veröffentlicht.

- `scripts/crawl.py` – Abruf fussball.de → SQLite (`.venv/bin/python scripts/crawl.py --mandanten 63`; `--nur-vereine` nur Vereinsspielpläne)
- `scripts/europlan.py` – Stadien eines Landes von Europlan (`python3 scripts/europlan.py 1 ~/klobasa-de-data/europlan/europlan_de.json`, fortsetzbar)
- `scripts/osm_extract.sh`, `scripts/osm_reduce.py` – OSM-Auszug (Plätze, Straßen, PLZ, Orte, Gemeinden) für venues.py
- `scripts/venues.py` – Spielstätten verorten, Europlan zuordnen (Ortsregel, Anlage-Link)
- `scripts/grenzen.py` – Länder-/Kreisgrenzen aus BKG VG250 für die Seite
- `scripts/ligaebenen_entwurf.py` – Entwurf für `data/ligaebenen.json` (von Felix zu prüfen)
- `scripts/preprocess.py` – Datenpakete je Bundesland (6 Wochen / Rest), verschlüsselt mit `fczcrypt.py`
- `scripts/deploy.sh` – Seite bauen (`--lokal` Klartext zum Testen), `--push` auf gh-pages (verwaister Zweig)
- `scripts/phase0.py` – Erreichbarkeitstest
- `scripts/erstbefuellung.py` – Erstbefüllung bundesweit, Land für Land in mehreren Nächten (Timer `klobasa-de-erst.timer`, 02:00–07:30)
- `scripts/sponsor_pruefung.py` – meldet Ligennamen, die nach dem Kürzen noch nach Sponsor aussehen (`logs/sponsoren_verdacht.txt`); CZ-Sponsorenliste `site/cz_sponsoren.json`
- `scripts/ligatyp.py` – Positivliste Ligatypen (Spielklasse und Staffelname); Prüfliste `logs/ligatyp_pruefliste.tsv`, Ausfiltern erst mit `KLOBASA_LIGATYP=filter`, Ausnahmen in `data/ligatyp_freigabe.json`
- `scripts/namen.py` – gemeinsame Namensregeln: Personennamen in Klammern entfernen, Kleinfeld/7er/9er erkennen
- `scripts/druck_auswertung.py`: misst, wie oft die Druckansicht eine Spielstätte bringt, die der Vereinsspielplan nicht kannte
- `scripts/sperren.py` – gemeinsame Sperren: Vorrang des täglichen Laufs vor der Erstbefüllung, Sperrdatei bei 403/429/Prüfseite (Freigabe: `erstbefuellung.py --freigabe`)
- `scripts/make_icon.py` – Icons und Logo aus `data/logo_5e.svg` (Entwurf 5E); braucht cairosvg und Pillow im venv

Rohdaten aus fussball.de und Europlan (Spiele, Stadien, Handkorrekturen) liegen nie im Repo, nur in `~/klobasa-de-data/`.

Quellen: fussball.de, Europlan, © OpenStreetMap-Mitwirkende (ODbL), © BKG (2026) dl-de/by-2-0.
