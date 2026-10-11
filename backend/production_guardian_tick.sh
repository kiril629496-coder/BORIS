#!/bin/bash
set -u
cd /root/BORIS/backend
exec 9>/run/lock/boris-production-guardian.lock
flock -n 9 || exit 0
LOG=/root/BORIS/backend/.boris_logs/persistent_guardian.log
mkdir -p "$(dirname "$LOG")"
log(){ printf '%s\n' "$*" >> "$LOG"; }
log "=== $(date -u +%FT%TZ) ==="
# GUARDIAN_ATOMIC_LOG_V1: never redirect to an empty path. Every autonomous
# action is logged to the persistent guardian journal.
# HA invariant: backend replicas are healed only through canonical rolling.
for s in boris-frontend.service boris-deploy.service boris-background-worker.service boris-crm-sync.service boris-telephony-runtime.service; do
  st="$(systemctl is-active "$s" 2>/dev/null || true)"
  log "$s=$st"
  [ "$st" = active ] || systemctl restart "$s" >> "$LOG" 2>&1 || true
done
for s in boris-backend.service boris-backend-replica.service; do
  st="$(systemctl is-active "$s" 2>/dev/null || true)"
  log "$s=$st"
done
curl -fsS http://127.0.0.1:8000/health >> "$LOG" 2>&1 || true; echo >> "$LOG"
curl -fsS http://127.0.0.1:8001/health >> "$LOG" 2>&1 || true; echo >> "$LOG"

# PRODUCTION_CONTRACT_NO_PARALLEL_DEPLOY_V2:
# A global contract is expensive and must never compete with canonical rollout.
# Defer this guardian tick when ANY other contract, scoped deploy or HA rolling
# owner is active; the timer will verify the finished generation on the next tick.
DEFER_REASON=""
if pgrep -f 'production_contract_runner.py' >/dev/null 2>&1; then
  DEFER_REASON="contract_active"
elif pgrep -f '^/bin/bash /root/BORIS/backend/rolling_restart_backend.sh$' >/dev/null 2>&1; then
  DEFER_REASON="rolling_active"
elif pgrep -f 'boris-backend-scoped-deploy[^ ]*\.py .*/scoped-release-requests/' >/dev/null 2>&1; then
  DEFER_REASON="scoped_deploy_active"
fi
if [ -n "$DEFER_REASON" ]; then
  log "PRODUCTION_CONTRACT_DEFERRED=$DEFER_REASON"
  exit 0
fi

CONTRACT_TMP="$(mktemp /tmp/boris_guardian_contract.XXXXXX)"
FINAL_CONTRACT_TMP="$(mktemp /tmp/boris_guardian_contract_final.XXXXXX)"
trap 'rm -f "$CONTRACT_TMP" "$FINAL_CONTRACT_TMP"' EXIT
BORIS_POLICY_NOTIFY=0 BORIS_POLICY_SELF_HEAL=0 ./venv/bin/python production_contract_runner.py > "$CONTRACT_TMP" 2>&1
CONTRACT_RC=$?
cat "$CONTRACT_TMP" >> "$LOG"
# GUARDIAN_BACKEND_RECONCILE_DELEGATED_TO_DEPLOY_V1: one owner for backend rolling.
# Guardian observes drift and keeps deploy alive; it never rolls API replicas itself.
if grep -q '"backend_runtime_generation": false' "$CONTRACT_TMP"; then
  log "BACKEND_RECONCILE_OWNED_BY_DEPLOY"
  systemctl is-active --quiet boris-deploy.service || systemctl restart boris-deploy.service >> "$LOG" 2>&1 || true
fi
# GUARDIAN_NONBACKEND_SOURCE_QUIET_V1: do not restart long-lived workers or
# rebuild frontend while another production task is actively writing source.
# A restart during source churn creates generation ping-pong and transient port
# loss. Defer one guardian tick; the next tick converges after a 30s quiet window.
source_quiet_30s(){
  local newest now
  newest="$(find /root/BORIS/backend/app /root/BORIS/backend -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' \) -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)"
  now="$(date +%s)"
  [ -n "$newest" ] && [ $((now-newest)) -ge 30 ]
}
frontend_quiet_30s(){
  local newest now
  newest="$(find /root/BORIS/frontend -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' -o -name '*.css' -o -name '*.html' \) -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)"
  now="$(date +%s)"
  [ -z "$newest" ] || [ $((now-newest)) -ge 30 ]
}
if source_quiet_30s; then
  if grep -q '"background_runtime_generation": false' "$CONTRACT_TMP"; then systemctl restart boris-background-worker.service >> "$LOG" 2>&1 || true; fi
  if grep -q '"telephony_runtime_generation": false' "$CONTRACT_TMP"; then systemctl restart boris-telephony-runtime.service >> "$LOG" 2>&1 || true; fi
  if grep -q '"crm_runtime_generation": false' "$CONTRACT_TMP"; then systemctl restart boris-crm-sync.service >> "$LOG" 2>&1 || true; fi
  if grep -q '"deploy_runtime_generation": false' "$CONTRACT_TMP"; then systemctl restart boris-deploy.service >> "$LOG" 2>&1 || true; fi
