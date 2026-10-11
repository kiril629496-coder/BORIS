#!/bin/bash
set -u
LOG=/root/BORIS/backend/logs/legacy_autonomy_sweeper.log
mkdir -p /root/BORIS/backend/logs
patterns=(
  "scripts/ownerless_marketer_75m_v3.py"
  "scripts/ownerless_marketer_60m_v2.py"
  "max150_autopilot_soak.sh"
  "production_watch_90m_v1.py"
  "rollout_runtime_integrity"
  "kpi_formula_v13_ownerless"
)
now=$(date -u +%FT%TZ)
ps -eo pid=,args= | while read -r pid args; do
  [ -n "${pid:-}" ] || continue
  for pat in "${patterns[@]}"; do
    case "$args" in
      *"$pat"*)
        echo "$now TERMINATE pid=$pid pattern=$pat args=$args" >> "$LOG"
        kill -TERM "$pid" 2>/dev/null || true
        break
        ;;
    esac
  done
done
