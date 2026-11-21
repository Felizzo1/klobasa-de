#!/usr/bin/env python3
"""Entwurf für data/ligaebenen.json: (Mandant, Mannschaftsart, Gebiet, Spielklasse) -> Ligaebene 1..13.

  .venv/bin/python scripts/ligaebenen_entwurf.py [--mandant 02,43] [--schreiben] [--vergleich]

Vorschlag Herren (aus den Europlan-Stufen):
  Jede Staffel wird der Europlan-Liga zugeordnet, in der die meisten ihrer Mannschaften stehen (Vereinsname normiert,
  Mannschaftszusatz II/III/2/3 muss passen; mindestens 3 Mannschaften und 30 % der Staffel). Je Spielklasse und Gebiet
  gilt die häufigste Stufe ihrer Staffeln. Ohne Treffer: dieselbe Spielklasse in anderen Gebieten des Verbands,
  sonst die Kette (Verband -> Bezirk -> Kreis) unterhalb der bekannten Stufen.
Frauen (Europlan führt keine Frauenligen): Bundesliga 1, 2. Bundesliga 2, Regionalliga 3, dann die Kette ab 4.
Vorhandene Einträge mit "geprueft": true bleiben unverändert. Staffelnamen stehen nur in der Prüfliste außerhalb des Repos.
--vergleich: geprüfte Einträge neu berechnen und Abweichungen zeigen (Test der Regel), nichts schreiben.
"""
import json, os, re, sqlite3, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import club_key, fold

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DATA = os.path.expanduser('~/klobasa-de-data')
DB = os.environ.get('KLOBASA_DB') or os.path.join(DATA, 'klobasa.sqlite')
EP_PATH = os.path.join(DATA, 'europlan', 'europlan_de.json')
OUT = os.path.join(ROOT, 'data', 'ligaebenen.json')
PRUEF = os.path.join(DATA, 'ligaebenen_pruefliste.tsv')

# Reihenfolge der Spielklassennamen (oben -> unten), für Kette und Rückfall
RANG = [r'^bundesliga', r'^2\.? ?bundesliga', r'^3\.? ?liga', r'regionalliga', r'oberliga(?!.*kreis)(?!.*bezirk)',
        r'verbandsliga', r'landesliga', r'landesklasse', r'bezirksoberliga', r'bezirksliga', r'bezirksklasse',
        r'kreisoberliga', r'kreisliga \(?a|1\.? ?kreisliga|^kreisliga$', r'kreisliga \(?b|2\.? ?kreisliga',
        r'kreisliga \(?c|3\.? ?kreisliga', r'kreisliga \(?d|4\.? ?kreisliga', r'^kreisklasse$|1\.? ?kreisklasse|kreisklasse a',
        r'2\.? ?kreisklasse|kreisklasse b', r'3\.? ?kreisklasse|kreisklasse c', r'kreisklasse']

def rang(name):
    n = fold(name)
    for i, p in enumerate(RANG):
        if re.search(p, n):
            return i
    return len(RANG)

def ebene(gebiet, verband):
    g = fold(gebiet)
    if g.startswith('kreis') or ' kreis' in g: return 2
    if g.startswith('bezirk') or g.startswith('region'): return 1
    return 0

RES = re.compile(r'\s(ii|iii|iv|v|[2-6])\.?(?=\s|$)', re.I)
ROEM = {'ii': 2, 'iii': 3, 'iv': 4, 'v': 5}

def team_key(name):
    """„SV Mölkau 04 II“ -> („moelkau“, 2); „Vorwärts Epe 1923 II (Ground A)“ -> („vorwaerts epe“, 2)."""
    n = re.sub(r'\(.*?\)', ' ', name or '')
    n = re.sub(r'\bzg\.?\s*$', ' ', n).strip()
    res = 1
    m = None
    for m in RES.finditer(' ' + n):
        pass
    if m and m.end() >= len(n) - 6:  # Zusatz am Ende (ggf. gefolgt von „9er“ o. Ä.)
        t = m.group(1).lower(); res = ROEM.get(t) or int(t)
        n = (' ' + n)[:m.start()]
    return club_key(n), res

