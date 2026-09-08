#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
export PYTHONPATH="$ROOT"
"$ROOT/venv/bin/python" -m unittest tests.test_storage_singleton_reconcile
"$ROOT/venv/bin/python" "$ROOT/scripts/storage_singleton_reconcile.py"
remaining=$("$ROOT/venv/bin/python" - <<'PY'
from app.db.session import SessionLocal
from sqlalchemy import text
s=SessionLocal(); n=s.execute(text("select count(*) from (select 1 from storage group by account_id,key having count(*)>1) q")).scalar(); s.close(); print(int(n or 0))
PY
)
[ "$remaining" = "0" ] || { echo "STORAGE_SINGLETON_DUPLICATES=$remaining"; exit 2; }
echo STORAGE_SINGLETON_GUARD_PASS
