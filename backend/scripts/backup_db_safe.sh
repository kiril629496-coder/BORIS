#!/bin/bash
set -euo pipefail
cd /root/BORIS/backend
DATABASE_URL="${DATABASE_URL:-$(./.venv/bin/python - <<'PY'
from pathlib import Path
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if line.startswith('DATABASE_URL='):
        print(line.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))
        break
PY
)}"
[ -n "$DATABASE_URL" ] || { echo 'DATABASE_URL missing'; exit 2; }
BACKUP_DIR=/root/BORIS/backend/backups/schema
mkdir -p "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/boris_db_$STAMP.sql.gz"
TMP="$OUT.partial"
trap 'rm -f "$TMP"' EXIT
pg_dump "$DATABASE_URL" | gzip -1 > "$TMP"
gzip -t "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
mapfile -t old < <(ls -1t "$BACKUP_DIR"/boris_db_*.sql.gz 2>/dev/null | tail -n +8)
((${#old[@]}==0)) || rm -f -- "${old[@]}"
echo "SAFE_DB_BACKUP=PASS $OUT $(stat -c %s "$OUT")"
