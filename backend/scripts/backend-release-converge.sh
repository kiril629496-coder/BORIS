#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/BORIS/backend
cd "$ROOT"
LOG="$ROOT/logs/backend_release_converge_$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$ROOT/logs"
exec > >(tee -a "$LOG") 2>&1

echo "BACKEND_RELEASE_CONVERGE_START $(date -u +%FT%TZ)"
for attempt in $(seq 1 30); do
  echo "=== attempt=$attempt $(date -u +%FT%TZ) ==="
  set +e
  sudo ./rolling_restart_backend.sh
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    echo "ROLLING_DEPLOY=PASS attempt=$attempt"
    curl -fsS http://127.0.0.1:8000/health
    curl -fsS http://127.0.0.1:8001/health
    curl -fsS https://boris-ai.pro/health
    PYTHONPATH=. BORIS_TEST_MODE=1 venv/bin/python -m unittest \
      tests.test_billing_concurrency_guard \
      tests.test_plan_item_paid_safety \
      tests.test_text_fallback_safety \
      tests.test_campaign_paid_text_guard \
      tests.test_paid_image_safety \
      tests.test_no_legacy_direct_paid_api
    PYTHONPATH=. venv/bin/python scripts/avito_visual_contract_regression.py
    PYTHONPATH=. venv/bin/python scripts/banner_internal_idempotency_regression.py
    PYTHONPATH=. venv/bin/python scripts/avito_media_quality_audit.py
    PYTHONPATH=. venv/bin/python scripts/qa_usage_pricing.py
    PYTHONPATH=. venv/bin/python scripts/qa_proxy_health.py
    PYTHONPATH=. venv/bin/python scripts/campaign_launcher_contract_regression.py
    PYTHONPATH=. venv/bin/python scripts/campaign_launcher_real_context_regression.py
    PYTHONPATH=. venv/bin/python scripts/qa_cpx_receipt_safety_triggers.py
    PYTHONPATH=. venv/bin/python scripts/qa_cpx_run_scope_v2.py
    PYTHONPATH=. venv/bin/python qa_marketer_rollout.py
    echo "BACKEND_RELEASE_CONVERGED=PASS $(date -u +%FT%TZ)"
    echo "LOG=$LOG"
    exit 0
  fi
  echo "ROLLING_DEPLOY_RETRY rc=$rc"
  if [ "$rc" -ne 19 ] && [ "$rc" -ne 33 ]; then
    echo "NON_TRANSIENT_ROLLING_RC=$rc; one guarded retry window remains"
  fi
  sleep 20
done

echo "BACKEND_RELEASE_CONVERGED=FAIL $(date -u +%FT%TZ)"
echo "LOG=$LOG"
exit 1
