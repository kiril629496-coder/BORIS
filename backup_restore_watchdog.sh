#!/bin/bash
set -euo pipefail
B=/root/BORIS/backups
LATEST=$(ls -1t "$B"/boris_db_*.sql.gz 2>/dev/null | head -1 || true)
[ -n "$LATEST" ] || exit 2
if python3 - "$B/restore_qa_status.json" "$LATEST" <<'PY'
import json,sys,os
try:d=json.load(open(sys.argv[1]))
except Exception: raise SystemExit(1)
raise SystemExit(0 if d.get('ok') and d.get('backup')==os.path.basename(sys.argv[2]) else 1)
PY
then echo RESTORE_WATCHDOG=OK; exit 0; fi
systemctl start boris-backup-restore-qa.service
echo RESTORE_WATCHDOG=RETRIGGERED
