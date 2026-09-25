#!/usr/bin/env python3
import argparse, datetime as dt, json, os, shlex, subprocess, sys, time
from pathlib import Path
ROOT=Path('/root/BORIS'); B=ROOT/'backend'; STATE=B/'data'/'guardian_state.json'; OUT=B/'images'/'guardian'
STATE.parent.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
for line in (B/'.env').read_text(errors='ignore').splitlines():
    if line and not line.lstrip().startswith('#') and '=' in line:
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
sys.path.insert(0,str(B))
from app.telegram_bot import send_telegram_message
from app.db.session import SessionLocal
from app.services.reliability import system_health
from sqlalchemy import text
import requests
CHAT='-1003952038222'; THREAD=4; WEB='http://127.0.0.1:3002'; WEB_HEALTH=WEB; API='http://127.0.0.1:8000'; PUBLIC_HEALTH='https://boris-ai.pro/health'
API_REPLICAS=[('API-1','boris-backend.service','http://127.0.0.1:8000'),('API-2','boris-backend-replica.service','http://127.0.0.1:8001')]
CORE=['boris-background-worker.service','boris-frontend.service','boris-dispatcher.service','boris-watchdog.service','boris-crm-sync.service']
DOMAIN=[('МОП',['boris-inbox.service']),('РОП',['boris-report.timer']),('ИИ-маркетолог',['boris-ai-marketer.timer']),('CRM',['boris-crm-sync.service']),('Автовосстановление',['boris-dispatcher.service','boris-watchdog-check.timer'])]
ROUTES=['/dashboard/home','/messages','/dashboard/campaign','/dashboard/analytics','/dashboard/crm','/ai','/rop','/dashboard/marketing','/social','/dashboard/billing','/dashboard/sitebuild','/dashboard/company','/dashboard/reliability']
VIEWS=[('ПК',1440,900),('Планшет',1024,768),('Мобильный',390,844)]
MARKETER_DB_MITIGATION_PATH=Path('/etc/systemd/system/boris-ai-marketer.service.d/40-db-session-mitigation.conf')
MARKETER_DB_MITIGATION_TEXT='''[Service]\n# TEMP_MARKETER_DB_IDLE_TX_MITIGATION_V1\n# Scoped P0 mitigation until MARKETER_ROLLOUT_DB_SESSION_BOUNDARY_V1 is deployed.\nEnvironment="PGOPTIONS=-c idle_in_transaction_session_timeout=10min"\n'''
def sh(cmd, timeout=25):
    try:
        p=subprocess.run(cmd,shell=True,text=True,capture_output=True,timeout=timeout)
        return p.returncode,(p.stdout+p.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        out=((exc.stdout or '') if isinstance(exc.stdout,str) else '') + ((exc.stderr or '') if isinstance(exc.stderr,str) else '')
        return 124,(out + f'\nTIMEOUT after {timeout}s: {cmd}').strip()
def active(u):
    rc,o=sh('systemctl is-active '+u); return rc==0 and o.splitlines()[-1:]==['active']
def enabled(u):
    rc,o=sh('systemctl is-enabled '+u); return rc==0 and 'enabled' in o

def intentionally_condition_gated(u):
    """True when systemd intentionally skipped a service behind a failed Condition* gate.

    A runtime safety/audit gate owns the service in this state. Guardian must not
    fight that gate or report a process crash when systemd itself reports a clean
    result with ConditionResult=no.
    """
    rc,o=sh('systemctl show '+shlex.quote(u)+' -p ConditionResult -p Result', timeout=5)
    if rc != 0:
        return False
    fields={}
    for line in str(o or '').splitlines():
        if '=' in line:
            k,v=line.split('=',1); fields[k.strip()]=v.strip()
    return fields.get('ConditionResult')=='no' and fields.get('Result') in {'success',''}

def load():
    try:return json.loads(STATE.read_text())
    except:return {}
def save(s): STATE.write_text(json.dumps(s,ensure_ascii=False,indent=2))
def tg(text): return send_telegram_message(CHAT,text[:3900],thread_id=THREAD)
def _unique_owner_alert_items(items):
    """Stable text dedupe for owner-facing warning/error bullets."""
    return list(dict.fromkeys(str(x) for x in (items or []) if str(x).strip()))


def _owner_warning_transition(state, warnings, threshold=3):
    """Deterministic state transition for owner-facing yellow warnings.

    Internal marketer retries are never owner actions. A new warning must be
    observed in ``threshold`` consecutive Guardian checks before one send; a
    stable warning sends once, and its recovery also sends at most once.
    """
    owner_warnings=_unique_owner_alert_items(
        x for x in (warnings or [])
        if not str(x).startswith('ИИ-маркетолог: поклиентский retry требуется:')
    )
    warning_sig='|'.join(sorted(owner_warnings))
    warning_prev=str(state.get('last_warning_sig') or '')
    if owner_warnings:
        if warning_sig==warning_prev:
            state['pending_warning_sig']=''; state['pending_warning_count']=0
            return {'action':'steady','warnings':owner_warnings,'sig':warning_sig,'previous_sig':warning_prev,'count':0}
        pending=str(state.get('pending_warning_sig') or '')
        count=int(state.get('pending_warning_count',0) or 0)+1 if pending==warning_sig else 1
        state['pending_warning_sig']=warning_sig; state['pending_warning_count']=count
        if count>=max(1,int(threshold or 3)):
            state['last_warning_sig']=warning_sig
            state['pending_warning_sig']=''; state['pending_warning_count']=0
            return {'action':'send','warnings':owner_warnings,'sig':warning_sig,'previous_sig':warning_prev,'count':count}
        return {'action':'pending','warnings':owner_warnings,'sig':warning_sig,'previous_sig':warning_prev,'count':count}
    state['pending_warning_sig']=''; state['pending_warning_count']=0
    if warning_prev:
        state['last_warning_sig']=''
        return {'action':'recovery','warnings':[],'sig':'','previous_sig':warning_prev,'count':0}
    return {'action':'none','warnings':[],'sig':'','previous_sig':'','count':0}

def heal(u,s):
    now=time.time(); key='heal:'+u
    if now-float(s.get(key,0) or 0)<1800:return False
    s[key]=now; sh('systemctl restart '+u); time.sleep(2); return active(u)

def canonical_backend_rollout_active():
    """True only while the canonical HA rolling script is actually running.

    Guardian must never race an intentional drain/restart performed by the deploy
    owner. Otherwise a health tick can restart the same replica in the middle of
    nginx fencing and turn a safe single-replica rollout into a two-replica gap.
    """
    rc,out=sh("pgrep -af '/root/BORIS/backend/rolling_restart_backend.sh'",timeout=5)
    return rc==0 and 'rolling_restart_backend.sh' in str(out or '')
def backend_api_memory_guard():
    """Detect regression of the API memory envelope without restarting APIs.

    A too-low MemoryHigh previously throttled a healthy Python process until both
    replicas became effectively unavailable.  The persistent systemd contract is
    2 GiB soft / 2.5 GiB hard plus MALLOC_ARENA_MAX=2 to bound glibc arena
    retention.  Guardian only diagnoses here; canonical deploy remains restart owner.
    """
    bad=[]; warnings=[]
    min_high=2*1024*1024*1024
    for name,unit,_url in API_REPLICAS:
        rc_h,raw_h=sh(f"systemctl show {unit} -p MemoryHigh --value",timeout=5)
        rc_e,env=sh(f"systemctl show {unit} -p Environment --value",timeout=5)
        rc_c,raw_c=sh(f"systemctl show {unit} -p MemoryCurrent --value",timeout=5)
        try: high=int(str(raw_h).strip())
        except Exception: high=0
        try: current=int(str(raw_c).strip())
        except Exception: current=0
        if rc_h!=0 or high<=0:
            warnings.append(f"{name}: не удалось прочитать MemoryHigh")
            continue
        if high < min_high:
            bad.append(f"{name}: MemoryHigh ниже безопасных 2 ГБ ({high} bytes)")
        if rc_e!=0 or "MALLOC_ARENA_MAX=2" not in str(env or ""):
            bad.append(f"{name}: отсутствует MALLOC_ARENA_MAX=2")
        if rc_c==0 and current>0 and current >= int(high*0.95):
            warnings.append(f"{name}: память близка к soft-limit ({current}/{high})")
    return {"bad":bad,"warnings":warnings}

def platform_health():
    db=SessionLocal()
    try:return system_health(db)
    except Exception as e:return {'state':'critical','error':type(e).__name__}
    finally:db.close()
def bid_cap_health():
    """Read-only fleet invariant from the authoritative hourly reconciler state.

    Guardian never changes money here. The AI-marketer performs the actual
    lowering; Guardian only detects drift/stale execution so a broken scheduler
    cannot silently leave expensive bids running.
    """
    db=SessionLocal()
    try:
        rows=db.execute(text("""
            SELECT DISTINCT ON (s.account_id) s.account_id,s.value
              FROM storage s
              JOIN accounts a ON a.account_id=s.account_id
             WHERE s.key='cpx_cap_reconcile_runtime'
               AND a.avito_client_id IS NOT NULL
               AND a.avito_client_secret IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','marketing','background')
               )
             ORDER BY s.account_id,s.id DESC
        """)).fetchall()
        configured_rows=db.execute(text("""
            SELECT DISTINCT a.account_id
              FROM accounts a
             WHERE a.avito_client_id IS NOT NULL
               AND a.avito_client_secret IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','marketing','background')
               )
             ORDER BY a.account_id
        """)).fetchall()
        configured_ids=[str(r[0]) for r in configured_rows]
        configured=len(configured_ids)
        bad=[]; hard_bad=[]; repair_accounts=set(); seen=set(); newest=None; stale_accounts=[]
        now=dt.datetime.now(dt.timezone.utc)

        # FUNDING_PAUSE_BID_CAP_STALE_SUPPRESSION_V1:
        # A confirmed external wallet pause suppresses only the stale-timestamp
        # alarm. It never suppresses a real cap violation, failed status,
        # remaining over-cap bid, or auto-promotion residue.
        funding_paused=set()
        try:
            pause_rows=db.execute(text("""
                SELECT DISTINCT ON (account_id) account_id,value
                  FROM storage
                 WHERE key='guardian_balance_funding_health'
                 ORDER BY account_id,id DESC
            """)).fetchall()
            for _aid,_raw in pause_rows:
                try:_p=json.loads(_raw or '{}')
                except Exception:continue
                if (
                    bool(_p.get('work_pause_required'))
                    and bool(_p.get('owner_action_required'))
                    and str(_p.get('work_pause_reason') or '').startswith('avito_wallet_')
                ):
                    funding_paused.add(str(_aid))
        except Exception:
            funding_paused=set()

        for account_id,raw in rows:
            account_id=str(account_id); seen.add(account_id)
            try:data=json.loads(raw or '{}')
            except Exception:
                msg='Не удалось прочитать данные проверки ставок по одному аккаунту'
                bad.append(msg); hard_bad.append(msg); repair_accounts.add(account_id); continue
            status=str(data.get('status') or '')
            remaining=int(data.get('remaining_over_cap') or 0)
            auto_remaining=int(data.get('auto_promotions_remaining') or 0)
            if status in {'fail','error','blocked','needs_retry'} or remaining>0 or auto_remaining>0:
                msg='По одному аккаунту не завершена корректировка ставок'
                bad.append(msg); hard_bad.append(msg); repair_accounts.add(account_id)
            stamp=data.get('finished_at') or data.get('started_at')
            if stamp:
                try:
                    t=dt.datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
                    if t.tzinfo is None:t=t.replace(tzinfo=dt.timezone.utc)
                    newest=t if newest is None or t>newest else newest
                    age_minutes=int(max(0,(now-t).total_seconds())//60)
                    if age_minutes>120 and account_id not in funding_paused:
                        stale_accounts.append((account_id,age_minutes)); repair_accounts.add(account_id)
                except Exception:
                    bad.append('По одному аккаунту повреждено время последней проверки ставок'); repair_accounts.add(account_id)
            else:
                bad.append('По одному аккаунту нет времени последней проверки ставок'); repair_accounts.add(account_id)
        missing_accounts=sorted(set(configured_ids)-seen)
        missing=len(missing_accounts)
        repair_accounts.update(missing_accounts)
        stale_minutes=None if newest is None else int(max(0,(now-newest).total_seconds())//60)
        if missing>0: bad.append(f'Не запущена проверка ставок по {missing} аккаунтам')
        for account_id,age_minutes in stale_accounts[:5]:
            bad.append(f'Проверка ставок по одному аккаунту не обновлялась {age_minutes} мин')
        if len(stale_accounts)>5:
            bad.append(f'Ещё аккаунтов без свежей проверки ставок: {len(stale_accounts)-5}')
        return {'configured':int(configured),'seen':len(seen),'stale_minutes':stale_minutes,
                'stale_accounts':len(stale_accounts),'missing_accounts':missing_accounts,
                'repair_accounts':sorted(repair_accounts),'hard_bad':hard_bad,
                'funding_paused_stale_suppressed':len(funding_paused),
                'bad':bad}
    except Exception as e:
        return {'configured':0,'seen':0,'stale_minutes':None,'missing_accounts':[],
                'repair_accounts':[],'hard_bad':[f'health query: {type(e).__name__}'],
                'bad':[f'health query: {type(e).__name__}']}
    finally: db.close()


def heal_bid_cap_accounts(accounts, state, max_accounts=2):
    """Bounded ownerless lower-only recovery for missing/stale cap runtime.

    The canonical reconciler has its own singleton flock and can only lower or
    remove promotion; it never increases spend. Guardian therefore may repair a
    small number of stale/missing tenants when the hourly marketer is not active.
    """
    results=[]; now=time.time(); attempted=0
    for account_id in list(dict.fromkeys(str(x) for x in (accounts or []) if x)):
        key='bid_cap_heal:'+account_id
        if now-float(state.get(key,0) or 0)<300:
            results.append({'account_id':account_id,'status':'backoff'}); continue
        if attempted>=max(1,int(max_accounts)): break
        attempted+=1; state[key]=now
        try:
            p=subprocess.run(
                [str(B/'venv/bin/python'), str(B/'cpx_cap_reconciler.py'), '--apply', '--account', account_id],
                cwd=str(B), text=True, capture_output=True, timeout=120,
            )
            out=(p.stdout or '')+(p.stderr or '')
            results.append({
                'account_id':account_id,
                'status':'attempted' if p.returncode==0 else 'error',
                'returncode':int(p.returncode),
                'overlap':'CPX_CAP_RECONCILE_SKIP overlap' in out,
            })
        except subprocess.TimeoutExpired:
            results.append({'account_id':account_id,'status':'timeout'})
        except Exception as exc:
            results.append({'account_id':account_id,'status':'error','error_type':type(exc).__name__})
    return results


def bid_cap_recovery_mode(cap_health, marketer_cycle_running: bool) -> str:
    if not (cap_health or {}).get('bad'):
        return 'ok'
    if (cap_health or {}).get('hard_bad'):
        return 'critical'
    if marketer_cycle_running:
        return 'wait_hourly'
    if (cap_health or {}).get('repair_accounts'):
        return 'repair'
    return 'critical'


def budget_brake_health():
    """Read-only per-account daily-budget safety state."""
    from app.services.marketing_money_policy import effective_daily_budget_limit
    db=SessionLocal()
    try:
        accounts=[str(r[0]) for r in db.execute(text("""
            SELECT a.account_id FROM accounts a
             WHERE a.avito_client_id IS NOT NULL AND a.avito_client_secret IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','marketing','background')
               )
             ORDER BY a.account_id
        """)).fetchall()]
        limited=[]; limits={}
        for account_id in accounts:
            try:
                limit=effective_daily_budget_limit(db,account_id)
                if limit:
                    limited.append(account_id); limits[account_id]=float(limit)
            except Exception:
                pass
        rows=db.execute(text("""
            SELECT DISTINCT ON (s.account_id) s.account_id,s.value
              FROM storage s
             WHERE s.key='cpx_budget_brake_runtime'
             ORDER BY s.account_id,s.id DESC
        """)).fetchall()
        runtime={str(a):raw for a,raw in rows if str(a) in set(limited)}
        now=dt.datetime.now(dt.timezone.utc); bad=[]; near=[]; warnings=[]; repair_accounts=[]; owner_actions=[]
        for account_id in limited:
            expected_limit=float(limits.get(account_id) or 0)
            raw=runtime.get(account_id)
            if raw is None:
                bad.append(f'{account_id}: нет budget-brake runtime'); repair_accounts.append(account_id); continue
            try:data=json.loads(raw or '{}')
            except Exception:
                bad.append(f'{account_id}: budget runtime JSON повреждён'); repair_accounts.append(account_id); continue
            status=str(data.get('status') or '')
            used=data.get('budget_used_pct')
            after=data.get('paid_promotions_after')
            try:
                runtime_limit=float(data.get('daily_budget_limit_rub')) if data.get('daily_budget_limit_rub') is not None else None
            except Exception:
                runtime_limit=None
            # BUDGET_POLICY_CHANGE_SELF_HEAL_V1: a new/changed positive owner
            # limit must not wait for the next hourly marketer cycle. The saved
            # brake evidence is stale immediately when it describes another
            # limit, even if its checked_at timestamp is recent.
            if runtime_limit is None or abs(runtime_limit-expected_limit)>0.01:
                bad.append(f'{account_id}: budget runtime limit drift {runtime_limit}->{expected_limit}')
                repair_accounts.append(account_id)
            if status=='near_limit':
                near.append(f'{account_id}:{used}%')
            if status in {'fail','error','blocked','needs_retry','would_brake'}:
                bad.append(f'{account_id}: budget status={status}, used={used}%, paid_after={after}')
                repair_accounts.append(account_id)
            # BUDGET_DEFERRED_RETRY_GUARD_V1: a provider 429/transport wait
            # blocks fresh verification. The retry is non-destructive: current
            # paid delivery stays untouched while new money growth remains frozen.
            if status=='deferred':
                warnings.append(f'{account_id}: budget brake deferred')
                repair_accounts.append(account_id)
            if status in {'spend_unknown','spend_stale'}:
                # BUDGET_STALE_SIGNAL_AUTOHEAL_V2:
                # Stale/unknown spend blocks NEW raises but never authorizes
                # disabling existing CPX. Refresh stats, recompute the growth
                # guard and keep existing paid delivery running.
                warnings.append(f'{account_id}: {status}')
                bad.append(f'{account_id}: budget spend signal={status}, требуется свежая проверка')
                repair_accounts.append(account_id)
            # KPI_CONTINUITY_BUDGET_GUARDIAN_V1: historical 'braked' is a
            # legacy incident state, never a desired post-condition. Recompute it
            # through the non-destructive guard. Current healthy cutoff state is
            # growth_frozen_existing_preserved and paid promotion may remain live.
            if status=='braked':
                warnings.append(f'{account_id}: найден старый destructive budget-brake runtime')
                repair_accounts.append(account_id)
            if status=='growth_frozen_existing_preserved':
                near.append(f'{account_id}:{used}% growth frozen; delivery preserved')
            # PRESENCE_BUDGET_OWNER_DECISION_V1: after all safe automatic
            # reductions are exhausted (CPX=0, additive growth frozen), a
            # repeated placement/presence overspend cannot be self-healed
            # without taking active client ads offline or changing the owner's
            # budget. Surface exactly that external policy decision once.
            presence_rt=data.get('presence_budget_pressure') if isinstance(data.get('presence_budget_pressure'),dict) else {}
            pressure=presence_rt.get('pressure') if isinstance(presence_rt.get('pressure'),dict) else {}
            try:
                _presence_today=float(((pressure.get('today') or {}).get('presence_rub')) or 0)
                _completed_over=int(pressure.get('completed_over_budget_days') or 0)
                _latest_ratio=float(pressure.get('latest_completed_presence_ratio') or 0)
            except Exception:
                _presence_today=0.0; _completed_over=0; _latest_ratio=0.0
            _structural_presence=bool(
                pressure.get('blocked')
                and (_completed_over>=2 or _latest_ratio>=1.25)
                and expected_limit>0
                and _presence_today>=expected_limit
            )
            # Structural placement/presence pressure is independent from CPX.
            # Do not require CPX to be zero before surfacing the external funding/
            # placement conflict; disabling client traffic is not a self-heal.
            _growth_frozen=bool(not bool(presence_rt.get('inventory_growth_enabled')))
            if _structural_presence and _growth_frozen:
                owner_actions.append({
                    'account_id':account_id,
                    'daily_budget_limit_rub':round(expected_limit,2),
                    'presence_today_rub':round(_presence_today,2),
                    'completed_over_budget_days':_completed_over,
                    'latest_completed_presence_ratio':round(_latest_ratio,4),
                    'reason':'presence_cost_exceeds_owner_daily_budget_after_safe_brake',
                })
            stamp=data.get('checked_at')
            if stamp:
                try:
                    t=dt.datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
                    if t.tzinfo is None:t=t.replace(tzinfo=dt.timezone.utc)
                    age=int(max(0,(now-t).total_seconds())//60)
                    # BUDGET_GROWTH_FREEZE_REVERIFY_V1: there is no provider
                    # post-condition to "drive to zero" anymore. A frozen-growth
                    # runtime only needs reasonably fresh spend evidence; 15 min
                    # aligns with the canonical money-signal freshness boundary.
                    try:
                        _used_num=float(used) if used is not None else None
                    except Exception:
                        _used_num=None
                    max_age_min = 15 if (
                        status=='growth_frozen_existing_preserved'
                        and _used_num is not None
                    ) else 120
                    if age>max_age_min:
                        bad.append(f'{account_id}: budget-check устарел на {age} мин')
                        repair_accounts.append(account_id)
                except Exception:
                    bad.append(f'{account_id}: budget timestamp повреждён'); repair_accounts.append(account_id)
            else:
                bad.append(f'{account_id}: нет времени budget-check'); repair_accounts.append(account_id)
        return {'configured':len(limited),'seen':len(runtime),'near':near,'warnings':warnings,'bad':bad,
                'repair_accounts':sorted(set(repair_accounts)),'owner_actions':owner_actions}
    except Exception as e:
        return {'configured':0,'seen':0,'near':[],'warnings':[],'bad':[f'budget health query: {type(e).__name__}'],'repair_accounts':[],'owner_actions':[]}
    finally: db.close()


def heal_budget_brake_accounts(accounts, state, max_accounts=3):
    """Bounded ownerless recovery for stale/mismatched daily-budget evidence.

    Read fresh Avito stats first, then recompute the non-destructive budget
    growth guard. Existing paid promotion is never removed here. Provider/data
    failure blocks new growth and is retried on a later Guardian cycle.
    """
    results=[]
    now=time.time()
    attempted=0
    # BUDGET_HEAL_BACKOFF_NO_STARVATION_V1:
    # Backoff tenants do not consume the bounded attempt budget. Otherwise the
    # first three alphabetic accounts can permanently starve every later client
    # from fresh-spend recovery while all three simply return "backoff".
    for account_id in list(dict.fromkeys(str(x) for x in (accounts or []) if x)):
        key='budget_heal:'+account_id
        if now-float(state.get(key,0) or 0)<300:
            results.append({'account_id':account_id,'status':'backoff'}); continue
        if attempted >= max(1,int(max_accounts)):
            break
        attempted += 1
        state[key]=now
        try:
            from daily_stats_collector import _collect_one
            from cpx_budget_brake import brake_account
            stats=_collect_one(account_id,attempts=2) or {}
            db=SessionLocal()
            try:
                brake=brake_account(db,account_id,apply=True) or {}
            finally:
                db.close()
            results.append({'account_id':account_id,'status':'attempted',
                            'stats_status':stats.get('status'),'brake_status':brake.get('status'),
                            'paid_promotions_after':brake.get('paid_promotions_after')})
        except Exception as exc:
            results.append({'account_id':account_id,'status':'error','error_type':type(exc).__name__})
    return results



def legacy_budget_incident_health():
    """Detect durable exact snapshots left by the old destructive budget brake."""
    from app.models.storage import Storage
    from zoneinfo import ZoneInfo
    today=dt.datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
    pending_states={'braked','resume_retry','provider_accepted','verify_pending'}
    db=SessionLocal()
    try:
        rows=db.execute(text("""
            SELECT DISTINCT ON (account_id) account_id,value
              FROM storage
             WHERE key='cpx_budget_resume_state'
             ORDER BY account_id,id DESC
        """)).fetchall()
        details=[]
        for account_id,raw in rows:
            try:data=json.loads(raw or '{}')
            except Exception:continue
            if str(data.get('brake_reason') or '')!='daily_budget_cutoff':continue
            brake_day=str(data.get('brake_day_msk') or '')
            if not brake_day or brake_day>=today:continue
            items=data.get('items') if isinstance(data.get('items'),dict) else {}
            pending=[]
            for key,item in items.items():
                if not isinstance(item,dict):continue
                if str(item.get('status') or '') not in pending_states:continue
                if str(item.get('original_mode') or '')!='manual':continue
                if str(item.get('restore_target_source') or '')!='exact_pre_brake_bid':continue
                try:
                    iid=int(item.get('item_id') or key); bid=int(item.get('bid_penny') or 0)
                except Exception:continue
                if iid>0 and bid>0:pending.append(iid)
            if pending:
                details.append({'account_id':str(account_id),'brake_day_msk':brake_day,'pending_exact':len(pending)})
        return {'ok':True,'accounts':[x['account_id'] for x in details],'details':details,'pending_exact_total':sum(x['pending_exact'] for x in details)}
    except Exception as exc:
        return {'ok':False,'accounts':[],'details':[],'pending_exact_total':0,'error':type(exc).__name__}
    finally:db.close()


def heal_legacy_budget_incidents(accounts,state,max_accounts=1):
    """Bounded ownerless exact compensation for incidents created by old code."""
    results=[]; attempted=0; now=time.time()
    for account_id in list(dict.fromkeys(str(x) for x in (accounts or []) if x)):
        key='legacy_budget_incident_heal:'+account_id
        if now-float(state.get(key,0) or 0)<240:
            results.append({'account_id':account_id,'status':'backoff'}); continue
        if attempted>=max(1,int(max_accounts)):break
        attempted+=1; state[key]=now
        cmd=("cd /root/BORIS/backend && ./venv/bin/python run/cpx_exact_incident_recovery.py "
             "--apply --limit 20 --account "+shlex.quote(account_id))
        rc,out=sh(cmd,timeout=90)
        parsed={}
        for line in reversed(str(out or '').splitlines()):
            try:
                parsed=json.loads(line); break
            except Exception:continue
        status=str(parsed.get('status') or ('error' if rc else 'unknown'))
        results.append({
            'account_id':account_id,'status':status,'returncode':rc,
            'verified':int(parsed.get('verified') or 0),
            'reconciled_exact_state':int(parsed.get('reconciled_exact_state') or 0),
            'retry_after_seconds':int(parsed.get('retry_after_seconds') or 0),
            'error':str(parsed.get('error') or '')[:180] or None,
        })
    return results


def wallet_capacity_health():
    """Detect an external funding blocker without changing money.

    BALANCE_FUNDING_OWNER_ESCALATION_V1: when an active paid marketer is below
    KPI and the real Avito wallet cannot fund even one lead at the configured
    red CPL, BORIS cannot self-heal that external wallet. Persist the diagnosis
    and escalate once; normal money guards remain authoritative.
    """
    from app.models.storage import Storage
    from app.services.control_plane_adapters_ext import marketing_service_entitlement
    from app.services.marketing_clock import marketing_today_iso
    db=SessionLocal(); out=[]
    try:
        accounts=[str(r[0]) for r in db.execute(text("""
            SELECT a.account_id FROM accounts a
             WHERE a.avito_client_id IS NOT NULL AND a.avito_client_secret IS NOT NULL
             ORDER BY a.account_id
        """)).fetchall()]
        for account_id in accounts:
            ent=marketing_service_entitlement(db,account_id) or {}
            if ent.get('state')!='active': continue
            krow=db.execute(text("select value from storage where account_id=:a and key='kpi_settings' order by id desc limit 1"),{'a':account_id}).fetchone()
            try:kpi=json.loads(krow[0] or '{}') if krow else {}
            except Exception:kpi={}
            try:target=float(kpi.get('target_leads_per_day') or 0); red=float(kpi.get('max_cost_per_lead_rub') or 0); budget=float(kpi.get('daily_budget_limit_rub') or 0)
            except Exception:target=red=budget=0
            if target<=0 or red<=0: continue
            day=marketing_today_iso()
            row=db.execute(text("select value from storage where account_id=:a and key=:k order by id desc limit 1"),{'a':account_id,'k':'daily_stats:'+day}).fetchone()
            try:snap=json.loads(row[0] or '{}') if row else {}
            except Exception:snap={}
            bal=(snap.get('balance') or {}) if isinstance(snap,dict) else {}; items=snap.get('items') or [] if isinstance(snap,dict) else []
            try:real=float(bal.get('real'))
            except Exception:real=None
            contacts=sum(int((x or {}).get('contacts') or 0) for x in items if isinstance(x,dict) and x.get('status')=='active')
            # ZERO_BUDGET_EXTERNAL_AUTHORITY_V1:
            # A zero daily BORIS ad budget is not an internal repair task and it
            # does not mean Avito has zero presence spend. Free diagnosis/content
            # may continue, but money growth requires an explicit business limit.
            # Persist this as an external owner boundary so the money journal can
            # report it truthfully without mutating its immutable guard module.
            if budget<=0:
                _budget_owner_action=bool(contacts < target)
                data={'version':4,'account_id':account_id,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                      'service_active':True,'daily_budget_limit_rub':budget,'max_cpl_rub':red,
                      'target_leads_per_day':target,'contacts_today':contacts,
                      'real_balance_rub':round(real,2) if real is not None else None,
                      'owner_action_required':_budget_owner_action,
                      'reason':'daily_budget_not_authorized' if _budget_owner_action else None,
                      'work_pause_required':False,'work_pause_reason':None,
                      'money_growth_pause_required':_budget_owner_action,
                      'resume_condition':'owner_sets_positive_daily_budget_with_authorization' if _budget_owner_action else None,
                      'funding_suppressed_by_money_guard':False,
                      'money_guard_status':'no_explicit_daily_budget',
                      'presence_budget_blocked':False}
                sr=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=='guardian_balance_funding_health').order_by(Storage.id.desc()).first()
                if sr: sr.value=json.dumps(data,ensure_ascii=False)
                else: db.add(Storage(account_id=account_id,key='guardian_balance_funding_health',value=json.dumps(data,ensure_ascii=False)))
                db.commit()
                continue
            if real is None: continue
            # BALANCE_FUNDING_RESPECTS_BUDGET_GUARD_V1: low wallet is not the
            # limiting factor when canonical money policy already says STOP.
            # In particular, structural paid-presence overspend must never be
            # followed by the opposite owner instruction to add more money.
            brow=db.execute(text("select value from storage where account_id=:a and key='cpx_budget_brake_runtime' order by id desc limit 1"),{'a':account_id}).fetchone()
            try:brake=json.loads(brow[0] or '{}') if brow else {}
            except Exception:brake={}
            presence_rt=brake.get('presence_budget_pressure') if isinstance(brake.get('presence_budget_pressure'),dict) else {}
            pressure=presence_rt.get('pressure') if isinstance(presence_rt.get('pressure'),dict) else {}
            money_guard_blocks_funding=bool(
                str(brake.get('status') or '')=='braked'
                or pressure.get('blocked')
                or presence_rt.get('owner_action_required')
            )
            low_wallet=bool(contacts < target and real < red)
            required=bool(low_wallet and not money_guard_blocks_funding)
            reason=('avito_balance_below_one_red_cpl' if required else
                    ('budget_or_presence_brake_precedes_funding' if low_wallet and money_guard_blocks_funding else None))
            # LOW_WALLET_GLOBAL_WORK_PAUSE_V1:
            # Funding notification and work permission are separate decisions.
            # A structural presence-budget guard may suppress the contradictory
            # instruction "top up now", but it must NOT grant permission to keep
            # generating/publishing/optimizing an account whose real wallet
            # cannot fund even one red-CPL lead. All KPI mutation lanes pause
            # until Guardian observes recovery (or the lead goal is already met).
            data={'version':3,'account_id':account_id,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                  'service_active':True,'daily_budget_limit_rub':budget,'max_cpl_rub':red,
                  'target_leads_per_day':target,'contacts_today':contacts,'real_balance_rub':round(real,2),
                  'owner_action_required':required,'reason':reason,
                  'work_pause_required':bool(low_wallet),
                  'work_pause_reason':'avito_wallet_below_one_red_cpl' if low_wallet else None,
                  'resume_condition':'real_balance_rub_gte_max_cpl_rub_or_goal_met' if low_wallet else None,
                  'funding_suppressed_by_money_guard':bool(low_wallet and money_guard_blocks_funding),
                  'money_guard_status':str(brake.get('status') or '') or None,
                  'presence_budget_blocked':bool(pressure.get('blocked'))}
            sr=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=='guardian_balance_funding_health').order_by(Storage.id.desc()).first()
            if sr: sr.value=json.dumps(data,ensure_ascii=False)
            else: db.add(Storage(account_id=account_id,key='guardian_balance_funding_health',value=json.dumps(data,ensure_ascii=False)))
            db.commit()
            if required: out.append(data)
        return out
    except Exception as exc:
        db.rollback(); return []
    finally: db.close()

def isolated_route_health():
    """Secret-free external Avito route status; warning-only, never core outage."""
    db=SessionLocal()
    try:
        provider={}
        row=db.execute(text("select value from storage where account_id='__browser_gateway__' and key='isolated_route_provider_health' order by id desc limit 1")).fetchone()
        if row:
            try: provider=json.loads(row[0])
            except Exception: provider={}
        # Count only the CURRENT state of each job. The storage table can contain
        # historical revisions, so scanning raw rows could resurrect already healed
        # blocked_network_route states and spam the owner with a fake blocker.
        blocked=0
        rows=db.execute(text("""
            select distinct on (account_id,key) value
              from storage
             where key like 'mass_editor_job:%'
             order by account_id,key,id desc
        """)).fetchall()
        for (raw,) in rows:
            try:j=json.loads(raw)
            except Exception:continue
            if str(j.get('executor') or j.get('executor_selected') or '')=='isolated_browser' and str(j.get('status') or '')=='blocked_network_route':
                blocked+=1
        return {'provider':provider,'blocked_jobs':blocked}
    except Exception as e:return {'provider':{'state':'unknown','error_type':type(e).__name__},'blocked_jobs':0}
    finally:db.close()
CLIENT_REPORT_ACCOUNTS=('prodazha_bytovok_25677','3411770_94346')
CLIENT_REPORT_CHAT='-1004425842869'

def client_report_scheduler_health(self_heal=True):
    """Pre-delivery health for the two active Bytovki client reports.

    Detects scheduler/route drift before the 20:30 Moscow send. Safe self-heal is
    limited to enabling cron and restoring thread_id=0 for an already canonical
    client chat. Missing/wrong recipient routes fail closed.
    """
    critical=[]; warnings=[]; healed=[]; route_state={}
    cron_active=active('cron')
    cron_enabled=enabled('cron')
    if self_heal and ((not cron_active) or (not cron_enabled)):
        rc,_=sh('systemctl enable --now cron')
        time.sleep(0.5)
        cron_active=active('cron')
        cron_enabled=enabled('cron')
        if rc==0 and cron_active and cron_enabled:
            healed.append('cron daemon')
    if not cron_active or not cron_enabled:
        critical.append('cron daemon остановлен/отключён')

    rc,cron_text=sh('crontab -l',timeout=10)
    lines=[
        line.strip() for line in str(cron_text or '').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    cron_line_ok=(
        rc==0 and any(
            line.startswith('30 17 * * *')
            and 'client_report.py' in line
            and '--send' in line
            and CLIENT_REPORT_ACCOUNTS[0] in line
            and CLIENT_REPORT_ACCOUNTS[1] in line
            for line in lines
        )
    )
    if not cron_line_ok:
        critical.append('нет канонической cron-команды ежедневных client_report по двум аккаунтам')

    db=SessionLocal()
    try:
        rows=db.execute(text("""
            SELECT DISTINCT ON (account_id) account_id,chat_id,thread_id
              FROM tg_routes
             WHERE thread_key='client_reports'
               AND account_id IN (:a1,:a2)
             ORDER BY account_id,id DESC
        """),{'a1':CLIENT_REPORT_ACCOUNTS[0],'a2':CLIENT_REPORT_ACCOUNTS[1]}).fetchall()
        found={str(r[0]):(str(r[1] or ''),int(r[2] or 0)) for r in rows}
        for account_id in CLIENT_REPORT_ACCOUNTS:
            route=found.get(account_id)
            if not route:
                critical.append(f'{account_id}: отсутствует client_reports route')
                route_state[account_id]={'state':'missing'}
                continue
            chat_id,thread_id=route
            route_state[account_id]={'chat_id':chat_id,'thread_id':thread_id}
            if chat_id!=CLIENT_REPORT_CHAT:
                critical.append(f'{account_id}: client_reports ведёт в неверный Telegram chat')
                continue
            if thread_id!=0:
                if self_heal:
                    res=db.execute(text("""
                        UPDATE tg_routes SET thread_id=0
                         WHERE account_id=:a AND chat_id=:c
                           AND thread_key='client_reports'
                           AND COALESCE(thread_id,0)<>0
                    """),{'a':account_id,'c':CLIENT_REPORT_CHAT})
                    db.commit()
                    check=db.execute(text("""
                        SELECT thread_id FROM tg_routes
                         WHERE account_id=:a AND chat_id=:c
                           AND thread_key='client_reports'
                         ORDER BY id DESC LIMIT 1
                    """),{'a':account_id,'c':CLIENT_REPORT_CHAT}).fetchone()
                    if int((check[0] if check else -1) or 0)==0 and int(getattr(res,'rowcount',0) or 0)>0:
                        healed.append(f'{account_id}: Telegram route thread_id=0')
                        route_state[account_id]['thread_id']=0
                    else:
                        critical.append(f'{account_id}: не удалось восстановить thread_id=0')
                else:
                    critical.append(f'{account_id}: client_reports thread_id drift={thread_id}')
    except Exception as exc:
        try: db.rollback()
        except Exception: pass
        critical.append('проверка tg_routes: '+type(exc).__name__)
    finally:
        db.close()

    return {
        'state':'critical' if critical else 'ok',
        'critical':critical,
        'warnings':warnings,
        'healed':healed,
        'cron_active':bool(cron_active),
        'cron_enabled':bool(cron_enabled),
        'cron_line_ok':bool(cron_line_ok),
        'routes':route_state,
    }


def email_outreach_runtime_health():
    """Runtime truth for active owner cold-email outreach.

    Static QA proves contracts; this proves the minute worker and today's
    delivery fact are actually alive. A recoverable within-window lag is only a
    warning. Critical means self-heal is exhausted or today's SLA can no longer
    be repaired automatically.
    """
    try:
        from app.services.email_queue import queue_stats
        from app.services.prospect_campaigns import owner_daily_delivery_health, owner_copy_integrity_health
        qs=queue_stats()
        db=SessionLocal()
        try:
            owner_ids=[int(r[0]) for r in db.execute(text("""SELECT DISTINCT owner_id
              FROM prospect_campaigns
              WHERE account_id='__owner_outreach__' AND status='active'
              ORDER BY owner_id""")).all()]
        finally:
            db.close()
        critical=[]; warnings=[]; owners=[]; integrity=[]; healed=[]
        if owner_ids and not bool(qs.get('worker_alive')):
            critical.append('минутный worker email-рассылки не обновляет heartbeat')
        for owner_id in owner_ids:
            # Content integrity is checked before plan/fact. This is the
            # DETECT -> SELF-HEAL -> VERIFY contour for the exact approved copy.
            ci=owner_copy_integrity_health(owner_id,self_heal=True)
            integrity.append(ci)
            healed.extend(str(x) for x in (ci.get('healed') or []))
            ci_critical=list(ci.get('critical') or [])
            if ci_critical:
                # Pending queued/sending drift remains fail-closed and can be
                # recovered by the queue/pre-SMTP guards. Missing DB locks,
                # persistent campaign drift, exceptions or an already escaped
                # wrong send mean automatic repair did not fully succeed.
                hard=(
                    not bool(ci.get('campaign_lock_ok',True))
                    or not bool(ci.get('queue_lock_ok',True))
                    or int(ci.get('campaign_copy_drift') or 0)>0
                    or int(ci.get('wrong_sent_after_lock') or 0)>0
                    or str(ci.get('status') or '')=='copy_integrity_exception'
                )
                if hard:
                    critical.append(
                        f"owner {owner_id}: целостность согласованного email-текста нарушена: "
                        + ",".join(ci_critical[:4])
                    )
                else:
                    warnings.append(
                        f"Email-текст owner {owner_id}: найдено безопасно блокируемое отклонение; "
                        "BORIS восстанавливает очередь автоматически"
                    )

            h=owner_daily_delivery_health(owner_id); owners.append(h)
            state=str(h.get('state') or '')
            status=str(h.get('status') or '')
            if state=='critical':
                # daily_cap_exceeded is already fail-closed by the canonical
                # pre-SMTP quota guard. When nothing remains queued and the
                # health contract says owner_action_required=False, failing the
                # quick guardian would only make the owner an operator for an
                # already-contained historical overshoot. Keep it visible as a
                # warning; future sending remains blocked until the next day.
                no_owner_action=not bool(h.get('owner_action_required'))
                if status=='window_closed_shortfall' and no_owner_action:
                    warnings.append(
                        f"Email owner {owner_id}: окно отправки закрыто, план {h.get('daily_cap',0)}, "
                        f"факт {h.get('successful',0)}, остаток {h.get('remaining',0)}; "
                        "BORIS продолжит автоматически в следующее разрешённое окно"
                    )
                elif (status=='daily_cap_exceeded'
                        and int(h.get('remaining') or 0)==0
                        and int(h.get('queued_now') or 0)==0
                        and no_owner_action):
                    warnings.append(
                        f"Email owner {owner_id}: дневной лимит ранее превышен; "
                        "дальнейшая отправка уже заблокирована до следующего дня"
                    )
                else:
                    critical.append(
                        f"owner {owner_id}: {status}; план {h.get('daily_cap',0)}, "
                        f"факт {h.get('successful',0)}, осталось {h.get('remaining',0)}"
                    )
            elif state=='degraded':
                if h.get('can_self_heal'):
                    warnings.append(
                        f"Email-рассылка догоняет план автоматически: "
                        f"ожидалось {h.get('expected_by_now',0)}, отправлено {h.get('successful',0)}"
                    )
                else:
                    critical.append(
                        f"owner {owner_id}: {status}; автоматическое догоняющее действие недоступно"
                    )
        return {'critical':critical,'warnings':warnings,'queue':qs,'owners':owners,
                'integrity':integrity,'healed':healed}
    except Exception as e:
        return {'critical':[f'email runtime health: {type(e).__name__}'],'warnings':[],'queue':{},'owners':[]}

def mop_training_contract_health():
    """Read-only smoke contract for the client-facing MOP training contour.

    No paid AI call and no client-memory mutation:
      - the live Next page must render the training UI + version marker;
      - the live FastAPI schema must expose every route required by the page.
    Source/runtime generation convergence is owned by boris-deploy.
    """
    bad = []
    details = {}
    try:
        r = requests.get(WEB + '/messages/mop-training', timeout=10)
        details['frontend_http'] = r.status_code
        body = r.text or ''
        if r.status_code >= 500:
            bad.append(f'страница тренировки МОП HTTP {r.status_code}')
        else:
            required_ui = (
                'Живая тренировка МОП',
                'Начать спарринг с МОП',
                'data-mop-training-contract="v2-single-post-timeout"',
            )
            missing = [x for x in required_ui if x not in body]
            if missing:
                bad.append('страница тренировки МОП обслуживает устаревшую/неполную сборку')
                details['frontend_missing'] = missing
    except Exception as exc:
        bad.append('страница тренировки МОП недоступна')
        details['frontend_error'] = type(exc).__name__

    expected_runtime_contract = 'v4-ai-provider-readiness'
    healed = []
    replicas = (
        ('main', 'http://127.0.0.1:8000', 'boris-backend.service'),
        ('replica', 'http://127.0.0.1:8001', 'boris-backend-replica.service'),
    )

    def _runtime_contract(base_url):
        try:
            rr = requests.get(
                base_url + '/api/mop-combat/runtime-contract',
                params={'account_id': 'mbroker_27859'},
                timeout=5,
            )
            payload = rr.json() if rr.status_code == 200 else {}
            contract = str((payload or {}).get('contract') or '')
            ai_readiness = dict((payload or {}).get('ai_readiness') or {})
            ai_ready = bool((payload or {}).get('ai_ready'))
            ok = (
                rr.status_code == 200
                and contract == expected_runtime_contract
                and bool((payload or {}).get('price_followup_guard'))
                and bool((payload or {}).get('manager_turn_counter'))
                and bool((payload or {}).get('ai_readiness_probe'))
                and ai_ready
            )
            return ok, rr.status_code, contract, ai_ready, ai_readiness
        except Exception as exc:
            return False, 0, type(exc).__name__, False, {'reason': type(exc).__name__}

    for replica_name, base_url, service_name in replicas:
        ok, http_status, contract, ai_ready, ai_readiness = _runtime_contract(base_url)
        details[f'{replica_name}_runtime_http'] = http_status
        details[f'{replica_name}_runtime_contract'] = contract
        details[f'{replica_name}_ai_ready'] = ai_ready
        details[f'{replica_name}_ai_readiness'] = ai_readiness
        if ok:
            continue

        # MOP_RUNTIME_CONVERGENCE_DELEGATED_TO_DEPLOY_V3:
        # Guardian never races the canonical rolling deploy. Code/version drift
        # is delegated to boris-deploy; provider exhaustion/pressure is surfaced
        # separately so a healthy HTTP 200 cannot hide a dead training service.
        details[f'{replica_name}_runtime_reconcile'] = 'delegated_to_boris_deploy'
        if contract != expected_runtime_contract:
            bad.append(
                f'{replica_name} backend тренировки МОП на старом/неполном runtime-контракте'
            )
        elif not ai_ready:
            bad.append(
                f'{replica_name} тренировка МОП без доступного AI: '
                + str((ai_readiness or {}).get('reason') or 'provider_unavailable')
            )
        else:
            bad.append(f'{replica_name} runtime тренировки МОП не прошёл проверку')

    try:
        r = requests.get(API + '/openapi.json', timeout=10)
        details['backend_http'] = r.status_code
        paths = (r.json() or {}).get('paths') or {} if r.status_code == 200 else {}
        required_paths = (
            '/api/mop-combat/runtime-contract',
            '/api/mop-combat/sparring/start',
            '/api/mop-combat/sparring/turn',
            '/api/mop-combat/sparring/feedback',
            '/api/mop-combat/sparring/learning',
            '/api/mop-combat/sparring/finish',
        )
        missing_paths = [p for p in required_paths if p not in paths]
        if r.status_code != 200 or missing_paths:
            bad.append('backend-контракт тренировки МОП неполный')
            details['backend_missing'] = missing_paths
    except Exception as exc:
        bad.append('backend-контракт тренировки МОП не проверен')
        details['backend_error'] = type(exc).__name__

    return {'bad': bad, 'details': details, 'healed': healed}


def _marketer_deterministic_fleet_abort():
    """Return a known internal fleet-abort signature from the last marketer cycle.

    This is read-only. It never retries or performs money actions. A DB safety
    refusal is expected locally; letting it escape and abort later tenants is not.
    """
    try:
        log=(B/'logs'/'ai_marketer_hourly.log')
        if not log.is_file():
            return None
        raw=log.read_text(encoding='utf-8',errors='replace')[-250000:]
        start=raw.rfind('===== AI_MARKETER_START ')
        if start >= 0:
            raw=raw[start:]
        if ('STAGE_FAIL marketer_rollout' in raw
                and 'blocked_first_bid_cross_source_hourly_atomic' in raw):
            return 'blocked_first_bid_cross_source_hourly_atomic'
        # MARKETER_DB_SESSION_FLEET_ABORT_V1: a long-lived coordinator read
        # transaction can be killed by PostgreSQL idle_in_transaction timeout
        # while provider/subprocess work is running. This is an internal fleet
        # abort, not an account-local business guard, so page immediately rather
        # than waiting for another hourly failure.
        if ('STAGE_FAIL marketer_rollout' in raw
                and ('SSL connection has been closed unexpectedly' in raw
                     or ('sqlalchemy.exc.OperationalError' in raw
                         and 'virtual_marketer_runtime' in raw))):
            return 'rollout_db_session_closed'
    except Exception:
        return None
    return None


def marketer_db_session_mitigation_health():
    """Check permanent source fix or scoped service mitigation; never touches money."""
    try:
        rollout=(B/'marketer_rollout_runner.py').read_text(encoding='utf-8',errors='replace')
    except Exception:
        rollout=''
    # MARKETER_DB_SESSION_PERMANENT_FIX_DETECT_V2: the permanent repair is a
    # three-part contract, not the obsolete single marker used by the temporary
    # mitigation. Require all three invariants so Guardian never mistakes a
    # partial/comment-only change for a production fix.
    _permanent_markers=(
        'MARKETER_DB_READ_RECONNECT_V2',
        'MARKETER_COORDINATOR_READ_TX_RELEASE_V1',
        'MARKETER_COORDINATOR_FRESH_SESSION_V2',
    )
    source_fixed=all(marker in rollout for marker in _permanent_markers)
    rc,env=sh('systemctl show boris-ai-marketer.service -p Environment --value',timeout=5)
    mitigation_active=(rc==0 and 'idle_in_transaction_session_timeout=10min' in str(env or ''))
    return {'source_fixed':source_fixed,'mitigation_active':mitigation_active,'environment_read_ok':rc==0}


def heal_marketer_db_session_mitigation(state):
    """Bounded self-heal for the known rollout DB-session fleet abort.

    This only installs a systemd environment override for the next canonical
    timer-owned marketer run. It does not start the service and cannot execute
    a provider or money mutation.
    """
    health=marketer_db_session_mitigation_health()
    if health.get('source_fixed'):
        return {'status':'source_fixed',**health}
    if health.get('mitigation_active'):
        return {'status':'already_active',**health}
    if _marketer_deterministic_fleet_abort()!='rollout_db_session_closed':
        return {'status':'not_triggered',**health}
    key='heal:marketer_db_session_mitigation'
    now=time.time()
    if now-float(state.get(key,0) or 0)<600:
        return {'status':'backoff',**health}
    state[key]=now
    try:
        MARKETER_DB_MITIGATION_PATH.parent.mkdir(parents=True,exist_ok=True)
        MARKETER_DB_MITIGATION_PATH.write_text(MARKETER_DB_MITIGATION_TEXT,encoding='utf-8')
    except Exception as exc:
        return {'status':'write_failed','error':type(exc).__name__,**health}
    rc,out=sh('systemctl daemon-reload',timeout=15)
    after=marketer_db_session_mitigation_health()
    if rc==0 and after.get('mitigation_active'):
        return {'status':'healed',**after}
    return {'status':'verify_failed','daemon_reload_rc':rc,'error':str(out or '')[-300:],**after}


def marketer_execution_health(state):
    """Classify the last hourly marketer execution with canonical timer retry.

    One failed execution is not immediately an owner incident when the hourly
    timer is enabled, active and has a next trigger. The timer is the single
    canonical retry owner (the service itself refuses manual starts). A second
    distinct failed execution without an intervening success becomes critical.
    """
    timer='boris-ai-marketer.timer'
    timer_active=active(timer)
    timer_enabled=enabled(timer)
    next_rc,next_out=sh("systemctl show boris-ai-marketer.timer -p NextElapseUSecRealtime --value")
    retry_ready=bool(timer_active and timer_enabled and next_rc==0 and str(next_out or '').strip())
    failed_rc,_=sh('systemctl is-failed boris-ai-marketer.service')
    deterministic_abort=_marketer_deterministic_fleet_abort()
    if failed_rc == 0 and deterministic_abort:
        if deterministic_abort == 'rollout_db_session_closed':
            _msg=('ИИ-маркетолог: внутренний DB-session fleet-abort — PostgreSQL закрыл '
                  'координаторную сессию во время rollout; следующие активные аккаунты могли не обработаться')
        else:
            _msg=('ИИ-маркетолог: внутренний fleet-abort — безопасный DB-отказ одного аккаунта '
                  'оборвал обработку следующих активных аккаунтов')
        return {
            'bad':[_msg],
            'warnings':[], 'retry_ready':retry_ready, 'failed':True,
            'consecutive':max(1,int(state.get('marketer_consecutive_failed_executions',0) or 0)),
            'failure_kind':deterministic_abort,
        }
    if failed_rc != 0:
        state['marketer_last_failed_event']=''
        state['marketer_consecutive_failed_executions']=0
        return {'bad':[], 'warnings':[], 'retry_ready':retry_ready, 'failed':False, 'consecutive':0}

    _,failure_id=sh("systemctl show boris-ai-marketer.service -p InactiveEnterTimestampMonotonic --value")
    failure_id=str(failure_id or '').strip() or 'unknown_failed_execution'
    previous=str(state.get('marketer_last_failed_event') or '')
    consecutive=int(state.get('marketer_consecutive_failed_executions',0) or 0)
    if failure_id != previous:
        consecutive=(consecutive+1) if previous else 1
        state['marketer_last_failed_event']=failure_id
        state['marketer_consecutive_failed_executions']=consecutive

    if consecutive >= 2:
        return {
            'bad':['ИИ-маркетолог: два часовых execution подряд завершились ошибкой'],
            'warnings':[], 'retry_ready':retry_ready, 'failed':True,
            'consecutive':consecutive, 'failure_id':failure_id,
        }
    if not retry_ready:
        return {
            'bad':['ИИ-маркетолог: execution завершился ошибкой, а канонический hourly retry не запланирован'],
            'warnings':[], 'retry_ready':False, 'failed':True,
            'consecutive':consecutive, 'failure_id':failure_id,
        }
    return {
        'bad':[],
        'warnings':['ИИ-маркетолог: один часовой цикл завершился ошибкой; канонический таймер повторит полный цикл автоматически'],
        'retry_ready':True, 'failed':True,
        'consecutive':consecutive, 'failure_id':failure_id,
    }


def _marketer_prepare_stall_tick(state, account_id, operation_id, status, generation=None, progress_token=None, hard_after=4):
    """Count only whole scheduler generations with no real proposal progress.

    Guardian runs every few minutes while KPI/content execution is hourly. The
    same prepare operation may legitimately stay ``apply_in_progress`` for many
    cycles because proposals are submitted one by one. A changed progress token
    (submitted/skipped count or last submit evidence) resets the no-progress
    counter; only a new hourly data generation without progress increments it.
    """
    prefix=f"marketer_prepare_pending:{account_id}:"
    current=prefix+str(operation_id)
    for key in list(state):
        if key.startswith(prefix) and key != current:
            state.pop(key,None)
    rec=state.get(current) if isinstance(state.get(current),dict) else {}
    generation=str(generation or "")
    progress_token=str(progress_token or "")
    prev_generation=str(rec.get("generation") or "")
    prev_progress=str(rec.get("progress_token") or "")
    progressed=bool(progress_token and progress_token != prev_progress)
    if progressed or not prev_generation:
        count=1
        first_seen=time.time()
    elif generation and generation == prev_generation:
        count=int(rec.get("checks") or 1)
        first_seen=float(rec.get("first_seen") or time.time())
    else:
        count=int(rec.get("checks") or 0)+1
        first_seen=float(rec.get("first_seen") or time.time())
    state[current]={
        "checks":count,"first_seen":first_seen,"status":str(status or ""),
        "generation":generation,"progress_token":progress_token,
    }
    return {
        "checks":count, "first_seen":first_seen, "generation":generation,
        "progress_token":progress_token,"progressed":progressed,
        "stalled":count>=max(2,int(hard_after)),
        "warning":count>=2 and count<max(2,int(hard_after)),
    }


def _marketer_prepare_stall_clear(state, account_id):
    prefix=f"marketer_prepare_pending:{account_id}:"
    for key in list(state):
        if key.startswith(prefix):
            state.pop(key,None)


def _marketer_prepare_waits_on_fresh_prerequisite(
    health, ledger=None, operation_id=None, now=None, max_age_minutes=75
):
    """True only while a newer safe lifecycle legitimately owns the account.

    KPI_PREPARE_ACTIVE_OBSERVATION_V2: do not infer lifecycle progress only from
    a stage label. A matching active execution-ledger obligation is stronger
    evidence that an ``apply_in_progress`` queue is intentionally serialized
    behind an already published/feed-applied item.

    The ledger evidence is account-scoped by its storage row and operation-scoped
    by the prepare operation id, so unrelated work cannot suppress a real stall.
    Freshness is bounded by ledger.updated_at; stale evidence never grants an
    indefinite exemption. The older health-stage fallback remains for compatible
    lifecycle states that have not yet materialized a ledger entry.
    """
    now=now or dt.datetime.now(dt.timezone.utc)

    if isinstance(ledger,dict):
        try:
            stamp=ledger.get('updated_at')
            checked=dt.datetime.fromisoformat(str(stamp or '').replace('Z','+00:00'))
            if checked.tzinfo is None:
                checked=checked.replace(tzinfo=dt.timezone.utc)
            age=(now-checked).total_seconds()
            fresh=-300 <= age <= max(1,int(max_age_minutes))*60
        except Exception:
            fresh=False
        if fresh and int(ledger.get('active_count') or 0)>0:
            prefix=(str(operation_id or '').strip()+':apply:') if operation_id else ''
            for ob in (ledger.get('obligations') or []):
                if not isinstance(ob,dict):
                    continue
                oid=str(ob.get('operation_id') or '')
                if prefix and not oid.startswith(prefix):
                    continue
                status=str(ob.get('status') or '')
                effect=str(ob.get('effect_status') or '')
                active_pair=(
                    (status=='published' and effect in {'waiting_observation_window','waiting_more_data'})
                    or (status=='publish_requested' and effect=='waiting_avito_confirmation')
                    or (status=='feed_applied' and effect=='waiting_publish')
                )
                if active_pair:
                    return True

    if not isinstance(health,dict):
        return False
    stage=str(health.get('stage') or '')
    if stage not in {
        'publish','confirm_publish','active_operation','effect_check',
        'mapping_wait','safe_feed_promote',
    }:
        return False
    stamp=health.get('checked_at')
    if not stamp:
        return False
    try:
        checked=dt.datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
        if checked.tzinfo is None:
            checked=checked.replace(tzinfo=dt.timezone.utc)
        age=(now-checked).total_seconds()
        return -300 <= age <= max(1,int(max_age_minutes))*60
    except Exception:
        return False


def _marketer_fresh_expected_wait(health, now=None, max_age_minutes=75):
    """Recognize explicit fresh ownerless/external waits without stage-name drift.

    A deferred record with a concrete future ``next_check_at`` is already a
    durable retry contract. It must not page the owner merely because a new
    controller stage name was added. Older records without next_check_at still
    require the conservative known-stage allowlist below.
    """
    if not isinstance(health,dict):
        return False
    if str(health.get('state') or '') not in {'deferred','waiting'}:
        return False
    if not str(health.get('reason') or '').strip():
        return False
    try:
        checked=dt.datetime.fromisoformat(str(health.get('checked_at') or '').replace('Z','+00:00'))
        if checked.tzinfo is None:
            checked=checked.replace(tzinfo=dt.timezone.utc)
        now=now or dt.datetime.now(dt.timezone.utc)
        age=(now-checked).total_seconds()
        if not (-300 <= age <= max(1,int(max_age_minutes))*60):
            return False
    except Exception:
        return False
    next_raw=health.get('next_check_at')
    if next_raw:
        try:
            next_check=dt.datetime.fromisoformat(str(next_raw).replace('Z','+00:00'))
            if next_check.tzinfo is None:
                next_check=next_check.replace(tzinfo=dt.timezone.utc)
            wait_seconds=(next_check-now).total_seconds()
            if -300 <= wait_seconds <= 24*3600:
                return True
        except Exception:
            pass
    stage=str(health.get('stage') or '')
    return stage in {
        'mapping_wait','mapping_recovery','provider_stats_lagging',
        'service_period','inventory_recovery','publication_authorization_gap',
        'canonical_replacement_prepare',
    }


def _marketer_fresh_meaningful_nonmoney(health, now=None, max_age_minutes=75):
    """Treat a fresh successful non-money controller stage as real hourly work.

    kpi_goal_runner writes state=ok only after the controller completed without a
    blocked/waiting/failed result. Requiring a separate kpi_prepare row made valid
    stages such as effect_resolve and proposal_repair_scheduled look like empty
    cycles and created false owner alerts. Explicit no_action/observe states remain
    unhealthy for a behind-KPI account.
    """
    if not isinstance(health,dict) or str(health.get('state') or '')!='ok':
        return False
    stage=str(health.get('stage') or '')
    if not stage.startswith('non_money:'):
        return False
    suffix=stage.split(':',1)[1].strip().lower() if ':' in stage else ''
    if suffix in {'', 'no_action', 'observe', 'waiting', 'deferred'}:
        return False
    try:
        checked=dt.datetime.fromisoformat(str(health.get('checked_at') or '').replace('Z','+00:00'))
        if checked.tzinfo is None:
            checked=checked.replace(tzinfo=dt.timezone.utc)
        now=now or dt.datetime.now(dt.timezone.utc)
        age=(now-checked).total_seconds()
        return -300 <= age <= max(1,int(max_age_minutes))*60
    except Exception:
        return False


def marketer_owner_rule_drift_health():
    """Detect any drift between live KPI rules and Control Plane baselines.

    RULESET_DRIFT_GUARD_V2: daily budget is not the whole business rule. Target
    leads, max CPL and autopilot mode must stay aligned too, otherwise a stale
    reconciler can silently restore yesterday's economics while all services are green.
    Read-only by design: this detector never creates money authority or rewrites KPI.
    """
    bad=[]; warnings=[]; details=[]
    try:
        from kpi_goal_runner import get_goal_auto_accounts
        from app.models.control_plane import ControlRequirement
        accounts=[x for x in (get_goal_auto_accounts() or []) if x.get('account_id')]
    except Exception as exc:
        return {'bad':[], 'warnings':['ИИ-маркетолог: контроль правил недоступен: '+type(exc).__name__], 'details':[]}
    db=SessionLocal()
    try:
        for acc in accounts:
            aid=str(acc.get('account_id') or '')
            raw=db.execute(text("select value from storage where account_id=:a and key='kpi_settings' order by id desc limit 1"),{'a':aid}).scalar()
            try: kpi=json.loads(raw or '{}')
            except Exception: kpi={}
            req=db.query(ControlRequirement).filter(ControlRequirement.key==f'production.marketing.autopilot.{aid}').first()
            if req is None:
                warnings.append(f'ИИ-маркетолог: для {aid} нет Control Plane baseline; текущий KPI остаётся каноническим')
                details.append({'account_id':aid,'kind':'control_plane_baseline_missing'})
                continue
            desired=dict(req.desired_state_json or {})

            # Non-money business rules must never drift silently.
            for field,label in (
                ('target_leads_per_day','план лидов'),
                ('max_cost_per_lead_rub','максимальный CPL'),
            ):
                try: live=float(kpi.get(field))
                except Exception: live=None
                try: baseline=float(desired.get(field))
                except Exception: baseline=None
                if live is None or baseline is None or abs(live-baseline)>0.001:
                    bad.append(f'ИИ-маркетолог: конфликт правила {label} {aid}: KPI={live}, Control Plane={baseline}')
                    details.append({'account_id':aid,'kind':'control_plane_rule_drift','field':field,'kpi':live,'control':baseline,'requirement_id':str(req.id)})

            live_autopilot=bool(kpi.get('bid_autopilot', True))
            control_mode=str(desired.get('autopilot_mode') or '')
            control_autopilot=control_mode in {'goal_auto','always_auto'}
            if live_autopilot != control_autopilot:
                bad.append(f'ИИ-маркетолог: конфликт режима автопилота {aid}: KPI={live_autopilot}, Control Plane={control_mode or "missing"}')
                details.append({'account_id':aid,'kind':'control_plane_rule_drift','field':'bid_autopilot','kpi':live_autopilot,'control_mode':control_mode,'requirement_id':str(req.id)})

            # Money budget gets the stronger owner-provenance check before CP drift.
            auth=kpi.get('daily_budget_authorization') if isinstance(kpi.get('daily_budget_authorization'),dict) else {}
            try: kpi_budget=float(kpi.get('daily_budget_limit_rub'))
            except Exception: kpi_budget=None
            try: req_budget=float(desired.get('daily_budget_limit_rub'))
            except Exception: req_budget=None
            if str(auth.get('source') or '')=='authenticated_set_kpi_settings':
                try: auth_budget=float(auth.get('daily_budget_limit_rub'))
                except Exception: auth_budget=None
                if kpi_budget is None or auth_budget is None or abs(kpi_budget-auth_budget)>0.001:
                    bad.append(f'ИИ-маркетолог: нарушена owner-provenance бюджета {aid}: KPI={kpi_budget}, authorization={auth_budget}')
                    details.append({'account_id':aid,'kind':'kpi_vs_authorization','kpi_budget':kpi_budget,'authorized_budget':auth_budget})
                if req_budget is None or kpi_budget is None or abs(req_budget-kpi_budget)>0.001:
                    bad.append(f'ИИ-маркетолог: конфликт правила дневной бюджет {aid}: подтверждено владельцем {kpi_budget}, Control Plane={req_budget}')
                    details.append({'account_id':aid,'kind':'control_plane_rule_drift','field':'daily_budget_limit_rub','kpi':kpi_budget,'control':req_budget,'requirement_id':str(req.id)})
            elif kpi_budget is not None and req_budget is not None and abs(req_budget-kpi_budget)>0.001:
                # No new money authority is inferred; just expose configuration drift.
                warnings.append(f'ИИ-маркетолог: бюджет без owner-provenance расходится с Control Plane {aid}: KPI={kpi_budget:g}, Control Plane={req_budget:g}')
                details.append({'account_id':aid,'kind':'unproven_budget_drift','kpi_budget':kpi_budget,'control_budget':req_budget,'requirement_id':str(req.id)})
    finally:
        db.close()
    return {'bad':bad,'warnings':warnings,'details':details}


def heal_missing_marketing_control_baselines(account_ids):
    """Create only missing CP baselines from already owner-authorized live KPI.

    MARKETER_CONTROL_BASELINE_SELF_HEAL_V1: this mirrors existing declared
    state; it never creates/changes Avito spend authority. Positive budget is
    copied only when kpi_settings already carries authenticated owner provenance.
    Existing requirements are never overwritten here.
    """
    wanted={str(x) for x in (account_ids or []) if str(x)}
    if not wanted:
        return []
    from app.models.control_plane import ControlRequirement
    from app.models.storage import Storage
    from app.services.control_plane import create_requirement
    results=[]
    db=SessionLocal()
    try:
        for aid in sorted(wanted):
            key=f'production.marketing.autopilot.{aid}'[:160]
            if db.query(ControlRequirement).filter(ControlRequirement.key==key).first() is not None:
                results.append({'account_id':aid,'status':'already_exists'})
                continue
            def _j(k):
                r=(db.query(Storage).filter(Storage.account_id==aid,Storage.key==k)
                   .order_by(Storage.id.desc()).first())
                try:return json.loads(r.value or '{}') if r else {}
                except Exception:return {}
            kpi=_j('kpi_settings'); ap=_j('autopilot_settings')
            auth=kpi.get('daily_budget_authorization') if isinstance(kpi.get('daily_budget_authorization'),dict) else {}
            mode=str(ap.get('mode') or '')
            try: target=float(kpi.get('target_leads_per_day') or 0)
            except Exception: target=0.0
            try: cpl=float(kpi.get('max_cost_per_lead_rub') or 0)
            except Exception: cpl=0.0
            try: budget=float(kpi.get('daily_budget_limit_rub') or 0)
            except Exception: budget=0.0
            if target<=0 or cpl<=0 or mode not in {'goal_auto','always_auto'}:
                results.append({'account_id':aid,'status':'blocked','reason':'kpi_or_autopilot_not_ready'})
                continue
            if budget>0 and str(auth.get('source') or '')!='authenticated_set_kpi_settings':
                results.append({'account_id':aid,'status':'blocked','reason':'budget_owner_provenance_missing'})
                continue
            desired={'autopilot_mode':mode,'target_leads_per_day':target,'max_cost_per_lead_rub':cpl}
            if budget>0: desired['daily_budget_limit_rub']=budget
            try:
                hard=float(kpi.get('hard_max_bid_rub') or 0)
            except Exception: hard=0.0
            if hard>0: desired['hard_max_bid_rub']=hard
            create_requirement(
                db,key=key,title=f'ИИ-маркетолог: {aid}',
                normalized_rule='Автопилот обязан соблюдать KPI и финансовые ограничения, диагностировать отклонения и подтверждать фактическое действие.',
                requirement_type='kpi_rule',scope_type='account',scope_id=aid,module='marketing',priority='high',
                desired_state=desired,
                actions=[{'type':'diagnose_on_kpi_fail'},{'type':'execute_with_money_guard'}],
                constraints=[{'type':'max_cpl','value':cpl},{'type':'daily_budget_required','value':budget if budget>0 else None}],
                verification=[{'level':'L5','type':'actual_settings_readback','required':True},{'level':'L6','type':'kpi_effect','required':True}],
                source={'type':'legacy_production_import','authoritative_after_import':True,'allow_override':True,
                        'bootstrap':'guardian_missing_baseline_v1','money_authority_created':False},
            )
            db.commit()
            results.append({'account_id':aid,'status':'created','desired':desired})
    except Exception as exc:
        db.rollback()
        results.append({'account_id':'*','status':'error','reason':type(exc).__name__})
    finally:
        db.close()
    return results


def marketer_source_contract_health():
    """Read-only invariants for the live protected marketer source.

    MARKETER_SOURCE_CONTRACT_GUARD_V1 prevents green health from hiding known
    throughput/economics regressions. It never edits protected money files.
    """
    import re
    bad=[]; warnings=[]; details=[]
    try:
        rollout=(B/'marketer_rollout_runner.py').read_text(encoding='utf-8')
        cpx=(B/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
        runner=(B/'cpx_advisor_runner.py').read_text(encoding='utf-8')
    except Exception as exc:
        return {'bad':['ИИ-маркетолог: не удалось проверить protected source contract: '+type(exc).__name__],
                'warnings':[], 'details':[]}

    m=re.search(r'(?m)^STABLE_MAX_ACTIONS\s*=\s*(\d+)', rollout)
    stable_cap=int(m.group(1)) if m else None
    if stable_cap is None or stable_cap < 5:
        bad.append(f'ИИ-маркетолог: stable throughput зажат до {stable_cap}; активный мандат допускает до 5 безопасных действий за цикл')
        details.append({'kind':'stable_throughput_cap','current':stable_cap,'required_min':5})

    if 'LOWVIEWS_ATOMIC_CAP_POLICY_BLOCK_V1' not in cpx or 'first_bid_cross_source_hourly_limit' not in cpx:
        bad.append('ИИ-маркетолог: отсутствует account-local обработка atomic first-bid safety block')
        details.append({'kind':'atomic_fleet_isolation_missing'})

    try:
        final_guard=cpx[cpx.index('CPX_FINAL_RAISE_CURRENT_DAY_KPI_ONLY_V1'):cpx.index('OWNER_MANUAL_BID_OVERRIDE_V1')]
    except ValueError:
        final_guard=''
    try:
        profitable=cpx[cpx.index('# PROFITABLE_DAY_PUSH_V1'):cpx.index('# PROFITABLE_DAY_CONTINUE_AFTER_TARGET_OWNER_RULE_V1')]
    except ValueError:
        profitable=''
    bad_spend_blocks=[]
    if not final_guard or 'all_spend_rub' in final_guard or 'ad_spend_rub' not in final_guard:
        bad_spend_blocks.append('final_raise_guard')
    if not profitable or 'all_spend_rub' in profitable or 'ad_spend_rub' not in profitable:
        bad_spend_blocks.append('profitable_day_push')
    if bad_spend_blocks:
        bad.append('ИИ-маркетолог: CPL money-guard использует неканонический расход; нужен ad_spend=presence+promotion без rest')
        details.append({'kind':'cpl_spend_truth','bad_blocks':bad_spend_blocks})

    # RED_CPL_EXECUTION_CONTRACT_GUARD_V1: a red-CPL lower recommendation must
    # remain executable even when the account is behind KPI and raise-measurement
    # slots are free. Otherwise Advisor can prove waste while production silently
    # does zero money-saving work. Dry-run must also stay read-only and never
    # replace the canonical apply heartbeat used by account-progress health.
    if 'RED_CPL_LOWER_GENERAL_V1' not in runner:
        bad.append('ИИ-маркетолог: потерян мост исполнения красного CPL — доказанное снижение ставки снова может остаться только рекомендацией')
        details.append({'kind':'red_cpl_lower_execution_bridge_missing'})
    if 'CPX_RUNNER_DRY_RUN_NO_RUNTIME_MUTATION_V1' not in runner:
        bad.append('ИИ-маркетолог: dry-run советника может затереть боевой heartbeat виртуального маркетолога')
        details.append({'kind':'cpx_dry_run_runtime_mutation_guard_missing'})
    return {'bad':bad,'warnings':warnings,'details':details}


def marketer_account_progress_health(state, hard_after=3):
    """Per-account execution contract for active paid KPI accounts.

    A behind-KPI account is not healthy merely because its controller returned
    HTTP/status=ok. If the last non-money result is no_action and BORIS also made
    no autonomous action during the last hourly window, keep the gap visible.
    Guardian is read-only here: the canonical hourly marketer remains retry owner.
    """
    warnings=[]; bad=[]; details=[]
    try:
        from kpi_goal_runner import get_goal_auto_accounts
        accounts=[x for x in (get_goal_auto_accounts() or [])
                  if float(x.get('target_leads_per_day') or 0)>0
                  and not bool(x.get('monitor_only'))]
    except Exception as exc:
        return {'warnings':['ИИ-маркетолог: не удалось проверить поклиентское исполнение: '+type(exc).__name__],
                'bad':[], 'details':[]}
    db=SessionLocal()
    active_gap_keys=set()
    active_provider_lag_keys=set()
    try:
        business_day=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=3)).date().isoformat()
        for acc in accounts:
            aid=str(acc.get('account_id') or '')
            target=float(acc.get('target_leads_per_day') or 0)
            if not aid or target<=0: continue
            vals={}
            rows=db.execute(text("""
                SELECT DISTINCT ON (key) key,value
                  FROM storage
                 WHERE account_id=:a
                   AND key = ANY(:keys)
                 ORDER BY key,id DESC
            """), {'a':aid,'keys':[
                f'daily_stats:{business_day}',
                'virtual_marketer_account_health',
                'virtual_marketer_runtime',
                'marketer_rollout_state_v1',
                'kpi_execution_ledger',
            ]}).fetchall()
            for key,value in rows:
                try: vals[str(key)]=json.loads(value or '{}')
                except Exception: vals[str(key)]={}
            ds=vals.get(f'daily_stats:{business_day}') or {}
            provider_day=str(ds.get('stats_date') or '')
            if provider_day!=business_day:
                # PROVIDER_STATS_LAG_SLA_V1: one fresh lagged snapshot around
                # Moscow midnight is normal. Repeated distinct hourly snapshots
                # with the previous provider day are not allowed to stay invisible.
                collected_at=str(ds.get('collected_at') or '')
                if provider_day and collected_at:
                    lag_key=f'{aid}:provider_stats_lag'
                    active_provider_lag_keys.add(lag_key)
                    counter_key='marketer_provider_lag:'+lag_key
                    prev=state.get(counter_key)
                    if isinstance(prev,dict):
                        prev_count=int(prev.get('count') or 0)
                        prev_generation=str(prev.get('generation') or '')
                    else:
                        prev_count=int(prev or 0)
                        prev_generation=''
                    count=prev_count if collected_at==prev_generation else prev_count+1
                    state[counter_key]={'count':count,'generation':collected_at,
                                        'provider_day':provider_day,'business_day':business_day}
                    details.append({'account_id':aid,'kind':'provider_stats_day_lagged',
                                    'provider_day':provider_day,'business_day':business_day,
                                    'generation':collected_at,'consecutive_cycles':count})
                    if count>=max(2,int(hard_after)):
                        # PROVIDER_STATS_LAG_EXTERNAL_WAIT_V2:
                        # A fresh collector run with yesterday's provider day is
                        # external Avito reporting lag, not an internal Guardian
                        # failure. Money mutations already fail closed on this
                        # exact condition. Keep it visible and retry on the next
                        # canonical stats cycle; the owner cannot repair it.
                        warnings.append(
                            f'ИИ-маркетолог: Avito-статистика {aid} продолжает отдавать '
                            f'provider_day={provider_day} при business_day={business_day} '
                            f'({count} разных сборов); денежные действия заблокированы безопасно, '
                            'BORIS перепроверит автоматически'
                        )
                    elif count>=2:
                        warnings.append(f'ИИ-маркетолог: Avito-статистика {aid} второй час отстаёт от московского дня; BORIS продолжает автоматический retry')
                continue
            contacts=sum(int((x or {}).get('contacts') or 0)
                         for x in (ds.get('items') or []) if isinstance(x,dict))
            if contacts>=target:
                continue
            health=vals.get('virtual_marketer_account_health') or {}
            stage=str(health.get('stage') or '')

            # KPI_PREPARE_STALL_GUARD_V1: storage rows have no created_at. Track
            # the same pending operation across Guardian checks instead of letting
            # ready_for_ai/apply_in_progress remain green forever. Read-only only.
            pending_rows=db.execute(text("""
                SELECT key,value FROM storage
                 WHERE account_id=:a AND key LIKE 'kpi_prepare:%'
                 ORDER BY id DESC LIMIT 40
            """), {'a':aid}).fetchall()
            pending=None
            for _key,_value in pending_rows:
                try: _pv=json.loads(_value or '{}')
                except Exception: _pv={}
                _ps=str(_pv.get('status') or '')
                if _ps in {'ready_for_ai','proposal_ready','apply_in_progress'}:
                    _proposals=[p for p in (_pv.get('proposal') or []) if isinstance(p,dict)]
                    _done=sum(1 for p in _proposals if p.get('apply_status'))
                    _last_submit=max(
                        [str(p.get('apply_submitted_at') or '') for p in _proposals]
                        or ['']
                    )
                    # KPI_PREPARE_PROGRESS_EVIDENCE_V1: status/operation_id alone
                    # are not progress. A multi-item prepare stays apply_in_progress
                    # while individual proposals are submitted one by one.
                    _progress_token='|'.join((
                        _ps,str(_done),str(len(_proposals)),_last_submit,
                        str(_pv.get('proposal_ready_at') or _pv.get('ai_completed_at') or ''),
                    ))
                    pending={
                        'operation_id':str(_pv.get('operation_id') or str(_key).split(':',1)[-1]),
                        'status':_ps,
                        'progress_token':_progress_token,
                    }
                    break
            if pending:
                # KPI_PREPARE_PREREQUISITE_WAIT_V1: a proposal queue is not
                # stalled while a fresh higher-priority publish/measurement
                # lifecycle legitimately owns this account. Clear accumulated
                # stall debt; once that prerequisite becomes stale or finishes,
                # normal progress accounting resumes automatically.
                if _marketer_prepare_waits_on_fresh_prerequisite(
                    health,
                    ledger=vals.get('kpi_execution_ledger') or {},
                    operation_id=pending['operation_id'],
                ):
                    _marketer_prepare_stall_clear(state,aid)
                else:
                    pst=_marketer_prepare_stall_tick(
                        state,aid,pending['operation_id'],pending['status'],
                        generation=str(ds.get('collected_at') or ''),
                        progress_token=pending.get('progress_token'),
                    )
                    if pst.get('stalled'):
                        bad.append(
                            f"ИИ-маркетолог: зависла контентная очередь {aid}: "
                            f"{pending['status']} {pending['operation_id']} не продвигается {pst['checks']} проверок"
                        )
                    elif pst.get('warning'):
                        warnings.append(
                            f"ИИ-маркетолог: контентная очередь {aid} ещё не продвинулась: "
                            f"{pending['status']} {pending['operation_id']}, проверка {pst['checks']}"
                        )
            else:
                _marketer_prepare_stall_clear(state,aid)

            # ACCOUNT_EXECUTION_OBLIGATION_V2: behind-KPI health is outcome-based,
            # not stage-name based. A client is healthy for this hour only when
            # there is a real autonomous action, a fresh progressing non-money
            # lifecycle, or a fresh explicit blocker/retry owned by BORIS/external
            # dependency. A stale/missing rollout result is a fleet coverage gap.
            actions=int(db.execute(text("""
                SELECT count(*) FROM action_log
                 WHERE account_id=:a AND actor='boris_auto'
                   AND ts >= now() - interval '70 minutes'
                   AND COALESCE(source,'') NOT IN ('cpx_daily_learning','effect_check')
                   AND COALESCE(action,'') NOT LIKE 'Оценил результат%'
            """), {'a':aid}).scalar() or 0)
            if actions>0:
                continue

            runtime=vals.get('virtual_marketer_runtime') or {}
            rollout=vals.get('marketer_rollout_state_v1') or {}
            now_utc=dt.datetime.now(dt.timezone.utc)
            def _fresh_ts(raw, max_age=70*60):
                try:
                    x=dt.datetime.fromisoformat(str(raw or '').replace('Z','+00:00'))
                    if x.tzinfo is None: x=x.replace(tzinfo=dt.timezone.utc)
                    age=(now_utc-x.astimezone(dt.timezone.utc)).total_seconds()
                    return -60 <= age <= max_age
                except Exception:
                    return False

            health_fresh=_fresh_ts(health.get('checked_at'))
            runtime_fresh=_fresh_ts(runtime.get('last_run_at'))
            rollout_fresh=_fresh_ts(rollout.get('checked_at'))
            rollout_stage=str(rollout.get('stage') or '')
            rollout_reason=str(rollout.get('last_reason') or '')
            runtime_status=str(runtime.get('status') or '')
            runtime_mode=str(runtime.get('mode') or '')

            # Explicit, fresh blockers/retries are valid execution outcomes; the
            # separate owner/external health lanes decide whether escalation is needed.
            known_blocker=bool(
                rollout_fresh and rollout_stage=='blocked' and rollout_reason
            ) or bool(
                runtime_fresh and runtime_status in {'blocked','waiting','deferred'} and runtime_mode
            ) or _marketer_fresh_expected_wait(health, now=now_utc) \
              or _marketer_prepare_waits_on_fresh_prerequisite(
                    health,
                    ledger=vals.get('kpi_execution_ledger') or {},
                    operation_id=(pending or {}).get('operation_id'),
                    now=now_utc,
                 )
            if known_blocker:
                continue

            # A non-money stage counts only when it is fresh and actually advanced
            # work. Pure no_action/observe states do not satisfy the obligation.
            meaningful_nonmoney=_marketer_fresh_meaningful_nonmoney(health, now=now_utc)
            if meaningful_nonmoney:
                continue

            reason=str(health.get('reason') or runtime_mode or rollout_reason or 'причина не записана')[:220]
            gap_kind='fleet_not_processed' if not rollout_fresh else 'no_effective_action'
            gap_key=f'{aid}:{gap_kind}'
            active_gap_keys.add(gap_key)
            counter_key='marketer_account_gap:'+gap_key

            # ACCOUNT_PROGRESS_GENERATION_V3: Guardian runs every few minutes,
            # but this is an hourly business SLA. Count the same marketer cycle
            # only once; otherwise three Guardian ticks would fake three failed cycles.
            generation=str(
                health.get('checked_at') or runtime.get('last_run_at')
                or rollout.get('checked_at') or business_day
            )
            prev=state.get(counter_key)
            if isinstance(prev,dict):
                prev_count=int(prev.get('count') or 0)
                prev_generation=str(prev.get('generation') or '')
            else:
                prev_count=int(prev or 0)
                prev_generation=''
            count=prev_count if generation==prev_generation else prev_count+1
            state[counter_key]={'count':count,'generation':generation}
            details.append({'account_id':aid,'target':target,'fact':contacts,'stage':stage,
                            'reason':reason,'gap_kind':gap_kind,'rollout_fresh':rollout_fresh,
                            'runtime_fresh':runtime_fresh,'health_fresh':health_fresh,
                            'generation':generation,'consecutive_cycles':count})
            msg=f'{aid}: KPI {contacts}/{target:g}, за последний час 0 полезных авто-действий, {gap_kind}, stage={stage or "missing"}, cycles={count} — {reason}'
            if count>=max(2,int(hard_after)):
                bad.append('ИИ-маркетолог: пустой поклиентский KPI-цикл: '+msg)
            else:
                warnings.append('ИИ-маркетолог: поклиентский retry требуется: '+msg)
        for key in list(state):
            if key.startswith('marketer_account_gap:'):
                gap=key[len('marketer_account_gap:'):]
                if gap not in active_gap_keys:
                    state.pop(key,None)
            elif key.startswith('marketer_provider_lag:'):
                gap=key[len('marketer_provider_lag:'):]
                if gap not in active_provider_lag_keys:
                    state.pop(key,None)
    finally:
        db.close()
    return {'warnings':warnings,'bad':bad,'details':details}


def classify_mop_training_health(mop_health, rollout_active, state, hard_after=3, generation=None):
    """Classify MOP training health without hiding a sustained outage.

    A single/short local production-pressure fence is intentional self-protection,
    not proof that MOP training is broken. Treat the first two consecutive
    pressure-only observations as warning; the third remains a hard failure.
    Contract drift during canonical HA rollout is also convergence-only.
    Any other MOP training failure stays hard immediately.
    """
    items=[str(x) for x in (mop_health or {}).get('bad',[])[:4]]
    key='mop_ai_pressure_only_count'
    if not items:
        state[key]=0
        state.pop('mop_ai_pressure_only_since',None)
        state.pop('mop_ai_pressure_only_generation',None)
        return [],[]

    version_drift_only=bool(
        rollout_active
        and all('backend тренировки МОП на старом/неполном runtime-контракте' in x
                for x in items)
    )
    if version_drift_only:
        state[key]=0
        state.pop('mop_ai_pressure_only_since',None)
        state.pop('mop_ai_pressure_only_generation',None)
        return [],['Тренировка МОП: одна backend-реплика догоняет версию во время штатной HA-выкатки']

    pressure_only=bool(
        items
        and all('тренировка МОП без доступного AI: local_fenced_by_production_pressure' in x
                for x in items)
    )
    if pressure_only:
        # MOP_PRESSURE_DISTINCT_GENERATION_V1: Guardian runs every two minutes,
        # while training/provider readiness is a production-generation signal.
        # Repeated quick checks of the same hourly generation must not fake three
        # independent failures. Escalate only across distinct generations.
        generation=str(generation or dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H'))
        prev_generation=str(state.get('mop_ai_pressure_only_generation') or '')
        count=int(state.get(key,0) or 0)
        if generation != prev_generation:
            count += 1
            state['mop_ai_pressure_only_generation']=generation
        state[key]=count
        state.setdefault('mop_ai_pressure_only_since',time.time())
        if count < max(1,int(hard_after or 3)):
            return [],[
                'Тренировка МОП временно отложена защитой нагрузки сервера; '
                f'подтверждение {count}/{max(1,int(hard_after or 3))}, повтор будет автоматически'
            ]
        return ['Тренировка МОП: ' + '; '.join(items)],[]

    state[key]=0
    state.pop('mop_ai_pressure_only_since',None)
    state.pop('mop_ai_pressure_only_generation',None)
    return ['Тренировка МОП: ' + '; '.join(items)],[]


def factory_rule_runtime_health():
    """Read-only Rule Runtime health for blocked, stalled and exhausted work."""
    bad=[]; details=[]
    db=SessionLocal()
    try:
        from app.factory import rule_runtime as RR
        rows=RR.daily_status(db)
        blocked=[x for x in rows if x.get('enabled') and str(x.get('status') or '').startswith('blocked_')]
        if blocked:
            bad.append('blocked_rules')
            details.extend(str(x.get('rule_key'))+':'+str(x.get('status')) for x in blocked[:8])
        stats=db.execute(text("""
            SELECT
              count(*) FILTER (WHERE status='RUNNING' AND started_at < now()-interval '20 minutes') AS stale_running,
              count(*) FILTER (WHERE status='FAILED' AND attempts>=5) AS exhausted_failed
            FROM factory_rule_runtime_slots
        """)).mappings().first() or {}
        if int(stats.get('stale_running') or 0):
            bad.append('stale_running')
        if int(stats.get('exhausted_failed') or 0):
            bad.append('exhausted_failed')
        due=[x for x in rows if int(x.get('due_now') or 0)>0 and x.get('status')=='active']
        last_eval=db.execute(text("SELECT max(last_evaluated_at) FROM factory_rule_runtime WHERE enabled")).scalar()
        # boris-factory.timer runs every 30m with up to 2m randomized delay.
        # Do not declare the scheduler stalled inside its normal cadence window;
        # require a genuinely missed cycle plus safety margin.
        if due and last_eval and (dt.datetime.now(dt.timezone.utc)-last_eval).total_seconds()>2400:
            bad.append('scheduler_stalled')
    finally:
        db.close()
    return {'bad':bad,'details':details}


def heal_factory_rule_runtime_scheduler(rule_health, state):
    """Recover only the canonical Factory timer when Rule Runtime is stalled.

    This never starts a rule executor directly. It repairs the scheduler control
    plane, verifies the timer is active, and leaves the next natural Factory tick
    to execute/verify due slots through the normal exactly-once gateway.
    """
    bad=list(rule_health.get('bad') or [])
    if 'scheduler_stalled' not in bad:
        state.pop('factory_rule_runtime_recovery_pending_until', None)
        return {'attempted':False,'healed':False,'pending':False}
    now=time.time()
    pending_until=float(state.get('factory_rule_runtime_recovery_pending_until',0) or 0)
    if pending_until > now:
        return {'attempted':False,'healed':False,'pending':True}
    last=float(state.get('heal:boris-factory.timer',0) or 0)
    if now-last < 600:
        return {'attempted':False,'healed':False,'pending':False,'cooldown':True}
    state['heal:boris-factory.timer']=now
    if enabled('boris-factory.timer'):
        rc,_out=sh('systemctl restart boris-factory.timer',timeout=15)
    else:
        rc,_out=sh('systemctl enable --now boris-factory.timer',timeout=15)
    healed = rc==0 and active('boris-factory.timer')
    if healed:
        state['factory_rule_runtime_recovery_pending_until']=now+300
    return {'attempted':True,'healed':healed,'pending':healed}


def quick(send_ok=False):
    s=load(); bad=[]; warnings=[]; healed=[]
    # MARKETING_LIFECYCLE_GUARDIAN_WIRED_V1: run the bounded lifecycle finalizer
    # and budget-brake retry from the existing 5-minute guardian. This closes
    # matured learning even after entitlement expiry and retries only lower-only
    # budget safety actions; it never raises bids.
    lifecycle_rc,lifecycle_out=sh("cd /root/BORIS/backend && ./venv/bin/python3 guardian_lifecycle_snapshot.py", timeout=120)
    if lifecycle_rc!=0:
        warnings.append("Маркетинговый lifecycle guardian временно не завершил проверку; следующий 5-минутный цикл повторит автоматически")
    elif "MEASUREMENT_SELF_HEAL" in lifecycle_out or "BUDGET_BRAKE_SELF_HEAL" in lifecycle_out:
        healed.append("маркетинговое обучение/контроль бюджета")
    # A global correction is healthy only after the same production contract
    # passes across the full discovered account scope. Run every five minutes,
    # never during an authoritative deploy lock. Guardian is root-owned, so the
    # contract can perform its bounded runtime self-heal here.
    now=time.time(); last_contract=float(s.get('last_contract_check',0) or 0)
    if now-last_contract>=300 and not os.path.exists('/tmp/.boris_deploy.lock'):
        s['last_contract_check']=now
        contract_rc, contract_out = sh('cd /root/BORIS/backend && BORIS_POLICY_NOTIFY=0 BORIS_POLICY_SELF_HEAL=0 ./venv/bin/python production_contract_runner.py', timeout=90)
        if contract_rc == 75:
            # Canonical deploy owns convergence. A source/runtime generation gap
            # during that bounded rollout is not an outage and must not poison
            # the quick guardian; the next timer tick verifies the final state.
            warnings.append('Идёт штатная выкатка новой версии; BORIS проверит итог автоматически')
        elif contract_rc != 0:
            bad.append('Глобальные корректировки применены не на всех аккаунтах')
        elif '\"attempted\": true' in contract_out and '\"failed\": []' in contract_out:
            healed.append('глобальная политика')
    for u in CORE:
        if active(u):
            continue
        # SYSTEMD_CONDITION_GATE_OWNERSHIP_V1: a deliberate Condition*=false
        # safety/audit fence is not a crashed service. Never bypass the owning
        # gate; keep it visible as diagnostic warning and let its owner reopen it.
        if intentionally_condition_gated(u):
            warnings.append(f'{u}: intentionally gated by systemd Condition*; Guardian did not bypass the safety gate')
            continue
        (healed if heal(u,s) else bad).append(u)
    # HA API replicas are checked independently. Heal a failed replica while the
    # other keeps serving traffic; only loss of both replicas is an API outage.
    api_alive=[]
    rollout_active=canonical_backend_rollout_active()
    for name,unit,url in API_REPLICAS:
        ok=active(unit)
        if ok:
            try:
                r=requests.get(url+'/health',timeout=5)
                ok=(r.status_code==200 and (r.json() or {}).get('status')=='ok')
            except Exception: ok=False
        if not ok:
            if rollout_active:
                # HA_ROLLOUT_GUARDIAN_FENCE_V1: this replica may be deliberately
                # drained/restarting. The deploy owner owns recovery until the
                # rolling process exits; the next 2-minute tick verifies again.
                warnings.append(f'{name}: штатная HA-выкатка, guardian не вмешивается в drain/restart')
            else:
                # API_HEAL_DELEGATED_TO_DEPLOY_V2:
                # Restart=always recovers process crashes. Runtime/source
                # convergence belongs to boris-deploy; Guardian never races it.
                s['api_reconcile_requested_at'] = time.time()
                warnings.append(f'{name}: API recovery delegated to boris-deploy')
        api_alive.append(ok)
    if not any(api_alive):
        bad.append('API полностью недоступен: обе рабочие реплики не отвечают')
    elif not all(api_alive):
        # One healthy replica keeps BORIS online. This is an internal redundancy
        # degradation, not an owner-action incident, so do not spam Telegram.
        s['api_redundancy_degraded_at'] = time.time()
    memory_guard=backend_api_memory_guard()
    bad.extend(memory_guard.get("bad") or [])
    warnings.extend(memory_guard.get("warnings") or [])
    rule_health=factory_rule_runtime_health()
    rule_recovery=heal_factory_rule_runtime_scheduler(rule_health,s)
    rule_bad=list(rule_health.get('bad') or [])
    if 'scheduler_stalled' in rule_bad and rule_recovery.get('pending'):
        rule_bad=[x for x in rule_bad if x!='scheduler_stalled']
        warnings.append('Rule Runtime: scheduler восстановлен; следующий естественный Factory-cycle подтвердит due/verify')
        if rule_recovery.get('healed'):
            healed.append('Rule Runtime scheduler')
    bad.extend('Rule Runtime: '+str(x) for x in rule_bad)
    # Validate the real public ingress as well: nginx/TLS/upstream routing can
    # fail while both local API processes still look healthy.
    try:
        r=requests.get(PUBLIC_HEALTH,timeout=7)
        if r.status_code!=200 or (r.json() or {}).get('status')!='ok':
            bad.append(f'Публичный API health HTTP {r.status_code}')
    except Exception as e:
        bad.append(f'Публичный API health: {str(e)[:70]}')
    for name,url in [('frontend',WEB_HEALTH+'/login')]:
        try:
            r=requests.get(url,timeout=10)
            # 4xx here means the process is reachable; only 5xx/connection failure is an outage.
            if r.status_code>=500: bad.append(f'{name} HTTP {r.status_code}')
        except Exception as e:
            # One immediate retry prevents a backend restart/reload race from becoming a false incident.
            time.sleep(2)
            try:
                r=requests.get(url,timeout=10)
                if r.status_code>=500: bad.append(f'{name} HTTP {r.status_code}')
            except Exception as e2: bad.append(f'{name}: {str(e2)[:70]}')
    mop_training_health=mop_training_contract_health()
    _mop_generation=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H')
    _mop_critical,_mop_warnings=classify_mop_training_health(
        mop_training_health, rollout_active, s, hard_after=3, generation=_mop_generation
    )
    bad.extend(_mop_critical)
    warnings.extend(_mop_warnings)
    ph=platform_health()
    storage=ph.get('storage') or {}; backup=ph.get('backup') or {}; queue=ph.get('queue') or {}
    # STORAGE_PRESSURE_SELF_HEAL_V1: disk pressure is an internal maintenance
    # problem first, not an owner task. Ask the bounded storage guardian to
    # remove only its explicitly safe retention classes, then re-read health.
    # Escalate red only if the post-cleanup filesystem is still degraded.
    if storage.get('state') in ('degraded','critical'):
        _storage_before=(storage.get('free_gb'),storage.get('free_pct'),storage.get('state'))
        _gc_rc,_gc_out=sh('systemctl start boris-storage-guardian.service', timeout=120)
        ph=platform_health()
        storage=ph.get('storage') or {}; backup=ph.get('backup') or {}; queue=ph.get('queue') or {}
        if storage.get('state') not in ('degraded','critical'):
            healed.append('дисковое место')
        elif _gc_rc==0 and 'STORAGE_GUARDIAN=' in str(_gc_out or ''):
            warnings.append(
                'Хранилище остаётся под давлением после безопасной автоочистки; '
                f"свободно {storage.get('free_gb','—')} ГБ ({storage.get('free_pct','—')}%)"
            )
    if storage.get('state') in ('degraded','critical'):
        bad.append(f"Диск: свободно {storage.get('free_gb','—')} ГБ ({storage.get('free_pct','—')}%)")
    if backup.get('state') in ('degraded','critical'):
        bad.append(f"Backup БД: последняя копия {backup.get('age_hours','—')} ч назад")
    # Guardian quick must page only on actionable queue incidents.
    # Deferred work and historical/system failures are diagnostics, not an outage.
    ready_q=int(queue.get('ready_queued', queue.get('queued',0)) or 0)
    oldest=int(queue.get('oldest_queued_age_sec',0) or 0)
    # Business validation/rejection is workflow state, not infrastructure outage.
    # reliability.queue_health already separates it from failed_24h/platform failures.
    # `failed_24h` is historical telemetry and can contain already-terminal
    # business/provider jobs. Reliability queue state is the canonical classifier:
    # page only on a current critical queue, depth or age breach. Historical
    # failures remain visible in health but must not make the quick service fail.
    platform_failed=int(queue.get('failed_24h',0) or 0)
    if queue.get('state')=='critical' or ready_q >= 50 or oldest >= 600:
        bad.append(f"Очередь: готово {ready_q}, старейшая {oldest} сек, системных ошибок 24ч {platform_failed}")
    if ph.get('state')=='critical' and ph.get('error'):
        bad.append('Reliability control plane: '+str(ph.get('error')))
    cap_health=bid_cap_health()
    _mk_rc,_mk_state=sh("systemctl show boris-ai-marketer.service -p ActiveState --value")
    _marketer_cycle_running=(str(_mk_state or '').strip() in {'active','activating'})
    cap_mode=bid_cap_recovery_mode(cap_health,_marketer_cycle_running)
    if cap_mode=='wait_hourly':
        warnings.append('Контроль ставок: новый/устаревший runtime уже обновляется текущим часовым циклом ИИ-маркетолога')
    elif cap_mode=='repair':
        cap_recovery=heal_bid_cap_accounts(cap_health.get('repair_accounts'),s,max_accounts=2)
        cap_health=bid_cap_health()
        cap_mode=bid_cap_recovery_mode(cap_health,False)
        if cap_mode=='ok':
            healed.append('контроль ставок')
        elif any(x.get('status')=='attempted' for x in cap_recovery):
            warnings.append('BORIS выполнил lower-only повторную проверку ставок; часть аккаунтов ещё требует следующего безопасного retry')
    if cap_mode=='critical' and cap_health.get('bad'):
        bad.append('Контроль ставок: '+'; '.join(cap_health.get('bad')[:5]))
    budget_health=budget_brake_health()
    # BUDGET_GUARDIAN_SELF_HEAL_V2: do not make the owner the operator.
    # Refresh stale/mismatched spend evidence and recompute the non-destructive
    # growth guard. Existing paid delivery stays live; only NEW growth authority
    # is frozen near the budget boundary.
    if budget_health.get('repair_accounts'):
        budget_recovery=heal_budget_brake_accounts(budget_health.get('repair_accounts'),s,max_accounts=3)
        budget_health=budget_brake_health()
        if not budget_health.get('bad') and not budget_health.get('repair_accounts'):
            healed.append('контроль дневного бюджета')
        elif any(x.get('status')=='attempted' for x in budget_recovery):
            warnings.append('BORIS выполнил автоматическую повторную проверку дневного бюджета; часть ограничений ещё требует следующего безопасного retry')
    if budget_health.get('bad'):
        bad.append('Контроль дневного бюджета: '+'; '.join(budget_health.get('bad')[:5]))

    # LEGACY_BUDGET_INCIDENT_SELF_HEAL_V1: the old destructive brake could leave
    # hundreds of exact pre-brake bids disabled. Detect the durable compensation
    # queue and restore it ownerlessly in bounded batches. The recovery executable
    # independently rechecks budget, owner provenance, hard cap, balance, fresh
    # spend, provider min/max, shared throttle and exact post-write readback.
    legacy_incident=legacy_budget_incident_health()
    if legacy_incident.get('accounts'):
        incident_recovery=heal_legacy_budget_incidents(legacy_incident.get('accounts'),s,max_accounts=1)
        legacy_incident=legacy_budget_incident_health()
        _recovered=sum(int(x.get('verified') or 0)+int(x.get('reconciled_exact_state') or 0) for x in incident_recovery)
        if _recovered:
            healed.append(f'точное восстановление ставок после старого budget-brake: {_recovered}')
        if legacy_incident.get('pending_exact_total'):
            _retry=max([int(x.get('retry_after_seconds') or 0) for x in incident_recovery] or [0])
            warnings.append(
                'BORIS сам восстанавливает ставки после старого массового budget-brake; '
                f"в очереди по durable-state ещё {legacy_incident.get('pending_exact_total')}"
                + (f', внешний Retry-After {_retry} сек' if _retry else '')
            )
    # KPI_EXECUTION_HEALTH_V1: a green timer is not enough. If the last hourly
    # marketer service itself failed, Guardian must page even when safety QA and
    # production contract are green. This catches DB-pool/API execution failures
    # that previously left KPI behind plan while infrastructure looked healthy.
    # KPI_TIMER_SELF_HEAL_V1: an enabled but inactive marketer timer silently
    # stops all future KPI work while the last service execution can still look
    # green. Detect the scheduler itself, restore it, and verify that systemd has
    # a next trigger. No money action is executed by Guardian.
    marketer_timer='boris-ai-marketer.timer'
    # KPI_TIMER_SELF_HEAL_V2: protect both runtime and boot persistence. A stale
    # remote/manual `disable --now` can otherwise stop future KPI work even when
    # the previous marketer execution itself was green. Guardian is root-owned,
    # so it re-enables the timer when needed, starts/restarts it, and verifies a
    # concrete next trigger. Guardian never executes a money action directly.
    # KPI_TIMER_SELF_HEAL_V1_TEST_CONTRACT: DETECT -> SELF-HEAL -> VERIFY.
    timer_active=active(marketer_timer)
    timer_enabled=enabled(marketer_timer)
    if (not timer_active) or (not timer_enabled):
        if not timer_enabled:
            _enable_rc,_enable_out=sh('systemctl enable --now '+marketer_timer)
            timer_healed=(_enable_rc==0)
        else:
            timer_healed=heal(marketer_timer,s)
        timer_active=active(marketer_timer)
        timer_enabled=enabled(marketer_timer)
        next_rc,next_out=sh("systemctl show boris-ai-marketer.timer -p NextElapseUSecRealtime --value")
        if timer_healed and timer_active and timer_enabled and next_rc==0 and str(next_out or '').strip():
            healed.append('расписание ИИ-маркетолога')
        else:
            bad.append('ИИ-маркетолог: часовое расписание остановлено/отключено и не восстановилось автоматически')
    # KPI_CONTENT_AI_SCHEDULER_SELF_HEAL_V1: content rescue must have a real
    # executor. Keep its dedicated timer enabled/active and verify a future
    # trigger. Guardian never calls paid AI itself.
    content_ai_timer='boris-kpi-content-ai.timer'
    content_ai_active=active(content_ai_timer)
    content_ai_enabled=enabled(content_ai_timer)
    if (not content_ai_active) or (not content_ai_enabled):
        if not content_ai_enabled:
            _cai_enable_rc,_cai_enable_out=sh('systemctl enable --now '+content_ai_timer)
            content_ai_healed=(_cai_enable_rc==0)
        else:
            content_ai_healed=heal(content_ai_timer,s)
        content_ai_active=active(content_ai_timer)
        content_ai_enabled=enabled(content_ai_timer)
        _cai_next_rc,_cai_next_out=sh("systemctl show boris-kpi-content-ai.timer -p NextElapseUSecRealtime --value")
        if content_ai_healed and content_ai_active and content_ai_enabled and _cai_next_rc==0 and str(_cai_next_out or '').strip():
            healed.append('расписание AI-контентного восстановления')
        else:
            bad.append('ИИ-маркетолог: расписание AI-контентного восстановления остановлено и не восстановилось автоматически')
    _cai_failed_rc,_cai_failed_out=sh('systemctl is-failed boris-kpi-content-ai.service')
    if _cai_failed_rc == 0:
        warnings.append('ИИ-маркетолог: последний AI-контентный цикл завершился ошибкой; timer повторит очередь автоматически')

    db_session_guard=marketer_db_session_mitigation_health()
    if not db_session_guard.get('source_fixed') and not db_session_guard.get('mitigation_active'):
        db_session_heal=heal_marketer_db_session_mitigation(s)
        db_session_guard=marketer_db_session_mitigation_health()
        if db_session_heal.get('status')=='healed':
            healed.append('DB-session защита ИИ-маркетолога')
    if not db_session_guard.get('source_fixed') and not db_session_guard.get('mitigation_active'):
        bad.append('ИИ-маркетолог: отсутствует и permanent DB-session fix, и временная scoped-защита systemd')
    elif not db_session_guard.get('source_fixed') and db_session_guard.get('mitigation_active'):
        warnings.append('ИИ-маркетолог: временная DB-session защита активна; permanent source-fix подготовлен, но ещё не выкачен')
    marketer_exec=marketer_execution_health(s)
    bad.extend(marketer_exec.get('bad') or [])
    warnings.extend(marketer_exec.get('warnings') or [])
    account_progress=marketer_account_progress_health(s)
    bad.extend(account_progress.get('bad') or [])
    warnings.extend(account_progress.get('warnings') or [])
    owner_rule_drift=marketer_owner_rule_drift_health()
    _missing_cp=[x.get('account_id') for x in (owner_rule_drift.get('details') or [])
                 if x.get('kind')=='control_plane_baseline_missing' and x.get('account_id')]
    if _missing_cp:
        _cp_heal=heal_missing_marketing_control_baselines(_missing_cp)
        if any(x.get('status')=='created' for x in _cp_heal):
            owner_rule_drift=marketer_owner_rule_drift_health()
            healed.append('Control Plane правила новых аккаунтов')
    bad.extend(owner_rule_drift.get('bad') or [])
    warnings.extend(owner_rule_drift.get('warnings') or [])
    source_contract=marketer_source_contract_health()
    bad.extend(source_contract.get('bad') or [])
    warnings.extend(source_contract.get('warnings') or [])
    client_report_runtime=client_report_scheduler_health(self_heal=True)
    for item in client_report_runtime.get('healed',[])[:5]:
        healed.append('клиентские отчёты: '+str(item))
    for item in client_report_runtime.get('warnings',[])[:5]:
        warnings.append('Клиентские отчёты: '+str(item))
    if client_report_runtime.get('critical'):
        bad.append(
            'Клиентские отчёты: '
            +'; '.join(str(x) for x in client_report_runtime.get('critical',[])[:5])
        )

    email_runtime=email_outreach_runtime_health()
    worker_dead=any('worker email-рассылки' in str(x) for x in email_runtime.get('critical',[]))
    if worker_dead:
        # EMAIL_OUTREACH_SELF_HEAL_V1: restore the canonical minute lifecycle,
        # then verify a fresh heartbeat. The oneshot service itself is not kept
        # artificially active; the timer remains the scheduler of record.
        key='heal:email_outreach_runtime'
        if time.time()-float(s.get(key,0) or 0)>=600:
            s[key]=time.time()
            sh('systemctl enable --now boris-email-queue.timer')
            sh('systemctl restart boris-email-queue.timer')
            sh('systemctl start --no-block boris-email-queue.service')
            time.sleep(3)
            email_runtime=email_outreach_runtime_health()
            if not email_runtime.get('critical'):
                healed.append('минутный цикл email-рассылки')
    for item in email_runtime.get('healed',[])[:5]:
        healed.append('email-рассылка: '+str(item))
    for item in email_runtime.get('warnings',[])[:5]:
        warnings.append(str(item))
    if email_runtime.get('critical'):
        bad.append('Email-рассылка: '+'; '.join(str(x) for x in email_runtime.get('critical',[])[:5]))
    if budget_health.get('warnings'):
        warnings.append('Данные расходов Avito временно неполные; денежные действия заблокированы безопасно: '+'; '.join(budget_health.get('warnings')[:5]))
    # PRESENCE_BUDGET_OWNER_ESCALATION_V1: only after BORIS has exhausted all
    # reversible lower-only actions. This is a policy choice, not a service
    # failure, so dedupe it separately from red Guardian incidents.
    presence_owner_actions=list(budget_health.get('owner_actions') or [])
    presence_sig='|'.join(sorted(
        str(x.get('account_id'))+':'+str(x.get('daily_budget_limit_rub'))+':'+str(x.get('presence_today_rub'))
        for x in presence_owner_actions
    ))
    presence_prev=str(s.get('last_presence_budget_owner_action_sig') or '')
    if presence_owner_actions and presence_sig!=presence_prev:
        lines=[]
        for x in presence_owner_actions[:8]:
            lines.append(
                f"• {x.get('account_id')}: лимит {x.get('daily_budget_limit_rub')} ₽/день, "
                f"расход только за размещение сегодня {x.get('presence_today_rub')} ₽"
            )
        tg('💳 BORIS — дневной лимит ниже стоимости размещения Avito\n\n'
           +'\n'.join(lines)
           +'\n\nBORIS уже отключил CPX-продвижение и заморозил добавление новых объявлений. '
           'Дальше безопасно уменьшить расход без снятия действующих объявлений нельзя. '
           'Нужно бизнес-решение: увеличить дневной лимит либо сократить платное присутствие/активный пул. '
           'До решения BORIS не будет наращивать расход.')
        s['last_presence_budget_owner_action_sig']=presence_sig
    elif not presence_owner_actions and presence_prev:
        tg('✅ BORIS — стоимость размещения снова укладывается в дневной лимит. Дополнительное решение владельца больше не требуется.')
        s['last_presence_budget_owner_action_sig']=''
    funding_owner_actions=wallet_capacity_health()
    funding_sig='|'.join(sorted(str(x.get('account_id'))+':'+str(x.get('real_balance_rub')) for x in funding_owner_actions))
    funding_prev=str(s.get('last_funding_owner_action_sig') or '')
    if funding_owner_actions and funding_sig!=funding_prev:
        lines=[]
        for x in funding_owner_actions[:8]:
            lines.append(f"• {x.get('account_id')}: баланс {x.get('real_balance_rub')} ₽, красная цена лида {x.get('max_cpl_rub')} ₽, план {x.get('target_leads_per_day')}, факт {x.get('contacts_today')}")
        tg('💳 BORIS — требуется внешнее действие: пополнить баланс Avito\n\n'+'\n'.join(lines)+'\n\nBORIS уже ограничивает расход безопасно. После пополнения продолжит работу автоматически; дополнительных команд не требуется.')
        s['last_funding_owner_action_sig']=funding_sig
    elif not funding_owner_actions and funding_prev:
        tg('✅ BORIS — баланс Avito снова достаточен для текущего KPI. Автоматическая работа продолжается.')
        s['last_funding_owner_action_sig']=''
    route_health=isolated_route_health(); provider=route_health.get('provider') or {}; blocked_routes=int(route_health.get('blocked_jobs') or 0)
    provider_state=str(provider.get('state') or '')
    if blocked_routes > 0 and provider_state in {'transport_down','transport_degraded','auth_failed','avito_blocked'}:
        label={'transport_down':'сетевой маршрут провайдера недоступен','transport_degraded':'сетевой маршрут нестабилен','auth_failed':'провайдер отклонил доступ','avito_blocked':'нет рабочего выхода до Avito'}.get(provider_state,provider_state)
        warnings.append(f'Avito Browser Session: {label}; ожидают задач {blocked_routes}. Основной контур BORIS работает.')
    # OWNER_ALERT_CONTENT_DEDUPE_V1: one underlying incident must appear once
    # in the owner message even if several health probes report the same text.
    warnings=_unique_owner_alert_items(warnings)
    bad=_unique_owner_alert_items(bad)
    # OWNER_INTERNAL_RETRY_SUPPRESSION_V1 + OWNER_WARNING_DEBOUNCE_V2:
    # transition logic is isolated so duplicate/retry/recovery behavior can be
    # regression-tested without sending real Telegram messages.
    warning_transition=_owner_warning_transition(s,warnings,threshold=3)
    owner_warnings=warning_transition['warnings']
    warning_action=warning_transition['action']
    if warning_action=='send':
        human_warnings=[]
        for item in owner_warnings:
            if item.startswith('ИИ-маркетолог: последний AI-контентный цикл завершился ошибкой'):
                human_warnings.append('Один цикл AI-контента не завершился. Канонический таймер повторит очередь автоматически.')
            elif item.startswith('Avito Browser Session:'):
                human_warnings.append(item.replace('Avito Browser Session:', 'Браузер Avito:'))
            elif item.startswith('Идёт штатная выкатка новой версии'):
                human_warnings.append('Идёт штатное обновление BORIS. Итог будет проверен автоматически.')
            else:
                human_warnings.append(item)
        human_warnings=_unique_owner_alert_items(human_warnings)
        tg('⚠️ BORIS — временное ограничение\n'+'\n'.join('• '+x for x in human_warnings)+'\n\nОт вас действий не требуется. BORIS продолжает работу, проверяет восстановление и повторяет безопасные попытки автоматически.')
    elif warning_action=='recovery':
        if provider_state in {'transport_down','transport_degraded','auth_failed','avito_blocked'} and blocked_routes == 0:
            tg('✅ BORIS — внешнее ограничение больше не блокирует активные задачи. Старые ожидания закрыты безопасно; основной контур работает.')
        else:
            tg('✅ BORIS — временное ограничение снято. От вас действий не требуется.')
    sig='|'.join(sorted(bad)); prev=s.get('last_alert_sig','')
    # OWNER_ALERT_DEBOUNCE_V2: do not turn transient deploy/restart races into
    # alternating red/green Telegram spam. A new incident and its recovery must
    # both be observed twice before the owner is notified.
    if bad:
        s['healthy_confirmations']=0
        pending=str(s.get('pending_alert_sig') or '')
        count=int(s.get('pending_alert_count',0) or 0)+1 if pending==sig else 1
        s['pending_alert_sig']=sig; s['pending_alert_count']=count
        if sig!=prev and count>=2:
            human=[]
            for item in bad:
                if item=='Глобальные корректировки применены не на всех аккаунтах':
                    human.append('Не все клиентские аккаунты получили обязательные системные настройки. BORIS повторяет применение автоматически.')
                elif item.startswith('API полностью недоступен'):
                    human.append('Серверный API BORIS сейчас недоступен на обеих рабочих репликах. Запущено автовосстановление.')
                elif item.startswith('Очередь:'):
                    human.append('Очередь фоновых задач остановилась или накопила системные ошибки. BORIS перезапускает обработку.')
                elif item.startswith('ИИ-маркетолог:'):
                    human.append('Последний цикл ИИ-маркетолога завершился ошибкой. BORIS запускает повторную проверку и восстановление.')
                elif item.startswith('Контроль ставок:'):
                    human.append('Нарушено правило безопасных ставок. Денежные действия ограничены до восстановления контроля.')
                elif item.startswith('Контроль дневного бюджета:'):
                    human.append('Нарушен контроль дневного рекламного бюджета. Расходные действия ограничены до проверки.')
                elif item.startswith('Email-рассылка:'):
                    human.append('Email-рассылка не выполняет текущий план и уже не может безопасно восстановиться без отдельного исправления. BORIS продолжает диагностику автоматически.')
                else:
                    human.append(str(item))
            msg='🚨 BORIS — нужна автоматическая починка\n\n'+'\n'.join('• '+x for x in human)
            if healed: msg+='\n\nУже исправлено в этом цикле: '+', '.join(healed)
            msg+='\n\nОт вас действий не требуется. Следующее сообщение придёт только после устойчивого восстановления или если изменится сама проблема.'
            tg(msg); s['last_alert_sig']=sig
    else:
        s['pending_alert_sig']=''; s['pending_alert_count']=0
        if prev:
            hc=int(s.get('healthy_confirmations',0) or 0)+1; s['healthy_confirmations']=hc
            if hc>=2:
                tg('✅ BORIS — восстановление подтверждено\n\nКритические сервисы снова работают устойчиво. От вас действий не требуется.')
                s['last_alert_sig']=''; s['healthy_confirmations']=0
        elif send_ok:
            tg('✅ BORIS — критический контур работает нормально.')
    save(s); return not bad,bad,healed
def login_ok():
    try:
        r=requests.post(API+'/api/auth/login',json={'email':os.getenv('QA_EMAIL','qa_test@borisqa.ru'),'password':os.getenv('QA_PASSWORD','')},timeout=20); return r.status_code==200
    except:return False
def ui_audit():
    from playwright.sync_api import sync_playwright
    findings=[]
    try:
        auth=requests.post(API+'/api/auth/login',json={'email':os.getenv('QA_EMAIL','qa_test@borisqa.ru'),'password':os.getenv('QA_PASSWORD','')},timeout=20).json()
        token=auth.get('access_token') or ''
        account=str((auth.get('user') or {}).get('account_id') or '')
    except Exception:
        token=''; account=''
    if not token:
        return ['QA token: API login failed']
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--host-resolver-rules=MAP boris-ai.pro 127.0.0.1'])
        for label,w,h in VIEWS:
            ctx=browser.new_context(viewport={'width':w,'height':h}); page=ctx.new_page(); errs=[]
            page.add_init_script("localStorage.setItem('boris_token',"+json.dumps(token)+");"+("localStorage.setItem('boris_currentAccount',"+json.dumps(account)+");" if account else ""))
            page.on('console',lambda m: errs.append('console '+m.text[:100]) if m.type=='error' and 'Failed to load resource: the server responded with a status of 404' not in m.text and 'net::ERR_ABORTED' not in m.text and 'net::ERR_FAILED' not in m.text else None)
            page.on('response',lambda r: errs.append(str(r.status)+' '+r.url) if r.status>=500 else None)
            # Route changes cancel Next.js prefetches by design. Ignore only
            # explicit ERR_ABORTED; every other network failure keeps its URL.
            page.on('requestfailed',lambda r: errs.append('network '+str(r.failure)+' '+r.url[:160]) if 'ERR_ABORTED' not in str(r.failure or '') else None)
            try:
                try:
                    page.goto(WEB+'/dashboard/home',wait_until='commit',timeout=15000)
                except Exception:
                    page.goto(WEB+'/dashboard/home',wait_until='commit',timeout=25000)
                page.wait_for_timeout(1200)
            except Exception as e:
                findings.append(f'{label}: старт кабинета недоступен после retry — {str(e)[:65]}'); ctx.close(); continue
            for route in ROUTES:
                try:
                    target=WEB+route
                    try:
                        page.goto(target,wait_until='commit',timeout=15000)
                    except Exception as first:
                        # A transient navigation timeout is not a UI defect. Retry once on the same page.
                        page.wait_for_timeout(1500)
                        try: page.goto(target,wait_until='domcontentloaded',timeout=35000)
                        except Exception as second:
                            findings.append(f'{label} {route}: navigation failed after retry — {str(second)[:70]}'); continue
                    page.wait_for_timeout(700)
                    d=page.evaluate("() => ({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,n:(document.body.innerText||'').length})")
                    if d['sw']>d['cw']+8: findings.append(f"{label} {route}: overflow {d['sw']}>{d['cw']}")
                    if d['n']<40: findings.append(f'{label} {route}: пустой экран')
                except Exception as e: findings.append(f'{label} {route}: runtime audit — {str(e)[:70]}')
            findings.extend(f'{label}: {e}' for e in sorted(set(errs))[:6])
            try: page.screenshot(path=str(OUT/f"guardian_{dt.datetime.now().strftime('%Y%m%d_%H%M')}_{w}.png"))
            except: pass
            ctx.close()
        browser.close()
    return findings
def daily():
    qok,qbad,healed=quick(); rows=[]; bad=[]
    for name,units in DOMAIN:
        ok=all(active(u) if u.endswith('.service') else (active(u) and enabled(u)) for u in units); rows.append(('✅' if ok else '❌')+' '+name)
        if not ok: bad.append(name)
    auth=login_ok(); rows.append(('✅' if auth else '❌')+' Авторизация/API'); bad += ([] if auth else ['Авторизация/API'])
    sales_rc,sales_out=sh('/root/BORIS/backend/venv/bin/python /root/BORIS/backend/scripts/qa_sales_outreach_safety.py')
    sales_ok=(sales_rc==0 and 'SALES_OUTREACH_SAFETY=PASS' in sales_out)
    rows.append(('✅' if sales_ok else '❌')+' Sales outreach safety')
    if not sales_ok: bad.append('Sales outreach safety')
    email_runtime=email_outreach_runtime_health()
    email_critical=email_runtime.get('critical') or []
    email_warnings=email_runtime.get('warnings') or []
    if email_critical:
        rows.append('❌ Email outreach runtime')
        bad.append('Email outreach runtime')
    elif email_warnings:
        rows.append('⚠️ Email outreach runtime · BORIS догоняет план')
    else:
        rows.append('✅ Email outreach runtime')
    memory_rc,memory_out=sh('/root/BORIS/backend/venv/bin/python /root/BORIS/backend/scripts/qa_memory_confirmation.py')
    memory_ok=(memory_rc==0 and 'MEMORY_CONFIRMATION_E2E=PASS' in memory_out)
    rows.append(('✅' if memory_ok else '❌')+' Memory confirmation')
    if not memory_ok: bad.append('Memory confirmation')
    marketer_rc,marketer_out=sh('/root/BORIS/backend/venv/bin/python /root/BORIS/backend/scripts/qa_marketer_safety.py')
    marketer_ok=(marketer_rc==0 and 'MARKETER_SAFETY=PASS' in marketer_out)
    rows.append(('✅' if marketer_ok else '❌')+' AI marketer safety')
    if not marketer_ok: bad.append('AI marketer safety')
    disabled_rc,disabled_out=sh('/root/BORIS/backend/venv/bin/python /root/BORIS/backend/scripts/qa_account_disable.py')
    disabled_ok=(disabled_rc==0 and 'ACCOUNT_DISABLE_CONTRACT=PASS' in disabled_out)
    rows.append(('✅' if disabled_ok else '❌')+' Account disable contract')
    if not disabled_ok: bad.append('Account disable contract')
    ui=ui_audit(); rows.append(('✅ Интерфейс ПК/планшет/мобильный') if not ui else f'❌ Интерфейс: {len(ui)} проблем'); bad += ([] if not ui else ['Интерфейс'])
    disk=sh('df -P /')[1].splitlines()[-1].split()[4]; ml=[x for x in sh('free -m')[1].splitlines() if x.startswith('Mem:')]; mem=(f'{ml[0].split()[2]}/{ml[0].split()[1]} MB' if ml else 'н/д')
    status='🟢 Система стабильна' if not bad and qok else '🔴 Нужна проверка'; detail_rows=(['• '+x for x in ui[:10]] if ui else ['• критичных UI-дефектов не найдено'])
    if not sales_ok: detail_rows.append('• Sales outreach safety: '+sales_out[-500:].replace('\n',' '))
    if email_critical: detail_rows.append('• Email runtime: '+'; '.join(str(x) for x in email_critical[:3]))
    elif email_warnings: detail_rows.append('• Email runtime: BORIS догоняет план автоматически')
    details='\n'.join(detail_rows)
    msg=f"🛡 BORIS GUARDIAN — ежедневный аудит {dt.datetime.now().strftime('%d.%m.%Y')}\n\n{status}\n\n"+'\n'.join(rows)+f'\n\nРесурсы: диск {disk}, память {mem}\n\nUI/Runtime:\n{details}'
    if healed: msg+='\n\n🔧 Автоматически восстановлено: '+', '.join(healed)
    msg+='\n\nКритические сбои → немедленный alert. Безопасные сервисные сбои → авто-рестарт + повторная проверка.'; tg(msg); return 0
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--daily',action='store_true'); p.add_argument('--send-ok',action='store_true'); a=p.parse_args(); raise SystemExit(daily() if a.daily else (0 if quick(a.send_ok)[0] else 2))
    rule_health=factory_rule_runtime_health()
    bad.extend('Rule Runtime: '+str(x) for x in (rule_health.get('bad') or []))
