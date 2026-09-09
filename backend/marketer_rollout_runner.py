#!/usr/bin/env python3
"""Staged live rollout for repaired BORIS CPX marketer.

No global all-at-once money switch. Each cycle:
- fail closed on global money-safety regression;
- select only paid/KPI accounts with explicit positive budget + hard bid cap;
- require fresh confirmed Avito spend and live inventory;
- execute at most one account action while canary;
- promote account to stable only after repeated safe cycles;
- never touches zero-budget tenants.

The actual mutation always goes through cpx_advisor_runner -> apply_one -> autonomy guard.
"""
from __future__ import annotations
import json, subprocess, sys, math, time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
sys.path.insert(0, '/root/BORIS/backend')
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.marketing_money_policy import latest_confirmed_spend, presence_budget_pressure
from app.services.marketing_signal_guard import money_spend_signal_eligible
from app.services.marketing_clock import marketing_today_iso
from kpi_goal_runner import get_goal_auto_accounts
from app.api.cpx_advisor import hourly_account_low_views_ramp, profitable_day_push, _marketer_mandate_entitlement

STATE_KEY='marketer_rollout_state_v1'
MAX_ACCOUNTS_PER_CYCLE=8
CANARY_SAFE_CYCLES=3
STABLE_MAX_ACTIONS=2
# OWNER_PRIORITY_GROWTH_ACCOUNTS_V1: owner explicitly asked these live clients to be
# optimized urgently. This changes rotation priority only; it never bypasses
# billing, KPI, fresh-money, hard-cap or autonomy guards.
OWNER_PRIORITY_ACCOUNTS={
 'antonybatono_78732','vasyailchenko_82650','planetazayavki_65985',
 'garik_plitka_mo_58647','pbi_artashes_plitka_mo_83993',
 'prodazha_bytovok_25677','evgeniy_peregorodki_12447',
 'td_sakura_boris_82421',
}
OWNER_PRIORITY_MAX_PER_CYCLE=8
# UNIFIED_STAGED_ALL_MARKETER_ACCOUNTS_V1


def _load(db,a,k):
    r=db.execute(text("select value from storage where account_id=:a and key=:k order by id desc limit 1"),{'a':a,'k':k}).fetchone()
    if not r:return {}
    try:return json.loads(r[0] or '{}')
    except Exception:return {}


def _save(db,a,payload):
    raw=json.dumps(payload,ensure_ascii=False)
    r=db.query(Storage).filter(Storage.account_id==a,Storage.key==STATE_KEY).order_by(Storage.id.desc()).first()
    if r:r.value=raw
    else:db.add(Storage(account_id=a,key=STATE_KEY,value=raw))
    db.commit()


def _save_runtime_projection(db, account_id, payload):
    raw=json.dumps(payload,ensure_ascii=False)
    r=(db.query(Storage)
       .filter(Storage.account_id==account_id,Storage.key=='virtual_marketer_runtime')
       .order_by(Storage.id.desc()).first())
    if r:r.value=raw
    else:db.add(Storage(account_id=account_id,key='virtual_marketer_runtime',value=raw))
    db.commit()


