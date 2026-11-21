#!/usr/bin/env python3
"""Spielstätten verorten und Europlan zuordnen. Bearbeitet nur Spielstätten ohne Zuordnung (jede nur einmal).

  .venv/bin/python scripts/venues.py [--neu-versuchen] [--ohne-nominatim]

Reihenfolge:
  1. Handkorrektur (data/zuordnung_manuell.json)
  2. Europlan über Heimverein und Liga, Gegenprobe: höchstens 15 km vom PLZ-Mittelpunkt
  3. OSM: Fußballplatz bis 300 m von der Straße bzw. mit passendem Namen im PLZ-Gebiet
     (Flächen ab 3.000 m² bevorzugt, damit keine Bolzplätze oder Schulhöfe gewählt werden)
  4. Nominatim (Straße mit Hausnummer, höchstens 4 Anfragen pro Minute, dauerhafter Zwischenspeicher);
     liegt ein Fußballplatz bis 300 m um den Treffer, gilt dieser
  5. Europlan über Entfernung (300 m) für alles mit Koordinaten aus 3 oder 4
  6. Rückfall, als ungefähr markiert: Straßenmitte aus OSM, sonst PLZ-Mittelpunkt
Belag: fussball.de (Text/Platzart) zuerst, sonst OSM-surface.
--neu-versuchen: Spielstätten ohne Europlan-ID noch einmal bearbeiten (z. B. nach neuem Europlan-Abruf).
OSM-Daten © OpenStreetMap-Mitwirkende (ODbL), Nominatim © OpenStreetMap-Mitwirkende.
"""
import argparse, json, os, re, sqlite3, sys, time
from collections import Counter, defaultdict
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import club_key, fold, km, street_key  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.environ.get('KLOBASA_DB') or os.path.join(DATA, 'klobasa.sqlite')
OSM = os.path.join(DATA, 'osm')
EP_PATH = os.path.join(ROOT, 'data', 'europlan_de.json')
MANUAL_PATH = os.path.join(ROOT, 'data', 'zuordnung_manuell.json')
UA = 'Klobasa-Planer-DE/0.1 (privates Projekt; Geokodierung von Sportplaetzen, Zwischenspeicher)'
NOMINATIM_PAUSE = 15.0  # 4 pro Minute
PLZ_RADIUS = 15.0
ORT_RADIUS = 3.0        # km: Ortsregel greift nur, wenn der im Europlan-Namen genannte Ort weiter weg liegt
ORTSREGEL_LOG = os.path.join(DATA, 'logs', 'ortsregel.tsv')
ANLAGE = 0.25           # km: Plätze näher beieinander gelten als eine Anlage
EP_RADIUS = 0.30        # km: Europlan über Entfernung
PITCH_RADIUS = 0.3      # km: Fußballplatz um den Straßen- bzw. Adresspunkt
MIN_FLAECHE = 3000      # m²: kleinere Flächen nur, wenn es nichts Größeres gibt
UNGEFAEHR = ('osm_strasse', 'plz')

ART_RE = re.compile(r'kunstrasen|kunststoff|\bkr\b|\bkrp\b|\bkura\b|artificial', re.I)
STOP = {'sportplatz', 'sportanlage', 'spa', 'spo', 'stadion', 'platz', 'rasen', 'rasenplatz', 'kunstrasen', 'kunstrasenplatz',
        'kr', 'kura', 'sportpark', 'sportzentrum', 'spz', 'sportgelaende', 'anlage', 'am', 'an', 'der', 'die', 'das', 'im',
        'in', 'str', 'strasse', 'weg', 'hauptplatz', 'nebenplatz', 'neu', 'alt', 'und', 'b', 'a', 'sp', 'sportstaette',
        'waldsportplatz', 'rasenpl', 'hartplatz', 'tenne', 'halle', 'sporthalle', 'kleinfeld', 'vereinsgelaende',
        'f', 'fussball', 'fussballplatz', 'arena', 'jahnsportplatz'}

def toks(s):
    return {w for w in re.sub(r'[^a-z0-9]+', ' ', fold(s or '')).split() if len(w) > 2 and w not in STOP and not w.isdigit()}

def is_art(name):
    return bool(ART_RE.search(name or ''))

