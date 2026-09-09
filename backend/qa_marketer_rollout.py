#!/usr/bin/env python3
from __future__ import annotations
import json, re
from pathlib import Path
from sqlalchemy import text
from app.db.session import SessionLocal

ROOT=Path('/root/BORIS/backend')
checks=[]
def ck(name, ok, detail=''):
    checks.append((name,bool(ok),detail)); print(('PASS' if ok else 'FAIL'),name,detail)

db=SessionLocal()
try:
    bad=db.execute(text("""
      select distinct m.account_scope[1] from money_mandates m join storage s
      on s.account_id=m.account_scope[1] and s.key='kpi_settings'
      where m.status='active' and m.revoked_at is null and m.source='paid_tariff_kpi'
      and 'cpx.raise_bid'=any(m.allowed_operations)
      and coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0
    """)).fetchall()
    ck('ZERO_BUDGET_NO_KPI_RAISE_MANDATE',not bad,[x[0] for x in bad])

    bad_launch=db.execute(text("""
      select m.account_scope[1],m.max_actions_run,m.max_actions_day,m.max_bid_delta_pct,m.daily_budget_rub,
             coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0) cfg
      from money_mandates m
      join storage s on s.id=(select max(s2.id) from storage s2 where s2.account_id=m.account_scope[1] and s2.key='kpi_settings')
      where m.status='active' and m.revoked_at is null and m.source='new_feed_launch'
      and (m.max_actions_run<>1 or m.max_actions_day>24 or m.max_bid_delta_pct>10
           or m.daily_budget_rub is null or m.daily_budget_rub<=0 or m.daily_budget_rub>coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)
           or coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0
           or m.max_bid_rub is null or m.max_bid_rub<=0 or m.max_bid_rub>150)
    """)).fetchall()
    ck('FIRST_BID_EXPLICIT_BUDGET_BOUNDED',not bad_launch,[tuple(x) for x in bad_launch])

    bad_launch_authority=db.execute(text("""
      select m.account_scope[1]
      from money_mandates m
      left join accounts a on a.account_id=m.account_scope[1]
      left join storage s on s.id=(select max(s2.id) from storage s2 where s2.account_id=m.account_scope[1] and s2.key='kpi_settings')
      left join storage ap on ap.id=(select max(ap2.id) from storage ap2 where ap2.account_id=m.account_scope[1] and ap2.key='autopilot_settings')
      where m.status='active' and m.revoked_at is null and m.source='new_feed_launch'
        and 'cpx.raise_bid'=any(m.allowed_operations)
        and (
          a.id is null or coalesce(a.avito_client_id,'')='' or coalesce(a.avito_client_secret,'')=''
          or coalesce((s.value::jsonb->>'bid_autopilot')::boolean,false) is not true
          or coalesce(ap.value::jsonb->>'mode','') not in ('goal_auto','always_auto')
          or coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0
          or coalesce(s.value::jsonb->'daily_budget_authorization'->>'policy_version','')<>'MONEY_BUDGET_OWNER_PROVENANCE_V1'
          or coalesce(nullif(s.value::jsonb->'daily_budget_authorization'->>'authorized_by_user_id',''),'0')::int<=0
          or coalesce(s.value::jsonb->'daily_budget_authorization'->>'source','')<>'authenticated_set_kpi_settings'
          or coalesce(nullif(s.value::jsonb->'daily_budget_authorization'->>'daily_budget_limit_rub',''),'0')::numeric
             <>coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)
        )
    """)).fetchall()
    ck('FIRST_BID_ACTIVE_AUTHORITY_CURRENT',not bad_launch_authority,[x[0] for x in bad_launch_authority])

    bad_paid=db.execute(text("""
      select m.account_scope[1],m.daily_budget_rub from money_mandates m join storage s
      on s.account_id=m.account_scope[1] and s.key='kpi_settings'
      where m.status='active' and m.revoked_at is null and m.source='paid_tariff_kpi'
      and 'cpx.raise_bid'=any(m.allowed_operations)
      and (m.daily_budget_rub is null or m.daily_budget_rub<=0 or coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0)
    """)).fetchall()
    ck('PAID_KPI_RAISE_REQUIRES_BUDGET',not bad_paid,[tuple(x) for x in bad_paid])

    bad_paid_mode=db.execute(text("""
      select m.account_scope[1]
      from money_mandates m
      left join storage s on s.id=(select max(s2.id) from storage s2 where s2.account_id=m.account_scope[1] and s2.key='kpi_settings')
      left join storage ap on ap.id=(select max(ap2.id) from storage ap2 where ap2.account_id=m.account_scope[1] and ap2.key='autopilot_settings')
      where m.status='active' and m.revoked_at is null and m.source='paid_tariff_kpi'
        and 'cpx.raise_bid'=any(m.allowed_operations)
        and (coalesce((s.value::jsonb->>'bid_autopilot')::boolean,false) is not true
             or coalesce(ap.value::jsonb->>'mode','') not in ('goal_auto','always_auto'))
    """)).fetchall()
    ck('PAID_KPI_ACTIVE_MODE_AUTHORITY_CURRENT',not bad_paid_mode,[x[0] for x in bad_paid_mode])

    cohorts=db.execute(text("""
      select account_id,count(distinct object_name) n from action_log
      where source='reach_rescue' and ts>=now()-interval '24 hours' and action like '%ставк%'
      group by account_id having count(distinct object_name)>5
    """)).fetchall()
    ck('REACH_RESCUE_COHORT_LE_5',not cohorts,[tuple(x) for x in cohorts])

    ev=db.execute(text("""
      select count(distinct object_name) from action_log where account_id='evgeniy_peregorodki_12447'
      and source='reach_rescue' and ts>=now()-interval '24 hours' and action like '%ставк%'
    """)).scalar() or 0
    ck('EVGENIY_RESCUE_BOUNDED',ev<=5,ev)

    # REALIZED_AUTONOMOUS_STEP_QA_V1: verify recent provider-confirmed autonomous
    # raises, not just mandate configuration. This catches rounding regressions
    # where a nominal 10% step becomes >10% after whole-ruble serialization.
    realized_bad=[]
    realized=db.execute(text("""
      select account_id,object_name,before_val,after_val,source,ts from action_log
      where actor='boris_auto' and ts>=now()-interval '70 minutes'
        and action like '%Поднял ставку%'
      order by ts desc
    """)).fetchall()
    for aid,obj,before_raw,after_raw,source,ts in realized:
        try:
            bm=re.search(r'[-+]?\d+(?:[.,]\d+)?',str(before_raw or ''))
            am=re.search(r'[-+]?\d+(?:[.,]\d+)?',str(after_raw or ''))
            if not bm or not am:
                realized_bad.append((aid,obj,'unparseable',source,str(ts))); continue
            before=float(bm.group(0).replace(',','.')); after=float(am.group(0).replace(',','.'))
            if before <= 0:
                # REALIZED_FIRST_ACTIVATION_RECEIPT_QA_V1: zero->positive is a
                # baseline CPX activation, not a percentage raise. It is valid
                # only when the exactly-once receipt proves the same provider
                # write for an explicit bounded first-activation source.
                first_receipt=db.execute(text("""
                  select id from cpx_execution_receipts
                  where account_id=:a and item_id=:i and source=:s
                    and action='raise' and old_bid_penny=0
                    and intended_bid_penny=:p and observed_bid_penny=:p
                    and status in ('succeeded','reconciled')
                    and created_at between :ts - interval '2 minutes'
                                       and :ts + interval '2 minutes'
                  order by id desc limit 1
                """), {
                    "a":str(aid), "i":int(obj), "s":str(source),
                    "p":int(round(after*100)), "ts":ts,
                }).fetchone() if str(source) in {"feed20","account_low_views_ramp"} else None
                if after>150.000001 or not first_receipt:
                    realized_bad.append((aid,obj,before,after,'zero_without_first_activation_receipt',source,str(ts)))
                continue
            pct=((after-before)/before*100.0)
            if after>150.000001 or pct>10.0001:
                realized_bad.append((aid,obj,before,after,round(pct,3),source,str(ts)))
        except Exception:
            realized_bad.append((aid,obj,'parse_error',source,str(ts)))
    ck('REALIZED_AUTONOMOUS_STEP_LE_10',not realized_bad,realized_bad[:20])
finally:
    db.close()