def _blocked_runtime_projection(account_id, reason, evidence=None):
    """Owner-facing truth for a money preflight refusal; no provider mutation."""
    # MARKETER_PREFLIGHT_OWNER_ACTION_TRUTH_V1: every blocked rollout refreshes
    # virtual_marketer_runtime. Owner dependencies are explicit; external/data
    # waits remain ownerless so BORIS, not the owner, keeps operating the retry.
    evidence=dict(evidence or {})
    _presence=evidence.get('pressure') if isinstance(evidence.get('pressure'),dict) else {}
    _presence_today=_presence.get('today') if isinstance(_presence.get('today'),dict) else {}
    owner_actions={
        'budget_zero': 'Задайте суточный рекламный бюджет, если хотите разрешить BORIS повышать ставки.',
        'budget_zero_existing_spend': (
            f"При бюджете BORIS 0 ₽ Avito уже списал {float(evidence.get('spent_today_rub') or 0):.0f} ₽ сегодня"
            + (f" (из них presence {float(evidence.get('presence_rub') or 0):.0f} ₽)." if evidence.get('presence_rub') is not None else ".")
            + " BORIS новые повышения ставок и рост числа объявлений не запускает; "
              "активное CPX-продвижение автоматически снимается lower-only safety brake и остаётся выключенным до появления подтверждённого бюджета. "
              "Если расход после этого продолжается, это отдельное базовое платное размещение/presence Avito: "
              "его нужно изменить/отключить либо сознательно задать новый дневной бюджет."
        ),
        'daily_budget_owner_provenance_missing': (
            f"Подтвердите сохранённый суточный рекламный бюджет {float(evidence.get('budget') or 0):.0f} ₽ "
            'в настройках аккаунта. BORIS не будет повышать ставки до подтверждения владельцем.'
        ),
        'target_missing': 'Задайте целевое количество лидов в день для автоматического управления ставками.',
        'target_missing_existing_spend': (
            f"Целевая норма лидов не задана, поэтому BORIS не управляет ставками. "
            f"При этом Avito сегодня уже списал {float(evidence.get('spent_today_rub') or 0):.0f} ₽"
            + (f" (основной тип расхода {evidence.get('primary_spend_type')}: {float(evidence.get('primary_spend_rub') or 0):.0f} ₽)." if evidence.get('primary_spend_type') else ".")
            + " Задайте целевое количество лидов в день, если хотите разрешить автоматическое управление; "
              "до этого BORIS не будет добавлять новые денежные действия."
        ),
        'presence_budget_pressure': (
            f"Базовое платное размещение Avito уже списало {float(_presence_today.get('presence_rub') or 0):.0f} ₽ сегодня "
            f"при суточном лимите {float(_presence.get('daily_budget_limit_rub') or evidence.get('budget') or 0):.0f} ₽"
            + (f" и превышало лимит уже {int(_presence.get('completed_over_budget_days') or 0)} завершённых дня." if int(_presence.get('completed_over_budget_days') or 0)>0 else ".")
            + " BORIS уже остановил добавление новых объявлений и новые повышения ставок. "
              "Чтобы реально уложить общий расход в лимит, нужно уменьшить/отключить платное размещение части действующих объявлений "
              "или сознательно изменить суточный лимит. BORIS не будет сам снимать действующие объявления без отдельного разрешения."
        ),
        'hard_cap_missing': 'Задайте максимальную ставку за просмотр для автоматического управления ставками.',
        'red_cpl_missing': 'Задайте максимальную цену обращения для автоматического управления ставками.',
    }
    owner_action=owner_actions.get(str(reason))
    _paused_by_owner=str(reason) in {'bid_autopilot_disabled','autonomous_mode_disabled'}
    _safe_mode={
        'bid_autopilot_disabled':'PAUSED_BY_OWNER: автопилот ставок выключен владельцем; автоматические повышения не выполняются.',
        'autonomous_mode_disabled':'PAUSED_BY_OWNER: автономный режим маркетолога выключен владельцем; автоматические повышения не выполняются.',
    }.get(str(reason))
    # MARKETER_EXPECTED_WAIT_NOT_BLOCKED_V1: provider/data/inventory waits that
    # require no owner action are normal scheduler states, not broken production.
    _expected_wait = str(reason) in {
        'no_active_items','stats_not_fresh','stats_spend_snapshot_skew',
        'spend_signal_unverified','spend_provider_degraded',
        'provider_stats_day_lagged','dedicated_stats_owner_not_completed',
    }
    now=datetime.now(timezone.utc).isoformat()
    return {
        'account_id':str(account_id),
        'last_run_at':now,
        'finished_at':now,
        'apply_enabled':False,
        'mandate_active':False,
        'allowed_operations':[],
        # MARKETER_PAUSED_BY_OWNER_NOT_INCIDENT_V1: deliberate owner pause is
        # neither success nor failure and must not look like a broken production
        # contour in owner-facing runtime.
        'status':('paused' if _paused_by_owner else 'waiting' if _expected_wait else 'blocked'),
        'mode':(_safe_mode or ('WAITING_OWNER: '+owner_action if owner_action else
                'SAFE_WAIT: денежное действие временно заблокировано; BORIS продолжит автоматическую проверку.')),
        'money_block_reason':str(reason or 'preflight_blocked'),
        'money_block_evidence':evidence,
        'owner_action_required':bool(owner_action),
        'owner_action':owner_action,
        'client_value': ('BORIS соблюдает паузу владельца и не меняет ставки автоматически.' if _paused_by_owner else
                         'BORIS защищает деньги клиента и не делает неподтверждённое увеличение расхода.'),
        'boris_action_now': ('BORIS продолжает наблюдение, но не будет автоматически повышать ставки, пока владелец не включит автопилот.' if _paused_by_owner else
                            'BORIS продолжает автоматическую проверку и измерение; денежное действие возобновится только когда оно безопасно.'),
        'planned':0,'would_apply':0,'applied':0,'skipped':0,'blocked':0,'failed':0,
        'block_reasons':[str(reason or 'preflight_blocked')],
        'reconciled_from':'marketer_rollout_preflight',
    }


def _rollout_marketer_entitlement(db, account_id: str) -> tuple[bool, dict]:
    """Use the exact same marketer product-authority contract as mandate/executor.

    MARKETER_ROLLOUT_UNLIMITED_SCOPE_V1: billing.unlimited is not money authority.
    MARKETER_ROLLOUT_CANONICAL_ENTITLEMENT_V1: rollout uses the exact same
    current marketing service-period truth as mandate/executor. Legacy
    billing.unlimited metadata never grants automatic Avito money authority.
    This helper centralizes the decision for current preflight and stale-state
    reconciliation.
    """
    billing=_load(db,account_id,'billing')
    from app.services.control_plane_adapters_ext import marketing_service_entitlement
    service=marketing_service_entitlement(db,account_id)
    return _marketer_mandate_entitlement(billing,service)


def _global_preflight(db):
    bad_zero=db.execute(text("""
      select distinct m.account_scope[1]
      from money_mandates m
      join storage s on s.account_id=m.account_scope[1] and s.key='kpi_settings'
      where m.status='active' and m.revoked_at is null and 'cpx.raise_bid'=any(m.allowed_operations)
        and coalesce((s.value::jsonb->>'daily_budget_limit_rub')::numeric,0)<=0
    """)).fetchall()
    return {'ok':not bad_zero,'zero_budget_raise_accounts':[str(x[0]) for x in bad_zero]}


