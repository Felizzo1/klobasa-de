#!/usr/bin/env python3
"""Spielstätten verorten und Europlan zuordnen. Bearbeitet nur Spielstätten ohne Zuordnung (jede nur einmal).

  .venv/bin/python scripts/venues.py [--neu-versuchen] [--ohne-nominatim]

Reihenfolge:
  1. Handkorrektur (~/klobasa-de-data/zuordnung_manuell.json)
  2. Europlan über Heimverein und Liga, Gegenprobe: höchstens 15 km vom PLZ-Mittelpunkt
  3. OSM: Fußballplatz bis 300 m von der Straße bzw. mit passendem Namen im PLZ-Gebiet
     (Flächen ab 3.000 m² bevorzugt, damit keine Bolzplätze oder Schulhöfe gewählt werden)
  4. Nominatim (Straße mit Hausnummer, höchstens 4 Anfragen pro Minute, dauerhafter Zwischenspeicher);
     liegt ein Fußballplatz bis 300 m um den Treffer, gilt dieser
  5. Europlan über Entfernung (300 m) für alles mit Koordinaten aus 3 oder 4
  6. Rückfall, als ungefähr markiert: Straßenmitte aus OSM, sonst PLZ-Mittelpunkt
  7. Kein eigener Europlan-Platz, aber einer derselben Anlage (unter 250 m, genaue Koordinaten, Ortsregel):
     europlan_anlage_id, auf der Seite als eigener Link „Europlan (Anlage)“, getrennt vom Platz-Link
Ortsregel: Nennt die Spielstätte einen Ort und Europlan einen anderen, ist es kein Treffer (A); nennt nur Europlan einen
  Ort über 3 km entfernt, auch nicht (B). Kein Widerspruch, wenn beide Orte in derselben Gemeinde (OSM admin_level=8) liegen.
  Wendungen mit „An der“, „An den“, „Am“, „Zum“, „Zur“ (z. B. „An der alten Ziegelei“) zählen nie als Ortsname.
  Verworfene Treffer: ~/klobasa-de-data/logs/ortsregel_<Zeitstempel>.tsv (je Lauf eine neue Datei)
Belag: fussball.de (Text/Platzart) zuerst, sonst OSM-surface.
--neu-versuchen: Spielstätten ohne Europlan-ID noch einmal bearbeiten (z. B. nach neuem Europlan-Abruf).
OSM-Daten © OpenStreetMap-Mitwirkende (ODbL), Nominatim © OpenStreetMap-Mitwirkende.
"""
import argparse, json, os, re, sqlite3, sys, time
from collections import Counter, defaultdict
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import club_key, fold, km, street_key  # noqa: E402
from sperren import vorrang_aktiv  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.environ.get('KLOBASA_DB') or os.path.join(DATA, 'klobasa.sqlite')
OSM = os.environ.get('OSM_DIR') or os.path.join(DATA, 'osm')
# Rohdaten aus Europlan/fussball.de nie ins (öffentliche) Repo: beide liegen in ~/klobasa-de-data
EP_PATH = os.path.join(DATA, 'europlan', 'europlan_de.json')
MANUAL_PATH = os.path.join(DATA, 'zuordnung_manuell.json')
UA = 'Klobasa-Planer-DE/0.1 (privates Projekt; Geokodierung von Sportplaetzen, Zwischenspeicher)'
NOMINATIM_PAUSE = 15.0  # 4 pro Minute
PLZ_RADIUS = 15.0
ORT_PLZ_RADIUS = 25.0  # km: Ort der Adresse um den PLZ-Mittelpunkt
ORT_RADIUS = 3.0        # km: Ortsregel greift nur, wenn der im Europlan-Namen genannte Ort weiter weg liegt
ORTSREGEL_LOG = os.path.join(DATA, 'logs', time.strftime('ortsregel_%Y%m%d-%H%M%S.tsv'))
ANLAGE = 0.25           # km: Plätze näher beieinander gelten als eine Anlage
EP_RADIUS = 0.30        # km: Europlan über Entfernung
PITCH_RADIUS = 0.3      # km: Fußballplatz um den Straßen- bzw. Adresspunkt
MIN_FLAECHE = 3000      # m²: kleinere Flächen nur, wenn es nichts Größeres gibt
UNGEFAEHR = ('osm_strasse', 'osm_ortsteil', 'plz')
ORTSTEIL_RADIUS = 1.5  # km: Fußballplatz um den Ortsknoten eines im Namen genannten Ortsteils

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

