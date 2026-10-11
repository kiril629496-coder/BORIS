#!/usr/bin/env bash
set -euo pipefail

ROOT="${UASAAAP_ROOT:-/srv/uasaaap}"
RELEASES="$ROOT/releases"
DEPLOY_LOCK="${UASAAAP_DEPLOY_LOCK:-/opt/uasaaap/deploy.lock}"
MIN_AGE_HOURS="${UASAAAP_RELEASE_MIN_AGE_HOURS:-72}"
KEEP_RECENT="${UASAAAP_RELEASE_KEEP_RECENT:-10}"
MAX_DELETE="${UASAAAP_RELEASE_MAX_DELETE:-10}"
MODE="${1:---dry-run}"

case "$MODE" in
  --dry-run|--apply) ;;
  *) echo "usage: $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac

[ -d "$RELEASES" ] || { echo "BLOCK: releases directory missing" >&2; exit 3; }
[ "$MIN_AGE_HOURS" -ge 24 ] || { echo "BLOCK: MIN_AGE_HOURS must be >=24" >&2; exit 4; }
[ "$KEEP_RECENT" -ge 2 ] || { echo "BLOCK: KEEP_RECENT must be >=2" >&2; exit 5; }
[ "$MAX_DELETE" -ge 0 ] || { echo "BLOCK: MAX_DELETE must be >=0" >&2; exit 6; }

if [ "$MODE" = "--apply" ]; then
  # Serialize only the mutation phase against canonical autodeploy.
  mkdir -p "$(dirname "$DEPLOY_LOCK")"
  exec 9>"$DEPLOY_LOCK"
  flock -n 9 || { echo "BLOCK: deploy lock busy" >&2; exit 7; }
fi

now="$(date +%s)"
cutoff="$((now-MIN_AGE_HOURS*3600))"

declare -A keep_recent=()
while IFS= read -r dir; do
  [ -n "$dir" ] && keep_recent["$dir"]=1
done < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | head -n "$KEEP_RECENT" | cut -d' ' -f2-)

protected_path() {
  local candidate="$1" target link
  for link in "$ROOT/current" "$ROOT/previous"; do
    target="$(realpath "$link" 2>/dev/null || true)"
    [ -n "$target" ] && [ "$candidate" = "$target" ] && return 0
  done
  while IFS= read -r link; do
    target="$(realpath "$link" 2>/dev/null || true)"
    [ -n "$target" ] && [ "$candidate" = "$target" ] && return 0
  done < <(find "$ROOT" -maxdepth 3 -type l -print 2>/dev/null)
  return 1
}

process_uses_release() {
  local candidate="$1" p target
  for p in /proc/[0-9]*/cwd /proc/[0-9]*/exe; do
    target="$(realpath "$p" 2>/dev/null || true)"
    case "$target" in "$candidate"|"$candidate"/*) return 0;; esac
  done
  return 1
}

current_before="$(realpath "$ROOT/current")"
previous_before="$(realpath "$ROOT/previous")"
printf 'MODE=%s\nMIN_AGE_HOURS=%s\nKEEP_RECENT=%s\nMAX_DELETE=%s\nCURRENT=%s\nPREVIOUS=%s\n' \
  "$MODE" "$MIN_AGE_HOURS" "$KEEP_RECENT" "$MAX_DELETE" "$current_before" "$previous_before"

count=0
skipped=0
reclaimable_kb=0

while IFS= read -r dir; do
  [ -d "$dir" ] || continue
  mtime="$(stat -c %Y "$dir")"
  if [ "$mtime" -ge "$cutoff" ]; then
    skipped=$((skipped+1))
    continue
  fi

  if [ -n "${keep_recent[$dir]:-}" ]; then
    echo "KEEP recent $dir"
    skipped=$((skipped+1))
    continue
  fi

  if protected_path "$dir" || process_uses_release "$dir"; then
    echo "KEEP protected $dir"
    skipped=$((skipped+1))
    continue
  fi

  if [ "$MAX_DELETE" -gt 0 ] && [ "$count" -ge "$MAX_DELETE" ]; then
    echo "KEEP batch-limit $dir"
    skipped=$((skipped+1))
    continue
  fi

  if [ "$MODE" = "--dry-run" ]; then
    size_kb="$(du -skx "$dir" 2>/dev/null | awk '{print $1}')"
    size_kb="${size_kb:-0}"
    reclaimable_kb=$((reclaimable_kb+size_kb))
    printf 'WOULD_DELETE %sKB %s\n' "$size_kb" "$dir"
  else
    printf 'DELETE %s\n' "$dir"
    rm -rf --one-file-system "$dir"
  fi
  count=$((count+1))
done < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -n | cut -d' ' -f2-)

current_after="$(realpath "$ROOT/current")"
previous_after="$(realpath "$ROOT/previous")"
[ "$current_after" = "$current_before" ] || { echo "BLOCK: current changed during retention" >&2; exit 8; }
[ "$previous_after" = "$previous_before" ] || { echo "BLOCK: previous changed during retention" >&2; exit 9; }

printf 'RETENTION_STATUS=PASS\nCANDIDATES=%s\nSKIPPED=%s\nRECLAIMABLE_KB=%s\n' "$count" "$skipped" "$reclaimable_kb"
