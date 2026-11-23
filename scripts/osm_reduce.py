#!/usr/bin/env python3
"""Verdichtet die mit osm_extract.sh gefilterten OSM-Daten zu kleinen JSON-Tabellen für venues.py.

  .venv/bin/python scripts/osm_reduce.py

Eingabe/Ausgabe in ~/klobasa-de-data/osm/ (nicht im Repo). Daten © OpenStreetMap-Mitwirkende, ODbL.
  plz.json      {plz: [lat, lon]}                       Mittelpunkt (repräsentativer Punkt) des PLZ-Gebiets
  pitches.json  [[osm_id, art, name, surface, lat, lon, plz, sport, fläche_m2]]  Fußballplätze + benannte Stadien/Anlagen
                (fläche 0 = nur als Punkt erfasst)
  places.json   {ortsname_gefaltet: [[lat, lon], ...]}  Ortsknoten, für die Ortsregel in venues.py
  gemeinden.json {"gem": [[name_gefaltet, lat, lon]], "orte": {ortsname_gefaltet: [gem_index je Punkt in places.json]}}
                Gemeinden (admin_level=8) für die Ortsregel: Ortsteile derselben Gemeinde widersprechen sich nicht
  --nur-plaetze: nur pitches.json, places.json und gemeinden.json neu erzeugen (Straßen dauern lange)
  --nur-gemeinden: nur gemeinden.json
  streets.json  {"plz|straßenname": [lat, lon]}          Mittelpunkt der Abschnitte einer Straße im PLZ-Gebiet, oder
                {"plz|straßenname": [[lat, lon, n], ...]}  bei getrennten gleichnamigen Teilen (über 1 km, z. B. in Ortsteilen),
                                                           nach Zahl der Abschnitte absteigend
  --nur-strassen: nur streets.json (braucht nur plz.geojsonseq und streets.geojsonseq)
"""
import json, math, os, re, sys, time
import numpy as np
import shapely
from shapely.geometry import shape

D = os.path.expanduser(os.environ.get('OSM_DIR') or '~/klobasa-de-data/osm')
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

