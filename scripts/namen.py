"""Gemeinsame Namensregeln für Staffeln (crawl.py, preprocess.py, ligaebenen_entwurf.py)."""
import re

_W = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*"
_PERSON = re.compile(rf"^(?:Dr\.\s+)?{_W}(?:\s+{_W}){{1,2}}$")
# Wörter, die in Klammerzusätzen von Ligen vorkommen und keine Personen sind
_KEIN_NAME = re.compile(r"(?i)staffel|gruppe|liga|klasse|kreis|bezirk|nord|süd|ost|west|mitte|kleinfeld|herren|frauen|damen|"
                        r"junior|reserve|stadt|land|runde|rasen|platz|halle|winter|herbst|frühjahr|saison|meister|pokal|"
                        r"^(?:ober|unter|nieder|hoch|hohe|alt|neu)")

def ohne_personen_klammer(name):
    """Entfernt Klammerzusätze, die wie ein Personenname aussehen (z. B. Staffelleiter):
    „Kreisliga Frauen (Kerstin Grothe-Benkenstein)“ -> „Kreisliga Frauen“. „(Kleinfeld)“, „(Staffel Nord)“ bleiben."""
    def weg(m):
        inhalt = m.group(1).strip()
        if _PERSON.match(inhalt) and not any(_KEIN_NAME.search(w) for w in inhalt.replace('-', ' ').split()):
            return ''
        return m.group(0)
    return re.sub(r'\s+', ' ', re.sub(r'\s*\(([^()]*)\)', weg, name or '')).strip()

_KLEIN = re.compile(r'(?i)\b[79]er\b|kleinfeld')

def ist_kleinfeld(name):
    """Kleinfeld-/7er-/9er-Staffel (keine Ligaebene, eigene Kategorie)."""
    return bool(_KLEIN.search(name or ''))
