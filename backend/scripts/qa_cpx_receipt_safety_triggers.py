from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal
from sqlalchemy import text

def main():
    db=SessionLocal()
    try:
        rows=dict(db.execute(text("select tgname,pg_get_triggerdef(oid) from pg_trigger where tgrelid='cpx_execution_receipts'::regclass and not tgisinternal")).all())
        fn1=db.execute(text("select pg_get_functiondef('boris_cpx_receipt_raise_authority_guard()'::regprocedure)")).scalar() or ''
        fn2=db.execute(text("select pg_get_functiondef('boris_cpx_receipt_atomic_capacity_guard()'::regprocedure)")).scalar() or ''
        checks={
          'authority_trigger':'trg_cpx_receipt_raise_authority_guard' in rows,
          'capacity_trigger':'trg_cpx_receipt_atomic_action_cap' in rows,
          'strict_paid_authority':'paid_tariff_kpi' in fn1 and "NEW.source" in fn1,
          'launch_source_isolation':'new_feed_launch' in fn2 and "feed20" in fn2,
          'atomic_account_lock':'pg_advisory_xact_lock' in fn2 and 'cpx-action-cap:' in fn2,
          'run_cap':'blocked_max_actions_run_atomic' in fn2 and 'run_count' in fn2,
          'day_cap':'blocked_max_actions_day_atomic' in fn2 and 'day_count' in fn2,
          'global_caps':'LEAST' in fn2 and '120' in fn2,
          'moscow_money_day':'CPX_MOSCOW_MONEY_DAY_V1' in fn2 and "Europe/Moscow" in fn2,
          'run_scope_v3':'CPX_ATOMIC_RUN_SCOPE_V3' in fn2 and "new_feed_hourly_ramp" in fn2 and "budget_resume" in fn2 and "split_part(COALESCE(NEW.request_key,''),':',2)" in fn2,
          'budget_resume_hourly_scope':"IN ('new_feed_hourly_ramp', 'budget_resume')" in fn2 or "IN ('new_feed_hourly_ramp','budget_resume')" in fn2,
          'feed20_exactly_one_hourly_scope':'CPX_FEED20_EXACTLY_ONE_PER_HOUR_V1' in fn2 and "run_cap := LEAST(run_cap, 1)" in fn2 and "^f20v3_[0-9]{8}T[0-9]{2}" in fn2,
        }
        for k,v in checks.items(): print(('PASS' if v else 'FAIL'),k)
        failed=[k for k,v in checks.items() if not v]
        print('QA_CPX_RECEIPT_SAFETY_TRIGGERS=', 'PASS' if not failed else 'FAIL', 'checks=',len(checks),'failed=',len(failed))
        raise SystemExit(1 if failed else 0)
    finally: db.close()
if __name__=='__main__': main()
