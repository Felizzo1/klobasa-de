"""Gemeinsame Sperren der Pipeline (crawl.py, venues.py, erstbefuellung.py, taeglich.sh).

pipeline.lock      immer nur ein Lauf (täglicher Lauf oder Erstbefüllung) gleichzeitig
fussballde.lock    nie zwei Zugriffe auf fussball.de gleichzeitig (crawl.py)
vorrang.lock       hält taeglich.sh exklusiv, solange er läuft oder wartet: die Erstbefüllung (--nachgeben) prüft das
                   vor jedem Abruf, pausiert sauber und macht nach dem täglichen Lauf weiter
fussballde_gesperrt.txt  entsteht bei 403/429/Prüfseite; solange sie existiert, ruft crawl.py nichts ab
                   (Freigabe nur durch Felix: erstbefuellung.py --freigabe)
"""
import fcntl, os
from datetime import datetime

DATA = os.path.expanduser('~/klobasa-de-data')
VORRANG = os.path.join(DATA, 'vorrang.lock')
SPERRDATEI = os.path.join(DATA, 'fussballde_gesperrt.txt')

def vorrang_aktiv():
    """True, wenn der tägliche Lauf gerade läuft oder auf die Pipeline wartet."""
    try:
        with open(VORRANG, 'a') as fd:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True

def vorrang_abwarten():
    """Blockiert, bis der tägliche Lauf fertig ist."""
    with open(VORRANG, 'a') as fd:
        fcntl.flock(fd, fcntl.LOCK_SH)
        fcntl.flock(fd, fcntl.LOCK_UN)

def gesperrt():
    """Grund der Sperre (Text) oder None."""
    try:
        return open(SPERRDATEI, encoding='utf-8').read().strip() or 'gesperrt'
    except FileNotFoundError:
        return None

def sperren(grund):
    with open(SPERRDATEI, 'w', encoding='utf-8') as f:
        f.write(f'{datetime.now():%F %T} {grund}\n')
