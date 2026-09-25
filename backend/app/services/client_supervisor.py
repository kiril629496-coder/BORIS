"""Unified per-client operational supervisor projection.

CLIENT_SUPERVISOR_BRAIN_COMPOSITE_V1
Read-only and evidence-first. It composes the canonical System Brain adapters,
paid-period evidence, queues and account configuration; it does not create a
second KPI/autopilot engine and never sends/publishes/spends by itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import text

from app.models.account import Account
from app.models.account_slot import AccountSlot
from app.models.background_job import BackgroundJob
from app.models.storage import Storage
from app.models.control_plane import ControlIncident
from app.client_config.entitlements import has_entitlement
from app.services.control_plane_adapters_ext import marketing_service_entitlement
from app.services.brain_world_state import build_world_state
from app.services.social_entitlement import normalize_posting_subscription, posting_subscription_active
from app.services.exception_observability import observe_suppressed


def _now(): return datetime.now(timezone.utc)
def _iso(v): return v.isoformat() if v is not None else None

def _storage(db, aid, key):
    row=(db.query(Storage).filter(Storage.account_id==aid,Storage.key==key)
         .order_by(Storage.id.desc()).first())
    if not row: return {}
    try: return json.loads(row.value or "{}")
    except Exception: return {}


SUPERVISOR_SNAPSHOT_KEY="client_supervisor_snapshot_v1"


def _campaign_requires_attention(status: str | None, settings_json: str | dict | None) -> bool:
    """Expose only actionable campaign lifecycle states as owner attention.

    Avito can terminally accept part of a feed while rejecting duplicate ads.
    Feed Factory records that outcome as ``no_retry_required``; replaying those
    duplicates is unsafe, so such a settled ``partial`` campaign must not stay
    red forever. Missing/ambiguous evidence remains fail-closed and visible.
    """
    state=str(status or '').strip().lower()
    if state in {'processing','submitted','scheduled','scheduled_waiting','pending_review'}:
        return True
    if state!='partial':
        return False
    try:
        raw=json.loads(settings_json or '{}') if isinstance(settings_json,str) else (settings_json or {})
        ff=(raw.get('feed_factory') or {}) if isinstance(raw,dict) else {}
        activation=ff.get('owner_activation') or {}
        report=activation.get('report') or {}
        repair=activation.get('repair_plan') or report.get('repair_plan') or {}
        watch=ff.get('post_publish_watch') or {}
        unresolved=activation.get('unresolved',report.get('unresolved'))
        if str(repair.get('state') or '')!='no_retry_required':
            return True
        if int(repair.get('retryable_count') or 0)>0:
            return True
        if int(repair.get('external_wait_count') or 0)>0:
            return True
        if bool(repair.get('owner_action_required')):
            return True
        if unresolved is None or int(unresolved or 0)>0:
            return True
        if str(watch.get('status') or '')!='observation_complete':
            return True
        if bool(watch.get('owner_action_required')):
            return True
        return False
    except Exception:
        return True


def load_supervisor_snapshot(db, aid: str, max_age_seconds: int | None=None) -> dict | None:
    """Read a fresh background supervisor snapshot; stale truth never masks live state."""
    import os
    ttl=int(max_age_seconds if max_age_seconds is not None else os.getenv("BORIS_CLIENT_SUPERVISOR_SNAPSHOT_TTL_SEC","120"))
    payload=_storage(db,str(aid),SUPERVISOR_SNAPSHOT_KEY)
    if not isinstance(payload,dict) or not payload.get("account_id") or not payload.get("checked_at"):
        return None
    try:
        checked=datetime.fromisoformat(str(payload["checked_at"]).replace("Z","+00:00"))
        if checked.tzinfo is None: checked=checked.replace(tzinfo=timezone.utc)
        age=max(0.0,(_now()-checked).total_seconds())
    except Exception:
        return None
    if age>max(5,ttl):
        return None
    out=dict(payload)
    out["snapshot_age_seconds"]=round(age,1)
    out["snapshot_source"]="background_client_supervisor"
    return out


def save_supervisor_snapshot(db, state: dict) -> None:
    """Persist one exact factual client-state projection for fast fleet reads."""
    aid=str((state or {}).get("account_id") or "").strip()
    if not aid: return
    raw=json.dumps(state,ensure_ascii=False,default=str)
    row=(db.query(Storage).filter(Storage.account_id==aid,Storage.key==SUPERVISOR_SNAPSHOT_KEY)
         .order_by(Storage.id.desc()).first())
    if row: row.value=raw
    else: db.add(Storage(account_id=aid,key=SUPERVISOR_SNAPSHOT_KEY,value=raw))
    db.flush()

def _paid_inbox(db, aid, now):
    rows=(db.query(AccountSlot).filter(AccountSlot.account_id==aid,AccountSlot.product=='inbox')
          .order_by(AccountSlot.id.desc()).all())
    explicit=[r for r in rows if r.paid_until is not None]
    active=[]
    for r in explicit:
        until=r.paid_until if r.paid_until.tzinfo else r.paid_until.replace(tzinfo=timezone.utc)
        if until>now and str(r.status or '') in {'connected','connecting','paid_empty'}: active.append(r)
    if active:
        r=max(active,key=lambda x:x.paid_until)
        return {'state':'active','source':'account_slots.paid_until','until':_iso(r.paid_until),'status':r.status}
    if explicit:
        r=max(explicit,key=lambda x:x.paid_until)
        return {'state':'expired','source':'account_slots.paid_until','until':_iso(r.paid_until),'status':r.status}
    return {'state':'unknown','source':'no_explicit_inbox_paid_until','until':None}

def _binding_products(db, aid):
    rows=db.execute(text("select distinct product from ai_bindings where account_id=:a order by product"),{'a':aid}).all()
    return [str(r[0]) for r in rows if r and r[0]]

def _resolve_client_period_state(explicit_active, explicit_expired, service_evidence: dict) -> str:
    """Resolve billing truth without inventing a paid period.

    Explicit paid evidence always wins. When no period exists, current service
    evidence distinguishes a real billing-accounting gap from a dormant legacy
    account that has no active service to supervise.
    """
    if explicit_active:
        return 'active'
    if explicit_expired:
        return 'expired'
    return 'billing_gap' if any(bool(v) for v in (service_evidence or {}).values()) else 'inactive_unprovisioned'

def candidate_accounts(db):
    # CLIENT_SUPERVISOR_AUTHORITATIVE_SCOPE_V3: exact same discovery/retirement
    # contract as production_contract_runner. Stale storage-only identities must
    # not become supervised clients, while credentialed real accounts must not
    # disappear merely because they have no active module binding.
    import os
    excluded={x.strip().lower() for x in os.getenv('BORIS_POLICY_EXCLUDED_ACCOUNTS','grant_kaluzhskaya_oblast_beton_plity_77392').split(',') if x.strip()}
    ids={str(r[0]) for r in db.execute(text("""
      WITH configured AS (
        SELECT account_id FROM account_slots WHERE status='connected'
        UNION SELECT account_id FROM ai_bindings WHERE account_id IS NOT NULL
        UNION SELECT account_id FROM mop_modes WHERE account_id IS NOT NULL
        UNION SELECT account_id FROM storage WHERE key IN ('kpi_settings','virtual_marketer_runtime','mop_crm_sales_settings')
        UNION SELECT default_account_id AS account_id FROM campaigns WHERE default_account_id IS NOT NULL
      )
      SELECT DISTINCT a.account_id FROM accounts a
       WHERE a.account_id IS NOT NULL AND btrim(a.account_id)<>''
         AND NOT EXISTS (SELECT 1 FROM reliability_kill_switches r WHERE r.account_id=a.account_id AND r.blocked=true AND r.module IN ('actions','background'))
         AND (EXISTS (SELECT 1 FROM configured c WHERE c.account_id=a.account_id)
              OR NULLIF(btrim(coalesce(a.avito_client_id,'')),'') IS NOT NULL
              OR a.avito_user_id IS NOT NULL
              OR NULLIF(btrim(coalesce(a.avito_login,'')),'') IS NOT NULL)
         AND NOT (
              coalesce(a.billing_mode,'manual')='manual'
              AND NULLIF(btrim(coalesce(a.avito_user_id,'')),'') IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM accounts canonical
                  WHERE canonical.account_id<>a.account_id
                    AND canonical.owner_user_id=a.owner_user_id
                    AND coalesce(canonical.billing_mode,'manual')='auto'
                    AND NULLIF(btrim(coalesce(canonical.avito_user_id,'')),'')
                        = NULLIF(btrim(coalesce(a.avito_user_id,'')),'')
              )
         )
    """)).all() if r and r[0]}
    return sorted(a for a in ids if a.lower() not in excluded and not a.lower().startswith(('qa_','qa-','__qa','test_','test-','testref_','demo_','sandbox_')))

def _operational_signals(db, aid: str, module_map: dict, now) -> dict:
    """CLIENT_SUPERVISOR_OPERATIONAL_SIGNALS_V1
    Owner-facing facts only; no mutations and no paid provider calls.
    """
    feed=(module_map.get('feed_factory') or {}).get('actual_state') or {}
    messages=(module_map.get('messages') or {}).get('actual_state') or {}
    crm=(module_map.get('crm') or {}).get('actual_state') or {}
    telephony=(module_map.get('telephony') or {}).get('actual_state') or {}
    react=(module_map.get('reactivation') or {}).get('actual_state') or {}
    analytics=(module_map.get('analytics') or {}).get('actual_state') or {}
    marketing=(module_map.get('marketing') or {}).get('actual_state') or {}
    campaign_rows=db.execute(text("""select status,settings_json,updated_at
      from campaigns where default_account_id=:a"""),{'a':aid}).mappings().all()
    campaigns={
      'relevant':sum(1 for r in campaign_rows if str(r.get('status') or '') not in {'draft','superseded','cancelled'}),
      'attention':sum(1 for r in campaign_rows if _campaign_requires_attention(r.get('status'),r.get('settings_json'))),
      'last_update':max((r.get('updated_at') for r in campaign_rows if r.get('updated_at') is not None),default=None),
    }
    # Account-scoped CRM truth comes from the canonical Avito deal scope.
    crm_live=db.execute(text("""select count(*) as deals,
      count(*) filter (where created_at>=date_trunc('day',now())) as deals_today,
      count(*) filter (where status not in ('won','lost','closed') and next_action_at is not null and next_action_at<now()) as overdue_next_actions
      from boris_crm_deals where avito_account_id=:a"""),{'a':aid}).mappings().first()
    calls=db.execute(text("""select count(*) as calls_24h,
      count(*) filter (where direction='inbound' and answered_at is null and ended_at>=now()-interval '24 hours') as missed_24h,
      count(*) filter (where callback_due_at is not null and callback_due_at<now() and coalesce(callback_status,'') not in ('done','completed','cancelled')) as overdue_callbacks
      from telephony_calls where account_id=:a and created_at>=now()-interval '24 hours'"""),{'a':aid}).mappings().first()
    leads=db.execute(text("""select count(*) as leads_24h,
      count(*) filter (where created_at>=date_trunc('day',now())) as leads_today
      from leads where account_id=:a and created_at>=now()-interval '24 hours'"""),{'a':aid}).mappings().first()
    return {
      'publications': {'queued':int(feed.get('queued') or 0),'running':int(feed.get('running') or 0),'stale_running':int(feed.get('stale_running') or 0),
        'waiting_external_24h':int(feed.get('waiting_external_24h') or 0),'retryable_failures_24h':int(feed.get('retryable_failures_24h') or 0),
        'campaigns_relevant':int((campaigns or {}).get('relevant') or 0),'campaigns_attention':int((campaigns or {}).get('attention') or 0),'last_campaign_update':_iso((campaigns or {}).get('last_update'))},
      'messages': {'inbound_24h':int(messages.get('inbound_24h') or 0),'outbound_24h':int(messages.get('outbound_24h') or 0),'unanswered':int(messages.get('actionable_unanswered_dialogs') or 0)},
      'leads': {'kpi_actual_today':marketing.get('actual_leads'),'target_today':marketing.get('target_leads_per_day'),
                'crm_leads_today':int((leads or {}).get('leads_today') or 0),'crm_leads_24h':int((leads or {}).get('leads_24h') or 0)},
      'crm': {'deals':int((crm_live or {}).get('deals') or crm.get('deals') or 0),'deals_today':int((crm_live or {}).get('deals_today') or crm.get('deals_today') or 0),
        'overdue_next_actions':int((crm_live or {}).get('overdue_next_actions') or 0),'overdue_tasks':int(crm.get('overdue_tasks') or 0)},
      'calls': {'last_24h':int((calls or {}).get('calls_24h') or telephony.get('calls_24h') or 0),'missed_24h':int((calls or {}).get('missed_24h') or telephony.get('missed_24h') or 0),'overdue_callbacks':int((calls or {}).get('overdue_callbacks') or telephony.get('overdue_callbacks') or 0)},
      'reactivation': {'active_candidates':int(react.get('active_candidates') or 0),'ready_messages':int(react.get('ready_messages') or 0),'sent_messages':int(react.get('sent_messages') or 0),'blocked_messages':int(react.get('blocked_messages') or 0),'scheduler_state':react.get('scheduler_state')},
      'kpi': {'target_leads_per_day':marketing.get('target_leads_per_day'),'actual_leads':marketing.get('actual_leads'),'cpl_today':marketing.get('cpl_today'),'active_items':marketing.get('active_items'),'stats_date':marketing.get('stats_date')},
      'analytics': {'daily_stats_snapshots_today':int(analytics.get('daily_stats_snapshots_today') or 0)},
    }

def build_client_state(db, aid: str) -> dict:
    now=_now()
    acc=db.query(Account).filter(Account.account_id==aid).first()
    # CLIENT_SUPERVISOR_MASTER_SUSPENSION_V1:
    # Tenant-wide actions kill switch has higher authority than old module-specific
    # entitlements. A suspended/non-paying client must never appear "active" just
    # because a separate MOP/manager package still has a later paid_until.
    _suspension_row=db.execute(text("""
      select blocked,reason,updated_at
        from reliability_kill_switches
       where account_id=:a and module='actions'
       order by id desc limit 1
    """),{'a':aid}).mappings().first()
    account_suspended=bool(_suspension_row and _suspension_row.get('blocked'))
    suspension={
      'blocked':account_suspended,
      'reason':(_suspension_row.get('reason') if _suspension_row else None),
      'updated_at':_iso(_suspension_row.get('updated_at')) if _suspension_row else None,
      'source':'reliability_kill_switches:actions' if _suspension_row else None,
    }
    bindings=_binding_products(db,aid)
    inbox=_paid_inbox(db,aid,now)
    marketing=marketing_service_entitlement(db,aid,now=now)
    mop=has_entitlement(db,aid,'mop'); rop=has_entitlement(db,aid,'rop'); ecs=has_entitlement(db,aid,'ecs')
    social_raw=_storage(db,aid,'posting_subscription')
    social_norm,_social_changes=normalize_posting_subscription(social_raw)
    social_active=posting_subscription_active(social_norm,today=now.date())
    social={
      'state':'active' if social_active else ('expired' if social_raw else 'unknown'),
      'plan':social_norm.get('plan') if isinstance(social_norm,dict) else None,
      'platforms':social_norm.get('platforms') if isinstance(social_norm,dict) else None,
      'paid_at':social_norm.get('paid_at') if isinstance(social_norm,dict) else None,
      'period_days':social_norm.get('period_days') if isinstance(social_norm,dict) else None,
      'posts_per_day':social_norm.get('posts_per_day') if isinstance(social_norm,dict) else None,
      'source':'storage:posting_subscription',
    }
    kpi=_storage(db,aid,'kpi_settings'); autopilot=_storage(db,aid,'autopilot_settings')
    react=db.execute(text("select enabled,mode,daily_limit,disabled_reason from reactivation_settings where account_id=:a"),{'a':aid}).mappings().first()
    react=dict(react) if react else None

    explicit_active=[]; explicit_expired=[]
    for name,e in [('inbox',inbox),('marketing',marketing),('social',social)]:
        (explicit_active if e.get('state')=='active' else explicit_expired if e.get('state')=='expired' else []).append(name)
    for name,e in [('mop',mop),('rop',rop),('ecs',ecs)]:
        if e.get('state')=='known':
            (explicit_active if e.get('allowed') else explicit_expired).append(name)
    # CLIENT_SUPERVISOR_PERIOD_RESOLUTION_V1: absence of paid evidence is not
    # one generic "unknown" state. Distinguish a dormant legacy account with no
    # current service from a real operating contour whose billing evidence is
    # missing. Activity never proves payment; it only proves that a billing gap
    # must not be silently treated as inactive.
    service_evidence={
      'slot_configured':bool(db.execute(text("select 1 from account_slots where account_id=:a and coalesce(status,'')<>'released' limit 1"),{'a':aid}).first()),
      'binding_configured':bool(bindings),
      'mop_mode_recent_30d':bool(db.execute(text("select 1 from mop_modes where account_id=:a and coalesce(queue_enabled_at,updated_at)>=now()-interval '30 days' limit 1"),{'a':aid}).first()),
      'active_campaign':bool(db.execute(text("select 1 from campaigns where default_account_id=:a and status not in ('draft','superseded','cancelled') limit 1"),{'a':aid}).first()),
      'messages_recent_30d':bool(db.execute(text("select 1 from messenger_messages where account_id=:a and to_timestamp(avito_created_at)>=now()-interval '30 days' limit 1"),{'a':aid}).first()),
      'crm_recent_30d':bool(db.execute(text("select 1 from boris_crm_deals where avito_account_id=:a and created_at>=now()-interval '30 days' limit 1"),{'a':aid}).first()),
      'leads_recent_30d':bool(db.execute(text("select 1 from leads where account_id=:a and created_at>=now()-interval '30 days' limit 1"),{'a':aid}).first()),
      'telephony_configured':bool(db.execute(text("select 1 from telephony_provider_configs where account_id=:a limit 1"),{'a':aid}).first()),
    }
    client_state=(
        'suspended'
        if account_suspended
        else _resolve_client_period_state(explicit_active,explicit_expired,service_evidence)
    )

    expected=set()
    if inbox.get('state')=='active': expected.add('messages')
    if mop.get('allowed') and 'mop' in bindings: expected.update(('mop','crm'))
    if rop.get('allowed') and 'rop' in bindings: expected.add('rop')
    if marketing.get('state')=='active' and kpi:
        expected.update(('marketing','feed_factory','analytics'))
    # CLIENT_SUPERVISOR_SOCIAL_PAID_SCOPE_V1: a paid Social subscription is
    # expected service even before posting_projects exists. This keeps missing
    # channel/project setup visible instead of silently hiding a paid module.
    if social.get('state')=='active': expected.add('social')
    if react and react.get('enabled') is not False and mop.get('allowed'): expected.add('reactivation')
    # Telephony is expected only with actual account-scoped provider/config evidence.
    tel=bool(service_evidence.get('telephony_configured'))
    if tel: expected.add('telephony')
    if account_suspended:
        expected.clear()

    world=build_world_state(db,account_id=aid)
    module_map={m['module']:m for m in world.get('modules') or []}
    modules=[]; needs_owner=[]; internal_bad=[]; human_work=[]
    if client_state=='billing_gap':
        needs_owner.append({
          'module':'billing','reason':'PAID_PERIOD_EVIDENCE_MISSING',
          'owner_action_required':True,
          'evidence':{k:v for k,v in service_evidence.items() if v},
        })
    for name in sorted(expected):
        m=module_map.get(name) or {'module':name,'health':'blocked','diagnosis':{'classification':'MODULE_NOT_IN_BRAIN'}}
        d=m.get('diagnosis') or {}; h=str(m.get('health') or 'blocked')
        owner=bool(d.get('owner_action_required'))
        row={'module':name,'health':h,'classification':d.get('classification'),'owner_action_required':owner,
             'safe_action':d.get('safe_action'),'freshness':m.get('freshness') or {},'actual_state':m.get('actual_state') or {}}
        modules.append(row)
        # CLIENT_SUPERVISOR_WORKING_DEGRADED_V1: KPI off-target with an
        # observation/optimization plan is business work in progress, not a broken
        # client contour. Only actionable recovery/critical states become internal
        # incidents. Also do not ask the owner for MOP entitlement through the
        # generic Messages adapter when this client did not purchase/enable MOP.
        _class=str(d.get('classification') or '')
        _safe=str(d.get('safe_action') or '')
        _dep=str(d.get('dependency_state') or '')
        _owner=owner
        if name=='messages' and _class=='UNANSWERED_MOP_ENTITLEMENT_WAIT' and 'mop' not in expected:
            _owner=False
        if _owner:
            needs_owner.append({'module':name,'reason':d.get('classification')})
        elif _dep=='waiting_external':
            # CLIENT_SUPERVISOR_EXTERNAL_DEPENDENCY_V1: a client/platform input
            # that BORIS cannot safely invent is not an internal runtime failure.
            # Keep it visible in module health, but do not create a self-heal loop.
            pass
        elif h=='critical' or (h in {'degraded','blocked'} and _safe not in {
            '','observe_existing_plan','continue_account_scoped_kpi_recovery',
            'wait','none','surface_overdue_tasks'
        }):
            internal_bad.append({'module':name,'reason':d.get('classification'),'safe_action':d.get('safe_action')})

    q=db.query(BackgroundJob).filter(BackgroundJob.account_id==aid)
    queue={'queued':q.filter(BackgroundJob.status=='queued').count(),
           'running':q.filter(BackgroundJob.status.in_(('running','validating'))).count()}
    # CLIENT_SUPERVISOR_UNRESOLVED_FAILURE_TRUTH_V1:
    # a durable self-healed Feed Factory failure stays in history but must not
    # remain a red owner-facing outage for the next 24 hours. Preserve the raw
    # count and expose recovered/recovering/external classes separately.
    _failed_raw=int(db.execute(text("select count(*) from background_jobs where account_id=:a and status='failed' and finished_at>=now()-interval '24 hours'"),{'a':aid}).scalar() or 0)
    _feed_failed_raw=int(db.execute(text("""select count(*) from background_jobs
        where account_id=:a and kind in ('campaign_prepare','campaign_enhance','campaign_deliver')
          and status='failed' and finished_at>=now()-interval '24 hours'"""),{'a':aid}).scalar() or 0)
    _feed_state=(module_map.get('feed_factory') or {}).get('actual_state') or {}
    _feed_unresolved=int(_feed_state.get('unresolved_failed_24h') or 0)
    _non_feed_failed=max(0,_failed_raw-_feed_failed_raw)
    queue['failed_24h_raw']=_failed_raw
    queue['failed_24h']=_non_feed_failed+min(_feed_failed_raw,_feed_unresolved)
    queue['feed_failed_24h_raw']=_feed_failed_raw
    queue['feed_recovered_failed_24h']=int(_feed_state.get('recovered_failed_24h') or 0)
    queue['feed_recovering_failed_24h']=int(_feed_state.get('recovering_failed_24h') or 0)
    queue['feed_waiting_external_failed_24h']=int(_feed_state.get('waiting_external_failed_24h') or 0)
    queue['stale_running']=int(db.execute(text("select count(*) from background_jobs where account_id=:a and status in ('running','validating') and started_at<now()-interval '20 minutes'"),{'a':aid}).scalar() or 0)

    operational=_operational_signals(db,aid,module_map,now)
    marketing_actual=(module_map.get('marketing') or {}).get('actual_state') or {}
    # CLIENT_SUPERVISOR_KPI_TRUTH_V2: canonical marketing actual_state uses
    # actual_leads for today's Avito contacts. Bind owner plan/fact to that truth.
    _actual_leads=marketing_actual.get('actual_leads')
    _stats_date=marketing_actual.get('stats_date')
    plan_fact={'target_leads_per_day':kpi.get('target_leads_per_day'),
               'max_cost_per_lead_rub':kpi.get('max_cost_per_lead_rub'),
               'daily_budget_limit_rub':kpi.get('daily_budget_limit_rub'),
               'contacts_today':_actual_leads,
               'actual_leads':_actual_leads,
               'cpl_today':marketing_actual.get('cpl_today'),
               'active_items':marketing_actual.get('active_items'),
               'stats_date':_stats_date,
               'business_date':marketing_actual.get('business_date'),
               'actual_leads_observed':marketing_actual.get('actual_leads_observed'),
               'provider_stats_current':marketing_actual.get('provider_stats_current'),
               'provider_stats_lagging':marketing_actual.get('provider_stats_lagging'),
               'fact_source':'canonical_marketing_actual_leads',
               'autopilot_mode':autopilot.get('mode')}
    # CLIENT_SUPERVISOR_OVERDUE_ACTIONS_V1: explicit overdue facts for the
    # client-control screen; derived from the canonical operational projection.
    overdue_actions=[]
    for _name,_value in (
      ('crm_overdue_tasks',operational['crm'].get('overdue_tasks')),
      ('crm_overdue_next_actions',operational['crm'].get('overdue_next_actions')),
      ('telephony_overdue_callbacks',operational['calls'].get('overdue_callbacks')),
      ('feed_stale_running',operational['publications'].get('stale_running')),
      ('feed_retryable_failures_24h',operational['publications'].get('retryable_failures_24h')),
    ):
        # CLIENT_SUPERVISOR_EXPECTED_WORK_SCOPE_V1: historical CRM/call tasks
        # remain in storage for audit, but they are not current client work when
        # the corresponding paid module is not expected for this account.
        if _name.startswith('crm_') and 'crm' not in expected: continue
        if _name.startswith('telephony_') and 'telephony' not in expected: continue
        if _name.startswith('feed_') and 'feed_factory' not in expected: continue
        try:
            if int(_value or 0)>0: overdue_actions.append({'kind':_name,'count':int(_value)})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=367)
    # CLIENT_SUPERVISOR_MOP_MANAGER_HANDOFF_V1: a paid MOP human handoff with
    # a dedicated CRM task is normal manager work, not owner work. Keep it visible
    # in the client workspace while preserving internal failures separately.
    if client_state=='active' and 'mop' in expected:
        _mop_actual=(module_map.get('mop') or {}).get('actual_state') or {}
        _mop_managed=int(_mop_actual.get('managed_human_required') or 0)
        if _mop_managed>0:
            _examples=list(_mop_actual.get('managed_human_examples') or [])
            human_work.append({
              'module':'mop','reason':'MOP_MANAGER_HANDOFF_ACTIVE',
              'safe_action':'surface_in_manager_workspaces','count':_mop_managed,
              'manager_control':{
                'assigned_tasks':sum(1 for x in _examples if x.get('manager_user_id')),
                'owner_action_required':False,'external_action':False,
              },
              'items':[{
                'draft_id':x.get('draft_id'),'deal_id':x.get('deal_id'),
                'task_id':x.get('task_id'),'assigned_user_id':x.get('manager_user_id'),
                'due_at':_iso(x.get('due_at')) if not isinstance(x.get('due_at'),str) else x.get('due_at'),
              } for x in _examples[:5]],
            })

    # CLIENT_SUPERVISOR_OVERDUE_HUMAN_WORK_V3: boris_crm_tasks is the canonical
    # manager work list, not an autonomous transport queue. An overdue call/task
    # stays visible in CRM/Command Center, but does not become an internal P1
    # merely because BORIS has no connected channel that can legally execute it.
    # Actual MOP/CRM runtime failures are still caught by module health and open
    # ControlIncident rows below. This keeps human sales work distinct from system
    # self-heal obligations instead of hiding either class.
    if client_state=='active' and 'crm' in expected:
        _crm_overdue=int(operational['crm'].get('overdue_tasks') or 0)
        _crm_rows=db.execute(text("""select t.id,t.title,t.due_at,d.avito_chat_id,t.source,
                 t.assigned_user_id,d.responsible_user_id,
                 (select count(*) from boris_crm_activities a
                   where a.source='brain_crm_overdue_reminder'
                     and coalesce(a.metadata_json->>'task_id','')=t.id::text) as reminder_count,
                 (select max(a.created_at) from boris_crm_activities a
                   where a.source='brain_crm_overdue_reminder'
                     and coalesce(a.metadata_json->>'task_id','')=t.id::text) as last_reminded_at,
                 (select a.metadata_json->>'escalation_level' from boris_crm_activities a
                   where a.source='brain_crm_overdue_reminder'
                     and coalesce(a.metadata_json->>'task_id','')=t.id::text
                   order by a.created_at desc,a.id desc limit 1) as escalation_level
          from boris_crm_tasks t join boris_crm_deals d on d.id=t.deal_id
          where d.avito_account_id=:a and t.status='open' and t.due_at<now()
          order by t.due_at,t.id limit 20"""),{'a':aid}).mappings().all()
        _crm_oldest=(_crm_rows[0].get('due_at') if _crm_rows else None)
        if _crm_overdue and _crm_oldest is not None:
            _age=now-(_crm_oldest if _crm_oldest.tzinfo else _crm_oldest.replace(tzinfo=timezone.utc))
            if _age.total_seconds()>=3600:
                _chat=sum(1 for x in _crm_rows if x.get('avito_chat_id'))
                _call=max(0,_crm_overdue-_chat)
                _reminded_tasks=sum(1 for x in _crm_rows if int(x.get('reminder_count') or 0)>0)
                _reminder_events=sum(int(x.get('reminder_count') or 0) for x in _crm_rows)
                _last_reminded=max((x.get('last_reminded_at') for x in _crm_rows if x.get('last_reminded_at')),default=None)
                _levels={str(x.get('escalation_level') or '') for x in _crm_rows if x.get('escalation_level')}
                _unassigned=sum(1 for x in _crm_rows if x.get('assigned_user_id') is None and x.get('responsible_user_id') is None)
                human_work.append({'module':'crm','reason':'CRM_OVERDUE_HUMAN_TASKS',
                  'safe_action':'surface_in_manager_workspaces','count':_crm_overdue,'oldest_due_at':_iso(_crm_oldest),
                  'chat_backed_count':_chat,'call_only_count':_call,
                  'manager_control':{'reminded_tasks':_reminded_tasks,'reminder_events':_reminder_events,
                    'last_reminded_at':_iso(_last_reminded),'levels':sorted(_levels),'unassigned_tasks':_unassigned,
                    'owner_action_required':False,'external_action':False},
                  'items':[{'task_id':int(x['id']),'title':str(x.get('title') or 'Задача'),'due_at':_iso(x.get('due_at')),
                            'channel':'messages' if x.get('avito_chat_id') else 'crm_call_work','source':x.get('source'),
                            'assigned_user_id':x.get('assigned_user_id') or x.get('responsible_user_id'),
                            'reminder_count':int(x.get('reminder_count') or 0),'last_reminded_at':_iso(x.get('last_reminded_at')),
                            'escalation_level':x.get('escalation_level')} for x in _crm_rows[:5]]})
    incidents=db.query(ControlIncident).filter(ControlIncident.account_id==aid,ControlIncident.state=='open').all()
    for x in incidents:
        if x.priority in {'P0','P1'} and not any(n.get('module')==x.module for n in needs_owner):
            # MONEY_GUARD is deliberately owner policy; everything else is internal.
            if str(x.incident_type or '').startswith('MONEY_GUARD_'):
                needs_owner.append({'module':x.module,'reason':x.incident_type})
            else: internal_bad.append({'module':x.module,'reason':x.incident_type,'safe_action':None})
    if client_state in {'expired','inactive_unprovisioned','suspended'}: overall='inactive_expected'
    elif client_state=='billing_gap': overall='waiting_owner'
    elif internal_bad: overall='needs_self_heal'
    elif needs_owner: overall='waiting_owner'
    elif human_work or any(m['health'] not in {'pass'} for m in modules): overall='working'
    else: overall='healthy'
    return {'account_id':aid,'name':getattr(acc,'name',None),'client_state':client_state,
            'suspension':suspension,
            'paid_periods':{'inbox':inbox,'marketing':marketing,'mop':mop,'rop':rop,'ecs':ecs,'social':social,
                            'service_evidence':service_evidence},
            'bindings':bindings,'expected_modules':sorted(expected),'modules':modules,'plan_fact':plan_fact,'operational':operational,'overdue_actions':overdue_actions,
            'queue':queue,'reactivation_settings':react,'needs_owner':needs_owner,'internal_problems':internal_bad,'human_work':human_work,
            'overall':overall,'checked_at':now.isoformat()}

def build_fleet_state(db) -> dict:
    rows=[]; snapshot_hits=0; live_reads=0
    for aid in candidate_accounts(db):
        try:
            snap=load_supervisor_snapshot(db,aid)
            if snap is not None:
                rows.append(snap); snapshot_hits+=1
            else:
                rows.append(build_client_state(db,aid)); live_reads+=1
        except Exception as exc:
            db.rollback(); rows.append({'account_id':aid,'overall':'read_error','error_type':type(exc).__name__,'checked_at':_now().isoformat()})
    counts={k:sum(1 for x in rows if x.get('overall')==k) for k in ('healthy','working','waiting_owner','needs_self_heal','degraded','inactive_expected','unknown_period','read_error')}
    return {'status':'critical' if counts['read_error'] or counts['needs_self_heal'] else ('degraded' if counts['degraded'] else 'pass'),
            'counts':counts,'accounts':rows,'checked_at':_now().isoformat(),
            'snapshot':{'fresh_hits':snapshot_hits,'live_reads':live_reads,'ttl_seconds':120},
            'rule':'paid-period evidence first; inactive/expired is not an incident; fresh background snapshot preferred, stale snapshot never hides live state'}
