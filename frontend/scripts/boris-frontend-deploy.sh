#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/BORIS/frontend"
STORE="$ROOT/.boris-builds"
ARTIFACT_ROOT="$STORE/artifacts"
CURRENT="$STORE/current"
LOCK="$STORE/deploy.lock"
HEALTH_PATH="${BORIS_DEPLOY_HEALTH_PATH:-/dashboard/command-center}"
LOCK_WAIT="${BORIS_DEPLOY_LOCK_WAIT:-120}"

cd "$ROOT"
mkdir -p "$ARTIFACT_ROOT"

ARTIFACT="${1:-}"
if [ -z "$ARTIFACT" ]; then
  echo "usage: npm run deploy -- /root/BORIS/frontend/.boris-builds/artifacts/<READY-artifact>"
  exit 64
fi
ARTIFACT="$(realpath "$ARTIFACT")"
case "$ARTIFACT" in
  "$ARTIFACT_ROOT/"*) ;;
  *) echo "DEPLOY=FAIL artifact outside authoritative store"; exit 65;;
esac

META="$ARTIFACT/BORIS_ARTIFACT.json"
[ -f "$META" ] || { echo "DEPLOY=FAIL missing BORIS_ARTIFACT.json"; exit 66; }
[ -f "$ARTIFACT/BUILD_ID" ] || { echo "DEPLOY=FAIL missing BUILD_ID"; exit 67; }
read -r STATUS BUILD_ID IMMUTABLE SOURCE_HASH CREATED_AT < <(python3 - "$META" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding='utf-8'))
print(
    m.get('status',''),
    m.get('build_id',''),
    str(bool(m.get('immutable'))).lower(),
    m.get('snapshot_source_hash',''),
    m.get('created_at',''),
)
PY
)
[ "$STATUS" = "READY" ] || { echo "DEPLOY=FAIL artifact status=$STATUS"; exit 68; }
[ "$IMMUTABLE" = "true" ] || { echo "DEPLOY=FAIL artifact is not immutable"; exit 69; }
[ "$BUILD_ID" = "$(cat "$ARTIFACT/BUILD_ID")" ] || { echo "DEPLOY=FAIL BUILD_ID metadata mismatch"; exit 70; }

# Immutable snapshot builds are deployable even when shared source changes
# afterwards. Current source is diagnostic only: concurrent work must not make
# a complete READY artifact permanently undeployable.
CURRENT_SOURCE_HASH="$(python3 "$ROOT/scripts/frontend-source-hash.py")"
echo "ARTIFACT_SOURCE_HASH=$SOURCE_HASH"
echo "CURRENT_SOURCE_HASH=$CURRENT_SOURCE_HASH"
if [ "$SOURCE_HASH" != "$CURRENT_SOURCE_HASH" ]; then
  echo "SOURCE_MOVED_AFTER_SNAPSHOT=1"
fi

# Only the release switch is serialized. Builds never take this lock and may
# run concurrently without touching the live runtime release.
exec 9>"$LOCK"
if ! flock -w "$LOCK_WAIT" 9; then
  echo "DEPLOY=FAIL production switch lock timeout"
  exit 71
fi

# FRONTEND_DEPLOY_POSTLOCK_SOURCE_DIAGNOSTIC_V2:
# Re-read source after the deploy lock only for diagnostics. A source edit that
# happens while we wait belongs to a later isolated build; it does not invalidate
# this complete immutable artifact. The generation convergence controller will
# build and deploy that newer source separately.
POSTLOCK_SOURCE_HASH="$(python3 "$ROOT/scripts/frontend-source-hash.py")"
echo "POSTLOCK_SOURCE_HASH=$POSTLOCK_SOURCE_HASH"
# FRONTEND_DEPLOY_POSTLOCK_SOURCE_GUARD_V1:
# Once the production switch lock is held, never promote an artifact built from
# an older source generation. Exit 76 is the canonical "source moved" signal;
# the deploy reconciler will rebuild the newest immutable generation.
if [ "$SOURCE_HASH" != "$POSTLOCK_SOURCE_HASH" ]; then
  echo "SOURCE_MOVED_WHILE_WAITING=1"
  echo "DEPLOY=CONVERGING source_changed_before_switch"
  exit 76
