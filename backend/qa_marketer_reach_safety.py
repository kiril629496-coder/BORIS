#!/usr/bin/env python3
import json
from pathlib import Path
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.autonomy import load_mandate

EVG='evgeniy_peregorodki_12447'
checks=[]
def ck(name, ok, detail=''):
    checks.append((name,bool(ok),str(detail)))
    print(('PASS' if ok else 'FAIL'),name,detail)

db=SessionLocal()
try:
    # No positive spend authority on any explicitly zero-budget account.
    rows=db.execute(text("select account_id,value from storage where key='kpi_settings'")).fetchall()
    zero=[]
    for a,v in rows:
        try: d=json.loads(v or '{}')
        except: continue
        if 'daily_budget_limit_rub' in d and float(d.get('daily_budget_limit_rub') or 0)<=0:
            zero.append(str(a))
    bad=[]
    for a in zero:
        n=db.execute(text("""select count(*) from money_mandates where :a=any(account_scope)
          and status='active' and revoked_at is null and source='paid_tariff_kpi'
          and 'cpx.raise_bid'=any(allowed_operations) and (valid_until is null or valid_until>now())"""),{'a':a}).scalar()
        if int(n or 0)>0: bad.append((a,int(n)))
    ck('ZERO_BUDGET_HAS_NO_KPI_RAISE',not bad,bad)

    # Feed launch is paid too: same explicit budget + hard money guard, only tighter action envelope.
    bad_launch=[]
    launches=db.execute(text("""select unnest(account_scope) a,id,daily_budget_rub,max_actions_run,max_actions_day,max_bid_delta_pct,max_bid_rub
      from money_mandates where source='new_feed_launch' and status='active' and revoked_at is null""")).mappings().all()
    for r in launches:
        kr=db.execute(text("select value from storage where account_id=:a and key='kpi_settings' order by id desc limit 1"),{'a':r['a']}).fetchone()
        try:
            k=json.loads(kr[0] or '{}') if kr else {}; budget=float(k.get('daily_budget_limit_rub') or 0); cap=min(float(k.get('hard_max_bid_rub') or 150),150)
        except: budget=0; cap=150
        # First activation is paid too: explicit account budget is mandatory.
        # Launch differs only by a smaller action envelope (1/run, <=24/day).
        ok=(budget>0 and float(r['daily_budget_rub'] or 0)>0 and float(r['daily_budget_rub'] or 0)<=budget
            and int(r['max_actions_run'] or 0)==1 and 0<int(r['max_actions_day'] or 0)<=24
            and 0<int(r['max_bid_delta_pct'] or 0)<=10 and 0<float(r['max_bid_rub'] or 0)<=cap)
        if not ok: bad_launch.append(dict(r)|{'kpi_budget':budget,'kpi_cap':cap})
    ck('FIRST_BID_EXPLICIT_BUDGET_BOUNDED',not bad_launch,bad_launch)

    normal=load_mandate(db,EVG,'cpx.raise_bid',source='reach_rescue')
    launch=load_mandate(db,EVG,'cpx.raise_bid',source='feed20')
    active_paid=db.execute(text("""select count(*) from money_mandates where :a=any(account_scope) and source='paid_tariff_kpi' and status='active' and revoked_at is null and 'cpx.raise_bid'=any(allowed_operations) and (valid_until is null or valid_until>now())"""),{'a':EVG}).scalar()
    active_launch=db.execute(text("""select count(*) from money_mandates where :a=any(account_scope) and source='new_feed_launch' and status='active' and revoked_at is null and 'cpx.raise_bid'=any(allowed_operations) and (valid_until is null or valid_until>now())"""),{'a':EVG}).scalar()
    normal_ok=(normal is None if int(active_paid or 0)==0 and int(active_launch or 0)==0 else bool(normal and normal.get('source')=='paid_tariff_kpi'))
    if int(active_launch or 0)>0:
        launch_ok=bool(launch and launch.get('source')=='new_feed_launch')
    elif int(active_paid or 0)>0:
        launch_ok=bool(launch and launch.get('source')=='paid_tariff_kpi')
    else:
        launch_ok=(launch is None)
    ck('MANDATE_SOURCE_ISOLATION',normal_ok and launch_ok,{'normal':normal.get('source') if normal else None,'launch':launch.get('source') if launch else None,'active_paid':int(active_paid or 0),'active_launch':int(active_launch or 0)})

    started=[str(r[0]) for r in db.execute(text("select distinct object_name from action_log where account_id=:a and source='reach_rescue' and ts>=now()-interval '24 hours'"),{'a':EVG}).fetchall()]
    ck('EVGENIY_REACH_COHORT_BOUNDED',len(started)<=5,started)
