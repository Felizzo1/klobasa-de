"""Gemeinsame Hilfsfunktionen: Normierung von Straßen- und Vereinsnamen, Entfernung."""
import math, re, unicodedata

def fold(s):
    s = s.lower().replace('ß', 'ss').replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue')
    s = unicodedata.normalize('NFKD', s)
    return ''.join(ch for ch in s if not unicodedata.combining(ch))

def street_key(s):
    """„Pirnaer Landstr. 121b“ und „Pirnaer Landstraße“ -> „pirnaer landstr“."""
    s = fold(s or '')
    s = re.sub(r'\s+\d+\s*[a-z]?(\s*[-/]\s*\d+\s*[a-z]?)?\s*$', '', s)  # Hausnummer am Ende
    s = re.sub(r'strasse\b|str\b\.?', 'str', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

# Rechtsformen, Allerweltswörter und Zusätze, die in Vereinsnamen nichts unterscheiden
CLUB_GENERIC = re.compile(r'^(sv|sg|spg|fsv|tsv|tus|vfb|vfl|fc|sc|spvgg|bsg|eintracht|einheit|fortuna|ev|e|v|'
                          r'fv|ssv|ksv|esv|psv|bsc|tsg|tv|tsc|sf|sportfreunde|sportverein|fussballclub|fussballverein|'
                          r'verein|club|1|2|3|ii|iii|iv|i|ffc|fsg|sgm|jsg|usv|lsv|'
                          r'und|von|der|die|das|am|an|im|in|e v|mae|frauen|damen)$')

# Farbkürzel in Vereinsnamen
FARBEN = {'bw': 'blau weiss', 'rw': 'rot weiss', 'gw': 'gruen weiss', 'sw': 'schwarz weiss', 'bg': 'blau gelb'}

def club_key(n):
    """„SV Blau-Gelb Mülsen 2“ -> „muelsen“, „1. FC Magdeburg 1965“ -> „magdeburg“."""
    n = fold(n or '')
    n = re.sub(r'\(.*?\)', ' ', n)
    n = re.sub(r'\bzg\.?$', ' ', n)
    n = re.sub(r'e\.\s*v\.?', ' ', n)
    n = re.sub(r'[^a-z0-9]+', ' ', n)
    n = ' '.join(FARBEN.get(w, w) for w in n.split())   # „SG BW Brachstedt“ = „SG Blau-Weiß Brachstedt“
    return ' '.join(w for w in n.split() if not CLUB_GENERIC.match(w) and not re.fullmatch(r'(18|19|20)\d\d|\d\d', w))
