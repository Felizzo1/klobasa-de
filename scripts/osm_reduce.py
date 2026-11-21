#!/usr/bin/env python3
"""Verdichtet die mit osm_extract.sh gefilterten OSM-Daten zu kleinen JSON-Tabellen für venues.py.

  .venv/bin/python scripts/osm_reduce.py

Eingabe/Ausgabe in ~/klobasa-de-data/osm/ (nicht im Repo). Daten © OpenStreetMap-Mitwirkende, ODbL.
  plz.json      {plz: [lat, lon]}                       Mittelpunkt (repräsentativer Punkt) des PLZ-Gebiets
  pitches.json  [[osm_id, art, name, surface, lat, lon, plz, sport, fläche_m2]]  Fußballplätze + benannte Stadien/Anlagen
                (fläche 0 = nur als Punkt erfasst)
  --nur-plaetze: nur pitches.json neu erzeugen (Straßen dauern lange)
  streets.json  {"plz|straßenname": [lat, lon]}          Mittelpunkt aller Abschnitte einer Straße im PLZ-Gebiet
"""
import json, math, os, re, sys, time
import numpy as np
import shapely
from shapely.geometry import shape

D = os.path.expanduser('~/klobasa-de-data/osm')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import street_key  # noqa: E402

def features(name):
    with open(os.path.join(D, name), encoding='utf-8') as f:
        for line in f:
            line = line.strip().lstrip('\x1e')
            if line:
                yield json.loads(line)

t0 = time.time()
# --- PLZ-Gebiete
plz_codes, plz_geoms, plz_out = [], [], {}
for ft in features('plz.geojsonseq'):
    p = ft['properties']
    code = p.get('postal_code') or p.get('ref') or p.get('name')
    if not code or not re.fullmatch(r'\d{5}', code):
        continue
    g = shape(ft['geometry'])
    if g.is_empty:
        continue
    pt = g.representative_point()
    plz_codes.append(code); plz_geoms.append(g)
    plz_out[code] = [round(pt.y, 5), round(pt.x, 5)]
tree = shapely.STRtree(plz_geoms)
print(f'{len(plz_out)} PLZ-Gebiete ({time.time()-t0:.0f} s)', flush=True)

def plz_of(xs, ys):
    """PLZ für viele Punkte auf einmal (Punkt im Polygon)."""
    pts = shapely.points(xs, ys)
    idx_pt, idx_poly = tree.query(pts, predicate='within')
    res = [None] * len(xs)
    for i, j in zip(idx_pt, idx_poly):
        res[i] = plz_codes[j]
    return res

# --- Plätze
SOCCER = re.compile(r'(^|;)\s*(soccer|football|fußball)\s*($|;)', re.I)
rows = []
for ft in features('pitches.geojsonseq'):
    p = ft['properties']
    art = p.get('leisure')
    sport = p.get('sport', '')
    if art == 'pitch' and not SOCCER.search(sport):
        continue
    if art in ('stadium', 'sports_centre') and not (p.get('name') and (not sport or SOCCER.search(sport))):
        continue
    g = shape(ft['geometry'])
    c = g.representative_point() if g.geom_type != 'Point' else g
    oid = ft.get('id') or p.get('@id') or ''
    # Fläche in m² (Grad² -> m² mit Breitenkorrektur, genügt für den Vergleich mit 3.000 m²)
    area = g.area * 111320.0 ** 2 * math.cos(math.radians(c.y)) if g.geom_type in ('Polygon', 'MultiPolygon') else 0
    rows.append([str(oid), art, p.get('name', ''), p.get('surface', ''), round(c.y, 6), round(c.x, 6), None, sport, round(area)])
pl = plz_of([r[5] for r in rows], [r[4] for r in rows])
for r, z in zip(rows, pl):
    r[6] = z
json.dump(rows, open(os.path.join(D, 'pitches.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print(f'{len(rows)} Fußballplätze/Anlagen ({time.time()-t0:.0f} s)', flush=True)

if '--nur-plaetze' in sys.argv:
    sys.exit(0)

# --- Straßen: ein Punkt je Abschnitt, gemittelt je (PLZ, Name)
names, xs, ys = [], [], []
for ft in features('streets.geojsonseq'):
    nm = ft['properties'].get('name')
    co = ft['geometry']['coordinates']
    if not nm or not co:
        continue
    x, y = co[len(co) // 2]
    names.append(street_key(nm)); xs.append(x); ys.append(y)
print(f'{len(names)} Straßenabschnitte gelesen ({time.time()-t0:.0f} s)', flush=True)
acc = {}
CH = 500000
for s in range(0, len(names), CH):
    pl = plz_of(xs[s:s + CH], ys[s:s + CH])
    for nm, x, y, z in zip(names[s:s + CH], xs[s:s + CH], ys[s:s + CH], pl):
        if z and nm:
            a = acc.setdefault(z + '|' + nm, [0.0, 0.0, 0])
            a[0] += y; a[1] += x; a[2] += 1
streets = {k: [round(a[0] / a[2], 5), round(a[1] / a[2], 5)] for k, a in acc.items()}
json.dump(streets, open(os.path.join(D, 'streets.json'), 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(plz_out, open(os.path.join(D, 'plz.json'), 'w', encoding='utf-8'))
print(f'{len(streets)} Straßen je PLZ, fertig ({time.time()-t0:.0f} s)', flush=True)
