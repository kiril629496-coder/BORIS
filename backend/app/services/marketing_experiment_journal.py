# -*- coding: utf-8 -*-
"""Daily causal journal for BORIS marketing experiments.

Read-only with respect to providers: joins durable action_log / KPI apply history
with stored daily statistics so BORIS can compare money/bid experiments once per day.

WS_AVITO_MONEY_JOURNAL_SCOPE_V1: this module deliberately does not own or
classify title/description/image experiments. Content lifecycle belongs to
WS-AVITO-CONTENT; this journal may only consume aggregate account economics.
"""
from __future__ import annotations
import json
from datetime import date, timedelta, datetime, timezone
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.control_plane_adapters_ext import marketing_service_entitlement
from app.services.kpi_lead_quality import apply_business_lead_filter

# DAILY_EXPERIMENT_JOURNAL_DYNAMIC_SCOPE_V1: canonical marketer discovery is
# the base scope. Explicit owner-priority accounts are an OBSERVATION overlay,
# never a money-entitlement override: persist() records their entitlement and all
# spend mutations continue through the canonical money guards. This keeps named
# accounts visible even when an expired/unknown paid period correctly removes
# them from the execution cohort.
OWNER_PRIORITY_OBSERVE_ACCOUNTS = (
    'antonybatono_78732', 'td_sakura_boris_82421', 'vasyailchenko_82650',
    'planetazayavki_65985', 'garik_plitka_mo_58647', 'pbi_artashes_plitka_mo_83993',
    'prodazha_bytovok_25677', 'evgeniy_peregorodki_12447',
)
def _target_accounts():
    from kpi_goal_runner import get_goal_auto_accounts
    base=[str(x.get('account_id')) for x in get_goal_auto_accounts() if x.get('account_id')]
    return list(dict.fromkeys(base + list(OWNER_PRIORITY_OBSERVE_ACCOUNTS)))

def _j(raw, default):
    try: return json.loads(raw) if isinstance(raw,str) else (raw if raw is not None else default)
    except Exception: return default

def _storage_json(db, account_id, key, default=None):
    raw=db.execute(text("select value from storage where account_id=:a and key=:k order by id desc limit 1"),
                   {'a':account_id,'k':key}).scalar()
    return _j(raw, {} if default is None else default)

def _day_stats(db,a,d):
    o=_storage_json(db,a,f'daily_stats:{d}',{})
    items=o.get('items') if isinstance(o,dict) else []
    items=items if isinstance(items,list) else []
    views=sum(int((x or {}).get('views') or 0) for x in items if isinstance(x,dict))
    contacts=sum(int((x or {}).get('contacts') or 0) for x in items if isinstance(x,dict))
    sp=(o.get('spending') or {}) if isinstance(o,dict) else {}
    spend=None
    if isinstance(sp,dict) and sp.get('status')=='ok':
        try: spend=float(sp.get('all_spend_rub') or 0)
        except Exception: spend=None
    if spend is None:
        dsp=_storage_json(db,a,f'daily_spending:{d}',{})
        if isinstance(dsp,dict) and dsp.get('all_spend_rub') is not None:
            try: spend=float(dsp.get('all_spend_rub') or 0)
            except Exception: spend=None
    return {'views':views,'contacts':contacts,'spend_rub':spend,
            'cpl_rub':round(spend/contacts,2) if spend is not None and contacts else None}

def _business_day_stats(db,a,d):
    base=_day_stats(db,a,d)
    raw=float(base.get('contacts') or 0)
    if str(d)==date.today().isoformat():
        quality=apply_business_lead_filter(db,a,raw)
        business=float(quality.get('business_contacts_today') or 0)
        lead_truth='business_contacts_current_day'
    else:
        quality={'status':'historical_raw_only','raw_contacts_today':raw,
                 'business_contacts_today':None}
        business=None
        lead_truth='raw_contacts_historical_unfiltered'
    return {**base,'business_contacts':business,'lead_truth':lead_truth,'lead_quality':quality}

