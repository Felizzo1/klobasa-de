#!/usr/bin/env python3
"""Abruf der Ansetzungen (Herren und Frauen) von fussball.de in SQLite.

  .venv/bin/python scripts/crawl.py [--mandanten 63] [--menu-erzwingen] [--limit N] [--warten MIN] [--ende HH:MM]

Sperre: Es greift immer nur ein Prozess auf fussball.de zu (~/klobasa-de-data/fussballde.lock, gilt für täglichen
Lauf, Erstbefüllung und Handaufrufe). Ist sie belegt, wird bis --warten Minuten gewartet, sonst Ende mit Code 75.
--ende HH:MM: Zeitgrenze; danach keine neuen Abrufe, Stand wird gesichert, Code 3 (nächster Lauf macht weiter).

Ablauf: 1. Menü (wöchentlich) -> Staffeln, 2. Spielplan je Staffel (ajax.fixturelist, ab heute bis Saisonende),
3. Druckansicht für Staffeln mit Spiel in den nächsten 8 Tagen -> Spielstätten,
4. Vereinsspielplan (ab heute 6 Wochen, mit Spielstätten und Platzart) für Vereine, deren Heimspiele noch keinen
   echten Ort haben (neu: 42 Tage, danach nur noch Lücken in den nächsten 14 Tagen), 5. Rest schätzen.

Langsam: eine Anfrage gleichzeitig, 1,5–3 s Pause. Bei 403/429 oder 5 Fehlschlägen in Folge Abbruch (Stand bleibt).
Datenschutz: Die Druckansicht enthält Schiedsrichternamen. Ausgewertet wird nur der Text nach „Spielstätte:“,
die Rohseite wird weder gespeichert noch geloggt.
"""
import argparse, fcntl, html as H, io, json, logging, os, random, re, sqlite3, sys, time
from datetime import date, datetime, timedelta
import requests
from fontTools.ttLib import TTFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from namen import ohne_personen_klammer  # noqa: E402
from sperren import gesperrt, sperren, vorrang_aktiv  # noqa: E402

BASE = 'https://www.fussball.de'
UA = 'Klobasa-Planer-DE/0.1 (privates Projekt)'
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.path.join(DATA, 'klobasa.sqlite')
ARTEN = ('Herren', 'Frauen')
COMPTYPE = '1'  # Meisterschaft
MENU_MAX_AGE = 7 * 86400
SOON = 42       # Tage: nächstes Spiel binnen 6 Wochen -> bei jedem Lauf
LATER_MAX_AGE = 14 * 86400
DONE_MAX_AGE = 30 * 86400
DRUCK_TAGE = 8
VEREIN_TAGE = 42        # Zeitraum des Vereinsspielplans ab heute
VEREIN_NEU_TAGE = 42    # noch nie geholter Verein: Lücken in diesem Zeitraum
VEREIN_LUECKE_TAGE = 14 # schon geholter Verein: nur Lücken in diesem Zeitraum
VEREIN_MIN_ABSTAND = 20 * 3600
VEREIN_SAISON_ABSTAND = 7 * 86400  # Vereinsspielplan bis Saisonende: je Verein höchstens wöchentlich
VEREIN_SAISON_ABSTAND_VOLL = 28 * 86400   # Vereine ohne geschätzte Spiele: nur alle 4 Wochen (Platzwechsel fängt die Druckansicht)
VEREIN_MAX_ZEILEN = 2000           # Seiten zu je 100 Wettbewerbszeilen (Saison mit Jugend kann lang sein)
ECHT = ('druck', 'verein')

log = logging.getLogger('crawl')

# ---------------------------------------------------------------- Abruf
class Gesperrt(Exception):
    """403/429/Prüfseite: sofort aufhören, Sperrdatei anlegen, auf Freigabe warten."""

class Vorrang(Exception):
    """Der tägliche Lauf will an fussball.de (nur mit --nachgeben): Stand sichern, Code 4."""

class Abbruch(Exception):
    pass

class Zeitende(Exception):
    pass

LOCK = os.path.join(DATA, 'fussballde.lock')
PRUEFSEITE = re.compile(rb'captcha|cf-challenge|challenge-platform|cf-turnstile|just a moment\.\.\.|bot.?protection|'
                        rb'zugriff verweigert|access denied', re.I)
FRISCH = 0  # Sekunden: Spielpläne/Druckansichten, die jünger sind, nicht neu holen (--frisch, Fortsetzen nach Pause)

def sperre(warten_min):
    """Exklusive Sperre für alle Zugriffe auf fussball.de; bleibt bis Prozessende bestehen."""
    fd = open(LOCK, 'w')
    bis = time.time() + warten_min * 60
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); return fd
        except BlockingIOError:
            if time.time() >= bis: return None
            time.sleep(20)

def zeitgrenze(hhmm):
    """„07:30“ -> Zeitstempel des nächsten 07:30."""
    if not hhmm: return None
    h, m = map(int, hhmm.split(':'))
    d = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    if d <= datetime.now(): d += timedelta(days=1)
    return d.timestamp()