PLATZ_NR = re.compile(r'(?i)(?<![a-zäöüß])(?:platz|pl\.|spielfeld|feld|kunstrasenplatz|rasenplatz)\s*(?:nr\.?\s*)?(\d{1,2})(?!\d)')

def platz_nr(name):
    m = PLATZ_NR.search(name or '')
    return m.group(1) if m else None

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
    ap.add_argument('--nur', help='nur Spielstätten, deren Schlüssel diesen Text enthält (Diagnose, gibt Zwischenschritte aus)')
    ap.add_argument('--alle', action='store_true', help='alle Spielstätten neu bearbeiten (z. B. nach neuem OSM-Straßenindex)')
    ap.add_argument('--ohne-nominatim', action='store_true')
    ap.add_argument('--europlan', default=EP_PATH)
    ap.add_argument('--trocken', action='store_true', help='nichts speichern (Test)')
    ap.add_argument('--ende', help='Zeitgrenze HH:MM: danach keine weiteren Spielstätten, Rest im nächsten Lauf (Code 3)')
    ap.add_argument('--nachgeben', action='store_true', help='Vorrang des täglichen Laufs beachten: anhalten, Code 4')
    a = ap.parse_args()
    ende = None
    if a.ende:
        from datetime import datetime, timedelta
        h, m = map(int, a.ende.split(':')); d = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        ende = (d + timedelta(days=1) if d <= datetime.now() else d).timestamp()
    c = sqlite3.connect(DB)
    for col in ('osm_id TEXT', 'belag_quelle TEXT', 'bearbeitet INTEGER', 'koord_ungefaehr INTEGER', 'europlan_anlage_id INTEGER'):
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

    def gleiche_nummer(v, s):
        """Nennt die Spielstätte eine Platznummer („Sportpark Ostra Platz 21“), gilt der Europlan-Platz derselben Anlage (250 m)
        mit dieser Nummer – auch wenn Verein/Liga auf einen anderen Platz zeigten (Dresdner SC II: Platz 11 statt 21)."""
        n = platz_nr(v['name'])
        if not n or not s or platz_nr(s['name']) == n:
            return s
        gleich = [x for _, x in egrid.near(s['lat'], s['lon'], ANLAGE) if platz_nr(x['name']) == n]
        return gleich[0] if len(gleich) == 1 else s
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
    cond = '1=1' if a.alle or a.nur else 'bearbeitet IS NULL' + (' OR europlan_id IS NULL' if a.neu_versuchen else '')
    venues = [dict(zip(('key', 'raw', 'name', 'strasse', 'plz', 'ort', 'belag', 'platzart'), r)) for r in
              c.execute(f'SELECT schluessel, rohtext, name, strasse, plz, ort, belag, platzart FROM spielstaetten WHERE {cond}')]
    if a.nur:
        venues = [v for v in venues if a.nur.lower() in v['key']]
    # Ortsregel: Nennt der Europlan-Name einen Ort (Wort, das Ort einer Spielstätte ist, z. B. „Beilrode“), der weder in
    # der Spielstätte steht noch höchstens 3 km entfernt liegt, passt der Platz nicht. Ortsteil und Gemeinde dürfen
    # verschieden heißen; entscheidend ist die Entfernung. Koordinaten der Orte aus OSM (alle Orte dieses Namens),
    # sonst die PLZ-Mittelpunkte der Spielstätten in diesem Ort.
    osm_places = defaultdict(list); osm_places_gem = defaultdict(list)
    osm_places_full = json.load(open(os.path.join(OSM, 'places.json'), encoding='utf-8'))
    osm_places_by_first = defaultdict(list)
    for k in osm_places_full: osm_places_by_first[k.split()[0] if k else ''].append(k)
    gj = json.load(open(os.path.join(OSM, 'gemeinden.json'), encoding='utf-8'))
    GEM = gj['gem']; gem_by_word = defaultdict(list)
    for i, (gn, la, lo) in enumerate(GEM):
        for w in gn.split():
            if len(w) >= 4: gem_by_word[w].append((i, la, lo))
    for nm, pts in osm_places_full.items():
        if len(nm.split()) <= 2:
            gids = gj['orte'].get(nm) or [-1] * len(pts)
            for w in nm.split():
                if len(w) >= 4: osm_places[w] += pts; osm_places_gem[w] += gids
    def gemeinden_of(w, ref):
        """Gemeinde des nächsten Orts dieses Namens (bis 15 km um ref) und die nächste Gemeinde dieses Namens (bis 20 km)."""
        if not ref: return set()
        out = set()
        c1 = [(km(ref[0], ref[1], la, lo), g) for (la, lo), g in zip(osm_places.get(w, ()), osm_places_gem.get(w, ()))]
        c1 = [x for x in c1 if x[0] <= PLZ_RADIUS]
        if c1 and min(c1)[1] >= 0: out.add(min(c1)[1])
        c2 = [(km(ref[0], ref[1], la, lo), i) for i, la, lo in gem_by_word.get(w, ())]
        c2 = [x for x in c2 if x[0] <= 20]
        if c2: out.add(min(c2)[1])
        return out
    def same_gemeinde(ws1, ws2, ref1, ref2):
        """ws1: Ortswörter der Spielstätte (bezogen auf ref1), ws2: Ortswörter von Europlan (bezogen auf den Europlan-Platz)."""
        g1 = set().union(*(gemeinden_of(w, ref1) for w in ws1)) if ws1 else set()
        return bool(g1) and any(g1 & gemeinden_of(w, ref2) for w in ws2)
    # Wendungen wie „An der alten Ziegelei“, „Am Wasserturm“, „Zur Linde“: nie Ortsname
    PHRASE = re.compile(r'\b(?i:an\s+der|an\s+den|an\s+dem|am|zum|zur)\s+(?:[a-zäöüß][\w.-]*\s+)*[\w.-]+')
    def no_phrase(name):
        return PHRASE.sub(' ', name or '')
    PLACES = defaultdict(list)
    for o, z in c.execute('SELECT DISTINCT ort, plz FROM spielstaetten WHERE ort IS NOT NULL'):
        for w in toks(re.sub(r'/.*$', '', o)):
            if len(w) >= 4 and z in plz: PLACES[w].append(plz[z])
    rejected = []
    def ort_point(v, weit=False):
        """OSM-Ortsknoten des Spielstätten-Orts (bei gleichnamigen Orten der nächste zum PLZ-Mittelpunkt, bis 15 km).
        weit=True (nur für die Wahl unter gleichnamigen Straßen): bis 25 km – in großen ländlichen PLZ-Gebieten liegt der
        Mittelpunkt oft 15–20 km vom Ort –, dazu Namen mit Zusatz („Mühlberg/Elbe“) und Teile von Doppelnamen („Gülitz-Reetz“)."""
        pc = plz.get(v['plz'] or '')
        nm = re.sub(r'[^a-z0-9]+', ' ', fold(re.sub(r'/.*$', '', v['ort'] or ''))).strip()
        pts = [] if not nm else osm_places_full.get(nm, [])
        if weit and nm and not pts:
            pts = [p for k in osm_places_by_first.get(nm.split()[0], ()) if k.startswith(nm + ' ') for p in osm_places_full[k]]
        if weit and nm and not pts:
            pts = [p for w in nm.split() if len(w) >= 4 for p in osm_places_full.get(w, [])]
        if not pc or not pts: return None
        d, p = min((km(pc[0], pc[1], la, lo), (la, lo)) for la, lo in pts)
        return p if d <= (ORT_PLZ_RADIUS if weit else PLZ_RADIUS) else None
    def place_words(name, ref):
        """Wörter eines Namens, die einen Ort bezeichnen: Ort einer Spielstätte oder OSM-Ort bis 15 km."""
        out = set()
        for w in toks(no_phrase(name)):
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
        # Nennt der Europlan-Name den Ort der Spielstätte auch selbst (zweisprachig: „Nebelschütz / Njebjelčicy“), greift A nicht
        if vp and ep and not any(same(a, b) for a in vp for b in ep | toks(s['name'])):
            if same_gemeinde(vp, ep, ref, (s['lat'], s['lon'])):  # Ortsteil und Gemeinde (z. B. Mühlbach in Müglitztal): kein Widerspruch
                return False
            rejected.append((v['raw'], s['id'], s['name'], 'A: Spielstätte nennt ' + '/'.join(sorted(vp)) +
                             ', Europlan ' + '/'.join(sorted(ep)), ''))
            return True
        # Regel B: Europlan-Name nennt einen Ort, der nicht in der Spielstätte steht und über 3 km entfernt liegt
        for w in toks(no_phrase(s['name'])):
            if w in PLACES and not any(same(w, o) for o in own) and ref:
                pts = osm_places.get(w) or PLACES[w]
                d = min(km(ref[0], ref[1], la, lo) for la, lo in pts)
                if d > ORT_RADIUS and not same_gemeinde(toks(v['ort'] or '') | toks(no_phrase(v['name'])), {w}, ref, (s['lat'], s['lon'])):
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
        if osm_p and not (v.get('strasse_unsicher') and op): ref, rad = (osm_p['lat'], osm_p['lon']), 1.0
        elif sp and name_orte(v) and not v.get('strasse_unsicher'): ref, rad = sp, 2.0   # Ortsteil: Straße statt Gemeindemitte
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
            # Straße als Bezug: bei mehreren gleichnamigen Teilen zählt der nächste Teil
            refs = street_parts(v) if (ref is sp and v.get('strasse_unsicher')) else [pc]
            alle = list(cands.values())
            cands = [s for s in alle if min(km(r[0], r[1], s['lat'], s['lon']) for r in refs) <= rad
                     and not other_place(v, s, oref)]
            if not cands:
                # Rückfall: Platz des Heimvereins mit passendem Namen („Sportplatz Auerswalde“), eindeutig, bis 15 km
                # um Ort bzw. PLZ-Mitte (Gegenprobe wie im Bauplan) – wenn die Straße die Lage nicht eindeutig angibt
                vt = toks(v['name']); weit = [r for r in (op, plz.get(v['plz'] or '')) if r]
                cands = [s for s in alle if vt & toks(s['name']) and weit
                         and min(km(r[0], r[1], s['lat'], s['lon']) for r in weit) <= PLZ_RADIUS and not other_place(v, s, oref)]
                if len({s['id'] for s in cands}) != 1:
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

    def name_orte(v):
        """Ortsknoten zu Ortsnamen im Namen der Spielstätte, die nicht der Ort der Adresse sind
        („Waldsportplatz Zabeltitz“ bei „01561 Großenhain“), bis 15 km um den Ort der Adresse oder den PLZ-Mittelpunkt;
        je Name der nächste."""
        if 'name_orte' in v: return v['name_orte']
        pc = plz.get(v['plz'] or ''); out = []
        if pc:
            own = toks(re.sub(r'/.*$', '', v['ort'] or ''))
            ws = re.sub(r'[^a-z0-9]+', ' ', fold(no_phrase(v['name']))).split()
            zwei = {f'{a} {b}' for a, b in zip(ws, ws[1:]) if f'{a} {b}' in osm_places_full}   # „Groß Kölzig“
            for w in sorted(zwei) + sorted(toks(no_phrase(v['name'])) - {x for z in zwei for x in z.split()}):
                if len(w) < 4 or any(same(w, o) for o in own) or w in own: continue
                # nur vollständige Ortsnamen („zabeltitz“), nicht Wortteile wie „neuer“ aus „Neuer Anbau“
                refs = [r for r in (ort_point(v, weit=True), pc) if r]   # große PLZ-Gebiete: vom Ort der Adresse aus messen
                pts = [(min(km(r[0], r[1], la, lo) for r in refs), (la, lo)) for la, lo in osm_places_full.get(w, ())]
                pts = [x for x in pts if x[0] <= PLZ_RADIUS]
                # nur Ortsteile derselben Gemeinde (Zabeltitz gehört zu Großenhain; Lübeck nicht zu Schönberg)
                # nur Ortsteile derselben Gemeinde (Zabeltitz gehört zu Großenhain; Lübeck nicht zu Schönberg); ohne
                # auffindbaren Ort der Adresse (Amtsname wie „Neiße-Malxetal“) genügt die Nähe zur PLZ
                if pts and (same_gemeinde({w}, own, refs[0], refs[0]) if own and ort_point(v, weit=True) else True):
                    out.append(min(pts)[1])
        v['name_orte'] = out
        return out

    def street_parts(v):
        """Teile der Straße im PLZ-Gebiet, sortiert nach Nähe zum im Namen genannten Ortsteil, sonst zum Ort der Adresse,
        sonst zum PLZ-Mittelpunkt. v['strasse_unsicher']: mehrere Teile und kein Ortsteil im Namen (Wahl nur geschätzt)."""
        val = streets.get(f"{v['plz']}|{street_key(v['strasse'] or '')}") if v['strasse'] and v['plz'] else None
        if not val: return []
        teile = [t[:2] for t in (val if isinstance(val[0], list) else [val])]
        hints = name_orte(v) or [p for p in [ort_point(v, weit=True), plz.get(v['plz'] or '')] if p][:1]
        v['strasse_unsicher'] = len(teile) > 1 and not name_orte(v)
        if len(teile) > 1 and hints:
            teile.sort(key=lambda t: min(km(t[0], t[1], h[0], h[1]) for h in hints))
        return teile

    def street_point(v):
        t = street_parts(v)
        return t[0] if t else None

    def osm_locate(v):
        """Schritt 3: Platz bis 300 m von der Straße, sonst benannter Platz im PLZ-Gebiet."""
        vt = toks(v['name'])
        teile = street_parts(v)
        if len(teile) > 1:  # gleichnamige Straßenteile: Platz mit passendem Namen an irgendeinem Teil vor dem nächstgelegenen
            ps = [p for p in (pitch_near(v, t[0], t[1]) for t in teile) if p]
            named = [p for p in ps if vt & toks(p['name'])]
            if named: return named[0]
            p0 = pitch_near(v, teile[0][0], teile[0][1])   # nur der gewählte (nächste) Teil, nie ein Platz an einem anderen
            if p0: return p0
        elif teile:
            p = pitch_near(v, teile[0][0], teile[0][1])
            if p: return p
        if vt and v['plz']:
            hits = size_pref([p for p in named_by_plz.get(v['plz'], []) if len(vt & toks(p['name'])) >= max(1, min(2, len(vt)))])
            if hits:
                # mehrere Plätze derselben Anlage: Belag entscheidet, sonst der erste
                if v['belag'] != 'unbekannt':
                    hits = [p for p in hits if osm_belag(p['surface']) == v['belag']] or hits
                h = hits[0]
                if all(km(h['lat'], h['lon'], p['lat'], p['lon']) <= 1 for p in hits):
                    return h
        # Ortsteil im Namen („Waldsportplatz Zabeltitz“): Fußballplatz im Umkreis seines Ortsknotens
        for la, lo in name_orte(v):
            near = size_pref([(d, p) for d, p in pgrid.near(la, lo, ORTSTEIL_RADIUS) if p['art'] == 'pitch' or p['name']])
            if near:
                named = [x for x in near if vt & toks(x[1]['name'])]
                pool = named or near
                if v['belag'] != 'unbekannt':
                    pool = [x for x in pool if osm_belag(x[1]['surface']) == v['belag']] or pool
                d, p = min(pool, key=lambda x: x[0])
                # nicht weit weg von der (eindeutigen) Straße der Adresse
                if teile and not v.get('strasse_unsicher') and km(p['lat'], p['lon'], teile[0][0], teile[0][1]) > 1.0:
                    continue
                if named or all(km(p['lat'], p['lon'], q['lat'], q['lon']) <= 0.5 for _, q in pool):
                    return p
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

    zeitende = vorrang = False
    for i, v in enumerate(venues, 1):
        if ende and time.time() >= ende:
            zeitende = True; st['offen (Zeitgrenze)'] = len(venues) - i + 1; break
        if a.nachgeben and vorrang_aktiv():
            vorrang = True; st['offen (Vorrang täglicher Lauf)'] = len(venues) - i + 1; break
        v.update(lat=None, lon=None, kh=None, ep=None, zh=None, osm=None, ob=None, anl=None)
        man = manual.get(v['key'])
        if man:  # 1. Handkorrektur
            if man.get('europlan') in EP_BY_ID:
                s = EP_BY_ID[man['europlan']]; set_(v, ep=s['id'], lat=s['lat'], lon=s['lon'], kh='europlan', zh='manuell')
            if 'lat' in man:
                set_(v, lat=man['lat'], lon=man['lon'], kh='manuell', zh=v['zh'] or 'manuell')
            st['1 Handkorrektur'] += 1
        o = osm_locate(v)
        if a.nur:
            print('Diagnose:', v['raw'], '| Ortsteile im Namen:', name_orte(v), '| Ort der Adresse:', ort_point(v),
                  '| Straße:', street_point(v), '| OSM-Platz:', o and (o['name'], o['lat'], o['lon']), flush=True)
        if v['ep'] is None and not man:  # 2. Europlan über Verein und Liga
            s, why = ep_club(v, o)
            s = gleiche_nummer(v, s)
            if s:
                set_(v, ep=s['id'], lat=s['lat'], lon=s['lon'], kh='europlan', zh='verein_liga'); st['2 Europlan Verein/Liga'] += 1
            else:
                st['2 nicht: ' + why] += 1
            if a.nur: print('  Europlan über Verein:', s and (s['id'], s['name']), why, '| Heimspiele:', homes.get(v['key'], [])[:3], flush=True)
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
            s = gleiche_nummer(v, ep_near(v))
            if s:
                set_(v, ep=s['id'], zh='entfernung'); st['5 Europlan Entfernung'] += 1
                if v['kh'] != 'manuell': set_(v, lat=s['lat'], lon=s['lon'], kh='europlan')
        # 6. Rückfall (ungefähr): Straßenmitte, sonst PLZ-Mittelpunkt
        if v['lat'] is None:
            sp = street_point(v)
            if sp:
                set_(v, lat=sp[0], lon=sp[1], kh='osm_strasse'); st['6 Straßenmitte (ungefähr)'] += 1
            elif name_orte(v):
                la, lo = name_orte(v)[0]; set_(v, lat=la, lon=lo, kh='osm_ortsteil'); st['6 Ortsteil (ungefähr)'] += 1
            elif plz.get(v['plz'] or ''):
                pc = plz[v['plz']]; set_(v, lat=pc[0], lon=pc[1], kh='plz'); st['6 PLZ-Mittelpunkt (ungefähr)'] += 1
        if v['lat'] is None:
            st['ohne Koordinaten'] += 1
        # 7. Kein eigener Platz bei Europlan: Europlan-Platz derselben Anlage (unter 250 m, nur genaue Koordinaten)
        if v['ep'] is None and v['lat'] is not None and v['kh'] not in UNGEFAEHR:
            ref = ort_point(v) or (v['lat'], v['lon'])
            near = [s for d, s in egrid.near(v['lat'], v['lon'], ANLAGE) if not other_place(v, s, ref)]
            if near:
                v['anl'] = near[0]['id']; st['7 Europlan (Anlage)'] += 1
        belag_upd = v['belag'] == 'unbekannt' and v['ob']
        c.execute('''UPDATE spielstaetten SET lat=?, lon=?, koord_herkunft=?, koord_ungefaehr=?, europlan_id=?,
                     zuordnung_herkunft=?, osm_id=?, bearbeitet=?, europlan_anlage_id=?, belag=CASE WHEN ? THEN ? ELSE belag END,
                     belag_quelle=CASE WHEN ? THEN 'osm' ELSE belag_quelle END WHERE schluessel=?''',
                  (v['lat'], v['lon'], v['kh'], int(v['kh'] in UNGEFAEHR), v['ep'], v['zh'] or 'keine', v['osm'], int(time.time()), v['anl'],
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
    if zeitende: sys.exit(3)
    if vorrang: sys.exit(4)

if __name__ == '__main__':
    main()
