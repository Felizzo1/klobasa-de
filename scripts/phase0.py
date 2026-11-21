#!/usr/bin/env python3
"""Phase 0: Erreichbarkeit von fussball.de vom Raspi prüfen.

Langsam: eine Anfrage nach der anderen, 1,5–3 s Pause. Rohseiten landen in OUTDIR (außerhalb des Repos).
Schiedsrichternamen werden nicht ausgegeben (nur der Teil nach „Spielstätte:“).
"""
import io, json, random, re, sys, time, html as H
from pathlib import Path
import requests
from fontTools.ttLib import TTFont

BASE = 'https://www.fussball.de'
UA = 'Klobasa-Planer-DE/0.1 (privates Projekt)'
STAFFEL = '031B6082V0000008VS5489BUVV628VP4-G'
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
OUT.mkdir(parents=True, exist_ok=True)

S = requests.Session()
S.headers['User-Agent'] = UA
last = 0.0

def get(path, name=None):
    global last
    wait = last + random.uniform(1.5, 3.0) - time.time()
    if wait > 0:
        time.sleep(wait)
    t = time.time()
    r = S.get(BASE + path if path.startswith('/') else path, timeout=60)
    last = time.time()
    blocked = r.status_code in (403, 429) or bool(re.search(rb'captcha|cf-challenge|Access Denied', r.content[:20000], re.I))
    print(f'{r.status_code}  {len(r.content):>8} B  {time.time()-t:5.2f}s  {r.headers.get("content-type","")[:40]:40}  {"BLOCK? " if blocked else ""}{path}')
    if name:
        (OUT / name).write_bytes(r.content)
    if r.status_code in (403, 429):
        raise SystemExit('Abbruch: Status %d' % r.status_code)
    return r

NAMES = {'zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9',
         'colon':':','period':'.','comma':',','space':' ','hyphen':'-','slash':'/'}
fonts = {}
def decoder(key):
    if key not in fonts:
        b = get(f'/export.fontface/-/format/woff/id/{key}/type/font', f'font_{key}.woff').content
        cmap = TTFont(io.BytesIO(b)).getBestCmap()
        fonts[key] = {cp: NAMES.get(g, g if len(g) == 1 else '') for cp, g in cmap.items()}
        print('   Font', key, len(cmap), 'Zeichen, Glyphen z. B.', sorted(set(cmap.values()))[:20])
    t = fonts[key]
    return lambda s: ''.join(t.get(ord(c), c) for c in s)

OBF = re.compile(r'<span[^>]*data-obfuscation="([^"]+)"[^>]*>(.*?)</span>', re.S)
def deobf(h):
    return OBF.sub(lambda m: decoder(m.group(1))(H.unescape(m.group(2))), h)

def text(h):
    return re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', ' ', h))).strip()

# 1 + 2: Menü
base = get('/wam_base.json', 'wam_base.json').json()
print('   currentSaison', base.get('currentSaison'), '| Mandanten', len(base.get('Mandanten', {})), '| CompetitionTypes', base.get('CompetitionTypes'))
saison = base['currentSaison']
kinds = get(f'/wam_kinds_63_{saison}_1.json', 'wam_kinds_63.json').json()
print('   Mannschaftsarten Sachsen:', kinds.get('Mannschaftsart'))

# 3: Gesamtspielplan
sp = get(f'/spielplan/sachsenklasse-ost-sachsen-sachsenklasse-herren-saison2627-sachsen/-/staffel/{STAFFEL}', 'spielplan.html').text
keys = sorted(set(re.findall(r'data-obfuscation="([^"]+)"', sp)))
print('   Keys:', keys, '| Spiel-Links:', len(set(re.findall(r'/spiel/[^"]*?/-/spiel/([A-Z0-9]+)', sp))))

# 4: Fonts + Entschlüsseln
d = deobf(sp)
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', d, re.S)
lines = [text(r) for r in rows]
for ref in ['SG Dresden Striesen', 'Post SV Dresden']:
    for i, l in enumerate(lines):
        if ref in l and ':' in l:
            print('   REF', i, l[:160])
dates = re.findall(r'\b(?:Mo|Di|Mi|Do|Fr|Sa|So), \d\d\.\d\d\.\d\d\b', ' '.join(lines))
times = re.findall(r'\b\d\d:\d\d\b', ' '.join(lines))
print('   Daten gefunden:', len(dates), 'Zeiten:', len(times))
(OUT / 'spielplan_zeilen.txt').write_text('\n'.join(lines), encoding='utf-8')

# 5: Druckansicht – nur Spielstätten ausgeben
dr = get(f'/spieltagsuebersicht.druck/-/max/999/mode/PRINT/staffel/{STAFFEL}', None).text
dd = text(deobf(dr))
venues = re.findall(r'Spielstätte:\s*(.*?)(?=verlegt vom:|Schiedsrichter|Spielstätte:|\b(?:Mo|Di|Mi|Do|Fr|Sa|So), \d\d\.|$)', dd)
print('   Druckansicht: Spielstätten', len(venues))
for v in venues[:12]:
    print('    ', v.strip()[:120])
# Druckansicht ohne Schiedsrichter sichern
(OUT / 'druck_spielstaetten.txt').write_text('\n'.join(v.strip() for v in venues), encoding='utf-8')

# 6: ajax.fixturelist
fx = get(f'/ajax.fixturelist/-/staffel/{STAFFEL}', 'fixturelist.html').text
print('   fixturelist: Spiel-Links', len(set(re.findall(r'/-/spiel/([A-Z0-9]+)', fx))), '| Keys', sorted(set(re.findall(r'data-obfuscation="([^"]+)"', fx))))
(OUT / 'fixturelist_zeilen.txt').write_text('\n'.join(text(r) for r in re.findall(r'<tr[^>]*>(.*?)</tr>', deobf(fx), re.S)), encoding='utf-8')

# Slug-Test
get(f'/spielplan/egal/-/staffel/{STAFFEL}', 'spielplan_egal.html')
get(f'/spielplan/-/staffel/{STAFFEL}', 'spielplan_ohneslug.html')
