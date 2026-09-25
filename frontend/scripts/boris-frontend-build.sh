#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/BORIS/frontend"
STORE="$ROOT/.boris-builds"
WORK_ROOT="$STORE/work"
ARTIFACT_ROOT="$STORE/artifacts"
HASHER="$ROOT/scripts/frontend-source-hash.py"

cd "$ROOT"

# FRONTEND_BUILD_DIAGNOSTIC_NO_BUILD_V1: read-only callers must never enter the
# production build lock or invoke Next.js. This flag is used by health/deploy
# diagnostics and must stay side-effect free.
case "${1:-}" in
  "")
    ;;
  --print-source-hash)
    [ "$#" -eq 1 ] || { echo "FRONTEND_BUILD=FAIL unexpected arguments" >&2; exit 64; }
    python3 "$HASHER"
    exit 0
    ;;
  *)
    echo "FRONTEND_BUILD=FAIL unsupported argument: $1" >&2
    exit 64
    ;;
esac

mkdir -p "$WORK_ROOT" "$ARTIFACT_ROOT"

# FRONTEND_BUILD_READY_DEDUPE_V1: identical source must reuse the newest READY
# immutable artifact instead of consuming CPU on another Next.js build. Check
# both before and after the lock: a concurrent builder may finish while this
# process is waiting.
reuse_ready_artifact() {
  local expected_hash="$1"
  local candidate=""
  [ -f "$STORE/LATEST_READY" ] || return 1
  candidate="$(cat "$STORE/LATEST_READY" 2>/dev/null || true)"
  [ -n "$candidate" ] && [ -d "$candidate" ] && [ -f "$candidate/BORIS_ARTIFACT.json" ] || return 1
  local artifact_hash=""
  artifact_hash="$(python3 - "$candidate/BORIS_ARTIFACT.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    if data.get("status") == "READY" and data.get("immutable") is True:
        print(str(data.get("snapshot_source_hash") or ""))
except Exception:
    pass
PY
)"
  [ "$artifact_hash" = "$expected_hash" ] || return 1
  echo "FRONTEND_BUILD=CACHE_HIT"
  echo "ISOLATED_BUILD=REUSED_READY"
  echo "SNAPSHOT_SOURCE_HASH=$artifact_hash"
  echo "ARTIFACT=$candidate"
  echo "LIVE_RUNTIME_TOUCHED=0"
  exit 0
}

SOURCE_HASH_BEFORE_LOCK="$(python3 "$HASHER")"
reuse_ready_artifact "$SOURCE_HASH_BEFORE_LOCK" || true

# FRONTEND_BUILD_SINGLEFLIGHT_V1: serialize autonomous production builds.
BUILD_LOCK="/run/lock/boris-frontend-build.lock"
[ -e "$BUILD_LOCK" ] || : >"$BUILD_LOCK"
exec 7>"$BUILD_LOCK"
flock -w "${BORIS_FRONTEND_BUILD_LOCK_WAIT_SEC:-900}" 7 || {
  echo "FRONTEND_BUILD=SAFE_DEFERRED another production build is still running"
  exit 75
}

SOURCE_HASH_AFTER_LOCK="$(python3 "$HASHER")"
reuse_ready_artifact "$SOURCE_HASH_AFTER_LOCK" || true

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="${BORIS_BUILD_KEY:-${STAMP}-$$-${RANDOM}}"
WORK="$WORK_ROOT/$KEY"
ARTIFACT="$ARTIFACT_ROOT/$KEY"
ARTIFACT_TMP="$ARTIFACT_ROOT/.${KEY}.tmp.$$"
LOG="$STORE/${KEY}.log"

[ ! -e "$WORK" ] || { echo "FRONTEND_BUILD=FAIL work exists: $WORK"; exit 40; }
[ ! -e "$ARTIFACT" ] || { echo "FRONTEND_BUILD=FAIL artifact exists: $ARTIFACT"; exit 41; }
mkdir -p "$WORK"
cleanup() { rm -rf "$WORK" "$ARTIFACT_TMP"; }
trap cleanup EXIT

