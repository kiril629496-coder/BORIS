#!/bin/bash
set -euo pipefail
# OWNERLESS_ROLLING_LANE_V2
ROLLING_LANE="${BORIS_ROLLING_LANE:-}"
# DEPLOY_COORDINATOR_FAIL_CLOSED_V1: only long-lived BORIS services may request a rollout.
# Remote SentinelX jobs can inherit environment variables, so env lane alone is not authority.
CALLER_CGROUP="$(cat /proc/$PPID/cgroup 2>/dev/null || true)"
case "$CALLER_CGROUP" in
  *boris-deploy.service*) ;;
  *) echo "BLOCKED_NON_SERVICE_ROLLING_CALLER lane=${ROLLING_LANE:-none}" >&2; exit 23 ;;
esac
PARENT_ARGS="$(ps -o args= -p "$PPID" 2>/dev/null || true)"
if [[ "$PARENT_ARGS" == *sentinelx-cloud-core* ]]; then echo "BLOCKED_REMOTE_ROLLING_RESTART" >&2; exit 23; fi
case "$ROLLING_LANE" in
  deploy_v1) ;;
  *) echo "BLOCKED_UNTRUSTED_ROLLING_LANE lane=${ROLLING_LANE:-none}" >&2; exit 23 ;;
esac
UP=/etc/nginx/conf.d/boris_api_upstream.conf
LOCK=/run/lock/boris-backend-rolling.lock
KPI_LOCK=/root/BORIS/backend/run/kpi_goal_runner.lock
# /run/lock is shared by root-owned deploy services and SentinelX. Open an existing
# lock read-only so ownership/umask cannot make later deploys fail with EACCES.
[ -e "$LOCK" ] || : >"$LOCK"
exec 9<"$LOCK"; flock -n 9 || { echo 'ABORT: rolling restart already running'; exit 19; }
# DEPLOY_KPI_LOCK_V1: a KPI tick can contain external provider calls. Never roll
# either API generation through a live KPI runner. Take the same exclusive lock
# used by kpi_goal_runner.py and keep it for the complete drain/restart sequence.
# Blocking here is intentional: deploy waits for the current KPI obligation to
# finish instead of terminating a request and creating an ambiguous replay state.
[ -e "$KPI_LOCK" ] || : >"$KPI_LOCK"
exec 8>"$KPI_LOCK"
flock -w "${BORIS_KPI_DEPLOY_LOCK_WAIT_SEC:-600}" 8 || { echo 'ABORT: KPI runner did not quiesce'; exit 17; }
echo 'DEPLOY_KPI_LOCK=PASS'
health(){ curl --noproxy '*' -fsS --max-time 3 "http://127.0.0.1:$1/health" >/dev/null; }