def _existing_spend_observation(db, account_id: str) -> dict:
    """DB-only reporting truth for static owner/config blockers.

    This helper never contacts Avito and never authorizes money. It only says
    whether a same-day, previously confirmed spend total already exists while
    BORIS is refusing any new money-increasing action for a separate reason.
    """
    try:
        sp=latest_confirmed_spend(db,account_id,max_age_seconds=86400) or {}
        spent=float(sp.get('spent_today_rub') or 0)
    except Exception:
        return {'spent_today_rub':0.0}
    if spent<=0:
        return {'spent_today_rub':0.0}
    day=str(sp.get('spending_date') or '')
    ded=_load(db,account_id,'daily_spending:'+day) if day else {}
    breakdown=ded.get('breakdown') if isinstance(ded,dict) and isinstance(ded.get('breakdown'),dict) else {}
    clean_breakdown={}
    for k,v in breakdown.items():
        try:
            fv=float(v)
            if math.isfinite(fv) and fv>=0: clean_breakdown[str(k)]=fv
        except Exception:
            continue
    primary_type=None; primary_rub=None
    if clean_breakdown:
        primary_type,primary_rub=max(clean_breakdown.items(),key=lambda x:x[1])
    return {
        'spent_today_rub':spent,
        'spending_date':day,
        'spend_storage_key':sp.get('storage_key'),
        'spend_breakdown':clean_breakdown,
        'primary_spend_type':primary_type,
        'primary_spend_rub':primary_rub,
        'existing_placement_spend':True,
        'boris_money_growth_blocked':True,
    }


