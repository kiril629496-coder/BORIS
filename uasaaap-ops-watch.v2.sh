#!/usr/bin/env bash
set -uo pipefail

BASE="${UASAAAP_BASE_URL:-https://uasaaap.ru}"
LOG_PREFIX="UASAAAP_OPS_WATCH"
WARN_DISK="${UASAAAP_WARN_DISK_PCT:-80}"
FAIL_DISK="${UASAAAP_FAIL_DISK_PCT:-90}"
WARN_RELEASES="${UASAAAP_WARN_RELEASE_COUNT:-80}"
FAIL_RELEASES="${UASAAAP_FAIL_RELEASE_COUNT:-120}"
WARN_BACKUP_AGE="${UASAAAP_WARN_BACKUP_AGE_SEC:-108000}"
FAIL_BACKUP_AGE="${UASAAAP_FAIL_BACKUP_AGE_SEC:-172800}"
bad=0
warn=0

check_url() {
  local path="$1" code
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 12 "${BASE%/}$path" 2>/dev/null || true)"
  if [[ "$code" != "200" ]]; then
    echo "$LOG_PREFIX FAIL url=${BASE%/}$path code=${code:-000}"
    bad=1
  fi
}

check_active() {
  local unit="$1" label="$2"
  if ! systemctl is-active --quiet "$unit"; then
    echo "$LOG_PREFIX FAIL $label=inactive"
    bad=1
  fi
}

check_active uasaaap.service service
check_active uasaaap-backup.timer backup_timer
check_active uasaaap-release-retention.timer retention_timer
check_active uasaaap-internal-readiness.timer internal_readiness_timer

check_url "/"
check_url "/api/v2/health"
check_url "/api/v2/health/ready"
check_url "/manifest.webmanifest"

data_mode="$(stat -c %a /srv/uasaaap/shared/data 2>/dev/null || echo missing)"
backup_mode="$(stat -c %a /srv/uasaaap/backups 2>/dev/null || echo missing)"
if [[ "$data_mode" != "700" ]]; then
  echo "$LOG_PREFIX FAIL data_mode=$data_mode expected=700"
  bad=1
fi
if [[ "$backup_mode" != "700" ]]; then
  echo "$LOG_PREFIX FAIL backup_mode=$backup_mode expected=700"
  bad=1
fi

source_release="$(cat /srv/uasaaap/current/RELEASE_SHA 2>/dev/null | tr -d '[:space:]')"
runtime_release="$(curl -fsS --max-time 5 http://127.0.0.1:4173/health 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("release",""))' 2>/dev/null || true)"
current_target="$(basename "$(realpath /srv/uasaaap/current 2>/dev/null || true)")"
if [[ ! "$source_release" =~ ^[0-9a-f]{40}$ ]]; then
  echo "$LOG_PREFIX FAIL source_release=invalid"
  bad=1
elif [[ "$runtime_release" != "$source_release" ]]; then
  echo "$LOG_PREFIX FAIL release_parity=runtime_mismatch"
  bad=1
elif [[ "$current_target" != "${source_release:0:12}-"* ]]; then
  echo "$LOG_PREFIX FAIL release_parity=current_symlink_mismatch"
  bad=1
elif ! grep -qF "Environment=RELEASE_SHA=$source_release" /etc/systemd/system/uasaaap.service.d/99-release.conf 2>/dev/null; then
  echo "$LOG_PREFIX FAIL release_parity=systemd_env_mismatch"
  bad=1
fi

disk_pct="$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
if [[ "$disk_pct" =~ ^[0-9]+$ ]]; then
  if (( disk_pct >= FAIL_DISK )); then
    echo "$LOG_PREFIX FAIL disk_pct=$disk_pct threshold=$FAIL_DISK"
    bad=1
  elif (( disk_pct >= WARN_DISK )); then
    echo "$LOG_PREFIX WARN disk_pct=$disk_pct threshold=$WARN_DISK"
    warn=1
  fi
fi

release_count="$(find /srv/uasaaap/releases -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$release_count" =~ ^[0-9]+$ ]]; then
  if (( release_count >= FAIL_RELEASES )); then
    echo "$LOG_PREFIX FAIL release_count=$release_count threshold=$FAIL_RELEASES"
    bad=1
  elif (( release_count >= WARN_RELEASES )); then
    echo "$LOG_PREFIX WARN release_count=$release_count threshold=$WARN_RELEASES"
    warn=1
  fi
fi

backup_marker=/srv/uasaaap/backups/verified-backup.json
if [[ -f "$backup_marker" ]]; then
  backup_mtime="$(stat -c %Y "$backup_marker" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  backup_age=$((now-backup_mtime))
  if (( backup_age >= FAIL_BACKUP_AGE )); then
    echo "$LOG_PREFIX FAIL backup_age_sec=$backup_age threshold=$FAIL_BACKUP_AGE"
    bad=1
  elif (( backup_age >= WARN_BACKUP_AGE )); then
    echo "$LOG_PREFIX WARN backup_age_sec=$backup_age threshold=$WARN_BACKUP_AGE"
    warn=1
  fi
else
  echo "$LOG_PREFIX FAIL backup_marker=missing"
  bad=1
  backup_age=-1
fi

if (( bad == 0 )); then
  if (( warn == 1 )); then
    echo "$LOG_PREFIX PASS_WITH_WARNINGS base=$BASE disk_pct=${disk_pct:-unknown} release_count=${release_count:-unknown} backup_age_sec=${backup_age:-unknown}"
  else
    echo "$LOG_PREFIX PASS base=$BASE disk_pct=${disk_pct:-unknown} release_count=${release_count:-unknown} backup_age_sec=${backup_age:-unknown}"
  fi
  exit 0
fi

echo "$LOG_PREFIX RETRY"
sleep 3
bad=0
check_url "/"
check_url "/api/v2/health"
check_url "/api/v2/health/ready"
check_active uasaaap.service service
check_active uasaaap-backup.timer backup_timer
check_active uasaaap-release-retention.timer retention_timer
check_active uasaaap-internal-readiness.timer internal_readiness_timer
if (( bad == 0 )); then
  echo "$LOG_PREFIX RECOVERED"
  exit 0
fi

echo "$LOG_PREFIX CRITICAL"
exit 1
