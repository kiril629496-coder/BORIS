#!/usr/bin/env bash
set -u
cd /root/BORIS/backend
mkdir -p /root/BORIS/backend/logs
exec >>/root/BORIS/backend/logs/ai_marketer_hourly.log 2>&1
LOCK=/tmp/boris_ai_marketer_hourly_root.lock
exec 9>"$LOCK"
flock -n 9 || { echo "AI_MARKETER_SKIP overlap $(date -u +%Y-%m-%dT%H:%M:%SZ)"; exit 0; }

# AI_MARKETER_INTERRUPT_TRUTH_V1: interrupted canonical cycles remain durable.
# The next timer run records recovery and reruns the complete guarded pipeline.
RUN_MARKER="/root/BORIS/backend/run/ai_marketer_cycle.running"
mkdir -p /root/BORIS/backend/run
if [ -s "$RUN_MARKER" ]; then
  echo "AI_MARKETER_RECOVER_INTERRUPTED previous=$(cat "$RUN_MARKER" 2>/dev/null || true) at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
printf 'pid=%s started_at=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN_MARKER"
cleanup_cycle_marker(){ rm -f "$RUN_MARKER"; }
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "AI_MARKETER_INTERRUPTED rc=$rc at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; else cleanup_cycle_marker; fi; exit "$rc"' EXIT
trap 'echo "AI_MARKETER_SIGNAL signal=TERM at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; exit 143' TERM
trap 'echo "AI_MARKETER_SIGNAL signal=INT at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; exit 130' INT

echo "===== AI_MARKETER_START $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
# MONEY_WORKSTREAM_SCOPE_PREFLIGHT_V1: no money stage may run while canonical
# single-writer ownership is invalid. The guard may self-heal active-registry
# drift from immutable root-of-trust; a real canonical conflict remains fatal.
if ! ./venv/bin/python run/workstream_scope_guard.py; then
  echo "AI_MARKETER_SCOPE_BLOCK reason=workstream_single_writer_invalid"
  exit 1