def _performance_verdict(*, entitlement, target, business_leads, views, spend, cpl,
                         red_cpl, budget, budget_authorized, content_only,
                         balance_health, marketer_health, title_health):
    active=bool((entitlement or {}).get('state')=='active')
    target=float(target or 0); leads=float(business_leads or 0); views=float(views or 0)
    red=float(red_cpl or 0); budget=float(budget or 0)
    over_budget=bool(budget>0 and spend is not None and float(spend)>budget)
    balance_owner=bool((balance_health or {}).get('owner_action_required')) and not over_budget
    marketer_state=str((marketer_health or {}).get('state') or '')
    marketer_reason=str((marketer_health or {}).get('reason') or '')
    title_severity=str((title_health or {}).get('severity') or '')
    # EXPERIMENT_JOURNAL_MAPPING_GAP_TRUTH_V1: exact identity capacity and
    # current-revision mutation authority are different. Only a real mapping gap
    # is an internal canonical-mapping self-heal blocker.
    mapping_gap=int((title_health or {}).get(
        'mapping_capacity_gap',
        (title_health or {}).get('capacity_gap') or 0,
    ) or 0)
    base={'service_active':active,'content_only':bool(content_only),
          'is_money_kpi_account':bool(active and not content_only and target>0),
          'owner_action_required':False,'blocker':None,'next_action':None,
          'budget_violation':over_budget}
    if not active:
        return {**base,'status':'OBSERVE_ONLY_INACTIVE','next_action':'observe_only_paid_period_inactive'}
    if content_only:
        return {**base,'status':'CONTENT_ONLY','next_action':'content_optimization_only_no_bid_autopilot'}
    if target<=0:
        return {**base,'status':'KPI_CONFIG_MISSING','blocker':'target_missing',
                'next_action':'self_heal_or_surface_minimum_kpi_configuration'}
    if over_budget:
        return {**base,'status':'KPI_BUDGET_VIOLATION','blocker':'daily_budget_exceeded',
                'next_action':'keep_growth_blocked_cut_losers_and_repair_conversion'}
    # EXPERIMENT_JOURNAL_OWNER_BUDGET_PRIORITY_V1: zero/unproven BORIS daily
    # budget is an owner authorization boundary, not a provider-wallet problem.
    # Check it before balance health so accounts with enough Avito money but no
    # spending mandate are not told to top up the wrong thing.
    if budget<=0 and budget_authorized is False:
        return {**base,'status':'KPI_BLOCKED_EXTERNAL',
                'blocker':'daily_budget_not_authorized',
                'owner_action_required':True,
                'next_action':'authorize_daily_budget_then_auto_resume'}
    if budget>0 and budget_authorized is False and balance_owner:
        return {**base,'status':'KPI_BLOCKED_EXTERNAL',
                'blocker':'daily_budget_owner_provenance_missing_and_avito_balance_low',
                'owner_action_required':True,
                'next_action':'confirm_daily_budget_and_fund_external_avito_wallet_then_auto_resume'}
    if balance_owner:
        return {**base,'status':'KPI_BLOCKED_EXTERNAL',
                'blocker':str((balance_health or {}).get('reason') or 'avito_balance_low'),
                'owner_action_required':True,'next_action':'fund_external_avito_wallet_then_auto_resume'}
    if leads>=target and (red<=0 or cpl is None or float(cpl)<=red):
        return {**base,'status':'KPI_MET','next_action':'hold_or_bounded_scale_only_if_profitable'}
    if leads>0 and red>0 and cpl is not None and float(cpl)>red:
        return {**base,'status':'KPI_EXPENSIVE_LEADS','blocker':'cpl_above_redline',
                'next_action':'cut_losers_reallocate_and_repair_conversion'}
    if mapping_gap>0 and title_severity.startswith('degraded'):
        return {**base,'status':'KPI_BLOCKED_INTERNAL_SELF_HEAL',
                'blocker':'writable_identity_capacity_gap',
                'next_action':'recover_exact_canonical_mapping_then_continue_content_recovery'}
    if marketer_state=='deferred':
        return {**base,'status':'KPI_BEHIND_RECOVERING',
                'blocker':marketer_reason or 'marketer_deferred',
                'next_action':'automatic_retry_or_existing_observation_until_safe_mutation'}
    if budget>0 and budget_authorized is False:
        return {**base,'status':'KPI_BLOCKED_INTERNAL_SELF_HEAL',
                'blocker':'daily_budget_owner_provenance_missing',
                'next_action':'recover_durable_owner_provenance_or_surface_minimum_owner_action'}
    if budget<=0 and budget_authorized is False:
        return {**base,'status':'KPI_BLOCKED_INTERNAL_SELF_HEAL',
                'blocker':'budget_zero_no_growth_authority',
                'next_action':'continue_non_money_recovery_and_surface_truthful_money_block'}
    if views<=0:
        return {**base,'status':'KPI_BEHIND_RECOVERING','blocker':'no_traffic',
                'next_action':'repair_inventory_status_category_supply_and_visibility'}
    if leads<=0:
        threshold=max(10.0,target*8.0)
        if views<threshold:
            return {**base,'status':'KPI_BEHIND_RECOVERING','blocker':'traffic_starved',
                    'next_action':'increase_reach_on_proven_candidates_when_money_guard_allows'}
        return {**base,'status':'KPI_BEHIND_RECOVERING','blocker':'views_without_contacts',
                'next_action':'repair_offer_title_photo_price_category_conversion'}
    return {**base,'status':'KPI_BEHIND_RECOVERING',
            'next_action':'scale_proven_converters_with_pacing_until_target_or_guard'}

