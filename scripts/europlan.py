#!/usr/bin/env python3
"""Sammelt alle Stadien eines Landes von europlan-online.de (Name, Verein, Koordinaten, Link, Liga mit Stufe).

  python3 scripts/europlan.py LAND-ID AUSGABE [--limit N]
  python3 scripts/europlan.py 1 ~/klobasa-de-data/europlan/europlan_de.json      # Deutschland (nicht ins Repo)

Fortsetzbar: Jede geholte Liga wird sofort in einem Zwischenstand (JSON-Lines) neben dem Datenordner gespeichert
(~/klobasa-de-data/europlan/ligen_<LAND>.jsonl). Ein neuer Aufruf überspringt bereits geholte Ligen.
Bewusst langsam: eine Seite alle 4 Sekunden. Bei Status 403/429 oder 5 Fehlschlägen in Folge Abbruch.
"""
import argparse, html, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

BASE = 'https://www.europlan-online.de/'
DELAY = 4.0
UA = 'Klobasa-Planer-DE/0.1 (privates Projekt, langsamer Abruf)'
STATE_DIR = os.path.expanduser('~/klobasa-de-data/europlan')

class Blocked(Exception):
    pass

last = 0.0
def get(url):
    global last
    for i in range(3):
        wait = last + DELAY - time.time()
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f'Status {e.code} bei {url}')
            print('  Fehler', url, e, file=sys.stderr, flush=True)
        except Exception as e:
            print('  Fehler', url, e, file=sys.stderr, flush=True)
        time.sleep(15 * (i + 1))
    return None

MARKER = re.compile(
    r'fromLonLat\(\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]\s*\).*?'
    r'<strong>(.*?)<\\/strong><br\\/>(.*?)<br\\/><a href="([^"]*?stadion-(\d+)\.html)"', re.S)
LIGA_LINK = re.compile(r'<a href="index\.php\?s=liga&(?:amp;)?id=(\d+)"[^>]*>([^<]*?)\s*\((\d+)\)\s*<')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('land', type=int)
    ap.add_argument('out')
    ap.add_argument('--limit', type=int, default=0, help='nur die ersten N fehlenden Ligen holen (Test)')
    a = ap.parse_args()
    os.makedirs(STATE_DIR, exist_ok=True)
    state = os.path.join(STATE_DIR, f'ligen_{a.land}.jsonl')
    done = {}
    if os.path.exists(state):
        for line in open(state, encoding='utf-8'):
            if line.strip():
                d = json.loads(line); done[d['id']] = d

    land = get(f'{BASE}index.php?s=land&id={a.land}')
    if not land:
        raise SystemExit('Länderseite nicht erreichbar')
    ligen = {}
    for lid, name, stufe in LIGA_LINK.findall(land):
        ligen.setdefault(int(lid), (html.unescape(name).strip(), int(stufe)))
    for lid in set(int(x) for x in re.findall(r's=liga&(?:amp;)?id=(\d+)', land)) - set(ligen):
        ligen[lid] = ('', None)
    todo = [l for l in sorted(ligen) if l not in done]
    print(f'{len(ligen)} Ligen, {len(done)} schon geholt, {len(todo)} offen', flush=True)
    if a.limit:
        todo = todo[:a.limit]

    fails = 0
    with open(state, 'a', encoding='utf-8') as st:
        for k, lid in enumerate(todo, 1):
            try:
                h = get(f'{BASE}index.php?s=liga&id={lid}')
            except Blocked as e:
                print('ABBRUCH:', e, '– Stand gesichert', flush=True); break
            if not h:
                fails += 1
                if fails >= 5:
                    print('ABBRUCH: 5 Fehlschläge in Folge – Stand gesichert', flush=True); break
                continue
            fails = 0
            h1 = html.unescape((re.search(r'<h1[^>]*>(.*?)<', h, re.S) or [None, ''])[1]).strip()
            stad = [{'club': html.unescape(c).strip(), 'name': html.unescape(n).strip(), 'id': int(sid), 'href': href,
                     'lat': round(float(lat), 5), 'lon': round(float(lon), 5)}
                    for lon, lat, c, n, href, sid in MARKER.findall(h)]
            d = {'id': lid, 'name': ligen[lid][0] or h1, 'h1': h1, 'stufe': ligen[lid][1], 'stadien': stad}
            st.write(json.dumps(d, ensure_ascii=False) + '\n'); st.flush()
            done[lid] = d
            print(f'{k}/{len(todo)} [{lid}] {d["name"]} (Stufe {d["stufe"]}): {len(stad)} Stadien', flush=True)

    # Ausgabe aus allem, was bisher geholt ist
    stadien, leagues = {}, []
    for lid in sorted(done):
        d = done[lid]
        leagues.append({'id': lid, 'name': d['name'], 'stufe': d['stufe'], 'entries': [[s['club'], s['id']] for s in d['stadien']]})
        for s in d['stadien']:
            e = stadien.setdefault(s['id'], {'id': s['id'], 'name': s['name'], 'lat': s['lat'], 'lon': s['lon'],
                                             'href': s['href'], 'clubs': [], 'ligen': []})
            if s['club'] and s['club'] not in e['clubs']:
                e['clubs'].append(s['club'])
            if d['name'] and d['name'] not in e['ligen']:
                e['ligen'].append(d['name'])
    data = {'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source': BASE, 'country': a.land,
            'complete': len(done) >= len(ligen), 'leagues_total': len(ligen),
            'stadiums': sorted(stadien.values(), key=lambda s: s['id']), 'leagues': leagues}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    print(f'fertig: {len(done)}/{len(ligen)} Ligen, {len(stadien)} Stadien, '
          f'{sum(len(l["entries"]) for l in leagues)} Verein-Platz-Einträge -> {a.out}', flush=True)

if __name__ == '__main__':
    main()