runner=(ROOT/'cpx_advisor_runner.py').read_text()
intr=(ROOT/'app/services/intraday.py').read_text()
advisor=(ROOT/'app/api/cpx_advisor.py').read_text()
auto=(ROOT/'app/services/autonomy.py').read_text()
roll=(ROOT/'marketer_rollout_runner.py').read_text()
watch=(ROOT/'new_ad_bid_watch_runner.py').read_text()
worker=(ROOT/'boris_background_worker.py').read_text()
ck('PER_ITEM_REQUEST_ID', 'request_id": f"{_rid(acc)}:{int(it[\'id\'])}"' in runner)
ck('MAX_ACTIONS_OVERRIDE', '--max-actions' in runner and 'MAX_ACTIONS_OVERRIDE' in runner)
ck('RUN_PREFIX_LIMITS', '_request_run_prefix' in auto and "prefix + ':%'" in auto)
ck('RUN_CAP_ACCOUNT_SCOPED', 'count_actions_run(db, account_id, rid)' in auto and 'WHERE account_id=:acc AND' in auto)
ck('GLOBAL_AUTO_ACTION_CAPS', 'GLOBAL_MAX_AUTO_ACTIONS_RUN = 5' in (ROOT/'app/services/marketing_money_policy.py').read_text() and 'GLOBAL_MAX_AUTO_ACTIONS_DAY = 120' in (ROOT/'app/services/marketing_money_policy.py').read_text() and 'effective_max_actions_run' in auto)
ck('MANDATE_SOURCE_ISOLATION', 'MONEY_MANDATE_SOURCE_ISOLATION_V1' in auto and 'source=None' in auto)
ck('REACH_DURABLE_COHORT_V2','REACH_RESCUE_COHORT_DURABLE_V2' in intr)
ck('REACH_INTRADAY_STOP','REACH_RESCUE_MAX_VIEWS_TODAY_NO_CONTACT = 10' in intr)
ck('NEGATIVE_FEEDBACK_PLANNER_BLOCK','_negative_raise_measurement' in intr and 'negative_feedback' in intr)
ck('CANONICAL_MARKETING_ENTITLEMENT_MANDATE_GUARD','MARKETER_CANONICAL_ENTITLEMENT_GUARD_V1' in advisor and 'MARKETER_UNLIMITED_NOT_ENTITLEMENT_V1' in advisor and 'marketing_service_entitlement' in advisor)
ck('WINNER_MEMORY_PRIORITY','WINNER_MEMORY_RANK_V1' in intr and 'PROFITABLE_DAY_WINNER_MEMORY_PRIORITY_V1' in advisor)
ck("PROFITABLE_DAY_CURRENT_PROMOTION_TRUTH", "PROFITABLE_DAY_CURRENT_PROMOTION_TRUTH_V2" in advisor and "active_paid_ids" in advisor)
ck('MEASURE_NO_SIGNAL_TIMEOUT','MEASURE_NO_SIGNAL_TIMEOUT_V1' in advisor and 'inconclusive_no_signal' in advisor and 'no_signal_feedback' in intr)
ck('CAPABILITY_COOLDOWN','CPX_ITEM_CAPABILITY_COOLDOWN_V1' in advisor and 'cpx_item_capability_cooldown' in intr)
ck('LOWVIEWS_CAPABILITY_ROTATION','ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V2' in advisor and 'ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V3' in advisor and 'capability_skipped' in advisor and 'return 1440' in advisor)
ck('LOWVIEWS_RUN_ID_SCOPE','ACCOUNT_LOW_VIEWS_RUN_ID_SCOPE_V1' in advisor and 'lv30_{account_id}_' in advisor)
ck('LOWVIEWS_ALIAS_HOURLY','ACCOUNT_LOW_VIEWS_PROVIDER_ALIAS_HOURLY_V1' in advisor and '"account_low_views_ramp"}' in advisor)
ck('LOWVIEWS_NEGATIVE_FEEDBACK','ACCOUNT_LOW_VIEWS_NEGATIVE_FEEDBACK_GUARD_V1' in advisor and 'or _account_low_views_ramp' in advisor)
ck("LOWVIEWS_ACCOUNT_SIGNAL_COOLDOWN","ACCOUNT_LOW_VIEWS_ACCOUNT_SIGNAL_COOLDOWN_V1" in advisor and "account_no_signal_measurement_wait" in advisor)
ck('LOWVIEWS_STATS_FRESH_15M_SELECTION','ACCOUNT_LOW_VIEWS_FRESH_SIGNAL_V3' in advisor and 'money_stats_snapshot_eligible' in advisor and 'max_age_seconds=900' in advisor)
ck('AVITO_EFFECTIVE_MIN_BID','AVITO_EFFECTIVE_MIN_BID_V1' in advisor)
ck('AVITO_UID_SELF_HEAL','AVITO_UID_SELF_HEAL_V1' in advisor and '/core/v1/accounts/self' in advisor)
ck('FIRST_BID_FULL_MONEY_GUARD','NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3' in advisor and 'MONEY_GUARD_ALL_RAISES_V3' in auto and 'LAUNCH_FIRST_BID_ONLY' not in auto)
ck('MONEY_CONTOUR_AUTODEPLOY_PROTECTED','MONEY_CONTOUR_PROTECTED_V1' in (ROOT/'app/ext_api/repo.py').read_text() and '_deny_autonomous_money_contour(paths)' in (ROOT/'app/ext_api/repo.py').read_text())
ck('DB_MONEY_GUARD_V4_NO_LAUNCH_BYPASS','BORIS_MARKETER_MONEY_MANDATE_DB_GUARD_V5_MAX150' in (ROOT/'sql/marketer_money_mandate_guard_v1.sql').read_text() and 'daily_budget_rub must be NULL' not in (ROOT/'sql/marketer_money_mandate_guard_v1.sql').read_text())
ck('KPI_STORAGE_MONEY_RECONCILE_DB','BORIS_KPI_MONEY_RECONCILE_DB_V2_MAX150' in (ROOT/'sql/marketer_kpi_money_reconcile_v1.sql').read_text())
ck('ROLLOUT_GLOBAL_PREFLIGHT','_global_preflight' in roll and 'MAX_ACCOUNTS_PER_CYCLE=8' in roll)
ck('ROLLOUT_PROOF_REQUIRES_APPLY','planned==0 or (applied>0 and blocked==0)' in roll)
ck('ROLLOUT_GUARD_BLOCK_NOT_FAILURE','ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V2' in roll and "'last_reason':'guarded_hold'" in roll and '(blocked>0 or skipped>0)' in roll)
ck('ROLLOUT_LIVE_PROOF_REQUIRED','ROLLOUT_LIVE_PROOF_REQUIRED_V1' in roll and 'live_successes>=1' in roll)
ck('NEW_AD_WATCH_ONE_PER_ACCOUNT','bootstrap_new_no_promo(a,max_items=1)' in watch)
ck('NEW_AD_WATCH_GLOBAL_CAP','GLOBAL_MUTATION_CAP=3' in watch and 'mutations >= GLOBAL_MUTATION_CAP' in watch)
_kpi_src=(ROOT/'kpi_goal_runner.py').read_text()
_batch_src=advisor
ck('KPI_FIRST_BID_BOUNDED',
   'FIRST_BID_BOUNDED_BASELINE_COHORT_V3' in _kpi_src
   and 'bootstrap_new_no_promo_batch(account_id, max_items=5)' in _kpi_src
   and 'FIRST_BID_BOUNDED_COHORT_V2' in _batch_src
   and 'batch_effective_limit' in _batch_src)
ck('FIRST_BID_BOUNDED_COHORT',
   'FIRST_BID_BOUNDED_COHORT_V2' in advisor
   and 'min(int(max_items or 1), 5)' in advisor
   and 'GLOBAL_MAX_AUTO_ACTIONS_RUN = 5' in (ROOT/'app/services/marketing_money_policy.py').read_text())
ck('CPX_GETBIDS_RETRY_AFTER_FAIL_CLOSED','CPX_GETBIDS_RETRY_AFTER_FAIL_CLOSED_V1' in advisor and 'avito_account_throttled' in advisor and 'retry_after_seconds' in advisor)
ck('CPX_FIRST_BID_THROTTLE_NO_RETRY','CPX_FIRST_BID_THROTTLE_NO_RETRY_V1' in advisor and 'for _attempt in range(4)' not in advisor)
ck('FIRST_BID_SHARED_ACCOUNT_THROTTLE','FIRST_BID_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1' in advisor and 'FIRST_BID_SHARED_ACCOUNT_THROTTLE_RECORD_V1' in advisor and 'first_bid_promotions_429' in advisor)
ck('CPX_ACCOUNT_THROTTLE_STOP','CPX_ACCOUNT_THROTTLE_STOP_V1' in advisor and 'CPX_LOWVIEWS_ACCOUNT_THROTTLE_STOP_V1' in advisor)
ck('KPI_NEW_FEED_RAMP_BOUNDED', 'NEW_FEED_ACCOUNT_SINGLE_PENDING_V7' in (ROOT/'kpi_goal_runner.py').read_text() and 'hourly_new_feed_low_views_ramp(account_id, max_items=1)' in (ROOT/'kpi_goal_runner.py').read_text())
ck('NEW_FEED_PENDING_ITEM_SKIP', 'NEW_FEED_PENDING_ITEM_SKIP_V7' in advisor and 'if iid in _pending_new_feed:' in advisor and 'new_feed_account_measurement_pending' not in advisor)

ck('CPX_APPLY_LANE_GUARD','BLOCKED_UNTRUSTED_MONEY_LANE' in runner and 'staged_rollout_v1' in runner)
ck('UNIFIED_STAGED_ALL_ACCOUNTS','UNIFIED_STAGED_ALL_MARKETER_ACCOUNTS_V1' in roll and 'eligible=[a for a in eligible if a!=EVGENIY]' not in roll)
ck('KPI_APPLY_LANE_GUARD','BLOCKED_UNTRUSTED_MONEY_LANE' in (ROOT/'kpi_goal_runner.py').read_text() and 'controlled_kpi_v1' in (ROOT/'kpi_goal_runner.py').read_text())
ck('KPI_APPLY_SERVICE_CALLER_GUARD','KPI_APPLY_SERVICE_CALLER_GUARD_V1' in (ROOT/'kpi_goal_runner.py').read_text() and 'BLOCKED_UNTRUSTED_KPI_APPLY_CALLER' in (ROOT/'kpi_goal_runner.py').read_text())
ck('GENERIC_LOWVIEWS_MONEY_LANE_REMOVED','ACCOUNT_LOW_VIEWS_MONEY_LANE_REMOVED_V2' in (ROOT/'kpi_goal_runner.py').read_text() and 'ACCOUNT_LOW_VIEWS_API_MONEY_LANE_REMOVED_V2' in (ROOT/'app/api/avito.py').read_text())
ck('DIRECT_LOWVIEWS_LANE_GUARD','ACCOUNT_LOW_VIEWS_DIRECT_LANE_GUARD_V3' in advisor and 'controlled_lowviews_v2' in advisor)
ck('DIRECT_LOWVIEWS_SERVICE_CALLER_GUARD','ACCOUNT_LOW_VIEWS_SERVICE_CALLER_GUARD_V2' in advisor and 'required_caller' in advisor and '/proc/self/cgroup' in advisor)
ck('CANONICAL_GLOBAL_HARD_CAP_150','GLOBAL_HARD_MAX_BID_RUB = 150.0' in (ROOT/'app/services/marketing_money_policy.py').read_text())
cap_reconciler=(ROOT/'cpx_cap_reconciler.py').read_text()
ck('CPX_CAP_RETRY_AFTER_FAIL_CLOSED','CPX_CAP_RETRY_AFTER_FAIL_CLOSED_V1' in cap_reconciler and 'raise ProviderDeferred("avito_cpx_rate_limit"' in cap_reconciler)
ck('CPX_CAP_ACCOUNT_THROTTLE_STOP','CPX_CAP_ACCOUNT_THROTTLE_STOP_V1' in cap_reconciler and 'result["status"] = "deferred"' in cap_reconciler)
ck('CPX_CAP_MANUAL_THROTTLE_PROPAGATE','CPX_CAP_MANUAL_THROTTLE_PROPAGATE_V1' in cap_reconciler and 'except ProviderDeferred:' in cap_reconciler)
throttle_ledger=(ROOT/'app/services/avito_account_throttle.py').read_text()
ck('AVITO_SHARED_ACCOUNT_THROTTLE_LEDGER','AVITO_SHARED_ACCOUNT_THROTTLE_LEDGER_V1' in throttle_ledger and 'record_account_throttle' in throttle_ledger and 'account_throttle_remaining' in throttle_ledger)
ck('AVITO_SHARED_THROTTLE_STATS_PRECHECK','AVITO_SHARED_ACCOUNT_THROTTLE_STATS_PRECHECK_V1' in avito if 'avito' in globals() else 'AVITO_SHARED_ACCOUNT_THROTTLE_STATS_PRECHECK_V1' in (ROOT/'app/api/avito.py').read_text())
ck('CPX_SHARED_THROTTLE_PRECHECK','CPX_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1' in advisor and 'record_account_throttle' in advisor)
ck('CPX_ARCHIVE_SHARED_THROTTLE','CPX_ARCHIVE_FINAL_SHARED_THROTTLE_RECHECK_V1' in advisor and 'cpx_archive_remove_429' in advisor and 'avito_account_throttled' in advisor)
ck('CPX_BALANCE_SHARED_THROTTLE','CPX_BALANCE_SHARED_ACCOUNT_THROTTLE_V1' in advisor and 'cpx_balance_self_429' in advisor and 'cpx_balance_429' in advisor and 'blocked_balance_rate_limited' in advisor)
ck('CPX_RECEIPT_SHARED_ACCOUNT_THROTTLE','CPX_RECEIPT_SHARED_ACCOUNT_THROTTLE_V1' in advisor and 'cpx_receipt_429' in advisor and 'waiting_throttle' in advisor)
ck('CPX_CAP_SHARED_THROTTLE_PRECHECK','AVITO_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1' in cap_reconciler and 'record_account_throttle' in cap_reconciler)
ck("DEPLOY_COORDINATOR_FAIL_CLOSED","DEPLOY_COORDINATOR_FAIL_CLOSED_V1" in (ROOT/"rolling_restart_backend.sh").read_text() and "BLOCKED_NON_SERVICE_ROLLING_CALLER" in (ROOT/"rolling_restart_backend.sh").read_text())
ck("DEPLOY_SINGLE_BACKEND_ROLLOUT_OWNER","DEPLOY_SINGLE_BACKEND_ROLLOUT_OWNER_V1" in (ROOT/"app/ext_api/deploy.py").read_text())
ck("BACKEND_ROLLING_STRICT_SINGLE_OWNER","*boris-deploy.service*" in (ROOT/"rolling_restart_backend.sh").read_text() and "deploy_v1)" in (ROOT/"rolling_restart_backend.sh").read_text() and "production_guardian_v1" not in (ROOT/"rolling_restart_backend.sh").read_text())
ck("MARKETER_ROLLOUT_SERVICE_CALLER_GUARD","MARKETER_ROLLOUT_SERVICE_CALLER_GUARD_V2" in (ROOT/"marketer_rollout_runner.py").read_text() and "BLOCKED_UNTRUSTED_ROLLOUT_CALLER" in (ROOT/"marketer_rollout_runner.py").read_text() and "/proc/self/cgroup" in (ROOT/"marketer_rollout_runner.py").read_text())

