#!/bin/bash
set -euo pipefail
cd /root/BORIS/backend
# DB_BACKUP_SAFE_ENV_PARSE_V1: .env is data, not shell code. Sourcing it made
# cron backups fail when ordinary values contained spaces. Read only DATABASE_URL.
DATABASE_URL="${DATABASE_URL:-$(./venv/bin/python - <<'PYENV'
from pathlib import Path
for raw in Path('.env').read_text(encoding='utf-8').splitlines():
    line=raw.strip()
    if line.startswith('DATABASE_URL='):
        print(line.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))
        break
PYENV
)}"
[ -n "$DATABASE_URL" ] || { echo 'DATABASE_URL missing'; exit 2; }
BACKUP_DIR=/root/BORIS/backups
mkdir -p "$BACKUP_DIR"; chmod 750 "$BACKUP_DIR"
exec 9>/run/lock/boris-db-backup.lock; flock -n 9 || { echo 'backup already running'; exit 0; }
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/boris_db_$STAMP.sql.gz"; TMP="$OUT.partial"
trap 'rm -f "$TMP"' EXIT
pg_dump "$DATABASE_URL" | gzip -1 > "$TMP"
gzip -t "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$OUT"
sync -d "$BACKUP_DIR" 2>/dev/null || true
trap - EXIT
# Keep seven immutable daily logical backups. Restore QA validates each new one.
mapfile -t old < <(ls -1t "$BACKUP_DIR"/boris_db_*.sql.gz 2>/dev/null | tail -n +8)
((${#old[@]}==0)) || rm -f -- "${old[@]}"
echo "$(date '+%Y-%m-%d %H:%M') backup ready: $OUT ($(du -h "$OUT" | cut -f1))"
