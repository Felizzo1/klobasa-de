#!/bin/bash
# Baut die Seite und veröffentlicht sie ohne Datenhistorie auf dem verwaisten Zweig gh-pages (ein einziger Commit, Force-Push).
#   scripts/deploy.sh --lokal    nur bauen, Daten im Klartext (zum Testen auf dem Pi, nie veröffentlichen)
#   scripts/deploy.sh            bauen, verschlüsselt (KLOBASA_PASSWORT aus ~/.config/klobasa-de/env)
#   scripts/deploy.sh --push     bauen, verschlüsselt, auf gh-pages pushen (vor dem ersten Mal Felix fragen)
# Arbeitsverzeichnis: ~/klobasa-de-data/build/site (nicht im Repo).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
B=~/klobasa-de-data/build
OUT=$B/site
MODE=${1:-}
[ -f ~/.config/klobasa-de/env ] && set -a && . ~/.config/klobasa-de/env && set +a
mkdir -p "$B"
DATEN=$B/daten-neu.$$
if [ "$MODE" = "--lokal" ]; then
  env -u KLOBASA_PASSWORT "$REPO/.venv/bin/python" -I "$REPO/scripts/preprocess.py" --out "$DATEN" --klartext
else
  : "${KLOBASA_PASSWORT:?KLOBASA_PASSWORT fehlt (~/.config/klobasa-de/env)}"
  "$REPO/.venv/bin/python" -I "$REPO/scripts/preprocess.py" --out "$DATEN"
fi
# Seite frisch zusammenstellen: Dateien aus site/ (ohne die alte Gesamtdatei kreise.json) + neue Daten
NEU=$B/site-neu.$$
mkdir -p "$NEU"
rsync -a --exclude kreise.json "$REPO/site/" "$NEU/"
mv "$DATEN" "$NEU/daten"
touch "$NEU/.nojekyll"
# altes Ergebnis nicht löschen, sondern als site.vorher behalten (eine Generation)
if [ -d "$OUT" ]; then rm -rf "$B/site.vorher"; mv "$OUT" "$B/site.vorher"; fi
mv "$NEU" "$OUT"
du -sh "$OUT" "$OUT/daten"
if [ "$MODE" = "--push" ]; then
  REMOTE=$(git -C "$REPO" remote get-url origin)
  # Deploy-Key des Repos (liegt außerhalb von ~/.ssh), gilt auch für das frische Repo im Build-Ordner
  export GIT_SSH_COMMAND="ssh -i $HOME/.config/klobasa-de/deploy_key -o IdentitiesOnly=yes"
  cd "$OUT"
  rm -rf .git
  git init -q -b gh-pages
  git add -A
  git -c user.name=Claude -c user.email=noreply@anthropic.com commit -q -m "Seite $(date '+%Y-%m-%d %H:%M')"
  git push -f "$REMOTE" gh-pages
  rm -rf .git
fi