ck('BACKGROUND_WORKER_WATCHDOG','_new_ad_bid_watch_loop' in worker and '("new_ad_bid_watch", _new_ad_bid_watch_loop)' in worker)
ck('MARKETER_SPEND_FRESH_15M_ROLLOUT','MARKETER_GROWTH_SIGNAL_MAX_AGE_15M_V1' in roll and 'max_age_seconds=900' in roll)
ck('MARKETER_SPEND_FRESH_15M_MUTATION','MARKETER_MUTATION_SPEND_FRESH_15M_V1' in advisor and 'max_age_seconds=900' in advisor)
ck('MARKETER_STATS_FRESH_15M_ROLLOUT','MARKETER_STATS_FRESH_15M_PREFLIGHT_V1' in roll and '_ds_age <= 900' in roll and 'stats_not_fresh' in roll)
ck('MARKETER_SPEND_STATS_COHERENT_PREFLIGHT','MARKETER_PREFLIGHT_SPEND_STATS_COHERENCE_V1' in roll and '_snapshot_skew <= 300' in roll and 'stats_spend_snapshot_skew' in roll)
ck("MARKETER_PROVEN_ACTIVE_BID_ONLY", "MARKETER_PROVEN_ACTIVE_BID_ONLY_V1" in runner and "current_bid_rub" in runner)
ck("MICRO_CONVERTER_DEEP_HEADROOM", "MICRO_CONVERTER_DEEP_HEADROOM_V1" in runner and "MICRO_PROVEN_MAX_PER_CYCLE = 1" in runner and "MICRO_PROVEN_MAX_CPL_SHARE = 0.50" in runner and "micro_converter_deep_headroom" in runner and "_advice_raise_by_id" in runner and "days_with_data" in runner)
ck("WHOLE_RUBLE_STEP_GRANULARITY", "MARKETER_WHOLE_RUBLE_STEP_GRANULARITY_V1" in runner and "_whole_ruble_raise_possible" in runner and "whole_ruble_granularity_skipped" in runner)
ck("ACCOUNT_RAISE_MEASUREMENT_BACKLOG_CAP", "ACCOUNT_RAISE_MEASUREMENT_BACKLOG_CAP_V1" in advisor and "MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT = 2" in advisor and "_active_raise_measurement_summary" in advisor)
ck("FIRST_BID_ACCOUNT_MEASUREMENT_BACKLOG", "FIRST_BID_ACCOUNT_MEASUREMENT_BACKLOG_V1" in advisor and '"status": "measurement_wait"' in advisor and '"account_measurement_backlog_wait"' in advisor)
ck("ACCOUNT_RAISE_MEASUREMENT_FINAL_GUARD", "ACCOUNT_RAISE_MEASUREMENT_FINAL_GUARD_V1" in advisor and '"blocked_by": "account_measurement_backlog_wait"' in advisor)
ck("KPI_MEASUREMENT_BACKLOG_NO_SECOND_MONEY_LANE", "MONEY_MEASUREMENT_WAIT" in _kpi_src and '_measurement_backlog_wait' in _kpi_src and '"status": "measurement_wait"' in _kpi_src)
ck("ACCOUNT_RAISE_MEASUREMENT_BACKLOG_SELF_HEAL", "ACCOUNT_RAISE_MEASUREMENT_BACKLOG_SELF_HEAL_V1" in advisor and "_compact_raise_measurement_backlog" in advisor and "inconclusive_backlog_compaction" in advisor and "do_not_learn_from_overparallel_window" in advisor and '"backlog_compaction": measurement_compaction' in advisor)
ck("ACCOUNT_MEASUREMENT_BACKLOG_PLAN_HOLD", "MARKETER_ACCOUNT_MEASUREMENT_BACKLOG_PLAN_HOLD_V1" in runner and "MEASUREMENT_WAIT:" in runner and "account_measurement_backlog_wait" in runner and "measurement_sweep_then_retry_automatically" in runner)
ck("ROLLOUT_GUARD_V1_COMPAT_MARKER", "ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V1" in roll and "ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V2" in roll)
ck("MARKETER_CURRENT_PROMOTION_TRUTH", "MARKETER_CURRENT_PROMOTION_TRUTH_V2" in runner and "promotion_active" in runner)
ck('MARKETER_PUSH_MAX_CPL_KEYS_QUOTED', "headroom.get('actual_cpl_rub')" in runner and "headroom.get('red_cpl_rub')" in runner and 'headroom.get(actual_cpl_rub)' not in runner and 'headroom.get(red_cpl_rub)' not in runner)
ck('MARKETER_STATS_FRESH_15M_MUTATION','MARKETER_MUTATION_STATS_FRESH_15M_V1' in advisor and '_stats_age <= 900' in advisor and '_mut_stats_ok' in advisor)
ck('MARKETER_ADVICE_STATS_FRESH_15M','MARKETER_ADVICE_STATS_FRESH_15M_V1' in advisor and 'money_stats_snapshot_eligible' in advisor and 'not _stats_money_fresh' in advisor)
ck('MARKETER_ADVICE_SHARED_ACCOUNT_THROTTLE','MARKETER_ADVICE_SHARED_ACCOUNT_THROTTLE_V1' in advisor and 'cpx_advice_promotions_429' in advisor and 'stats_not_fresh' in advisor and 'avito_account_throttled' in advisor)
ck('MARKETER_ADVICE_THROTTLE_BEFORE_TOKEN','MARKETER_ADVICE_THROTTLE_BEFORE_TOKEN_V1' in advisor and '_advice_retry <= 0 and _money_entitlement_active and _stats_money_fresh and tok and item_ids' in advisor)
cpxpromo=(ROOT/'app/api/cpxpromo.py').read_text()
ck('CPXPROMO_STATS_FRESH_15M_MUTATION','CPXPROMO_STATS_FRESH_15M_V1' in cpxpromo and 'CPXPROMO_RAISE_REQUIRES_FRESH_STATS_V1' in cpxpromo and 'blocked_stats_unavailable' in cpxpromo)
ck('CPXPROMO_RAISE_FRESHNESS_RECHECK','CPXPROMO_RAISE_RECHECK_AFTER_DIRECTION_V1' in cpxpromo and 'money = _money_state(body.account_id)' in cpxpromo)
ck('CPXPROMO_FINAL_RAISE_SIGNAL_RECHECK','CPXPROMO_FINAL_RAISE_SIGNAL_RECHECK_V1' in cpxpromo and 'final_money = _money_state(body.account_id)' in cpxpromo and 'money_signal_stale_or_degraded' in cpxpromo)
ck('CPXPROMO_SHARED_ACCOUNT_THROTTLE','CPXPROMO_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1' in cpxpromo and 'record_account_throttle' in cpxpromo and 'avito_account_throttled' in cpxpromo)
ck('CPXPROMO_FINAL_SHARED_THROTTLE','CPXPROMO_FINAL_SHARED_THROTTLE_RECHECK_V1' in cpxpromo and 'cpxpromo_setmanual_429' in cpxpromo)
ck('CPXPROMO_DIRECT_SHARED_THROTTLE','CPXPROMO_DIRECT_SHARED_THROTTLE_V1' in cpxpromo and 'cpxpromo_bids_429' in cpxpromo and 'cpxpromo_promotions_429' in cpxpromo and 'cpxpromo_remove_429' in cpxpromo)
ck('NON_LAUNCH_ZERO_BID_RAISE_FAIL_CLOSED',
   'NON_LAUNCH_ZERO_BID_RAISE_FAIL_CLOSED_V2' in advisor
   and 'blocked_bid_delta_unprovable' in advisor
   and '_is_bounded_first_activation' in advisor
   and '_is_new_feed_launch or _native_provider_probe_first_bid' in advisor)
