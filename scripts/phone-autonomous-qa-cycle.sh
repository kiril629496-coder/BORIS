#!/usr/bin/env bash
set -u
FAIL=0
PHONE_QA_CYCLES=${PHONE_QA_CYCLES:-4}
PHONE_QA_SLEEP_SEC=${PHONE_QA_SLEEP_SEC:-600}
ROOT=/root/BORIS
LOG="$ROOT/logs/phone-autonomous-qa-cycle.log"
LOCK="$ROOT/logs/phone-autonomous-qa-cycle.lock"
mkdir -p "$ROOT/logs"
exec >>"$LOG" 2>&1
# One QA runner owns native build/test work at a time. Concurrent Android clean/dex
# invocations can corrupt evidence even when source is healthy.
exec 7>"$LOCK"
if ! flock -n 7; then
  echo "PHONE_AUTONOMOUS_QA_SINGLETON=SKIP already_running"
  exit 0
fi
echo "PHONE_AUTONOMOUS_QA_SINGLETON=PASS"
printf '\n=== BORIS PHONE AUTONOMOUS QA %s ===\n' "$(date -u +%FT%TZ)"
for cycle in $(seq 1 "$PHONE_QA_CYCLES"); do
  echo "--- cycle=$cycle at $(date -u +%FT%TZ) ---"
  for spec in "8000 main" "8001 replica"; do set -- $spec; code=$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/health" || true); echo "health_$2=$code"; [ "$code" = 200 ] || FAIL=1; done
  public_code=$(curl -sS -o /dev/null -w '%{http_code}' https://boris-ai.pro/health || true); echo "health_public=$public_code"; [ "$public_code" = 200 ] || FAIL=1
  systemctl is-active --quiet boris-telephony-runtime.service || { echo TELEPHONY_RUNTIME_ACTIVE=FAIL; FAIL=1; }

  cd "$ROOT"
  bash clients/boris-phone/scripts/product-qa.sh || { echo "PHONE_PRODUCT_QA_CYCLE=FAIL"; FAIL=1; }
  backend/venv/bin/python clients/boris-phone/scripts/runtime-db-qa.py || { echo "PHONE_RUNTIME_DB_QA_CYCLE=FAIL"; FAIL=1; }
  cd "$ROOT/backend"
  PYTHONPATH=. venv/bin/python -m unittest \
    tests.test_telephony_db_integration \
    tests.test_telephony_api_access \
    tests.test_telephony_adapter_normalization \
    tests.test_phone_minute_alert_crash_lease \
    tests.test_dependency_call_transaction_boundary \
    tests.test_calltracking_paid_safety \
    tests.test_messenger_paid_safety \
    tests.test_legacy_paid_text_paths \
    tests.test_campaign_paid_text_guard \
    tests.test_paid_image_safety \
    tests.test_text_fallback_safety \
    tests.test_openai_audio_guard \
    tests.test_video_paid_safety \
    tests.test_ai_guard_audit \
    tests.test_no_legacy_direct_paid_api || { echo "ZERO_PAID_REGRESSION_CYCLE=FAIL"; FAIL=1; }

  cd "$ROOT/frontend"
  src_hash=$(python3 scripts/frontend-source-hash.py 2>/dev/null || true)
  active=$(readlink -f .boris-builds/current 2>/dev/null || true)
  art_hash=""
  if [ -n "$active" ] && [ -f "$active/BORIS_ARTIFACT.json" ]; then
    art_hash=$(python3 - "$active/BORIS_ARTIFACT.json" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get('snapshot_source_hash',''))
except Exception: print('')
PY
)
  fi
  echo "frontend_source_hash=$src_hash"
  echo "frontend_active=$active"
  echo "frontend_artifact_hash=$art_hash"
  if [ -n "$src_hash" ] && [ "$src_hash" = "$art_hash" ]; then echo "FRONTEND_SOURCE_ARTIFACT_MATCH=PASS"; else echo "FRONTEND_SOURCE_ARTIFACT_MATCH=REBUILD_REQUIRED"; FAIL=1; fi

  if [ "$cycle" = 1 ]; then
    cd "$ROOT"
    bash clients/boris-phone/ios/scripts/source-release-qa.sh || { echo "IOS_SOURCE_RELEASE_QA_CYCLE=FAIL"; FAIL=1; }
    (cd clients/boris-phone/android && bash scripts/source-build-qa.sh) || { echo "ANDROID_SOURCE_BUILD_QA_CYCLE=FAIL"; FAIL=1; }
  fi

  # Remove only abandoned isolated workspaces; never touch a running builder or active release.
  if ! pgrep -af 'next build|boris-frontend-build.sh' | grep -v 'pgrep -af' >/dev/null; then
    find "$ROOT/frontend/.boris-builds/work" -mindepth 1 -maxdepth 1 -type d -mmin +240 -print0 2>/dev/null | xargs -0 -r rm -rf
  fi
  df -h / | tail -1
  [ "$cycle" = "$PHONE_QA_CYCLES" ] || sleep "$PHONE_QA_SLEEP_SEC"
done
printf '=== BORIS PHONE AUTONOMOUS QA COMPLETE %s fail=%s ===\n' "$(date -u +%FT%TZ)" "$FAIL"
exit "$FAIL"
