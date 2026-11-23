#!/usr/bin/env python3
"""Erstbefüllung bundesweit, Land für Land, verteilt auf mehrere Nächte (Timer klobasa-de-erst.timer, 22:00).

  .venv/bin/python -I scripts/erstbefuellung.py [--start-bis 07:00] [--status] [--freigabe]

Je Verband (kleine zuerst): crawl.py (Menü, Spielpläne, Druckansicht, Vereinsspielpläne) -> venues.py ->
ligaebenen_entwurf.py (Entwurf aus Europlan-Stufen, geprüfte Einträge bleiben). Die Verbände werden der Reihe nach
abgearbeitet; ein neuer Verband beginnt nur zwischen 22:00 und --start-bis (07:00), der laufende wird danach sauber zu
Ende gebracht, die nächste Nacht macht mit dem folgenden weiter.
Vorrang: Der tägliche Lauf (01:30) hält vorrang.lock. crawl.py/venues.py laufen mit --nachgeben, sichern dann ihren Stand
und enden mit Code 4; die Erstbefüllung gibt pipeline.lock frei, wartet, bis der tägliche Lauf fertig ist, und setzt
denselben Schritt fort (--frisch 12: schon geholte Spielpläne/Druckansichten nicht noch einmal).
Sperre: Bei 403/429/Prüfseite stoppt crawl.py sofort (Code 2) und legt fussballde_gesperrt.txt an; dann ruft nichts mehr
fussball.de ab (auch der tägliche Lauf nicht), bis Felix freigibt: erstbefuellung.py --freigabe.
Bei 5 Fehlschlägen in Folge (Code 1) bleibt die Erstbefüllung ebenfalls bis zur Freigabe stehen.
Notbremse: crawl.py/venues.py bekommen --ende 21:30 (vor dem nächsten Start um 22:00).
Stand: ~/klobasa-de-data/erstbefuellung.json, Log: ~/klobasa-de-data/logs/erstbefuellung.log
"""
import fcntl, json, os, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sperren import SPERRDATEI, gesperrt, vorrang_abwarten, vorrang_aktiv  # noqa: E402

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.path.join(DATA, 'klobasa.sqlite')
STATE = os.path.join(DATA, 'erstbefuellung.json')
LOG = os.path.join(DATA, 'logs', 'erstbefuellung.log')
PY = [os.path.join(REPO, '.venv', 'bin', 'python'), '-I']
# klein -> groß (grob nach Zahl der Europlan-Plätze im Land); 89 = DFB-Ebene (Bundesliga bis Regionalliga), 63 ist schon da
NOTBREMSE = '21:30'  # spätestens hier hören crawl.py/venues.py auf (nächster Start 22:00)
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

def pipeline_sperre(lock, warten_s):
    bis = time.time() + warten_s
    while True:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); return True
        except BlockingIOError:
            if time.time() > bis: return False
            time.sleep(15)

def pause(lock):
    """Täglichen Lauf vorlassen: pipeline.lock freigeben, warten, bis er fertig ist, Sperre wieder nehmen."""
    log('Pause: täglicher Lauf hat Vorrang')
    fcntl.flock(lock, fcntl.LOCK_UN)
    time.sleep(5)
    vorrang_abwarten()
    if not pipeline_sperre(lock, 3600):
        log('pipeline.lock nach dem täglichen Lauf 60 min nicht frei – Nacht beendet'); return False
    log('täglicher Lauf fertig, weiter')
    return True

