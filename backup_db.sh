#!/bin/bash
set -e
cd /root/BORIS/backend
set -a; . ./.env; set +a
BACKUP_DIR=/root/BORIS/backups
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/boris_db_$STAMP.sql.gz"
pg_dump "$DATABASE_URL" | gzip > "$OUT"
# оставить только 7 последних
ls -1t "$BACKUP_DIR"/boris_db_*.sql.gz | tail -n +8 | xargs -r rm -f
echo "$(date '+%Y-%m-%d %H:%M') бэкап готов: $OUT ($(du -h "$OUT" | cut -f1))"
