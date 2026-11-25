#!/usr/bin/env python3
"""Ver- und Entschlüsselung im Format der Seite und der Erweiterung.

Format: b"FCZ1" + salt(16) + iv(12) + AES-256-GCM(ciphertext + tag)
Schlüssel: PBKDF2-HMAC-SHA256(passwort, salt, 310000 Iterationen)

Aufruf:
  fczcrypt.py encrypt IN OUT   (Passwort aus Umgebungsvariable KLOBASA_PASSWORT, z. B. aus ~/.config/klobasa-de/env)
  fczcrypt.py decrypt IN OUT
"""
import os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

MAGIC = b'FCZ1'
ITER = 310000

def key(pw: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode('utf-8'))

_KEYS = {}

def encrypt(data: bytes, pw: str, salt: bytes = None) -> bytes:
    """salt=None: neues Salz je Datei. Mit festem Salz (ein Bau, viele Pakete) wird der Schlüssel nur einmal abgeleitet,
    auch auf der Seite (sie merkt sich den Schlüssel je Salz). Der IV ist immer neu."""
    salt = salt or os.urandom(16)
    iv = os.urandom(12)
    k = _KEYS.get((pw, salt)) or _KEYS.setdefault((pw, salt), key(pw, salt))
    return MAGIC + salt + iv + AESGCM(k).encrypt(iv, data, None)

def decrypt(blob: bytes, pw: str) -> bytes:
    if blob[:4] != MAGIC:
        raise SystemExit('Unbekanntes Dateiformat')
    salt, iv, ct = blob[4:20], blob[20:32], blob[32:]
    try:
        return AESGCM(key(pw, salt)).decrypt(iv, ct, None)
    except Exception:
        raise SystemExit('Entschlüsselung fehlgeschlagen: falsches Passwort?')

if __name__ == '__main__':
    mode, src, dst = sys.argv[1:4]
    pw = os.environ.get("KLOBASA_PASSWORT")
    if not pw:
        raise SystemExit('KLOBASA_PASSWORT fehlt')
    data = open(src, 'rb').read()
    open(dst, 'wb').write(encrypt(data, pw) if mode == 'encrypt' else decrypt(data, pw))