# BACKEND_REFUSE_MANUAL_STOP_V1:
# API units refuse ordinary manual stop/restart, so an ad-hoc root/SentinelX
# command cannot take an nginx upstream out from under the HA coordinator.
# The canonical rolling owner terminates the service cgroup intentionally;
# Restart=always then starts the next generation under systemd supervision.
restart_protected_unit(){
  local unit="${1:?unit required}" before="${2:?previous activation required}" after=''
  sudo systemctl kill --kill-whom=all --signal=TERM "$unit" || return 1
  for _i in $(seq 1 120); do
    after="$(systemctl show "$unit" -p ActiveEnterTimestampMonotonic --value)"
    if [ -n "$after" ] && [ "$after" != "$before" ] && systemctl is-active --quiet "$unit"; then
      return 0
    fi
    sleep 0.2
  done
  echo "FAIL: protected API generation restart not observed unit=$unit" >&2
  return 1
}
# ROLLING_INTERRUPT_SAFE_DRAIN_V1: if the rollout process is terminated by
# supervisor/timeout while one replica is drained, never blindly re-enable a
# replica that may still be down. Restore the both-replica nginx config only
# when both local health probes already succeed; otherwise preserve the current
# safe drain and let the next canonical rollout/self-heal converge it.
_interrupt_cleanup(){
  local rc=$?
  trap - EXIT TERM INT
  if health 8000 && health 8001; then
    render none >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap _interrupt_cleanup EXIT TERM INT
wait_health(){ local p=$1; for i in $(seq 1 100); do health "$p" && return 0; sleep 0.2; done; return 1; }
public(){
  # PUBLIC_5XX_STRICT_V1: a public HTTP 5xx is a real HA failure even when both
  # loopback APIs are healthy. Transport-only failures may converge briefly,
  # but never hide an observed 5xx behind retry classification.
  local i code rc
  for i in $(seq 1 6); do
    set +e
    code=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 5 https://boris-ai.pro/health)
    rc=$?
    set -e
    if [ "$rc" -eq 0 ] && [[ "$code" =~ ^2[0-9][0-9]$ ]]; then return 0; fi
    if [[ "$code" =~ ^5[0-9][0-9]$ ]]; then
      echo "FAIL: public health HTTP $code" >&2
      return 1
    fi
    sleep 0.5
  done
  echo "FAIL: public health transport/status convergence failed code=${code:-000} rc=${rc:-1}" >&2
  return 1
}
nginx_workers_converged(){
  # NGINX_DRAIN_GENERATION_FENCE_V1: do not stop the opposite API while an old
  # nginx worker can still carry the previous single-replica drain config.
  # A long-lived old worker blocks deployment rather than risking public 5xx.
  local i
  for i in $(seq 1 120); do
    if ! ps -eo args= | grep -q '^nginx: worker process is shutting down'; then return 0; fi
    sleep 0.25
  done
  echo 'FAIL: old nginx worker generation did not drain safely' >&2
  return 1
}
# DEPLOY_INSTANCE_CONTRACT_RETRY_V1: bounded same-instance contract retry.
contract(){
  # A sibling maintenance/deploy process may restart this exact replica in the
  # tiny gap after wait_health() succeeds. Verify the same target generation,
  # but tolerate only a short bounded convergence window; never fall back to the
  # other replica because this check proves the restarted instance itself.
  local p=$1 i
  for i in $(seq 1 24); do
    /root/BORIS/backend/venv/bin/python /root/BORIS/backend/backend_contract_check.py "http://127.0.0.1:$p" >/dev/null 2>&1 && return 0
    sleep 0.25
  done
  return 1
}
capacity_gate(){
  /root/BORIS/backend/venv/bin/python - <<'PY'
from app.db.session import SessionLocal
from app.services.reliability import capacity_health
s=SessionLocal(); h=capacity_health(s); s.close()
unsafe=[]
for x in h.get("signals",[]):
    k=x.get("key"); v=float(x.get("value") or 0)
    # Conservative deploy envelope: leave headroom for one API replica being drained.
    if (k=="cpu_load" and v>=90) or (k=="memory_used" and v>=72) or (k=="db_connections" and v>=78) or x.get("state")=="critical": unsafe.append((k,v,x.get("state")))
if unsafe:
    print("DEPLOY_CAPACITY_WAIT",unsafe); raise SystemExit(1)
print("DEPLOY_CAPACITY_PASS",h.get("state"),"db",h.get("db_connections_pct"),"load1",h.get("load1"))
PY
}
wait_capacity(){
  local max=${BORIS_DEPLOY_CAPACITY_WAIT_SEC:-180} waited=0
  while ! capacity_gate; do
    if [ "$waited" -ge "$max" ]; then echo "ABORT: no safe capacity window after ${waited}s"; return 1; fi
    sleep 10; waited=$((waited+10));
  done
  echo "DEPLOY_SAFE_WINDOW=PASS waited=${waited}s"
}

render(){
  local drain=${1:-none} tmp rollback
  # ROLLING_NGINX_ROOT_ATOMIC_V2: SentinelX and non-root operators must be able
  # to run the canonical HA rollout. Build the candidate as the current user,
  # then perform only the final root-owned file operations via sudo. Keep an
  # immediate rollback copy so a bad nginx candidate can never strand traffic.
  tmp="$(mktemp /tmp/boris_api_upstream.XXXXXX)"
  rollback="/tmp/boris_api_upstream.rollback.$$"
  cat > "$tmp" <<EOF
upstream boris_api_pool {
    least_conn;
    server 127.0.0.1:8000 max_fails=1 fail_timeout=1s${drain:+$([ "$drain" = 8000 ] && echo ' down' || true)};
    server 127.0.0.1:8001 max_fails=1 fail_timeout=1s${drain:+$([ "$drain" = 8001 ] && echo ' down' || true)};
    keepalive 32;
}
EOF
  sudo cp "$UP" "$rollback"
  sudo cp "$tmp" "$UP.tmp"
  rm -f "$tmp"
  sudo chown root:root "$UP.tmp"
  sudo chmod 0644 "$UP.tmp"
  sudo mv "$UP.tmp" "$UP"
  if ! sudo nginx -t >/dev/null; then
    sudo cp "$rollback" "$UP"
    sudo rm -f "$rollback"
    sudo nginx -t >/dev/null || true
    echo 'FAIL: nginx candidate rejected; upstream restored'
    return 1
  fi
  sudo systemctl reload nginx
  sleep 1
  if ! public; then
    sudo cp "$rollback" "$UP"
    sudo nginx -t >/dev/null || true
    sudo systemctl reload nginx || true
    sudo rm -f "$rollback"
    echo 'FAIL: nginx reload caused public health failure; upstream rolled back' >&2
    return 1
  fi
  # When returning to both replicas, fence the retired nginx generation before
  # any later step is allowed to drain the opposite API.
  if [ "${drain:-none}" = 'none' ]; then
    if ! nginx_workers_converged; then
      # Keep the newly loaded both-replicas config. Aborting here is safe because
      # no opposite API has been stopped yet; rolling back would unnecessarily
      # leave production in a single-replica drain state.
      sudo rm -f "$rollback"
      echo 'FAIL: nginx worker generation overlap; kept both replicas enabled' >&2
      return 1
    fi
  fi
  sudo rm -f "$rollback"
  return 0
}
# SOURCE_QUIESCENCE_BEFORE_ROLL_V1: autonomous workers may land several safe
# edits seconds apart. Restarting both replicas for every intermediate inode
# generation creates avoidable drain churn. Require the backend import surface
# to remain unchanged for two consecutive observations before a rollout pass.
backend_source_fingerprint(){
  PYTHONPATH=/root/BORIS/backend /root/BORIS/backend/venv/bin/python - <<'PY'
import production_contract_runner as p
print(p._backend_runtime_content_fingerprint())
PY
}
active_backend_root(){
  realpath /root/BORIS/releases/backend/current/backend
}

# DEPLOY_SOURCE_IMPORT_FENCE_V2: validate the exact immutable release selected
# for production. The mutable development workspace is never a rollout candidate.
source_import_smoke(){
  local rel
  rel="$(active_backend_root)" || return 1
  BORIS_RUNTIME_BACKEND_ROOT="$rel" PYTHONPATH="$rel" PYTHONDONTWRITEBYTECODE=1 \
    timeout 35 /root/BORIS/backend/venv/bin/python -c 'import app.main' >/dev/null 2>&1
}
source_still_safe(){
  local expected="$1" current
  current="$(backend_source_fingerprint)" || return 1
  [ -n "$expected" ] && [ "$current" = "$expected" ] || return 1
  source_import_smoke
}

certify_backend_generation(){
  # BACKEND_CONTENT_CERTIFY_V1: called only after both API replicas passed the
  # canonical drain/restart/contract sequence while source bytes stayed stable.
  # Atomic rename + fsync makes this certificate crash-safe.
  local fp="$1" tmp=/root/BORIS/backend/run/backend_runtime_content.sha256.tmp.$$
  printf '%s\n' "$fp" > "$tmp"
  sync "$tmp" 2>/dev/null || true
  mv -f "$tmp" /root/BORIS/backend/run/backend_runtime_content.sha256
}
wait_backend_source_quiet(){
  # DEPLOY_SOURCE_QUIESCENCE_WINDOW_V2: two equal 2-second samples were only a
  # ~4s debounce and caused repeated HA rollouts while independent autonomous
  # work landed source changes minutes apart. Require a real quiet window before
  # touching either API generation; keep it configurable for emergency tuning.
  local prev='' cur='' stable=0
  local sample_sec="${BORIS_DEPLOY_SOURCE_SAMPLE_SEC:-2}"
  local quiet_sec="${BORIS_DEPLOY_SOURCE_QUIET_SEC:-30}"
  local max_wait_sec="${BORIS_DEPLOY_SOURCE_QUIET_MAX_SEC:-180}"
  local required=$(( (quiet_sec + sample_sec - 1) / sample_sec ))
  local max_samples=$(( max_wait_sec / sample_sec ))
  [ "$required" -ge 2 ] || required=2
  [ "$max_samples" -ge "$required" ] || max_samples="$required"
  for i in $(seq 1 "$max_samples"); do
    cur="$(backend_source_fingerprint)" || return 1
    if [ -n "$prev" ] && [ "$cur" = "$prev" ]; then
      stable=$((stable+1))
      if [ "$stable" -ge "$required" ]; then
        echo "DEPLOY_SOURCE_QUIESCENCE=PASS stable_sec=$((stable*sample_sec)) samples=$i fingerprint=${cur:0:12}"
        return 0
      fi
    else
      stable=0
    fi
    prev="$cur"
    sleep "$sample_sec"
  done
  echo "ABORT: backend source did not quiesce for ${quiet_sec}s within ${max_wait_sec}s"
  return 1
}

# BACKEND_STARTUP_IMPORT_PREFLIGHT_V1: never drain or restart a healthy API
# generation until the candidate source can import the exact application entrypoint.
# py_compile cannot catch missing imported names; this gate catches those before
# either production replica is touched.
backend_startup_import_preflight(){
  local log="/tmp/boris_backend_import_preflight.$$.log" rel
  rel="$(active_backend_root)" || return 1
  if PYTHONDONTWRITEBYTECODE=1 BORIS_POLICY_SELF_HEAL=0 BORIS_RUNTIME_BACKEND_ROOT="$rel" PYTHONPATH="$rel" \
      timeout 90 /root/BORIS/backend/venv/bin/python - <<'PYI' >"$log" 2>&1
import app.main
print("BACKEND_STARTUP_IMPORT_PREFLIGHT=PASS")
PYI
  then
    tail -1 "$log" || true
    rm -f "$log"
    return 0
  fi
  rc=$?
  echo "ABORT: candidate backend cannot import app.main rc=$rc" >&2
  tail -80 "$log" >&2 || true
  rm -f "$log"
  return 1
}

# Preconditions: capacity must not be critical, source must be quiet, candidate
# startup must import successfully, and both live replicas must already be healthy.
wait_capacity || exit 18
wait_backend_source_quiet || exit 35
backend_startup_import_preflight || exit 37
stable_preflight(){
  for i in $(seq 1 20); do
    if health 8000 && health 8001 && contract 8000 && contract 8001 && public; then sleep 2; health 8000 && health 8001 && public && return 0; fi
    sleep 2
  done
  return 1
}
stable_preflight || { echo 'ABORT: HA preflight/contract not stable'; exit 20; }
# Replica-first rollout. Source may still be changing because autonomous workers
# can land a safe edit while this deployment is in progress. A rollout must not
# report success while either API process predates the newest imported source.
roll_pair(){
  local expected_fp="${1:?expected source fingerprint required}"
  local before_replica after_replica before_primary after_primary
  source_still_safe "$expected_fp" || { echo 'SOURCE_FENCE: unsafe/change before API2 drain' >&2; return 47; }
  render 8001 || { echo 'FAIL: could not safely drain API2' >&2; return 43; }
  source_still_safe "$expected_fp" || { render none || true; echo 'SOURCE_FENCE: unsafe/change before API2 restart' >&2; return 47; }
  before_replica="$(systemctl show boris-backend-replica.service -p ActiveEnterTimestampMonotonic --value)"
  restart_protected_unit boris-backend-replica.service "$before_replica" || { render none || true; echo 'FAIL: API2 protected restart not observed'; return 42; }
  after_replica="$(systemctl show boris-backend-replica.service -p ActiveEnterTimestampMonotonic --value)"
  [ -n "$after_replica" ] && [ "$after_replica" != "$before_replica" ] || { render none || true; echo 'FAIL: API2 restart not observed'; return 42; }
  wait_health 8001 || { render none || true; echo 'FAIL: API2 recovery'; return 22; }
  contract 8001 || { render 8001 || true; echo 'FAIL: API2 contract; kept drained'; return 32; }
  render none || { echo 'FAIL: both-replica nginx generation did not converge after API2; API1 restart blocked' >&2; return 44; }
  source_still_safe "$expected_fp" || { echo 'SOURCE_FENCE: source changed/import failed; API1 preserved' >&2; return 47; }
  render 8000 || { echo 'FAIL: could not safely drain API1' >&2; return 45; }
  source_still_safe "$expected_fp" || { render none || true; echo 'SOURCE_FENCE: unsafe/change before API1 restart; API1 preserved' >&2; return 47; }
  before_primary="$(systemctl show boris-backend.service -p ActiveEnterTimestampMonotonic --value)"
  restart_protected_unit boris-backend.service "$before_primary" || { render none || true; echo 'FAIL: API1 protected restart not observed'; return 41; }
  after_primary="$(systemctl show boris-backend.service -p ActiveEnterTimestampMonotonic --value)"
  [ -n "$after_primary" ] && [ "$after_primary" != "$before_primary" ] || { render none || true; echo 'FAIL: API1 restart not observed'; return 41; }
  wait_health 8000 || { render none || true; echo 'FAIL: API1 recovery'; return 21; }
  contract 8000 || { render 8000 || true; echo 'FAIL: API1 contract; kept drained'; return 31; }
  render none || { echo 'FAIL: both-replica nginx generation did not converge after API1' >&2; return 46; }
  # Other autonomous maintenance may briefly restart a sibling service after our
  # drain step. Do not turn one transient curl into a raw rc=7 deployment result.
  # Reuse the bounded HA stability probe and always leave Nginx with both APIs enabled.
  stable_preflight || { render none || true; echo 'FAIL: HA postflight/contract not stable'; return 34; }
  return 0
}

generation_current(){
  PYTHONPATH=/root/BORIS/backend /root/BORIS/backend/venv/bin/python - <<'PY'
import production_contract_runner as p
checks,_=p._runtime_generation_state()
raise SystemExit(0 if checks.get('backend_runtime_generation') else 1)
PY
}

# BACKEND_FRESH_RUNTIME_CERTIFY_NO_RESTART_V1: a previous HA pass can restart
# both replicas successfully but finish before writing the content certificate.
# If both live processes are already newer than the exact quiet source and all
# preflight contracts are green, certify those bytes without another HA restart.
pre_roll_fp="$(backend_source_fingerprint)" || exit 36
if source_still_safe "$pre_roll_fp" && generation_current; then
  certify_backend_generation "$pre_roll_fp"
  if generation_current; then
    echo "ROLLING_RESTART_SKIPPED=FRESH_RUNTIME_CERTIFIED fingerprint=${pre_roll_fp:0:12}"
    exit 0
  fi
fi

# GENERATION_STABLE_ROLLOUT_V1: bounded replay of the *deployment*, never the
# paid/business operation. If source changed between replica and primary restart,
# roll the pair again until both long-lived processes are newer than that source.
# This closes the partial-generation race without taking application traffic down.
for pass in 1 2 3; do
  echo "ROLLING_GENERATION_PASS=$pass"
  pass_source_fp="$(backend_source_fingerprint)" || exit 36
  if ! source_still_safe "$pass_source_fp"; then
    echo "ROLLING_SOURCE_IMPORT_NOT_READY pass=$pass" >&2
    wait_backend_source_quiet || exit 35
    continue
  fi
  set +e
  roll_pair "$pass_source_fp"
  roll_rc=$?
  set -e
  if [ "$roll_rc" -eq 47 ]; then
    echo "ROLLING_SOURCE_CHANGED_OR_IMPORT_FAILED pass=$pass; preserved known-good replica"
    render none || true
    wait_backend_source_quiet || exit 35
    continue
  elif [ "$roll_rc" -ne 0 ]; then
    exit "$roll_rc"
  fi
  post_source_fp="$(backend_source_fingerprint)" || exit 36
  if [ "$pass_source_fp" = "$post_source_fp" ]; then
    certify_backend_generation "$post_source_fp"
    if generation_current; then
      echo 'ROLLING_RESTART_DRAIN=PASS API1=OK API2=OK CONTRACTS=OK PUBLIC=OK GENERATION=OK'
      exit 0
    fi
  fi
  echo "ROLLING_GENERATION_CHANGED_DURING_DEPLOY pass=$pass"
  wait_backend_source_quiet || exit 35
done

echo 'FAIL: backend source kept changing across 3 rollout passes; generation not stable'
exit 33