fi
FAIL=0
run_stage() {
  local name="$1"; shift
  echo "--- STAGE $name ---"
  if "$@"; then
    return 0
  else
    local rc=$?
    echo "STAGE_FAIL $name rc=$rc"
    FAIL=1
    return "$rc"
  fi
}
run_observer_stage() {
  local name="$1"; shift
  echo "--- STAGE $name ---"
  if "$@"; then
    return 0
  else
    local rc=$?
    echo "STAGE_WARN $name rc=$rc"
    return "$rc"
  fi
}
# LATE_DAY_SPEND_RESET_WIRE_V1: reconcile any previous Moscow-day temporary boost first.
run_stage late_day_spend_reset ./venv/bin/python late_day_spend_controller.py --reset-only || true
# LAUNCH_MANDATE_HOURLY_RECONCILE_WIRE_V1: local-only stale money-authority cleanup
# runs before the first Avito provider read. It revalidates only already-existing
# new_feed_launch grants and never creates a new grant by itself.
run_stage launch_mandate_reconcile ./venv/bin/python -c 'import json; from app.api.cpx_advisor import reconcile_existing_new_feed_launch_mandates; r=reconcile_existing_new_feed_launch_mandates(); print(json.dumps(r,ensure_ascii=False)); raise SystemExit(1 if int(r.get("errors") or 0) else 0)' || true
# PAID_TARIFF_MANDATE_HOURLY_RECONCILE_WIRE_V1: DB-only standing KPI
# authority reconcile runs before provider reads. This prevents a new/re-enabled
# paid client from being permanently blocked by preflight solely because the
# paid_tariff_kpi row has not yet been created by a downstream action.
run_stage paid_tariff_mandate_reconcile ./venv/bin/python -c 'import json; from app.api.cpx_advisor import reconcile_paid_tariff_raise_mandates; r=reconcile_paid_tariff_raise_mandates(); print(json.dumps(r,ensure_ascii=False)); raise SystemExit(1 if int(r.get("errors") or 0) else 0)' || true
# STATS_BEFORE_CPX_CAP_V1: compatibility marker for the production contract.
# STATS_ABSOLUTE_FIRST_PROVIDER_READ_V1: fresh stats must be the first Avito
# provider read in the canonical money cycle. portfolio_bootstrap can populate
# the shared Retry-After ledger and previously made every following stats read
# look throttled, turning a healthy provider into a systemic money-data failure.
# Scope preflight/reset above are local-only and safe before this point.
# DAILY_STATS_PROVIDER_DEFERRED_EXIT_V1: rc=75 means every account was
# explicitly throttled by Avito. That is an external dependency wait: skip all
# money-increasing stages, keep the systemd cycle healthy, and retry next timer.
# A genuine collector/storage/transport failure is still an internal failure.
# MARKETER_DEDICATED_STATS_SINGLE_OWNER_V1:
# boris-daily-stats-collector.service is the ONLY hourly provider stats reader.
# It is independently timer-owned at :05. This money pipeline waits for that
# current-hour collection to finish, then reuses the persisted snapshot. Running
# daily_stats_collector.py here too caused two simultaneous provider sweeps,
# tenant 429s and a false spend_provider_degraded block immediately after a
# successful fresh snapshot.
wait_for_dedicated_stats_owner() {
  local unit="boris-daily-stats-collector.service"
  # DEDICATED_STATS_WAIT_WITHIN_FRESHNESS_WINDOW_V1:
  # Fleet stats may legitimately take several minutes on large accounts.
  # Wait up to 10 minutes: long enough for observed 4+ minute provider sweeps,
  # but still inside the canonical 15-minute money-freshness envelope.
  local max_wait_sec=600
  local poll_sec=2
  local waited=0
  local hour_floor start_raw start_epoch active
  hour_floor="$(date -u -d "$(date -u +%Y-%m-%dT%H):05:00Z" +%s 2>/dev/null || echo 0)"
  while [ "$waited" -le "$max_wait_sec" ]; do
    active="$(systemctl show "$unit" -p ActiveState --value 2>/dev/null || echo unknown)"
    start_raw="$(systemctl show "$unit" -p ExecMainStartTimestamp --value 2>/dev/null || true)"
    start_epoch="$(date -u -d "$start_raw" +%s 2>/dev/null || echo 0)"
    if [ "$start_epoch" -ge "$hour_floor" ] && [ "$active" != "active" ] && [ "$active" != "activating" ]; then
      echo "DEDICATED_STATS_READY unit=$unit start=$start_raw state=$active waited_sec=$waited"
      return 0
    fi
    sleep "$poll_sec"
    waited=$((waited + poll_sec))
  done
  echo "DEDICATED_STATS_WAIT_TIMEOUT unit=$unit waited_sec=$waited"
  return 1
}

