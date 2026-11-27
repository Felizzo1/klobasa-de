#!/usr/bin/env python3
"""ntfy-Meldung von der Kommandozeile (gleicher Kanal wie sperren.melde und Uptime Kuma).

  .venv/bin/python -I scripts/melde.py TITEL TEXT [PRIORITÄT]       Priorität: min, low, default, high (Standard), urgent
  .venv/bin/python -I scripts/melde.py --dienst UNIT                 Meldung über einen fehlgeschlagenen systemd-Benutzerdienst
                                                                     (für OnFailure=klobasa-de-fehler@%n.service)
Exit 1, wenn die Nachricht nicht ankam.
"""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sperren import melde  # noqa: E402

LOGS = {  # Dienst -> Log, dessen letzte Zeile die Meldung ergänzt (Logs enthalten keine Personendaten)
    'klobasa-de.service': '~/klobasa-de-data/logs/taeglich.log',
    'klobasa-de-erst.service': '~/klobasa-de-data/logs/erstbefuellung.log',
    'klobasa-de-bfv.service': '~/klobasa-de-data/logs/bfv.log',
    'klobasa-de-bfv-nacht3.service': '~/klobasa-de-data/logs/bfv.log',
}

def dienst(unit):
    try:
        info = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'Result', '-p', 'ExecMainStatus', '--value'],
                              capture_output=True, text=True, timeout=20).stdout.split()
    except Exception:
        info = []
    text = f'Dienst {unit} ist fehlgeschlagen'
    if len(info) >= 2:
        text += f' (Ergebnis {info[0]}, Code {info[1]})'
    log = LOGS.get(unit)
    if log:
        try:
            zeilen = [z.strip() for z in open(os.path.expanduser(log), encoding='utf-8') if z.strip()]
            if zeilen:
                text += f'.\nLetzte Logzeile: {zeilen[-1][:300]}'
        except OSError:
            pass
    text += f'\nDetails: journalctl --user -u {unit}'
    return f'Klobasa-Planer: {unit.removesuffix(".service")} fehlgeschlagen', text

if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == '--dienst':
        titel, text, prio = *dienst(a[1]), 'high'
    elif len(a) in (2, 3):
        titel, text, prio = a[0], a[1], a[2] if len(a) == 3 else 'high'
    else:
        raise SystemExit(__doc__)
    fehler = melde(titel, text, prio)
    if fehler:
        print(f'ntfy nicht gesendet ({fehler})', file=sys.stderr); sys.exit(1)
