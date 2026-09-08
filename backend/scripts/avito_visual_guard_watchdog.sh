#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
LOGDIR=/var/log/boris
mkdir -p "$LOGDIR"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT
{
  echo "[$STAMP] AVITO_VISUAL_GUARD_WATCHDOG START"
  # systemd gives this oneshot 180s total. Never wait longer than the service
  # lifetime for a rollout lock: otherwise systemd kills the watchdog before it
  # can diagnose the lock or run any visual checks. A busy rollout is not a
  # visual failure; skip this tick cleanly and let the 15-minute timer retry.
  if ! flock -w 120 /run/lock/boris-backend-rolling.lock -c true; then
    echo "[$STAMP] STEP deploy_quiescence BUSY rolling lock >120s; clean skip, timer will retry"
    echo "[$STAMP] AVITO_VISUAL_GUARD_WATCHDOG SKIP_DEPLOY_BUSY"
    exit 0
  fi
  echo "[$STAMP] STEP deploy_quiescence PASS"

  run_step() {
    local name="$1"; shift
    echo "[$STAMP] STEP $name START"
    if ! "$@"; then
      local rc=$?
      echo "[$STAMP] STEP $name FAIL rc=$rc"
      exit 97
    fi
    echo "[$STAMP] STEP $name PASS"
  }

  run_step quarantine env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/quarantine_legacy_visual_files.py"
  run_step visual_regression env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_visual_contract_regression.py"
  run_step idempotency_regression env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/banner_internal_idempotency_regression.py"
  run_step paid_safety_tests env PYTHONPATH="$ROOT" BORIS_TEST_MODE=1 "$ROOT/venv/bin/python" -m unittest tests.test_billing_concurrency_guard tests.test_plan_item_paid_safety tests.test_paid_image_artifact_guard tests.test_billing_usage_idempotency tests.test_avito_batch_billing_exactly_once tests.test_campaign_banner_tariff_order tests.test_campaign_listing_tariff_order tests.test_draft_storage_atomic tests.test_feed_storage_atomic tests.test_auto_duplicate_exactly_once tests.test_pipeline_draft_paid_safety tests.test_parsed_draft_paid_safety
  run_step hero_feed_selfheal_contract env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" -m unittest tests.test_avito_campaign_hero_feed_selfheal
  run_step uniquify_content_identity env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" -m unittest tests.test_avito_uniquify_content_identity
  run_step watchdog_timeout_contract env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" -m unittest tests.test_avito_visual_guard_timeout_contract
  run_step missing_published_identity_contract env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" -m unittest tests.test_avito_media_audit_missing_published_identity
  run_step feed_lifecycle_contract env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" -m unittest tests.test_avito_feed_lifecycle_guard
  run_step feed_lifecycle_guard env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_feed_lifecycle_guard.py"
  run_step hero_feed_selfheal env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_campaign_hero_feed_selfheal.py"
  run_step media_audit env PYTHONPATH="$ROOT" "$ROOT/venv/bin/python" "$ROOT/scripts/avito_media_quality_audit.py"
  run_step storage_singleton_guard "$ROOT/scripts/storage_singleton_guard.sh"
  run_step notification_atomic_tests env PYTHONPATH="$ROOT" BORIS_TEST_MODE=1 "$ROOT/venv/bin/python" -m unittest tests.test_notification_store_atomic
  run_step local_health curl -fsS --max-time 15 -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "http://127.0.0.1:8000/health?visual_guard=$(date +%s%N)"
  run_step public_health curl -fsS --max-time 15 -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "https://boris-ai.pro/health?visual_guard=$(date +%s%N)"
  LEGACY_RESP=$(curl -fsS --max-time 15 -X POST http://127.0.0.1:8000/api/banners/full_ai -H 'Content-Type: application/json' --data '{"account_id":"qa_visual_contract","raw_description":"watchdog-no-paid","use_template_library":false}') || { echo "[$STAMP] STEP legacy_guard FAIL curl"; exit 98; }
  if ! grep -q '"code":"legacy_text_in_image_forbidden"' <<<"$LEGACY_RESP"; then
    echo "[$STAMP] STEP legacy_guard FAIL unexpected_response=$LEGACY_RESP"
    exit 99
  fi
  echo "[$STAMP] STEP legacy_guard PASS"
  echo "[$STAMP] AVITO_VISUAL_GUARD_WATCHDOG PASS"
} >"$OUT" 2>&1 || {
  rc=$?
  cat "$OUT" >>"$LOGDIR/avito_visual_guard_watchdog.log"
  logger -t BORIS_AVITO_VISUAL_GUARD "FAIL rc=$rc; see $LOGDIR/avito_visual_guard_watchdog.log"
  exit "$rc"
}
cat "$OUT" >>"$LOGDIR/avito_visual_guard_watchdog.log"