def _account_preflight(db,a):
    k=_load(db,a,'kpi_settings')
    # MARKETER_ENTITLEMENT_PRECEDES_OWNER_CONFIG_V1: never ask an owner to fill
    # budget/KPI fields for a money service whose current paid authority is not
    # proven. Entitlement is the first business prerequisite; only active paid
    # accounts proceed to owner-config diagnostics.
    entitled, entitlement = _rollout_marketer_entitlement(db, a)
    if not entitled:
        # MARKETER_ENTITLEMENT_REASON_TRUTH_V1: unknown authority is fail-closed
        # for money exactly like expired authority, but it is not the same fact.
        # Preserve the canonical entitlement state so owner/control-plane views
        # never claim an expiry date that BORIS does not actually know.
        _ent_state=str((entitlement or {}).get('state') or '').strip().lower()
        _reason='paid_period_expired' if _ent_state=='expired' else 'paid_period_unknown'
        return False,_reason,{'entitlement':entitlement}
    # AUTOPILOT_MODE_PRECEDES_OWNER_MONEY_CONFIG_V1: when the owner has
    # deliberately disabled autonomous bidding, do not ask them to confirm a
    # budget/KPI that BORIS is not currently allowed to use. This is a safe
    # paused state, not a broken money configuration.
    ap=_load(db,a,'autopilot_settings')
    if not bool(k.get('bid_autopilot')):
        return False,'bid_autopilot_disabled',{'bid_autopilot':False}
    _mode=str(ap.get('mode') or '').strip()
    if _mode not in {'goal_auto','always_auto'}:
        return False,'autonomous_mode_disabled',{'mode':(_mode or 'not_configured')}

    # MARKETER_ZERO_ACTIVE_INVENTORY_PRECEDES_OWNER_CONFIG_V1:
    # A fresh, complete current-day inventory proving zero active listings is a
    # stronger prerequisite than budget/KPI configuration. Asking the owner to
    # confirm money settings cannot unlock an account with nothing to buy traffic
    # for. Treat this as BORIS-owned automatic wait and recheck next cycle.
    _early_ds=_load(db,a,f'daily_stats:{marketing_today_iso()}')
    _early_comp=(_early_ds.get('completeness') or {}) if isinstance(_early_ds,dict) else {}
    try:
        _early_collected=datetime.fromisoformat(str((_early_ds or {}).get('collected_at') or '').replace('Z','+00:00'))
        if _early_collected.tzinfo is None:
            _early_collected=_early_collected.replace(tzinfo=timezone.utc)
        _early_age=(datetime.now(timezone.utc)-_early_collected.astimezone(timezone.utc)).total_seconds()
    except Exception:
        _early_age=None
    _early_items=(_early_ds.get('items') or []) if isinstance(_early_ds,dict) else []
    _early_complete=bool(_early_comp.get('complete') is True and _early_comp.get('inventory_complete') is True)
    _early_fresh=bool(_early_age is not None and -60 <= float(_early_age) <= 900)
    if _early_complete and _early_fresh:
        _early_active=sum(1 for x in _early_items if isinstance(x,dict) and str(x.get('status') or '').lower()=='active')
        if _early_active <= 0:
            return False,'no_active_items',{'active':0,'stats_age_seconds':_early_age,'inventory_complete':True}

    budget=float(k.get('daily_budget_limit_rub') or 0)
    cap=float(k.get('hard_max_bid_rub') or 0)
    target=float(k.get('target_leads_per_day') or 0)
    if budget<=0:
        # ZERO_BUDGET_EXISTING_SPEND_TRUTH_V1: zero BORIS budget must not be
        # presented as zero real Avito spend. Existing placement/presence can
        # continue charging independently from CPX raises. This is DB-only
        # reporting evidence; it never authorizes a provider mutation.
        _existing=_existing_spend_observation(db,a)
        if float(_existing.get('spent_today_rub') or 0)>0:
            _existing['presence_rub']=(_existing.get('spend_breakdown') or {}).get('presence')
            return False,'budget_zero_existing_spend',_existing
        return False,'budget_zero',{}
    if cap<=0:return False,'hard_cap_missing',{}
    if target<=0:
        # TARGET_MISSING_EXISTING_SPEND_TRUTH_V1: a missing KPI target blocks
        # BORIS money automation, but it does not erase charges already made by
        # existing Avito placements today. Surface both facts together.
        _existing=_existing_spend_observation(db,a)
        if float(_existing.get('spent_today_rub') or 0)>0:
            return False,'target_missing_existing_spend',_existing
        return False,'target_missing',{}
    # MARKETER_STATIC_MONEY_BLOCK_PRECEDENCE_V1: immutable owner/config blockers
    # must be reported before transient provider freshness. Waiting for another
    # Avito snapshot cannot repair missing budget authorization or red CPL.
    auth=k.get('daily_budget_authorization') if isinstance(k.get('daily_budget_authorization'),dict) else {}
    try:
        budget_proven=(str(auth.get('policy_version') or '')=='MONEY_BUDGET_OWNER_PROVENANCE_V1'
                       and int(auth.get('authorized_by_user_id') or 0)>0
                       and float(auth.get('daily_budget_limit_rub') or -1)==budget
                       and str(auth.get('source') or '')=='authenticated_set_kpi_settings')
    except Exception:
        budget_proven=False
    if not budget_proven:
        return False,'daily_budget_owner_provenance_missing',{'budget':budget,'owner_authorized':False}
    try:
        _static_max_cpl=float(k.get('max_cost_per_lead_rub') or 0)
    except Exception:
        _static_max_cpl=0.0
    if _static_max_cpl<=0:
        return False,'red_cpl_missing',{'max_cpl':_static_max_cpl}
    # MARKETER_GROWTH_SIGNAL_MAX_AGE_15M_V1: raising bids is a growth decision.
    # It needs a genuinely fresh same-day spend signal; a 1-2h old snapshot is
    # acceptable for observation, not for buying more traffic after provider throttling.
    sp=latest_confirmed_spend(db,a,max_age_seconds=900) or {}
    # MARKETER_SPEND_TIMESTAMP_SANITY_V1: freshness is not only age<=15m;
    # materially future/non-finite provider data must fail closed too.
    try:
        _sp_ts=float(sp.get('timestamp') or 0); _sp_val=float(sp.get('spent_today_rub'))
        _sp_sane=(sp.get('status')=='ok' and math.isfinite(_sp_ts) and math.isfinite(_sp_val)
                  and _sp_ts>0 and _sp_val>=0 and _sp_ts<=time.time()+60)
    except Exception:
        _sp_sane=False
    if not _sp_sane:
        return False,'spend_not_fresh',{'spend':sp}
    spent=_sp_val
    # MARKETER_PRESENCE_OWNER_ESCALATION_V1: if base paid placement itself is
    # structurally above the owner's daily red line, BORIS has already exhausted
    # its safe automatic self-heal (freeze additive growth + block new raises).
    # Continuing to say SAFE_WAIT would hide a real business decision: changing
    # current paid placement or changing the owner's limit.
    _presence_pressure=presence_budget_pressure(db,a,budget)
    if bool((_presence_pressure or {}).get('blocked')):
        return False,'presence_budget_pressure',{
            'spent':spent,'budget':budget,'spend':sp,'pressure':_presence_pressure,
            'automatic_self_heal':'additive_growth_frozen_and_new_raises_blocked',
        }
    # MARKETER_BUDGET_BLOCK_PRECEDENCE_V1: a confirmed last-good spend already
    # above the owner's budget is sufficient to keep growth blocked even if a
    # later provider refresh was throttled. Surface the stronger economic reason.
    if spent>=budget*0.90:
        return False,'budget_near_limit',{'spent':spent,'budget':budget,'spend':sp}
    if not money_spend_signal_eligible(db,a,sp):
        # MARKETER_SPEND_DEGRADED_REASON_TRUTH_V1: distinguish a fresh last-good
        # sample followed by provider degradation from genuinely stale spend.
        try:
            _day=str(sp.get('spending_date') or '')
            _ded=_load(db,a,'daily_spending:'+_day) if _day else {}
            if isinstance(_ded,dict) and (_ded.get('fallback_status') or _ded.get('fallback_at')):
                return False,'spend_provider_degraded',{'spend':sp,'fallback_status':_ded.get('fallback_status'),'fallback_at':_ded.get('fallback_at'),'last_good_age_sec':_ded.get('last_good_age_sec')}
        except Exception:
            pass
        return False,'spend_signal_unverified',{'spend':sp}
    # MARKETER_PREFLIGHT_SPEND_STATS_COHERENCE_V1: money preflight must not mix a
    # newly refreshed spend snapshot with an older inventory/statistics snapshot.
    # A mixed-time decision can authorize a raise from stale item evidence even
    # though the spend signal itself is fresh. Fail closed until the canonical
    # collector has produced a coherent pair; no owner intervention is required.
    try:
        _sp_confirmed=datetime.fromtimestamp(_sp_ts,tz=timezone.utc)
    except Exception:
        _sp_confirmed=None
    # MARKETER_PREFLIGHT_MOSCOW_STATS_DAY_V1: daily stats and spend budgets are
    # one Moscow business day. Between 00:00 and 02:59 MSK the server UTC date
    # is still yesterday; using it here falsely blocks fresh money decisions for
    # the first three hours of every Moscow day.
    ds=_load(db,a,'daily_stats:'+marketing_today_iso())
    # MARKETER_STATS_FRESH_15M_PREFLIGHT_V1: a fresh spend signal alone is not
    # sufficient to authorize a raise. Inventory/stats used to decide which ads
    # need more money must come from a complete collection received <=15m ago.
    # Older snapshots remain available for reporting but are observation-only.
    try:
        _ds_collected=datetime.fromisoformat(str(ds.get('collected_at') or '').replace('Z','+00:00'))
        if _ds_collected.tzinfo is None:
            _ds_collected=_ds_collected.replace(tzinfo=timezone.utc)
        _ds_collected=_ds_collected.astimezone(timezone.utc)
        _ds_age=(datetime.now(timezone.utc)-_ds_collected).total_seconds()
        _ds_complete=bool((ds.get('completeness') or {}).get('complete') is True)
        _ds_fresh=(_ds_complete and -60 <= _ds_age <= 900)
        _snapshot_skew=abs((_ds_collected-_sp_confirmed).total_seconds()) if _sp_confirmed is not None else float('inf')
        _ds_coherent=bool(_snapshot_skew <= 300)
    except Exception:
        _ds_age=None; _ds_complete=False; _ds_fresh=False; _snapshot_skew=None; _ds_coherent=False
    if not _ds_fresh:
        return False,'stats_not_fresh',{'stats_age_seconds':_ds_age,'stats_complete':_ds_complete}
    if not _ds_coherent:
        return False,'stats_spend_snapshot_skew',{'stats_age_seconds':_ds_age,'snapshot_skew_seconds':_snapshot_skew}
    active=sum(1 for x in (ds.get('items') or []) if x.get('status')=='active')
    if active<=0:return False,'no_active_items',{'active':active,'stats_age_seconds':_ds_age}
    kill=db.execute(text("select count(*) from reliability_kill_switches where account_id=:a and blocked=true and module in ('actions','marketing','background')"),{'a':a}).scalar() or 0
    if kill:return False,'kill_switch',{}
    mand=db.execute(text("""select id from money_mandates where :a=any(account_scope) and status='active' and revoked_at is null
      and source='paid_tariff_kpi' and 'cpx.raise_bid'=any(allowed_operations)
      and (valid_until is null or valid_until>now()) order by id desc limit 1"""),{'a':a}).fetchone()
    if not mand:
        # MARKETER_PREFLIGHT_MANDATE_REASON_TRUTH_V2: a downgraded lower-only
        # paid mandate is not the same thing as a missing tariff. Surface the
        # exact fail-closed business reason so Brain/guardian can explain and
        # self-heal what is possible instead of reporting a misleading
        # no_paid_kpi_mandate incident to the owner.
        try:
            max_cpl=float(k.get('max_cost_per_lead_rub') or 0)
        except Exception:
            max_cpl=0.0
        auth=k.get('daily_budget_authorization') if isinstance(k.get('daily_budget_authorization'),dict) else {}
        try:
            budget_proven=(str(auth.get('policy_version') or '')=='MONEY_BUDGET_OWNER_PROVENANCE_V1'
                           and int(auth.get('authorized_by_user_id') or 0)>0
                           and float(auth.get('daily_budget_limit_rub') or -1)==budget
                           and str(auth.get('source') or '')=='authenticated_set_kpi_settings')
        except Exception:
            budget_proven=False
        if budget>0 and not budget_proven:
            return False,'daily_budget_owner_provenance_missing',{'budget':budget,'owner_authorized':False}
        if max_cpl<=0:
            return False,'red_cpl_missing',{'max_cpl':max_cpl}
        return False,'no_raise_capable_paid_kpi_mandate',{'budget':budget,'max_cpl':max_cpl,'owner_authorized':budget_proven}
    return True,'ready',{'budget':budget,'cap':cap,'spent':spent,'active':active,'mandate_id':mand[0]}


