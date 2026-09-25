#!/bin/bash
set -euo pipefail
BACKUP_DIR=/root/BORIS/backups
STATUS="$BACKUP_DIR/restore_qa_status.json"
LOCK=/run/lock/boris-backup-restore.lock
mkdir -p "$BACKUP_DIR"
exec 9>"$LOCK"
flock -n 9 || exit 0
LATEST=$(ls -1t "$BACKUP_DIR"/boris_db_*.sql.gz 2>/dev/null | head -1 || true)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
write_fail(){ python3 - "$STATUS" "$NOW" "$LATEST" "$1" <<'PY'
import json,sys,os
path,now,backup,error=sys.argv[1:]
tmp=path+'.tmp'
json.dump({'ok':False,'state':'failed','checked_at':now,'backup':os.path.basename(backup) if backup else None,'error':error[:500]},open(tmp,'w'))
os.replace(tmp,path)
PY
}
if [ -z "$LATEST" ]; then write_fail 'backup_not_found'; exit 1; fi
# If this exact immutable backup already passed a full restore, do not spend CPU
# re-reading 121MB gzip on every manual/timer invocation. New backups are always
# integrity-tested before their first restore.
if python3 - "$STATUS" "$LATEST" <<'PY'
import json,sys,os
try:d=json.load(open(sys.argv[1]))
except: raise SystemExit(1)
raise SystemExit(0 if d.get('ok') and d.get('backup')==os.path.basename(sys.argv[2]) else 1)
PY
then exit 0; fi
if ! gzip -t "$LATEST"; then write_fail 'gzip_integrity_failed'; exit 1; fi
# Restore QA is intentionally guaranteed for every new backup. The service runs
# Nice=15 / idle I/O, so a busy host may make it slower but must not silently
# postpone disaster-recovery proof until the next day.
TMPDB="boris_restore_qa_$(date +%Y%m%d_%H%M%S)_$$"
cleanup(){ sudo -u postgres dropdb --if-exists "$TMPDB" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if ! sudo -u postgres createdb "$TMPDB"; then write_fail 'temporary_database_create_failed'; exit 1; fi
START=$(date +%s)
if ! gzip -dc "$LATEST" | sudo -u postgres psql --set ON_ERROR_STOP=1 "$TMPDB" >/tmp/boris_restore_qa.log 2>&1; then write_fail "restore_failed:$(tail -1 /tmp/boris_restore_qa.log)"; exit 1; fi
END=$(date +%s)
TABLES=$(sudo -u postgres psql -At "$TMPDB" -c "select count(*) from pg_tables where schemaname='public'")
ACCOUNTS=$(sudo -u postgres psql -At "$TMPDB" -c "select count(*) from accounts" 2>/dev/null || echo 0)
JOBS=$(sudo -u postgres psql -At "$TMPDB" -c "select count(*) from background_jobs" 2>/dev/null || echo 0)
SIZE_BYTES=$(sudo -u postgres psql -At "$TMPDB" -c "select pg_database_size(current_database())")
if [ "$TABLES" -lt 50 ]; then write_fail "restore_suspicious_table_count:$TABLES"; exit 1; fi
python3 - "$STATUS" "$NOW" "$LATEST" "$((END-START))" "$TABLES" "$ACCOUNTS" "$JOBS" "$SIZE_BYTES" <<'PY'
import json,sys,os
path,now,b,secs,tables,accounts,jobs,size=sys.argv[1:]
data={'ok':True,'state':'pass','checked_at':now,'backup':os.path.basename(b),'restore_seconds':int(secs),'tables':int(tables),'accounts':int(accounts),'background_jobs':int(jobs),'restored_size_mb':round(int(size)/1048576,1),'temporary_db_dropped':True}
tmp=path+'.tmp'; json.dump(data,open(tmp,'w')); os.replace(tmp,path)
PY
cleanup; trap - EXIT