def build(account_id, report_day=None):
    day=report_day or date.today()
    if isinstance(day,str): day=date.fromisoformat(day)
    db=SessionLocal()
    try:
        start=datetime.combine(day,datetime.min.time())
        end=start+timedelta(days=1)
        actions=[dict(r) for r in db.execute(text("""
          select ts,action,object_name,before_val,after_val,reason,source,request_id
          from action_log where account_id=:a and ts>=:s and ts<:e
            and (action ilike '%ставк%' or source like 'cpx_%' or source like 'reach_%')
          order by ts,id
        """),{'a':account_id,'s':start,'e':end}).mappings()]
        # WS_AVITO_MONEY_JOURNAL_SCOPE_V1: content/apply history is intentionally
        # not loaded here. This workstream records only bid/money evidence.
        # DAILY_PENDING_MEASUREMENT_PROGRESS_V1: daily journal must explain what
        # BORIS is waiting for, not only the final 7-day verdict. This is DB-only:
        # no provider request and no money mutation. A pending snapshot is never
        # permission to scale; only a finalized `improved` measurement may unlock
        # the next bounded step through the normal money guards.
        pending=[]
        from app.api.cpx_advisor import _calculate_measure_effect
        mrows=db.execute(text("select key,value from storage where account_id=:a and key like 'cpx_measure:%'"),{'a':account_id}).fetchall()
        for mk,mv in mrows:
            mo=_j(mv,{})
            if not isinstance(mo,dict) or mo.get('status')!='waiting_measurement': continue
            try:
                started=datetime.fromisoformat(str(mo.get('started_at')))
                progress=_calculate_measure_effect(db,account_id,int(mo.get('item_id')),started)
            except Exception as exc:
                progress={'status':'error','reason':type(exc).__name__+': '+str(exc)[:120]}
            pending.append({'item_id':str(mo.get('item_id') or ''),'action':mo.get('action'),'old_bid_rub':mo.get('old_bid_rub'),'new_bid_rub':mo.get('new_bid_rub'),'started_at':mo.get('started_at'),'progress':progress,'scale_eligible':False})
        # DAILY_EXPERIMENT_PORTFOLIO_MONEY_V1: deterministic winner/loser memory
        # for bid hypotheses only. Content experiments are owned by WS-AVITO-CONTENT.
        learning=_j(db.execute(text("select value from storage where account_id=:a and key='cpx_learning_journal' order by id desc limit 1"),{'a':account_id}).scalar(),[])
        learning=learning if isinstance(learning,list) else []
        _kpi=_j(db.execute(text("select value from storage where account_id=:a and key='kpi_settings' order by id desc limit 1"),{'a':account_id}).scalar(),{})
        _ent=marketing_service_entitlement(db,account_id) or {}
        try: _target=float(_kpi.get('target_leads_per_day') or 0)
        except Exception: _target=0.0
        try: _red=float(_kpi.get('max_cost_per_lead_rub') or 0)
        except Exception: _red=0.0
        try: _budget=float(_kpi.get('daily_budget_limit_rub') or 0)
        except Exception: _budget=0.0
        _today_econ=_day_stats(db,account_id,day.isoformat())
        _business_today=_business_day_stats(db,account_id,day.isoformat())
        _spent=_today_econ.get('spend_rub')
        _contacts=float(_business_today.get('business_contacts') if _business_today.get('business_contacts') is not None else _today_econ.get('contacts') or 0)
        _cpl=round(float(_spent)/_contacts,2) if _spent is not None and _contacts>0 else None
        _account_scale_blockers=[]
        if _ent.get('state')!='active': _account_scale_blockers.append('marketing_paid_period_inactive_or_unknown')
        if _target<=0: _account_scale_blockers.append('target_leads_per_day_missing')
        if _red<=0: _account_scale_blockers.append('max_cpl_missing')
        if _budget<=0: _account_scale_blockers.append('daily_budget_missing')
        if _spent is None: _account_scale_blockers.append('spend_unknown')
        elif _budget>0 and _spent>=_budget*0.90: _account_scale_blockers.append('budget_near_or_over_limit')
        if _contacts<=0: _account_scale_blockers.append('no_current_lead_signal')
        if _cpl is not None and _red>0 and _cpl>_red: _account_scale_blockers.append('cpl_above_redline')
        _pending_item_actions={(str(x.get('item_id') or ''),str(x.get('action') or '')) for x in pending}
        _stats_raw=_j(db.execute(text("select value from storage where account_id=:a and key=:k order by id desc limit 1"),{'a':account_id,'k':'daily_stats:'+day.isoformat()}).scalar(),{})
        _today_items_by_id={str(x.get('id')):x for x in (_stats_raw.get('items') or []) if isinstance(x,dict) and x.get('id') is not None}
        _advice=_j(db.execute(text("select value from storage where account_id=:a and key='cpx_advice' order by id desc limit 1"),{'a':account_id}).scalar(),{})
        _paid_state={}
        for _grp in (_advice.get('recommendations') or {}).values():
            if not isinstance(_grp,list): continue
            for _it in _grp:
                if not isinstance(_it,dict) or _it.get('id') is None: continue
                _iid=str(_it.get('id')); _cur=_paid_state.get(_iid) or {}
                _cur['promotion_active']=bool(_cur.get('promotion_active') or _it.get('promotion_active'))
                if _it.get('bid_rub') is not None:
                    try: _cur['bid_rub']=float(_it.get('bid_rub'))
                    except Exception: pass
                _paid_state[_iid]=_cur
        from app.services.marketing_money_policy import effective_hard_bid_cap
        try: _hard_cap=float(effective_hard_bid_cap(db,account_id))
        except Exception: _hard_cap=0.0
        bid_portfolio=[]
        for x in learning[-100:]:
            if not isinstance(x,dict): continue
            finished=str(x.get('finished_at') or '')[:10]; eff=str(x.get('effect') or '')
            cls='winner' if eff=='improved' else ('loser' if eff in {'worsened','inconclusive_no_signal'} else 'observe')
            current_day=bool(finished==day.isoformat())
            is_raise=str(x.get('action') or '')=='raise' and float(x.get('new_bid_rub') or 0)>float(x.get('old_bid_rub') or 0)
            _iid=str(x.get('item_id') or ''); _item_pending=(_iid,'raise') in _pending_item_actions
            _scale_blockers=list(_account_scale_blockers)
            if _item_pending: _scale_blockers.append('same_item_measurement_pending')
            _today_item=_today_items_by_id.get(_iid) or {}; _paid_item=_paid_state.get(_iid) or {}
            if not _paid_item.get('promotion_active') or float(_paid_item.get('bid_rub') or 0)<=0: _scale_blockers.append('paid_promotion_not_active_now')
            if int(_today_item.get('contacts') or 0)<=0: _scale_blockers.append('item_has_no_lead_today')
            if _hard_cap<=0: _scale_blockers.append('hard_bid_cap_missing')
            elif float(_paid_item.get('bid_rub') or 0)>=_hard_cap: _scale_blockers.append('hard_bid_cap_reached')
            _learning_candidate=bool(cls=='winner' and current_day and is_raise)
            bid_portfolio.append({**x,'classification':cls,'current_day':current_day,'is_raise_experiment':is_raise,'learning_scale_candidate':_learning_candidate,'scale_blockers':_scale_blockers if _learning_candidate else [],'current_item_signal':{'contacts_today':int(_today_item.get('contacts') or 0),'views_today':int(_today_item.get('views') or 0),'promotion_active':bool(_paid_item.get('promotion_active')),'current_bid_rub':_paid_item.get('bid_rub'),'hard_cap_rub':_hard_cap},'scale_eligible':bool(_learning_candidate and not _scale_blockers),'next_action':('guarded_plus_10pct_candidate' if _learning_candidate and not _scale_blockers else ('wait_or_fix_blockers' if _learning_candidate else 'observe_or_do_not_repeat'))})
        before=_day_stats(db,account_id,(day-timedelta(days=1)).isoformat())
        after=_day_stats(db,account_id,day.isoformat())
        _fact=(_advice.get('fact') or {}) if isinstance(_advice,dict) else {}
        _budget_authorized=_fact.get('daily_budget_authorized')
        _content_only=bool(_kpi.get('placement_package_content_only')) or (_kpi.get('bid_autopilot') is False)
        _balance_health=_storage_json(db,account_id,'guardian_balance_funding_health',{})
        _marketer_health=_storage_json(db,account_id,'virtual_marketer_account_health',{})
        _title_health=_storage_json(db,account_id,'guardian_title_cohort_health',{})
        _verdict=_performance_verdict(
            entitlement=_ent,target=_target,business_leads=_contacts,views=after.get('views') or 0,
            spend=_spent,cpl=_cpl,red_cpl=_red,budget=_budget,budget_authorized=_budget_authorized,
            content_only=_content_only,balance_health=_balance_health,
            marketer_health=_marketer_health,title_health=_title_health,
        )
        _attainment=round(100.0*_contacts/_target,1) if _target>0 else None
        _conversion=round(100.0*_contacts/float(after.get('views') or 0),2) if float(after.get('views') or 0)>0 else None
        _business_kpi={
            **_verdict,
            'target_business_leads':_target,
            'fact_business_leads':_contacts,
            'attainment_pct':_attainment,
            'lead_gap':max(0.0,_target-_contacts),
            'views':int(after.get('views') or 0),
            'view_to_business_lead_pct':_conversion,
            'spend_rub':_spent,
            'cpl_rub':_cpl,
            'max_cpl_rub':_red or None,
            'daily_budget_rub':_budget,
            'daily_budget_authorized':_budget_authorized,
            'lead_truth':_business_today.get('lead_truth'),
            'excluded_job_seekers_today':(_business_today.get('lead_quality') or {}).get('excluded_job_seekers_today'),
            'day_over_day':{
                'previous_metric':'raw_contacts_historical_unfiltered',
                'previous_views':before.get('views'),
                'previous_raw_contacts':before.get('contacts'),
                'previous_spend_rub':before.get('spend_rub'),
                'previous_raw_cpl_rub':before.get('cpl_rub'),
                'views_delta':int(after.get('views') or 0)-int(before.get('views') or 0),
                'raw_contacts_delta':int(after.get('contacts') or 0)-int(before.get('contacts') or 0),
            },
            'actions_today':{
                'bid_actions_count':len(actions),
                'pending_measurements_count':len(pending),
                'virtual_marketer_state':_marketer_health.get('state'),
                'virtual_marketer_stage':_marketer_health.get('stage'),
            },
            'action_results':{
                'bid_winners_today':sum(1 for x in bid_portfolio if x['classification']=='winner' and x.get('current_day')),
                'bid_losers_today':sum(1 for x in bid_portfolio if x['classification']=='loser' and x.get('current_day')),
                'scale_eligible_now':sum(1 for x in bid_portfolio if x.get('scale_eligible')),
            },
        }
        return {'version':4,'scope':'WS-AVITO-MONEY','account_id':account_id,'date':day.isoformat(),
                'generated_at':datetime.now(timezone.utc).isoformat(),'technical_run_ok':True,
                'business_kpi':_business_kpi,'bid_actions':actions,'pending_measurements':pending,
                'portfolio':{'bid':bid_portfolio,'summary':{
                    'bid_winners':sum(1 for x in bid_portfolio if x['classification']=='winner' and x.get('current_day') and x.get('is_raise_experiment')),
                    'bid_losers':sum(1 for x in bid_portfolio if x['classification']=='loser' and x.get('current_day') and x.get('is_raise_experiment')),
                    'observing':sum(1 for x in bid_portfolio if x['classification']=='observe'),
                    'bid_scale_eligible_now':sum(1 for x in bid_portfolio if x.get('scale_eligible')),
                    'bid_learning_candidates_blocked':sum(1 for x in bid_portfolio if x.get('learning_scale_candidate') and not x.get('scale_eligible'))}},
                'scale_account_guard':{'service_entitlement':_ent,'target_leads_per_day':_target,
                    'red_cpl_rub':_red,'daily_budget_rub':_budget,'today_spend_rub':_spent,
                    'today_business_contacts':_contacts,'today_cpl_rub':_cpl,'blockers':_account_scale_blockers},
                'result_before':before,'result_today':after,
                'delta':{'views':after['views']-before['views'],'contacts':after['contacts']-before['contacts']},
                'causal_note':'Daily progress is observational. technical_run_ok is not business KPI success. Money scaling stays guarded at <=10% per autonomous write; content experiments are owned by WS-AVITO-CONTENT.'}

    finally: db.close()