def _runtime(db,a):
    return _load(db,a,'virtual_marketer_runtime')


def _runtime_claims_marketer_money(runtime):
    """Whether an owner-facing runtime still claims active CPX money authority."""
    if not isinstance(runtime,dict):
        return False
    if bool(runtime.get('mandate_active')):
        return True
    ops={str(x) for x in (runtime.get('allowed_operations') or [])}
    if ops & {'cpx.raise_bid','cpx.lower_bid'}:
        return True
    diagnostics=runtime.get('plan_diagnostics') if isinstance(runtime.get('plan_diagnostics'),dict) else {}
    diag_ops={str(x) for x in (diagnostics.get('allowed_operations') or [])}
    if diag_ops & {'cpx.raise_bid','cpx.lower_bid'}:
        return True
    try:
        if int(diagnostics.get('selected_for_execution') or 0) > 0:
            return True
    except Exception:
        return True
    return False


def _inactive_runtime_projection(source, account_id, entitlement, now_iso):
    """Canonical non-error projection for an inactive/unknown paid period."""
    runtime=dict(source or {})
    diagnostics=dict(runtime.get('plan_diagnostics') or {}) if isinstance(runtime.get('plan_diagnostics'),dict) else {}
    if diagnostics:
        diagnostics.update({
            'allowed_operations':[],
            'selected_for_execution':0,
            'planned_after_guard':0,
            'proven_after_guard':0,
            'decision':'INACTIVE_PAID_PERIOD',
            'reason':'paid_period_inactive',
            'next_action':'none_until_paid_period_active',
        })
        runtime['plan_diagnostics']=diagnostics
    _ent_state=str((entitlement or {}).get('state') or '').strip().lower()
    _unknown=_ent_state!='expired'
    _reason='paid_period_unknown' if _unknown else 'paid_period_expired'
    # MARKETER_RUNTIME_ENTITLEMENT_TRUTH_V1: unknown entitlement is not an
    # expired/inactive subscription. Keep both fail-closed for money, but expose
    # the exact reason so owner/Brain does not invent a renewal task.
    runtime.update({
        'account_id':str(account_id),
        'apply_enabled':False,
        'mandate_active':False,
        'allowed_operations':[],
        'status':('blocked' if _unknown else 'inactive'),
        'mode':('SAFE_WAIT: оплаченный период BORIS не подтверждён; денежные действия заблокированы до автоматической проверки.'
                if _unknown else 'INACTIVE: оплаченный период BORIS завершён'),
        'money_block_reason':_reason,
        'money_block_evidence':{'entitlement':dict(entitlement or {})},
        'planned':0,
        'would_apply':0,
        'applied':0,
        'skipped':0,
        'blocked':0,
        'failed':0,
        'block_reasons':[_reason],
        'owner_action_required':False,
        'owner_action':None,
        'service_entitlement':dict(entitlement or {}),
        'finished_at':now_iso,
        'reconciled_at':now_iso,
        'reconciled_from':'canonical_marketing_service_entitlement',
    })
    return runtime


