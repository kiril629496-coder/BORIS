#!/usr/bin/env bash
set -euo pipefail

RELEASE_LINK=/root/BORIS/releases/backend/current/backend
LIVE_BACKEND=/root/BORIS/backend
PYTHON_BIN="$LIVE_BACKEND/venv/bin/python"

resolve_release(){
  local rel
  rel="$(realpath -e "$RELEASE_LINK")"
  case "$rel" in
    /root/BORIS/releases/backend/*/backend) ;;
    *) echo "INVALID_BACKEND_RELEASE_PATH=$rel" >&2; return 41 ;;
  esac
  [ -r "$rel/app/main.py" ] || { echo "BACKEND_RELEASE_MAIN_MISSING=$rel" >&2; return 42; }
  local artifact="${rel%/backend}/BORIS_ARTIFACT.json"
  [ -r "$artifact" ] || { echo "BACKEND_RELEASE_ARTIFACT_MISSING=$artifact" >&2; return 43; }
  "$PYTHON_BIN" - "$artifact" "$rel" <<'PY'
import json, os, sys
artifact, rel = sys.argv[1:]
data=json.load(open(artifact,encoding='utf-8'))
build=str(data.get('build_id') or '')
parent=os.path.basename(os.path.dirname(rel))
if data.get('format')!='BORIS_BACKEND_RELEASE_V1' or not build or build!=parent:
    raise SystemExit('BACKEND_RELEASE_MANIFEST_MISMATCH')
print(rel)
PY
}

export_release_env(){
  local rel="$1" build
  build="$(basename "$(dirname "$rel")")"
  export BORIS_RUNTIME_BACKEND_ROOT="$rel"
  export BORIS_RELEASE_BUILD_ID="$build"
  export PYTHONPATH="$rel"
  export PYTHONDONTWRITEBYTECODE=1
}

preflight(){
  local role="${1:-api}" rel code
  rel="$(resolve_release | tail -1)"
  export_release_env "$rel"
  case "$role" in
    api)
      code='import app.main; print("BACKEND_IMMUTABLE_RELEASE_PREFLIGHT=PASS role=api")'
      ;;
    worker)
      [ -r "$rel/boris_background_worker.py" ] || { echo "BACKEND_RELEASE_WORKER_MISSING=$rel" >&2; return 44; }
      code='import app.telegram_bot, app.api.messenger, app.services.jobs; print("BACKEND_IMMUTABLE_RELEASE_PREFLIGHT=PASS role=worker")'
      ;;
    telephony)
      [ -r "$rel/telephony_runtime_runner.py" ] || { echo "BACKEND_RELEASE_TELEPHONY_MISSING=$rel" >&2; return 45; }
      code='import app.services.telephony_core, app.services.calltracking_own, app.services.telphin_sip_trunk, app.services.asterisk_gateway; print("BACKEND_IMMUTABLE_RELEASE_PREFLIGHT=PASS role=telephony")'
      ;;
    crm)
      code='import app.crm.sync_engine; print("BACKEND_IMMUTABLE_RELEASE_PREFLIGHT=PASS role=crm")'
      ;;
    *) echo "UNKNOWN_BACKEND_RELEASE_PREFLIGHT_ROLE=$role" >&2; return 46 ;;
  esac
  "$PYTHON_BIN" -c "$code"
}

case "${1:-}" in
  --preflight)
    preflight "${2:-api}"
    exit 0
    ;;
  --worker)
    REL="$(resolve_release | tail -1)"
    export_release_env "$REL"
    cd "$LIVE_BACKEND"
    exec /usr/bin/xvfb-run -a "$PYTHON_BIN" "$REL/boris_background_worker.py"
    ;;
  --telephony)
    REL="$(resolve_release | tail -1)"
    export_release_env "$REL"
    cd "$LIVE_BACKEND"
    exec "$PYTHON_BIN" "$REL/telephony_runtime_runner.py"
    ;;
  --crm)
    REL="$(resolve_release | tail -1)"
    export_release_env "$REL"
    cd "$LIVE_BACKEND"
    [ -n "${DATABASE_URL:-}" ] || { echo "CRM_SYNC_FATAL DATABASE_URL_MISSING" >&2; exit 47; }
    echo "BORIS_CRM_SYNC_WORKER_START release=$(basename "$(dirname "$REL")") pid=$$" >&2
    while true; do
      if "$PYTHON_BIN" -m app.crm.sync_engine; then
        :
      else
        rc=$?
        echo "BORIS_CRM_SYNC_ITERATION_FAIL rc=$rc" >&2
      fi
      sleep 5
    done
    ;;
esac

HOST="${1:?host required}"
PORT="${2:?port required}"
REL="$(resolve_release | tail -1)"
export_release_env "$REL"
cd "$LIVE_BACKEND"
exec /usr/bin/xvfb-run -a "$PYTHON_BIN" -m uvicorn --app-dir "$REL" app.main:app \
  --host "$HOST" --port "$PORT" --timeout-graceful-shutdown 15