def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    s = lade()
    if '--status' in sys.argv:
        g = gesperrt()
        if g: print('GESPERRT:', g)
        for md in REIHENFOLGE:
            e = s['verbaende'].get(md, {})
            print(md, e.get('name', ''), e.get('status', 'offen'), e.get('bilanz', ''))
        return
    if '--freigabe' in sys.argv:
        g = gesperrt()
        if g: os.remove(SPERRDATEI)
        los = [md for md, e in s['verbaende'].items() if e.get('status') in ('abbruch', 'gesperrt')]
        for md in los: s['verbaende'][md]['status'] = 'offen'
        speichere(s)
        log(f'Freigabe von Hand: Sperre {"aufgehoben (" + g + ")" if g else "war nicht gesetzt"}, wieder offen: {los or "–"}')
        return
    start_bis = sys.argv[sys.argv.index('--start-bis') + 1] if '--start-bis' in sys.argv else '07:00'
    h, m = map(int, start_bis.split(':'))
    now = datetime.now()
    grenze = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now.hour >= 22: grenze += timedelta(days=1)
    elif now >= grenze:
        log(f'außerhalb des Startfensters (22:00–{start_bis}), nichts getan'); return
    elif now.hour >= 12:
        log(f'außerhalb des Startfensters (22:00–{start_bis}), nichts getan'); return
    g = gesperrt()
    if g:
        log(f'fussball.de GESPERRT seit {g} – keine Abrufe bis zur Freigabe (erstbefuellung.py --freigabe)'); return
    gesp = [md for md, e in s['verbaende'].items() if e.get('status') in ('abbruch', 'gesperrt')]
    if gesp:
        log(f'Abbruch bei {gesp} in einer früheren Nacht – keine weiteren Abrufe bis zur Freigabe (erstbefuellung.py --freigabe)'); return
    lock = open(os.path.join(DATA, 'pipeline.lock'), 'w')
    if vorrang_aktiv(): vorrang_abwarten()
    if not pipeline_sperre(lock, 3600):
        log('pipeline.lock nach 60 min nicht frei (täglicher Lauf?), Nacht ausgelassen'); return
    names = {}
    try: names = {k.lstrip('_'): v for k, v in json.load(open(os.path.join(DATA, 'phase0', 'wam_base.json')))['Mandanten'].items()}
    except Exception: pass
    log(f'Nacht beginnt, neue Verbände bis {start_bis}, Notbremse {NOTBREMSE}')
    while True:
        if time.time() >= grenze.timestamp():
            log(f'{start_bis} erreicht, kein neuer Verband mehr'); break
        offen = [md for md in REIHENFOLGE if s['verbaende'].get(md, {}).get('status') != 'fertig']
        if not offen:
            log('ALLE VERBÄNDE FERTIG – Timer kann auf bundesweit umgestellt werden'); s['alle_fertig'] = datetime.now().isoformat(timespec='minutes'); speichere(s); return
        md = offen[0]
        e = s['verbaende'].setdefault(md, {'name': names.get(md, md), 'status': 'offen', 'naechte': 0, 'minuten': 0})
        heute = (datetime.now() - timedelta(hours=12)).date().isoformat()   # Nacht = Datum des Abends
        e['naechte'] += 1 if e.get('letzte_nacht') != heute else 0
        e['letzte_nacht'] = heute
        stop = False
        for schritt, args in (('crawl', ['crawl.py', '--mandanten', md, '--warten', '30', '--ende', NOTBREMSE, '--nachgeben', '--frisch', '12']),
                              ('venues', ['venues.py', '--ende', NOTBREMSE, '--nachgeben'])):
            log(f'{md} {e["name"]}: {args[0]}')
            while True:
                rc, mins = lauf(args, schritt)
                e['minuten'] = round(e['minuten'] + mins); speichere(s)
                if rc != 4: break
                if not pause(lock): return
            if rc == 0:
                log(f'{md}: {args[0]} fertig ({mins:.0f} min)'); continue
            stop = True
            if rc == 3: log(f'{md}: Notbremse {NOTBREMSE} bei {args[0]}, weiter in der nächsten Nacht')
            elif rc == 75: log(f'{md}: fussball.de-Sperre belegt, Nacht beendet')
            elif rc == 2:
                e['status'] = 'gesperrt'; speichere(s)
                log(f'{md}: fussball.de GESPERRT ({gesperrt()}) – sofort gestoppt, keine Abrufe bis zur Freigabe '
                    '(erstbefuellung.py --freigabe)')
            elif schritt == 'crawl':
                e['status'] = 'abbruch'; speichere(s)
                log(f'{md}: crawl.py ABBRUCH (Code {rc}, siehe erst_crawl.log) – keine Abrufe bis zur Freigabe')
            else: log(f'{md}: venues.py Fehler (Code {rc}, siehe erst_venues.log) – Nacht beendet')
            break
        if stop: break
        lauf(['ligaebenen_entwurf.py', '--mandant', md, '--schreiben'], 'ligaebenen')
        try: e['bilanz'] = bilanz(md)
        except Exception as ex: e['bilanz'] = {'fehler': f'{type(ex).__name__}: {ex}'}
        e['status'] = 'fertig'; e['fertig'] = datetime.now().isoformat(timespec='minutes'); speichere(s)
        log(f'{md} {e["name"]} FERTIG nach {e["naechte"]} Nacht/Nächten, {e["minuten"]} min: {e["bilanz"]}')
    log('Nacht beendet')

if __name__ == '__main__':
    main()