class Fetcher:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers['User-Agent'] = UA
        self.last = 0.0
        self.n = 0
        self.fails_in_row = 0
        self.errors = 0
        self.ende = None
        self.nachgeben = False

    def get(self, path, tries=3):
        url = path if path.startswith('http') else BASE + path
        if self.ende and time.time() >= self.ende:
            raise Zeitende()
        if self.nachgeben and vorrang_aktiv():
            raise Vorrang()
        for i in range(tries):
            wait = self.last + random.uniform(1.5, 3.0) - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                r = self.s.get(url, timeout=60)
            except requests.RequestException as e:
                log.warning('Netzfehler %s: %s', path, e)
                r = None
            if r is not None and r.status_code in (403, 429):
                raise Gesperrt(f'Status {r.status_code} bei {path}')
            if r is not None and PRUEFSEITE.search(r.content[:8000]):
                raise Gesperrt(f'Prüfseite (Status {r.status_code}) bei {path}')
            if r is not None and r.status_code == 200:
                self.fails_in_row = 0
                return r
            if r is not None:
                log.warning('Status %s bei %s (%d B)', r.status_code, path, len(r.content))
            self.fails_in_row += 1
            if self.fails_in_row >= 5:
                raise Abbruch(f'5 Fehlschläge in Folge, zuletzt {path}')
            time.sleep(5 * (i + 1))
        self.errors += 1
        return None

# ---------------------------------------------------------------- Datenbank
SCHEMA = '''
CREATE TABLE IF NOT EXISTS staffeln (
  staffel_id TEXT PRIMARY KEY, slug TEXT, name TEXT, mandant TEXT, mandant_name TEXT,
  art_id TEXT, art TEXT, spielklasse_id TEXT, spielklasse TEXT, gebiet_id TEXT, gebiet TEXT, saison TEXT,
  im_menue INTEGER DEFAULT 1, menue_gesehen INTEGER, zuletzt_geholt INTEGER, naechstes_spiel TEXT,
  zuletzt_druck INTEGER, fehler TEXT);
CREATE TABLE IF NOT EXISTS spiele (
  spiel_id TEXT PRIMARY KEY, staffel_id TEXT, spielnr TEXT, datum TEXT, zeit TEXT,
  heim_id TEXT, heim TEXT, gast_id TEXT, gast TEXT, status TEXT, info TEXT,
  spielstaette TEXT, spielstaette_quelle TEXT, verlegt_vom TEXT, gesehen INTEGER);
CREATE INDEX IF NOT EXISTS spiele_staffel ON spiele(staffel_id, datum);
CREATE TABLE IF NOT EXISTS spielstaetten (
  schluessel TEXT PRIMARY KEY, rohtext TEXT, name TEXT, strasse TEXT, plz TEXT, ort TEXT, belag TEXT,
  lat REAL, lon REAL, koord_herkunft TEXT, europlan_id INTEGER, zuordnung_herkunft TEXT, erstmals INTEGER);
CREATE TABLE IF NOT EXISTS team_heimplatz (team_id TEXT PRIMARY KEY, team TEXT, spielstaette TEXT, datum TEXT);
CREATE TABLE IF NOT EXISTS fonts (key TEXT PRIMARY KEY, tabelle TEXT, geholt INTEGER);
CREATE TABLE IF NOT EXISTS mannschaftsarten (mandant TEXT, art_id TEXT, name TEXT, gesehen INTEGER, PRIMARY KEY (mandant, art_id));
CREATE TABLE IF NOT EXISTS vereine (verein_id TEXT PRIMARY KEY, name TEXT, zuletzt_geholt INTEGER, spiele INTEGER);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS laeufe (id INTEGER PRIMARY KEY, start INTEGER, ende INTEGER, abrufe INTEGER, fehler INTEGER,
  staffeln_geholt INTEGER, spiele INTEGER, druck INTEGER, abbruch TEXT, notiz TEXT);
'''

def db():
    os.makedirs(DATA, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    for tab, col in (('spiele', 'heim_verein TEXT'), ('spiele', 'gast_verein TEXT'), ('spielstaetten', 'platzart TEXT'),
                     ('vereine', 'saison_geholt INTEGER')):
        try:
            c.execute(f'ALTER TABLE {tab} ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    return c

def meta(c, k, v=None):
    if v is None:
        r = c.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        return r[0] if r else None
    c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (k, str(v)))

# ---------------------------------------------------------------- Entschlüsselung
NAMES = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7',
         'eight': '8', 'nine': '9', 'colon': ':', 'period': '.', 'comma': ',', 'space': ' ', 'hyphen': '-', 'slash': '/'}

class Fonts:
    def __init__(self, c, f):
        self.c, self.f, self.mem = c, f, {}

    def table(self, key):
        if key in self.mem:
            return self.mem[key]
        r = self.c.execute('SELECT tabelle FROM fonts WHERE key=?', (key,)).fetchone()
        if r:
            t = {int(k): v for k, v in json.loads(r[0]).items()}
        else:
            resp = self.f.get(f'/export.fontface/-/format/woff/id/{key}/type/font')
            if resp is None:
                raise ValueError(f'Font {key} nicht ladbar')
            cmap = TTFont(io.BytesIO(resp.content)).getBestCmap()
            t = {cp: NAMES.get(g, g if len(g) == 1 else '') for cp, g in cmap.items()}
            self.c.execute('INSERT OR REPLACE INTO fonts VALUES (?,?,?)', (key, json.dumps(t), int(time.time())))
        self.mem[key] = t
        return t

    def decode(self, key, s):
        t = self.table(key)
        return ''.join(t.get(ord(ch), ch) for ch in s)

