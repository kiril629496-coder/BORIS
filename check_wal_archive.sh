#!/bin/bash
set -euo pipefail
DIR=/var/lib/postgresql/16/wal_archive
exec 9>/run/lock/boris-wal-guardian.lock; flock -n 9 || exit 0
status=$(sudo -u postgres psql -Atqc "select archived_count||'|'||failed_count||'|'||coalesce(last_archived_wal,'')||'|'||coalesce(last_failed_wal,'') from pg_stat_archiver")
count=$(find "$DIR" -maxdepth 1 -type f | wc -l)
# WAL older than 72h is redundant for this local staging archive; offsite is separate and not claimed here.
find "$DIR" -maxdepth 1 -type f -mmin +4320 -delete
printf '%s WAL_ARCHIVE files=%s stats=%s\n' "$(date -Is)" "$count" "$status"