def main():
    nur = set(next((a.split('=', 1)[1] if '=' in a else sys.argv[sys.argv.index(a) + 1]
                    for a in sys.argv if a.startswith('--mandant')), '').split(',')) - {''}
    vergleich = '--vergleich' in sys.argv
    c = sqlite3.connect(DB)
    staffeln = c.execute('''SELECT staffel_id, mandant, mandant_name, art, gebiet, spielklasse, name FROM staffeln
                            WHERE im_menue=1 ORDER BY name''').fetchall()
    teams = defaultdict(set)
    for sid, h, g in c.execute('''SELECT p.staffel_id, p.heim, p.gast FROM spiele p JOIN staffeln s USING(staffel_id)
                                  WHERE s.im_menue=1 AND p.status!='verschwunden' '''):
        teams[sid].update(x for x in (h, g) if x)
    # Europlan: Mannschaft -> Ligen
    ep = json.load(open(EP_PATH, encoding='utf-8'))
    liga = {l['id']: l for l in ep['leagues'] if l.get('stufe')}
    idx = defaultdict(set)
    for l in liga.values():
        for nm, _ in l['entries']:
            k = team_key(nm)
            if k[0]: idx[k].add(l['id'])
    # Staffel -> Europlan-Stufe
    st_ep = {}
    for sid, m, mn, art, geb, sk, name in staffeln:
        if art != 'Herren' or not teams[sid]: continue
        cnt = Counter()
        for t in teams[sid]:
            k = team_key(t)
            if k[0]:
                cnt.update(idx.get(k, ()))
        if cnt:
            lid, n = cnt.most_common(1)[0]
            if n >= 3 and n >= 0.3 * len(teams[sid]):
                st_ep[sid] = (liga[lid]['stufe'], n, len(teams[sid]), liga[lid]['name'])
    # Gruppen
    grp = defaultdict(list)
    for s in staffeln:
        grp[(s[1], s[3], s[4], s[5])].append(s)
    vorschlag = {}
    for k, ss in grp.items():
        if k[1] != 'Herren': continue
        werte = [st_ep[s[0]][0] for s in ss if s[0] in st_ep]
        if werte:
            st, n = Counter(werte).most_common(1)[0]
            vorschlag[k] = (st, f'Europlan: {n} von {len(ss)} Staffeln Stufe {st}'
                                + (f' (andere: {sorted(set(werte) - {st})})' if len(set(werte)) > 1 else ''))
    # Rückfall 1: gleiche Spielklasse im selben Verband (andere Gebiete)
    by_sk = defaultdict(list)
    for (m, art, geb, sk), (st, _) in vorschlag.items():
        by_sk[(m, art, sk)].append(st)
    for k in grp:
        if k[1] == 'Herren' and k not in vorschlag and by_sk.get((k[0], k[1], k[3])):
            st = Counter(by_sk[(k[0], k[1], k[3])]).most_common(1)[0][0]
            vorschlag[k] = (st, 'gleiche Spielklasse in anderen Gebieten (Europlan)')
    # Rückfall 2: Kette je Verband/Art: Verband -> Bezirk -> Kreis, Rang nach Namen
    for (m, art) in {(k[0], k[1]) for k in grp}:
        ks = [k for k in grp if k[0] == m and k[1] == art]
        mname = grp[ks[0]][0][2]
        start = {'Herren': 6, 'Frauen': 4}[art] if art in ('Herren', 'Frauen') else 6
        unten = {}  # Ebene (0/1/2) -> tiefste vergebene Stufe
        for e in (0, 1, 2):
            gebiete = defaultdict(list)
            for k in ks:
                if ebene(k[2], mname) == e: gebiete[k[2]].append(k)
            for geb, gk in gebiete.items():
                gk.sort(key=lambda k: rang(k[3]))
                basis = max([unten[x] for x in unten if x < e], default=start - 1)
                for i, k in enumerate(gk):
                    if k in vorschlag: continue
                    n = fold(k[3])
                    if art == 'Frauen' and re.match(r'^2\.? ?bundesliga', n): st = 2
                    elif art == 'Frauen' and n.startswith('bundesliga'): st = 1
                    elif art == 'Frauen' and 'regionalliga' in n: st = 3
                    else:
                        # direkt unter der nächsthöheren bekannten Klasse im selben Gebiet, sonst unter der Ebene darüber
                        drueber = [vorschlag[x][0] for x in gk[:i] if x in vorschlag]
                        st = (max(drueber) + 1) if drueber else basis + 1 + i
                    vorschlag[k] = (st, f'Kette ({["Verband", "Bezirk", "Kreis"][e]}, Platz {i + 1} im Gebiet)')
                vals = [vorschlag[k][0] for k in gk if vorschlag.get(k, (None,))[0]]
                if vals: unten[e] = max(unten.get(e, 0), max(vals))

    alt = {}
    if os.path.exists(OUT):
        for e in json.load(open(OUT, encoding='utf-8'))['eintraege']:
            alt[(e['mandant'], e['art'], e['gebiet'], e['spielklasse'])] = e
    out = []; namen = {}
    for k, ss in sorted(grp.items(), key=lambda kv: (kv[0][0], kv[0][1], ebene(kv[0][2], ''), kv[0][2], rang(kv[0][3]))):
        namen[k] = ' / '.join(s[6] for s in ss)
        st, grund = vorschlag.get(k, (None, 'unbekannt'))
        e = {'mandant': k[0], 'verband': ss[0][2], 'art': k[1], 'gebiet': k[2], 'spielklasse': k[3], 'stufe': st,
             'geprueft': False, 'grund': grund}
        if vergleich:
            if k in alt and alt[k].get('geprueft') and (not nur or k[0] in nur):
                a = alt[k]['stufe']
                print(f"{'OK ' if a == st else 'ABW'} {k[1]:6} {k[2][:28]:28} {k[3]:18} geprüft {a}  Entwurf {st}  ({grund})")
            continue
        if k in alt and alt[k].get('geprueft'):
            e = alt[k]
        out.append(e)
    if vergleich: return
    # Einträge anderer Verbände, die gerade nicht im Menü sind, nicht verlieren
    da = {(e['mandant'], e['art'], e['gebiet'], e['spielklasse']) for e in out}
    out += [e for k, e in alt.items() if k not in da]
    for e in out:
        if nur and e['mandant'] not in nur: continue
        k = (e['mandant'], e['art'], e['gebiet'], e['spielklasse'])
        print(f"{e['verband'][:12]:12} {e['art']:6} {e['stufe'] or '?':>2} {'✓' if e['geprueft'] else ' '} "
              f"{e['gebiet'][:28]:28} {e['spielklasse'][:22]:22} {e['grund']}")
    if '--schreiben' in sys.argv:
        out.sort(key=lambda e: (e['mandant'], e['art'], ebene(e['gebiet'], ''), e['gebiet'], rang(e['spielklasse'])))
        json.dump({'hinweis': 'Ligaebene je (Mandant, Mannschaftsart, Gebiet, Spielklasse). Entwurf aus scripts/ligaebenen_entwurf.py; '
                              'geprueft=true schützt einen Eintrag vor dem Überschreiben.', 'eintraege': out},
                  open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        # Prüfliste mit Staffelnamen (fussball.de-Daten) und Europlan-Liga nur außerhalb des Repos
        with open(PRUEF, 'w', encoding='utf-8') as f:
            f.write('verband\tart\tstufe\tgeprueft\tgebiet\tspielklasse\tgrund\tstaffeln (Europlan-Liga)\n')
            for e in out:
                k = (e['mandant'], e['art'], e['gebiet'], e['spielklasse'])
                det = ' / '.join(s[6] + (f" [{st_ep[s[0]][3]}: {st_ep[s[0]][1]}/{st_ep[s[0]][2]}]" if s[0] in st_ep else '')
                                 for s in grp.get(k, []))
                f.write(f"{e['verband']}\t{e['art']}\t{e['stufe']}\t{'ja' if e['geprueft'] else ''}\t{e['gebiet']}\t"
                        f"{e['spielklasse']}\t{e['grund']}\t{det}\n")
        print('geschrieben:', OUT, len(out), 'Einträge; Prüfliste:', PRUEF)

if __name__ == '__main__':
    main()