def _reconcile_stale_rollout_states(db):
    # MARKETER_STALE_RUNTIME_SOURCE_UNION_V2: rollout-state is not the only
    # owner-facing source. An older cpx advisor cycle may have written a newer
    # virtual_marketer_runtime after rollout-state was already blocked. Scan the
    # union of both storage keys and reconcile from canonical entitlement truth.
    rows=db.execute(text("""
      select distinct account_id
        from storage
       where key in (:state_key,:runtime_key)
    """),{'state_key':STATE_KEY,'runtime_key':'virtual_marketer_runtime'}).fetchall()
    changed=0
    for row in rows:
        a=str(row[0])
        state=_load(db,a,STATE_KEY)
        runtime=_load(db,a,'virtual_marketer_runtime')
        entitled, ent=_rollout_marketer_entitlement(db,a)
        if entitled:
            continue

        last=state.get('last_runtime') if isinstance(state,dict) and isinstance(state.get('last_runtime'),dict) else {}
        expected_reason=('paid_period_expired' if str((ent or {}).get('state') or '').lower()=='expired' else 'paid_period_unknown')
        state_needs=bool(state) and (
            str(state.get('stage') or '') != 'blocked'
            or str(state.get('last_reason') or '') != expected_reason
            or _runtime_claims_marketer_money(last)
        )
        _expected_runtime_status=('inactive' if expected_reason=='paid_period_expired' else 'blocked')
        runtime_needs=bool(runtime) and (
            str(runtime.get('status') or '').lower() != _expected_runtime_status
            or str(runtime.get('money_block_reason') or '') != expected_reason
            or _runtime_claims_marketer_money(runtime)
            or bool(runtime.get('owner_action_required'))
        )
        if not state_needs and not runtime_needs:
            continue

        now=datetime.now(timezone.utc).isoformat()
        sanitized=_inactive_runtime_projection(runtime or last,a,ent,now)
        if not isinstance(state,dict):
            state={}
        state.update({
            'stage':'blocked',
            'last_reason':('paid_period_expired' if str((ent or {}).get('state') or '').lower()=='expired' else 'paid_period_unknown'),
            'last_evidence':{'entitlement':ent},
            'checked_at':now,
            'last_runtime':sanitized,
            'preflight':{'entitlement':ent},
        })
        _save(db,a,state)

        runtime_row=(db.query(Storage)
            .filter(Storage.account_id==a,Storage.key=='virtual_marketer_runtime')
            .order_by(Storage.id.desc()).first())
        raw=json.dumps(sanitized,ensure_ascii=False)
        if runtime_row:
            runtime_row.value=raw
        else:
            db.add(Storage(account_id=a,key='virtual_marketer_runtime',value=raw))
        db.commit()
        changed += 1
    return changed


def reconcile_runtime_projections_on_stats_wait():
    """Refresh owner-facing money truth when the dedicated stats sweep is late.

    MONEY_RUNTIME_LOCAL_RECONCILE_ON_STATS_WAIT_V1: this function is deliberately
    DB-only. It does not call Avito, does not execute recommendations, does not
    create money receipts and does not mutate rollout stage. Its only job is to
    prevent yesterday/previous-cycle money status from surviving after the
    canonical hourly money pipeline has fail-closed on missing current stats.
    """
    db=SessionLocal()
    results=[]; errors=0
    try:
        eligible=[x['account_id'] for x in get_goal_auto_accounts()
                  if float(x.get('target_leads_per_day') or 0)>0]
        for a in eligible:
            try:
                ok,reason,evidence=_account_preflight(db,a)
                if ok:
                    # The pipeline itself did not receive proof that the current
                    # dedicated provider sweep completed. Even if a previous
                    # snapshot is still barely inside its TTL, do not present
                    # that as current money execution authority.
                    reason='dedicated_stats_owner_not_completed'
                    evidence={
                        'stats_owner':'boris-daily-stats-collector.service',
                        'current_hour_collection_complete':False,
                    }
                projection=_blocked_runtime_projection(a,reason,evidence)
                if reason=='dedicated_stats_owner_not_completed':
                    projection['mode']='SAFE_WAIT: ждём завершения текущего сбора статистики; новые денежные действия не выполняются.'
                    projection['boris_action_now']='BORIS продолжает бесплатную диагностику и повторит денежную проверку автоматически после свежего снимка.'
                projection['reconciled_from']='dedicated_stats_owner_wait_local_truth'
                projection['runtime_truth_kind']='money_fail_closed_local_projection'
                _save_runtime_projection(db,a,projection)
                results.append({'account_id':a,'reason':reason,
                                'owner_action_required':bool(projection.get('owner_action_required'))})
            except Exception as exc:
                errors += 1
                results.append({'account_id':a,'reason':'runtime_reconcile_error',
                                'error':f'{type(exc).__name__}: {str(exc)[:160]}'})
        return {'status':'ok' if errors==0 else 'error','checked':len(eligible),
                'errors':errors,'results':results}
    finally:
        db.close()


def _trusted_rollout_caller():
    # MARKETER_ROLLOUT_SELF_CGROUP_IMPORT_V1: Path is imported explicitly;
    # a missing import previously made the canonical systemd caller fail closed.
    # MARKETER_ROLLOUT_SERVICE_CALLER_GUARD_V2: authorize by the runner's own
    # systemd cgroup, not its parent PID. This survives legitimate shell/stage
    # wrappers while remote SentinelX/ownerless processes remain outside the
    # boris-ai-marketer.service cgroup and fail closed.
    try:
        cgroup = Path("/proc/self/cgroup").read_text(errors="ignore")
    except Exception:
        cgroup = ""
    return "boris-ai-marketer.service" in cgroup


