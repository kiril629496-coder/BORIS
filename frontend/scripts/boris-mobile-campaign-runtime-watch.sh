#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/BORIS/frontend
PY=/root/BORIS/backend/venv/bin/python
STATUS_DIR="$ROOT/.boris-builds"
STATUS_FILE="$STATUS_DIR/mobile-campaign-watch.status"
ALERT_FILE="$STATUS_DIR/mobile-campaign-watch.alert"
LOCK_FILE="$STATUS_DIR/mobile-campaign-runtime-watch.lock"
LOG_TAG=boris-mobile-campaign-watch

mkdir -p "$STATUS_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "WATCH=SKIP reason=already_running"
  exit 0
fi

deploy_in_progress() {
  # Match only a real deploy shell, not another agent whose prompt merely
  # contains the deploy script name.
  pgrep -af '^bash (scripts/boris-frontend-deploy\.sh|/root/BORIS/frontend/scripts/boris-frontend-deploy\.sh)( |$)' >/dev/null 2>&1
}

skip_for_deploy() {
  local reason="$1"
  printf 'status=SKIP\nreason=%s\nat=%s\nrelease=%s\n' "$reason" "$(date -u +%FT%TZ)" "$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)" > "$STATUS_FILE"
  echo "WATCH=SKIP reason=$reason"
  exit 0
}

# Do not race a production switch: deploy itself already runs both gates.
if deploy_in_progress; then
  skip_for_deploy deploy_in_progress
fi
START_RELEASE="$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)"
PREVIOUS_STATUS=""
PREVIOUS_DETAIL=""
PREVIOUS_RELEASE=""
if [ -f "$STATUS_FILE" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      status) PREVIOUS_STATUS="$value" ;;
      detail) PREVIOUS_DETAIL="$value" ;;
      release) PREVIOUS_RELEASE="$value" ;;
    esac
  done < "$STATUS_FILE"
fi

previous_terminal_failure_same_release() {
  local current
  current="$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)"
  [ "$PREVIOUS_STATUS" = "FAIL" ] &&
  [ "$PREVIOUS_RELEASE" = "$current" ] &&
  case "$PREVIOUS_DETAIL" in
    needs_owner_or_code_fix|frontend_restart_not_permitted|persistent_previous_failure) return 0 ;;
    *) return 1 ;;
  esac
}

run_gates() {
  "$PY" "$ROOT/scripts/qa-mobile-campaign-deploy-gate.py" &&
  "$PY" "$ROOT/scripts/qa-campaign-launch-contract.py"
}

public_api_alive() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 https://boris-ai.pro/api/auth/login || true)"
  case "$code" in
    200|400|401|403|405|422) return 0 ;;
    *) return 1 ;;
  esac
}

restart_frontend() {
  if [ "$(id -u)" -eq 0 ]; then
    systemctl restart boris-frontend.service
  else
    sudo -n systemctl restart boris-frontend.service
  fi
}

write_status() {
  local state="$1" detail="$2"
  {
    echo "status=$state"
    echo "detail=$detail"
    echo "at=$(date -u +%FT%TZ)"
    echo "build_id=$(cat "$ROOT/.boris-builds/current/BUILD_ID" 2>/dev/null || true)"
    echo "release=$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)"
  } > "$STATUS_FILE"
  # A proven recovery closes the previous incident. If the same release breaks
  # again later, that is a new incident and may be escalated once again.
  if [ "$state" = "PASS" ]; then
    rm -f "$ALERT_FILE"
  fi
}

notify_owner_failure_once() {
  local reason="$1"
  local release fingerprint previous=""
  release="$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)"
  fingerprint="$release|$reason"
  if [ -f "$ALERT_FILE" ]; then
    previous="$(cat "$ALERT_FILE" 2>/dev/null || true)"
  fi
  if [ "$previous" = "$fingerprint" ]; then
    echo "WATCH_ALERT=SKIP reason=duplicate_incident"
    return 0
  fi

  # Use BORIS' existing owner-notification channel. This is called only after
  # retry + one safe frontend restart failed; no technical progress spam.
  if (
    cd /root/BORIS/backend &&
    "$PY" - "$reason" <<'PY'
import sys
from app.ext_api import notify
reason = sys.argv[1]
message = (
    "BORIS: клиентский запуск рекламной кампании с телефона/ПК не "
    "восстановился автоматически. Повторная проверка и безопасный "
    "перезапуск frontend уже выполнены; повторять их вручную не нужно. "
    "Нужно исправление кода/production-контура. Причина: " + reason
)
raise SystemExit(0 if notify.send(message) else 2)
PY
  ); then
    printf '%s\n' "$fingerprint" > "$ALERT_FILE"
    echo "WATCH_ALERT=SENT"
    return 0
  fi

  # Alert delivery failure must not hide the real runtime failure.
  logger -t "$LOG_TAG" -p user.err "owner escalation delivery failed for mobile/campaign runtime incident"
  echo "WATCH_ALERT=FAIL"
  return 1
}

