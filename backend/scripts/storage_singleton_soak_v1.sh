#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
export PYTHONPATH="$ROOT"
cycles=${1:-9}
interval=${2:-300}
for i in $(seq 1 "$cycles"); do
  echo "=== STORAGE_SOAK_CYCLE_${i} $(date -u +%FT%TZ) ==="
  "$ROOT/scripts/storage_singleton_guard.sh"
  "$ROOT/venv/bin/python" -m unittest tests.test_storage_singleton_reconcile tests.test_draft_storage_atomic tests.test_feed_storage_atomic >/dev/null
  for u in http://127.0.0.1:8000/health http://127.0.0.1:8001/health https://boris-ai.pro/health; do
    ok=0
    for n in $(seq 1 12); do curl -fsS --max-time 5 "$u?storage_soak=$(date +%s%N)" >/dev/null && { ok=1; break; }; sleep 2; done
    [ "$ok" = 1 ] || { echo "HEALTH_FAIL $u"; exit 31; }
  done
  echo "STORAGE_SOAK_CYCLE_${i}_PASS"
  [ "$i" -eq "$cycles" ] || sleep "$interval"
done
echo STORAGE_SINGLETON_SOAK_PASS
