"""Positivliste der Ligatypen: Auf die Seite kommen nur Staffeln, deren Spielklasse UND Staffelname einen bekannten Ligatyp
nennen. Alles andere landet in der Prüfliste (preprocess.py -> ~/klobasa-de-data/logs/ligatyp_pruefliste.tsv).

Bekannt sind:
- die Ligatypen selbst (Bundesliga, 3. Liga, Regionalliga, Oberliga, Verbandsliga, Landesliga, Landesklasse, Bezirksoberliga,
  Bezirksliga, Bezirksklasse, Kreisoberliga, Kreisliga [A–D], Kreisklasse [A–D], A-/B-/C-Klasse, Gruppenliga, Stadtliga …),
  auch mit „Frauen“/„Herren“ davor („Frauenkreisliga“) und als übliche Kürzel (KL, KLA, KK, BZL, KOL, LL, LK, AK, BK …)
- regionale Ligen, deren Name die eigene Region trägt: „Erzgebirgsliga“, „Ostfrieslandklasse A“, „Bördeoberliga“,
  „Westsachsenliga“ – der Wortteil vor -liga/-klasse muss zum Gebiet (Kreis/Bezirk) oder zum Landesverband passen
- Runden einer bekannten Spielklasse („Meisterrunde“, „Platzierungsrunde“) und Kleinfeld-Staffeln („Kleinfeld 7er Staffel A“)

Warum auch der Name: fussball.de gibt Freizeitligen eine normale Spielklasse („Freizeitliga - Gruppe 1 + 2“, Kreis Essen,
Spielklasse „Kreisliga D“). „Freizeit“ ist keine Region, also Prüfliste.

Ausnahmen von Hand (echte Ligen mit ungewöhnlichem Namen): data/ligatyp_freigabe.json, {"staffeln": {"<STAFFEL-ID>": "Grund"}}.
"""
import json, os, re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
FREIGABE = os.path.join(ROOT, 'data', 'ligatyp_freigabe.json')

_TYP = r'''
  (?:2\.|3\.)?\s*bundesliga | [23]\.\s*liga | regionalliga | oberliga | verbandsliga | landesliga | landesklasse
| landesoberliga | bezirksoberliga | bezirksliga | bezirksklasse | kreisoberliga | kreisliga | kreisklasse | gruppenliga
| stadtoberliga | stadtliga | stadtklasse | sonderklasse | regionalklasse | regionenliga | reserveklasse | reserveliga
| (?:landes|bezirks|kreis|frauen|herren)?meisterschaft
| [abcde]\s?-?\s?(?:klasse|liga) | [abcde]\d?-\d?\s?klasse
'''
TYP_RE = re.compile(rf'(?ix)(?<![a-zäöüß])(?:frauen|herren)?(?:{_TYP})(?![a-zäöüß])')
KURZ_RE = re.compile(r'(?<![A-Za-zÄÖÜäöü])(?:F|R)?(?:KKL?|KK[ABC]|KL[ABCD]?|BZL|BzL|BOL|KOL|LL|LK|GL|VL|OL|AK|BK|CK|FSK)'
                     r'(?=$|[\s\-./(\d])')
# oberste Verbandsligen mit Landesnamen (Spielklasse bei fussball.de)
SK_REGIONAL = re.compile(r'(?i)^(?:bayern|hessen|rheinland|saarland|niederrhein|mittelrhein|westfalen|sachsen|thüringen|brandenburg'
                         r'|berlin|bremen|hamburg|schleswig-holstein|baden-württemberg|südwest|nordost)-?liga$')
RUNDE_RE = re.compile(r'(?i)meisterr+unde|platzierungsrunde|abstiegsrunde|aufstiegsrunde|relegation|entscheidungsspiel|qualifikation')
KLEIN_RE = re.compile(r'(?i)kleinfeld|\b[79]er\b')
# Wortteil vor -liga/-klasse: „Ostfrieslandklasse“ -> „Ostfriesland“, „Bördekreisklasse“ -> „Börde“, „Harzoberliga“ -> „Harz“
KOMPOSITUM = re.compile(r'(?i)([a-zäöüß][a-zäöüß/\-]*?)(?:s?-?(?:ober|kreis|bezirks)?(?:liga|klasse))(?![a-zäöüß])')
# alleinstehendes „Liga“/„Klasse“ mit Region im Namen: „Herren Dahme/Fläming Liga“, „Schröder-Liga Saar“
ALLEIN = re.compile(r'(?i)(?:^|[\s\-])(?:liga|klasse)(?![a-zäöüß])')
HIMMEL = re.compile(r'(?i)^(?:nord|süd|ost|west)+(?:-)?')


def _stamm(w):
    w = HIMMEL.sub('', w.lower().strip('-/ '))
    return w[:-1] if w.endswith('s') and len(w) > 5 else w   # Genitiv-s („Erzgebirgs“)


def _regionen(gebiet, mandant_name):
    toks = set()
    for t in re.split(r'[\s/\-,()]+', f'{gebiet or ""} {mandant_name or ""}'):
        t = _stamm(t)
        if len(t) >= 4 and t not in ('kreis', 'bezirk', 'region', 'verband', 'land'):
            toks.add(t)
    return toks


def _freigabe():
    try:
        return json.load(open(FREIGABE, encoding='utf-8')).get('staffeln', {})
    except FileNotFoundError:
        return {}


class Ligatyp:
    def __init__(self):
        self.frei = _freigabe()

    def pruefe(self, staffel_id, name, spielklasse, gebiet='', mandant_name='', name_ohne_sponsor=''):
        """None = bekannte Liga; sonst Grund für die Prüfliste."""
        if staffel_id in self.frei:
            return None
        if not (TYP_RE.search(spielklasse or '') or SK_REGIONAL.search(spielklasse or '')):
            return 'Spielklasse kein bekannter Ligatyp'
        for n in {re.sub(r'\s+', ' ', name or ''), name_ohne_sponsor or ''}:
            if not n:
                continue
            if TYP_RE.search(n) or KURZ_RE.search(n) or RUNDE_RE.search(n) or KLEIN_RE.search(n):
                return None
            reg = _regionen(gebiet, mandant_name)
            for m in KOMPOSITUM.finditer(n):
                p = _stamm(m.group(1))
                if len(p) >= 4 and any(p in r or r in p for r in reg):
                    return None
            if ALLEIN.search(n) and any(r in _stamm(w) or _stamm(w) in r for w in re.split(r'[\s/\-,()]+', n)
                                           for r in reg if len(_stamm(w)) >= 4):
                return None
        return 'Staffelname nennt keinen Ligatyp'
