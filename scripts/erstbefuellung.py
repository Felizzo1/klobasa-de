#!/usr/bin/env python3
"""Erstbefüllung bundesweit, Land für Land, verteilt auf mehrere Nächte (Timer klobasa-de-erst.timer, 02:00).

  .venv/bin/python -I scripts/erstbefuellung.py [--ende 07:30] [--status]

Je Verband (kleine zuerst): crawl.py (Menü, Spielpläne, Druckansicht, Vereinsspielpläne) -> venues.py ->
ligaebenen_entwurf.py (Entwurf aus Europlan-Stufen, geprüfte Einträge bleiben). Ein Verband gilt als fertig, wenn
crawl.py und venues.py ohne Zeitgrenze durchgelaufen sind; sonst macht die nächste Nacht dort weiter.
Sperren: pipeline.lock (nie gleichzeitig mit dem täglichen Lauf) und fussballde.lock in crawl.py.
Abbruch der Nacht bei 403/429 oder 5 Fehlschlägen in Folge (crawl.py Code 1) und bei belegter Sperre.
Läuft nur nachts (01:00–Zeitgrenze), damit ein nachgeholter Start tagsüber nichts abruft.
Stand: ~/klobasa-de-data/erstbefuellung.json, Log: ~/klobasa-de-data/logs/erstbefuellung.log
"""
import fcntl, json, os, sqlite3, subprocess, sys, time
from datetime import datetime

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.path.join(DATA, 'klobasa.sqlite')
STATE = os.path.join(DATA, 'erstbefuellung.json')
LOG = os.path.join(DATA, 'logs', 'erstbefuellung.log')
PY = [os.path.join(REPO, '.venv', 'bin', 'python'), '-I']
# klein -> groß (grob nach Zahl der Europlan-Plätze im Land); 89 = DFB-Ebene (Bundesliga bis Regionalliga), 63 ist schon da
REIHENFOLGE = ['89', '02', '43', '03', '66', '62', '64', '65', '61', '04', '41', '42', '33', '23', '32', '22', '34', '35',
               '21', '01', '31']

def log(msg):
    line = f"{datetime.now():%F %T} {msg}"
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')

def lade():
    try: return json.load(open(STATE, encoding='utf-8'))
    except FileNotFoundError: return {'verbaende': {}}

def speichere(s):
    tmp = STATE + '.neu'; json.dump(s, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1); os.replace(tmp, STATE)

def bilanz(md):
    c = sqlite3.connect(DB)
    q = lambda sql: c.execute(sql, (md,)).fetchone()[0]
    b = {
        'staffeln_herren': q("SELECT COUNT(*) FROM staffeln WHERE im_menue=1 AND mandant=? AND art='Herren'"),
        'staffeln_frauen': q("SELECT COUNT(*) FROM staffeln WHERE im_menue=1 AND mandant=? AND art='Frauen'"),
        'spiele_offen': q("""SELECT COUNT(*) FROM spiele p JOIN staffeln s USING(staffel_id) WHERE s.mandant=? AND s.im_menue=1
                             AND p.datum>=date('now') AND p.status='normal'"""),
        'ohne_spielstaette': q("""SELECT COUNT(*) FROM spiele p JOIN staffeln s USING(staffel_id) WHERE s.mandant=? AND s.im_menue=1
                             AND p.datum>=date('now') AND p.status='normal' AND p.spielstaette IS NULL"""),
    }
    v = c.execute("""SELECT COUNT(*), SUM(v.europlan_id IS NOT NULL), SUM(v.europlan_id IS NULL AND v.europlan_anlage_id IS NOT NULL),
                            SUM(v.koord_ungefaehr=1), SUM(v.lat IS NULL)
                     FROM spielstaetten v WHERE v.schluessel IN (SELECT p.spielstaette FROM spiele p JOIN staffeln s USING(staffel_id)
                     WHERE s.mandant=? AND s.im_menue=1 AND p.datum>=date('now'))""", (md,)).fetchone()
    b.update(spielstaetten=v[0], europlan=v[1] or 0, nur_anlage=v[2] or 0, ungefaehr=v[3] or 0, ohne_koordinaten=v[4] or 0)
    # Offene Frage 4: Mannschaftsarten, die nach Herren/Frauen klingen, aber anders heißen
    b['andere_arten'] = [r[0] for r in c.execute("""SELECT DISTINCT name FROM mannschaftsarten WHERE mandant=?
        AND (name LIKE '%Herren%' OR name LIKE '%Frauen%' OR name LIKE '%Männer%' OR name LIKE '%Damen%')
        AND name NOT IN ('Herren','Frauen') AND name NOT LIKE '%Ü%'""", (md,))]
    return b