MONEY_STATS_READY=0
if wait_for_dedicated_stats_owner; then
  MONEY_STATS_READY=1
  # Portfolio bootstrap may inspect provider inventory only AFTER current spend
  # and KPI facts are durably collected. It cannot poison the money input stage.
  run_stage portfolio_bootstrap ./venv/bin/python portfolio_bootstrap_runner.py || true
  # BID_CAP_RECONCILE_V1: enforce hard cap before any growth action.
  run_stage bid_cap_reconcile ./venv/bin/python cpx_cap_reconciler.py --apply || true
  # HARD_DAILY_BUDGET_BRAKE_V1: after fresh spend collection and before any
  # growth/KPI actions, remove ongoing paid CPX if the confirmed daily red line
  # is already exhausted. This stage never raises bids or changes content.
  run_stage budget_brake ./venv/bin/python cpx_budget_brake.py --apply || true
  # BUDGET_BRAKE_NEXT_DAY_RESUME_WIRE_V1: only a prior-day durable brake
  # snapshot may restore promotion, max 5 items/hour, through the same money
  # guards. Same-day resume is impossible by contract.
  run_stage budget_resume ./venv/bin/python cpx_budget_resume.py --apply || true
  run_stage cpx_portfolio ./venv/bin/python cpx_advisor_runner.py || true # EMERGENCY_MONEY_FREEZE: recommendations only
  # DAILY_ACTION_LEARNING_V1: close completed experiments before next hypothesis; internal ledger only.
  run_stage action_effect_learning ./venv/bin/python effect_check.py --apply || true
  # NEGATIVE_RAISE_ROLLBACK_SELF_HEAL_V2: loser compensation executes from
  # canonical avito_money_core code; production no longer depends on the legacy
  # unowned top-level runner.
  run_stage negative_raise_rollback ./venv/bin/python -c 'import json; from app.api.cpx_advisor import run_negative_raise_rollback_cycle; r=run_negative_raise_rollback_cycle(); print(json.dumps(r,ensure_ascii=False)); raise SystemExit(1 if int(r.get("errors") or 0) else 0)' || true
  # DAILY_EXPERIMENT_JOURNAL_V1: persist bid/title hypotheses and measured outcomes once per canonical cycle.
  run_stage experiment_journal ./venv/bin/python -m app.services.marketing_experiment_journal || true
  # UNIFIED_STAGED_ALL_MARKETER_ACCOUNTS_V1: no tenant-specific paid lane.
  # All paid autonomous accounts are rotated through the same guarded staged rollout.
  # STAGED_MARKETER_ROLLOUT_V1: repaired money contour expands tenant-by-tenant,
  # max 3 accounts/cycle and 1 live action/account during canary.
  run_stage marketer_rollout ./venv/bin/python marketer_rollout_runner.py || true
else
  # Per-account money guards remain fail-closed. Do not create a second provider
  # reader as a fallback: that would recreate the duplicate-owner throttle loop.
  echo "MONEY_STAGES_SKIPPED reason=dedicated_stats_owner_not_completed"
fi
# KPI runner owns per-account freshness checks. Always run it so a provider
# outage in the money lane cannot stop diagnostics/content work for every tenant.
# Accounts without confirmed same-day spend remain fail-closed for money writes.
run_stage kpi_non_money ./venv/bin/python kpi_goal_runner.py --non-money-apply || true # OWNERLESS: executes only free/content/lifecycle work; money hard-disabled
# LATE_DAY_SPEND_CATCHUP_WIRE_V1: evening under-spend acceleration after content planning.
run_stage late_day_spend_catchup ./venv/bin/python late_day_spend_controller.py --boost-only || true
# MONEY_RUNTIME_LOCAL_RECONCILE_ON_STATS_WAIT_WIRE_V1: if the dedicated stats
# owner did not finish, the money stages correctly fail closed, but the previous
# cycle's owner-facing runtime must not survive as if it were current. Reproject
# the money truth from DB only AFTER non-money work, so the final visible state
# says both that BORIS keeps working for free and why money is paused.
if [ "$MONEY_STATS_READY" -eq 0 ]; then
  run_stage money_runtime_truth_reconcile ./venv/bin/python marketer_rollout_runner.py --runtime-reconcile-only || true
fi
# The global production contract is observed here for correlation, but it is
# enforced independently by boris-guardian. A parallel frontend rollout must
# not make the AI-marketer systemd unit red after all marketing/money stages
# completed safely. Contract failures remain visible as STAGE_WARN and stay red
# in the authoritative global rollout state until Guardian/source deployment
# closes them.
run_observer_stage reach_safety_qa ./venv/bin/python qa_marketer_reach_safety.py || true
run_observer_stage rollout_safety_qa ./venv/bin/python qa_marketer_rollout.py || true
run_observer_stage production_contract ./venv/bin/python production_contract_runner.py || true
echo "===== AI_MARKETER_DONE $(date -u +%Y-%m-%dT%H:%M:%SZ) fail=$FAIL ====="
exit "$FAIL"