def persist(account_id, report_day=None):
    payload=build(account_id,report_day)
    db=SessionLocal()
    try:
        entitlement=marketing_service_entitlement(db,account_id)
    finally:
        db.close()
    payload['service_entitlement']=entitlement
    # OWNER_PRIORITY_JOURNAL_TRUTH_V1: this field describes owner-requested
    # optimization priority only; it never grants money authority. Entitlement,
    # budget, CPL and hard-cap guards remain the sole execution boundary.
    payload['owner_priority_growth']=bool(account_id in OWNER_PRIORITY_OBSERVE_ACCOUNTS)
    payload['optimization_policy']={
        'cadence':'hourly_execute_daily_learn',
        'ordinary_bid_step_pct_max':10,
        'late_day_catchup_intensity_pct_max':50,
        'autonomous_bid_write_step_pct_max':10,
        'hard_bid_cap_source':'account_kpi_settings_and_money_executor',
        'scale_rule':'all autonomous bid writes are <=10%; pacing intensity only selects urgency/cohort and never expands the write cap',
        'single_hypothesis_per_item':True,
        # OWNER_GROWTH_LEARNING_POLICY_V2: journal mirrors bounded execution; reporting limits never grant money authority.
        'winner_portfolio_max':10,
        'max_cumulative_bid_growth_pct':70,
        'late_day_one_step_then_measure':True,
        'scale_evidence':'finalized winner only; current entitlement/economics still required',
    }
    key='marketing_experiment_journal:'+payload['date']; raw=json.dumps(payload,ensure_ascii=False,default=str)
    db=SessionLocal()
    try:
        row=db.execute(text('select id from storage where account_id=:a and key=:k order by id desc limit 1'),{'a':account_id,'k':key}).first()
        if row: db.execute(text('update storage set value=:v where id=:i'),{'v':raw,'i':row[0]})
        else: db.execute(text('insert into storage(account_id,key,value) values(:a,:k,:v)'),{'a':account_id,'k':key,'v':raw})
        db.commit()
    finally: db.close()
    return payload