def lauf(args, name):
    t = time.time()
    r = subprocess.run(PY + [os.path.join(REPO, 'scripts', args[0])] + args[1:],
                       stdout=open(os.path.join(DATA, 'logs', f'erst_{name}.log'), 'a'), stderr=subprocess.STDOUT)
    return r.returncode, (time.time() - t) / 60

def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    s = lade()
    if '--status' in sys.argv:
        for md in REIHENFOLGE:
            e = s['verbaende'].get(md, {})
            print(md, e.get('name', ''), e.get('status', 'offen'), e.get('bilanz', ''))
        return
    ende = sys.argv[sys.argv.index('--ende') + 1] if '--ende' in sys.argv else '07:30'
    h, m = map(int, ende.split(':'))
    now = datetime.now()
    grenze = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if not (1 <= now.hour and now < grenze):
        log(f'außerhalb des Nachtfensters (01:00–{ende}), nichts getan'); return
    lock = open(os.path.join(DATA, 'pipeline.lock'), 'w')
    bis = time.time() + 3600
    while True:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if time.time() > bis: log('pipeline.lock nach 60 min nicht frei (täglicher Lauf?), Nacht ausgelassen'); return
            time.sleep(30)
    names = {}
    try: names = {k.lstrip('_'): v for k, v in json.load(open(os.path.join(DATA, 'phase0', 'wam_base.json')))['Mandanten'].items()}
    except Exception: pass
    gesperrt = [md for md, e in s['verbaende'].items() if e.get('status') == 'abbruch']
    if gesperrt:
        log(f'Abbruch bei {gesperrt} in einer früheren Nacht – keine weiteren Abrufe, bis der Status in {STATE} von Hand '
            'zurückgesetzt ist'); return
    log(f'Nacht beginnt, Zeitgrenze {ende}')
    while time.time() < grenze.timestamp() - 600:
        offen = [md for md in REIHENFOLGE if s['verbaende'].get(md, {}).get('status') != 'fertig']
        if not offen:
            log('ALLE VERBÄNDE FERTIG – Timer kann auf bundesweit umgestellt werden'); s['alle_fertig'] = datetime.now().isoformat(timespec='minutes'); speichere(s); return
        md = offen[0]
        e = s['verbaende'].setdefault(md, {'name': names.get(md, md), 'status': 'offen', 'naechte': 0, 'minuten': 0})
        e['naechte'] += 1 if e.get('letzte_nacht') != now.date().isoformat() else 0
        e['letzte_nacht'] = now.date().isoformat()
        log(f'{md} {e["name"]}: crawl.py')
        rc, mins = lauf(['crawl.py', '--mandanten', md, '--warten', '30', '--ende', ende], 'crawl')
        e['minuten'] = round(e['minuten'] + mins); speichere(s)
        if rc == 3: log(f'{md}: Zeitgrenze beim Abruf ({mins:.0f} min), weiter in der nächsten Nacht'); break
        if rc == 75: log(f'{md}: fussball.de-Sperre belegt, Nacht beendet'); break
        if rc != 0: log(f'{md}: crawl.py ABBRUCH (Code {rc}, siehe erst_crawl.log) – Nacht beendet, nichts weiter abgerufen'); e['status'] = 'abbruch'; speichere(s); break
        log(f'{md}: Abruf fertig ({mins:.0f} min), venues.py')
        rc, mins2 = lauf(['venues.py', '--ende', ende], 'venues')
        e['minuten'] = round(e['minuten'] + mins2); speichere(s)
        if rc == 3: log(f'{md}: Zeitgrenze bei venues.py, weiter in der nächsten Nacht'); break
        if rc != 0: log(f'{md}: venues.py Fehler (Code {rc}, siehe erst_venues.log) – Nacht beendet'); break
        lauf(['ligaebenen_entwurf.py', '--mandant', md, '--schreiben'], 'ligaebenen')
        try: e['bilanz'] = bilanz(md)
        except Exception as ex: e['bilanz'] = {'fehler': f'{type(ex).__name__}: {ex}'}
        e['status'] = 'fertig'; e['fertig'] = datetime.now().isoformat(timespec='minutes'); speichere(s)
        log(f'{md} {e["name"]} FERTIG nach {e["naechte"]} Nacht/Nächten, {e["minuten"]} min: {e["bilanz"]}')
    log('Nacht beendet')

if __name__ == '__main__':
    main()