fi

PREVIOUS="$(readlink -f "$CURRENT" 2>/dev/null || true)"
PREVIOUS_ID=""
PREVIOUS_SOURCE_HASH=""
PREVIOUS_CREATED_AT=""
if [ -n "$PREVIOUS" ] && [ -f "$PREVIOUS/BUILD_ID" ]; then
  PREVIOUS_ID="$(cat "$PREVIOUS/BUILD_ID")"
  if [ -f "$PREVIOUS/BORIS_ARTIFACT.json" ]; then
    read -r PREVIOUS_SOURCE_HASH PREVIOUS_CREATED_AT < <(python3 - "$PREVIOUS/BORIS_ARTIFACT.json" <<'PY'
import json,sys
try:
    m=json.load(open(sys.argv[1],encoding='utf-8'))
    print(m.get('snapshot_source_hash',''), m.get('created_at',''))
except Exception:
    print('', '')
PY
)
  fi
fi

# FRONTEND_DEPLOY_MONOTONIC_RELEASE_GUARD_V1:
# Source may move after an isolated snapshot, but production must never move
# backwards to an artifact created before the currently active release.
if [ "${BORIS_ALLOW_ROLLBACK_ARTIFACT:-0}" != "1" ] && [ -n "$PREVIOUS_CREATED_AT" ]; then
  TARGET_EPOCH="$(date -d "$CREATED_AT" +%s 2>/dev/null || echo 0)"
  PREVIOUS_EPOCH="$(date -d "$PREVIOUS_CREATED_AT" +%s 2>/dev/null || echo 0)"
  if [ "$TARGET_EPOCH" -le 0 ] || [ "$PREVIOUS_EPOCH" -le 0 ]; then
    echo "DEPLOY=FAIL invalid artifact created_at metadata"
    exit 78
  fi
  if [ "$TARGET_EPOCH" -lt "$PREVIOUS_EPOCH" ]; then
    echo "DEPLOY=FAIL target artifact is older than active production release"
    echo "TARGET_CREATED_AT=$CREATED_AT"
    echo "ACTIVE_CREATED_AT=$PREVIOUS_CREATED_AT"
    exit 79
  fi
fi

switch_to() {
  local target="$1"
  local tmp="$STORE/.current.$$.new"
  rm -f "$tmp"
  ln -s "$target" "$tmp"
  # Atomic rename: current is always either the old complete artifact or the
  # new complete artifact; there is no intermediate missing BUILD_ID state.
  mv -Tf "$tmp" "$CURRENT"
}

restart_frontend() {
  if [ "${BORIS_DEPLOY_EXTERNAL_RESTART:-0}" = "1" ]; then
    echo "FRONTEND_RESTART=EXTERNAL"
    return 0
  fi
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    systemctl restart boris-frontend.service
  else
    sudo -n systemctl restart boris-frontend.service
  fi
}

health_ok() {
  local local_code public_code
  local_code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:3002${HEALTH_PATH}" || true)"
  public_code="$(curl -sS -o /dev/null -w '%{http_code}' "https://boris-ai.pro${HEALTH_PATH}" || true)"
  echo "HEALTH_LOCAL=$local_code"
  echo "HEALTH_PUBLIC=$public_code"
  [ "$local_code" = "200" ] && [ "$public_code" = "200" ]
}

mobile_runtime_ok() {
  /root/BORIS/backend/venv/bin/python "$ROOT/scripts/qa-mobile-campaign-deploy-gate.py"
}

campaign_launch_runtime_ok() {
  /root/BORIS/backend/venv/bin/python "$ROOT/scripts/qa-campaign-launch-contract.py"
}

runtime_generation_fresh() {
  # FRONTEND_DEPLOY_RUNTIME_PIN_GUARD_V1:
  # Next resolves .boris-builds/current once at process start. A later symlink
  # switch without restart means the process is serving an older artifact even
  # when current/BUILD_ID look correct on disk.
  local link_epoch started_text started_epoch
  link_epoch="$(stat -c '%Y' "$CURRENT" 2>/dev/null || echo 0)"
  started_text="$(systemctl show boris-frontend.service -p ExecMainStartTimestamp --value 2>/dev/null || true)"
  started_epoch="$(date -d "$started_text" +%s 2>/dev/null || echo 0)"
  echo "CURRENT_SWITCH_EPOCH=$link_epoch"
  echo "FRONTEND_START_EPOCH=$started_epoch"
  [ "$link_epoch" -gt 0 ] && [ "$started_epoch" -ge "$link_epoch" ]
}

