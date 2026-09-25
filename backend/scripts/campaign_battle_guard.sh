#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
LOG=/var/log/boris/campaign_battle_guard.log
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
{
  echo "[$STAMP] CAMPAIGN_BATTLE_GUARD START"
  if ! flock -w 240 /run/lock/boris-backend-rolling.lock -c true; then
    echo "[$STAMP] deploy_quiescence FAIL"
    exit 90
  fi
  echo "[$STAMP] deploy_quiescence PASS"
  env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/campaign_launcher_contract_regression.py"
  echo "[$STAMP] launcher PASS"
  env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/campaign_one_card_battle_audit.py"
  echo "[$STAMP] battle PASS"
  env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_media_quality_audit.py"
  echo "[$STAMP] media PASS"
  "$ROOT/venv/bin/python" "$ROOT/production_contract_runner.py" | tail -n 1
  echo "[$STAMP] production_contract PASS"
  curl -fsS --max-time 15 -H 'Cache-Control: no-cache' "https://boris-ai.pro/health?campaign_battle=$(date +%s%N)" >/dev/null
  echo "[$STAMP] public_health PASS"
  echo "[$STAMP] CAMPAIGN_BATTLE_GUARD PASS"
} >"$TMP" 2>&1 || {
  rc=$?
  sudo -n mkdir -p /var/log/boris
  sudo -n tee -a "$LOG" <"$TMP" >/dev/null
  logger -t BORIS_CAMPAIGN_BATTLE_GUARD "FAIL rc=$rc; see $LOG" || true
  exit "$rc"
}
sudo -n mkdir -p /var/log/boris
sudo -n tee -a "$LOG" <"$TMP" >/dev/null
