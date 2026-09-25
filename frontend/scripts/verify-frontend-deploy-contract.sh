#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/BORIS/frontend"
BUILD="$ROOT/scripts/boris-frontend-build.sh"
DEPLOY="$ROOT/scripts/boris-frontend-deploy.sh"
NEXTCFG="$ROOT/next.config.ts"
UNIT="/etc/systemd/system/boris-frontend.service"
CURRENT="$ROOT/.boris-builds/current"

fail() { echo "FRONTEND_DEPLOY_CONTRACT=FAIL $*"; exit 1; }

# Canonical build/deploy entrypoints.
grep -Fq '"build": "bash scripts/boris-frontend-build.sh"' "$ROOT/package.json" || fail "package build wrapper"
grep -Fq '"deploy": "bash scripts/boris-frontend-deploy.sh"' "$ROOT/package.json" || fail "package deploy wrapper"

# Direct production builds must fail closed; isolated work is the only build location.
grep -Fq 'BORIS_SAFE_BUILD_REQUIRED' "$NEXTCFG" || fail "direct build guard"
grep -Fq '.boris-builds/work' "$NEXTCFG" || fail "isolated work guard"
grep -Fq 'WORK_ROOT="$STORE/work"' "$BUILD" || fail "isolated build workspace"
grep -Fq 'ARTIFACT_ROOT="$STORE/artifacts"' "$BUILD" || fail "artifact store"
grep -Fq "'status': 'READY'" "$BUILD" || fail "READY artifact metadata"
grep -Fq "'immutable': True" "$BUILD" || fail "immutable artifact metadata"

# Production must be release-symlink based, never project-root .next based.
grep -Fq 'Environment=NEXT_DIST_DIR=.boris-builds/current' "$UNIT" || fail "runtime current release binding"
grep -Fq 'CURRENT="$STORE/current"' "$DEPLOY" || fail "current release switch"
grep -Fq 'flock -w' "$DEPLOY" || fail "deploy lock"
grep -Fq 'mv -Tf' "$DEPLOY" || fail "atomic symlink rename"
grep -Fq 'ROLLBACK=PASS' "$DEPLOY" || fail "rollback path"

# Forbidden regressions: deploy must not wait for builders/current source, and must never mutate live root .next.
if grep -Eq 'SOURCE_MISMATCH|ACTIVE_BUILDERS|pgrep[^\n]*(next build|npm run build)' "$DEPLOY"; then
  fail "safe-window/source-coupling guard returned"
fi
if grep -Eq 'stale artifact source hash mismatch|artifact became stale while waiting|source changed during deployment|REBUILD_REQUIRED=1' "$DEPLOY"; then
  fail "source-hash deploy blocker returned"
fi
grep -Fq 'SOURCE_MOVED_AFTER_SNAPSHOT=1' "$DEPLOY" || fail "source drift diagnostic missing"
grep -Fq 'SOURCE_MOVED_WHILE_WAITING=1' "$DEPLOY" || fail "post-lock source drift diagnostic missing"
grep -Fq 'SOURCE_MOVED_DURING_DEPLOY=1' "$DEPLOY" || fail "final source drift diagnostic missing"
grep -Fq 'FRONTEND_DEPLOY_MONOTONIC_RELEASE_GUARD_V1' "$DEPLOY" || fail "monotonic release guard missing"
grep -Fq 'target artifact is older than active production release' "$DEPLOY" || fail "older-artifact rollback guard missing"
if grep -Eq 'rm -rf[[:space:]]+"?\$ROOT/\.next|mv[[:space:]].*"?\$ROOT/\.next|\.next-deploy' "$DEPLOY"; then
  fail "live .next mutation returned"
fi

TARGET="$(readlink -f "$CURRENT" 2>/dev/null || true)"
[ -n "$TARGET" ] || fail "current symlink unresolved"
case "$TARGET" in
  "$ROOT/.boris-builds/artifacts/"*) ;;
  *) fail "current outside artifact store";;
esac
[ -f "$CURRENT/BUILD_ID" ] || fail "current BUILD_ID missing"
[ -f "$CURRENT/BORIS_ARTIFACT.json" ] || fail "current metadata missing"

python3 - "$CURRENT/BORIS_ARTIFACT.json" "$CURRENT/BUILD_ID" <<'PY' || exit 1
import json, pathlib, sys
meta = json.load(open(sys.argv[1], encoding='utf-8'))
bid = pathlib.Path(sys.argv[2]).read_text().strip()
assert meta.get('status') == 'READY', 'current not READY'
assert meta.get('immutable') is True, 'current not immutable'
assert meta.get('build_id') == bid, 'current BUILD_ID mismatch'
PY

echo "FRONTEND_DEPLOY_CONTRACT=PASS"
echo "LIVE_NEXT_BUILD_WRITES=0"
echo "ACTIVE_RELEASE=$TARGET"
echo "ACTIVE_BUILD_ID=$(cat "$CURRENT/BUILD_ID")"