# ---------------------------------------------------------------- Parser
def text(h):
    return re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', ' ', h))).replace('\xa0', ' ').replace('\u200b', '').strip()

SPAN = re.compile(r'<span([^>]*)data-obfuscation="([^"]+)"([^>]*)>(.*?)</span>', re.S)
TEAM = re.compile(r'<td class="column-club[^"]*">(.*?)</td>', re.S)
TEAM_ID = re.compile(r'/team-id/([A-Z0-9]+)')
LOGO_ID = re.compile(r'getLogo/format/\d+/id/([A-Z0-9]+)')  # Logo-ID = Vereins-ID
SPIEL_ID = re.compile(r'/-/spiel/([A-Z0-9]+)')
RE_HEAD = re.compile(r'^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag), (\d\d)\.(\d\d)\.(\d{4})$')
RE_DATE = re.compile(r'^(?:(Mo|Di|Mi|Do|Fr|Sa|So), (\d\d)\.(\d\d)\.(\d\d) \| ?)?(\d\d:\d\d|\*\*)?$')  # „**“ = Uhrzeit noch offen (Hamburg)

def decode_cell(fonts, cell):
    """Alle verschleierten Spans einer Zelle entschlüsseln (nur Datums-/Zeit-Zellen werden hier übergeben)."""
    return text(SPAN.sub(lambda m: fonts.decode(m.group(2), H.unescape(m.group(4))), cell))

def parse_fixturelist(page, fonts):
    """-> (spiele, probleme). Lädt nur Fonts der Keys aus Tagesüberschriften und Datums-/Zeitspalten."""
    spiele, probleme = [], []
    cur_date, cur_time = None, None
    for attrs, row in re.findall(r'<tr([^>]*)>(.*?)</tr>', page, re.S):
        if 'row-headline' in attrs:
            t = decode_cell(fonts, row)
            m = RE_HEAD.match(t)
            if not m:
                probleme.append('Überschrift: ' + t[:40]); continue
            cur_date, cur_time = f'{m[4]}-{m[3]}-{m[2]}', None
            continue
        if 'thead' in attrs or 'column-score' not in row:
            continue
        dc = re.search(r'<td class="column-date">(.*?)</td>', row, re.S)
        t = decode_cell(fonts, dc.group(1)) if dc else ''
        m = RE_DATE.match(t)
        if not m:
            probleme.append('Datum/Zeit: ' + t[:40]); continue
        if m[2]:
            d = f'20{m[4]}-{m[3]}-{m[2]}'
            if cur_date and d != cur_date:
                probleme.append(f'Datum {d} passt nicht zur Überschrift {cur_date}')
            cur_date = d
        if m[5]:
            cur_time = None if m[5] == '**' else m[5]
        elif m[2]:
            pass  # neues Datum ohne Zeitspalte: Zeit wie bisher von oben übernehmen
        teams = TEAM.findall(row)
        sid = SPIEL_ID.search(row)
        if len(teams) < 2 or not sid:
            continue  # spielfrei o. Ä.
        def team(cell):
            tid = TEAM_ID.search(cell); lid = LOGO_ID.search(cell)
            nm = re.search(r'<div class="club-name">(.*?)</div>', cell, re.S)
            return (tid.group(1) if tid else None), text(nm.group(1) if nm else cell), (lid.group(1) if lid else None)
        hid, hn, hv = team(teams[0]); gid, gn, gv = team(teams[1])
        sc = re.search(r'<td class="column-score">(.*?)</td>', row, re.S)
        info = ''
        if sc:
            it = re.search(r'class="info-text">(.*?)</span>', sc.group(1), re.S)
            info = text(it.group(1)) if it else ''
        status = 'normal'
        il = info.lower()
        if 'nichtantritt' in il: status = 'nichtantritt'
        elif 'abgesetzt' in il or 'absetzung' in il: status = 'abgesetzt'
        elif 'ausfall' in il or 'ausgefallen' in il or 'abgesagt' in il: status = 'ausgefallen'
        elif 'annulliert' in il: status = 'annulliert'
        elif info: status = 'info'
        if re.search(r'\bzg\.?$', hn) or re.search(r'\bzg\.?$', gn):
            status = 'zurueckgezogen' if status == 'normal' else status
        if not hid or not gid:
            status = 'spielfrei' if 'spielfrei' in (hn + gn).lower() else status
        spiele.append({'spiel_id': sid.group(1), 'datum': cur_date, 'zeit': cur_time, 'heim_id': hid, 'heim': hn,
                       'gast_id': gid, 'gast': gn, 'status': status, 'info': info,
                       'heim_verein': hv, 'gast_verein': gv})
        if not cur_date:
            probleme.append('Spiel ohne Datum ' + sid.group(1))
    return spiele, probleme