ck('NATIVE_PROVIDER_PROBE_FIRST_BID_GUARDS',
   'NATIVE_PROVIDER_PROBE_FIRST_BID_V1' in advisor
   and 'NATIVE_PROVIDER_PROBE_FIRST_BID_DAILY_CAP2_V1' in advisor
   and '_native_daily_cap = 2' in advisor
   and 'native_first_bid_unresolved_receipt' in advisor
   and 'native_first_bid_daily_cap' in advisor
   and 'write_capability") or "") == "provider_probe_money"' in advisor
   and 'reason_code") or "") == "has_contacts"' in advisor
   and 'NATIVE_PROVIDER_EFFECTIVE_MIN_FINAL_FENCE_V1' in advisor)
ck('NATIVE_PROVIDER_PROBE_FIRST_BID_PRIORITY',
   'NATIVE_PROVIDER_PROBE_FIRST_BID_PRIORITY_V1' in advisor
   and '_native_first_priority_ids' in advisor
   and '0 if int(x["item_id"]) in _native_first_priority_ids else 1' in advisor)
ck('NATIVE_FIRST_BID_MAX_TWO_EXPERIMENTS',
   'NATIVE_FIRST_BID_MAX_TWO_EXPERIMENTS_V1' in advisor
   and '_native_first_measurement_cap = 2' in advisor
   and 'native_first_bid_measurement_wait' in advisor
   and 'money_identity_proof") or "") == "cpx_execution_receipt"' in advisor)
ck('NATIVE_RECEIPT_MEASURE_UI_CONTINUITY',
   'NATIVE_RECEIPT_MEASURE_UI_CONTINUITY_V1' in advisor
   and '_receipt_backed_native_wait' in advisor
   and 'measure_matches_current = bool(_receipt_backed_native_wait)' in advisor)
ck('AUTONOMY_ROUNDING_BYPASS_DISABLED_AT_CALLER','AUTONOMY_ROUNDING_BYPASS_DISABLED_AT_CALLER_V1' in advisor and '"hourly_feed_ramp": False' in advisor and '"fast_raise_rounding": False' in advisor)
signal_guard=(ROOT/'app/services/marketing_signal_guard.py').read_text()
action_log=(ROOT/'app/api/action_log_api.py').read_text()
ck('MONEY_STATS_FRESH_HELPER','money_stats_snapshot_eligible' in signal_guard and 'max_age_seconds: float = 900.0' in signal_guard)
ck('CPX_FINAL_RAISE_SIGNAL_RECHECK','CPX_FINAL_RAISE_SIGNAL_RECHECK_V1' in advisor and 'money_raise_signals_eligible' in signal_guard and 'max_age_seconds=900' in advisor and 'money_signal_stale_or_degraded' in advisor)
ck('ROLLBACK_RAISE_STATS_FRESH_15M','ROLLBACK_RAISE_STATS_FRESH_15M_V1' in action_log and 'money_stats_snapshot_eligible' in action_log and 'blocked_stats_unavailable' in action_log)
ck('ACTION_LOG_ROLLBACK_SHARED_THROTTLE','ACTION_LOG_ROLLBACK_SHARED_THROTTLE_V1' in action_log and 'ACTION_LOG_ROLLBACK_FINAL_SHARED_THROTTLE_RECHECK_V1' in action_log and 'action_log_rollback_getbids_429' in action_log and 'action_log_rollback_setmanual_429' in action_log)
avito=(ROOT/'app/api/avito.py').read_text()
ck('AVITO_STATS_COLLECTION_TIMESTAMP','MARKETER_STATS_COLLECTION_FRESHNESS_V1' in avito and '"collected_at": _dt.now(_tz.utc).isoformat()' in avito)
ck('AVITO_RETRY_AFTER_ACCOUNT_THROTTLE','STATS_CONFIRMED_429_NO_RETRY_V1' in avito and 'record_account_throttle(account_id, _retry_after or 30.0, source="stats_read_429")' in avito and 'AVITO_ACCOUNT_THROTTLE_STOP_V1' in avito and 'AVITO_ACCOUNT_THROTTLE_STOP_V2' in avito)
ck('DIRECTOR_OVERVIEW_SHARED_THROTTLE','DIRECTOR_OVERVIEW_SHARED_THROTTLE_V1' in avito and '_overview_throttle_remaining(acc.account_id) > 0' in avito and 'director_overview_items_429' in avito and 'director_overview_contacts_stats_429' in avito)
ck('FEED_IMPORT_SHARED_THROTTLE','FEED_IMPORT_SHARED_ACCOUNT_THROTTLE_V1' in avito and 'FEED_IMPORT_NO_PARTIAL_INVENTORY_ON_THROTTLE_V1' in avito and 'feed_import_items_429' in avito and '_feed_throttle_remaining(account_id)' in avito)
initial_portfolio=(ROOT/'app/services/initial_portfolio.py').read_text()
ck('INITIAL_PORTFOLIO_SHARED_THROTTLE','INITIAL_PORTFOLIO_SHARED_ACCOUNT_THROTTLE_V1' in initial_portfolio and 'INITIAL_PORTFOLIO_THROTTLE_BEFORE_TOKEN_V1' in initial_portfolio and 'record_account_throttle' in initial_portfolio and 'initial_portfolio_items_429' in initial_portfolio and 'initial_portfolio_stats_429' in initial_portfolio and 'initial_portfolio_self_429' in initial_portfolio)
budget_brake=(ROOT/'cpx_budget_brake.py').read_text()
ck('CPX_BUDGET_BRAKE_SHARED_THROTTLE','CPX_BUDGET_BRAKE_SHARED_THROTTLE_V1' in budget_brake and 'CPX_BUDGET_BRAKE_ACCOUNT_THROTTLE_STOP_V1' in budget_brake and 'record_account_throttle(account_id, exc.retry_after_seconds, source="cpx_budget_brake_429")' in budget_brake)
ck('CPX_BUDGET_BRAKE_SPEND_FRESH_15M','CPX_BUDGET_BRAKE_SPEND_FRESH_15M_V1' in budget_brake and 'FRESH_SECONDS = 900' in budget_brake)
ck('CPX_BUDGET_BRAKE_EARLY_CUTOFF','CPX_BUDGET_BRAKE_ADAPTIVE_RESERVE_V1' in budget_brake and 'adaptive_budget_brake_policy' in budget_brake and 'if ratio < _cutoff' in budget_brake and 'early_cutoff' in budget_brake)
ck('CPX_BUDGET_BRAKE_CUTOFF_OBSERVABILITY','CPX_BUDGET_BRAKE_CUTOFF_OBSERVABILITY_V1' in budget_brake and 'adaptive_cutoff_pct' in budget_brake and 'adaptive_reserve_rub' in budget_brake)
ck('CPX_BUDGET_BRAKE_TRANSPORT_DEFERRED','CPX_BUDGET_BRAKE_TRANSPORT_DEFERRED_V1' in budget_brake and 'transport_retry_required' in budget_brake and 'httpx.ConnectTimeout' in budget_brake and 'httpx.PoolTimeout' in budget_brake)

