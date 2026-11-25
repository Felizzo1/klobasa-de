#!/usr/bin/env python3
"""Spielstätten für Bayern von bfv.de (fussball.de liefert sie für BFV-Ligen ab Landesliga nicht; geprüft 25.11.2026).

  .venv/bin/python -I scripts/bfv.py [--mandanten 31] [--ende 07:00] [--limit N]

bfv.de nutzt dieselben Staffel- und Spiel-IDs wie fussball.de. Ablauf, fortsetzbar (Tabelle bfv_teams):
  1. Tabelle je Staffel (/ergebnisse/wettbewerb/-/<STAFFEL-ID>/tabellen): alle Mannschafts-IDs der Staffel
     (die Namen sind dort verschleiert).
  2. Mannschaftsseite je Mannschaft, einmal pro Saison (/mannschaften/-/<TEAM-ID>): Name aus dem Seitentitel, zugeordnet
     zum Heimteam gleichen Namens in der Staffel;
     - die nächsten Spiele mit Ort je Spiel -> echte Spielstätte (Quelle „bfv“)
     - Abschnitt „Spielstätten“ (Heimplatz mit Zeitraum) -> Heimspiele ohne echten Ort, gekennzeichnet als geschätzt
Langsam: eine Anfrage gleichzeitig, 3–5 s Pause; Stopp bei 403/429/Prüfseite oder 5 Fehlschlägen in Folge.
Gespeichert wird nur die Spielstätte (Name | Straße | PLZ Ort); Kader, Spielberichte usw. werden nicht ausgewertet.
"""
import argparse, fcntl, json, logging, os, random, re, sqlite3, sys, time
from datetime import date
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl  # noqa: E402
from sperren import melde  # noqa: E402

DATA = os.path.expanduser('~/klobasa-de-data')
BASE = 'https://www.bfv.de'
LOCK = os.path.join(DATA, 'bfv.lock')
log = logging.getLogger('bfv')
SPIEL = re.compile(r'/ergebnisse/spiel/-/([A-Z0-9]+)')
PRUEF = re.compile(r'captcha|cf-challenge|challenge-platform|access denied|zugriff verweigert', re.I)

class Stopp(Exception):
    pass

