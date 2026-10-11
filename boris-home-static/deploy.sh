#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/BORIS/boris-home-static"
SRC="$ROOT/index.source.html"
LIVE="/var/www/boris-home/index.html"
BACKUPS="/var/www/boris-home/source-backups"

if [[ ! -f "$SRC" ]]; then
  echo "BORIS_HOME_SOURCE_MISSING"
  exit 2
fi

python3 "$ROOT/check.py" "$SRC"

mkdir -p "$BACKUPS"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -f "$LIVE" ]]; then
  cp -a "$LIVE" "$BACKUPS/index.html.$stamp"
fi

tmp="/var/www/boris-home/.index.html.$stamp.tmp"
install -m 0644 "$SRC" "$tmp"
mv -f "$tmp" "$LIVE"

python3 "$ROOT/check.py" "$LIVE"
echo "BORIS_HOME_DEPLOY_PASS"
sha256sum "$SRC" "$LIVE"
