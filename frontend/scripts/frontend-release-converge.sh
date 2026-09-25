#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/frontend
cd "$ROOT"
mkdir -p "$ROOT/.boris-builds"

# FRONTEND_RELEASE_CONVERGE_SINGLETON_V1: parallel chats/guardians may all
# notice the same source drift. Only one convergence owner may build/retry it;
# duplicate callers exit successfully because the active owner is responsible
# for convergence. This prevents a queue of redundant Next.js builds.
LOCK="$ROOT/.boris-builds/frontend-release-converge.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "FRONTEND_RELEASE_CONVERGE_ALREADY_RUNNING=PASS"
  exit 0
fi

LOG="$ROOT/.boris-builds/frontend_release_converge_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

active_source_hash() {
  python3 - "$ROOT/.boris-builds/current/BORIS_ARTIFACT.json" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get("snapshot_source_hash") or "")
except Exception: print("")
PY
}

# FRONTEND_RELEASE_ALREADY_CURRENT_V1: do not spend CPU rebuilding an artifact
# that is already the exact current source revision.
CURRENT0=$(python3 scripts/frontend-source-hash.py)
ACTIVE0=$(active_source_hash)
if [ -n "$ACTIVE0" ] && [ "$CURRENT0" = "$ACTIVE0" ]; then
  echo "FRONTEND_RELEASE_ALREADY_CURRENT=PASS hash=$CURRENT0"
  exit 0
fi

echo "FRONTEND_RELEASE_CONVERGE_START $(date -u +%FT%TZ)"
for attempt in $(seq 1 20); do
  echo "=== attempt=$attempt $(date -u +%FT%TZ) ==="
  OUT=$(npm run build 2>&1) || { rc=$?; echo "$OUT"; echo "BUILD_RETRY rc=$rc"; sleep 20; continue; }
  echo "$OUT"
  ARTIFACT=$(printf '%s\n' "$OUT" | sed -n 's/^ARTIFACT=//p' | tail -1)
  SNAP=$(printf '%s\n' "$OUT" | sed -n 's/^SNAPSHOT_SOURCE_HASH=//p' | tail -1)
  CURRENT=$(python3 scripts/frontend-source-hash.py)
  echo "SNAPSHOT=$SNAP CURRENT=$CURRENT ARTIFACT=$ARTIFACT"
  if [ -z "$ARTIFACT" ] || [ -z "$SNAP" ] || [ "$SNAP" != "$CURRENT" ]; then
    echo "SOURCE_CHANGED_DURING_BUILD; retrying current source"
    sleep 20
    continue
  fi
  npm run deploy -- "$ARTIFACT"
  POST=$(python3 scripts/frontend-source-hash.py)
  ACTIVE=$(python3 - "$ROOT/.boris-builds/current/BORIS_ARTIFACT.json" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1]))['snapshot_source_hash'])
except Exception: print('')
PY
)
  echo "POST=$POST ACTIVE=$ACTIVE"
  if [ "$POST" != "$ACTIVE" ]; then
    echo "SOURCE_CHANGED_AFTER_DEPLOY; converge again"
    sleep 20
    continue
  fi
  curl -fsS -o /dev/null -w 'PUBLIC_DASHBOARD=%{http_code}\n' https://boris-ai.pro/dashboard
  curl -fsS -o /dev/null -w 'PUBLIC_CAMPAIGN=%{http_code}\n' https://boris-ai.pro/dashboard/campaign
  curl -fsS -o /dev/null -w 'PUBLIC_BANNERS=%{http_code}\n' https://boris-ai.pro/dashboard/campaign/banners
  echo "FRONTEND_RELEASE_CONVERGED=PASS $(date -u +%FT%TZ)"
  echo "LOG=$LOG"
  exit 0
done

echo "FRONTEND_RELEASE_CONVERGED=FAIL $(date -u +%FT%TZ)"
echo "LOG=$LOG"
exit 1