if run_gates; then
  write_status PASS initial
  echo "WATCH=PASS"
  exit 0
fi

# First retry without touching production: protects against a short network/auth blip.
# A deploy may begin while the first browser suite is running; never fight it.
if deploy_in_progress; then
  skip_for_deploy deploy_started_after_first_failure
fi
sleep 12
if deploy_in_progress; then
  skip_for_deploy deploy_started_before_retry
fi
if run_gates; then
  write_status PASS retry
  echo "WATCH=PASS recovered=retry"
  exit 0
fi

# If current release changed while we were checking it, verify the new release
# before considering a restart. This avoids restarting a healthy just-deployed build.
CURRENT_RELEASE="$(readlink -f "$ROOT/.boris-builds/current" 2>/dev/null || true)"
if [ "$CURRENT_RELEASE" != "$START_RELEASE" ]; then
  if deploy_in_progress; then
    skip_for_deploy release_changed_during_deploy
  fi
  sleep 5
  if run_gates; then
    write_status PASS release_changed_reverified
    echo "WATCH=PASS recovered=release_changed_reverified"
    exit 0
  fi
fi

if deploy_in_progress; then
  skip_for_deploy deploy_started_before_self_heal
fi

# A deterministic code/runtime defect must not cause an endless restart loop.
# After one failed self-heal on the same immutable release, future ticks still
# re-check the real user path but do not restart frontend again.
if previous_terminal_failure_same_release; then
  write_status FAIL persistent_previous_failure
  logger -t "$LOG_TAG" -p user.err "runtime incident persists on same release; repeated frontend restart suppressed"
  notify_owner_failure_once "mobile_campaign_runtime_unrecovered" || true
  echo "WATCH=FAIL reason=persistent_previous_failure restart_suppressed=1"
  exit 1
fi

# Do not "heal" the frontend for a backend/API outage. That belongs to the
# production/backend guardian. Keep retrying on the next maintenance tick.
if ! public_api_alive; then
  write_status DEFER dependency_public_api_unavailable
  logger -t "$LOG_TAG" -p user.warning "runtime gate failed but public API is unavailable; frontend restart skipped"
  echo "WATCH=DEFER reason=dependency_public_api_unavailable"
  exit 0
fi

# Safe self-heal: one frontend restart, then prove the same real user scenarios.
# Backend, DB, campaigns and client data are not modified.
logger -t "$LOG_TAG" -p user.warning "mobile/campaign runtime gate failed twice; restarting boris-frontend once"
if ! restart_frontend; then
  write_status FAIL frontend_restart_not_permitted
  logger -t "$LOG_TAG" -p user.err "runtime self-heal could not restart boris-frontend"
  notify_owner_failure_once "mobile_campaign_runtime_unrecovered" || true
  echo "WATCH=FAIL reason=frontend_restart_not_permitted"
  exit 1
fi
for _ in $(seq 1 30); do
  local_code="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:3002/dashboard/command-center || true)"
  public_code="$(curl -sS -o /dev/null -w '%{http_code}' https://boris-ai.pro/dashboard/command-center || true)"
  if [ "$local_code" = 200 ] && [ "$public_code" = 200 ]; then
    break
  fi
  sleep 1
done

if run_gates; then
  write_status PASS restart_self_heal
  logger -t "$LOG_TAG" -p user.notice "runtime self-heal PASS after one frontend restart"
  echo "WATCH=PASS recovered=restart_self_heal"
  exit 0
fi

write_status FAIL needs_owner_or_code_fix
logger -t "$LOG_TAG" -p user.err "runtime gate still FAIL after retry+restart; manual/code intervention required"
notify_owner_failure_once "mobile_campaign_runtime_unrecovered" || true
echo "WATCH=FAIL after=retry+restart"
exit 1
