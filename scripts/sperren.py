"""Gemeinsame Sperren der Pipeline (crawl.py, venues.py, erstbefuellung.py, taeglich.sh).

pipeline.lock      immer nur ein Lauf (täglicher Lauf oder Erstbefüllung) gleichzeitig
fussballde.lock    nie zwei Zugriffe auf fussball.de gleichzeitig (crawl.py)
vorrang.lock       hält taeglich.sh exklusiv, solange er läuft oder wartet: die Erstbefüllung (--nachgeben) prüft das
                   vor jedem Abruf, pausiert sauber und macht nach dem täglichen Lauf weiter
fussballde_gesperrt.txt  entsteht bei 403/429/Prüfseite; solange sie existiert, ruft crawl.py nichts ab
                   (Freigabe nur durch Felix: erstbefuellung.py --freigabe)
Benachrichtigung: melde() schickt eine Push-Nachricht an den ntfy-Kanal aus ~/.config/uptime-kuma/ntfy-topic (ntfy.sh).
"""
import fcntl, os, urllib.request
from datetime import datetime

DATA = os.path.expanduser('~/klobasa-de-data')
VORRANG = os.path.join(DATA, 'vorrang.lock')
SPERRDATEI = os.path.join(DATA, 'fussballde_gesperrt.txt')
NTFY_TOPIC = os.path.expanduser('~/.config/uptime-kuma/ntfy-topic')

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

def melde(titel, text, prio='high'):
    """Push über ntfy.sh (Kanal aus NTFY_TOPIC, nie geloggt). Gibt None oder den Fehlertyp zurück, wirft nie."""
    try:
        topic = open(NTFY_TOPIC, encoding='utf-8').read().strip()
        req = urllib.request.Request(f'https://ntfy.sh/{topic}', data=text.encode('utf-8'), method='POST',
                                     headers={'Title': titel, 'Priority': prio, 'Tags': 'warning'})
        urllib.request.urlopen(req, timeout=20).read()
        return None
    except Exception as e:
        return type(e).__name__

def sperren(grund):
    with open(SPERRDATEI, 'w', encoding='utf-8') as f:
        f.write(f'{datetime.now():%F %T} {grund}\n')
    return melde('Klobasa-Planer: fussball.de gesperrt',
                 f'{grund}\nAlle Abrufe gestoppt, die Seite behält den alten Stand.\n'
                 'Freigabe: cd ~/klobasa-de && .venv/bin/python -I scripts/erstbefuellung.py --freigabe')
