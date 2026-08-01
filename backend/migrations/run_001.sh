#!/bin/bash
# run_001.sh test  — сухой прогон на боевой БД внутри транзакции с ROLLBACK (данные не меняются)
# run_001.sh apply — боевое применение с COMMIT (перед этим делается бэкап)
set -e

MODE="${1:-}"
if [ "$MODE" != "test" ] && [ "$MODE" != "apply" ]; then
    echo "Использование: bash run_001.sh test   (сухой прогон, откат)"
    echo "               bash run_001.sh apply  (боевое применение)"
    exit 2
fi

cd /root/BORIS/backend
DIR=/root/BORIS/backend/migrations
URL=$(grep -m1 '^DATABASE_URL' .env | cut -d= -f2- | sed 's/+asyncpg//; s/+psycopg2//; s/^"//; s/"$//')

if [ -z "$URL" ]; then
    echo "ОШИБКА: DATABASE_URL не найден в .env"
    exit 1
fi

TMP=$(mktemp /tmp/mig001_XXXX.sql)
trap 'rm -f "$TMP"' EXIT

echo "BEGIN;" > "$TMP"
cat "$DIR/001_email_verification.sql" >> "$TMP"
cat "$DIR/verify_001.sql" >> "$TMP"

if [ "$MODE" = "test" ]; then
    echo "\\echo '=== СУХОЙ ПРОГОН: откатываю, боевые данные не тронуты ==='" >> "$TMP"
    echo "ROLLBACK;" >> "$TMP"
else
    echo "COMMIT;" >> "$TMP"
    echo "=== БЭКАП БАЗЫ ПЕРЕД БОЕВЫМ ПРИМЕНЕНИЕМ ==="
    bash /root/BORIS/backup_db.sh
    ls -lt /root/BORIS/backups | head -3
fi

echo "=== СОСТОЯНИЕ ДО ==="
psql "$URL" -P pager=off -c "SELECT id, role, is_active, subscription_expires_at FROM users ORDER BY id"

echo "=== ПРОГОН (${MODE}) ==="
psql "$URL" -P pager=off -v ON_ERROR_STOP=1 -f "$TMP"

echo "=== ГОТОВО: ${MODE} ==="