def parse_druck(page):
    """-> {spiel_id: (spielstaette, verlegt_vom)}. Nur der Text nach „Spielstätte:“ wird übernommen."""
    out, cur = {}, None
    for attrs, row in re.findall(r'<tr([^>]*)>(.*?)</tr>', page, re.S):
        if 'row-venue' in attrs:
            if not cur:
                continue
            venue = verlegt = None
            for div in re.findall(r'<div[^>]*>(.*?)</div>', row, re.S):
                t = text(div)
                i = t.find('Spielstätte:')
                if i >= 0:
                    venue = t[i + len('Spielstätte:'):].strip()
                m = re.match(r'verlegt vom:\s*(\d\d)\.(\d\d)\.(\d{4})(?:\s+(\d\d:\d\d))?', t)
                if m:
                    verlegt = f'{m[3]}-{m[2]}-{m[1]}' + (' ' + m[4] if m[4] else '')
            out[cur] = (venue, verlegt)
            cur = None
            continue
        sid = SPIEL_ID.search(row)
        cur = sid.group(1) if sid and 'column-club' in row else None
    return out

def venue_key(v):
    return re.sub(r'\s*\|\s*', ' | ', re.sub(r'\s+', ' ', v)).strip().casefold()

def belag(v):
    s = v.lower()
    if re.search(r'kunstrasen|kunststoffrasen|\bkr\b|\bkura\b|\bkrp\b', s): return 'kunstrasen'
    if re.search(r'rasen', s): return 'rasen'
    return 'unbekannt'

