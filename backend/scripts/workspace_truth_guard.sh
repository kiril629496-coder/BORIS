#!/usr/bin/env bash
set -euo pipefail
cd /root/BORIS/backend
V=/root/BORIS/backend/venv/bin/python3
$V -m unittest -q tests.test_workspace_progress_truth tests.test_programming_journal_truth tests.test_executor_recovery
$V - <<'PY'
from app.ext_api import recover
q=recover.write_queue_truth_snapshot({'source':'workspace_truth_guard'})
assert q.get('truth')=='waiting_is_not_progress',q
print('QUEUE_TRUTH=PASS',q.get('executing_now'),q.get('waiting_free_coding_provider'))
PY
# HA_ROLLING_HEALTH_RETRY_V1: both API replicas are intentionally restarted
# one at a time by the canonical deploy owner. A single instantaneous curl made
# this truth guard report a false production failure when its timer landed
# inside that healthy rolling window. Require both replicas to become healthy,
# but give the bounded HA rollout time to settle before failing closed.
wait_health() {
  local url="$1" label="$2"
  local attempt
  for attempt in $(seq 1 45); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      echo "API_HEALTH=PASS replica=$label attempt=$attempt"
      return 0
    fi
    sleep 2
  done
  echo "API_HEALTH=FAIL replica=$label attempts=45" >&2
  return 7
}
wait_health http://127.0.0.1:8000/health primary
wait_health http://127.0.0.1:8001/health replica
echo WORKSPACE_TRUTH_GUARD=PASS
