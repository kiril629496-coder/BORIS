#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/root/BORIS/backend${PYTHONPATH:+:$PYTHONPATH}
cd /root/BORIS/backend
LOG="logs/release_then_operatorless_soak_$(date -u +%Y%m%dT%H%M%SZ).log"
exec >>"$LOG" 2>&1

echo "START=$(date -u +%FT%TZ)"
echo "GIT_ENABLED=$(systemctl is-enabled boris-gitdeploy.service 2>/dev/null || true)"
echo "GIT_ACTIVE=$(systemctl is-active boris-gitdeploy.service 2>/dev/null || true)"

# Wait for any already-running canonical release. Never overlap rolling restarts.
released=0
for attempt in $(seq 1 24); do
  echo "ROLLING_ATTEMPT=$attempt $(date -u +%FT%TZ)"
  set +e
  ./rolling_restart_backend.sh
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then released=1; break; fi
  if [ "$rc" -ne 19 ]; then echo "ROLLING_FATAL_RC=$rc"; exit "$rc"; fi
  echo "ROLLING_BUSY_WAIT=15s"
  sleep 15
done
[ "$released" -eq 1 ] || { echo "ROLLING_LOCK_TIMEOUT"; exit 19; }

echo '=== POST-RELEASE CONTRACT ==='
./scripts/operatorless_full_contract.sh

# ~40 min repeated proof after successful release.
for cycle in $(seq 1 6); do
  echo "=== SOAK_CYCLE_$cycle $(date -u +%FT%TZ) ==="
  ./scripts/operatorless_full_contract.sh
  [ "$cycle" -eq 6 ] || sleep 480
done

echo "RELEASE_OPERATORLESS_SOAK=PASS"
echo "END=$(date -u +%FT%TZ)"