# Snapshot source into a private workspace. Structured frontend edits use
# atomic file replacement, so each copied file is complete even while other
# developers continue changing the shared source tree.
tar -C "$ROOT" \
  --exclude='./node_modules' \
  --exclude='./.git' \
  --exclude='./.next*' \
  --exclude='./.boris-builds' \
  --exclude='./_ux_recovery_backup_*' \
  --exclude='*.bak*' \
  --exclude='*.before*' \
  --exclude='*.broken_*' \
  --exclude='*.step[0-9]*' \
  --exclude='*.old' \
  --exclude='*.orig' \
  --exclude='*.tmp' \
  -cf - . | tar -C "$WORK" -xf -
ln -s "$ROOT/node_modules" "$WORK/node_modules"

SNAPSHOT_HASH="$(cd "$WORK" && python3 scripts/frontend-source-hash.py)"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Mobile UI is a shared client-facing surface. Fail the artifact before
# Next.js compilation if any of the critical responsive/touch/navigation
# invariants that previously reached production are removed.
(
  cd "$WORK"
  python3 scripts/mobile-static-contract.py
)

# Prospecting showcase is a client-facing presentation surface. Keep it
# permanently isolated from live secrets/API controls and preserve the
# A/B + mobile + CRM contract before any production artifact is built.
(
  cd "$WORK"
  python3 scripts/qa-prospecting-showcase.py .
)

set +e
(
  cd "$WORK"
  NEXT_DIST_DIR=.next BORIS_ISOLATED_BUILD=1 "$ROOT/node_modules/.bin/next" build
) 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

if [ "$RC" -ne 0 ]; then
  echo "FRONTEND_BUILD=FAIL rc=$RC"
  echo "PRODUCTION_IMPACT=0"
  echo "WORKSPACE=$WORK"
  exit "$RC"
fi

[ -f "$WORK/.next/BUILD_ID" ] || { echo "FRONTEND_BUILD=FAIL missing BUILD_ID"; exit 42; }
BUILD_ID="$(cat "$WORK/.next/BUILD_ID")"

# Publish artifact only after the complete Next build succeeded. The final
# rename is atomic inside one filesystem; partial artifacts are never READY.
mkdir -p "$ARTIFACT_TMP"
cp -a "$WORK/.next/." "$ARTIFACT_TMP/"
cp "$LOG" "$ARTIFACT_TMP/BORIS_BUILD.log"
python3 - "$ARTIFACT_TMP/BORIS_ARTIFACT.json" "$BUILD_ID" "$SNAPSHOT_HASH" "$CREATED_AT" "$KEY" <<'PY'
import json, sys
path, build_id, snapshot_hash, created, key = sys.argv[1:]
with open(path, 'w', encoding='utf-8') as f:
    json.dump({
        'schema': 2,
        'kind': 'BORIS_FRONTEND_ARTIFACT',
        'artifact_key': key,
        'build_id': build_id,
        'snapshot_source_hash': snapshot_hash,
        'created_at': created,
        'status': 'READY',
        'immutable': True,
    }, f, ensure_ascii=False, indent=2)
    f.write('\n')
PY
[ "$(cat "$ARTIFACT_TMP/BUILD_ID")" = "$BUILD_ID" ] || { echo "FRONTEND_BUILD=FAIL BUILD_ID mismatch"; exit 43; }
mv "$ARTIFACT_TMP" "$ARTIFACT"
chmod -R a-w "$ARTIFACT" || true
printf '%s\n' "$ARTIFACT" > "$STORE/LATEST_ARTIFACT"
printf '%s\n' "$ARTIFACT" > "$STORE/LATEST_READY"

echo "FRONTEND_BUILD=PASS"
echo "ISOLATED_BUILD=PASS"
echo "ARTIFACT_STATUS=READY"
echo "BUILD_ID=$BUILD_ID"
echo "SNAPSHOT_SOURCE_HASH=$SNAPSHOT_HASH"
echo "ARTIFACT=$ARTIFACT"
echo "LIVE_RUNTIME_TOUCHED=0"
