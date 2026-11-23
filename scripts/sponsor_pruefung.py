#!/usr/bin/env python3
"""Prüft Ligennamen nach dem Kürzen auf übrig gebliebene Sponsoren (läuft im täglichen Lauf nach deploy.sh).

  .venv/bin/python -I scripts/sponsor_pruefung.py

CZ: lädt data.enc der fotbal-Seite (gleiches Passwort, KLOBASA_PASSWORT), kürzt wie czName() in site/index.html mit der
    Liste site/cz_sponsoren.json. DE: Staffelnamen aus der Datenbank, gekürzt mit liga_name() aus preprocess.py.
Verdächtig nach dem Kürzen: Wörter in Großbuchstaben (außer üblichen Abkürzungen und Ligawörtern), „°“, Firmenzusätze
(s.r.o., GmbH, .cz …) und ein angehängter Teil nach „ - “ oder „|“ ohne Ligawort.
Ausgabe: ~/klobasa-de-data/logs/sponsoren_verdacht.txt (bei jedem Lauf neu) und eine Zeile auf stdout.
"""
import gzip, json, os, re, sqlite3, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preprocess import liga_name  # noqa: E402
from fczcrypt import decrypt  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
OUT = os.path.join(DATA, 'logs', 'sponsoren_verdacht.txt')
CZ_URL = 'https://felizzo1.github.io/fotbal/data.enc'

# ---- CZ: gleiche Regeln wie czName() in site/index.html (bei Änderungen beide anpassen)
SJ = json.load(open(os.path.join(ROOT, 'site', 'cz_sponsoren.json'), encoding='utf-8'))
_sp = sorted(SJ.get('sponsoren', []), key=len, reverse=True)
SP_RE = re.compile(r'(^|[\s\-|,])(?:' + '|'.join(map(re.escape, _sp)) + r')(?=$|[\s\-|,])') if _sp else None
LIGA_RE = re.compile(r'(?:^|\s|-)(\d{1,2}\s*\.?\s*(?:[^\s\d\-|]+\s+){0,2}?(?:liga|lyga)\b)', re.I)

def cz_name(n, cup):
    if cup: return n
    if n in SJ.get('fest', {}): return SJ['fest'][n]
    s = re.sub(r'\s+', ' ', n).strip()
    m = LIGA_RE.search(s)
    if m and m.start() > 0: s = re.sub(r'^[\s\-]+', '', s[m.start():])
    if SP_RE: s = SP_RE.sub(r'\1', s)
    s = re.sub(r'(\d)\s*\.?\s*(liga|LIGA|Liga)\b', r'\1. liga', s, count=1)
    s = re.sub(r'(\d)\.(?=[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])', r'\1. ', s, count=1)
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s*[-|]\s*(?:[-|]\s*)+', ' - ', s)
    s = re.sub(r'^[\s\-|,]+|[\s\-|,]+$', '', s).strip()
    return s or n

# ---- Verdacht
# MPM/MSM: Městský přebor / Městská soutěž mužů (OFS Plzeň-město); DE: Kreis-/Regionskürzel (NWM, MSP, ABI, OTH, KFV …)
OK_CAPS = set('''MPM MSM OFS KFS PKFS JMKFS LKFS FAČR MSLŽ OP OPM KM ČB KH SK PŘEBOR MUŽŮ MUŽI MUŽE ŽEN ŽENY SKUPINA TŘÍDA OKRESNÍ
  KFV SN NWM MSP ABI OTH WM KRAJSKÝ VÝCHOD ZÁPAD SEVER JIH STŘED LIGA DOSPĚLÍ DOSPĚLÝCH II III IV VI VII VIII IX XI IA IB NWM SG SPG FSV TSV SV FC'''.split())
LIGAWORT = re.compile(r'(?i)skupin|sk\.|třída|tř\.|přebor|muž|žen|dospěl|podzim|jaro|okresní|krajsk|východ|západ|sever|jih|'
                      r'střed|ost|west|nord|süd|staffel|st\.|herren|frauen|runde|quali|meister|platzierung|abstieg|aufstieg|'
                      r'gruppe|^[a-z]$|^\d+$|ská$|ské$|sko$')
FIRMA = re.compile(r'(?i)s\.r\.o\.|a\.s\.|gmbh|\bag\b|\bkg\b|\.cz\b|\.de\b|\.com\b|\bgroup\b|\bsport\b|°')

def verdacht(name):
    gr = []
    caps = [w for w in re.findall(r'[A-Za-zÀ-žß.]+', name) if len(w.strip('.')) >= 3 and w.strip('.').isupper()
            and w.replace('.', '').upper() not in OK_CAPS]
    if caps: gr.append('Großbuchstaben: ' + ' '.join(caps))
    if FIRMA.search(name): gr.append('Firmenzusatz/°')
    teile = re.split(r'\s+[-|–]\s+|\s*\|\s*', name)
    if len(teile) > 1:
        rest = teile[-1].strip()
        if rest and rest.upper() not in OK_CAPS and not any(LIGAWORT.search(w) for w in rest.split()) and not re.search(r'(?i)liga|klasse', rest):
            gr.append(f'angehängt: „{rest}“')
    return gr

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    zeilen = []; n_cz = n_de = 0
    # CZ
    pw = os.environ.get('KLOBASA_PASSWORT')
    if pw:
        try:
            req = urllib.request.Request(CZ_URL, headers={'User-Agent': 'Klobasa-Planer-DE/0.1 (privates Projekt)'})
            d = json.loads(gzip.decompress(decrypt(urllib.request.urlopen(req, timeout=60).read(), pw)))
            for orig, cup in sorted({(c[0], c[3]) for c in d['comps']}):
                if cup: continue
                kurz = cz_name(orig, cup); g = verdacht(kurz)
                if g: n_cz += 1; zeilen.append(f'CZ\t{kurz}\t{orig}\t{"; ".join(g)}')
        except Exception as e:
            zeilen.append(f'CZ\t–\t–\tnicht prüfbar: {type(e).__name__}')
    else:
        zeilen.append('CZ\t–\t–\tnicht prüfbar: KLOBASA_PASSWORT fehlt')
    # DE
    c = sqlite3.connect(os.path.join(DATA, 'klobasa.sqlite'))
    for orig, sk in c.execute('SELECT DISTINCT name, spielklasse FROM staffeln WHERE im_menue=1 ORDER BY name'):
        kurz = liga_name(orig, sk); g = verdacht(kurz)
        if g: n_de += 1; zeilen.append(f'DE\t{kurz}\t{orig}\t{"; ".join(g)}')
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('# Ligennamen, die nach dem Kürzen noch nach Sponsor aussehen. Nachtragen: site/cz_sponsoren.json (CZ) '
                'bzw. liga_name() in scripts/preprocess.py (DE)\nland\tgekürzt\toriginal\tgrund\n')
        f.write('\n'.join(zeilen) + '\n')
    print(f'Sponsor-Verdacht: CZ {n_cz}, DE {n_de} (siehe {OUT})')

if __name__ == '__main__':
    main()