def main():
    if not _trusted_rollout_caller():
        print("BLOCKED_UNTRUSTED_ROLLOUT_CALLER", flush=True)
        return 23
    if '--runtime-reconcile-only' in sys.argv:
        result=reconcile_runtime_projections_on_stats_wait()
        print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
        return 0 if int(result.get('errors') or 0)==0 else 2
    db=SessionLocal()
    try:
        # MARKETER_STALE_RUNTIME_PRE_GLOBAL_GUARD_V1: this reconciliation is
        # read/local-storage only and must run even when a separate money guard
        # blocks the live rollout. Otherwise stale owner-facing authority could
        # survive indefinitely behind an unrelated global preflight failure.
        stale_reconciled=_reconcile_stale_rollout_states(db)
        if stale_reconciled:
            print('ROLLOUT_STALE_STATE_RECONCILED',stale_reconciled,flush=True)
        g=_global_preflight(db)
        if not g['ok']:
            print('ROLLOUT_BLOCK global_preflight',json.dumps(g,ensure_ascii=False),flush=True)
            return 2
        eligible=[x['account_id'] for x in get_goal_auto_accounts() if float(x.get('target_leads_per_day') or 0)>0]
        # Every paid autonomous KPI account with explicit positive budget enters the same bounded staged lane.
        ready=[]
        for a in eligible:
            ok,reason,evidence=_account_preflight(db,a)
            state=_load(db,a,STATE_KEY) or {'stage':'observe','safe_cycles':0,'fail_cycles':0}
            if not ok:
                state.update({'stage':'blocked','last_reason':reason,'last_evidence':evidence,'checked_at':datetime.now(timezone.utc).isoformat()})
                _save(db,a,state)
                _save_runtime_projection(db,a,_blocked_runtime_projection(a,reason,evidence))
                print(a,'BLOCK',reason,evidence,flush=True)
                continue
            # recover blocked account into canary automatically once preflight is healthy.
            stage=str(state.get('stage') or 'observe')
            if stage in {'observe','blocked'}: stage='canary'
            ready.append((a,stage,int(state.get('safe_cycles') or 0),int(state.get('live_successes') or 0),evidence))
        # deterministic rotation: least-proven accounts first, then account id.
        ready.sort(key=lambda x:(0 if x[0] in OWNER_PRIORITY_ACCOUNTS else 1,x[2],x[3],x[0]))
        priority=[x for x in ready if x[0] in OWNER_PRIORITY_ACCOUNTS]
        regular=[x for x in ready if x[0] not in OWNER_PRIORITY_ACCOUNTS]
        # OWNER_PRIORITY_FAIR_ROTATION_V2: checked_at is durable service evidence; least-recent priority tenant wins ties.
        def _last_serv(_row):
            _st=_load(db,_row[0],STATE_KEY) or {}
            return str(_st.get('checked_at') or '')
        priority.sort(key=lambda x:(_last_serv(x),x[2],x[3],x[0]))
        from itertools import islice as _islice_priority
        selected=list(_islice_priority(priority, OWNER_PRIORITY_MAX_PER_CYCLE))
        if regular and len(selected)<MAX_ACCOUNTS_PER_CYCLE: selected.append(regular[0])
        if len(selected)<MAX_ACCOUNTS_PER_CYCLE: selected.extend(priority[OWNER_PRIORITY_MAX_PER_CYCLE:OWNER_PRIORITY_MAX_PER_CYCLE+(MAX_ACCOUNTS_PER_CYCLE-len(selected))])
        if len(selected)<MAX_ACCOUNTS_PER_CYCLE: selected.extend(regular[1:1+(MAX_ACCOUNTS_PER_CYCLE-len(selected))])
        print('ROLLOUT_SELECTED',[(a,s,c,l) for a,s,c,l,_ in selected],flush=True)
        for a,stage,safe,live_successes,evidence in selected:
            max_actions=1 if stage!='stable' else STABLE_MAX_ACTIONS
            # OWNER_PROFITABLE_DAY_SQUEEZE_V1
            growth=profitable_day_push(a,max_items=max_actions) or {}
            print('PROFITABLE_DAY_PUSH',a,json.dumps(growth,ensure_ascii=False,default=str)[:5000],flush=True)
            # STAGED_LOW_VIEWS_10_20_30_V1
            # STAGED_LOW_VIEWS_CONTROLLED_LANE_V1
            import os as _os_lowviews_lane
            _prev_lowviews_lane=_os_lowviews_lane.environ.get("BORIS_MARKETER_MONEY_LANE")
            _os_lowviews_lane.environ["BORIS_MARKETER_MONEY_LANE"]="controlled_lowviews_v2"
            try:
                lowviews=hourly_account_low_views_ramp(a,max_items=max_actions) or {}
            finally:
                if _prev_lowviews_lane is None:
                    _os_lowviews_lane.environ.pop("BORIS_MARKETER_MONEY_LANE",None)
                else:
                    _os_lowviews_lane.environ["BORIS_MARKETER_MONEY_LANE"]=_prev_lowviews_lane
            print('LOWVIEWS_RESULT',a,json.dumps({
                'status':lowviews.get('status'),'active_items':lowviews.get('active_items'),
                'due_low_views':lowviews.get('due_low_views'),'healthy':lowviews.get('healthy'),
                'applied':lowviews.get('applied'),'blocked':lowviews.get('blocked'),
                'failed':lowviews.get('failed'),'hard_max_bid_rub':lowviews.get('hard_max_bid_rub')
            },ensure_ascii=False,default=str)[:5000],flush=True)
            if lowviews.get('status')=='error':
                old_state=_load(db,a,STATE_KEY) or {}
                state={'stage':'blocked','safe_cycles':int(old_state.get('safe_cycles') or 0),
                       'live_successes':int(old_state.get('live_successes') or live_successes),
                       'fail_cycles':int(old_state.get('fail_cycles') or 0)+1,
                       'last_reason':'lowviews_runner_failure','last_lowviews':lowviews,
                       'preflight':evidence,'checked_at':datetime.now(timezone.utc).isoformat()}
                _save(db,a,state)
                print('ROLLOUT_RESULT',a,json.dumps(state,ensure_ascii=False,default=str)[:1800],flush=True)
                continue
            # LOWVIEWS_ZERO_BID_HANDOFF_V1: after ordinary lowviews made no live mutation because a zero bid has no provable delta, hand one item to the bounded first-activation lane.
            if (not lowviews.get('changed_avito') and
                    any(str((x or {}).get('reason') or (x or {}).get('blocked_by') or '')=='blocked_bid_delta_unprovable' for x in (lowviews.get('blocked') or []))):
                from app.api.cpx_advisor import bootstrap_new_no_promo
                _boot=bootstrap_new_no_promo(a,max_items=1) or {}
                print('LOWVIEWS_ZERO_BID_HANDOFF',a,json.dumps(_boot,ensure_ascii=False,default=str)[:3000],flush=True)
                if _boot.get('changed_avito'):
                    lowviews={**lowviews,'changed_avito':True,'first_activation_handoff':_boot,'applied':list(lowviews.get('applied') or [])+list(_boot.get('applied') or [])}
            if lowviews.get('changed_avito'):
                safe+=1
                live_successes+=len(lowviews.get('applied') or [])
                new_stage='stable' if (safe>=CANARY_SAFE_CYCLES and live_successes>=1) else 'canary'
                state={'stage':new_stage,'safe_cycles':safe,'live_successes':live_successes,'fail_cycles':0,
                       'last_reason':'lowviews_live_apply','last_applied':len(lowviews.get('applied') or []),
                       'last_lowviews':lowviews,'preflight':evidence,
                       'checked_at':datetime.now(timezone.utc).isoformat()}
                _save(db,a,state)
                print('ROLLOUT_RESULT',a,json.dumps(state,ensure_ascii=False,default=str)[:1800],flush=True)
                continue
            cmd=['/root/BORIS/backend/venv/bin/python','/root/BORIS/backend/cpx_advisor_runner.py','--apply','--account',a,'--max-actions',str(max_actions)]
            import os as _os_lane
            _env=dict(_os_lane.environ); _env["BORIS_MARKETER_MONEY_LANE"]="staged_rollout_v1"
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=240,env=_env)
            print(p.stdout[-5000:],flush=True)
            rt=_runtime(db,a)
            applied=int(rt.get('applied') or 0); failed=int(rt.get('failed') or 0)
            blocked=int(rt.get('blocked') or 0); skipped=int(rt.get('skipped') or 0); planned=int(rt.get('planned') or 0)
            status=str(rt.get('status') or '')
            # A no-op plan can prove planner/runtime health. But when a money
            # action was planned, canary proof requires at least one confirmed
            # apply and zero blocks/failures. A guard block is safe, but it is
            # not evidence that the live lane works end-to-end.
            cycle_safe=(p.returncode==0 and status=='ok' and failed==0 and
                        (planned==0 or (applied>0 and blocked==0)))
            if cycle_safe:
                safe+=1
                if applied>0:
                    live_successes+=1
                # ROLLOUT_LIVE_PROOF_REQUIRED_V1: no-op cycles validate planning
                # health but cannot promote a tenant to stable money execution.
                new_stage='stable' if (safe>=CANARY_SAFE_CYCLES and live_successes>=1) else 'canary'
                state={'stage':new_stage,'safe_cycles':safe,'live_successes':live_successes,'fail_cycles':0,'last_reason':'safe_cycle',
                       'last_applied':applied,'last_runtime':rt,'preflight':evidence,'checked_at':datetime.now(timezone.utc).isoformat()}
            elif p.returncode==0 and status=='ok' and failed==0 and applied==0 and (blocked>0 or skipped>0):
                # ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V1 compatibility marker.
                # ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V2: an intentional money/economic
                # guard OR a provider-confirmed no-op/skip is a safe HOLD, not
                # proof of execution and not an incident. Preserve the proven
                # stage/counters without promoting safe_cycles.
                old=_load(db,a,STATE_KEY) or {}
                prev_stage=str(old.get('stage') or stage or 'canary')
                if prev_stage=='blocked': prev_stage='canary'
                state={'stage':prev_stage,'safe_cycles':int(old.get('safe_cycles') or safe),
                       'live_successes':int(old.get('live_successes') or live_successes),
                       'fail_cycles':0,'last_reason':'guarded_hold','runner_rc':p.returncode,
                       'last_applied':0,'last_runtime':rt,'preflight':evidence,
                       'checked_at':datetime.now(timezone.utc).isoformat()}
            else:
                old=_load(db,a,STATE_KEY) or {}
                state={'stage':'blocked','safe_cycles':int(old.get('safe_cycles') or 0),'live_successes':int(old.get('live_successes') or live_successes),'fail_cycles':int(old.get('fail_cycles') or 0)+1,
                       'last_reason':'runner_failure','runner_rc':p.returncode,'last_runtime':rt,'preflight':evidence,
                       'checked_at':datetime.now(timezone.utc).isoformat()}
            _save(db,a,state)
            print('ROLLOUT_RESULT',a,json.dumps(state,ensure_ascii=False,default=str)[:1800],flush=True)
        return 0
    finally:
        db.close()

if __name__=='__main__':
    raise SystemExit(main())
