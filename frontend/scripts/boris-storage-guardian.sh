#!/bin/bash
set -euo pipefail
ROOT=/root/BORIS/frontend/.boris-builds
BACKEND=/root/BORIS/backend
LOCK=/run/lock/boris-storage-guardian.lock
exec 9>"$LOCK"; flock -n 9 || exit 0
cd "$BACKEND"

# Durable paid-generation idempotency is intentionally short-lived. Keep seven
# days for crash/retry recovery, then remove old successful cache rows so 100+
# tenants cannot grow the generic storage table forever. Bounded to 5000 rows
# per guardian run; malformed rows are ignored rather than breaking storage QA.
cache_deleted=$(/root/BORIS/backend/venv/bin/python3 - <<'PY'
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from app.db.session import SessionLocal
cutoff = datetime.now(timezone.utc) - timedelta(days=7)
db = SessionLocal(); ids = []
try:
    rows = db.execute(text("select id,value from storage where key like 'generation_idempotency:%' order by id asc limit 10000")).fetchall()
    for row_id, raw in rows:
        try:
            data = json.loads(raw or '{}')
            created = datetime.fromisoformat(str(data.get('created_at') or ''))
            if created.tzinfo is None: created = created.replace(tzinfo=timezone.utc)
            result = data.get('result') or {}
            if created < cutoff and isinstance(result, dict) and result.get('status') == 'ok':
                ids.append(int(row_id))
                if len(ids) >= 5000: break
        except Exception:
            continue
    if ids:
        db.execute(text("delete from storage where id in (" + ",".join(str(x) for x in ids) + ")"))
        db.commit()
    print(len(ids))
finally:
    db.close()
PY
)

used=$(df -P / | awk 'NR==2{gsub("%","",$5);print $5}')