else
  log "NONBACKEND_RESTART_DEFERRED=source_churn"
fi
# GUARDIAN_FRONTEND_RECONCILE_DELEGATED_TO_DEPLOY_V1: boris-deploy.service is
# the single owner of frontend generation convergence. Parallel guardian builds
# can stale deploy-owned artifacts and create an endless rebuild loop.
if grep -q '"frontend_source_deployed": false' "$CONTRACT_TMP"; then
  log "FRONTEND_RECONCILE_OWNED_BY_DEPLOY"
  systemctl is-active --quiet boris-deploy.service || systemctl restart boris-deploy.service >> "$LOG" 2>&1 || true
fi
# GLOBAL_PRODUCTION_ACCEPTANCE_V1: persist an autonomous, domain-agnostic
# acceptance state. Convergence during source churn is not an incident. If the
# source is quiet and no canonical roll exists for three ticks, restart only
# boris-deploy.service; never raw-restart API replicas here.
if [ "$CONTRACT_RC" -eq 0 ]; then
  cp "$CONTRACT_TMP" "$FINAL_CONTRACT_TMP"
  FINAL_CONTRACT_RC="$CONTRACT_RC"
  log "FINAL_CONTRACT_REUSED=first_pass"
else
  BORIS_POLICY_NOTIFY=0 BORIS_POLICY_SELF_HEAL=0 ./venv/bin/python production_contract_runner.py > "$FINAL_CONTRACT_TMP" 2>&1
  FINAL_CONTRACT_RC=$?
  cat "$FINAL_CONTRACT_TMP" >> "$LOG"
fi
ACCEPTANCE_JSON="$(./venv/bin/python global_production_acceptance.py --contract-file "$FINAL_CONTRACT_TMP" --contract-rc "$FINAL_CONTRACT_RC" 2>>"$LOG" || true)"
[ -n "$ACCEPTANCE_JSON" ] && log "$ACCEPTANCE_JSON"
if printf '%s' "$ACCEPTANCE_JSON" | grep -q '"self_heal_action": "restart_boris_deploy"'; then
  if ! pgrep -f '^/bin/bash /root/BORIS/backend/rolling_restart_backend.sh$' >/dev/null 2>&1; then
    log "GLOBAL_ACCEPTANCE_SELF_HEAL=restart_boris_deploy"
    systemctl restart boris-deploy.service >> "$LOG" 2>&1 || true
    ./venv/bin/python - <<'PY2' >> "$LOG" 2>&1 || true
import json,time
from pathlib import Path
p=Path('/root/BORIS/backend/data/global_production_acceptance.json')
try:d=json.loads(p.read_text())
except Exception:d={}
d['last_self_heal_at']=time.time(); d['self_heal_executed']='restart_boris_deploy'
t=p.with_suffix('.json.tmp'); t.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n'); t.replace(p)
PY2
  fi
fi
# CONTROL_PLANE_SINGLE_SCHEDULER_V1: boris-system-brain.timer is the only
# scheduler allowed to execute the write-capable Control Plane reconciler.
# Production Guardian verifies/self-heals that scheduler but never launches a
# second writer in parallel. systemd itself serializes the canonical service.
if ! systemctl is-enabled --quiet boris-system-brain.timer || ! systemctl is-active --quiet boris-system-brain.timer; then
  log "SYSTEM_BRAIN_TIMER_SELF_HEAL=enable_now"
  systemctl enable --now boris-system-brain.timer >> "$LOG" 2>&1 || true
fi
BRAIN_TIMER_ACTIVE="$(systemctl is-active boris-system-brain.timer 2>/dev/null || true)"
BRAIN_LAST_RESULT="$(systemctl show boris-system-brain.service -p Result --value 2>/dev/null || true)"
log "SYSTEM_BRAIN_SCHEDULER timer=${BRAIN_TIMER_ACTIVE:-unknown} last_result=${BRAIN_LAST_RESULT:-unknown} owner=boris-system-brain.timer"
./venv/bin/python guardian_lifecycle_snapshot.py >> "$LOG" 2>&1 || true
journalctl --since '-7 min' -u boris-backend.service -u boris-backend-replica.service -u boris-background-worker.service -u boris-crm-sync.service -u boris-telephony-runtime.service --no-pager 2>/dev/null | grep -Ei 'QueuePool timeout|Deadlock|OperationalError|CRITICAL|Traceback|OOM|Killed process' | tail -80 >> "$LOG" || true
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 20971520 ]; then tail -c 10485760 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
