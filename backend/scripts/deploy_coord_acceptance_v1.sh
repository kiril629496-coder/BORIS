#!/bin/bash
set -u
LOG=logs/deploy_coord_acceptance_v1.log
: > "$LOG"
for n in $(seq 1 12); do
  echo "=== $n $(date -Is) ===" >> "$LOG"
  systemctl show boris-backend.service boris-backend-replica.service -p ActiveEnterTimestamp --value >> "$LOG"
  curl -fsS --max-time 4 http://127.0.0.1:8000/health >> "$LOG" 2>&1 || echo PRIMARY_FAIL >> "$LOG"
  echo >> "$LOG"
  curl -fsS --max-time 4 http://127.0.0.1:8001/health >> "$LOG" 2>&1 || echo REPLICA_FAIL >> "$LOG"
  echo >> "$LOG"
  tail -n 1 logs/money_policy_guard.log >> "$LOG"
  ps -eo pid,ppid,etimes,args | grep -E "rolling_restart_backend.sh|max150_autopilot_soak|kpi_formula_v13_ownerless" | grep -v grep >> "$LOG" || true
  sleep 300
done
echo "ACCEPTANCE_DONE $(date -Is)" >> "$LOG"