finally:
    db.close()

runner=Path('cpx_advisor_runner.py').read_text()
auto=Path('app/services/autonomy.py').read_text()
intra=Path('app/services/intraday.py').read_text()
hourly=Path('ai_marketer_hourly.sh').read_text()
cpx=Path('app/api/cpx_advisor.py').read_text()
avito=Path('app/api/avito.py').read_text()
_republish=avito.split('def republish_apply',1)[1].split('class KpiSettingsRequest',1)[0]
_kpi_check=avito.split('def kpi_check',1)[1].split('@router.',1)[0]
_kpi_legacy=avito.split('def kpi_autopilot_run',1)[1].split('@router.get("/kpi_check")',1)[0]
ck('PER_ITEM_REQUEST_ID', 'f"{_rid(acc)}:{int(it[\'id\'])}"' in runner)
ck('RUN_PREFIX_CAP', '_request_run_prefix' in auto and "request_id LIKE :pat" in auto)
ck('REACH_RESCUE_BOUNDED_SOURCE', 'REACH_RESCUE_MAX_ACTIVE = 5' in intra and 'REACH_RESCUE_MAX_ACTIONS = 3' in intra and 'REACH_RESCUE_COHORT_DURABLE_V1' in intra)
ck('AVITO_EFFECTIVE_MIN_BID', 'AVITO_EFFECTIVE_MIN_BID_V1' in cpx and 'float(x.get("compare") or 0) > 0' in cpx)
ck('GLOBAL_MONEY_FREEZE_REMAINS', 'cpx_advisor_runner.py || true # EMERGENCY_MONEY_FREEZE' in hourly)
ck('UNIFIED_STAGED_ALL_ACCOUNTS', 'UNIFIED_STAGED_ALL_MARKETER_ACCOUNTS_V1' in hourly and '--apply --account evgeniy_peregorodki_12447' not in hourly)
ck('FIRST_BID_FULL_MONEY_GUARD', 'NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3' in cpx and 'MONEY_GUARD_ALL_RAISES_V3' in auto and 'LAUNCH_FIRST_BID_ONLY' not in auto)
ck('DB_GUARD_V4_NO_LAUNCH_BYPASS', 'BORIS_MARKETER_MONEY_MANDATE_DB_GUARD_V5_MAX150' in Path('sql/marketer_money_mandate_guard_v1.sql').read_text())
ck('KPI_DB_IMMEDIATE_RECONCILE', 'BORIS_KPI_MONEY_RECONCILE_DB_V2_MAX150' in Path('sql/marketer_kpi_money_reconcile_v1.sql').read_text())
ck('CPX_APPLY_LANE_GUARD', 'BLOCKED_UNTRUSTED_MONEY_LANE' in runner and 'raise SystemExit(main())' in runner)
ck('DIRECT_LOWVIEWS_LANE_GUARD', 'ACCOUNT_LOW_VIEWS_DIRECT_LANE_GUARD_V3' in cpx and 'controlled_lowviews_v2' in cpx)
ck('CANONICAL_GLOBAL_HARD_CAP_150', 'GLOBAL_HARD_MAX_BID_RUB = 150.0' in Path('app/services/marketing_money_policy.py').read_text())
ck('REACH_MEASURE_WINDOW_DAILY' , 'REACH_RESCUE_MIN_MEASURE_MINUTES = 1440' in intra)
# OWNER_GROWTH_NO_SHRINK_CONTRACT_V1: every canonical hourly QA pass detects
# reintroduction of the legacy "expire first, maybe replace later" KPI behavior.
ck('KPI_RECOVERY_NEVER_SHRINKS',
   'OWNER_GROWTH_NO_SHRINK_V1' in _republish
   and 'it["date_end"] =' not in _republish
   and '"removed": 0' in _republish
   and 'no_safe_additive_source' in _republish)