# ---------------------------------------------------------------- Schritte
def walk_competitions(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and '/staffel/' in k:
                yield k.lstrip('_'), v
            else:
                yield from walk_competitions(v)

def menu(c, f, mandanten, saison, force):
    # Alter je Verband (die Liste des täglichen Laufs wächst mit der Erstbefüllung)
    alle = mandanten
    mandanten = [md for md in alle if force or not (meta(c, 'menu_' + md) and time.time() - float(meta(c, 'menu_' + md)) < MENU_MAX_AGE)]
    if not mandanten:
        log.info('Menü: noch aktuell'); return
    log.info('Menü neu für %s (%d von %d Verbänden)', ','.join(mandanten), len(mandanten), len(alle))
    base = f.get('/wam_base.json').json()
    mnames = {k.lstrip('_'): v for k, v in base['Mandanten'].items()}
    now = int(time.time()); found = set()
    for md in mandanten:
        r = f.get(f'/wam_kinds_{md}_{saison}_{COMPTYPE}.json')
        if r is None:
            raise Abbruch(f'Menü {md} nicht ladbar')
        kinds = r.json()
        for aid, an in kinds.get('Mannschaftsart', {}).items():
            c.execute('INSERT OR REPLACE INTO mannschaftsarten VALUES (?,?,?,?)', (md, aid.lstrip('_'), an, now))
        for aid, an in kinds.get('Mannschaftsart', {}).items():
            if an not in ARTEN:
                continue
            aid = aid.lstrip('_')
            for skid, skn in (kinds.get('Spielklasse', {}).get(aid) or {}).items():
                skid = skid.lstrip('_')
                for gid, gn in ((kinds.get('Gebiet', {}).get(aid) or {}).get(skid) or {}).items():
                    gid = gid.lstrip('_')
                    r = f.get(f'/wam_competitions_{md}_{saison}_{COMPTYPE}_{aid}_{skid}_{gid}.json')
                    if r is None:
                        continue
                    for url, name in walk_competitions(r.json()):
                        m = re.search(r'/spieltagsuebersicht/([^/]+)/-/staffel/([A-Z0-9-]+)', url)
                        if not m:
                            log.warning('Unbekannte Staffel-URL %s', url); continue
                        slug, sid = m.groups()
                        name = ohne_personen_klammer(name)  # Staffelleiter o. Ä. nie speichern
                        found.add(sid)
                        c.execute('''INSERT INTO staffeln (staffel_id, slug, name, mandant, mandant_name, art_id, art,
                                       spielklasse_id, spielklasse, gebiet_id, gebiet, saison, im_menue, menue_gesehen)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                                     ON CONFLICT(staffel_id) DO UPDATE SET slug=excluded.slug, name=excluded.name,
                                       mandant=excluded.mandant, mandant_name=excluded.mandant_name, art_id=excluded.art_id,
                                       art=excluded.art, spielklasse_id=excluded.spielklasse_id, spielklasse=excluded.spielklasse,
                                       gebiet_id=excluded.gebiet_id, gebiet=excluded.gebiet, saison=excluded.saison,
                                       im_menue=1, menue_gesehen=excluded.menue_gesehen''',
                                  (sid, slug, name, md, mnames.get(md), aid, an, skid, skn, gid, gn, saison, now))
        c.commit()
    if not found:
        raise Abbruch('Menü lieferte keine Staffeln')
    q = ','.join('?' * len(mandanten))
    c.execute(f'UPDATE staffeln SET im_menue=0 WHERE mandant IN ({q}) AND menue_gesehen<?', (*mandanten, now))
    for md in mandanten: meta(c, 'menu_' + md, now)
    c.commit()
    log.info('Menü: %d Staffeln', len(found))

def needs_fetch(row, today):
    zuletzt, naechstes = row
    if not zuletzt:
        return True
    age = time.time() - zuletzt
    if not naechstes:
        return age >= DONE_MAX_AGE
    if (date.fromisoformat(naechstes) - today).days > SOON:
        return age >= LATER_MAX_AGE
    return True

def spielplaene(c, f, fonts, mandanten, saison, run, limit):
    today = date.today()
    bis = f'20{saison[2:]}-06-30'
    q = ','.join('?' * len(mandanten))
    rows = c.execute(f'''SELECT staffel_id, name, zuletzt_geholt, naechstes_spiel FROM staffeln
                        WHERE im_menue=1 AND saison=? AND mandant IN ({q}) ORDER BY mandant, name''', (saison, *mandanten)).fetchall()
    todo = [r for r in rows if needs_fetch(r[2:], today) and not (FRISCH and r[2] and time.time() - r[2] < FRISCH)]
    if limit:
        todo = todo[:limit]
    log.info('Spielpläne: %d von %d Staffeln fällig', len(todo), len(rows))
    geholt = 0
    for k, (sid, name, _, _) in enumerate(todo, 1):
        r = f.get(f'/ajax.fixturelist/-/staffel/{sid}/datum-von/{today.isoformat()}/datum-bis/{bis}')
        if r is None:
            c.execute('UPDATE staffeln SET fehler=? WHERE staffel_id=?', ('Abruf fehlgeschlagen', sid)); continue
        try:
            sp, probleme = parse_fixturelist(r.text, fonts)
        except ValueError as e:
            sp, probleme = [], [str(e)]
        if probleme:
            log.error('Staffel %s (%s): Selbsttest fehlgeschlagen, alter Stand bleibt: %s', sid, name, '; '.join(probleme[:3]))
            c.execute('UPDATE staffeln SET fehler=? WHERE staffel_id=?', ('Selbsttest: ' + probleme[0], sid)); c.commit()
            continue
        offen = c.execute('SELECT COUNT(*) FROM spiele WHERE staffel_id=? AND datum>=?', (sid, today.isoformat())).fetchone()[0]
        if not sp and offen:
            log.warning('Staffel %s (%s): leeres Ergebnis, alter Stand (%d Spiele) bleibt', sid, name, offen)
            continue
        now = int(time.time())
        for s in sp:
            c.execute('''INSERT INTO spiele (spiel_id, staffel_id, datum, zeit, heim_id, heim, gast_id, gast, status, info, gesehen,
                           heim_verein, gast_verein)
                         VALUES (:spiel_id, :sid, :datum, :zeit, :heim_id, :heim, :gast_id, :gast, :status, :info, :now,
                           :heim_verein, :gast_verein)
                         ON CONFLICT(spiel_id) DO UPDATE SET staffel_id=excluded.staffel_id, datum=excluded.datum,
                           heim_verein=excluded.heim_verein, gast_verein=excluded.gast_verein,
                           zeit=excluded.zeit, heim_id=excluded.heim_id, heim=excluded.heim, gast_id=excluded.gast_id,
                           gast=excluded.gast, status=excluded.status, info=excluded.info, gesehen=excluded.gesehen''',
                      {**s, 'sid': sid, 'now': now})
        # Künftige Spiele, die nicht mehr im Spielplan stehen, als verschwunden markieren
        ids = [s['spiel_id'] for s in sp]
        c.execute(f'''UPDATE spiele SET status='verschwunden' WHERE staffel_id=? AND datum>=?
                      AND spiel_id NOT IN ({','.join('?' * len(ids)) or "''"})''', (sid, today.isoformat(), *ids))
        nxt = min((s['datum'] for s in sp if s['datum'] and s['status'] == 'normal'), default=None)
        c.execute('UPDATE staffeln SET zuletzt_geholt=?, naechstes_spiel=?, fehler=NULL WHERE staffel_id=?', (now, nxt, sid))
        c.commit(); geholt += 1
        if k % 10 == 0 or k == len(todo):
            log.info('  %d/%d Spielpläne (%d Abrufe)', k, len(todo), f.n)
    c.execute('UPDATE laeufe SET staffeln_geholt=? WHERE id=?', (geholt, run))

def druck(c, f, mandanten, run):
    today = date.today()
    bis = (today + timedelta(days=DRUCK_TAGE)).isoformat()
    q = ','.join('?' * len(mandanten))
    sids = [r[0] for r in c.execute(f'''SELECT DISTINCT s.staffel_id, s.zuletzt_druck FROM staffeln s JOIN spiele p ON p.staffel_id=s.staffel_id
              WHERE s.im_menue=1 AND s.mandant IN ({q}) AND p.datum BETWEEN ? AND ? AND p.status='normal' ''',
              (*mandanten, today.isoformat(), bis)) if not (FRISCH and r[1] and time.time() - r[1] < FRISCH)]
    log.info('Druckansichten: %d Staffeln mit Spiel bis %s', len(sids), bis)
    n = 0
    for k, sid in enumerate(sids, 1):
        r = f.get(f'/spieltagsuebersicht.druck/-/max/999/mode/PRINT/staffel/{sid}')
        if r is None:
            continue
        venues = parse_druck(r.text)
        del r  # Rohseite (mit Schiedsrichtern) sofort verwerfen
        now = int(time.time())
        for spid, (v, verlegt) in venues.items():
            if v:
                n += store_venue(c, spid, [p.strip() for p in v.split('|')], None, 'druck', verlegt)
        c.execute('UPDATE staffeln SET zuletzt_druck=? WHERE staffel_id=?', (now, sid))
        c.commit()
        if k % 10 == 0 or k == len(sids):
            log.info('  %d/%d Druckansichten (%d Spielstätten zugeordnet)', k, len(sids), n)
    c.execute('UPDATE laeufe SET druck=? WHERE id=?', (n, run))

def store_venue(c, spid, parts, platzart, quelle, verlegt=None):
    """Spielstätte (Name, Straße, PLZ Ort) für ein bekanntes Spiel speichern. -> 1, wenn das Spiel bekannt ist."""
    if not c.execute('SELECT 1 FROM spiele WHERE spiel_id=?', (spid,)).fetchone():
        return 0
    parts = [p for p in parts if p]
    if not parts:
        return 0
    if len(parts) >= 3:
        name, strasse, plzort = ', '.join(parts[:-2]), parts[-2], parts[-1]
    else:
        name, strasse, plzort = parts[0], None, parts[-1] if len(parts) == 2 else None
    raw = ' | '.join(x for x in (name, strasse, plzort) if x)
    key = venue_key(raw)
    m = re.match(r'(\d{5})\s+(.*)', plzort or '')
    plz, ort = m.groups() if m else (None, None)
    b = belag_aus(platzart, raw)
    c.execute('''INSERT INTO spielstaetten (schluessel, rohtext, name, strasse, plz, ort, belag, platzart, erstmals)
                 VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(schluessel) DO UPDATE SET
                   platzart=COALESCE(excluded.platzart, spielstaetten.platzart),
                   belag=CASE WHEN excluded.platzart IS NOT NULL THEN excluded.belag ELSE spielstaetten.belag END''',
              (key, raw, name, strasse, plz, ort, b, platzart, int(time.time())))
    if quelle == 'druck':
        c.execute('''UPDATE spiele SET spielstaette=?, spielstaette_quelle='druck', verlegt_vom=? WHERE spiel_id=?''',
                  (key, verlegt, spid))
    else:  # Vereinsspielplan überschreibt keine Angabe aus der Druckansicht
        c.execute('''UPDATE spiele SET spielstaette=?, spielstaette_quelle=? WHERE spiel_id=?
                     AND (spielstaette_quelle IS NULL OR spielstaette_quelle!='druck')''', (key, quelle, spid))
    row = c.execute('SELECT heim_id, heim, datum FROM spiele WHERE spiel_id=?', (spid,)).fetchone()
    if row and row[0]:
        c.execute('''INSERT INTO team_heimplatz VALUES (?,?,?,?) ON CONFLICT(team_id) DO UPDATE SET
                     team=excluded.team, spielstaette=excluded.spielstaette, datum=excluded.datum
                     WHERE excluded.datum >= team_heimplatz.datum OR team_heimplatz.datum IS NULL''',
                  (row[0], row[1], key, row[2]))
    return 1

PLATZART_BELAG = {'kunstrasenplatz': 'kunstrasen', 'kunstrasen': 'kunstrasen', 'rasenplatz': 'rasen', 'rasen': 'rasen',
                  'naturrasen': 'rasen', 'hybridrasen': 'rasen'}

def belag_aus(platzart, raw):
    """Platzart (festes Feld im Vereinsspielplan) geht vor dem Text der Spielstätte."""
    if platzart and platzart.lower() in PLATZART_BELAG:
        return PLATZART_BELAG[platzart.lower()]
    return belag(raw)

def parse_club(page):
    """Vereinsspielplan -> ([(spiel_id, [Teile der Spielstätte], platzart)], Zahl der Wettbewerbszeilen)."""
    out, cur = [], None
    rows = re.findall(r'<tr([^>]*)>(.*?)</tr>', page, re.S)
    n_comp = sum('row-competition' in a for a, _ in rows)
    for attrs, row in rows:
        if 'row-venue' in attrs:
            if cur:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                t = text(max(cells, key=len)) if cells else ''
                parts = [p.strip() for p in t.split(',')]
                platzart = None
                if len(parts) >= 4 or (len(parts) >= 2 and re.fullmatch(r'\w*(platz|rasen|halle|feld)|Halle', parts[0], re.I)):
                    platzart, parts = parts[0], parts[1:]
                out.append((cur, parts, platzart))
            cur = None
            continue
        sid = SPIEL_ID.search(row)
        if 'column-club' in row:
            cur = sid.group(1) if sid else None
    return out, n_comp

def vereine(c, f, mandanten, run, saisonende, saison_anteil=0):
    today = date.today()
    t_neu = (today + timedelta(days=VEREIN_NEU_TAGE)).isoformat()
    t_luecke = (today + timedelta(days=VEREIN_LUECKE_TAGE)).isoformat()
    bis = (today + timedelta(days=VEREIN_TAGE)).isoformat()
    q = ','.join('?' * len(mandanten))
    rows = c.execute(f'''SELECT p.heim_verein, MIN(p.heim), v.zuletzt_geholt FROM spiele p
        JOIN staffeln s ON s.staffel_id=p.staffel_id LEFT JOIN vereine v ON v.verein_id=p.heim_verein
        WHERE s.im_menue=1 AND s.mandant IN ({q}) AND p.status='normal' AND p.heim_verein IS NOT NULL
          AND (p.spielstaette_quelle IS NULL OR p.spielstaette_quelle NOT IN ('druck','verein'))
          AND p.datum >= ? AND p.datum <= CASE WHEN v.zuletzt_geholt IS NULL THEN ? ELSE ? END
        GROUP BY p.heim_verein''', (*mandanten, today.isoformat(), t_neu, t_luecke)).fetchall()
    todo = {vid: (nm, bis) for vid, nm, z in rows if not z or time.time() - z >= VEREIN_MIN_ABSTAND}
    n_luecke = len(todo)
    # Alle Vereine mit Heimspielen: Spielplan bis Saisonende, wöchentlich bei Spielen ohne echten Ort, sonst alle 4 Wochen. fussball.de kennt die Spielstätte
    # späterer Spiele oft schon; was dort steht, gilt als echt (ersetzt „geschätzt“). Älteste zuerst; mit saison_anteil
    # (täglicher Lauf) je Lauf höchstens 1/saison_anteil der Vereine (mind. 100), damit sich die Last über die Woche verteilt.
    rows = c.execute(f'''SELECT p.heim_verein, MIN(p.heim), v.saison_geholt,
          SUM(p.spielstaette IS NULL OR p.spielstaette_quelle='geschaetzt') FROM spiele p
        JOIN staffeln s ON s.staffel_id=p.staffel_id LEFT JOIN vereine v ON v.verein_id=p.heim_verein
        WHERE s.im_menue=1 AND s.mandant IN ({q}) AND p.status='normal' AND p.heim_verein IS NOT NULL AND p.datum >= ?
        GROUP BY p.heim_verein''', (*mandanten, today.isoformat())).fetchall()
    def ist_faellig(r):   # wöchentlich, solange Spiele ohne echten Ort offen sind; sonst alle 4 Wochen
        return not r[2] or time.time() - r[2] >= (VEREIN_SAISON_ABSTAND if r[3] else VEREIN_SAISON_ABSTAND_VOLL)
    faellig = sorted((r for r in rows if ist_faellig(r)),
                     key=lambda r: (r[2] or 0, -r[3]))
    if saison_anteil:
        faellig = faellig[:max(100, -(-len(rows) // saison_anteil))]
    for vid, nm, z, _ in faellig:
        todo[vid] = (nm, saisonende)
    log.info('Vereinsspielpläne: %d Vereine mit Lücken in den nächsten Wochen, %d bis Saisonende (fällig %d von %d Vereinen)',
             n_luecke, sum(1 for _, b in todo.values() if b == saisonende),
             sum(1 for r in rows if ist_faellig(r)), len(rows))
    n = 0
    for k, (vid, (nm, bis_v)) in enumerate(todo.items(), 1):
        gef = 0
        for offset in range(0, VEREIN_MAX_ZEILEN, 100):
            r = f.get(f'/ajax.club.matchplan/-/id/{vid}/mime-type/HTML/mode/PAGE/show-filter/false/max/100/'
                      f'datum-von/{today.isoformat()}/datum-bis/{bis_v}/match-type/-1/show-venues/checked/offset/{offset}')
            if r is None:
                break
            items, n_comp = parse_club(r.text)
            del r
            for spid, parts, platzart in items:
                gef += store_venue(c, spid, parts, platzart, 'verein')
            if n_comp < 100:
                break
        now = int(time.time())
        c.execute('''INSERT INTO vereine (verein_id, name, zuletzt_geholt, spiele, saison_geholt) VALUES (?,?,?,?,?)
                     ON CONFLICT(verein_id) DO UPDATE SET name=excluded.name, zuletzt_geholt=excluded.zuletzt_geholt,
                     spiele=excluded.spiele, saison_geholt=COALESCE(excluded.saison_geholt, saison_geholt)''',
                  (vid, nm, now, gef, now if bis_v == saisonende else None))
        c.commit(); n += gef
        if k % 25 == 0 or k == len(todo):
            log.info('  %d/%d Vereine (%d Spiele mit Spielstätte, %d Abrufe)', k, len(todo), n, f.n)
    c.execute('UPDATE laeufe SET notiz=? WHERE id=?', (f'verein: {len(todo)} Vereine, {n} Spiele', run))

def schaetzen(c):
    """Spiele ohne Spielstätte aus der Druckansicht: letzter bekannter Platz der Heimmannschaft (geschätzt)."""
    c.execute('''UPDATE spiele SET spielstaette=(SELECT spielstaette FROM team_heimplatz t WHERE t.team_id=spiele.heim_id),
                 spielstaette_quelle='geschaetzt'
                 WHERE (spielstaette_quelle IS NULL OR spielstaette_quelle='geschaetzt')
                   AND heim_id IN (SELECT team_id FROM team_heimplatz)''')
    c.commit()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mandanten', default='63', help='Kommagetrennt, z. B. 63 (Sachsen) oder alle')
    ap.add_argument('--menu-erzwingen', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='höchstens N Spielpläne (Test)')
    ap.add_argument('--nur-vereine', action='store_true', help='nur Vereinsspielpläne und Schätzung (ohne Menü, Spielpläne, Druckansicht)')
    ap.add_argument('--warten', type=float, default=0, help='Minuten auf die fussball.de-Sperre warten')
    ap.add_argument('--ende', help='Zeitgrenze HH:MM (danach Stand sichern, Code 3)')
    ap.add_argument('--nachgeben', action='store_true', help='Vorrang des täglichen Laufs beachten (Erstbefüllung): Stand sichern, Code 4')
    ap.add_argument('--saison-anteil', type=int, default=0,
                    help='Vereinsspielpläne bis Saisonende je Lauf höchstens 1/N der Vereine (täglicher Lauf: 6)')
    ap.add_argument('--frisch', type=float, default=0, help='Stunden: jüngere Spielpläne/Druckansichten nicht neu holen')
    a = ap.parse_args()
    global FRISCH
    FRISCH = a.frisch * 3600
    os.makedirs(os.path.join(DATA, 'logs'), exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(os.path.join(DATA, 'logs', 'crawl.log'), encoding='utf-8'),
                                  logging.StreamHandler(sys.stdout)])
    g = gesperrt()
    if g:
        log.error('fussball.de gesperrt seit %s – kein Abruf bis zur Freigabe (erstbefuellung.py --freigabe)', g); sys.exit(2)
    lock = sperre(a.warten)
    if lock is None:
        log.error('fussball.de-Sperre belegt (anderer Lauf aktiv), nach %.0f min aufgegeben', a.warten); sys.exit(75)
    c = db(); f = Fetcher(); fonts = Fonts(c, f); f.ende = zeitgrenze(a.ende); f.nachgeben = a.nachgeben
    zeitende = vorrang = gesperrt_grund = False
    run = c.execute('INSERT INTO laeufe (start) VALUES (?)', (int(time.time()),)).lastrowid; c.commit()
    abbruch = None
    try:
        base = f.get('/wam_base.json')
        if base is None:
            raise Abbruch('wam_base.json nicht ladbar')
        base = base.json()
        saison = base['currentSaison']
        mandanten = [k.lstrip('_') for k in base['Mandanten']] if a.mandanten == 'alle' else a.mandanten.split(',')
        if not a.nur_vereine:
            menu(c, f, mandanten, saison, a.menu_erzwingen)
            spielplaene(c, f, fonts, mandanten, saison, run, a.limit)
            druck(c, f, mandanten, run)
        vereine(c, f, mandanten, run, f'20{saison[2:]}-06-30', a.saison_anteil)
        schaetzen(c)
        # Font-Keys wechseln mit jeder Seite: alte Tabellen nicht ewig aufheben
        c.execute('DELETE FROM fonts WHERE geholt < ?', (int(time.time()) - 3 * 86400,)); c.commit()
    except Gesperrt as e:
        abbruch = gesperrt_grund = f'GESPERRT: {e}'; nf = sperren(str(e))
        if nf: log.warning('ntfy-Nachricht nicht gesendet (%s)', nf)
        log.error('GESPERRT: %s – sofort gestoppt, Stand gesichert, kein Abruf mehr bis zur Freigabe', e)
    except Vorrang:
        vorrang = True; c.commit(); log.info('Täglicher Lauf hat Vorrang – Stand gesichert, Pause')
    except Abbruch as e:
        abbruch = str(e); log.error('ABBRUCH: %s – Stand gesichert', e)
    except Zeitende:
        zeitende = True; c.commit(); log.info('Zeitgrenze %s erreicht – Stand gesichert, nächster Lauf macht weiter', a.ende)
    finally:
        n = c.execute("SELECT COUNT(*) FROM spiele WHERE datum>=date('now') AND status!='verschwunden'").fetchone()[0]
        c.execute('UPDATE laeufe SET ende=?, abrufe=?, fehler=?, spiele=?, abbruch=? WHERE id=?',
                  (int(time.time()), f.n, f.errors, n, abbruch or ('Zeitende' if zeitende else 'Vorrang' if vorrang else None), run))
        c.commit()
        log.info('Lauf %d fertig: %d Abrufe, %d Fehler, %d offene Spiele%s', run, f.n, f.errors, n,
                 ', ABBRUCH' if abbruch else '')
    sys.exit(2 if gesperrt_grund else 1 if abbruch else 3 if zeitende else 4 if vorrang else 0)

if __name__ == '__main__':
    main()