ownerless=(ROOT/'scripts/ownerless_marketer_75m_v3.py').read_text()
ck('OWNERLESS_SOAK_RETIRED', 'RETIRED_OWNERLESS_MARKETER_V1' in ownerless and 'BLOCKED_OWNERLESS_MARKETER' in ownerless and 'SystemExit(23)' in ownerless and 'marketer_rollout_runner.py' not in ownerless)
planeta_soak=(ROOT/'planeta_60m_autonomous_soak.py').read_text()
ck('OWNERLESS_PLANETA_MONEY_SOAK_RETIRED','RETIRED_OWNERLESS_PLANETA_MONEY_SOAK_V1' in planeta_soak and 'BLOCKED_RETIRED_OWNERLESS_PLANETA_MONEY_SOAK' in planeta_soak)
ck("NON_MONEY_MONEY_FENCE_POLICY_WAIT", "NON_MONEY_MONEY_FENCE_IS_POLICY_WAIT_V1" in (ROOT/"kpi_goal_runner.py").read_text())
ck("NON_MONEY_MEASUREMENT_LIFECYCLE_SWEEP", "NON_MONEY_MEASUREMENT_LIFECYCLE_SWEEP_V1" in (ROOT/"kpi_goal_runner.py").read_text() and "/api/cpx_advisor/run" in (ROOT/"kpi_goal_runner.py").read_text())
ck("NON_MONEY_FRESH_INVENTORY_PROVIDER_SKIP", "NON_MONEY_FRESH_INVENTORY_PROVIDER_SKIP_V1" in (ROOT/"kpi_goal_runner.py").read_text() and "_fresh_complete_active_inventory" in (ROOT/"kpi_goal_runner.py").read_text() and "NON_MONEY_INVENTORY_FROM_DAILY_STATS" in (ROOT/"kpi_goal_runner.py").read_text() and '"source": "fresh_complete_daily_stats"' in (ROOT/"kpi_goal_runner.py").read_text())
ck("NON_MONEY_LOCK_WAIT_FOR_DEPLOY", "NON_MONEY_LOCK_WAIT_FOR_DEPLOY_V1" in (ROOT/"kpi_goal_runner.py").read_text() and "BORIS_KPI_NONMONEY_LOCK_WAIT_SEC" in (ROOT/"kpi_goal_runner.py").read_text() and "KPI_NON_MONEY_LOCK_ACQUIRED" in (ROOT/"kpi_goal_runner.py").read_text())
ck("NON_MONEY_LOCK_TIMEOUT_VISIBLE", "NON_MONEY_LOCK_TIMEOUT_VISIBLE_V1" in (ROOT/"kpi_goal_runner.py").read_text() and "DEFERRED_KPI_LOCK_BUSY" in (ROOT/"kpi_goal_runner.py").read_text() and "return 75" in (ROOT/"kpi_goal_runner.py").read_text())
ck("MEASUREMENT_SWEEP_INTERNAL_HA", "MEASUREMENT_SWEEP_INTERNAL_HA_V2" in (ROOT/"kpi_goal_runner.py").read_text() and "for _measure_base in API_BASES" in (ROOT/"kpi_goal_runner.py").read_text())
ck("PREFLIGHT_MANDATE_REASON_TRUTH", "MARKETER_PREFLIGHT_MANDATE_REASON_TRUTH_V2" in (ROOT/"marketer_rollout_runner.py").read_text() and "daily_budget_owner_provenance_missing" in (ROOT/"marketer_rollout_runner.py").read_text())
ck("MEASURE_NO_BASELINE_TIMEOUT", "MEASURE_NO_BASELINE_TIMEOUT_V1" in (ROOT/"app/api/cpx_advisor.py").read_text() and "inconclusive_no_baseline" in (ROOT/"app/api/cpx_advisor.py").read_text())
guardian_lifecycle=(ROOT/"guardian_lifecycle_snapshot.py").read_text()
ck("GUARDIAN_MEASUREMENT_DUE_PRIORITY", "MEASUREMENT_DUE_BEATS_SATURATION_V2" in guardian_lifecycle and 'elif finalization_due: severity="finalization_due"' in guardian_lifecycle)
ck("GUARDIAN_MEASUREMENT_SELF_HEAL", "GUARDIAN_MEASUREMENT_SELF_HEAL_V1" in guardian_lifecycle and "finalize_due_measurements" in guardian_lifecycle and "max_items=50" in guardian_lifecycle and "_finalize_measure_state" in guardian_lifecycle)
ck("GUARDIAN_CANONICAL_CALENDAR_AGE", "GUARDIAN_CANONICAL_CALENDAR_AGE_V1" in guardian_lifecycle and "now.date()-started.date()" in guardian_lifecycle and "now.date()-dt.date()" in guardian_lifecycle)
ck("GUARDIAN_HISTORICAL_MEASURE_CLEANUP", "GUARDIAN_HISTORICAL_MEASURE_CLEANUP_V1" in guardian_lifecycle and "finalization_due=bool(aged_7d)" in guardian_lifecycle and "finalization_due=bool(service_active and aged_7d)" not in guardian_lifecycle)
ck("GUARDIAN_BUDGET_BRAKE_SELF_HEAL", "GUARDIAN_BUDGET_BRAKE_SELF_HEAL_V1" in guardian_lifecycle and 'Storage.key=="cpx_budget_brake_runtime"' in guardian_lifecycle and 'used < 90.0' in guardian_lifecycle and 'max_accounts=1' in guardian_lifecycle and '"cpx_budget_brake.py","--apply","--account",aid' in guardian_lifecycle)
cap_reconciler=(ROOT/"cpx_cap_reconciler.py").read_text()
ck("CPX_CAP_TRANSPORT_DEFERRED", "CPX_CAP_TRANSPORT_DEFERRED_V2" in cap_reconciler and 'result["status"] = "deferred"' in cap_reconciler and '"ConnectTimeout", "ReadTimeout", "ConnectError", "PoolTimeout"' in cap_reconciler)
ck("CPX_CAP_SAFETY_LOWER_LANE", "CPX_CAP_SAFETY_LOWER_LANE_V1" in cap_reconciler and '"safety_lane": "lower_only"' in cap_reconciler and '"can_increase_spend": False' in cap_reconciler)
ck("LATE_DAY_CAUSAL_POOL", "LATE_DAY_CAUSAL_POOL_V2" in (ROOT/"late_day_spend_controller.py").read_text() and "if contacts_7d<=0:continue" in (ROOT/"late_day_spend_controller.py").read_text() and "LATE_DAY_MEASUREMENT_BINDING_V3" in (ROOT/"late_day_spend_controller.py").read_text() and "LATE_DAY_LIVE_CONVERTER_FALLBACK_V1" in (ROOT/"late_day_spend_controller.py").read_text())
ck("LATE_DAY_MONEY_CONTOUR_PROTECTED", "backend/late_day_spend_controller.py" in (ROOT/"app/ext_api/repo.py").read_text())
ck("REACH_RESCUE_DAILY_GUARD", "REACH_RESCUE_DAILY_EVIDENCE_V2" in (ROOT/"app/services/intraday.py").read_text() and "REACH_RESCUE_ONE_HYPOTHESIS_PER_CYCLE_V2" in (ROOT/"cpx_advisor_runner.py").read_text() and "REACH_RESCUE_DAILY_MEASURE_V3" in (ROOT/"app/api/cpx_advisor.py").read_text())
ck("NEW_FEED_RAMP_FRESH_SIGNAL", "NEW_FEED_RAMP_FRESH_SIGNAL_V4" in (ROOT/"app/api/cpx_advisor.py").read_text())
ck("PROFITABLE_DAY_NORMAL_MEASUREMENT", "PROFITABLE_DAY_NORMAL_MEASUREMENT_V3" in (ROOT/"app/api/cpx_advisor.py").read_text())
ck("LATE_DAY_ONE_STEP_THEN_MEASURE", "LATE_DAY_ONE_STEP_THEN_MEASURE_V2" in (ROOT/"late_day_spend_controller.py").read_text())
ck("LATE_DAY_MEASUREMENT_BINDING", "LATE_DAY_MEASUREMENT_BINDING_V3" in (ROOT/"late_day_spend_controller.py").read_text() and "late_day_pacing_compatible" not in (ROOT/"late_day_spend_controller.py").read_text())
ck("NEW_FEED_ONE_STEP_THEN_MEASURE", "NEW_FEED_ONE_STEP_THEN_MEASURE_V5" in (ROOT/"app/api/cpx_advisor.py").read_text())
late_day=(ROOT/'late_day_spend_controller.py').read_text()
marketer_timer=Path('/etc/systemd/system/boris-ai-marketer.timer').read_text()
guardian=Path('boris_guardian.py').read_text()
ck('KPI_TIMER_SELF_HEAL_GUARDIAN','KPI_TIMER_SELF_HEAL_V2' in guardian and 'KPI_TIMER_SELF_HEAL_V1_TEST_CONTRACT' in guardian and 'if (not timer_active) or (not timer_enabled):' in guardian and "systemctl enable --now " in guardian and 'NextElapseUSecRealtime' in guardian and Path('/etc/systemd/system/boris-ai-marketer.timer.d/20-no-manual-stop.conf').exists() and 'RefuseManualStop=yes' in Path('/etc/systemd/system/boris-ai-marketer.timer.d/20-no-manual-stop.conf').read_text())
marketer_pipe=(ROOT/'ai_marketer_hourly.sh').read_text()
ck('LATE_DAY_SPEND_CATCHUP_POLICY','INTRADAY_SPEND_PACING_START_V1' in late_day and 'BOOST_START_HOUR=10' in late_day and 'LATE_DAY_SPEND_TIME_PACING_V1' in late_day and '_catchup_intensity(ratio, now_msk)' in late_day)
ck('LATE_DAY_SPEND_TIME_PACING','expected_spend_ratio' in late_day and 'pacing_gap_ratio' in late_day and 'seconds / 86400.0' in late_day)
ck('LATE_DAY_SPEND_EFFECT_LEARNING','LATE_DAY_SPEND_EFFECT_LEARNING_V1' in late_day and 'late_day_spend_learning' in late_day and 'contacts_at_check' in late_day and 'learn_previous_days(now)' in late_day)
ck('LATE_DAY_NEGATIVE_FEEDBACK_FENCE','LATE_DAY_NEGATIVE_FEEDBACK_FENCE_V1' in late_day and "_recent_verdicts[-2:]" not in late_day and "all(v==\"no_lead_after_boost\" for v in _recent_verdicts)" in late_day)
ck('LATE_DAY_SPEND_FRESH_MONEY_PROOF','LATE_DAY_SPEND_FRESH_MONEY_PROOF_V1' in late_day and 'money_raise_signals_eligible' in late_day and 'max_age_seconds=900.0' in late_day)
ck('LATE_DAY_CANONICAL_ENTITLEMENT','LATE_DAY_CANONICAL_SERVICE_ENTITLEMENT_V1' in late_day and 'marketing_service_entitlement' in late_day and 'blocked_entitlement' in late_day and 'changed_avito":False' in late_day)
ck('LATE_DAY_ONE_STEP_MEASURE_POLICY','LATE_DAY_ONE_STEP_THEN_MEASURE_V2' in late_day and 'target_uplift_pct' in late_day and 'step_pct=min(int(target_boost),10)' in late_day and 'waiting_measurement' in late_day)
ck('LATE_DAY_LIVE_CONVERTER_POLICY','LATE_DAY_LIVE_CONVERTER_FALLBACK_V1' in late_day and 'today_contacts>0' in late_day and 'contacts_7d>=2' in late_day)
ck('LATE_DAY_MEASUREMENT_BINDING_POLICY','LATE_DAY_MEASUREMENT_BINDING_V3' in late_day and '_late_day_pacing_advice' in late_day and 'late_day_pacing_compatible' not in late_day)
ck('BUDGET_BRAKE_MONOTONIC_OVERLIMIT','CPX_BUDGET_BRAKE_MONOTONIC_OVERLIMIT_V2' in budget_brake and 'stale_monotonic_overlimit_proof' in budget_brake)
ck('BUDGET_BRAKE_SAFETY_LOWER_LANE','CPX_BUDGET_BRAKE_SAFETY_LOWER_LANE_V1' in budget_brake and '_remove(' in budget_brake and '_set_manual(' not in budget_brake and '"action":"raise"' not in budget_brake)
ck('ADAPTIVE_BUDGET_RESERVE_FINAL_RAISE_GUARD','MARKETER_ADAPTIVE_BUDGET_RESERVE_V1' in advisor and 'adaptive_budget_brake_policy' in advisor and '_budget_cutoff' in advisor)
ck('LATE_DAY_SPEND_CATCHUP_GLOBAL_STEP','STRICT_STEP10_LATE_DAY_V1' in late_day and 'step_pct=min(int(target_boost),10)' in late_day and 'STRICT_STEP10_SOURCE_INVARIANT_V1' in advisor and 'MAX_BID_STEP_PCT = GLOBAL_MAX_BID_DELTA_PCT' in advisor)
ck('LATE_DAY_SPEND_BASELINE_DURABLE','LATE_DAY_SPEND_BASELINE_DURABLE_V1' in advisor and 'baseline_bid_rub' in advisor)
ck('LATE_DAY_SPEND_EXACT_RESET_COMPENSATION','LATE_DAY_SPEND_RESET_COMPENSATION_V1' in auto and 'allowed_late_day_exact_compensation' in auto and 'max_reset_drop_pct' in auto)
ck('LATE_DAY_SPEND_IDEMPOTENT_NO_FAKE_STEP','LATE_DAY_SPEND_IDEMPOTENT_NO_FAKE_STEP_V1' in late_day and '_real_change' in late_day)
ck('LATE_DAY_SPEND_DIRECT_30_50_SCOPE','STRICT_STEP10_LATE_DAY_V1' in late_day and 'target_boost, expected_ratio, pacing_gap = _catchup_intensity' in late_day and 'target_boost<=0' in late_day and 'if contacts>=target:continue' in late_day and 'if cpl is not None and cpl>red:continue' in late_day)
ck('LATE_DAY_SPEND_SHORT_ALIAS_WINDOW','LATE_DAY_SPEND_SHORT_ALIAS_WINDOW_V1' in advisor and '15.0 / 1440.0' in advisor)
ck('LATE_DAY_SPEND_CONTENT_ISOLATION','LATE_DAY_SPEND_CONTENT_ISOLATION_V1' in avito and 'late_day_spend_boost_state' in avito)
ck('LATE_DAY_SPEND_CANONICAL_WIRE','LATE_DAY_SPEND_RESET_WIRE_V1' in marketer_pipe and 'LATE_DAY_SPEND_CATCHUP_WIRE_V1' in marketer_pipe and '21:00:00 UTC' in marketer_timer)