ck('LEGACY_KPI_AUTOPILOT_CANONICAL_ONLY',
   'KPI_LEGACY_AUTOPILOT_CANONICAL_DELEGATE_V1' in _kpi_legacy
   and 'canonical = kpi_goal_tick(account_id)' in _kpi_legacy)
ck('KPI_GAP_POLICY_ADDITIVE',
   'canonical_goal_recovery' in _kpi_check
   and 'не уменьшая активный рекламный пул' in _kpi_check
   and 'BORIS сохраняет действующие объявления' in _kpi_check
   and 'проверить и снять неэффективные' not in _kpi_check
   and 'а не увеличивать количество' not in _kpi_check)
_kpi_utc_day_aliases = (
    '_date_kc.today()', '_date_krc.today()', '_date_kbl.today()',
    '_date_kbr.today()', '_date_kbc.today()', '_date_kbrl.today()',
    '_date_kbs.today()', '_date_kair.today()', '_date_kaic.today()',
    '_date_kairl.today()', '_date_kes.today()', '_date_inv.today()',
    '_date_identity.today()', '_date_krg.today()',
    '_date_title_cohort.today()', '_date_kd.today()', '_date_amw.today()',
)
ck('KPI_MOSCOW_BUSINESS_DAY_CLOCK',
   'KPI_MOSCOW_DAY_CLOCK_V1' in avito
   and all(token not in avito for token in _kpi_utc_day_aliases)
   and 'return marketing_today()' in avito)
_invexp = avito.split('def _kpi_exec_expand_inventory',1)[1].split('def _kpi_executor_registry',1)[0]
_goal_inventory = avito.split('def kpi_goal_tick',1)[1].split('@router.get("/kpi_goal_state")',1)[0]
ck('KPI_INVENTORY_EXPANSION_PREPARE_WIRED',
   'KPI_INVENTORY_EXPANSION_PREPARE_V1' in _invexp
   and 'avito.publish_feed_additive' in _invexp
   and 'feed_row.value' not in _invexp
   and 'KPI_GOAL_INVENTORY_PREPARE_WIRE_V1' in _goal_inventory
   and '"stage": "inventory_expand_prepare"' in _goal_inventory)


# STRICT_STEP10_SOURCE_INVARIANT_V1
# Fail closed until every autonomous source uses <=10% per actual bid write.
_policy_src=Path('app/services/marketing_money_policy.py').read_text()
_late_src=Path('late_day_spend_controller.py').read_text()
_cpx_src=Path('app/api/cpx_advisor.py').read_text()
_auto_src=Path('app/services/autonomy.py').read_text()
ck('STRICT_STEP10_POLICY_NO_SOURCE_EXPANSION', 'LATE_DAY_CATCHUP_MAX_BID_DELTA_PCT = 50' not in _policy_src)
ck('STRICT_STEP10_LATE_DAY_CONTROLLER', 'step_pct=int(target_boost)' not in _late_src)
ck('STRICT_STEP10_CPX_NO_DIRECT_EXCEPTION', 'OWNER_LATE_DAY_DIRECT_30_50_V1' not in _cpx_src)
ck('STRICT_STEP10_AUTONOMY_NO_SOURCE_EXCEPTION', 'OWNER_LATE_DAY_DIRECT_30_50_V1' not in _auto_src)

failed=[x for x in checks if not x[1]]
print('QA_MARKETER_REACH_SAFETY=', 'PASS' if not failed else 'FAIL', 'checks=',len(checks),'failed=',len(failed))
raise SystemExit(1 if failed else 0)
