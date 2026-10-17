#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEB=$(find "$ROOT/build" -maxdepth 1 -type f -name 'dfr_*_all.deb' -print -quit 2>/dev/null || true)
if [ -z "$DEB" ]; then
  echo "Paquet introuvable. Lancez d'abord scripts/build-deb.sh." >&2
  exit 1
fi
exec sudo apt install "$DEB"