ck('AUTOLOAD_SINGLE_UPLOAD_ID_FALLBACK','AUTOLOAD_SINGLE_UPLOAD_ID_FALLBACK_V1' in avito and '_authoritative_upload_id=int(meta.get("upload_id") or body.get("upload_id") or 0)' in avito)
ck('AUTOLOAD_IDENTITY_REPORT_PERPAGE100','AUTOLOAD_IDENTITY_REPORT_PERPAGE100_V1' in avito and '_httpx_recover.get("https://api.avito.ru/autoload/v4/uploads/current/items",headers=head,params={"page":1,"perPage":100}' in avito and '_httpx_batch.get("https://api.avito.ru/autoload/v4/uploads/current/items",headers=head,params={"page":1,"perPage":100}' in avito)
# NEW_FEED_RAMP_RECBID_ESCAPE_V1: exact new-feed hourly ramp can move above
# Avito recBid==current, while ordinary lanes still keep the recommendation cap.
_src_cpx=Path('/root/BORIS/backend/app/api/cpx_advisor.py').read_text()
ck('NEW_FEED_RAMP_RECBID_ESCAPE', all(x in _src_cpx for x in (
    'NEW_FEED_RAMP_RECBID_ESCAPE_V1',
    '_can_cross_rec_bid = str(body.source or "") == "new_feed_hourly_ramp"',
    'not _fast_measure_mode and not _can_cross_rec_bid',
    'if max_bid and new_bid > max_bid')) )
# NEW_FEED_LAUNCH_VS_RAMP_MANDATE_ISOLATION_V1
_src_cpx2=Path('/root/BORIS/backend/app/api/cpx_advisor.py').read_text()
_src_auto2=Path('/root/BORIS/backend/app/services/autonomy.py').read_text()
ck('NEW_FEED_LAUNCH_VS_RAMP_MANDATE_ISOLATION', all(x in _src_cpx2 for x in (
    'NEW_FEED_LAUNCH_VS_RAMP_MANDATE_ISOLATION_V1', 'max_actions_run=1,max_actions_day=24',
    '"max_actions_run":1,"max_actions_day":24')) and
    'MONEY_MANDATE_STRICT_SOURCE_ISOLATION_V2' in _src_auto2 and
    'required_source = "new_feed_launch" if str(source or "") == "feed20" else "paid_tariff_kpi"' in _src_auto2 and
    'return None' in _src_auto2)
# NF10_HOURLY_RUN_SCOPE_V1
_src_auto3=Path('/root/BORIS/backend/app/services/autonomy.py').read_text()
_src_cpx3=Path('/root/BORIS/backend/app/api/cpx_advisor.py').read_text()
ck('NF10_HOURLY_RUN_SCOPE', 'NF10_HOURLY_RUN_SCOPE_V1' in _src_auto3 and
   'return ":".join(parts[:2])' in _src_auto3 and
   "request_id=f\"nf10:{now.strftime('%%Y%%m%%d%%H')}:{iid}\"".replace("%%","%") in _src_cpx3)
# MARKETING_MOSCOW_DAY_BOUNDARY_V1
from datetime import datetime as _qa_dt, timezone as _qa_tz
from zoneinfo import ZoneInfo as _qa_ZI
_moscow_boundary = _qa_dt(2026,9,4,21,30,tzinfo=_qa_tz.utc).astimezone(_qa_ZI("Europe/Moscow")).date().isoformat()
_clock_src=Path('/root/BORIS/backend/app/services/marketing_clock.py').read_text()
_signal_src=Path('/root/BORIS/backend/app/services/marketing_signal_guard.py').read_text()
_avito_src=Path('/root/BORIS/backend/app/api/avito.py').read_text()
ck('MARKETING_MOSCOW_DAY_BOUNDARY', _moscow_boundary=='2026-09-05' and
   'Europe/Moscow' in _clock_src and 'marketing_today_iso()' in _signal_src and
   'spending_date = marketing_today_iso()' in _avito_src and '"date": marketing_today_iso()' in _avito_src)
_roll_day_src=(ROOT/'marketer_rollout_runner.py').read_text(encoding='utf-8')
ck('MARKETER_PREFLIGHT_MOSCOW_STATS_DAY', 'MARKETER_PREFLIGHT_MOSCOW_STATS_DAY_V1' in _roll_day_src and
   "daily_stats:'+marketing_today_iso()" in _roll_day_src and
   "daily_stats:'+datetime.now(timezone.utc).date().isoformat()" not in _roll_day_src)

ck('CPX_ADVISOR_MONEY_WRITABILITY_SUMMARY','CPX_ADVISOR_MONEY_WRITABILITY_SUMMARY_V1' in advisor and 'canonical_writable_active' in advisor and 'read_only_active' in advisor and 'stop_money_try_content' not in advisor)
ck('CPX_ADVISOR_EFFECTIVE_MONEY_WRITABILITY','CPX_ADVISOR_EFFECTIVE_MONEY_WRITABILITY_V3' in advisor and '_effective_probe_allowed' in advisor and 'or (_effective_probe_allowed and str(i.get(\"id\") or \"\").isdigit())' in advisor)
ck('CPX_ADVISOR_BUDGET_BRAKE_OWNER_ACTION','CPX_ADVISOR_BUDGET_BRAKE_OWNER_ACTION_V1' in advisor and 'owner_action_required' in advisor and 'cpx_budget_brake_runtime' in advisor)
ck('CPX_ADVISOR_OWNER_ACTION_MINIMAL','CPX_ADVISOR_OWNER_ACTION_MINIMAL_V1' in advisor and '_money_writable == 0 and _money_read_only > 0' in advisor and 'внутренняя задача BORIS, действие владельца не требуется' in advisor)
ck('CPX_ADVISOR_MONEY_IDENTITY', 'CPX_ADVISOR_MONEY_IDENTITY_V2' in advisor and 'CPX_PROVIDER_PROBE_MONEY_WRITABILITY_V1' in advisor and 'provider_probe_money' in advisor and '{\"canonical\", \"provider_exact_money\", \"provider_probe_money\"}' in advisor and 'CPX_FINAL_PROVIDER_ITEM_PROOF_V1' in advisor and 'getBids/{body.item_id}' in advisor)
ck('CPX_ADVISOR_SERVICE_ENTITLEMENT_GATE','CPX_ADVISOR_SERVICE_ENTITLEMENT_GATE_V1' in advisor and 'marketing_service_period_inactive' in advisor and 'service_period_ended' in advisor and '_money_entitlement_active' in advisor)
ck('CPX_ADVISOR_INACTIVE_SERVICE_NO_OWNER_ACTION','CPX_ADVISOR_INACTIVE_SERVICE_NO_OWNER_ACTION_V1' in advisor and '_ent_state == "active"' in advisor and 'reason_code in {"no_kpi"}' in advisor and '_early_bid_autopilot' in advisor)
ck('CPX_ADVISOR_BAD_CPL_NO_BUDGET_ESCALATION','CPX_ADVISOR_BAD_CPL_NO_BUDGET_ESCALATION_V1' in advisor and '_cpl_redline_block' in advisor and 'действие владельца не требуется — BORIS продолжит безопасную оптимизацию и измерение' in advisor)
ck('CPX_ADVISOR_TARGET_BUDGET_NOT_WORST_CASE','CPX_ADVISOR_TARGET_BUDGET_NOT_WORST_CASE_V1' in advisor and 'KPI_GAP_TARGET_BUDGET_NOT_WORST_CASE_V1' in advisor and '_kpi_economic_infeasible' not in advisor and 'kpi_economically_infeasible' not in advisor)
ck('CPX_ADVISOR_BALANCE_OWNER_ACTION_PRIORITY','CPX_ADVISOR_BALANCE_OWNER_ACTION_PRIORITY_V1' in advisor and 'guardian_balance_funding_health' in advisor and '_balance_owner_action_required' in advisor and 'Реальный баланс Avito' in advisor and 'BORIS сам увидит пополнение' in advisor)
ck('CPX_ADVISOR_EARLY_SAFE_SNAPSHOT','CPX_ADVISOR_EARLY_SAFE_SNAPSHOT_V1' in advisor and '_persist_nonactionable_advice' in advisor and '"no_stats"' in advisor and '"no_kpi"' in advisor)
ck('NEW_FEED_RAMP_ATTEMPTED_STATE_ONLY', 'NEW_FEED_RAMP_ATTEMPTED_STATE_ONLY_V1' in advisor and
   'not marked checked until they are actually attempted' in advisor and
   'created_at": target.get("created_at")' in advisor)