if '--nur-strassen' not in sys.argv:  # --nur-strassen: nur streets.json (braucht nur plz.geojsonseq und streets.geojsonseq)
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
    if '--nur-gemeinden' not in sys.argv:
        json.dump(rows, open(os.path.join(D, 'pitches.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{len(rows)} Fußballplätze/Anlagen ({time.time()-t0:.0f} s)', flush=True)

    # --- Orte (Name gefaltet -> Koordinaten aller Orte dieses Namens)
    from geo import fold  # noqa: E402
    places = {}
    for ft in features('places.geojsonseq'):
        nm = ft['properties'].get('name')
        if nm:
            x, y = ft['geometry']['coordinates']
            places.setdefault(re.sub(r'[^a-z0-9]+', ' ', fold(nm)).strip(), []).append([round(y, 5), round(x, 5)])
    if '--nur-gemeinden' not in sys.argv:
        json.dump(places, open(os.path.join(D, 'places.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{len(places)} Ortsnamen ({time.time()-t0:.0f} s)', flush=True)

    # --- Gemeinden (admin_level=8): Gemeinde je Ortsknoten
    gem, gem_geoms = [], []
    for ft in features('gemeinden.geojsonseq'):
        p = ft['properties']
        if p.get('admin_level') != '8' or not p.get('name'):
            continue
        g = shape(ft['geometry'])
        if g.is_empty:
            continue
        pt = g.representative_point()
        gem.append([re.sub(r'[^a-z0-9]+', ' ', fold(p['name'])).strip(), round(pt.y, 5), round(pt.x, 5)]); gem_geoms.append(g)
    gtree = shapely.STRtree(gem_geoms)
    keys = [k for k, pts in places.items() for _ in pts]
    pts = [q for k in places for q in places[k]]
    ip, ig = gtree.query(shapely.points([q[1] for q in pts], [q[0] for q in pts]), predicate='within')
    gid = [-1] * len(pts)
    for i, j in zip(ip, ig):
        gid[i] = int(j)
    orte, k0 = {}, 0
    for k, ps in places.items():
        orte[k] = gid[k0:k0 + len(ps)]; k0 += len(ps)
    json.dump({'gem': gem, 'orte': orte}, open(os.path.join(D, 'gemeinden.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{len(gem)} Gemeinden, {sum(g >= 0 for g in gid)}/{len(gid)} Ortsknoten zugeordnet ({time.time()-t0:.0f} s)', flush=True)

if '--nur-plaetze' in sys.argv or '--nur-gemeinden' in sys.argv:
    sys.exit(0)

# --- Straßen: ein Punkt je Abschnitt; je (PLZ, Name) zusammenhängende Teile (Einfachverkettung, Abstand bis 1 km)
# getrennt mitteln. Gleichnamige Straßen in verschiedenen Ortsteilen derselben PLZ („Am Sportplatz“ in Großenhain und
# in Zabeltitz) bleiben so getrennt, statt einen Punkt irgendwo dazwischen zu ergeben.
LINK_KM = 1.0
name_id, nid, xs, ys = {}, [], [], []
for ft in features('streets.geojsonseq'):
    nm = ft['properties'].get('name')
    co = ft['geometry']['coordinates']
    if not nm or not co:
        continue
    k = street_key(nm)
    if not k:
        continue
    x, y = co[len(co) // 2]
    nid.append(name_id.setdefault(k, len(name_id))); xs.append(x); ys.append(y)
print(f'{len(nid)} Straßenabschnitte gelesen ({time.time()-t0:.0f} s)', flush=True)
plz_id = {z: i for i, z in enumerate(sorted(set(plz_codes)))}
pid = np.full(len(nid), -1, dtype=np.int64)
CH = 500000
for s0 in range(0, len(nid), CH):
    for i, z in enumerate(plz_of(xs[s0:s0 + CH], ys[s0:s0 + CH])):
        if z: pid[s0 + i] = plz_id[z]
X = np.array(xs); Y = np.array(ys); N = np.array(nid, dtype=np.int64)
del xs, ys, nid
ok = pid >= 0
key = pid[ok] * 10_000_000 + N[ok]; X = X[ok]; Y = Y[ok]
order = np.argsort(key, kind='stable'); key = key[order]; X = X[order]; Y = Y[order]
names_by_id = {v: k for k, v in name_id.items()}; plz_by_id = {v: k for k, v in plz_id.items()}
grenzen = np.flatnonzero(np.diff(key)) + 1
streets = {}; n_multi = 0
for g0, g1 in zip(np.r_[0, grenzen], np.r_[grenzen, len(key)]):
    k = int(key[g0]); kk = plz_by_id[k // 10_000_000] + '|' + names_by_id[k % 10_000_000]
    ys_, xs_ = Y[g0:g1], X[g0:g1]
    m = len(ys_)
    if m == 1:
        streets[kk] = [round(float(ys_[0]), 5), round(float(xs_[0]), 5)]; continue
    # Einfachverkettung über die Abschnittsmitten
    cl = list(range(m))
    def find(i):
        while cl[i] != i:
            cl[i] = cl[cl[i]]; i = cl[i]
        return i
    cosl = math.cos(math.radians(float(ys_[0])))
    for i in range(m):
        dy = (ys_[i + 1:] - ys_[i]) * 111.2; dx = (xs_[i + 1:] - xs_[i]) * 111.2 * cosl
        for j in np.flatnonzero(dy * dy + dx * dx <= LINK_KM * LINK_KM):
            a_, b_ = find(i), find(i + 1 + int(j))
            if a_ != b_: cl[a_] = b_
    teile = {}
    for i in range(m):
        t = teile.setdefault(find(i), [0.0, 0.0, 0]); t[0] += float(ys_[i]); t[1] += float(xs_[i]); t[2] += 1
    if len(teile) == 1:
        t = next(iter(teile.values())); streets[kk] = [round(t[0] / t[2], 5), round(t[1] / t[2], 5)]
    else:
        n_multi += 1
        streets[kk] = sorted(([round(t[0] / t[2], 5), round(t[1] / t[2], 5), t[2]] for t in teile.values()), key=lambda t: -t[2])
json.dump(streets, open(os.path.join(D, 'streets.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print(f'{len(streets)} Straßen je PLZ, davon {n_multi} in mehreren getrennten Teilen ({time.time()-t0:.0f} s)', flush=True)
if '--nur-strassen' in sys.argv:
    sys.exit(0)
json.dump(plz_out, open(os.path.join(D, 'plz.json'), 'w', encoding='utf-8'))
print(f'{len(streets)} Straßen je PLZ, fertig ({time.time()-t0:.0f} s)', flush=True)