# FACTORY_BACKUP_PRESSURE_RETENTION_V1: legacy development Factory snapshots
# are not production deploy targets. They used to accumulate indefinitely and
# can consume tens of GB while the active production tree lives in /root/BORIS.
# Under >=88% filesystem pressure keep the 20 newest rollback points, delete
# only directories older than 14 days, stop as soon as usage returns to <=85%,
# and bound every run to 30 directories.
factory_backup_deleted=0
factory_backup_bytes=0
factory_backup_dir=/root/BORIS_FACTORY_BACKUPS
if [ "$used" -ge 88 ] && [ -d "$factory_backup_dir" ]; then
  declare -A keep_factory=()
  while IFS= read -r d; do
    [ -n "$d" ] && keep_factory["$d"]=1
  done < <(find "$factory_backup_dir" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -20 | cut -d' ' -f2-)
  while IFS= read -r line; do
    [ "$factory_backup_deleted" -ge 30 ] && break
    used_now=$(df -P / | awk 'NR==2{gsub("%","",$5);print $5}')
    [ "$used_now" -le 85 ] && break
    d=${line#* }; [ -d "$d" ] || continue
    [ -n "${keep_factory[$d]:-}" ] && continue
    find "$d" -maxdepth 0 -mmin +20160 -print -quit | grep -q . || continue
    sz=$(du -sb "$d" 2>/dev/null | awk '{print $1}' || echo 0)
    rm -rf --one-file-system "$d"
    factory_backup_deleted=$((factory_backup_deleted+1))
    factory_backup_bytes=$((factory_backup_bytes+sz))
  done < <(find "$factory_backup_dir" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -n)
  used=$(df -P / | awk 'NR==2{gsub("%","",$5);print $5}')
fi

# DB_BACKUP_PRESSURE_RETENTION_V1: generic automatic DB snapshots can be >200 MB
# each and several same-day copies accumulated during rollout. Only under >=88%
# pressure, keep the newest snapshot overall plus the newest snapshot for each UTC
# day in the last 7 days. Delete only older same-day duplicates (>6h old), bounded
# to 10 files/run. Named/manual/control-plane backups are outside this pattern.
db_backup_deleted=0
db_backup_bytes=0
if [ "$used" -ge 88 ] && [ -d /root/BORIS/backups ]; then
  declare -A keep_db=()
  declare -A seen_days=()
  newest_db=$(find /root/BORIS/backups -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)
  [ -n "$newest_db" ] && keep_db["$newest_db"]=1
  while IFS= read -r line; do
    f=${line#* }; [ -f "$f" ] || continue
    day=$(date -u -r "$f" +%F 2>/dev/null || true); [ -n "$day" ] || continue
    if [ -z "${seen_days[$day]:-}" ]; then
      seen_days["$day"]=1
      age_days=$(( ( $(date -u +%s) - $(stat -c %Y "$f") ) / 86400 ))
      [ "$age_days" -le 7 ] && keep_db["$f"]=1
    fi
  done < <(find /root/BORIS/backups -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr)
  while IFS= read -r line; do
    [ "$db_backup_deleted" -ge 10 ] && break
    f=${line#* }; [ -f "$f" ] || continue
    [ -n "${keep_db[$f]:-}" ] && continue
    find "$f" -maxdepth 0 -mmin +360 -print -quit | grep -q . || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    rm -f -- "$f"
    db_backup_deleted=$((db_backup_deleted+1)); db_backup_bytes=$((db_backup_bytes+sz))
  done < <(find /root/BORIS/backups -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -n)
fi

# SCHEMA_DB_BACKUP_PRESSURE_RETENTION_V1: rollout/schema migrations also create
# generic boris_db_*.sql.gz snapshots under backend/backups/schema. Under >=88%
# pressure keep newest overall plus newest per UTC day for 7 days, and delete
# only older same-day generic duplicates older than 2h. Named/manual/control-plane
# snapshots are deliberately excluded by the exact filename pattern.
schema_db_backup_deleted=0
schema_db_backup_bytes=0
schema_dir=/root/BORIS/backend/backups/schema
if [ "$used" -ge 88 ] && [ -d "$schema_dir" ]; then
  declare -A keep_schema_db=()
  declare -A seen_schema_days=()
  newest_schema_db=$(find "$schema_dir" -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)
  [ -n "$newest_schema_db" ] && keep_schema_db["$newest_schema_db"]=1
  while IFS= read -r line; do
    f=${line#* }; [ -f "$f" ] || continue
    day=$(date -u -r "$f" +%F 2>/dev/null || true); [ -n "$day" ] || continue
    if [ -z "${seen_schema_days[$day]:-}" ]; then
      seen_schema_days["$day"]=1
      age_days=$(( ( $(date -u +%s) - $(stat -c %Y "$f") ) / 86400 ))
      [ "$age_days" -le 7 ] && keep_schema_db["$f"]=1
    fi
  done < <(find "$schema_dir" -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr)
  while IFS= read -r line; do
    [ "$schema_db_backup_deleted" -ge 10 ] && break
    f=${line#* }; [ -f "$f" ] || continue
    [ -n "${keep_schema_db[$f]:-}" ] && continue
    find "$f" -maxdepth 0 -mmin +120 -print -quit | grep -q . || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    rm -f -- "$f"
    schema_db_backup_deleted=$((schema_db_backup_deleted+1)); schema_db_backup_bytes=$((schema_db_backup_bytes+sz))
  done < <(find "$schema_dir" -maxdepth 1 -type f -name 'boris_db_*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -n)
fi

# Build workspaces are disposable snapshots, never deploy targets. A failed or
# interrupted build used to leave ~66 MB behind forever because artifact cleanup
# only runs at high disk pressure. Remove only workdirs older than 6h and cap the
# cleanup per run, so a currently running isolated build can never be touched.
work_deleted=0
if [ -d "$ROOT/work" ]; then
  while IFS= read -r d; do
    [ "$work_deleted" -ge 20 ] && break
    [ -d "$d" ] || continue
    rm -rf --one-file-system "$d"
    work_deleted=$((work_deleted+1))
  done < <(find "$ROOT/work" -mindepth 1 -maxdepth 1 -type d -mmin +360 -print | sort)
fi

# Artifact cleanup remains conservative below 82% disk usage. Cache/work GC
# above is independent because it protects DB/build growth before disk pressure.
if [ "$used" -lt 82 ]; then echo "STORAGE_GUARDIAN=OK used=${used}% cache_deleted=${cache_deleted} work_deleted=${work_deleted} factory_backup_deleted=${factory_backup_deleted} factory_backup_bytes=${factory_backup_bytes} artifact_action=none db_backup_deleted=${db_backup_deleted} db_backup_bytes=${db_backup_bytes} schema_db_backup_deleted=${schema_db_backup_deleted} schema_db_backup_bytes=${schema_db_backup_bytes}"; exit 0; fi
active=$(readlink -f "$ROOT/current" 2>/dev/null || true)
ready=$(cat "$ROOT/LATEST_READY" 2>/dev/null || true)
latest=$(cat "$ROOT/LATEST_ARTIFACT" 2>/dev/null || true)
# Keep rollback depth proportional to disk pressure. Active/LATEST pointers
# are always preserved independently, so emergency GC cannot remove production.
keep_count=40
[ "$used" -ge 85 ] && keep_count=30
[ "$used" -ge 88 ] && keep_count=20
mapfile -t newest < <(find "$ROOT/artifacts" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | head -"$keep_count" | cut -d' ' -f2-)
keep=("$active" "$ready" "$latest" "${newest[@]}")
deleted=0
# At >=88% usage old immutable artifacts become emergency GC candidates after
# 2h; otherwise retain the previous conservative 24h age floor.
min_age=1440
[ "$used" -ge 88 ] && min_age=120
# Bound I/O per invocation: at most 60 immutable old artifacts.
while IFS= read -r d; do
  [ "$deleted" -ge 60 ] && break
  abs=$(readlink -f "$d" 2>/dev/null || true); preserve=0
  for k in "${keep[@]}"; do [ -n "$k" ] && [ "$abs" = "$(readlink -f "$k" 2>/dev/null || true)" ] && preserve=1 && break; done
  [ "$preserve" -eq 1 ] && continue
  # Age floor is dynamic: 24h normally, 2h only under >=88% disk pressure.
  find "$d" -maxdepth 0 -mmin +"$min_age" -print -quit | grep -q . || continue
  rm -rf --one-file-system "$d"; deleted=$((deleted+1))
done < <(find "$ROOT/artifacts" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -n | cut -d' ' -f2-)
after=$(df -P / | awk 'NR==2{print $5}')
echo "STORAGE_GUARDIAN=PASS before=${used}% after=${after} deleted=${deleted} factory_backup_deleted=${factory_backup_deleted} factory_backup_bytes=${factory_backup_bytes} db_backup_deleted=${db_backup_deleted} db_backup_bytes=${db_backup_bytes} schema_db_backup_deleted=${schema_db_backup_deleted} schema_db_backup_bytes=${schema_db_backup_bytes}"