ck('MONEY_WORKSTREAM_SCOPE_PREFLIGHT', 'MONEY_WORKSTREAM_SCOPE_PREFLIGHT_V1' in (ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8') and 'run/workstream_scope_guard.py' in (ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8') and 'AI_MARKETER_SCOPE_BLOCK' in (ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8'))
ck('CPX_MEASURE_CURRENT_TRUTH_SUPERSEDE', 'CPX_MEASURE_CURRENT_TRUTH_SUPERSEDE_V1' in advisor and 'do_not_learn_from_confounded_window' in advisor and 'write_capability_lost' in advisor and 'live_bid_changed' in advisor)
ck('CPX_MEASURE_PROMOTION_OFF_SUPERSEDE', 'CPX_MEASURE_PROMOTION_OFF_SUPERSEDE_V1' in (ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8') and '_bids_truth_complete' in (ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8') and 'promotion_inactive_or_bid_unreported' in (ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8'))
ck('CPX_MEASURE_EXPIRED_ENTITLEMENT_SUPERSEDE', 'CPX_MEASURE_EXPIRED_ENTITLEMENT_SUPERSEDE_V1' in (ROOT/'effect_check.py').read_text(encoding='utf-8') and 'service_period_ended' in (ROOT/'effect_check.py').read_text(encoding='utf-8') and 'do_not_learn_after_entitlement_end' in (ROOT/'effect_check.py').read_text(encoding='utf-8'))
ck('CPX_BUDGET_BRAKE_MEASURE_SUPERSEDE', 'CPX_BUDGET_BRAKE_MEASURE_SUPERSEDE_V1' in budget_brake and 'budget_brake_removed_promotion' in budget_brake and 'do_not_learn_from_braked_window' in budget_brake)
ck('CPX_EFFECT_ENTITLEMENT_CACHE', 'CPX_EFFECT_ENTITLEMENT_CACHE_V1' in (ROOT/'effect_check.py').read_text(encoding='utf-8') and '_entitlement_cache' in (ROOT/'effect_check.py').read_text(encoding='utf-8'))
_hourly_src=(ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8')
ck('LAUNCH_MANDATE_HOURLY_RECONCILE', 'LAUNCH_MANDATE_HOURLY_RECONCILE_V1' in advisor and 'LAUNCH_MANDATE_HOURLY_RECONCILE_WIRE_V1' in _hourly_src and 'reconcile_existing_new_feed_launch_mandates' in _hourly_src and _hourly_src.find('reconcile_existing_new_feed_launch_mandates') < _hourly_src.find('wait_for_dedicated_stats_owner'))
ck('LAUNCH_STALE_MODE_REVOKE', 'launchmode_v1' in advisor and 'Bid autopilot disabled; stale new-feed launch authority revoked.' in advisor and 'Autonomous marketing mode disabled; stale new-feed launch authority revoked.' in advisor)
ck('LAUNCH_STALE_CREDS_REVOKE', 'launchcreds_v1' in advisor and 'Real Avito credentials unavailable; stale new-feed launch authority revoked.' in advisor)
ck('STATS_ABSOLUTE_FIRST_PROVIDER_READ', 'STATS_ABSOLUTE_FIRST_PROVIDER_READ_V1' in _hourly_src and 'MARKETER_DEDICATED_STATS_SINGLE_OWNER_V1' in _hourly_src and _hourly_src.find('wait_for_dedicated_stats_owner') < _hourly_src.find('portfolio_bootstrap_runner.py'))
ck('DAILY_STATS_DEDICATED_SINGLE_OWNER', 'DAILY_STATS_PROVIDER_DEFERRED_EXIT_V1' in (ROOT/'daily_stats_collector.py').read_text(encoding='utf-8') and 'return 75' in (ROOT/'daily_stats_collector.py').read_text(encoding='utf-8') and 'MARKETER_DEDICATED_STATS_SINGLE_OWNER_V1' in _hourly_src and 'boris-daily-stats-collector.service' in _hourly_src and './venv/bin/python daily_stats_collector.py' not in _hourly_src and 'dedicated_stats_owner_not_completed' in _hourly_src)
ck('DAILY_STATS_WAIT_WITHIN_FRESHNESS_WINDOW','DEDICATED_STATS_WAIT_WITHIN_FRESHNESS_WINDOW_V1' in _hourly_src and 'local max_wait_sec=600' in _hourly_src)
ck('KPI_FRESH_STATS_MOSCOW_DAY','KPI_FRESH_STATS_MOSCOW_DAY_V1' in _kpi_src and 'today = marketing_today_iso()' in _kpi_src and 'datetime.now().date().isoformat()' not in _kpi_src)
ck('CPX_CURRENT_DAY_KPI_SIGNAL','CPX_ADVISOR_CURRENT_DAY_CONTACTS_ONLY_V1' in advisor and 'CPX_FINAL_RAISE_CURRENT_DAY_KPI_ONLY_V1' in advisor and 'ACCOUNT_LOW_VIEWS_CURRENT_DAY_SIGNAL_ONLY_V1' in advisor and 'PROFITABLE_DAY_CURRENT_DAY_CONTACTS_ONLY_V1' in advisor and '_item_stats_day = str((stats or {}).get("stats_date") or "")' in advisor and 'today.get("stats_date")' not in advisor)
ck('LATE_DAY_CURRENT_DAY_CONTACTS_ONLY','LATE_DAY_CURRENT_DAY_CONTACTS_ONLY_V1' in late_day and 'str(ds.get("stats_date") or "") == marketing_today_iso()' in late_day)
_signal_guard_src=(ROOT/'app/services/marketing_signal_guard.py').read_text(encoding='utf-8')
ck('PROVIDER_STATS_DAY_MONEY_FENCE','MARKETER_PROVIDER_STATS_DAY_MONEY_FENCE_V1' in _signal_guard_src and 'provider_stats_day_lagged' in _signal_guard_src and 'provider_day_current' in _signal_guard_src)
ck('RUNNER_PROVIDER_DAY_LAG_HOLD','MARKETER_RUNNER_PROVIDER_DAY_LAG_HOLD_V1' in runner and 'PROVIDER_STATS_WAIT' in runner and '_build_plan(acc, recs, advice=advice)' in runner)
ck('ROLLOUT_SKIPPED_SAFE_HOLD','ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V2' in roll and 'skipped=int(rt.get' in roll and '(blocked>0 or skipped>0)' in roll)
_daily_stats_src=(ROOT/'daily_stats_collector.py').read_text(encoding='utf-8')
ck('DAILY_STATS_PROVIDER_ALIAS_DEDUPE',
   'AVITO_STATS_PROVIDER_ALIAS_DEDUPE_V1' in _daily_stats_src and
   '_dedupe_provider_alias_rows' in _daily_stats_src and
   'FROM account_slots' in _daily_stats_src and
   "status IN ('connected','readonly')" in _daily_stats_src)
_runtime_roll_src=(ROOT/'marketer_rollout_runner.py').read_text(encoding='utf-8')
ck('MONEY_RUNTIME_LOCAL_RECONCILE_ON_STATS_WAIT',
   'MONEY_RUNTIME_LOCAL_RECONCILE_ON_STATS_WAIT_V1' in _runtime_roll_src and
   'reconcile_runtime_projections_on_stats_wait' in _runtime_roll_src and
   'runtime_truth_kind' in _runtime_roll_src and 'money_fail_closed_local_projection' in _runtime_roll_src and
   'dedicated_stats_owner_wait_local_truth' in _runtime_roll_src and
   'MONEY_RUNTIME_LOCAL_RECONCILE_ON_STATS_WAIT_WIRE_V1' in _hourly_src and
   'marketer_rollout_runner.py --runtime-reconcile-only' in _hourly_src and
   _hourly_src.find('kpi_goal_runner.py --non-money-apply') < _hourly_src.find('marketer_rollout_runner.py --runtime-reconcile-only'))
ck('CLIENT_WORKSPACE_MONEY_OBSERVER_ONLY', (ROOT/'app/api/control_plane.py').read_text(encoding='utf-8').count('CLIENT_WORKSPACE_MONEY_OBSERVER_ONLY_V1') >= 2 and 'canonical_money_owner_thread_id' in (ROOT/'app/api/control_plane.py').read_text(encoding='utf-8') and 'money_execution_mode' in (ROOT/'app/api/control_plane.py').read_text(encoding='utf-8') and 'no_separate_money_engine' in (ROOT/'app/api/control_plane.py').read_text(encoding='utf-8'))
ck('NEGATIVE_ROLLBACK_ADVICE_TYPE_FAIL_CLOSED', 'NEGATIVE_ROLLBACK_ADVICE_TYPE_FAIL_CLOSED_V1' in advisor and 'if not isinstance(advice, dict):' in advisor and 'if not isinstance(recommendations, dict):' in advisor)
ck('LOWER_ZERO_BID_NO_ACTIVATION', 'LOWER_ZERO_BID_NO_ACTIVATION_V1' in advisor and 'lower_no_active_bid' in advisor and 'changed_avito":False' in advisor)
ck('LOWER_MINBID_MONOTONIC_FAIL_CLOSED', 'LOWER_MINBID_MONOTONIC_FAIL_CLOSED_V1' in advisor and 'lower_provider_min_above_safe_target' in advisor)
ck('LOWER_FINAL_MONOTONIC_FENCE', 'LOWER_MONOTONIC_FINAL_FENCE_V1' in advisor and 'lower_not_strictly_monotonic' in advisor)
ck('NEGATIVE_ROLLBACK_ALREADY_COMPENSATED', 'NEGATIVE_ROLLBACK_ALREADY_COMPENSATED_V1' in advisor and 'if live_bid <= old_bid:' in advisor)
ck('NEGATIVE_ROLLBACK_RUNTIME_MONOTONIC_VERIFY', 'NEGATIVE_ROLLBACK_RUNTIME_MONOTONIC_VERIFY_V2' in advisor and 'negative_rollback_non_monotonic_receipt' in advisor)
ck('NEGATIVE_ROLLBACK_OWNED_RUNTIME', 'NEGATIVE_ROLLBACK_OWNED_CYCLE_V2' in advisor and 'run_negative_raise_rollback_cycle' in advisor and 'run_negative_raise_rollback_cycle' in (ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8') and 'negative_raise_rollback_runner.py' not in (ROOT/'ai_marketer_hourly.sh').read_text(encoding='utf-8'))
ck('WINNER_REPLAY_CURRENT_TRUTH_FENCE', 'WINNER_REPLAY_CURRENT_TRUTH_FENCE_V1' in advisor and 'winner_current_bid_changed' in advisor and 'winner_positive_feedback_required' in advisor and 'WINNER_REPLAY_SOURCE_FENCE_V1' in advisor)
ck('PROFITABLE_DAY_ADVICE_TYPE_FAIL_CLOSED', 'PROFITABLE_DAY_ADVICE_TYPE_FAIL_CLOSED_V1' in advisor and 'advisor_state_invalid' in advisor and 'advisor_recommendations_invalid' in advisor)
ck('ADVISOR_BUDGET_AUTHORIZATION_TRUTH', 'ADVISOR_BUDGET_AUTHORIZATION_TRUTH_V1' in advisor and 'daily_budget_authorized' in advisor and 'Подтвердите сохранённый суточный рекламный бюджет' in advisor and 'MONEY_BUDGET_OWNER_PROVENANCE_V1' in advisor)
ck('ENTITLEMENT_PRECEDES_CONFIG', 'MARKETER_ENTITLEMENT_PRECEDES_OWNER_CONFIG_V1' in roll and roll.index('entitled, entitlement = _rollout_marketer_entitlement') < roll.index('if budget<=0:'))
ck("CPX_ADVISOR_BID_AUTOPILOT_DISABLED_NO_RAISE", "CPX_ADVISOR_BID_AUTOPILOT_DISABLED_NO_RAISE_V1" in advisor and "_bid_autopilot_enabled = bool(kpi.get" in advisor and "and _bid_autopilot_enabled" in advisor and "Автоматическое управление ставками выключено владельцем" in advisor)
ck("CPX_ADVISOR_EARLY_AUTOPILOT_PAUSE_TRUTH", "CPX_ADVISOR_EARLY_AUTOPILOT_PAUSE_TRUTH_V1" in advisor and "_early_bid_autopilot" in advisor and "денежные параметры сейчас не требуются" in advisor)
ck("CPX_ADVISOR_MEASUREMENT_BACKLOG_NO_VISIBLE_RAISE", "CPX_ADVISOR_MEASUREMENT_BACKLOG_NO_VISIBLE_RAISE_V1" in advisor and "_raise_measurement_waiting" in advisor and "_raise_measurement_cap" in advisor and "Новые повышения ставок временно не формируются" in advisor)
ck("CPX_MEASURE_PROVIDER_QUERY_PRIORITIZES_WAITING", "CPX_MEASURE_PROVIDER_QUERY_PRIORITIZES_WAITING_V1" in advisor and "_waiting_probe_ids" in advisor and "_waiting_probe_ids + _active_item_ids_all" in advisor and "item_ids = _priority_ids[:200]" in advisor)
ck("CPX_MEASURE_EXACT_GETBIDS_TRUTH", "CPX_MEASURE_EXACT_GETBIDS_TRUTH_V1" in advisor and "cpx_advice_measure_exact_getbids_429" in advisor and "_bids_exact_item_ids" in advisor and 'getBids/{int(_mid)}' in advisor and '"exact_getbids_truth": True' in advisor)
ck("PROFITABLE_DAY_CANONICAL_MEASUREMENT_CAP", "PROFITABLE_DAY_CANONICAL_MEASUREMENT_CAP_V1" in advisor and "MEASUREMENT_BACKLOG_LIMIT = MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT" in advisor)
ck("LATE_DAY_PACING_SLOT_ROTATION", "LATE_DAY_PACING_SLOT_ROTATION_V1" in late_day and "_rotate_one_measurement_slot_for_pacing" in late_day and "inconclusive_pacing_slot_rotation" in late_day and "last_slot_rotation_day" in late_day and "measurement_clear_ids" in late_day)
ck("LATE_DAY_PACING_SLOT_ROTATION_FRESH_GUARD", "LATE_DAY_PACING_SLOT_ROTATION_FRESH_GUARD_V1" in late_day and "money_raise_signals_eligible(" in late_day and '"money_signal_not_fresh"' in late_day)
ck("ZERO_ACTIVE_INVENTORY_PRECEDES_OWNER_CONFIG", "MARKETER_ZERO_ACTIVE_INVENTORY_PRECEDES_OWNER_CONFIG_V1" in roll and roll.index("MARKETER_ZERO_ACTIVE_INVENTORY_PRECEDES_OWNER_CONFIG_V1") < roll.index("budget=float(k.get") and "'no_active_items'" in roll)
ck("PAUSED_BY_OWNER_NOT_INCIDENT", "MARKETER_PAUSED_BY_OWNER_NOT_INCIDENT_V1" in roll and "'paused' if _paused_by_owner else 'waiting' if _expected_wait else 'blocked'" in roll)
ck("EXPECTED_WAIT_NOT_BLOCKED", "MARKETER_EXPECTED_WAIT_NOT_BLOCKED_V1" in roll and "'no_active_items'" in roll and "'waiting' if _expected_wait" in roll)
ck('AUTOPILOT_MODE_PRECEDES_OWNER_MONEY_CONFIG', 'AUTOPILOT_MODE_PRECEDES_OWNER_MONEY_CONFIG_V1' in roll and
   roll.index("if not bool(k.get('bid_autopilot'))") < roll.index('budget=float') and
   "'bid_autopilot_disabled'" in roll and "'autonomous_mode_disabled'" in roll)
ck('PAUSED_BY_OWNER_RUNTIME_TRUTH', 'PAUSED_BY_OWNER:' in roll and '_paused_by_owner' in roll and
   'owner_action_required' in roll and 'не будет автоматически повышать ставки, пока владелец не включит автопилот' in roll)
ck('ZERO_BUDGET_EXISTING_SPEND_TRUTH', 'ZERO_BUDGET_EXISTING_SPEND_TRUTH_V1' in roll and "'budget_zero_existing_spend'" in roll and 'existing_placement_spend' in roll and 'boris_money_growth_blocked' in roll)

ck('STATIC_BLOCK_EXISTING_SPEND_HELPER', 'def _existing_spend_observation' in roll and 'primary_spend_type' in roll and 'spend_breakdown' in roll)
ck('TARGET_MISSING_EXISTING_SPEND_TRUTH', 'TARGET_MISSING_EXISTING_SPEND_TRUTH_V1' in roll and "'target_missing_existing_spend'" in roll and '_existing_spend_observation(db,a)' in roll)
ck('RUNTIME_ENTITLEMENT_TRUTH', 'MARKETER_RUNTIME_ENTITLEMENT_TRUTH_V1' in roll and "'paid_period_unknown'" in roll and "'paid_period_expired'" in roll and "_expected_runtime_status" in roll)
ck('TARGET_MISSING_OWNER_ACTION', "'target_missing': 'Задайте целевое количество лидов в день" in roll)
ck('STATIC_MONEY_BLOCK_PRECEDENCE', 'MARKETER_STATIC_MONEY_BLOCK_PRECEDENCE_V1' in roll and roll.index('MARKETER_STATIC_MONEY_BLOCK_PRECEDENCE_V1') < roll.index('MARKETER_GROWTH_SIGNAL_MAX_AGE_15M_V1'))
ck('PREFLIGHT_REASON_PRECEDENCE', 'MARKETER_BUDGET_BLOCK_PRECEDENCE_V1' in roll and 'MARKETER_SPEND_DEGRADED_REASON_TRUTH_V1' in roll and "'spend_provider_degraded'" in roll and "'spend_signal_unverified'" in roll)
ck('FINAL_RAISE_OWNER_PROVENANCE', 'FINAL_RAISE_OWNER_PROVENANCE_V1' in advisor and 'blocked_daily_budget_owner_provenance_missing' in advisor)
ck('AUTONOMOUS_RAISE_FINAL_MODE_FENCE', 'AUTONOMOUS_RAISE_FINAL_MODE_FENCE_V1' in advisor and
   'blocked_bid_autopilot_disabled' in advisor and 'blocked_autonomous_mode_disabled' in advisor and
   'if body.action == "raise" and _actor_auto:' in advisor and '_autopilot_allows(body.account_id)' in advisor)
ck('PAID_KPI_AUTOPILOT_MODE_RECONCILE', 'PAID_KPI_AUTOPILOT_MODE_RECONCILE_V1' in advisor and
   "confirmation_version='autopause_v1'" in advisor and "ARRAY['cpx.lower_bid']" in advisor and
   'autonomous_marketing_mode_disabled' in advisor)
ck('NEGATIVE_ROLLBACK_TENANT_ISOLATION', 'NEGATIVE_ROLLBACK_TENANT_ISOLATION_V1' in advisor and 'candidate_discovery' in advisor and 'continue' in advisor[advisor.index('NEGATIVE_ROLLBACK_TENANT_ISOLATION_V1'):advisor.index('NEGATIVE_ROLLBACK_RUNTIME_MONOTONIC_VERIFY_V2')])
money_policy_src=(ROOT/'app/services/marketing_money_policy.py').read_text(encoding='utf-8')
resume_src=(ROOT/'cpx_budget_resume.py').read_text(encoding='utf-8')
ck('PRESENCE_BUDGET_PRESSURE_POLICY', 'PRESENCE_BUDGET_PRESSURE_V1' in money_policy_src and
   'def presence_budget_pressure' in money_policy_src and 'presence_spend_exceeds_daily_budget_history' in money_policy_src)
ck('PRESENCE_BUDGET_GROWTH_SELF_HEAL', 'PRESENCE_BUDGET_GROWTH_SELF_HEAL_V1' in money_policy_src and
   'def reconcile_presence_budget_growth_policy' in money_policy_src and 'additive_growth_frozen' in money_policy_src and
   'owner_or_other_policy_change_preserved' in money_policy_src and 'frozen_policy_fingerprint' in money_policy_src)
ck('PRESENCE_BUDGET_OWNER_ESCALATION', 'PRESENCE_BUDGET_OWNER_ESCALATION_V1' in money_policy_src and
   'PRESENCE_BUDGET_OWNER_HISTORY_TRUTH_V1' in money_policy_src and
   '"owner_action_required": bool(_owner_action)' in money_policy_src and 'MARKETER_PRESENCE_OWNER_ESCALATION_V1' in roll and
   "'presence_budget_pressure'" in roll and 'automatic_self_heal' in roll)
ck('CPX_ADVISOR_PRESENCE_BLOCK_OWNER_VISIBILITY', 'CPX_ADVISOR_PRESENCE_BLOCK_OWNER_VISIBILITY_V1' in advisor and
   '_presence_owner_action_required' in advisor and '_brake_owner_action_required = bool(' in advisor)
ck('CPX_ADVISOR_PRESENCE_RUNTIME_FRESHEST', 'CPX_ADVISOR_PRESENCE_RUNTIME_FRESHEST_V1' in advisor and
   '"presence_budget_pressure_runtime"' in advisor and '_dedicated_at >= _nested_at' in advisor and
   '_brake_pressure = _presence_runtime' in advisor)
ck('PRESENCE_BUDGET_FINAL_RAISE_GUARD', 'PRESENCE_BUDGET_FINAL_RAISE_GUARD_V1' in advisor and
   'blocked_presence_budget_pressure' in advisor and '_presence_budget_pressure(_db_spend, account_id, float(limit))' in advisor)
ck('PRESENCE_BUDGET_RESUME_GUARD', 'PRESENCE_BUDGET_RESUME_GUARD_V1' in resume_src and
   'return False, "presence_budget_pressure"' in resume_src and 'presence_budget_pressure(db, account_id, float(limit))' in resume_src)
ck('PRESENCE_BUDGET_GROWTH_SELF_HEAL_WIRE', 'PRESENCE_BUDGET_GROWTH_SELF_HEAL_WIRE_V1' in budget_brake and
   'reconcile_presence_budget_growth_policy' in budget_brake and 'presence_budget_pressure' in budget_brake)
ck('PRESENCE_BUDGET_DRY_RUN_NO_MUTATION', 'PRESENCE_BUDGET_DRY_RUN_NO_MUTATION_V1' in money_policy_src and
   'mutate: bool = True' in money_policy_src and 'if mutate:' in money_policy_src and
   'mutate=bool(apply)' in budget_brake and 'would_freeze_additive_growth' in money_policy_src)
failed=[x for x in checks if not x[1]]
print('QA_MARKETER_ROLLOUT=', 'PASS' if not failed else 'FAIL','checks=',len(checks),'failed=',len(failed))
raise SystemExit(1 if failed else 0)