# FRONTEND_DEPLOY_SAME_BUILD_NOOP_V1: cache-hit/concurrent builders can hand
# deploy a duplicate artifact for the exact generation already serving traffic.
# Restarting the single Next process in that case creates a needless brief 502.
# Keep the current immutable release, verify both local/public health, and exit
# without touching systemd when BUILD_ID + source generation already match.
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS_ID" = "$BUILD_ID" ] && [ "$PREVIOUS_SOURCE_HASH" = "$SOURCE_HASH" ]; then
  echo "ATOMIC_SWITCH=NOOP same build selected"
  echo "PREVIOUS_BUILD_ID=$PREVIOUS_ID"
  echo "TARGET_BUILD_ID=$BUILD_ID"
  if ! runtime_generation_fresh; then
    echo "FRONTEND_RUNTIME_GENERATION=STALE restart_required"
    if ! restart_frontend; then
      echo "DEPLOY=FAIL stale-runtime restart"
      exit 73
    fi
    sleep 2
    runtime_generation_fresh || { echo "DEPLOY=FAIL runtime_generation_still_stale"; exit 81; }
    echo "FRONTEND_RUNTIME_GENERATION=RECONCILED"
  else
    echo "FRONTEND_RUNTIME_GENERATION=FRESH"
  fi
  if health_ok; then
    if ! mobile_runtime_ok; then
      echo "DEPLOY=FAIL mobile_runtime_qa"
      exit 80
    fi
    if ! campaign_launch_runtime_ok; then
      echo "DEPLOY=FAIL campaign_launch_runtime_qa"
      exit 82
    fi
    echo "DEPLOY=PASS"
    echo "ATOMIC_DEPLOY=NOOP"
    echo "PRODUCTION_BUILD_ID=$PREVIOUS_ID"
    echo "ACTIVE_RELEASE=$PREVIOUS"
    echo "BUILDERS_MAY_CONTINUE=1"
    exit 0
  fi
  echo "DEPLOY=FAIL same-build health"
  exit 74
fi

rollback() {
  if [ -z "$PREVIOUS" ] || [ ! -d "$PREVIOUS" ]; then
    echo "ROLLBACK=FAIL no previous release"
    return 1
  fi
  switch_to "$PREVIOUS"
  restart_frontend
  sleep 4
  if health_ok; then
    echo "ROLLBACK=PASS"
    echo "ROLLBACK_BUILD_ID=$PREVIOUS_ID"
    return 0
  fi
  echo "ROLLBACK=FAIL health"
  return 1
}

switch_to "$ARTIFACT"
ACTIVE_LINK_ID="$(cat "$CURRENT/BUILD_ID" 2>/dev/null || true)"
if [ "$ACTIVE_LINK_ID" != "$BUILD_ID" ]; then
  echo "DEPLOY=FAIL atomic switch BUILD_ID mismatch"
  rollback || true
  exit 72
fi

echo "ATOMIC_SWITCH=PASS"
echo "PREVIOUS_BUILD_ID=${PREVIOUS_ID:-NONE}"
echo "TARGET_BUILD_ID=$BUILD_ID"

if ! restart_frontend; then
  echo "DEPLOY=FAIL frontend restart"
  rollback || true
  exit 73
fi

if [ "${BORIS_DEPLOY_EXTERNAL_RESTART:-0}" = "1" ]; then
  echo "DEPLOY=SWITCHED_AWAITING_EXTERNAL_RESTART"
  exit 0
fi

sleep 4
if ! health_ok; then
  echo "DEPLOY=FAIL health"
  rollback || true
  exit 74
fi

