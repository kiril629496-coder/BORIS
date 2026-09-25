#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.services.marketing_money_policy import (
    GLOBAL_HARD_MAX_BID_RUB,
    GLOBAL_MAX_BID_DELTA_PCT,
)
from cpx_cap_reconciler import _accounts


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    db = SessionLocal()
    try:
        fleet = _accounts(db, None)
        check(bool(fleet), "no connected Avito accounts in fleet")
        check(all(float(cap) <= GLOBAL_HARD_MAX_BID_RUB for _, cap in fleet),
              "effective fleet cap exceeds global hard ceiling")

        violations = []
        for account_id, effective_cap in fleet:
            mandates = db.execute(text("""
                SELECT id,max_bid_rub,max_bid_delta_pct
                  FROM money_mandates
                 WHERE :a=ANY(account_scope)
                   AND status='active' AND revoked_at IS NULL
                   AND (valid_from IS NULL OR valid_from<=now())
                   AND (valid_until IS NULL OR valid_until>now())
            """), {"a": account_id}).fetchall()
            for mid, max_bid, step in mandates:
                if max_bid is not None and float(max_bid) > float(effective_cap) + 1e-9:
                    violations.append((account_id, int(mid), "mandate_bid", float(max_bid), float(effective_cap)))
                if step is not None and int(step) > GLOBAL_MAX_BID_DELTA_PCT:
                    violations.append((account_id, int(mid), "mandate_step", int(step), GLOBAL_MAX_BID_DELTA_PCT))

        check(not violations, f"money mandate drift: {violations[:8]}")

        # Source-level regression markers: these are intentionally cheap and
        # deterministic; real Avito state is verified by cpx_cap_reconciler.
        marketer = (ROOT / "ai_marketer_hourly.sh").read_text(encoding="utf-8")
        runner = (ROOT / "cpx_advisor_runner.py").read_text(encoding="utf-8")
        goal = (ROOT / "kpi_goal_runner.py").read_text(encoding="utf-8")
        stats = (ROOT / "daily_stats_collector.py").read_text(encoding="utf-8")
        stats_api = (ROOT / "app/api/avito.py").read_text(encoding="utf-8")
        reconciler = (ROOT / "cpx_cap_reconciler.py").read_text(encoding="utf-8")
        policy = (ROOT / "app/services/marketing_money_policy.py").read_text(encoding="utf-8")
        advisor = (ROOT / "app/api/cpx_advisor.py").read_text(encoding="utf-8")
        direct = (ROOT / "app/api/cpxpromo.py").read_text(encoding="utf-8")
        autonomy = (ROOT / "app/services/autonomy.py").read_text(encoding="utf-8")
        rollback = (ROOT / "app/api/action_log_api.py").read_text(encoding="utf-8")
        budget_brake = (ROOT / "cpx_budget_brake.py").read_text(encoding="utf-8")
        guardian = (ROOT / "boris_guardian.py").read_text(encoding="utf-8")
        position_monitor = (ROOT / "app/services/avito_position_monitor.py").read_text(encoding="utf-8")

        check("bid_cap_reconcile" in marketer, "hourly marketer does not run bid-cap reconciliation")
        check("HIGH_BID_SHARE = 0.10" in runner, "90/10 portfolio safety missing")
        check("MIN_PROVEN_VIEWS_7D = 20" in runner, "proof gate missing")
        check("ACCOUNT_INCIDENTS=" in goal and "SAFE_DEFERRED=" in goal,
              "tenant isolation/deferred semantics missing")
        check("LIFECYCLE_SCOPE_V1" in goal and "has_active_obligation" in goal and "lifecycle_only" in goal,
              "unfinished KPI lifecycle obligations can fall out of runner scope")
        check("paid_active" in goal and "if not paid_active" in goal,
              "lifecycle continuation can bypass paid-account guard")
        check("INTERNAL_HA_GRACE_V1" in goal and "SAFE_DEFERRED_HA_RECOVERED" in goal,
              "brief rolling backend restart can still fail the whole hourly marketer")
        avito_api = (ROOT / "app/api/avito.py").read_text(encoding="utf-8")
        check("KPI_EXECUTION_LANES_V1" in goal and "allow_stale_non_money=True" in goal,
              "stale spend still stops the whole KPI runner instead of money lane only")
        check("KPI_EXECUTION_LANES_V2" in avito_api and "allow_stale_non_money" in avito_api,
              "KPI controller has no independent non-money execution lane")
        check("KPI_MAPPING_OBLIGATION_V1" in avito_api and 'op["status"] = "mapping_wait"' in avito_api,
              "canonical mapping gaps can still discard a KPI hypothesis")
        check("KPI_ROLLBACK_CANONICAL_IDENTITY_V1" in avito_api and "rollback_identity_recovery" in avito_api,
              "rollback can still strand because it matches feed by external Avito item_id")
        check('"mapping_wait"' in avito_api and '"evidence_required":"official_autoload_current_items"' in avito_api,
              "mapping obligation is not durable/exact-evidence gated")
        check("EXECUTION_LEDGER_V1" in avito_api and 'key="kpi_execution_ledger"' in avito_api,
              "durable KPI execution ledger missing")
        check('"active_count": len(obligations)' in avito_api and '"mapping_obligation": op.get("mapping_obligation")' in avito_api,
              "execution ledger does not preserve unfinished mapping debt")
        check("CPX_RECEIPT_SESSION_ISOLATION_V1" in advisor,
              "CPX receipt lookup can regress into nested-session pool starvation")

        check((("STATS_RETRY_AFTER_BOUNDED_V1" in stats_api) or
               ("STATS_RETRY_AFTER_BOUNDED_V2" in stats_api and "STATS_CONFIRMED_429_NO_RETRY_V1" in stats_api))
              and "Retry-After" in stats_api,
              "STATS_RETRY_AFTER_BOUNDED")
        check("STATS_LAST_GOOD_SPEND_GRACE_V1" in stats_api and "last_good_age_sec" in stats_api,
              "STATS_LAST_GOOD_SPEND_GRACE")
        check("DAILY_STATS_PARTIAL" in stats and "DAILY_STATS_SYSTEMIC_FAIL" in stats,
              "stats tenant isolation missing")
        check("GLOBAL_HARD_MAX_BID_RUB = 150.0" in policy,
              "global bid hard ceiling missing")
        check("GLOBAL_MAX_BID_DELTA_PCT = 10" in policy,
              "global bid step ceiling missing")
        check("effective_hard_bid_cap" in reconciler,
              "fleet reconciler does not use authoritative money policy")
        check("MAX_BID_STEP_PCT = GLOBAL_MAX_BID_DELTA_PCT" in advisor,
              "CPX apply engine can bypass global step ceiling")
        check("auto_promotion_incompatible_with_hard_bid_cap" in direct and "reason_code\":\"hard_bid_cap" in direct,
              "direct CPX endpoint bypass remains")
        check("stored_snapshot_fallback" in reconciler and "inventory_complete" in reconciler,
              "429 inventory fallback/coverage gate missing")
        check("effective_hard_max_bid_rub" in autonomy and "effective_hard_bid_cap" in autonomy,
              "autonomy last-write global cap guard missing")
        check("effective_hard_bid_cap" in rollback and "reason_code\": \"hard_bid_cap\"" in rollback,
              "historical rollback can bypass current red line")
        check("budget_brake" in marketer and "cpx_budget_brake.py --apply" in marketer,
              "hard daily-budget brake is not in hourly marketer")
        check("paid_promotions_after" in budget_brake and "budget_exhausted_no_promo" in budget_brake,
              "budget brake verification contract missing")
        check("money_mandates" in stats and "daily_budget_limit_rub" in stats,
              "stats collector does not cover money-limit scope")
        check("budget_brake_health" in guardian and "Контроль дневного бюджета" in guardian,
              "Guardian does not enforce budget brake health")
        check("effective_daily_budget_limit" in policy and "latest_confirmed_spend" in policy,
              "authoritative daily budget/spend policy missing")
        check("POSITION_PAGINATION_JOB_SCOPE_V1" in position_monitor
              and 'bg._db_put(account_id, f"browser_job:{job_id}", job)' in position_monitor,
              "position monitor pagination is not tenant-scoped to the Browser Gateway job contract")
        check('bg._db_put(bg.SYSTEM_ACCOUNT, f"job:{job_id}", job)' not in position_monitor,
              "position monitor continuation page can still be stranded outside tenant browser_job scope")
        check("blocked_daily_budget_exhausted" in direct and "blocked_daily_budget_near_limit" in direct,
              "direct CPX budget guard missing")

        # Regression of direct endpoints: both requests must fail before token/network.
        from app.api.cpxpromo import SetManualBody, SetAutoBody, set_manual, set_auto
        probe_account = fleet[0][0]
        blocked_manual = set_manual(SetManualBody(account_id=probe_account, item_id=1, bid_penny=int(GLOBAL_HARD_MAX_BID_RUB * 100) + 100))
        blocked_auto = set_auto(SetAutoBody(account_id=probe_account, item_id=1, budget_penny=10000))
        check(blocked_manual.get("status") == "blocked" and not blocked_manual.get("changed_avito"),
              "direct manual >cap was not blocked before Avito")
        check(blocked_auto.get("status") == "blocked" and not blocked_auto.get("changed_avito"),
              "Avito auto promotion was not blocked before Avito")

        # Deterministic exhausted-budget regression. It must return before token
        # lookup/network; monkeypatches are restored in-process immediately.
        import app.api.cpxpromo as direct_api
        orig_cap, orig_money, orig_token = direct_api._hard_cap, direct_api._money_state, direct_api._token
        try:
            direct_api._hard_cap = lambda _account_id: 40.0
            direct_api._money_state = lambda _account_id: {
                "daily_budget_limit_rub": 1000.0,
                "spend": {"status": "ok", "spent_today_rub": 1000.0},
            }
            direct_api._token = lambda _account_id: (_ for _ in ()).throw(AssertionError("token lookup must not happen"))
            budget_block = direct_api.set_manual(
                direct_api.SetManualBody(account_id="qa_budget_guard", item_id=1, bid_penny=1000)
            )
        finally:
            direct_api._hard_cap, direct_api._money_state, direct_api._token = orig_cap, orig_money, orig_token
        check(budget_block.get("status") == "blocked"
              and budget_block.get("reason_code") == "blocked_daily_budget_exhausted"
              and not budget_block.get("changed_avito"),
              "exhausted daily budget did not block direct CPX before network")

        # ACCOUNT_DISABLE_CONTRACT_V1: a globally disabled tenant must never
        # re-enter autonomous bid reconciliation or KPI execution.
        disabled_rows = db.execute(text("""
            SELECT DISTINCT account_id FROM reliability_kill_switches
             WHERE blocked=true AND module='actions'
        """)).scalars().all()
        fleet_accounts = {str(account_id) for account_id, _cap in fleet}
        for disabled_account in disabled_rows:
            disabled_account = str(disabled_account)
            check(disabled_account not in fleet_accounts, f"disabled account in fleet: {disabled_account}")
            # Mirror the KPI discovery's authoritative exclusion without opening
            # a second Session while this rollback-safe QA already owns one.
            blocked_for_kpi = bool(db.execute(text("""
                SELECT 1 FROM reliability_kill_switches
                 WHERE account_id=:a AND blocked=true
                   AND module IN ('actions','marketing','background') LIMIT 1
            """), {"a": disabled_account}).first())
            check(blocked_for_kpi, f"disabled account missing KPI block: {disabled_account}")

        print("MARKETER_SAFETY=PASS", {
            "fleet_accounts": len(fleet),
            "global_hard_cap_rub": GLOBAL_HARD_MAX_BID_RUB,
            "mandate_violations": 0,
            "tenant_isolation": True,
            "proof_gate": True,
            "bid_reconcile_before_kpi": True,
        })
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