def run():
    out=[]
    for a in _target_accounts():
        try: out.append(persist(a))
        except Exception as e: out.append({'account_id':a,'error':type(e).__name__+': '+str(e)[:160]})
    technical_ok=not any('error' in x for x in out)
    active=[]; content_only=[]
    for x in out:
        if not isinstance(x,dict) or 'error' in x: continue
        k=x.get('business_kpi') or {}
        if k.get('content_only') and k.get('service_active'):
            content_only.append(x.get('account_id'))
        if k.get('is_money_kpi_account'):
            active.append(x)
    target=sum(float((x.get('business_kpi') or {}).get('target_business_leads') or 0) for x in active)
    fact=sum(float((x.get('business_kpi') or {}).get('fact_business_leads') or 0) for x in active)
    spend=sum(float((x.get('business_kpi') or {}).get('spend_rub') or 0) for x in active)
    met=sum(1 for x in active if (x.get('business_kpi') or {}).get('status')=='KPI_MET')
    status='KPI_DAY_MET' if active and met==len(active) else ('NO_ACTIVE_MONEY_KPI' if not active else 'KPI_DAY_FAILED')
    summary={
        'date':date.today().isoformat(),
        'business_kpi_status':status,
        'active_money_accounts':len(active),
        'target_business_leads':target,
        'fact_business_leads':fact,
        'attainment_pct':round(100.0*fact/target,1) if target>0 else None,
        'lead_gap':max(0.0,target-fact),
        'target_met_accounts':met,
        'total_spend_rub':round(spend,2),
        'blended_spend_per_business_lead_rub':round(spend/fact,2) if fact>0 else None,
        'content_only_accounts':content_only,
        'account_statuses':{str(x.get('account_id')):(x.get('business_kpi') or {}).get('status') for x in active},
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'rule':'lead_target_first_cpl_second_budget_constraint_not_goal',
    }
    db=SessionLocal()
    try:
        key='marketing_performance_scorecard:'+summary['date']
        raw=json.dumps(summary,ensure_ascii=False,default=str)
        row=db.execute(text("select id from storage where account_id='__marketing__' and key=:k order by id desc limit 1"),{'k':key}).first()
        if row: db.execute(text('update storage set value=:v where id=:i'),{'v':raw,'i':row[0]})
        else: db.execute(text("insert into storage(account_id,key,value) values('__marketing__',:k,:v)"),{'k':key,'v':raw})
        db.commit()
    finally: db.close()
    print(json.dumps({'ok':technical_ok,'technical_run_ok':technical_ok,'business_kpi_status':status,
                      'active_paid_summary':summary,'accounts':out},ensure_ascii=False,default=str))
if __name__=='__main__': run()
