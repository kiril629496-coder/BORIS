#!/root/BORIS/backend/venv/bin/python
"""BORIS money-policy self-heal guard.
Read/DB-only safety daemon: no Avito mutation, no external paid API calls.
Canonical invariant: every autonomous raise has explicit positive daily budget,
absolute bid cap <=150 RUB, step<=10%, run<=5/day<=120; launch is 1/run<=24/day.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
from app.db.session import SessionLocal

ROOT=Path('/root/BORIS/backend')
MONEY_SQL=ROOT/'sql/marketer_money_mandate_guard_v1.sql'
KPI_SQL=ROOT/'sql/marketer_kpi_money_reconcile_v1.sql'
A2A_SQL=ROOT/'sql/a2a_business_terminal_guard_v1.sql'

def emit(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)

def apply_sql(path):
    db=SessionLocal()
    try:
        raw=db.connection().connection; cur=raw.cursor(); cur.execute(path.read_text()); raw.commit()
    finally: db.close()

def live_def(name):
    db=SessionLocal()
    try:
        return str(db.execute(text("select pg_get_functiondef(CAST(:n AS regproc))"),{'n':name}).scalar() or '')
    finally: db.close()

def _control_plane_cap(db):
    row=db.execute(text("""select desired_state_json->>'hard_max_bid_rub'
      from control_requirements where key='system.marketing.absolute_autonomous_bid_cap' and status='active' limit 1""")).fetchone()
    if not row:
        raise RuntimeError('CONTROL_PLANE_MONEY_CAP_MISSING')
    try: cap=float(row[0] or 0)
    except Exception: cap=0
    if cap<=0: raise RuntimeError('CONTROL_PLANE_MONEY_CAP_INVALID')
    return cap

def main():
    repairs=[]
    probe=SessionLocal()
    try: safety_cap=_control_plane_cap(probe)
    finally: probe.close()
    # DDL is repaired before touching rows so row reconciliation is guarded by V4.
    g=live_def('boris_guard_money_mandate_v1')
    if ('CONTROL_PLANE_SAFETY_CAP' not in g or 'daily budget' not in g or 'BORIS_MONEY_PAID_UNTIL_HARD_STOP_DB_V1' not in g):
        apply_sql(MONEY_SQL); repairs.append('money_guard_ddl')
    k=live_def('boris_reconcile_money_from_kpi_v1')
    if ('CONTROL_PLANE_SAFETY_CAP' not in k or "status='revoked'" not in k):
        apply_sql(KPI_SQL); repairs.append('kpi_reconcile_ddl')
    # A2A_BUSINESS_TERMINAL_DB_SELF_HEAL_V1: a stale long-lived producer must
    # still be stopped at the database boundary. Reinstall the guard if a DB
    # restore/migration ever drops it. This adds no work and no money authority.
    a2a=live_def('boris_guard_a2a_business_terminal_v1')
    if ('BORIS_A2A_GUARD' not in a2a or 'business_goal_already_confirmed' not in a2a):
        apply_sql(A2A_SQL); repairs.append('a2a_business_terminal_guard_ddl')

    db=SessionLocal()
    try:
        # Latest KPI row per account is authoritative. Clamp persisted cap only downward.
        rows=db.execute(text("""select s.id,s.account_id,s.value from storage s
          join (select account_id,max(id) id from storage where key='kpi_settings' group by account_id) x on x.id=s.id""")).fetchall()
        cfg={}
        for rid,aid,raw in rows:
            try:d=json.loads(raw or '{}')
            except Exception:d={}
            budget=float(d.get('daily_budget_limit_rub') or 0)
            cap=float(d.get('hard_max_bid_rub') or safety_cap)
            if cap<=0 or cap>safety_cap:
                d['hard_max_bid_rub']=float(safety_cap)
                db.execute(text('update storage set value=:v where id=:i'),{'v':json.dumps(d,ensure_ascii=False),'i':rid})
                repairs.append(f'kpi_cap_control_plane:{aid}')
                cap=float(safety_cap)
            cfg[str(aid)]={'budget':max(0.0,budget),'cap':min(float(safety_cap),max(0.0,cap) or float(safety_cap))}
        db.flush()

        # MONEY_MANDATE_EXPIRED_SELF_HEAL_V1: expired rows must not remain
        # logically active forever. This only reduces authority; it never calls
        # a provider and never creates/re-enables spend permission.
        expired=db.execute(text("""select id from money_mandates
          where status='active' and revoked_at is null and valid_until is not null and valid_until<=now()""")).scalars().all()
        for mid in expired:
            db.execute(text("""update money_mandates set status='revoked',revoked_at=now(),version=version+1
              where id=:i and status='active' and revoked_at is null and valid_until<=now()"""),{'i':mid})
            repairs.append(f'revoke_expired:{mid}')

        mandates=db.execute(text("""select id,account_scope,source,daily_budget_rub,max_actions_run,max_actions_day,max_bid_delta_pct,max_bid_rub,valid_until
          from money_mandates where status='active' and revoked_at is null and 'cpx.raise_bid'=any(allowed_operations)""")).mappings().all()
        for m in mandates:
            scope=list(m['account_scope'] or [])
            # MONEY_MANDATE_PAID_PERIOD_RECONCILE_V1: existing automatic paid
            # authority is reconciled every minute, not only on INSERT/UPDATE.
            # Unknown/expired paid period => revoke. Future valid_until is only
            # clamped downward to paid_until. No authority is ever created here.
            if str(m['source'] or '') in ('paid_tariff_kpi','new_feed_launch'):
                paid_ends=[]; entitlement_ok=True
                for aid in scope:
                    br=db.execute(text("select value::jsonb from storage where account_id=:a and key='billing' order by id desc limit 1"),{'a':aid}).scalar()
                    pr=db.execute(text("select value::jsonb from storage where account_id=:a and key='payment_status' order by id desc limit 1"),{'a':aid}).scalar()
                    paid_until=None
                    try:
                        if br and br.get('paid_until'):
                            paid_until=db.execute(text("select cast(:v as timestamptz)"),{'v':str(br.get('paid_until'))}).scalar()
                        elif pr and pr.get('paid_at') and int(pr.get('period_days') or 0)>0:
                            paid_until=db.execute(text("select cast(:v as timestamptz)+make_interval(days=>:d)"),{'v':str(pr.get('paid_at')),'d':int(pr.get('period_days'))}).scalar()
                    except Exception:
                        paid_until=None
                    now=db.execute(text('select now()')).scalar()
                    if paid_until is None or paid_until<=now:
                        entitlement_ok=False; break
                    paid_ends.append(paid_until)
                if not entitlement_ok or not paid_ends:
                    db.execute(text("update money_mandates set status='revoked',revoked_at=now(),version=version+1 where id=:i and status='active' and revoked_at is null"),{'i':m['id']})
                    repairs.append(f'revoke_inactive_paid_period:{m["id"]}')
                    continue
                hard_end=min(paid_ends)
                if m['valid_until'] is None or m['valid_until']>hard_end:
                    db.execute(text("update money_mandates set valid_until=:u,version=version+1 where id=:i"),{'u':hard_end,'i':m['id']})
                    repairs.append(f'clamp_paid_until:{m["id"]}')
            if not scope:
                db.execute(text("update money_mandates set status='revoked',revoked_at=now(),version=version+1 where id=:i"),{'i':m['id']})
                repairs.append(f'revoke_unscoped:{m["id"]}')
                continue
            budgets=[cfg.get(str(a),{'budget':0})['budget'] for a in scope]
            caps=[cfg.get(str(a),{'cap':safety_cap})['cap'] for a in scope]
            budget=min(budgets) if budgets else 0
            cap=min(caps+[float(safety_cap)])
            if budget<=0:
                db.execute(text("update money_mandates set status='revoked',revoked_at=now(),version=version+1 where id=:i"),{'i':m['id']})
                repairs.append(f'revoke_zero_budget:{m["id"]}')
                continue
            run=1 if str(m['source'] or '')=='new_feed_launch' else min(5,max(1,int(m['max_actions_run'] or 5)))
            day=min(24,max(1,int(m['max_actions_day'] or 24))) if str(m['source'] or '')=='new_feed_launch' else min(120,max(1,int(m['max_actions_day'] or 120)))
            step=min(10,max(1,int(m['max_bid_delta_pct'] or 10)))
            current_budget=float(m['daily_budget_rub'] or 0)
            current_cap=float(m['max_bid_rub'] or 0)
            target_budget=min(budget,current_budget) if current_budget>0 else budget
            target_cap=min(cap,current_cap) if current_cap>0 else cap
            if (current_budget<=0 or current_budget>budget or current_cap<=0 or current_cap>cap or
                int(m['max_actions_run'] or 0)!=run or int(m['max_actions_day'] or 0)!=day or
                int(m['max_bid_delta_pct'] or 0)!=step):
                db.execute(text("""update money_mandates set daily_budget_rub=:b,max_bid_rub=:c,max_bid_delta_pct=:s,
                  max_actions_run=:r,max_actions_day=:d,version=version+1 where id=:i"""),
                  {'b':target_budget,'c':target_cap,'s':step,'r':run,'d':day,'i':m['id']})
                repairs.append(f'mandate_reconcile:{m["id"]}')
        db.commit()

        badcaps=[]
        for aid,d in cfg.items():
            if d['cap']>safety_cap: badcaps.append(aid)
        zero_raise=db.execute(text("""select count(*) from money_mandates m where m.status='active' and m.revoked_at is null
          and ('cpx.raise_bid'=any(m.allowed_operations) or 'raise_bid'=any(m.allowed_operations)) and exists(select 1 from unnest(m.account_scope) a
          join storage s on s.id=(select max(id) from storage where account_id=a and key='kpi_settings')
          where coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0)""")).scalar() or 0
        overcap=db.execute(text("""select count(*) from money_mandates where status='active' and revoked_at is null
          and ('cpx.raise_bid'=any(allowed_operations) or 'raise_bid'=any(allowed_operations)) and coalesce(max_bid_rub,999999)>:cap"""),{'cap':safety_cap}).scalar() or 0
        emit(f"MONEY_POLICY_GUARD=PASS control_plane_cap={safety_cap:g} repairs={repairs} zero_budget_raise={zero_raise} overcap_mandates={overcap} bad_caps={badcaps}")
        return 0 if int(zero_raise)==0 and int(overcap)==0 and not badcaps else 2
    finally: db.close()

if __name__=='__main__':
    raise SystemExit(main())
