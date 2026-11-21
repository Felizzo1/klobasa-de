#!/usr/bin/env python3
"""Länder- und Kreisgrenzen für die Seite aus BKG VG250 (Ebenen, Stand 01.01., UTM32s, Shape).

  .venv/bin/python scripts/grenzen.py [VG250-Ordner]

Quelle: © BKG (2026) dl-de/by-2-0, https://gdz.bkg.bund.de – Datenquellen: https://sgx.geodatenzentrum.de/web_public/gdz/datenquellen/datenquellen_vg_nuts.pdf
Download: https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/aktuell/vg250_01-01.utm32s.shape.ebenen.zip
Ausgabe:
  site/laender.json, site/kreise/<AGS>.json  vereinfacht für die Karte, Kreise je Land (gemeinsame Grenzen bleiben deckungsgleich)
  ~/klobasa-de-data/grenzen_genau.json     genauer, für die Zuordnung Spiel -> Land/Kreis in preprocess.py (nicht im Repo)
"""
import json, os, sys
import shapefile, shapely
from pyproj import Transformer
from shapely.geometry import shape, mapping

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/klobasa-de-data/vg250/x/vg250_ebenen_0101')
TOL = {'LAN': 0.008, 'KRS': 0.005}  # Grad, etwa 0,4–0,8 km: reicht für Umrisse auf der Karte
tr = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True).transform
def _tr(xy):
    import numpy as np
    x, y = tr(xy[:, 0], xy[:, 1])
    return np.column_stack([x, y])

def read(layer):
    r = shapefile.Reader(os.path.join(SRC, f'VG250_{layer}'), encoding='utf-8')
    out = []
    for sr in r.iterShapeRecords():
        a = sr.record.as_dict()
        if a.get('GF') != 4:  # 4 = Landfläche mit Struktur (ohne Gewässerflächen von Nord-/Ostsee, Bodensee)
            continue
        out.append((a, shapely.transform(shape(sr.shape.__geo_interface__), _tr).buffer(0)))
    return out

def rnd(g, nd):
    return shapely.set_precision(g, 10 ** -nd)

def fc(items, nd):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': p, 'geometry': mapping(rnd(g, nd))} for p, g in items]}

lan = read('LAN'); krs = read('KRS')
laender = sorted(((a['GEN'], a['AGS'][:2]), g) for a, g in lan)
kreise = sorted(((a['GEN'], a['BEZ'], a['AGS'], a['AGS'][:2]), g) for a, g in krs)
# genaue Fassung für Punkt-im-Polygon
genau = {'laender': [{'name': n, 'ags': s, 'geom': mapping(rnd(g, 5))} for (n, s), g in laender],
         'kreise': [{'name': n, 'bez': b, 'ags': ags, 'land': s, 'geom': mapping(rnd(g, 5))} for (n, b, ags, s), g in kreise]}
json.dump(genau, open(os.path.expanduser('~/klobasa-de-data/grenzen_genau.json'), 'w', encoding='utf-8'), ensure_ascii=False)
# Karte: deckungsgleich vereinfachen
lg = shapely.coverage_simplify([g for _, g in laender], TOL['LAN'])
kg = shapely.coverage_simplify([g for _, g in kreise], TOL['KRS'])
L = fc([({'name': n, 'ags': s}, g) for ((n, s), _), g in zip(laender, lg)], 3)
K = fc([({'name': n, 'bez': b, 'ags': ags, 'land': s}, g) for ((n, b, ags, s), _), g in zip(kreise, kg)], 3)
os.makedirs(os.path.join(ROOT, 'site', 'kreise'), exist_ok=True)
out = [('laender.json', L)] + [(f'kreise/{s}.json', {'type': 'FeatureCollection', 'features': [f for f in K['features'] if f['properties']['land'] == s]})
                               for (_, s), _ in laender]
for name, obj in out:
    p = os.path.join(ROOT, 'site', name)
    json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(name, len(obj['features']), 'Flächen,', os.path.getsize(p) // 1024, 'KB')
