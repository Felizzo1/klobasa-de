#!/usr/bin/env python3
"""Wie oft liefert die Druckansicht eine Spielstätte, die der Vereinsspielplan nicht schon kannte?

Grundlage: Tabelle druck_vergleich (crawl.py, je Spiel die erste Druckansicht). Zählt nur Spiele, deren Heimverein
zu dem Zeitpunkt schon einen Vereinsspielplan bis Saisonende hatte. Zeigt außerdem, ob alle Vereine einmal durch sind.
Aufruf: python3 scripts/druck_auswertung.py [--mandanten 63,89,…]
"""
import argparse, os, sqlite3, time

DB = os.environ.get('KLOBASA_DB') or os.path.expanduser('~/klobasa-de-data/klobasa.sqlite')
ap = argparse.ArgumentParser(); ap.add_argument('--mandanten'); a = ap.parse_args()
c = sqlite3.connect(DB)
md = a.mandanten.split(',') if a.mandanten else [r[0] for r in c.execute('SELECT DISTINCT mandant FROM staffeln')]
q = ','.join('?' * len(md))

# 1. Sind alle Vereine mit künftigen Heimspielen einmal bis Saisonende geholt?
gesamt, durch = c.execute(f'''SELECT COUNT(*), SUM(v.saison_geholt IS NOT NULL) FROM (SELECT DISTINCT p.heim_verein FROM spiele p
    JOIN staffeln s USING(staffel_id) WHERE s.im_menue=1 AND s.mandant IN ({q}) AND p.status='normal'
    AND p.heim_verein IS NOT NULL AND p.datum>=date('now')) x LEFT JOIN vereine v ON v.verein_id=x.heim_verein''', md).fetchone()
print(f'Vereinsspielpläne bis Saisonende: {durch or 0} von {gesamt} Vereinen einmal geholt')

# 2. Vergleich, nur Spiele mit Heimverein, dessen Saison-Spielplan vor der Druckansicht geholt war
rows = c.execute(f'''SELECT d.vorher, d.vorher_quelle, d.druck, strftime('%w', p.datum) FROM druck_vergleich d
    JOIN spiele p USING(spiel_id) JOIN staffeln s USING(staffel_id) JOIN vereine v ON v.verein_id=p.heim_verein
    WHERE s.mandant IN ({q}) AND v.saison_geholt IS NOT NULL AND v.saison_geholt <= d.zeit''', md).fetchall()
n = len(rows)
gleich = sum(1 for v, qu, d, _ in rows if qu == 'verein' and v == d)
anders = sum(1 for v, qu, d, _ in rows if qu == 'verein' and v != d)
fehlte = n - gleich - anders
print(f'Spiele mit Druckansicht nach Vereinsspielplan: {n}')
if n:
    print(f'  schon bekannt, gleich:          {gleich} ({100*gleich/n:.1f} %)')
    print(f'  Vereinsspielplan anderer Ort:   {anders} ({100*anders/n:.1f} %)')
    print(f'  Vereinsspielplan ohne Ort:      {fehlte} ({100*fehlte/n:.1f} %)')
    print(f'  → Druckansicht brachte Neues:   {anders+fehlte} ({100*(anders+fehlte)/n:.1f} %)')
