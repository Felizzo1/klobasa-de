#!/usr/bin/env python3
"""Entwurf für data/ligaebenen.json: (Mandant, Mannschaftsart, Gebiet, Spielklasse) -> Ligaebene 1..13.

  .venv/bin/python scripts/ligaebenen_entwurf.py [--schreiben]

Ohne --schreiben nur Ausgabe. Vorhandene Einträge mit "geprueft": true bleiben unverändert.
Vorschlag:
  - Verbandsebene (Gebiet = Name des Verbands) nach BASIS je Mandant
  - Kreisebene: Spielklassen eines Gebiets in der Reihenfolge KREIS_REIHE durchnummeriert, beginnend direkt unter der
    untersten Verbandsklasse. So bekommt die 1. Kreisklasse in Kreisen ohne Kreisliga A/B eine höhere Ebene als dort,
    wo A und B dazwischen liegen.
Felix prüft den Entwurf und setzt "geprueft": true. Staffelnamen stehen nur in der Prüfliste außerhalb des Repos.
"""
import json, os, sqlite3, sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB = os.environ.get('KLOBASA_DB') or os.path.expanduser('~/klobasa-de-data/klobasa.sqlite')
OUT = os.path.join(ROOT, 'data', 'ligaebenen.json')
# Verbandsebene je Mandant und Mannschaftsart: Spielklasse -> Ebene (Bundesliga bis Oberliga liegen bei Mandant 89/NOFV)
BASIS = {
    '63': {'Herren': {'Landesliga': 6, 'Landesklasse': 7}, 'Frauen': {'Landesliga': 4, 'Landesklasse': 5}},
}
KREIS_REIHE = ['Kreisoberliga', '1.Kreisliga (A)', '2.Kreisliga (B)', '3.Kreisliga (C)', '1.Kreisklasse', '2.Kreisklasse',
               '3.Kreisklasse']

c = sqlite3.connect(DB)
rows = c.execute('''SELECT mandant, mandant_name, art, gebiet, spielklasse, group_concat(name, ' / ')
                    FROM (SELECT * FROM staffeln WHERE im_menue=1 ORDER BY name) GROUP BY 1, 2, 3, 4, 5''').fetchall()
alt = {}
if os.path.exists(OUT):
    for e in json.load(open(OUT, encoding='utf-8'))['eintraege']:
        alt[(e['mandant'], e['art'], e['gebiet'], e['spielklasse'])] = e
by_geb = defaultdict(list)
for r in rows:
    by_geb[(r[0], r[2], r[3])].append(r)
out = []; staffeln = {}
for (m, art, geb), rs in sorted(by_geb.items()):
    basis = BASIS.get(m, {}).get(art, {})
    unten = max(basis.values(), default=None)
    kreis = sorted((r for r in rs if r[4] in KREIS_REIHE), key=lambda r: KREIS_REIHE.index(r[4]))
    for r in rs:
        k = (m, art, geb, r[4])
        staffeln[k] = r[5]
        if k in alt and alt[k].get('geprueft'):
            out.append(alt[k]); continue
        if r[4] in basis:
            st, grund = basis[r[4]], 'Verbandsebene'
        elif r[4] in KREIS_REIHE and unten is not None:
            st, grund = unten + 1 + kreis.index(r), f'Kreisebene, Platz {kreis.index(r) + 1} im Gebiet'
        else:
            st, grund = None, 'unbekannt'
        out.append({'mandant': m, 'verband': r[1], 'art': art, 'gebiet': geb, 'spielklasse': r[4], 'stufe': st,
                    'geprueft': False, 'grund': grund})
for e in out:
    print(f"{e['art']:6} {e['stufe'] or '?':>2}  {e['gebiet'][:30]:30} {e['spielklasse']:16} "
          f"{staffeln.get((e['mandant'], e['art'], e['gebiet'], e['spielklasse']), '')[:70]}")
if '--schreiben' in sys.argv:
    json.dump({'hinweis': 'Ligaebene je (Mandant, Mannschaftsart, Gebiet, Spielklasse). Entwurf aus scripts/ligaebenen_entwurf.py; '
                          'geprueft=true schützt einen Eintrag vor dem Überschreiben.', 'eintraege': out},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # Prüfliste mit Staffelnamen (fussball.de-Daten) nur außerhalb des Repos
    with open(os.path.expanduser('~/klobasa-de-data/ligaebenen_pruefliste.tsv'), 'w', encoding='utf-8') as f:
        f.write('art\tstufe\tgebiet\tspielklasse\tstaffeln\n')
        for e in out:
            f.write(f"{e['art']}\t{e['stufe']}\t{e['gebiet']}\t{e['spielklasse']}\t"
                    f"{staffeln.get((e['mandant'], e['art'], e['gebiet'], e['spielklasse']), '')}\n")
    print('geschrieben:', OUT, len(out), 'Einträge; Prüfliste: ~/klobasa-de-data/ligaebenen_pruefliste.tsv')
