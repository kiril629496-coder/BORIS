#!/usr/bin/env python3
"""Frequent, bounded first-bid watcher for newly published BORIS Feed Factory ads.
Runs independently of the hourly KPI cycle, but uses the same canonical bootstrap/apply_one guards.
One first activation per eligible account per run; zero budget/freshness failures remain fail-closed.
"""
from __future__ import annotations
import sys, json, fcntl
sys.path.insert(0,'/root/BORIS/backend')
from kpi_goal_runner import get_goal_auto_accounts
from app.api.cpx_advisor import bootstrap_new_no_promo, hourly_new_feed_low_views_ramp
LOCK='/root/BORIS/backend/run/new_ad_bid_watch.lock'
GLOBAL_MUTATION_CAP=3

def main():
    lock=open(LOCK,'w')
    try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('NEW_AD_BID_WATCH_SKIP overlap',flush=True); return 0
    failures=[]
    try:
        accounts=[x['account_id'] for x in get_goal_auto_accounts() if float(x.get('target_leads_per_day') or 0)>0]
        print('NEW_AD_BID_WATCH accounts=',len(accounts),flush=True)
        mutations=0
        for a in accounts:
            if mutations >= GLOBAL_MUTATION_CAP:
                print(a,'SKIP global_mutation_cap',GLOBAL_MUTATION_CAP,flush=True)
                continue
            try:
                # NEW_AD_WATCH_SINGLE_MONEY_STEP_V2: one money mutation per
                # account per 5-minute watchdog pass. Prefer first activation;
                # when it is not needed, advance one low-view new ad by one
                # incremental ramp step. Global + DB caps remain authoritative.
                r=bootstrap_new_no_promo(a,max_items=1) or {}
                print(a,'bootstrap',json.dumps(r,ensure_ascii=False,default=str)[:1400],flush=True)
                if r.get('changed_avito'):
                    mutations += 1
                elif r.get('status') not in ('error','external_wait') and mutations < GLOBAL_MUTATION_CAP:
                    ramp=hourly_new_feed_low_views_ramp(a,max_items=1) or {}
                    print(a,'ramp',json.dumps(ramp,ensure_ascii=False,default=str)[:1800],flush=True)
                    if ramp.get('changed_avito'):
                        mutations += 1
                    if ramp.get('status')=='error':
                        failures.append(a)
                if r.get('status')=='error':
                    failures.append(a)
            except Exception as e:
                failures.append(a); print(a,'ERROR',type(e).__name__,str(e)[:240],flush=True)
    finally: lock.close()
    print('NEW_AD_BID_WATCH_DONE mutations=',mutations,'failures=',failures,flush=True)
    return 1 if failures else 0
if __name__=='__main__': raise SystemExit(main())