# Client-facing prospecting showcase is part of the deploy contract. Verify
# the active runtime, assets and unauthenticated API isolation before green.
if ! "$ROOT/scripts/qa-prospecting-production.sh"; then
  echo "DEPLOY=FAIL prospecting_runtime_qa"
  rollback || true
  exit 79
fi

# FRONTEND_MOBILE_RUNTIME_GATE_V1: static contracts catch source regressions,
# this read-only browser gate proves the active 320px production shell actually
# works after restart. It never submits campaign/forms or paid actions.
if ! mobile_runtime_ok; then
  echo "DEPLOY=FAIL mobile_runtime_qa"
  rollback || true
  exit 80
fi

# FRONTEND_CAMPAIGN_LAUNCH_RUNTIME_GATE_V1: prove the real campaign launch UI
# works on phone + desktop. The browser aborts guardian/prepare before backend,
# so this cannot publish, spend client limits or trigger paid generation.
if ! campaign_launch_runtime_ok; then
  echo "DEPLOY=FAIL campaign_launch_runtime_qa"
  rollback || true
  exit 82
fi

LIVE_ID="$(cat "$CURRENT/BUILD_ID" 2>/dev/null || true)"
[ "$LIVE_ID" = "$BUILD_ID" ] || { echo "DEPLOY=FAIL live BUILD_ID changed"; rollback || true; exit 75; }

# Source may change while restart/health QA runs. The deployed immutable
# artifact remains valid; the newer source belongs to a later isolated build.
# Keep this as evidence, never as a false deployment failure after green runtime.
FINAL_SOURCE_HASH="$(python3 "$ROOT/scripts/frontend-source-hash.py")"
echo "FINAL_SOURCE_HASH=$FINAL_SOURCE_HASH"
# FRONTEND_DEPLOY_FINAL_SOURCE_GUARD_V1:
# A release can be healthy yet already stale if source changed during restart or
# runtime QA. Keep the healthy artifact live, but do not publish a false PASS;
# rc=76 makes the canonical reconciler immediately build/deploy the newer source.
if [ "$SOURCE_HASH" != "$FINAL_SOURCE_HASH" ]; then
  echo "SOURCE_MOVED_DURING_DEPLOY=1"
  echo "DEPLOY=CONVERGING source_changed_during_runtime_qa"
  exit 76
fi

echo "DEPLOY=PASS"
echo "ATOMIC_DEPLOY=PASS"
echo "PRODUCTION_BUILD_ID=$BUILD_ID"
echo "ACTIVE_RELEASE=$ARTIFACT"
echo "BUILDERS_MAY_CONTINUE=1"

# FRONTEND_ARTIFACT_RETENTION_V1: parallel autonomous builds can create dozens
# of immutable ~40-50MB releases per day. Prune only old releases after a
# successful atomic deploy; keep current, marker targets, previous rollback,
# newest releases, and everything younger than one hour.
RETENTION_ARGS=(
  --keep "${BORIS_FRONTEND_ARTIFACT_KEEP:-20}"
  --min-age-seconds "${BORIS_FRONTEND_ARTIFACT_MIN_AGE_SEC:-3600}"
)
[ -z "$PREVIOUS" ] || RETENTION_ARGS+=(--protect "$PREVIOUS")
# FRONTEND_RETENTION_ROOT_TIMER_V1: immutable artifacts may be produced by both
# root-owned system jobs and the SentinelX deploy user. A non-root deploy must
# not report a false cleanup failure when an old root-owned release is pruned.
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  python3 "$ROOT/scripts/frontend-artifact-retention.py" "${RETENTION_ARGS[@]}" \
    || echo "FRONTEND_ARTIFACT_RETENTION=WARNING cleanup_failed"
else
  # FRONTEND_RETENTION_PRIVILEGED_FALLBACK_V1: SentinelX/manual deploys may
  # encounter old root-owned immutable releases created by the system service.
  # Attempt exactly the bounded retention command under passwordless sudo; a
  # narrowly-scoped sudoers rule may allow python3 without allowing `sudo true`.
  if ! sudo -n python3 "$ROOT/scripts/frontend-artifact-retention.py" "${RETENTION_ARGS[@]}"; then
    echo "FRONTEND_ARTIFACT_RETENTION=DEFERRED privileged_cleanup_unavailable"
  fi
fi
