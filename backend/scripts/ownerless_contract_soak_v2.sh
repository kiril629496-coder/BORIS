#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
CYCLES=${BORIS_SOAK_CYCLES:-12}
INTERVAL=${BORIS_SOAK_INTERVAL:-300}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG=${BORIS_SOAK_LOG:-$ROOT/logs/ownerless_contract_soak_v2_${STAMP}.log}
mkdir -p "$ROOT/logs"
exec >>"$LOG" 2>&1

echo "OWNERLESS_SOAK_V2 START $(date -u +%FT%TZ) cycles=$CYCLES interval=$INTERVAL"

wait_roll_quiet(){
  local i
  for i in $(seq 1 120); do
    if flock -n /run/lock/boris-backend-rolling.lock -c true; then
      echo "ROLL_QUIET PASS attempt=$i"
      return 0
    fi
    sleep 2
  done
  echo "ROLL_QUIET FAIL busy>240s"
  return 1
}

health_retry(){
  local url="$1" i
  for i in $(seq 1 30); do
    if curl -fsS --max-time 8 -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "${url}?soak=$(date +%s%N)" >/dev/null; then
      echo "HEALTH PASS url=$url attempt=$i"
      return 0
    fi
    sleep 2
  done
  echo "HEALTH FAIL url=$url"
  return 1
}

for cycle in $(seq 1 "$CYCLES"); do
  echo "=== cycle=$cycle $(date -u +%FT%TZ) ==="
  wait_roll_quiet
  health_retry http://127.0.0.1:8000/health
  health_retry http://127.0.0.1:8001/health
  health_retry https://boris-ai.pro/health
  PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/production_contract_runner.py"
  PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_visual_contract_regression.py"
  PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/banner_internal_idempotency_regression.py"
  PYTHONPATH="$ROOT" BORIS_TEST_MODE=1 "$ROOT/venv/bin/python" -m unittest \
    tests.test_billing_concurrency_guard tests.test_plan_item_paid_safety tests.test_paid_image_artifact_guard
  PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_media_quality_audit.py"
  if [ $((cycle % 3)) -eq 1 ]; then
    PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/campaign_launcher_contract_regression.py"
    PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/campaign_one_card_battle_audit.py"
  fi
  systemctl is-active boris-avito-visual-guard.timer >/dev/null
  df -P / | tail -1
  echo "CYCLE PASS cycle=$cycle $(date -u +%FT%TZ)"
  if [ "$cycle" -lt "$CYCLES" ]; then sleep "$INTERVAL"; fi
done

echo "OWNERLESS_SOAK_V2 PASS $(date -u +%FT%TZ)"
echo "LOG=$LOG"
