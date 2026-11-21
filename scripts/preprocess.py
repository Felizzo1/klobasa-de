#!/usr/bin/env python3
"""Aufbereitung für die Seite: SQLite -> Datenpakete je Bundesland („nächste 6 Wochen“ und „Rest der Saison“) + index.

  .venv/bin/python scripts/preprocess.py [--out DIR] [--klartext]

Ausgabe in DIR (Standard ~/klobasa-de-data/build/daten):
  index.enc / index.json             Stand, Länder, Kreise, Paketliste mit Größen
  <AGS>-6w.enc, <AGS>-rest.enc       je Bundesland (AGS = Landesschlüssel, Sachsen 14); Inhalt gzip-JSON, AES-GCM (fczcrypt)
Verschlüsselt wird mit KLOBASA_PASSWORT (aus ~/.config/klobasa-de/env). --klartext schreibt zusätzlich .json (nur zum Testen
lokal, nie veröffentlichen).
Zuordnung Spiel -> Land/Kreis über den Spielort (BKG VG250, ~/klobasa-de-data/grenzen_genau.json aus grenzen.py),
ohne Spielort über den Landesverband (MANDANT_LAND).

Paketformat (Arrays, damit es klein bleibt):
  comps   [name, kat (0 Herren, 1 Frauen), ebene (0 = unbekannt), staffel_id, spielklasse]
  teams   [name]
  places  [name, lat, lon, kreis, land, ep_href, ep_name, ungefähr (0/1), belag (1 Rasen, 2 Kunstrasen, 4 Hartplatz, 0 unbekannt),
           adresse, anlage_href, anlage_name, platzart]
  matches [comp, 'JJJJ-MM-TTTHH:MM', heim, gast, status (0 normal, 1 abgesetzt, 2 ausgefallen, 3 Nichtantritt), place (-1),
           spiel_id, ort_geschätzt (0/1), verlegt_vom]
"""
import argparse, gzip, json, os, sqlite3, sys, time
from datetime import date, datetime, timedelta
import shapely
from shapely.geometry import shape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fczcrypt import encrypt  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.environ.get('KLOBASA_DB') or os.path.join(DATA, 'klobasa.sqlite')
NAH_TAGE = 42
MANDANT_LAND = {'63': '14'}  # Landesverband -> Bundesland (AGS), für Spiele ohne Spielort; wird mit Phase 5 vervollständigt
STATUS = {'normal': 0, 'abgesetzt': 1, 'ausgefallen': 2, 'nichtantritt': 3}
BELAG = {'rasen': 1, 'kunstrasen': 2}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(DATA, 'build', 'daten'))
    ap.add_argument('--klartext', action='store_true')
    a = ap.parse_args()
    pw = os.environ.get('KLOBASA_PASSWORT')
    if not pw and not a.klartext:
        raise SystemExit('KLOBASA_PASSWORT fehlt (oder --klartext zum lokalen Testen)')
    os.makedirs(a.out, exist_ok=True)
    c = sqlite3.connect(DB)
    t0 = time.time()

    # ---- Grenzen
    G = json.load(open(os.path.join(DATA, 'grenzen_genau.json'), encoding='utf-8'))
    laender = [(l['name'], l['ags']) for l in G['laender']]
    land_idx = {ags: i for i, (_, ags) in enumerate(laender)}
    names = [k['name'] for k in G['kreise']]
    kreise = []
    for k in G['kreise']:
        nm = k['name']
        if names.count(nm) > 1:  # Leipzig, Landkreis Leipzig usw.
            nm += ' (Stadt)' if 'Stadt' in k['bez'] else ' (Landkreis)' if 'kreis' in k['bez'].lower() else f" ({k['bez']})"
        kreise.append([nm, land_idx[k['land']], k['ags']])
    tree = shapely.STRtree([shape(k['geom']) for k in G['kreise']])

    # ---- Ligaebenen
    lv = {}
    for e in json.load(open(os.path.join(ROOT, 'data', 'ligaebenen.json'), encoding='utf-8'))['eintraege']:
        lv[(e['mandant'], e['art'], e['gebiet'], e['spielklasse'])] = e['stufe'] or 0

    # ---- Europlan
    ep = {s['id']: s for s in json.load(open(os.path.join(ROOT, 'data', 'europlan_de.json'), encoding='utf-8'))['stadiums']}

    # ---- Spielstätten
    venues = {}
    rows = c.execute('''SELECT schluessel, name, strasse, plz, ort, belag, platzart, lat, lon, koord_ungefaehr, europlan_id,
                        europlan_anlage_id FROM spielstaetten''').fetchall()
    pts = [(r[8], r[7]) for r in rows if r[7] is not None]
    hit = {}
    if pts:
        ip, ig = tree.query(shapely.points([p[0] for p in pts], [p[1] for p in pts]), predicate='within')
        for i, j in zip(ip, ig): hit.setdefault(int(i), int(j))
    k = 0
    for r in rows:
        key, name, strasse, plz, ort, belag, platzart, lat, lon, ung, epid, anl = r
        ki = -1
        if lat is not None:
            ki = hit.get(k, -1); k += 1
        b = 4 if (platzart or '').lower() == 'hartplatz' else BELAG.get(belag or '', 0)
        e = ep.get(epid); an = ep.get(anl)
        venues[key] = [name or '', round(lat, 5) if lat is not None else None, round(lon, 5) if lon is not None else None,
                       ki, kreise[ki][1] if ki >= 0 else -1, e['href'] if e else '', e['name'] if e else '', int(bool(ung)),
                       b, ', '.join(x for x in (strasse, ' '.join(y for y in (plz, ort) if y)) if x),
                       an['href'] if an else '', an['name'] if an else '', platzart or '']

    # ---- Spiele
    today = date.today()
    nah = (today + timedelta(days=NAH_TAGE)).isoformat()
    rows = c.execute('''SELECT p.spiel_id, p.datum, p.zeit, p.heim, p.gast, p.status, p.spielstaette, p.spielstaette_quelle,
                               p.verlegt_vom, s.staffel_id, s.name, s.art, s.mandant, s.gebiet, s.spielklasse
                        FROM spiele p JOIN staffeln s ON s.staffel_id=p.staffel_id
                        WHERE s.im_menue=1 AND p.datum >= ? AND p.status IN ('normal','abgesetzt','ausgefallen','nichtantritt')
                        ORDER BY p.datum, p.zeit, s.name''', (today.isoformat(),)).fetchall()
    packs = {}
    stats = {'spiele': 0, 'ohne_ort': 0, 'ohne_ebene': set()}
    for (sid, dat, zeit, heim, gast, status, vk, vq, verlegt, stid, stname, art, mand, geb, sk) in rows:
        v = venues.get(vk) if vk else None
        land = laender[v[4]][1] if v and v[4] >= 0 else MANDANT_LAND.get(mand)
        if not land:
            continue
        teil = '6w' if dat <= nah else 'rest'
        P = packs.setdefault((land, teil), {'comps': [], 'teams': [], 'places': [], 'matches': [], '_c': {}, '_t': {}, '_p': {}})
        def idx(kind, key, val):
            m = P['_' + kind[0]]
            if key not in m:
                m[key] = len(P[kind]); P[kind].append(val)
            return m[key]
        stufe = lv.get((mand, art, geb, sk), 0)
        if not stufe: stats['ohne_ebene'].add((mand, art, geb, sk))
        ci = idx('comps', stid, [stname, 0 if art == 'Herren' else 1, stufe, stid, sk])
        pi = idx('places', vk, v) if v and v[1] is not None else -1
        if pi < 0: stats['ohne_ort'] += 1
        P['matches'].append([ci, dat + 'T' + (zeit or ''), idx('teams', heim, heim), idx('teams', gast, gast), STATUS[status], pi,
                             sid, int(vq == 'geschaetzt'), verlegt or ''])
        stats['spiele'] += 1

    # ---- schreiben
    run = c.execute('SELECT start, ende, abrufe, fehler, abbruch FROM laeufe WHERE ende IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    saison = c.execute("SELECT saison FROM staffeln WHERE im_menue=1 LIMIT 1").fetchone()[0]
    def write(name, obj):
        raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        gz = gzip.compress(raw, 9, mtime=0)
        n = 0
        if pw:
            blob = encrypt(gz, pw); open(os.path.join(a.out, name + '.enc'), 'wb').write(blob); n = len(blob)
        if a.klartext:
            open(os.path.join(a.out, name + '.json'), 'wb').write(raw)
        return n or len(gz), len(raw)
    plist = []
    for (land, teil), P in sorted(packs.items()):
        for kk in ('_c', '_t', '_p'): P.pop(kk)
        name = f'{land}-{teil}'
        enc, raw = write(name, P)
        ds = [m[1][:10] for m in P['matches']]
        plist.append({'land': land, 'teil': teil, 'datei': name, 'bytes': enc, 'roh': raw, 'spiele': len(P['matches']),
                      'von': min(ds), 'bis': max(ds)})
    index = {'generated': datetime.now().astimezone().isoformat(timespec='seconds'), 'saison': saison, 'nah_bis': nah,
             'laender': [list(x) for x in laender], 'kreise': kreise, 'pakete': plist,
             'lauf': {'ende': run[1], 'abrufe': run[2], 'fehler': run[3], 'abbruch': run[4]} if run else None,
             'spiele': stats['spiele']}
    write('index', index)
    for p in plist:
        print(f"{p['datei']:10} {p['spiele']:6} Spiele  {p['bytes'] / 1024:7.0f} KB gzip  ({p['roh'] / 1024:.0f} KB roh)  {p['von']}–{p['bis']}")
    print(f"{stats['spiele']} Spiele, {stats['ohne_ort']} ohne Spielort, {len(stats['ohne_ebene'])} Spielklassen ohne Ebene, "
          f"{time.time() - t0:.1f} s -> {a.out}")

if __name__ == '__main__':
    main()