def osm_belag(surface):
    s = (surface or '').lower()
    if s in ('artificial_turf', 'artificial', 'astroturf', 'synthetic', 'kunstrasen'): return 'kunstrasen'
    if s in ('grass', 'grass_paver', 'natural_turf'): return 'rasen'
    return None

def by_belag(cands, belag):
    """Mehrere Plätze einer Anlage: nach Belag wählen. -> Platz oder None (unentschieden)."""
    if len(cands) == 1:
        return cands[0]
    if belag == 'kunstrasen':
        a = [s for s in cands if is_art(s['name'])]
        if len(a) == 1: return a[0]
    elif belag == 'rasen':
        a = [s for s in cands if not is_art(s['name']) and not re.search(r'nebenplatz|trainingsplatz', s['name'], re.I)]
        if len(a) == 1: return a[0]
        a = [s for s in cands if not is_art(s['name'])]
        if len(a) == 1: return a[0]
    h = [s for s in cands if re.search(r'hauptplatz', s['name'], re.I)]
    return h[0] if len(h) == 1 else None

class Grid:
    def __init__(self, items, lat, lon):
        self.g = defaultdict(list); self.lat, self.lon = lat, lon
        for it in items:
            self.g[(int(lat(it) * 50), int(lon(it) * 30))].append(it)
    def near(self, la, lo, r):
        out = []
        cy, cx = int(la * 50), int(lo * 30)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for it in self.g.get((cy + dy, cx + dx), ()):
                    d = km(la, lo, self.lat(it), self.lon(it))
                    if d <= r: out.append((d, it))
        return sorted(out, key=lambda x: x[0])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--neu-versuchen', action='store_true')
    ap.add_argument('--ohne-nominatim', action='store_true')
    ap.add_argument('--europlan', default=EP_PATH)
    ap.add_argument('--trocken', action='store_true', help='nichts speichern (Test)')
    a = ap.parse_args()
    c = sqlite3.connect(DB)
    for col in ('osm_id TEXT', 'belag_quelle TEXT', 'bearbeitet INTEGER', 'koord_ungefaehr INTEGER'):
        try: c.execute(f'ALTER TABLE spielstaetten ADD COLUMN {col}')
        except sqlite3.OperationalError: pass
    c.execute('CREATE TABLE IF NOT EXISTS nominatim (anfrage TEXT PRIMARY KEY, antwort TEXT, geholt INTEGER)')
    c.execute("UPDATE spielstaetten SET belag_quelle='fussball.de' WHERE belag!='unbekannt' AND belag_quelle IS NULL")

    # ---- Stammdaten
    plz = json.load(open(os.path.join(OSM, 'plz.json')))
    streets = json.load(open(os.path.join(OSM, 'streets.json'), encoding='utf-8'))
    pitches = [dict(zip(('id', 'art', 'name', 'surface', 'lat', 'lon', 'plz', 'sport', 'flaeche'), r))
               for r in json.load(open(os.path.join(OSM, 'pitches.json'), encoding='utf-8'))]
    pgrid = Grid(pitches, lambda p: p['lat'], lambda p: p['lon'])
    named_by_plz = defaultdict(list)
    for p in pitches:
        if p['name'] and p['plz']:
            named_by_plz[p['plz']].append(p)
    EPD = json.load(open(a.europlan, encoding='utf-8'))
    EP = EPD['stadiums']; EP_BY_ID = {s['id']: s for s in EP}
    egrid = Grid(EP, lambda s: s['lat'], lambda s: s['lon'])
    ep_by_club = defaultdict(list)
    for s in EP:
        for cl in s['clubs']:
            if cl.lower().startswith('ehemals'):
                continue
            k = club_key(cl)
            if k and s not in ep_by_club[k]:
                ep_by_club[k].append(s)
    # Europlan-Liga je Staffel über gemeinsame Vereine
    leagues = []
    for L in EPD['leagues']:
        ent = {}
        for cl, sid in L['entries']:
            k = club_key(cl)
            if k and k not in ent: ent[k] = sid
        leagues.append(ent)
    lg_by_key = defaultdict(list)
    for j, ent in enumerate(leagues):
        for k in ent: lg_by_key[k].append(j)
    staffel_keys = defaultdict(set)
    for sid, h, g in c.execute('SELECT staffel_id, heim, gast FROM spiele'):
        staffel_keys[sid].update(k for k in (club_key(h), club_key(g)) if k)
    staffel_league = {}
    for sid, ks in staffel_keys.items():
        cnt = Counter(j for k in ks for j in lg_by_key.get(k, ()))
        if cnt:
            j, ov = cnt.most_common(1)[0]
            if ov >= 3 and ov >= 0.5 * min(len(ks), len(leagues[j])): staffel_league[sid] = j
    manual = {}
    if os.path.exists(MANUAL_PATH):
        from crawl import venue_key
        for e in json.load(open(MANUAL_PATH, encoding='utf-8')).get('eintraege', []):
            manual[venue_key(e['spielstaette'])] = e

    # ---- zu bearbeitende Spielstätten
    cond = 'bearbeitet IS NULL' + (' OR europlan_id IS NULL' if a.neu_versuchen else '')
    venues = [dict(zip(('key', 'raw', 'name', 'strasse', 'plz', 'ort', 'belag', 'platzart'), r)) for r in
              c.execute(f'SELECT schluessel, rohtext, name, strasse, plz, ort, belag, platzart FROM spielstaetten WHERE {cond}')]
    # Ortsregel: Nennt der Europlan-Name einen Ort (Wort, das Ort einer Spielstätte ist, z. B. „Beilrode“), der weder in
    # der Spielstätte steht noch höchstens 3 km entfernt liegt, passt der Platz nicht. Ortsteil und Gemeinde dürfen
    # verschieden heißen; entscheidend ist die Entfernung. Koordinaten der Orte aus OSM (alle Orte dieses Namens),
    # sonst die PLZ-Mittelpunkte der Spielstätten in diesem Ort.
    osm_places = defaultdict(list)
    osm_places_full = json.load(open(os.path.join(OSM, 'places.json'), encoding='utf-8'))
    for nm, pts in osm_places_full.items():
        if len(nm.split()) <= 2:
            for w in nm.split():
                if len(w) >= 4: osm_places[w] += pts
    PLACES = defaultdict(list)
    for o, z in c.execute('SELECT DISTINCT ort, plz FROM spielstaetten WHERE ort IS NOT NULL'):
        for w in toks(re.sub(r'/.*$', '', o)):
            if len(w) >= 4 and z in plz: PLACES[w].append(plz[z])
    rejected = []
    def ort_point(v):
        """OSM-Ortsknoten des Spielstätten-Orts (bei gleichnamigen Orten der nächste zum PLZ-Mittelpunkt, bis 15 km)."""
        pc = plz.get(v['plz'] or '')
        nm = re.sub(r'[^a-z0-9]+', ' ', fold(re.sub(r'/.*$', '', v['ort'] or ''))).strip()
        pts = [] if not nm else osm_places_full.get(nm, [])
        if not pc or not pts: return None
        d, p = min((km(pc[0], pc[1], la, lo), (la, lo)) for la, lo in pts)
        return p if d <= PLZ_RADIUS else None
    def place_words(name, ref):
        """Wörter eines Namens, die einen Ort bezeichnen: Ort einer Spielstätte oder OSM-Ort bis 15 km."""
        out = set()
        for w in toks(name):
            if w in PLACES or (ref and any(km(ref[0], ref[1], la, lo) <= PLZ_RADIUS for la, lo in osm_places.get(w, ()))):
                out.add(w)
        return out
    def same(a, b):  # gleich bis auf Endung („Döbeln“/„Döbelner“)
        return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])
    def other_place(v, s, ref):
        """True = Europlan-Platz passt nicht (und wird protokolliert)."""
        own = toks(v['name']) | toks(v['ort'] or '') | toks(v['strasse'] or '')
        # Regel A: fussball.de-Name nennt einen Ort, Europlan-Name einen anderen -> kein Treffer, egal wie nah
        vp = place_words(v['name'], ref)
        ep = {w for w in place_words(s['name'], ref) if not any(same(w, o) for o in own)}  # „An der Mulde“ = Straße
        if vp and ep and not any(same(a, b) for a in vp for b in ep):
            rejected.append((v['raw'], s['id'], s['name'], 'A: Spielstätte nennt ' + '/'.join(sorted(vp)) +
                             ', Europlan ' + '/'.join(sorted(ep)), ''))
            return True
        # Regel B: Europlan-Name nennt einen Ort, der nicht in der Spielstätte steht und über 3 km entfernt liegt
        for w in toks(s['name']):
            if w in PLACES and not any(same(w, o) for o in own) and ref:
                pts = osm_places.get(w) or PLACES[w]
                d = min(km(ref[0], ref[1], la, lo) for la, lo in pts)
                if d > ORT_RADIUS:
                    rejected.append((v['raw'], s['id'], s['name'], 'B: Europlan nennt ' + w, round(d, 1)))
                    return True
        return False
    homes = defaultdict(list)
    for key, heim, sid in c.execute("SELECT spielstaette, heim, staffel_id FROM spiele WHERE spielstaette_quelle IN ('druck','verein')"):
        homes[key].append((heim, sid))
    print(f'{len(venues)} Spielstätten zu bearbeiten; Europlan {len(EP)} Stadien, {len(staffel_league)} Staffeln mit Europlan-Liga; '
          f'OSM {len(pitches)} Plätze, {len(streets)} Straßen, {len(plz)} PLZ', flush=True)
    st = Counter()

    def set_(v, **kw):
        v.update(kw)

    def ep_club(v, osm_p):
        """Schritt 2: Stimmen aller Heimspiele für ein Europlan-Stadion des Heimvereins.
        Gegenprobe: höchstens 1 km vom OSM-Platz an der Adresse, sonst 3 km vom OSM-Ortsknoten, sonst 2 km von der Straßenmitte, sonst 15 km vom
        PLZ-Mittelpunkt. Fängt gleichnamige Vereine und Plätze von Partnervereinen einer Spielgemeinschaft ab."""
        sp = street_point(v); op = ort_point(v)
        if osm_p: ref, rad = (osm_p['lat'], osm_p['lon']), 1.0
        elif op: ref, rad = op, 3.0
        elif sp: ref, rad = sp, 2.0
        else: ref, rad = plz.get(v['plz'] or ''), PLZ_RADIUS
        if not ref:
            return None, 'keine PLZ'
        pc = ref
        oref = op or ref  # Ortsregel misst ab dem Ort der Spielstätte
        votes = Counter(); ambig = 0
        for team, sid in homes.get(v['key'], []):
            prior = None
            if sid in staffel_league:
                prior = EP_BY_ID.get(leagues[staffel_league[sid]].get(club_key(team)))
            parts = [team] + [p for p in re.split(r'\s*/\s*', re.sub(r'^SpG\.?\s*', '', team)) if p.strip()] if '/' in team else [team]
            cands = {}
            for p in parts:
                for s in ep_by_club.get(club_key(p), []):
                    cands[s['id']] = s
            if prior: cands.setdefault(prior['id'], prior)
            cands = [s for s in cands.values() if km(pc[0], pc[1], s['lat'], s['lon']) <= rad and not other_place(v, s, oref)]
            if not cands:
                continue
            pick = None
            # Name der Spielstätte steht im Stadionnamen („Willy-Tröger-Stadion“)
            vt = toks(v['name'])
            named = [s for s in cands if vt and vt & toks(s['name'])]
            if len(named) == 1: pick = named[0]
            elif len({s['id'] for s in cands}) == 1: pick = cands[0]
            else:
                pool = named or cands
                anchor = prior if prior and prior in pool else pool[0]
                anlage = [s for s in pool if km(anchor['lat'], anchor['lon'], s['lat'], s['lon']) <= ANLAGE]
                if len(anlage) == len(pool) or (prior and prior in pool):
                    pick = by_belag(anlage, v['belag']) or (prior if prior in anlage else None)
            if pick: votes[pick['id']] += 2 if prior and pick['id'] == prior['id'] else 1
            else: ambig += 1
        if not votes:
            return None, 'mehrdeutig' if ambig else 'kein Verein'
        sid, n = votes.most_common(1)[0]
        if n >= 0.6 * sum(votes.values()):
            return EP_BY_ID[sid], None
        return None, 'mehrdeutig'

    def size_pref(ps):
        """Flächen ab 3.000 m² zuerst, dann nur als Punkt erfasste Plätze, zuletzt kleine Flächen."""
        for ok in (lambda p: p['flaeche'] >= MIN_FLAECHE, lambda p: p['flaeche'] == 0, lambda p: True):
            sel = [x for x in ps if ok(x[1] if isinstance(x, tuple) else x)]
            if sel: return sel
        return []

    def pitch_near(v, la, lo):
        vt = toks(v['name'])
        near = size_pref([(d, p) for d, p in pgrid.near(la, lo, PITCH_RADIUS) if p['art'] == 'pitch' or p['name']])
        def score(dp):
            d, p = dp
            sc = -d * 4
            if vt & toks(p['name']): sc += 3
            b = osm_belag(p['surface'])
            if b and v['belag'] != 'unbekannt': sc += 1.5 if b == v['belag'] else -1.5
            if p['art'] != 'pitch': sc -= 0.5
            return sc
        return max(near, key=score)[1] if near else None

    def street_point(v):
        return streets.get(f"{v['plz']}|{street_key(v['strasse'] or '')}") if v['strasse'] and v['plz'] else None

    def osm_locate(v):
        """Schritt 3: Platz bis 300 m von der Straße, sonst benannter Platz im PLZ-Gebiet."""
        sp = street_point(v)
        if sp:
            p = pitch_near(v, sp[0], sp[1])
            if p: return p
        vt = toks(v['name'])
        if vt and v['plz']:
            hits = size_pref([p for p in named_by_plz.get(v['plz'], []) if len(vt & toks(p['name'])) >= max(1, min(2, len(vt)))])
            if hits:
                # mehrere Plätze derselben Anlage: Belag entscheidet, sonst der erste
                if v['belag'] != 'unbekannt':
                    hits = [p for p in hits if osm_belag(p['surface']) == v['belag']] or hits
                h = hits[0]
                if all(km(h['lat'], h['lon'], p['lat'], p['lon']) <= 1 for p in hits):
                    return h
        return None

    def ep_near(v):
        """Schritt 5: Europlan-Platz bis 300 m um die Koordinaten, mehrere: Belag, sonst der nächste eindeutige."""
        near = [(d, s) for d, s in egrid.near(v['lat'], v['lon'], EP_RADIUS)
                if not other_place(v, s, ort_point(v) or (v['lat'], v['lon']))]
        if not near: return None
        cands = [s for _, s in near]
        pick = by_belag(cands, v['belag']) if len(cands) > 1 else cands[0]
        if not pick and (len(near) == 1 or near[1][0] - near[0][0] > 0.15): pick = near[0][1]
        return pick

    last_nom = [0.0]
    sess = requests.Session(); sess.headers['User-Agent'] = UA
    def nominatim(v):
        q = {'street': re.sub(r'\s+', ' ', v['strasse'] or ''), 'postalcode': v['plz'] or '', 'city': v['ort'] or '',
             'country': 'de', 'format': 'jsonv2', 'limit': '1'}
        if not q['street'] and not q['postalcode']:
            return None
        k = json.dumps(q, sort_keys=True, ensure_ascii=False)
        r = c.execute('SELECT antwort FROM nominatim WHERE anfrage=?', (k,)).fetchone()
        if r:
            res = json.loads(r[0])
        else:
            if a.ohne_nominatim:
                return None
            wait = last_nom[0] + NOMINATIM_PAUSE - time.time()
            if wait > 0: time.sleep(wait)
            last_nom[0] = time.time()
            resp = sess.get('https://nominatim.openstreetmap.org/search', params=q, timeout=60)
            if resp.status_code in (403, 429):
                raise SystemExit(f'Nominatim Status {resp.status_code}: Abbruch')
            if resp.status_code != 200:
                return None
            res = resp.json()
            c.execute('INSERT OR REPLACE INTO nominatim VALUES (?,?,?)', (k, json.dumps(res), int(time.time()))); c.commit()
            st['Nominatim-Abrufe'] += 1
        if not res: return None
        la, lo = float(res[0]['lat']), float(res[0]['lon'])
        pc = plz.get(v['plz'] or '')
        if pc and km(la, lo, pc[0], pc[1]) > PLZ_RADIUS:
            return None
        return la, lo

    for i, v in enumerate(venues, 1):
        v.update(lat=None, lon=None, kh=None, ep=None, zh=None, osm=None, ob=None)
        man = manual.get(v['key'])
        if man:  # 1. Handkorrektur
            if man.get('europlan') in EP_BY_ID:
                s = EP_BY_ID[man['europlan']]; set_(v, ep=s['id'], lat=s['lat'], lon=s['lon'], kh='europlan', zh='manuell')
            if 'lat' in man:
                set_(v, lat=man['lat'], lon=man['lon'], kh='manuell', zh=v['zh'] or 'manuell')
            st['1 Handkorrektur'] += 1
        o = osm_locate(v)
        if v['ep'] is None and not man:  # 2. Europlan über Verein und Liga
            s, why = ep_club(v, o)
            if s:
                set_(v, ep=s['id'], lat=s['lat'], lon=s['lon'], kh='europlan', zh='verein_liga'); st['2 Europlan Verein/Liga'] += 1
            else:
                st['2 nicht: ' + why] += 1
        # 3. OSM-Platz (auch nach Europlan-Treffer, dann nur für den Belag)
        if o:
            v['osm'] = o['id']; v['ob'] = osm_belag(o['surface'])
            if v['lat'] is None:
                set_(v, lat=o['lat'], lon=o['lon'], kh='osm_platz'); st['3 OSM-Platz'] += 1
        # 4. Nominatim, danach Platz um den Adresspunkt
        if v['lat'] is None:
            n = nominatim(v)
            if n:
                p = pitch_near(v, n[0], n[1])
                if p:
                    set_(v, lat=p['lat'], lon=p['lon'], kh='osm_platz', osm=p['id'], ob=osm_belag(p['surface']))
                    st['4 Nominatim + OSM-Platz'] += 1
                else:
                    set_(v, lat=n[0], lon=n[1], kh='nominatim'); st['4 Nominatim'] += 1
        # 5. Europlan über Entfernung
        if v['ep'] is None and v['lat'] is not None and v['kh'] in ('osm_platz', 'nominatim', 'manuell'):
            s = ep_near(v)
            if s:
                set_(v, ep=s['id'], zh='entfernung'); st['5 Europlan Entfernung'] += 1
                if v['kh'] != 'manuell': set_(v, lat=s['lat'], lon=s['lon'], kh='europlan')
        # 6. Rückfall (ungefähr): Straßenmitte, sonst PLZ-Mittelpunkt
        if v['lat'] is None:
            sp = street_point(v)
            if sp:
                set_(v, lat=sp[0], lon=sp[1], kh='osm_strasse'); st['6 Straßenmitte (ungefähr)'] += 1
            elif plz.get(v['plz'] or ''):
                pc = plz[v['plz']]; set_(v, lat=pc[0], lon=pc[1], kh='plz'); st['6 PLZ-Mittelpunkt (ungefähr)'] += 1
        if v['lat'] is None:
            st['ohne Koordinaten'] += 1
        belag_upd = v['belag'] == 'unbekannt' and v['ob']
        c.execute('''UPDATE spielstaetten SET lat=?, lon=?, koord_herkunft=?, koord_ungefaehr=?, europlan_id=?,
                     zuordnung_herkunft=?, osm_id=?, bearbeitet=?, belag=CASE WHEN ? THEN ? ELSE belag END,
                     belag_quelle=CASE WHEN ? THEN 'osm' ELSE belag_quelle END WHERE schluessel=?''',
                  (v['lat'], v['lon'], v['kh'], int(v['kh'] in UNGEFAEHR), v['ep'], v['zh'] or 'keine', v['osm'], int(time.time()),
                   bool(belag_upd), v['ob'], bool(belag_upd), v['key']))
        if belag_upd: st['Belag aus OSM'] += 1
        if i % 100 == 0:
            if not a.trocken: c.commit()
            print(f'  {i}/{len(venues)}', dict(st), flush=True)
    if a.trocken:
        c.rollback()
    else:
        c.commit()
    seen = set(); rej = [r for r in rejected if not (r[:2] in seen or seen.add(r[:2]))]
    with open(ORTSREGEL_LOG, 'w', encoding='utf-8') as f:
        f.write('spielstaette\teuroplan_id\teuroplan_name\tgrund\tkm\n')
        for r in rej: f.write('\t'.join(map(str, r)) + '\n')
    st['Ortsregel verworfen'] = len(rej)
    print('fertig:', dict(sorted(st.items())), flush=True)

if __name__ == '__main__':
    main()