class Holer:
    def __init__(self, ende=None):
        self.s = requests.Session(); self.s.headers['User-Agent'] = crawl.UA
        self.last = 0.0; self.n = 0; self.fails = 0; self.ende = ende
    def get(self, path):
        if self.ende and time.time() >= self.ende:
            raise Stopp('Zeitgrenze')
        for i in range(3):
            w = self.last + random.uniform(3.0, 5.0) - time.time()
            if w > 0: time.sleep(w)
            self.last = time.time(); self.n += 1
            try:
                r = self.s.get(BASE + path, timeout=60)
            except requests.RequestException as e:
                log.warning('Netzfehler %s: %s', path, e); r = None
            if r is not None and r.status_code in (403, 429):
                raise Stopp(f'Status {r.status_code} bei {path}')
            if r is not None and r.status_code == 200 and PRUEF.search(r.text[:8000]):
                raise Stopp(f'Prüfseite bei {path}')
            if r is not None and r.status_code == 200:
                self.fails = 0; return r.text
            if r is not None and r.status_code == 404:
                return None
            self.fails += 1
            if self.fails >= 5: raise Stopp(f'5 Fehlschläge in Folge, zuletzt {path}')
            time.sleep(10 * (i + 1))
        return None

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS bfv_teams (staffel_id TEXT, team_id TEXT, name TEXT, saison TEXT, geholt INTEGER,
                 echt INTEGER, geschaetzt INTEGER, heimplatz TEXT, PRIMARY KEY (staffel_id, team_id))''')
    c.execute('CREATE TABLE IF NOT EXISTS bfv_staffeln (staffel_id TEXT PRIMARY KEY, geholt INTEGER, teams INTEGER)')

def liga(c, h, sid):
    """Tabelle -> alle Mannschafts-IDs der Staffel."""
    page = h.get(f'/ergebnisse/wettbewerb/-/{sid}/tabellen')
    if page is None: return 0
    ids = list(dict.fromkeys(re.findall(r'/mannschaften/-/([0-9A-Z]{32})', page)))
    for tid in ids:
        c.execute('INSERT OR IGNORE INTO bfv_teams (staffel_id, team_id) VALUES (?,?)', (sid, tid))
    c.execute('INSERT OR REPLACE INTO bfv_staffeln VALUES (?,?,?)', (sid, int(time.time()), len(ids))); c.commit()
    return len(ids)

def norm(n):
    return re.sub(r'[^0-9a-zäöüß]+', ' ', (n or '').lower()).strip()

def text(s):
    return re.sub(r'\s+', ' ', crawl.text(s)).strip()

def dt(s):
    d, m, y = s.split('.'); return f'{y}-{m}-{d}'

def team(c, h, sid, tid, saison):
    page = h.get(f'/mannschaften/-/{tid}')
    if page is None: return
    m = re.search(r'<title>(.*?)</title>', page, re.S)
    titel = text(m.group(1)).rsplit('|', 1)[0].strip() if m else ''
    heim = [r[0] for r in c.execute("SELECT DISTINCT heim FROM spiele WHERE staffel_id=? AND datum>=date('now')", (sid,))]
    name = next((x for x in heim if x == titel), None) or next((x for x in heim if norm(x) == norm(titel)), None)
    echt = 0
    # nächste Spiele: Ort je Spiel (letzte Spiel-ID vor dem Ortsfeld)
    for m in re.finditer(r'bfv-spieltag-eintrag__location">(.*?)</div>', page, re.S):
        ids = SPIEL.findall(page[max(0, m.start() - 4000):m.start()])
        v = text(m.group(1))
        if ids and v:
            cur = c.execute('SELECT spielstaette_quelle FROM spiele WHERE spiel_id=?', (ids[-1],)).fetchone()
            if cur and cur[0] != 'druck':
                echt += crawl.store_venue(c, ids[-1], [p.strip() for p in v.split('|')], None, 'bfv')
    # Heimplatz der Saison (Zeiträume)
    plaetze = []
    for m in re.finditer(r'bfv-profile-more-information__location">\s*<div>([^<]*)</div>\s*<div>([^<]*)</div>', page):
        zr = re.findall(r'\d\d\.\d\d\.\d{4}', m.group(1)); v = text(m.group(2))
        if len(zr) == 2 and v: plaetze.append((dt(zr[0]), dt(zr[1]), v))
    gesch = 0
    if plaetze and name:
        for spid, datum in c.execute('''SELECT p.spiel_id, p.datum FROM spiele p WHERE p.staffel_id=? AND p.heim=? AND p.datum>=?
                    AND p.status='normal' AND (p.spielstaette_quelle IS NULL OR p.spielstaette_quelle='geschaetzt')''',
                                     (sid, name, date.today().isoformat())).fetchall():
            v = next((x[2] for x in plaetze if x[0] <= datum <= x[1]), None)
            if v: gesch += crawl.store_venue(c, spid, [p.strip() for p in v.split('|')], None, 'geschaetzt')
    c.execute('UPDATE bfv_teams SET name=?, saison=?, geholt=?, echt=?, geschaetzt=?, heimplatz=? WHERE staffel_id=? AND team_id=?',
              (name or f'?{titel}', saison, int(time.time()), echt, gesch, json.dumps(plaetze, ensure_ascii=False), sid, tid))
    c.commit()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mandanten', default='31'); ap.add_argument('--ende'); ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    fd = open(LOCK, 'w')
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('bfv.py läuft schon')
    c = crawl.db(); c.execute('PRAGMA busy_timeout=120000'); schema(c)   # läuft parallel zum täglichen Lauf (andere Quelle)
    h = Holer(crawl.zeitgrenze(a.ende))
    md = a.mandanten.split(',')
    q = ','.join('?' * len(md))
    saison = c.execute(f'SELECT saison FROM staffeln WHERE mandant IN ({q}) AND im_menue=1 LIMIT 1', md).fetchone()[0]
    try:
        # 1 Ligaseiten (einmal je Staffel)
        sids = [r[0] for r in c.execute(f'''SELECT s.staffel_id FROM staffeln s LEFT JOIN bfv_staffeln b USING(staffel_id)
                 WHERE s.mandant IN ({q}) AND s.im_menue=1 AND b.staffel_id IS NULL
                 AND EXISTS (SELECT 1 FROM spiele p WHERE p.staffel_id=s.staffel_id AND p.datum>=date('now'))''', md)]
        if a.limit: sids = sids[:a.limit]
        log.info('Ligaseiten: %d Staffeln', len(sids))
        for k, sid in enumerate(sids, 1):
            liga(c, h, sid)
            if k % 25 == 0: log.info('  %d/%d Ligaseiten (%d Abrufe)', k, len(sids), h.n)
        # 2 Mannschaftsseiten (einmal je Saison)
        todo = c.execute(f'''SELECT b.staffel_id, b.team_id FROM bfv_teams b JOIN staffeln s USING(staffel_id)
                 WHERE s.mandant IN ({q}) AND (b.saison IS NULL OR b.saison!=?)''', (*md, saison)).fetchall()
        if a.limit: todo = todo[:a.limit]
        log.info('Mannschaftsseiten: %d Mannschaften', len(todo))
        for k, (sid, tid) in enumerate(todo, 1):
            team(c, h, sid, tid, saison)
            if k % 50 == 0: log.info('  %d/%d Mannschaften (%d Abrufe)', k, len(todo), h.n)
    except Stopp as e:
        log.warning('Stopp: %s', e)
        if 'Zeitgrenze' not in str(e): melde('Klobasa-Planer: bfv.de-Abruf gestoppt', str(e))
    finally:
        c.commit()
    r = c.execute(f'''SELECT count(*), sum(p.spielstaette_quelle IN ('druck','verein','bfv')), sum(p.spielstaette_quelle='geschaetzt')
                     FROM spiele p JOIN staffeln s USING(staffel_id) WHERE s.mandant IN ({q}) AND s.im_menue=1
                     AND p.datum>=date('now') AND p.status='normal' ''', md).fetchone()
    log.info('Fertig: %d Abrufe. Offene Spiele %d, mit echtem Ort %d, geschätzt %d, ohne %d', h.n, r[0], r[1] or 0, r[2] or 0,
             r[0] - (r[1] or 0) - (r[2] or 0))

if __name__ == '__main__':
    main()
