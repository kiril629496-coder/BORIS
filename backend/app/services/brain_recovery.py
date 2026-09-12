"""BORIS System Brain safe recovery engine.

Only L1 internal self-heal operations live here. No paid AI calls, no client
messages, no money changes and no irreversible business mutations are allowed.
Every action returns deterministic before/result/after evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta


def _mop_safe_default_config(cfg, entitlement_allowed: bool):
    # MOP_AUTOSEND_SAFE_DEFAULT_V1:
    # Legacy settings without auto_send already behave as manual approval in the
    # settings API. Materialize that existing fail-closed default so owner policy
    # is not required merely to preserve the no-auto-send behavior.
    if not entitlement_allowed or not isinstance(cfg, dict) or "auto_send" in cfg:
        return None
    out=dict(cfg)
    out["auto_send"]=False
    out["auto_send_source"]="system_safe_default"
    out["auto_send_policy_version"]="MOP_AUTOSEND_SAFE_DEFAULT_V1"
    return out


def _ensure_mop_handoff_manager_tasks(db) -> dict:
    """Create one CRM manager task for each unresolved paid MOP human handoff.

    DB-only and idempotent: no provider call, client message, AI call or spend.
    """
    from app.client_config.entitlements import has_entitlement
    from sqlalchemy import text

    rows=db.execute(text("""
      SELECT md.id AS draft_id,md.account_id,md.avito_chat_id,md.updated_at,
             cd.id AS deal_id,cd.owner_user_id,cd.responsible_user_id,cd.contact_id
        FROM mop_drafts md
        LEFT JOIN LATERAL (
          SELECT d.id,d.owner_user_id,d.responsible_user_id,d.contact_id
            FROM boris_crm_deals d
           WHERE d.avito_account_id=md.account_id AND d.avito_chat_id=md.avito_chat_id
             AND d.status='open'
           ORDER BY d.id DESC LIMIT 1
        ) cd ON true
       WHERE md.status='human_required'
         -- MOP_HANDOFF_ACK_IS_NOT_MANAGER_COMPLETION_V1:
         -- The MOP may send a safe acknowledgement and then enter human_required.
         -- That acknowledgement becomes the latest outgoing message, but it does
         -- NOT mean a manager performed the follow-up. Require the source inbound
         -- and suppress the task only when a newer outgoing appears after handoff.
         AND EXISTS (
           SELECT 1 FROM messenger_messages src
            WHERE src.account_id=md.account_id
              AND src.avito_chat_id=md.avito_chat_id
              AND src.avito_message_id=md.avito_message_id
              AND lower(src.direction) LIKE 'in%'
         )
         AND NOT EXISTS (
           SELECT 1 FROM messenger_messages outm
            WHERE outm.account_id=md.account_id
              AND outm.avito_chat_id=md.avito_chat_id
              AND lower(outm.direction) LIKE 'out%'
              AND COALESCE(outm.msg_type,'') <> 'system'
              AND to_timestamp(outm.avito_created_at) > md.updated_at
         )
       ORDER BY md.updated_at,md.id LIMIT 100
    """)).mappings().all()

    created=updated=already_managed=skipped_unpaid=skipped_disabled=missing_manager=missing_deal=0
    items=[]
    for r in rows:
        aid=str(r.get('account_id') or '')
        entitlement=has_entitlement(db,aid,'mop') or {}
        if not entitlement.get('allowed'):
            skipped_unpaid+=1
            continue
        raw=db.execute(text(
            "SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"
        ),{'a':aid}).scalar()
        try:
            cfg=json.loads(raw) if isinstance(raw,str) else (raw or {})
        except Exception:
            cfg={}
        # MOP_DISABLED_HUMAN_HANDOFF_STILL_OWNED_V1:
        # Disabling the AI seller must never orphan a conversation already in
        # human_required. Route it to the responsible manager with a DB-only
        # task; do not re-enable MOP and do not send anything externally.
        if not bool((cfg or {}).get('mop_enabled',(cfg or {}).get('enabled',True))):
            pass
        deal_id=r.get('deal_id')
        if not deal_id:
            missing_deal+=1
            continue
        manager_id=r.get('responsible_user_id') or r.get('owner_user_id')
        if not manager_id:
            missing_manager+=1
            continue

        task=db.execute(text("""
          SELECT id,assigned_user_id,due_at FROM boris_crm_tasks
           WHERE deal_id=:d AND status='open' AND source='mop_handoff_recovery'
           ORDER BY id DESC LIMIT 1
        """),{'d':deal_id}).mappings().first()
        due=db.execute(text("SELECT now()+interval '15 minutes'")).scalar()
        if task:
            if task.get('assigned_user_id') is None or task.get('due_at') is None:
                db.execute(text("""
                  UPDATE boris_crm_tasks
                     SET assigned_user_id=COALESCE(assigned_user_id,:m),
                         due_at=COALESCE(due_at,:due)
                   WHERE id=:i
                """),{'m':manager_id,'due':due,'i':task['id']})
                updated+=1
            else:
                already_managed+=1
            task_id=int(task['id'])
        else:
            task_id=int(db.execute(text("""
              INSERT INTO boris_crm_tasks(
                owner_user_id,deal_id,contact_id,title,description,assigned_user_id,
                due_at,status,source,created_at
              ) VALUES(
                :u,:d,:c,'Связаться с клиентом',
                'Передано BORIS после безопасной MOP-эскалации: требуется действие менеджера',
                :m,:due,'open','mop_handoff_recovery',now()
              ) RETURNING id
            """),{'u':r.get('owner_user_id'),'d':deal_id,'c':r.get('contact_id'),
                   'm':manager_id,'due':due}).scalar())
            created+=1
        db.execute(text("""
          UPDATE boris_crm_deals
             SET next_action_at=CASE
                   WHEN next_action_at IS NULL OR next_action_at>:due THEN :due
                   ELSE next_action_at END,
                 updated_at=now()
           WHERE id=:d
        """),{'due':due,'d':deal_id})
        items.append({'draft_id':int(r['draft_id']),'deal_id':int(deal_id),
                      'task_id':task_id,'manager_user_id':int(manager_id)})
    if created or updated or items:
        db.commit()
    return {
      'policy_version':'MOP_HUMAN_HANDOFF_MANAGER_TASK_V1',
      'scanned':len(rows),'created':created,'updated':updated,
      'already_managed':already_managed,'skipped_unpaid':skipped_unpaid,
      'skipped_disabled':skipped_disabled,'missing_manager':missing_manager,
      'missing_deal':missing_deal,'changed':created+updated,'items':items,
    }


def _safe_mop_recovery(db) -> dict:
    from app.mop_core import recover_stale_analyzing
    from app.client_config.entitlements import has_entitlement
    from sqlalchemy import text

    before=int(db.execute(text("select count(*) from mop_drafts where status='analyzing' and coalesce(updated_at,created_at)<now()-interval '30 minutes'" )).scalar() or 0)
    stale_result=recover_stale_analyzing(db,stale_minutes=30,limit=100) or {}
    after=int(db.execute(text("select count(*) from mop_drafts where status='analyzing' and coalesce(updated_at,created_at)<now()-interval '30 minutes'" )).scalar() or 0)

    rows=db.execute(text("""
      SELECT DISTINCT ON (s.account_id) s.id,s.account_id,s.value
        FROM storage s
       WHERE s.key='mop_crm_sales_settings'
         AND EXISTS (
           SELECT 1 FROM mop_drafts d
            WHERE d.account_id=s.account_id AND d.status='draft_ready'
              AND EXISTS (
                SELECT 1 FROM messenger_messages m
                 WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
                   AND lower(m.direction) LIKE 'in%'
                   AND m.avito_created_at=(
                     SELECT max(m2.avito_created_at) FROM messenger_messages m2
                      WHERE m2.account_id=d.account_id AND m2.avito_chat_id=d.avito_chat_id
                   )
              )
         )
       ORDER BY s.account_id,s.id DESC
    """)).mappings().all()

    materialized=[]; skipped_unpaid=0; skipped_explicit=0; skipped_invalid=0
    for row in rows:
        aid=str(row.get("account_id") or "")
        try:
            cfg=json.loads(row.get("value") or "{}") if isinstance(row.get("value"),str) else (row.get("value") or {})
        except Exception:
            skipped_invalid+=1
            continue
        entitlement=has_entitlement(db,aid,"mop") or {}
        updated=_mop_safe_default_config(cfg,bool(entitlement.get("allowed")))
        if updated is None:
            if isinstance(cfg,dict) and "auto_send" in cfg:
                skipped_explicit+=1
            elif not entitlement.get("allowed"):
                skipped_unpaid+=1
            else:
                skipped_invalid+=1
            continue
        db.execute(text("UPDATE storage SET value=:v WHERE id=:i"),
                   {"v":json.dumps(updated,ensure_ascii=False),"i":int(row["id"])})
        materialized.append({"account_id":aid,"storage_id":int(row["id"]),"auto_send":False})
    if materialized:
        db.commit()

    # MOP_STALE_NONTERMINAL_RECONCILE_V1:
    # Close old work only from durable chat history. No outbound send happens here.
    # If a newer incoming/outgoing message exists, the old draft is obsolete.
    # If an exact send_failed draft is >24h old, late auto-send is unsafe: hand off.
    stale_rows=db.execute(text("""
      SELECT d.id,d.account_id,d.avito_chat_id,d.avito_message_id,d.status,d.created_at,
             extract(epoch from (now()-d.created_at))::bigint AS age_sec,
             src.avito_created_at AS source_ts,
             src.msg_type AS source_msg_type,
             latest.avito_message_id AS latest_message_id,
             latest.direction AS latest_direction,
             latest.avito_created_at AS latest_ts
        FROM mop_drafts d
        LEFT JOIN LATERAL (
          SELECT m.avito_created_at,m.msg_type
            FROM messenger_messages m
           WHERE m.account_id=d.account_id
             AND m.avito_message_id=d.avito_message_id
           ORDER BY m.id DESC LIMIT 1
        ) src ON TRUE
        LEFT JOIN LATERAL (
          SELECT m.avito_message_id,m.direction,m.avito_created_at
            FROM messenger_messages m
           WHERE m.account_id=d.account_id
             AND m.avito_chat_id=d.avito_chat_id
           ORDER BY m.avito_created_at DESC NULLS LAST,m.id DESC LIMIT 1
        ) latest ON TRUE
       WHERE d.status IN ('new','draft_ready','send_failed')
       ORDER BY d.updated_at,d.id
       LIMIT 300
    """)).mappings().all()
    stale_closed_answered=[]; stale_closed_superseded=[]; stale_late_handoff=[]; system_event_closed=[]
    from app import mop_core as _mcstale
    for rr in stale_rows:
        did=int(rr['id']); aid=str(rr.get('account_id') or '')
        src_ts=rr.get('source_ts'); latest_ts=rr.get('latest_ts')
        latest_id=str(rr.get('latest_message_id') or '')
        draft_mid=str(rr.get('avito_message_id') or '')
        direction=str(rr.get('latest_direction') or '').lower()
        status=str(rr.get('status') or '')
        # MOP_SYSTEM_EVENT_SELFHEAL_V1: even if an old/legacy path leaked an
        # Avito system notification into MOP, close it deterministically. This
        # never sends externally and prevents dead cards on non-dialogue events.
        if str(rr.get('source_msg_type') or '').lower()=='system':
            row,_=_mcstale.set_status(
                db,did,'no_reply_required',(status,),'closed_no_reply',
                channel='mop_recovery',actor_type='system',actor_id='brain_system_event_cleanup',
                payload='Avito system notification is not client dialogue',
                meta={'policy_version':'MOP_SYSTEM_EVENT_SELFHEAL_V1','external_action':False},
            )
            if row is not None: system_event_closed.append(did)
            continue
        if src_ts is not None and latest_ts is not None and latest_id and latest_id != draft_mid:
            event='answered_externally' if direction.startswith('out') else 'closed_no_reply'
            payload=(
                'newer outgoing message already completed the chat'
                if direction.startswith('out')
                else 'newer incoming message superseded stale draft'
            )
            row,_=_mcstale.set_status(
                db,did,'no_reply_required',(status,),event,
                channel='mop_recovery',actor_type='system',actor_id='brain_stale_reconcile',
                payload=payload,
                meta={'policy_version':'MOP_STALE_NONTERMINAL_RECONCILE_V1','external_action':False},
            )
            if row is not None:
                (stale_closed_answered if direction.startswith('out') else stale_closed_superseded).append(did)
            continue
        if status=='send_failed' and int(rr.get('age_sec') or 0)>24*3600:
            entitlement=has_entitlement(db,aid,'mop') or {}
            if not entitlement.get('allowed'):
                continue
            row,_=_mcstale.set_status(
                db,did,'human_required',('send_failed',),'handed_to_human',
                channel='mop_recovery',actor_type='system',actor_id='brain_stale_reconcile',
                payload='late auto-retry suppressed after 24h; manager follow-up required',
                meta={'policy_version':'MOP_STALE_NONTERMINAL_RECONCILE_V1',
                      'external_action':False,'reason':'stale_send_failed'},
            )
            if row is not None: stale_late_handoff.append(did)
    if stale_closed_answered or stale_closed_superseded or stale_late_handoff or system_event_closed:
        db.commit()

    # MOP_CHAT_FORBIDDEN_EXTERNAL_WAIT_V1:
    # A chat-specific Avito 403 is not retryable by BORIS. Repeating the same
    # send can never heal access and only creates noise. Move the exact failed
    # draft to waiting_external while preserving the reply and audit trail.
    forbidden_rows=db.execute(text("""
      SELECT id FROM mop_drafts
       WHERE status='send_failed'
         AND coalesce(send_error,'') LIKE 'Avito 403%'
       ORDER BY updated_at,id LIMIT 100
    """)).mappings().all()
    forbidden_wait=[]
    if forbidden_rows:
        from app import mop_core as _mc403
        for rr in forbidden_rows:
            did=int(rr['id'])
            row,_=_mc403.set_status(
                db,did,'waiting_external',('send_failed',),'provider_deferred',
                channel='mop_recovery',actor_type='system',actor_id='brain',
                payload='Avito 403 chat access denied; no blind retry',
                meta={'reason':'chat_forbidden_403','retry_safe':False,'owner_action_required':False})
            if row is not None: forbidden_wait.append(did)

    handoff=_ensure_mop_handoff_manager_tasks(db)

    return {
      "action":"recover_mop_runtime_and_safe_policy_defaults",
      "before":{"stale_analyzing":before},
      "result":{
        **stale_result,
        "safe_policy_materialized":len(materialized),
        "safe_policy_accounts":materialized,
        "skipped_unpaid":skipped_unpaid,
        "skipped_explicit":skipped_explicit,
        "skipped_invalid":skipped_invalid,
        "changed":(
            len(materialized)+len(forbidden_wait)+len(system_event_closed)
            +len(stale_closed_answered)+len(stale_closed_superseded)+len(stale_late_handoff)
            +int(handoff.get("changed") or 0)
        ),
        "system_event_closed":len(system_event_closed),
        "system_event_closed_ids":system_event_closed,
        "stale_closed_answered":len(stale_closed_answered),
        "stale_closed_answered_ids":stale_closed_answered,
        "stale_closed_superseded":len(stale_closed_superseded),
        "stale_closed_superseded_ids":stale_closed_superseded,
        "stale_late_handoff":len(stale_late_handoff),
        "stale_late_handoff_ids":stale_late_handoff,
        "chat_forbidden_waiting_external":len(forbidden_wait),
        "chat_forbidden_draft_ids":forbidden_wait,
        "manager_handoff":handoff,
      },
      "after":{"stale_analyzing":after},
      "passed":after<=before,
      "marker":"MOP_STALE_ANALYZING_RETRYABLE_V1+MOP_STALE_NONTERMINAL_RECONCILE_V1+MOP_AUTOSEND_SAFE_DEFAULT_V1+MOP_HUMAN_HANDOFF_MANAGER_TASK_V1",
    }


def _safe_feed_recovery() -> dict:
    from app.services.jobs import recover_interrupted_campaign_jobs
    changed=int(recover_interrupted_campaign_jobs() or 0)
    return {"action":"recover_interrupted_campaign_jobs","result":{"changed":changed},"passed":True}


def _safe_email_recovery() -> dict:
    from app.services.email_queue import recover_stale_sending
    changed=int(recover_stale_sending(stale_minutes=15,limit=200) or 0)
    return {"action":"recover_stale_email_sending","result":{"delivery_unknown_recovered":changed},"passed":True,"note":"no automatic resend"}


def _safe_sitebuild_recovery() -> dict:
    roots=(Path('/root/BORIS/backend/images/_sitebuild/videos'),Path('/root/BORIS/backend/images/_sitebuild/screens'))
    before={str(p):p.exists() for p in roots}
    created=[]
    for p in roots:
        if not p.exists():
            p.mkdir(parents=True,exist_ok=True);created.append(str(p))
    after={str(p):p.exists() for p in roots}
    return {"action":"restore_sitebuild_storage","before":before,"result":{"created":created},"after":after,"passed":all(after.values())}



def _safe_prospecting_scheduler_recovery() -> dict:
    """PROSPECTING_TIMER_SELFHEAL_V1: keep the autonomous Lead Radar timer alive.

    This is a bounded L1 infrastructure repair only: it starts the already
    installed/enabled timer when systemd reports it inactive. It does not send
    outreach and does not change source/provider policy.
    """
    import subprocess
    timer="boris-lead-radar.timer"
    before=subprocess.run(
        ["systemctl","is-active",timer],capture_output=True,text=True,timeout=10
    )
    active_before=(before.returncode==0 and before.stdout.strip()=="active")
    changed=0
    if not active_before:
        subprocess.run(["systemctl","start",timer],check=True,timeout=15)
        changed=1
    after=subprocess.run(
        ["systemctl","is-active",timer],capture_output=True,text=True,timeout=10
    )
    active_after=(after.returncode==0 and after.stdout.strip()=="active")
    return {
        "action":"ensure_lead_radar_timer_active",
        "before":{"timer":timer,"active":active_before},
        "result":{"changed":changed,"started":bool(changed)},
        "after":{"timer":timer,"active":active_after},
        "passed":active_after,
        "verification":"systemd is-active after bounded timer self-heal; no outreach write",
        "marker":"PROSPECTING_TIMER_SELFHEAL_V1",
    }


def _safe_social_recovery(db) -> dict:
    """Restart the shared background worker only when the social heartbeat is truly stale.

    This is bounded L1 infrastructure recovery: no post is created, no provider
    call is made here, and a fresh heartbeat is required after restart.
    """
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime, timezone, timedelta
    import subprocess, time
    hb=db.query(ReliabilityHeartbeat).filter(
        ReliabilityHeartbeat.module=='runtime',
        ReliabilityHeartbeat.worker_id=='social_posting_scheduler',
    ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
        age=int((datetime.now(timezone.utc)-seen).total_seconds())
    stale=(age is None or age>180 or str(hb.state if hb else '') in {'error','failed','critical','stale'})
    before={'heartbeat_age_sec':age,'state':hb.state if hb else None,'stale':stale}
    if not stale:
        return {'action':'recover_social_scheduler','before':before,'result':{'restarted':False},'after':before,'passed':True}
    subprocess.run(['systemctl','restart','boris-background-worker.service'],check=True,timeout=20)
    deadline=time.time()+25
    after={'heartbeat_age_sec':None,'state':None,'stale':True}
    while time.time()<deadline:
        db.expire_all()
        hb2=db.query(ReliabilityHeartbeat).filter(
            ReliabilityHeartbeat.module=='runtime',
            ReliabilityHeartbeat.worker_id=='social_posting_scheduler',
        ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
        if hb2 and hb2.last_seen_at:
            seen=hb2.last_seen_at if hb2.last_seen_at.tzinfo else hb2.last_seen_at.replace(tzinfo=timezone.utc)
            age2=int((datetime.now(timezone.utc)-seen).total_seconds())
            state2=str(hb2.state or '')
            after={'heartbeat_age_sec':age2,'state':state2,'stale':age2>180 or state2 in {'error','failed','critical','stale'}}
            if not after['stale']:
                break
        time.sleep(1)
    return {'action':'recover_social_scheduler','before':before,'result':{'restarted':True},'after':after,'passed':not after['stale']}

def _safe_reactivation_recovery(db) -> dict:
    """Recover the reactivation transport state without sending a new message.

    This is deliberately L1 only: stale ``sending`` records are made
    ``delivery_unknown`` (never retried blindly), existing Avito receipts are
    backfilled from the canonical messenger history, and incoming Avito replies
    close the corresponding reactivation candidate. No Avito write, SMS, email,
    paid AI call, or business decision is made here.
    """
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime, timezone, timedelta
    import subprocess
    hb=db.query(ReliabilityHeartbeat).filter(
        ReliabilityHeartbeat.module=='runtime',
        ReliabilityHeartbeat.worker_id=='reactivation_scheduler',
    ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
        age=int((datetime.now(timezone.utc)-seen).total_seconds())
    state=str(hb.state if hb else '')
    timer_before=subprocess.run(['systemctl','is-active','boris-reactivation.timer'],capture_output=True,text=True,timeout=10)
    active_before=timer_before.returncode==0 and timer_before.stdout.strip()=='active'
    stale=(age is None or age>1800 or state in {'error','failed','critical','stale','degraded'})
    before={'heartbeat_age_sec':age,'heartbeat_state':state or None,'timer_active':active_before,'stale':stale}
    restarted=False
    if stale or not active_before:
        subprocess.run(['systemctl','restart','boris-reactivation.timer'],check=True,timeout=20)
        restarted=True
    timer_after=subprocess.run(['systemctl','is-active','boris-reactivation.timer'],capture_output=True,text=True,timeout=10)
    active_after=timer_after.returncode==0 and timer_after.stdout.strip()=='active'

    # REACTIVATION_HEALTHY_SCHEDULER_RECOVERY_DEDUPE_V1: the normal scheduler is
    # the canonical transport reconciler. Guardian must not duplicate Avito reads
    # every cycle when that scheduler is fresh and has either no unresolved
    # ambiguity or fresh exact-chat 402 proof for every unresolved delivery.
    _hb_details=dict(getattr(hb,'details_json',None) or {}) if hb is not None else {}
    _hb_unresolved=int(_hb_details.get('delivery_unresolved') or 0)
    _hb_internal=int(_hb_details.get('delivery_unknown_internal_unresolved') or 0)
    _hb_external=int(_hb_details.get('delivery_unknown_external_402') or 0)
    _hb_evidence_fresh=False
    try:
        _hb_checked=datetime.fromisoformat(str(_hb_details.get('delivery_unknown_evidence_checked_at') or '').replace('Z','+00:00'))
        if _hb_checked.tzinfo is None: _hb_checked=_hb_checked.replace(tzinfo=timezone.utc)
        _hb_evidence_fresh=(datetime.now(timezone.utc)-_hb_checked).total_seconds() <= 1800
    except Exception:
        _hb_evidence_fresh=False
    _external_only=bool(_hb_unresolved>0 and _hb_internal==0 and _hb_external>=_hb_unresolved and _hb_evidence_fresh)
    _clean_cycle=bool(_hb_unresolved==0 and int(_hb_details.get('errors') or 0)==0)
    if active_after and not stale and age is not None and age<=900 and (_clean_cycle or _external_only):
        return {'action':'recover_reactivation_transport','before':before,'result':{'restarted':restarted,'forced_business_cycle':False,'skipped_duplicate_provider_reads':True,'delivery_unresolved':_hb_unresolved,'delivery_unknown_external_402':_hb_external,'delivery_unknown_internal_unresolved':_hb_internal},'after':{'timer_active':active_after,'heartbeat_age_sec':age,'heartbeat_state':state or None},'passed':True,'dependency_state':('waiting_external' if _external_only else None),'verification':'fresh canonical scheduler heartbeat already reconciled transport; guardian duplicate Avito reads suppressed'}

    # REACTIVATION_TRANSPORT_SELF_HEAL_V1: repair only evidence/state already
    # present in the canonical Avito messenger history. Never issue a new send.
    from app.reactivation_runtime import (
        reconcile_sent_receipts, reconcile_sent_event_receipts,
        reconcile_delivery_unknown, reconcile_transport_unavailable,
        reconcile_client_replies, reconcile_stale_ready_messages,
        recover_stale_sending,
    )
    evidence={}
    try:
        evidence['stale_sending_recovered']=int(recover_stale_sending(db,minutes=10) or 0)
        delivery=reconcile_delivery_unknown(db,limit=100,sync_active=True)
        evidence['delivery_reconciled']=int(delivery.get('fixed') or 0)
        evidence['delivery_unresolved']=int(delivery.get('unresolved') or 0)
        evidence['receipts_backfilled']=int(reconcile_sent_receipts(db,limit=200) or 0)
        evidence['event_receipts_backfilled']=int(reconcile_sent_event_receipts(db,limit=500) or 0)
        _tr=reconcile_transport_unavailable(db,limit=50); evidence['transport_restored']=int((_tr.get('restored') if isinstance(_tr,dict) else _tr) or 0)
        evidence['client_replies_reconciled']=int(reconcile_client_replies(db,limit=500) or 0)
        stale=reconcile_stale_ready_messages(db,limit=500)
        evidence['stale_ready_cancelled']=int(stale.get('cancelled') or 0)
        evidence['stale_ready_replied']=int(stale.get('replied') or 0)
        evidence['stale_ready_manager_taken_over']=int(stale.get('manager_taken_over') or 0)
    except Exception as exc:
        db.rollback()
        evidence['error']=type(exc).__name__
    after={'timer_active':active_after,'heartbeat_age_sec':age,'heartbeat_state':state or None,'evidence':evidence}
    # Do not claim recovery succeeded while an ambiguous delivery is still
    # unresolved. It must remain visible for diagnosis/escalation and must never
    # be converted into an automatic resend.
    unresolved=int(evidence.get('delivery_unresolved') or 0)
    external_blocked=0
    if unresolved>0:
        # REACTIVATION_AMBIGUOUS_EXTERNAL_CLASSIFICATION_V1: an ambiguous POST
        # cannot be self-healed by resending. If the exact chat is currently
        # unreadable because Avito returns 402, classify it as an external
        # evidence blocker rather than an internal recovery failure.
        try:
            from sqlalchemy import text as _sql_react
            from app.api.messenger import fetch_chat_messages
            rows=db.execute(_sql_react("""
                SELECT id,account_id,avito_chat_id FROM reactivation_messages
                 WHERE status='delivery_unknown' ORDER BY updated_at,id LIMIT 100
            """)).mappings().all()
            for row in rows:
                probe=fetch_chat_messages(str(row['account_id']),str(row['avito_chat_id']))
                msg=str((probe or {}).get('message') or '').lower()
                if (probe or {}).get('status')!='ok' and ('402' in msg or 'подписку с api мессенджера' in msg):
                    external_blocked+=1
            evidence['delivery_unknown_external_402']=external_blocked
            # Account/chat 402 is provider-side read access, not an owner
            # operating task. The normal capability scheduler already reprobes
            # automatically; expose that contract explicitly to the control plane.
            evidence['external_402_auto_reprobe']=bool(external_blocked>0)
            evidence['external_402_owner_action_required']=False
        except Exception as exc:
            db.rollback(); evidence['delivery_unknown_classify_error']=type(exc).__name__
    internal_unresolved=max(0,unresolved-external_blocked)
    evidence['delivery_unknown_internal_unresolved']=internal_unresolved
    # REACTIVATION_AMBIGUOUS_EVIDENCE_HEARTBEAT_V1: publish the exact-chat
    # read-only classification into the existing scheduler heartbeat so the
    # control plane can distinguish a current Avito 402 dependency from an
    # internally unresolved ambiguity without re-probing or blind retry loops.
    evidence_checked_at=datetime.now(timezone.utc).isoformat()
    evidence['delivery_unknown_evidence_checked_at']=evidence_checked_at
    if hb is not None:
        try:
            details=dict(hb.details_json or {})
            details['delivery_unknown_external_402']=int(external_blocked)
            details['delivery_unknown_internal_unresolved']=int(internal_unresolved)
            details['delivery_unknown_evidence_checked_at']=evidence_checked_at
            hb.details_json=details
            db.add(hb); db.commit()
        except Exception:
            db.rollback()
    passed=(active_after and 'error' not in evidence and internal_unresolved==0)
    return {'action':'recover_reactivation_transport','before':before,'result':{'restarted':restarted,'forced_business_cycle':False,**evidence},'after':after,'passed':passed,'dependency_state':('waiting_external' if unresolved>0 and internal_unresolved==0 else None),'verification':'canonical Avito messenger history reconciled; ambiguous delivery is never resent; current Avito 402 is classified as external evidence blocker instead of internal recovery failure'}


def _safe_rop_scheduler_recovery(db) -> dict:
    """Repair only ROP scheduler infrastructure; never force a paid analysis.

    Starting an analysis service from generic brain recovery could create paid
    AI calls. L1 recovery therefore only ensures the timer is enabled/running.
    The normal timer tick remains the sole business executor and keeps the
    existing ROP billing/dedupe guards authoritative.
    """
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime, timezone, timedelta
    import subprocess
    hb=db.query(ReliabilityHeartbeat).filter(
        ReliabilityHeartbeat.module=='runtime',
        ReliabilityHeartbeat.worker_id=='rop_auto_scheduler',
    ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
        age=int((datetime.now(timezone.utc)-seen).total_seconds())
    state=str(hb.state if hb else '')
    before_timer=subprocess.run(
        ['systemctl','is-active','boris-rop-auto.timer'],
        capture_output=True,text=True,timeout=10,
    )
    active_before=before_timer.returncode==0 and before_timer.stdout.strip()=='active'
    stale=(age is None or age>1800 or state in {'degraded','error','failed','critical','stale'})
    restarted=False
    if stale or not active_before:
        subprocess.run(['systemctl','restart','boris-rop-auto.timer'],check=True,timeout=20)
        restarted=True
    after_timer=subprocess.run(
        ['systemctl','is-active','boris-rop-auto.timer'],
        capture_output=True,text=True,timeout=10,
    )
    active_after=after_timer.returncode==0 and after_timer.stdout.strip()=='active'
    return {
        'action':'restart_rop_auto_scheduler',
        'before':{'heartbeat_age_sec':age,'heartbeat_state':state or None,'timer_active':active_before,'stale':stale},
        'result':{'restarted':restarted,'forced_paid_analysis':False},
        'after':{'timer_active':active_after,'heartbeat_age_sec':age,'heartbeat_state':state or None},
        'passed':active_after,
        'verification':'timer active; paid ROP work remains gated by canonical scheduled runner',
    }


def _safe_marketer_runtime_recovery(db) -> dict:
    """Remove stale owner-facing CPX authority for inactive marketer periods.

    MARKETER_STALE_RUNTIME_BRAIN_SELFHEAL_V1: this is L1 DB-only recovery.
    It never calls Avito, never creates a mandate and never changes spend.
    The canonical rollout reconciler owns the mutation so entitlement logic is
    not duplicated between System Brain and the marketer.
    """
    import json
    from app.models.storage import Storage
    from marketer_rollout_runner import (
        _reconcile_stale_rollout_states,
        _rollout_marketer_entitlement,
        _runtime_claims_marketer_money,
    )

    def _stale():
        bad=[]
        rows=db.query(Storage).filter(Storage.key=="virtual_marketer_runtime").all()
        for row in rows:
            try:
                runtime=json.loads(row.value or "{}")
            except Exception:
                continue
            if not isinstance(runtime,dict) or not _runtime_claims_marketer_money(runtime):
                continue
            entitled,_=_rollout_marketer_entitlement(db,str(row.account_id))
            if not entitled:
                bad.append(str(row.account_id))
        return sorted(set(bad))

    before=_stale()
    changed=int(_reconcile_stale_rollout_states(db) or 0)
    db.expire_all()
    after=_stale()
    return {
        "action":"reconcile_stale_marketer_runtime_authority",
        "before":{"stale_authority_accounts":before,"count":len(before)},
        "result":{"changed":changed},
        "after":{"stale_authority_accounts":after,"count":len(after)},
        "passed":len(after)==0,
        "verification":"canonical entitlement; owner-facing runtime contains no stale CPX money authority",
    }


def _safe_crm_next_action_recovery(db) -> dict:
    """Repair only recent live inquiries that lack any next action.

    Internal DB-only operation: no client message, provider call or spend. Existing
    open tasks are reused; otherwise one deterministic follow-up task is created.
    Owner-disabled MOP accounts are excluded to preserve explicit owner authority.
    """
    from sqlalchemy import text
    rows=db.execute(text("""
      SELECT d.id,d.owner_user_id,d.contact_id,d.avito_account_id,d.avito_chat_id,d.created_at
        FROM boris_crm_deals d
       WHERE d.status='open' AND d.next_action_at IS NULL
         AND d.created_at>=now()-interval '24 hours'
         -- CRM_NEXT_ACTION_LIVE_INQUIRY_EVIDENCE_V1: a deal can be created today
         -- while its imported Avito history is months old. Never create an urgent
         -- follow-up merely from DB creation time; chat-backed deals require a real
         -- recent inbound provider timestamp.
         AND (d.avito_chat_id IS NULL OR (
           EXISTS (
             SELECT 1 FROM messenger_messages mm
              WHERE mm.account_id=d.avito_account_id AND mm.avito_chat_id=d.avito_chat_id
                AND mm.direction='in' AND to_timestamp(mm.avito_created_at)>=now()-interval '24 hours'
           )
           -- CRM_NEXT_ACTION_UNANSWERED_ONLY_V1: next-action recovery is for a
           -- genuinely unanswered recent inquiry, not a chat that was already
           -- answered before the CRM deal/import was created.
           AND NOT EXISTS (
             SELECT 1 FROM storage sw
              WHERE sw.account_id=d.avito_account_id AND sw.key='messenger_item_whitelist'
                AND COALESCE(sw.value::jsonb->>'mode','legacy')='manual'
                AND jsonb_typeof(sw.value::jsonb->'item_ids')='array'
                AND jsonb_array_length(sw.value::jsonb->'item_ids')>0
                AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements_text(sw.value::jsonb->'item_ids') w(item)
                   WHERE w.item=(SELECT COALESCE(mx.item_id,'') FROM messenger_messages mx
                                  WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in'
                                  ORDER BY mx.avito_created_at DESC LIMIT 1)
                )
           )
           -- CRM_MOP_OWNED_OBLIGATION_NO_HUMAN_TASK_V1: if the exact chat
           -- is already owned by a canonical MOP draft waiting on provider, or
           -- by a draft_ready row whose auto_send policy is genuinely unset,
           -- do not fabricate a generic human follow-up. The correct visible
           -- dependency is MOP/provider or owner policy, not "contact client".
           AND NOT EXISTS (
             SELECT 1 FROM mop_drafts md
              WHERE md.account_id=d.avito_account_id AND md.avito_chat_id=d.avito_chat_id
                AND (
                  md.status='waiting_external'
                  OR (md.status='draft_ready' AND NOT EXISTS (
                    SELECT 1 FROM storage ms
                     WHERE ms.account_id=d.avito_account_id AND ms.key='mop_crm_sales_settings'
                       AND (ms.value::jsonb ? 'auto_send')
                  ))
                )
           )
           AND NOT EXISTS (
             SELECT 1 FROM messenger_messages mo
              WHERE mo.account_id=d.avito_account_id AND mo.avito_chat_id=d.avito_chat_id
                AND mo.direction='out' AND to_timestamp(mo.avito_created_at) > (
                  SELECT max(to_timestamp(mi.avito_created_at)) FROM messenger_messages mi
                   WHERE mi.account_id=d.avito_account_id AND mi.avito_chat_id=d.avito_chat_id AND mi.direction='in'
                )
           )
         ))
       ORDER BY d.id LIMIT 100
    """)).mappings().all()
    repaired=skipped_disabled=reused=created=0
    for r in rows:
        aid=str(r.get('avito_account_id') or '')
        cfg={}
        if aid:
            raw=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"),{'a':aid}).scalar()
            try:
                import json; cfg=json.loads(raw) if isinstance(raw,str) else (raw or {})
            except Exception:
                cfg={}
        if aid and not bool(cfg.get('mop_enabled',cfg.get('enabled',True))):
            skipped_disabled+=1; continue
        # CRM_NEXT_ACTION_MOP_SCOPE_PARITY_V1: never recreate generic operator
        # work for provider events/chats that Messages/MOP intentionally exclude.
        # This closes the create->cleanup->recreate loop using persisted evidence.
        if aid and r.get('avito_chat_id'):
            _last=db.execute(text("""SELECT content_type,text,item_id FROM messenger_messages
              WHERE account_id=:a AND avito_chat_id=:c AND direction='in' AND COALESCE(msg_type,'')<>'system'
              ORDER BY avito_created_at DESC LIMIT 1"""),{'a':aid,'c':r['avito_chat_id']}).mappings().first()
            if _last and str(_last.get('content_type') or '').lower()=='appcall' and not str(_last.get('text') or '').strip():
                skipped_disabled+=1; continue
            raw_wl=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='messenger_item_whitelist' ORDER BY id DESC LIMIT 1"),{'a':aid}).scalar()
            try:
                _wl_cfg=json.loads(raw_wl) if isinstance(raw_wl,str) else (raw_wl or {})
            except Exception:
                _wl_cfg={}
            _wl={str(x) for x in ((_wl_cfg or {}).get('item_ids') or []) if str(x)} \
                if isinstance(_wl_cfg,dict) and str((_wl_cfg or {}).get('mode') or 'legacy').lower()=='manual' else set()
            if _last and _wl and str(_last.get('item_id') or '') not in _wl:
                skipped_disabled+=1; continue
        task=db.execute(text("SELECT id,due_at,source,title,created_at FROM boris_crm_tasks WHERE deal_id=:d AND status='open' ORDER BY due_at NULLS LAST,id LIMIT 1"),{'d':r['id']}).mappings().first()

        # CRM_PROMISED_WORKTIME_NEXT_ACTION_V1: if BORIS already promised the
        # client "tomorrow during working hours", reuse the account's persisted
        # work-schedule start/timezone instead of inventing an emergency +15 min
        # follow-up. The promise is taken only from an already-sent outbound CRM
        # activity; no AI/provider call and no inferred promise.
        promised_due=None
        promised_title=None
        promised_activity=None
        if r.get('avito_chat_id'):
            promised_activity=db.execute(text("""
              SELECT created_at,body
                FROM boris_crm_activities
               WHERE deal_id=:d AND activity_type='message' AND direction='out'
                 AND created_at>=:created
                 AND LOWER(COALESCE(body,'')) LIKE '%завтра%'
                 AND LOWER(COALESCE(body,'')) LIKE '%рабоч%'
               ORDER BY created_at DESC,id DESC LIMIT 1
            """),{'d':r['id'],'created':r['created_at']}).mappings().first()
        if promised_activity:
            ws=(cfg.get('work_schedule') or {}) if isinstance(cfg,dict) else {}
            start=str(ws.get('start') or '').strip()
            tz_name=str(ws.get('timezone') or '').strip()
            try:
                from datetime import timedelta
                from zoneinfo import ZoneInfo
                hh,mm=[int(x) for x in start.split(':',1)]
                if not (0<=hh<=23 and 0<=mm<=59 and tz_name):
                    raise ValueError('invalid work schedule')
                base=promised_activity['created_at']
                if base.tzinfo is None:
                    base=base.replace(tzinfo=timezone.utc)
                local=base.astimezone(ZoneInfo(tz_name))
                promised_due=(local+timedelta(days=1)).replace(hour=hh,minute=mm,second=0,microsecond=0).astimezone(timezone.utc)
                promised_title='Перезвонить клиенту — обещан звонок завтра в рабочее время'
            except Exception:
                promised_due=None
                promised_title=None

        due=(task or {}).get('due_at')
        if due is None:
            due=promised_due or db.execute(text("SELECT greatest(now()+interval '15 minutes', :c + interval '30 minutes')"),{'c':r['created_at']}).scalar()
        if task:
            reused+=1
            if task.get('due_at') is None:
                db.execute(text("UPDATE boris_crm_tasks SET due_at=:due,title=COALESCE(:title,title) WHERE id=:id AND due_at IS NULL"),{'due':due,'title':promised_title,'id':task['id']})
        else:
            task_title=promised_title or 'Связаться с клиентом'
            task_description=('Автоматически создано BORIS по уже отправленному обещанию: завтра в рабочее время'
                              if promised_due else
                              'Автоматически создано BORIS: у новой сделки отсутствовало следующее действие')
            db.execute(text("""INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,due_at,status,source,created_at)
                VALUES(:u,:d,:c,:title,:description,:due,'open','brain_auto_recovery',now())"""),
                {'u':r.get('owner_user_id'),'d':r['id'],'c':r.get('contact_id'),'title':task_title,'description':task_description,'due':due})
            created+=1
        db.execute(text("UPDATE boris_crm_deals SET next_action_at=:due,updated_at=now() WHERE id=:d AND next_action_at IS NULL"),{'due':due,'d':r['id']})
        repaired+=1
    db.commit()
    # CRM_NEXT_ACTION_POSTCONDITION_PARITY_V2: verify exactly the same live,
    # unanswered provider-time scope used by selection/diagnosis. The old broad
    # created_at-only count treated already-answered/imported historical deals as
    # failed recovery, leaving Guardian falsely degraded although no actionable
    # deal remained. This postcondition is DB-only and never sends a message.
    after=int(db.execute(text("""
      SELECT count(*) FROM boris_crm_deals d
       WHERE d.status='open' AND d.next_action_at IS NULL
         AND d.created_at>=now()-interval '24 hours'
         AND (d.avito_chat_id IS NULL OR (
           EXISTS (
             SELECT 1 FROM messenger_messages mi
              WHERE mi.account_id=d.avito_account_id AND mi.avito_chat_id=d.avito_chat_id
                AND mi.direction='in' AND to_timestamp(mi.avito_created_at)>=now()-interval '24 hours'
           )
           AND NOT EXISTS (
             SELECT 1 FROM messenger_messages ml
              WHERE ml.account_id=d.avito_account_id AND ml.avito_chat_id=d.avito_chat_id AND ml.direction='in'
                AND COALESCE(ml.msg_type,'')<>'system'
                AND ml.avito_created_at=(SELECT max(mx.avito_created_at) FROM messenger_messages mx WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in' AND COALESCE(mx.msg_type,'')<>'system')
                AND LOWER(COALESCE(ml.content_type,''))='appcall' AND COALESCE(ml.text,'')=''
           )
           AND NOT EXISTS (
             SELECT 1 FROM storage sw
              WHERE sw.account_id=d.avito_account_id AND sw.key='messenger_item_whitelist'
                AND COALESCE(sw.value::jsonb->>'mode','legacy')='manual'
                AND jsonb_typeof(sw.value::jsonb->'item_ids')='array'
                AND jsonb_array_length(sw.value::jsonb->'item_ids')>0
                AND NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements_text(sw.value::jsonb->'item_ids') w(item)
                   WHERE w.item=(SELECT COALESCE(mx.item_id,'') FROM messenger_messages mx
                                  WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in'
                                  ORDER BY mx.avito_created_at DESC LIMIT 1)
                )
           )
           -- CRM_MOP_OWNED_OBLIGATION_NO_HUMAN_TASK_V1: if the exact chat
           -- is already owned by a canonical MOP draft waiting on provider, or
           -- by a draft_ready row whose auto_send policy is genuinely unset,
           -- do not fabricate a generic human follow-up. The correct visible
           -- dependency is MOP/provider or owner policy, not "contact client".
           AND NOT EXISTS (
             SELECT 1 FROM mop_drafts md
              WHERE md.account_id=d.avito_account_id AND md.avito_chat_id=d.avito_chat_id
                AND (
                  md.status='waiting_external'
                  OR (md.status='draft_ready' AND NOT EXISTS (
                    SELECT 1 FROM storage ms
                     WHERE ms.account_id=d.avito_account_id AND ms.key='mop_crm_sales_settings'
                       AND (ms.value::jsonb ? 'auto_send')
                  ))
                )
           )
           AND NOT EXISTS (
             SELECT 1 FROM messenger_messages mo
              WHERE mo.account_id=d.avito_account_id AND mo.avito_chat_id=d.avito_chat_id
                AND mo.direction='out' AND to_timestamp(mo.avito_created_at) > (
                  SELECT max(to_timestamp(mx.avito_created_at)) FROM messenger_messages mx
                   WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in'
                )
           )
         ))
         AND NOT EXISTS (
           SELECT 1 FROM storage s
            WHERE s.account_id=d.avito_account_id AND s.key='mop_crm_sales_settings'
              AND COALESCE((s.value::jsonb->>'mop_enabled')::boolean,(s.value::jsonb->>'enabled')::boolean,true)=false
         )
    """)).scalar() or 0)
    return {'action':'create_or_repair_next_action','result':{'repaired':repaired,'tasks_created':created,'tasks_reused':reused,'owner_disabled_skipped':skipped_disabled},'after':{'recent_actionable_missing':after},'passed':after==0}


def _safe_crm_assignment_phone_handoff_recovery(db) -> dict:
    """CRM_ASSIGNMENT_PHONE_HANDOFF_RECOVERY_V1.

    DB-only, bounded recovery for two canonical drifts:
    1) Messenger lead/task lost its manager while the open CRM deal already has
       a responsible user.
    2) A lead has a confirmed phone but no open human task because an older MOP
       path failed to persist human_required.

    No client message, AI request, provider call or money mutation occurs here.
    Existing explicit assignments are never overwritten.
    """
    import re
    from sqlalchemy import text
    from app.mop_core import text_hash
    from app.services.client_supervisor import load_supervisor_snapshot

    # This control query is branch-heavy and returns only a tiny recovery set.
    # PostgreSQL JIT startup costs far more than execution here; keep the setting
    # transaction-local so analytical/business queries elsewhere are untouched.
    db.execute(text("SET LOCAL jit=off"))

    rows=db.execute(text("""
      WITH latest_leads AS (
        SELECT DISTINCT ON (account_id,avito_chat_id)
               id,account_id,avito_chat_id,has_phone,assigned_user_id,assigned_at,updated_at
          FROM messenger_leads
         ORDER BY account_id,avito_chat_id,id DESC
      )
      SELECT l.id AS lead_id,l.account_id,l.avito_chat_id,l.has_phone,
             l.assigned_user_id,
             d.id AS deal_id,d.owner_user_id,d.responsible_user_id,d.contact_id
        FROM latest_leads l
        JOIN LATERAL (
          SELECT x.id,x.owner_user_id,x.responsible_user_id,x.contact_id
            FROM boris_crm_deals x
           WHERE x.avito_account_id=l.account_id
             AND x.avito_chat_id=l.avito_chat_id
             AND x.status='open'
           ORDER BY x.id DESC LIMIT 1
        ) d ON true
        JOIN LATERAL (
          SELECT ss.value::jsonb AS snap
            FROM storage ss
           WHERE ss.account_id=l.account_id
             AND ss.key='client_supervisor_snapshot_v1'
           ORDER BY ss.id DESC LIMIT 1
        ) sup ON true
       WHERE sup.snap->>'client_state'='active'
         AND (sup.snap->'expected_modules') ? 'crm'
         AND (sup.snap->>'checked_at')::timestamptz>=now()-interval '5 minutes'
         AND (
           l.assigned_user_id IS NULL
           OR EXISTS (
             SELECT 1 FROM boris_crm_tasks t
              WHERE t.deal_id=d.id AND t.status='open'
                AND t.assigned_user_id IS NULL
           )
           OR (
             l.has_phone=true
             AND l.updated_at>=now()-interval '30 days'
             AND (
               EXISTS (
                 SELECT 1 FROM boris_crm_activities pa
                  WHERE pa.deal_id=d.id AND pa.source='mop_goal'
                    AND right(COALESCE(pa.source_ref,''),14)='phone_received'
               )
               OR EXISTS (
                 SELECT 1 FROM messenger_messages pm
                  WHERE pm.account_id=l.account_id
                    AND pm.avito_chat_id=l.avito_chat_id
                    AND lower(pm.direction) LIKE 'in%'
                    AND COALESCE(pm.msg_type,'')<>'system'
                    AND regexp_replace(COALESCE(pm.text,''),'[^0-9]','','g')
                        ~ '(7|8)?9[0-9]{9}'
               )
             )
             AND NOT EXISTS (
               SELECT 1 FROM boris_crm_tasks t
                WHERE t.deal_id=d.id AND t.status='open'
             )
             AND NOT EXISTS (
               SELECT 1 FROM boris_crm_tasks t
                WHERE t.deal_id=d.id AND t.source='mop_phone_handoff_recovery'
             )
             AND NOT EXISTS (
               SELECT 1 FROM boris_crm_activities ev
                WHERE ev.deal_id=d.id
                  AND ev.source='brain_phone_handoff_evidence'
                  AND COALESCE(ev.metadata_json->>'lead_id','')=CAST(l.id AS text)
                  AND COALESCE(ev.metadata_json->>'outcome','')='human_followup'
             )
           )
         )
       ORDER BY l.id
       LIMIT 300
    """)).mappings().all()

    phone_re=re.compile(
        r"(?<!\d)(?:\+?7|8)?[\s\-()]*(9\d{2})[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
    )
    eligibility={}
    lead_assigned=0
    task_assigned=0
    phone_tasks_created=0
    phone_human_followup=0
    phone_existing_task=0
    phone_prior_recovery=0
    phone_no_message_evidence=0
    phone_satisfied_marked=0
    skipped_inactive=0
    missing_manager=0
    created_task_ids=[]

    for r in rows:
        aid=str(r.get("account_id") or "")
        if aid not in eligibility:
            try:
                snap=load_supervisor_snapshot(db,aid,max_age_seconds=300) if aid else None
                eligibility[aid]=bool(
                    snap and snap.get("client_state")=="active"
                    and "crm" in list(snap.get("expected_modules") or [])
                )
            except Exception:
                eligibility[aid]=False
        if not eligibility.get(aid):
            skipped_inactive+=1
            continue

        manager_id=r.get("responsible_user_id") or r.get("owner_user_id")
        if manager_id is None:
            missing_manager+=1
            continue
        manager_id=int(manager_id)
        deal_id=int(r["deal_id"])
        lead_id=int(r["lead_id"])

        if r.get("assigned_user_id") is None:
            updated=db.execute(text("""
              UPDATE messenger_leads
                 SET assigned_user_id=:u,
                     assigned_at=COALESCE(assigned_at,now()),
                     updated_at=now()
               WHERE id=:id AND assigned_user_id IS NULL
               RETURNING id
            """),{"u":manager_id,"id":lead_id}).scalar()
            if updated is not None:
                lead_assigned+=1

        assigned_tasks=db.execute(text("""
          UPDATE boris_crm_tasks
             SET assigned_user_id=:u
           WHERE deal_id=:d AND status='open' AND assigned_user_id IS NULL
           RETURNING id
        """),{"u":manager_id,"d":deal_id}).scalars().all()
        task_assigned+=len(assigned_tasks)

        if not bool(r.get("has_phone")):
            continue

        if db.execute(text("""
          SELECT 1 FROM boris_crm_tasks
           WHERE deal_id=:d AND status='open'
           LIMIT 1
        """),{"d":deal_id}).first():
            phone_existing_task+=1
            continue

        if db.execute(text("""
          SELECT 1 FROM boris_crm_tasks
           WHERE deal_id=:d AND source='mop_phone_handoff_recovery'
           LIMIT 1
        """),{"d":deal_id}).first():
            phone_prior_recovery+=1
            continue

        inbound=db.execute(text("""
          SELECT avito_created_at,text
            FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c
             AND lower(direction) LIKE 'in%'
             AND COALESCE(msg_type,'')<>'system'
           ORDER BY avito_created_at,id
        """),{"a":aid,"c":str(r.get("avito_chat_id") or "")}).mappings().all()
        phone_epoch=None
        for msg in inbound:
            if phone_re.search(str(msg.get("text") or "")):
                try:
                    phone_epoch=int(msg.get("avito_created_at") or 0)
                except Exception:
                    phone_epoch=0
                if phone_epoch:
                    break
        if not phone_epoch:
            goal_at=db.execute(text("""
              SELECT min(created_at)
                FROM boris_crm_activities
               WHERE deal_id=:d AND source='mop_goal'
                 AND right(COALESCE(source_ref,''),14)='phone_received'
            """),{"d":deal_id}).scalar()
            if goal_at is not None:
                try:
                    phone_epoch=int(goal_at.timestamp())
                except Exception:
                    phone_epoch=None
        if not phone_epoch:
            phone_no_message_evidence+=1
            continue

        mop_hashes={
            str(x[0]) for x in db.execute(text("""
              SELECT outgoing_text_hash
                FROM mop_drafts
               WHERE account_id=:a AND avito_chat_id=:c
                 AND outgoing_text_hash IS NOT NULL
            """),{"a":aid,"c":str(r.get("avito_chat_id") or "")}).all()
            if x[0]
        }
        outgoing=db.execute(text("""
          SELECT text,msg_type
            FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c
             AND lower(direction) LIKE 'out%'
             AND avito_created_at>:ts
           ORDER BY avito_created_at,id
        """),{"a":aid,"c":str(r.get("avito_chat_id") or ""),"ts":phone_epoch}).mappings().all()
        human_followup=False
        for msg in outgoing:
            body=str(msg.get("text") or "").strip()
            if not body or str(msg.get("msg_type") or "").lower()=="system":
                continue
            if text_hash(body) not in mop_hashes:
                human_followup=True
                break
        if human_followup:
            phone_human_followup+=1
            ref=f"brain_phone_handoff_satisfied:{lead_id}:{deal_id}"
            if not db.execute(text(
                "SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"
            ),{"r":ref}).first():
                db.execute(text("""
                  INSERT INTO boris_crm_activities(
                    owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                    source,source_ref,actor_type,actor_id,metadata_json,created_at
                  ) VALUES(
                    :o,:d,:c,'handoff_evidence','crm',
                    'Передача лида с телефоном уже выполнена человеком',
                    'После получения телефона найден исходящий ответ, не принадлежащий MOP.',
                    'brain_phone_handoff_evidence',:ref,'system','boris',
                    CAST(:meta AS jsonb),now()
                  )
                """),{
                  "o":r.get("owner_user_id"),"d":deal_id,"c":r.get("contact_id"),
                  "ref":ref,
                  "meta":json.dumps({
                    "lead_id":lead_id,"account_id":aid,"deal_id":deal_id,
                    "outcome":"human_followup",
                    "external_action":False,"owner_action_required":False,
                    "policy_version":"CRM_ASSIGNMENT_PHONE_HANDOFF_RECOVERY_V1",
                  },ensure_ascii=False),
                })
                phone_satisfied_marked+=1
            continue

        due=db.execute(text("SELECT now()+interval '15 minutes'")).scalar()
        task_id=db.execute(text("""
          INSERT INTO boris_crm_tasks(
            owner_user_id,deal_id,contact_id,title,description,assigned_user_id,
            due_at,status,source,created_at
          ) VALUES(
            :o,:d,:c,'Связаться с клиентом — получен телефон',
            'BORIS восстановил обязательную передачу менеджеру: клиент оставил телефон, а открытой задачи не было.',
            :u,:due,'open','mop_phone_handoff_recovery',now()
          ) RETURNING id
        """),{
          "o":r.get("owner_user_id"),"d":deal_id,"c":r.get("contact_id"),
          "u":manager_id,"due":due,
        }).scalar()
        if task_id is None:
            continue
        task_id=int(task_id)
        db.execute(text("""
          UPDATE boris_crm_deals
             SET next_action_at=CASE
                   WHEN next_action_at IS NULL OR next_action_at>:due THEN :due
                   ELSE next_action_at END,
                 updated_at=now()
           WHERE id=:d
        """),{"due":due,"d":deal_id})
        ref=f"brain_phone_handoff:{lead_id}:{deal_id}"
        if not db.execute(text(
            "SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"
        ),{"r":ref}).first():
            db.execute(text("""
              INSERT INTO boris_crm_activities(
                owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                source,source_ref,actor_type,actor_id,metadata_json,created_at
              ) VALUES(
                :o,:d,:c,'task_created','crm',
                'BORIS восстановил передачу лида с телефоном менеджеру',
                'Телефон уже был получен, человеческий follow-up после него не подтверждён.',
                'brain_phone_handoff',:ref,'system','boris',CAST(:meta AS jsonb),now()
              )
            """),{
              "o":r.get("owner_user_id"),"d":deal_id,"c":r.get("contact_id"),
              "ref":ref,
              "meta":json.dumps({
                "lead_id":lead_id,"account_id":aid,"task_id":task_id,
                "assigned_user_id":manager_id,
                "external_action":False,"owner_action_required":False,
                "policy_version":"CRM_ASSIGNMENT_PHONE_HANDOFF_RECOVERY_V1",
              },ensure_ascii=False),
            })
        phone_tasks_created+=1
        created_task_ids.append(task_id)

    db.commit()
    return {
      "action":"sync_crm_assignment_and_recover_phone_handoff",
      "result":{
        "changed":lead_assigned+task_assigned+phone_tasks_created+phone_satisfied_marked,
        "lead_assigned":lead_assigned,
        "task_assigned":task_assigned,
        "phone_tasks_created":phone_tasks_created,
        "phone_task_ids":created_task_ids,
        "phone_existing_task":phone_existing_task,
        "phone_human_followup":phone_human_followup,
        "phone_satisfied_marked":phone_satisfied_marked,
        "phone_prior_recovery":phone_prior_recovery,
        "phone_no_message_evidence":phone_no_message_evidence,
        "skipped_inactive_or_crm_off":skipped_inactive,
        "missing_manager":missing_manager,
      },
      "passed":True,
      "verification":"active CRM snapshot + exact open deal manager; DB-only assignment/task recovery; no client/provider/AI action",
      "marker":"CRM_ASSIGNMENT_PHONE_HANDOFF_RECOVERY_V1",
    }


def _safe_crm_task_existing_analysis_enrich(db) -> dict:
    """Turn generic call follow-up tasks into specific existing agreements.

    CRM_TASK_EXISTING_ANALYSIS_ENRICH_V1
    Uses only already-persisted call_analysis; no paid AI/provider request. A generic
    task is rewritten only when agreement.exists=true, confidence>=0.75 and text is
    non-empty. The original call reference stays in description for auditability.
    """
    from sqlalchemy import text
    rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,
             ca.analysis->'agreement'->>'text' AS agreement_text,
             COALESCE((ca.analysis->'agreement'->>'confidence')::numeric,0) AS confidence
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
        JOIN call_analysis ca ON ca.account_id=d.avito_account_id
          AND ca.call_id=cast(split_part(t.description,':',3) as bigint)
       WHERE t.status='open' AND t.source='avito_calltracking'
         AND t.title='Зафиксировать следующий шаг после звонка'
         AND COALESCE((ca.analysis->'agreement'->>'exists')::boolean,false)=true
         AND COALESCE((ca.analysis->'agreement'->>'confidence')::numeric,0)>=0.75
         AND NULLIF(TRIM(ca.analysis->'agreement'->>'text'),'') IS NOT NULL
       ORDER BY t.id LIMIT 100
    """)).mappings().all()
    changed=[]
    for r in rows:
        title=str(r['agreement_text']).strip()[:500]
        ref=f"brain_task_enrich:{int(r['id'])}"
        if db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{'r':ref}).first():
            continue
        updated=db.execute(text("UPDATE boris_crm_tasks SET title=:t WHERE id=:id AND status='open' AND title='Зафиксировать следующий шаг после звонка' RETURNING id"),{'t':title,'id':r['id']}).scalar()
        if updated is None: continue
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_enriched','crm','Следующий шаг уточнён из анализа звонка',:b,
                 'brain_task_enrich',:r,'system',now())"""),
          {'o':r['owner_user_id'],'d':r['deal_id'],'c':r['contact_id'],'b':f"confidence={r['confidence']}; {title}",'r':ref})
        changed.append({'task_id':int(r['id']),'title':title,'confidence':float(r['confidence'])})

    # CRM_TASK_EXISTING_ANALYSIS_ENRICH_V2: when the persisted transcript proves
    # the first call only reached an answering machine, make the human task
    # explicit instead of leaving the generic "record next step" wording.
    # CRM_TASK_AUTOANSWER_RESCHEDULE_V1: if that callback task is already overdue
    # and no later answered call exists, move it to the nearest configured working
    # window once. This is DB-only: it never calls the client and never hides the
    # obligation by marking it complete.
    autoanswer_rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,t.due_at,t.created_at,
             d.avito_account_id
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
        JOIN call_analysis ca ON ca.account_id=d.avito_account_id
          AND ca.call_id=cast(split_part(t.description,':',3) as bigint)
       WHERE t.status='open' AND t.source='avito_calltracking'
         AND t.title IN ('Зафиксировать следующий шаг после звонка',
                         'Перезвонить клиенту — первый звонок попал на автоответчик')
         AND COALESCE((ca.analysis->'agreement'->>'exists')::boolean,false)=false
         AND LOWER(COALESCE(ca.transcript,'')) LIKE '%автоответчик%'
       ORDER BY t.id LIMIT 100
    """)).mappings().all()
    for r in autoanswer_rows:
        title='Перезвонить клиенту — первый звонок попал на автоответчик'
        enrich_ref=f"brain_task_enrich_autoanswer:{int(r['id'])}"
        enrich_exists=db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{'r':enrich_ref}).first()
        if not enrich_exists:
            updated=db.execute(text("UPDATE boris_crm_tasks SET title=:t WHERE id=:id AND status='open' RETURNING id"),{'t':title,'id':r['id']}).scalar()
            if updated is not None:
                db.execute(text("""INSERT INTO boris_crm_activities
                  (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
                  VALUES(:o,:d,:c,'task_enriched','crm','Задача уточнена по факту автоответчика',
                         'Первый звонок не соединил с клиентом; нужен повторный контакт.',
                         'brain_task_enrich',:r,'system',now())"""),
                  {'o':r['owner_user_id'],'d':r['deal_id'],'c':r['contact_id'],'r':enrich_ref})
                changed.append({'task_id':int(r['id']),'title':title,'evidence':'autoanswer_transcript'})

        due_at=r.get('due_at')
        if due_at is None or due_at>=datetime.now(timezone.utc):
            continue
        reschedule_ref=f"brain_task_autoanswer_reschedule:{int(r['id'])}"
        if db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{'r':reschedule_ref}).first():
            continue
        # A real later answered call proves the retry already happened; in that
        # case evidence reconciliation, not rescheduling, owns the task outcome.
        later_answered=db.execute(text("""
          SELECT 1 FROM boris_crm_activities a
           WHERE a.deal_id=:d AND a.activity_type='call' AND a.created_at>:created
             AND COALESCE((a.metadata_json->>'missed')::boolean,false)=false
           LIMIT 1
        """),{'d':r['deal_id'],'created':r['created_at']}).first()
        if later_answered:
            continue

        raw=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"),{'a':r['avito_account_id']}).scalar()
        try:
            cfg=json.loads(raw) if isinstance(raw,str) else (raw or {})
        except Exception:
            cfg={}
        ws=(cfg.get('work_schedule') or {}) if isinstance(cfg,dict) else {}
        start=str(ws.get('start') or '09:00').strip() or '09:00'
        end=str(ws.get('end') or '21:00').strip() or '21:00'
        tz_name=str(ws.get('timezone') or 'Europe/Moscow').strip() or 'Europe/Moscow'
        days=ws.get('days') if isinstance(ws.get('days'),list) else [0,1,2,3,4,5,6]
        try:
            from zoneinfo import ZoneInfo
            sh,sm=[int(x) for x in start.split(':',1)]
            eh,em=[int(x) for x in end.split(':',1)]
            tz=ZoneInfo(tz_name)
            local_now=datetime.now(timezone.utc).astimezone(tz)
            start_today=local_now.replace(hour=sh,minute=sm,second=0,microsecond=0)
            end_today=local_now.replace(hour=eh,minute=em,second=0,microsecond=0)
            if local_now.weekday() in days and local_now < start_today:
                local_due=start_today
            elif local_now.weekday() in days and local_now < end_today:
                local_due=local_now + timedelta(minutes=15)
                if local_due>end_today:
                    local_due=end_today
            else:
                local_due=(local_now+timedelta(days=1)).replace(hour=sh,minute=sm,second=0,microsecond=0)
                for _ in range(7):
                    if local_due.weekday() in days:
                        break
                    local_due=(local_due+timedelta(days=1)).replace(hour=sh,minute=sm,second=0,microsecond=0)
            new_due=local_due.astimezone(timezone.utc)
        except Exception:
            new_due=datetime.now(timezone.utc)+timedelta(hours=12)

        moved=db.execute(text("UPDATE boris_crm_tasks SET due_at=:due WHERE id=:id AND status='open' AND due_at<now() RETURNING id"),{'due':new_due,'id':r['id']}).scalar()
        if moved is None:
            continue
        db.execute(text("UPDATE boris_crm_deals SET next_action_at=:due,updated_at=now() WHERE id=:d"),{'due':new_due,'d':r['deal_id']})
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_rescheduled','crm','Повторный звонок перенесён после автоответчика',:b,
                 'brain_task_autoanswer_reschedule',:r,'system',now())"""),
          {'o':r['owner_user_id'],'d':r['deal_id'],'c':r['contact_id'],
           'b':f'due={new_due.isoformat()}; timezone={tz_name}; work_start={start}; work_end={end}',
           'r':reschedule_ref})
        changed.append({'task_id':int(r['id']),'title':title,'evidence':'autoanswer_transcript','rescheduled_to':new_due.isoformat()})
    db.commit()
    return {'action':'enrich_crm_tasks_from_existing_call_analysis','result':{'changed':len(changed),'tasks':changed},'passed':True,
            'verification':'persisted agreement or deterministic autoanswer transcript only; no provider call'}


def _safe_crm_task_evidence_reconcile(db) -> dict:
    """Close canonical CRM tasks only when their requested outcome is proven.

    CRM_TASK_OUTCOME_EVIDENCE_RECONCILE_V1 / CRM_TASK_OUTCOME_EVIDENCE_RECONCILE_V2
    V2 counts proven outcomes from task creation time, so work completed before the deadline is not mislabeled overdue.
    No provider calls, client messages, AI generation, or inferred success.
    """
    from sqlalchemy import text
    candidates=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,t.source,t.due_at
        FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open' AND t.due_at<now() AND d.status='open'
         AND (
           (t.source='brain_auto_recovery' AND (
             EXISTS (
               SELECT 1 FROM boris_crm_activities a
                WHERE a.deal_id=t.deal_id AND a.created_at>=t.created_at
                  AND ((a.activity_type='message' AND a.direction='out') OR a.source='mop_goal')
             )
             OR EXISTS (
               SELECT 1 FROM messenger_messages mm
                WHERE mm.account_id=d.avito_account_id AND mm.avito_chat_id=d.avito_chat_id
                  AND mm.direction='out' AND to_timestamp(mm.avito_created_at)>=t.created_at
             )
           ))
           OR
           (t.source='avito_calltracking' AND t.title='Перезвонить по пропущенному звонку Avito' AND EXISTS (
             SELECT 1 FROM boris_crm_activities a
              WHERE a.deal_id=t.deal_id AND a.created_at>t.due_at
                AND a.activity_type='call'
                AND COALESCE((a.metadata_json->>'missed')::boolean,false)=false
           ))
         )
       ORDER BY t.id LIMIT 200
    """)).mappings().all()
    closed=[]
    for t in candidates:
        ref=f"brain_task_reconcile:{int(t['id'])}"
        if db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{'r':ref}).first():
            continue
        reason=('later_outbound_or_mop_goal' if t['source']=='brain_auto_recovery' else 'later_answered_call')
        changed=db.execute(text("""UPDATE boris_crm_tasks SET status='done',completed_at=now()
          WHERE id=:id AND status='open' RETURNING id"""),{'id':t['id']}).scalar()
        if changed is None:
            continue
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_completed','crm',NULL,'Задача закрыта по подтверждённому результату',:b,
                 'brain_task_reconcile',:r,'system',now())"""),
          {'o':t['owner_user_id'],'d':t['deal_id'],'c':t['contact_id'],'b':reason,'r':ref})
        db.execute(text("""UPDATE boris_crm_deals d SET next_action_at=(
          SELECT min(x.due_at) FROM boris_crm_tasks x WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL),updated_at=now()
          WHERE d.id=:d"""),{'d':t['deal_id']})
        closed.append({'task_id':int(t['id']),'reason':reason})
    # CRM_ALREADY_ANSWERED_AUTO_TASK_CLEANUP_V1: if the latest real inbound
    # had already received a real outbound before BORIS created its generic task,
    # the task is false work and must be cancelled. Provider timestamps are used;
    # sync/import time is never treated as customer activity.
    already_answered=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id
        FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open' AND t.source='brain_auto_recovery' AND t.title='Связаться с клиентом'
         AND d.avito_chat_id IS NOT NULL
         AND EXISTS (
           SELECT 1 FROM messenger_messages mo
            WHERE mo.account_id=d.avito_account_id AND mo.avito_chat_id=d.avito_chat_id AND mo.direction='out'
              AND to_timestamp(mo.avito_created_at) <= t.created_at
              AND to_timestamp(mo.avito_created_at) > (
                SELECT max(to_timestamp(mi.avito_created_at)) FROM messenger_messages mi
                 WHERE mi.account_id=d.avito_account_id AND mi.avito_chat_id=d.avito_chat_id
                   AND mi.direction='in' AND to_timestamp(mi.avito_created_at)<=t.created_at
              )
         )
       ORDER BY t.id LIMIT 200
    """)).mappings().all()
    already_answered_ids=[]
    for t in already_answered:
        ref=f"brain_task_already_answered:{int(t['id'])}"
        changed=db.execute(text("UPDATE boris_crm_tasks SET status='cancelled',completed_at=now() WHERE id=:id AND status='open' RETURNING id"),{'id':t['id']}).scalar()
        if changed is None: continue
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_cancelled','crm','Ложная задача снята',
                 'До создания задачи последнее входящее сообщение уже получило исходящий ответ.',
                 'brain_task_reconcile',:r,'system',now()) ON CONFLICT DO NOTHING"""),
          {'o':t['owner_user_id'],'d':t['deal_id'],'c':t['contact_id'],'r':ref})
        db.execute(text("""UPDATE boris_crm_deals d SET next_action_at=(SELECT min(x.due_at) FROM boris_crm_tasks x WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL),updated_at=now() WHERE d.id=:d"""),{'d':t['deal_id']})
        already_answered_ids.append(int(t['id']))

    # CRM_MOP_HANDOFF_HUMAN_REPLY_RECONCILE_V1:
    # A MOP handoff task is complete only when the latest real Avito message is
    # an outgoing message written outside the MOP draft sender. We distinguish a
    # real manager reply from a MOP acknowledgement by outgoing_text_hash.
    handoff_tasks=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,
             d.avito_account_id,d.avito_chat_id
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open'
         AND t.source='mop_handoff_recovery'
         AND d.status='open'
         AND d.avito_chat_id IS NOT NULL
       ORDER BY t.id LIMIT 300
    """)).mappings().all()
    mop_handoff_closed=[]
    from app.mop_core import text_hash as _mop_text_hash
    for t in handoff_tasks:
        latest=db.execute(text("""
          SELECT direction,text,avito_created_at
            FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c
             AND COALESCE(msg_type,'')<>'system'
           ORDER BY avito_created_at DESC,id DESC LIMIT 1
        """),{'a':t['avito_account_id'],'c':t['avito_chat_id']}).mappings().first()
        if not latest or not str(latest.get('direction') or '').lower().startswith('out'):
            continue
        body=str(latest.get('text') or '')
        if not body.strip():
            continue
        digest=_mop_text_hash(body)
        is_mop=bool(db.execute(text("""
          SELECT 1 FROM mop_drafts
           WHERE account_id=:a AND avito_chat_id=:c
             AND outgoing_text_hash=:h
           LIMIT 1
        """),{'a':t['avito_account_id'],'c':t['avito_chat_id'],'h':digest}).first())
        if is_mop:
            continue
        ref=f"brain_mop_handoff_human_reply:{int(t['id'])}"
        changed=db.execute(text("""
          UPDATE boris_crm_tasks SET status='done',completed_at=now()
           WHERE id=:id AND status='open' RETURNING id
        """),{'id':t['id']}).scalar()
        if changed is None:
            continue
        db.execute(text("""
          INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
           source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_completed','crm',
                 'Задача передачи менеджеру закрыта',
                 'В Avito подтверждён реальный исходящий ответ менеджера после передачи.',
                 'brain_task_reconcile',:r,'system',now())
          ON CONFLICT DO NOTHING
        """),{'o':t['owner_user_id'],'d':t['deal_id'],'c':t['contact_id'],'r':ref})
        db.execute(text("""
          UPDATE boris_crm_deals d SET next_action_at=(
            SELECT min(x.due_at) FROM boris_crm_tasks x
             WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL
          ),updated_at=now() WHERE d.id=:d
        """),{'d':t['deal_id']})
        mop_handoff_closed.append(int(t['id']))

    # CRM_STALE_IMPORTED_INQUIRY_TASK_CLEANUP_V1: close only system-created
    # generic tasks whose chat has no real inbound in the 24h before task creation.
    # This removes false urgent work caused by importing old Avito history today.
    stale=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id
        FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open' AND t.source='brain_auto_recovery'
         AND t.title='Связаться с клиентом' AND d.avito_chat_id IS NOT NULL
         AND NOT EXISTS (
           SELECT 1 FROM messenger_messages mm
            WHERE mm.account_id=d.avito_account_id AND mm.avito_chat_id=d.avito_chat_id
              AND mm.direction='in'
              AND to_timestamp(mm.avito_created_at) BETWEEN t.created_at-interval '24 hours' AND now()
         )
       ORDER BY t.id LIMIT 200
    """)).mappings().all()
    stale_closed=[]
    for t in stale:
        ref=f"brain_task_stale_import:{int(t['id'])}"
        changed=db.execute(text("UPDATE boris_crm_tasks SET status='cancelled',completed_at=now() WHERE id=:id AND status='open' RETURNING id"),{'id':t['id']}).scalar()
        if changed is None: continue
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_cancelled','crm','Ложная срочная задача снята',
                 'Сделка создана при импорте старой истории; свежего входящего обращения нет.',
                 'brain_task_reconcile',:r,'system',now()) ON CONFLICT DO NOTHING"""),
          {'o':t['owner_user_id'],'d':t['deal_id'],'c':t['contact_id'],'r':ref})
        db.execute(text("""UPDATE boris_crm_deals d SET next_action_at=(
          SELECT min(x.due_at) FROM boris_crm_tasks x WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL),updated_at=now()
          WHERE d.id=:d"""),{'d':t['deal_id']})
        stale_closed.append(int(t['id']))

    # CRM_NON_DIALOGUE_AND_MOP_SCOPE_TASK_CLEANUP_V1: generic follow-up work must
    # use the same authority/dialogue truth as Messages/MOP. A blank Avito appCall
    # is a call event, not a customer text turn. Likewise, when an account has an
    # explicit item whitelist, chats outside it are outside MOP sales authority.
    # These system-created tasks are false operator work and are cancelled using
    # persisted DB evidence only; no provider/AI call and no customer contact.
    scope_rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,d.avito_account_id,d.avito_chat_id,
             mm.content_type,mm.text,mm.item_id
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
        JOIN LATERAL (
          SELECT m.content_type,m.text,m.item_id,m.avito_created_at
            FROM messenger_messages m
           WHERE m.account_id=d.avito_account_id AND m.avito_chat_id=d.avito_chat_id
             AND m.direction='in' AND COALESCE(m.msg_type,'')<>'system'
           ORDER BY m.avito_created_at DESC LIMIT 1
        ) mm ON true
       WHERE t.status='open' AND t.source='brain_auto_recovery' AND t.title='Связаться с клиентом'
         AND d.avito_chat_id IS NOT NULL
       ORDER BY t.id LIMIT 200
    """)).mappings().all()
    scope_cancelled=[]
    for t in scope_rows:
        reason=None
        if str(t.get('content_type') or '').lower()=='appcall' and not str(t.get('text') or '').strip():
            reason='non_dialogue_appcall'
        if reason is None:
            raw=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='messenger_item_whitelist' ORDER BY id DESC LIMIT 1"),{'a':t['avito_account_id']}).scalar()
            try:
                cfg=json.loads(raw) if isinstance(raw,str) else (raw or {})
            except Exception:
                cfg={}
            wl={str(x) for x in ((cfg or {}).get('item_ids') or []) if str(x)} \
                if isinstance(cfg,dict) and str((cfg or {}).get('mode') or 'legacy').lower()=='manual' else set()
            if wl and str(t.get('item_id') or '') not in wl:
                reason='outside_mop_item_whitelist'
        if reason is None:
            md=db.execute(text("SELECT id,status FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1"),{'a':t['avito_account_id'],'c':t['avito_chat_id']}).mappings().first()
            if md and str(md.get('status') or '')=='waiting_external':
                reason='mop_waiting_external_provider'
            elif md and str(md.get('status') or '')=='draft_ready':
                raw_cfg=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"),{'a':t['avito_account_id']}).scalar()
                try:
                    mop_cfg=json.loads(raw_cfg) if isinstance(raw_cfg,str) else (raw_cfg or {})
                except Exception:
                    mop_cfg={}
                if isinstance(mop_cfg,dict) and 'auto_send' not in mop_cfg:
                    reason='mop_autosend_policy_unset'
        if reason is None:
            continue
        ref=f"brain_task_scope_cleanup:{int(t['id'])}"
        changed=db.execute(text("UPDATE boris_crm_tasks SET status='cancelled',completed_at=now() WHERE id=:id AND status='open' RETURNING id"),{'id':t['id']}).scalar()
        if changed is None: continue
        db.execute(text("""INSERT INTO boris_crm_activities
          (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
          VALUES(:o,:d,:c,'task_cancelled','crm','Ложная задача снята',:body,
                 'brain_task_reconcile',:r,'system',now()) ON CONFLICT DO NOTHING"""),
          {'o':t['owner_user_id'],'d':t['deal_id'],'c':t['contact_id'],'body':reason,'r':ref})
        db.execute(text("""UPDATE boris_crm_deals d SET next_action_at=(
          SELECT min(x.due_at) FROM boris_crm_tasks x WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL),updated_at=now()
          WHERE d.id=:d"""),{'d':t['deal_id']})
        scope_cancelled.append({'task_id':int(t['id']),'reason':reason})
    db.commit()
    return {'action':'reconcile_crm_task_outcomes','result':{'closed':len(closed),'tasks':closed,'mop_handoff_human_reply_closed':len(mop_handoff_closed),'mop_handoff_task_ids':mop_handoff_closed,'already_answered_cancelled':len(already_answered_ids),'already_answered_task_ids':already_answered_ids,'stale_import_cancelled':len(stale_closed),'stale_task_ids':stale_closed,'scope_cancelled':len(scope_cancelled),'scope_tasks':scope_cancelled},'passed':True,
            'verification':'deterministic post-task outcome evidence, provider timestamp, non-dialogue type, or explicit MOP item-authority proof only'}


def _safe_crm_reactivation_handoff(db) -> dict:
    """Complete explicit INTERNAL CRM→reactivation handoffs without contacting a client.

    CRM_REACTIVATION_INTERNAL_HANDOFF_V1
    The control plane may create a canonical task "Передать в реактивацию". Leaving
    that task open makes the owner an operator. This recovery is deliberately narrow:
    open deal + exact Avito chat + enabled Reactivation in recommend mode + no real
    customer inbound after task creation. System messages do not count as a reply.
    It creates/reuses only a reactivation candidate; it never generates a draft,
    calls Avito, or sends a message. The CRM task closes only after candidate truth exists.
    """
    from sqlalchemy import text
    from app import reactivation_core as rc
    rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.created_at,
             d.avito_account_id,d.avito_chat_id
        FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open' AND d.status='open'
         AND t.source='control_plane_internal_plan'
         AND t.title='Передать в реактивацию'
         AND t.due_at<now()
         AND NULLIF(d.avito_account_id,'') IS NOT NULL
         AND NULLIF(d.avito_chat_id,'') IS NOT NULL
       ORDER BY t.id LIMIT 100
    """)).mappings().all()
    changed=[]; skipped=[]
    for r in rows:
        aid=str(r['avito_account_id']); chat=str(r['avito_chat_id'])
        st=rc.settings_for(db,aid)
        if not st or not bool(st.get('enabled')) or str(st.get('mode') or '')!='recommend':
            skipped.append({'task_id':int(r['id']),'reason':'reactivation_not_enabled_recommend'})
            continue
        enabled=list(st.get('reasons_enabled') or [])
        if 'no_reply' not in enabled:
            skipped.append({'task_id':int(r['id']),'reason':'no_reply_reason_not_enabled'})
            continue
        # A genuine customer reply after the task means the stale handoff should not
        # start a reactivation cycle. Avito/system nudges are explicitly excluded.
        real_inbound=db.execute(text("""
          SELECT 1 FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c AND direction='in'
             AND to_timestamp(avito_created_at)>=:created
             AND COALESCE(msg_type,'')<>'system' AND COALESCE(content_type,'')<>'system'
             AND COALESCE(text,'') NOT LIKE '[Системное сообщение]%'
           LIMIT 1
        """),{'a':aid,'c':chat,'created':r['created_at']}).first()
        if real_inbound:
            skipped.append({'task_id':int(r['id']),'reason':'customer_replied_after_handoff_task'})
            continue
        cid,how=rc.create_candidate(db,aid,chat,'no_reply',['no_reply'],r['created_at'],
                                    summary='Передано из подтверждённого внутреннего плана CRM',evidence=[])
        if cid is None:
            skipped.append({'task_id':int(r['id']),'reason':str(how or 'candidate_blocked')})
            continue
        # Exact durable proof before closing the CRM obligation.
        exists=db.execute(text("SELECT 1 FROM reactivation_candidates WHERE id=:i AND account_id=:a AND avito_chat_id=:c LIMIT 1"),{'i':cid,'a':aid,'c':chat}).first()
        if not exists:
            skipped.append({'task_id':int(r['id']),'reason':'candidate_verify_failed'})
            continue
        ref=f"brain_reactivation_handoff:{int(r['id'])}"
        if not db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{'r':ref}).first():
            db.execute(text("""INSERT INTO boris_crm_activities
              (owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at)
              VALUES(:o,:d,:c,'reactivation_handoff','crm','Передано в реактивацию',:b,
                     'brain_reactivation_handoff',:r,'system',now())"""),
              {'o':r['owner_user_id'],'d':r['deal_id'],'c':r['contact_id'],
               'b':f'candidate_id={int(cid)}; mode=recommend; external_action=false','r':ref})
        done=db.execute(text("UPDATE boris_crm_tasks SET status='done',completed_at=now() WHERE id=:id AND status='open' RETURNING id"),{'id':r['id']}).scalar()
        if done is not None:
            db.execute(text("""UPDATE boris_crm_deals d SET next_action_at=(
              SELECT min(x.due_at) FROM boris_crm_tasks x WHERE x.deal_id=d.id AND x.status='open' AND x.due_at IS NOT NULL),updated_at=now()
              WHERE d.id=:d"""),{'d':r['deal_id']})
            changed.append({'task_id':int(r['id']),'candidate_id':int(cid),'candidate_state':how})
    db.commit()
    return {'action':'handoff_crm_tasks_to_reactivation','result':{'changed':len(changed),'tasks':changed,'skipped':skipped},'passed':True,
            'verification':'internal candidate only; recommend mode; no provider/AI/send'}


def _safe_calltracking_orphan_task_link_repair(db) -> dict:
    """CALLTRACKING_ORPHAN_TASK_EXACT_LINK_RECOVERY_V1.

    Repair only open calltracking tasks whose exact description identity
    (account_id + call_id) resolves to one canonical Avito call activity with a
    deal. Contact ids must agree when both sides are present. No fuzzy matching.
    """
    from sqlalchemy import text
    rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.contact_id,t.description,t.assigned_user_id,
             split_part(t.description,':',2) AS account_id,
             split_part(t.description,':',3) AS call_id,
             a.id AS activity_id,a.deal_id AS activity_deal_id,a.contact_id AS activity_contact_id
        FROM boris_crm_tasks t
        JOIN boris_crm_activities a
          ON a.owner_user_id=t.owner_user_id
         AND a.source_ref=(
           'avito_call:' || split_part(t.description,':',2) || ':' ||
           split_part(t.description,':',3)
         )
       WHERE t.status='open' AND t.deal_id IS NULL
         AND t.source='avito_calltracking'
         AND t.description LIKE 'avito_call_task:%:%'
         AND a.deal_id IS NOT NULL
       ORDER BY t.id LIMIT 300
    """)).mappings().all()
    changed=[]
    skipped=[]
    for r in rows:
        if r.get("contact_id") is not None and r.get("activity_contact_id") is not None and int(r["contact_id"])!=int(r["activity_contact_id"]):
            skipped.append({"task_id":int(r["id"]),"reason":"contact_mismatch"})
            continue
        deal=db.execute(text("""
          SELECT id,responsible_user_id,avito_account_id,contact_id
          FROM boris_crm_deals
          WHERE id=:d AND owner_user_id=:o LIMIT 1
        """),{"d":r["activity_deal_id"],"o":r["owner_user_id"]}).mappings().first()
        if not deal or str(deal.get("avito_account_id") or "")!=str(r.get("account_id") or ""):
            skipped.append({"task_id":int(r["id"]),"reason":"deal_account_mismatch"})
            continue
        ref=f"brain_calltracking_task_link:{int(r['id'])}:{int(r['activity_id'])}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{"k":ref})
        updated=db.execute(text("""
          UPDATE boris_crm_tasks
             SET deal_id=:d,
                 contact_id=COALESCE(contact_id,:c),
                 assigned_user_id=COALESCE(assigned_user_id,:assigned)
           WHERE id=:id AND owner_user_id=:o AND deal_id IS NULL
           RETURNING id
        """),{
          "d":int(deal["id"]),
          "c":r.get("activity_contact_id"),
          "assigned":int(deal["responsible_user_id"]) if deal.get("responsible_user_id") is not None else int(r["owner_user_id"]),
          "id":int(r["id"]),"o":int(r["owner_user_id"]),
        }).scalar()
        if updated is None:
            continue
        if not db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{"r":ref}).first():
            db.execute(text("""
              INSERT INTO boris_crm_activities(
                owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                source,source_ref,actor_type,actor_id,metadata_json,created_at
              ) VALUES(
                :o,:d,:c,'task_link_repaired','crm','BORIS восстановил связь задачи со сделкой',
                'Связь восстановлена по точному account_id + call_id calltracking.',
                'brain_calltracking_task_link',:ref,'system','boris',CAST(:meta AS jsonb),now()
              )
            """),{
              "o":int(r["owner_user_id"]),"d":int(deal["id"]),
              "c":r.get("activity_contact_id"),"ref":ref,
              "meta":json.dumps({
                "task_id":int(r["id"]),"activity_id":int(r["activity_id"]),
                "account_id":str(r.get("account_id") or ""),"call_id":str(r.get("call_id") or ""),
                "match":"exact_account_call_id","external_action":False,
                "owner_action_required":False,
              },ensure_ascii=False),
            })
        db.execute(text("""
          UPDATE boris_crm_deals d SET next_action_at=(
            SELECT min(t.due_at) FROM boris_crm_tasks t
             WHERE t.deal_id=d.id AND t.status='open' AND t.due_at IS NOT NULL
          ),updated_at=now() WHERE d.id=:d
        """),{"d":int(deal["id"])})
        changed.append({"task_id":int(r["id"]),"deal_id":int(deal["id"]),"activity_id":int(r["activity_id"])})
    db.commit()
    return {
      "action":"repair_calltracking_task_links",
      "result":{"changed":len(changed),"tasks":changed,"skipped":skipped},
      "passed":True,
      "verification":"exact account_id+call_id activity link only; no fuzzy match/external action",
    }


def _safe_calltracking_task_outcome_reconcile(db) -> dict:
    """CALLTRACKING_TASK_OUTCOME_RECOVERY_V1.

    Historical companion to the ingest-time guard. Reconcile only exact call
    obligations when a later answered Avito call exists for the same contact and
    account. Specific promises are intentionally untouched.
    """
    from sqlalchemy import text
    rows=db.execute(text("""
      SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,t.due_at,t.created_at,
             d.avito_account_id,
             (
               SELECT min(a.created_at)
                 FROM boris_crm_activities a
                WHERE a.contact_id=t.contact_id
                  AND a.source='avito_calltracking'
                  AND a.channel='avito_calltracking'
                  AND COALESCE(a.metadata_json->>'account_id','')=d.avito_account_id
                  AND COALESCE((a.metadata_json->>'missed')::boolean,false)=false
                  AND COALESCE((a.metadata_json->>'talk_duration')::int,0)>0
                  AND a.created_at > CASE
                    WHEN t.title='Перезвонить по пропущенному звонку Avito' THEN t.created_at
                    ELSE t.due_at
                  END
             ) AS later_answered_call_at
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE t.status='open' AND d.status='open'
         AND t.source='avito_calltracking'
         AND t.title IN ('Перезвонить по пропущенному звонку Avito',
                         'Зафиксировать следующий шаг после звонка')
       ORDER BY t.id LIMIT 500
    """)).mappings().all()
    changed=[]
    for r in rows:
        call_at=r.get("later_answered_call_at")
        if call_at is None:
            continue
        if r["title"]=="Перезвонить по пропущенному звонку Avito":
            status="done"; activity="task_completed"; reason="answered_call_after_callback_obligation"
        else:
            status="cancelled"; activity="task_cancelled"; reason="superseded_by_later_answered_call"
        ref=f"brain_calltracking_task_reconcile:{int(r['id'])}:{call_at.isoformat()}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{"k":ref})
        updated=db.execute(text("""
          UPDATE boris_crm_tasks SET status=:s,completed_at=COALESCE(completed_at,now())
           WHERE id=:id AND status='open' RETURNING id
        """),{"s":status,"id":int(r["id"])}).scalar()
        if updated is None:
            continue
        if not db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{"r":ref}).first():
            db.execute(text("""
              INSERT INTO boris_crm_activities(
                owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                source,source_ref,actor_type,actor_id,metadata_json,created_at
              ) VALUES(
                :o,:d,:c,:activity,'crm','BORIS сверил просроченную задачу по звонку',:body,
                'brain_calltracking_task_reconcile',:ref,'system','boris',CAST(:meta AS jsonb),now()
              )
            """),{
              "o":r["owner_user_id"],"d":r["deal_id"],"c":r["contact_id"],
              "activity":activity,"body":reason,"ref":ref,
              "meta":json.dumps({
                "task_id":int(r["id"]),"reason":reason,
                "later_answered_call_at":call_at.isoformat(),
                "account_id":r.get("avito_account_id"),
                "external_action":False,"owner_action_required":False,
              },ensure_ascii=False),
            })
        db.execute(text("""
          UPDATE boris_crm_deals d SET next_action_at=(
            SELECT min(t.due_at) FROM boris_crm_tasks t
             WHERE t.deal_id=d.id AND t.status='open' AND t.due_at IS NOT NULL
          ),updated_at=now() WHERE d.id=:d
        """),{"d":r["deal_id"]})
        changed.append({"task_id":int(r["id"]),"status":status,"reason":reason})
    db.commit()
    return {
      "action":"reconcile_calltracking_task_outcomes",
      "result":{"changed":len(changed),"tasks":changed},
      "passed":True,
      "verification":"same contact/account + later answered Avito call; specific promises untouched; no external action",
    }


def _safe_mop_job_seeker_crm_exclusion(db) -> dict:
    """MOP_JOB_SEEKER_CRM_EXCLUSION_V1.

    Reconcile only high-confidence MOP job-seeker evidence into canonical CRM.
    The evidence must be an exact no_reply_required draft with a closed_no_reply
    event emitted by actor_id=job_seeker_guard and policy
    MOP_JOB_SEEKER_NO_SALES_V1. No text heuristics are re-run here.

    This is internal/reversible only: mark the sales deal lost, cancel its open
    CRM tasks, clear next_action_at, and append one idempotent timeline event.
    No provider call, AI call, customer message, phone call or spend.
    """
    from sqlalchemy import text

    rows=db.execute(text("""
      SELECT md.id AS draft_id,md.account_id,md.avito_chat_id,
             e.id AS evidence_event_id,e.metadata AS evidence_meta,
             d.id AS deal_id,d.owner_user_id,d.contact_id,d.pipeline_id,
             d.stage_id,d.responsible_user_id
        FROM mop_drafts md
        JOIN LATERAL (
          SELECT ev.id,ev.metadata
            FROM mop_draft_events ev
           WHERE ev.draft_id=md.id
             AND ev.event='closed_no_reply'
             AND ev.actor_id='job_seeker_guard'
             AND COALESCE(ev.metadata->>'policy_version','')='MOP_JOB_SEEKER_NO_SALES_V1'
           ORDER BY ev.id DESC LIMIT 1
        ) e ON true
        JOIN boris_crm_deals d
          ON d.avito_account_id=md.account_id
         AND d.avito_chat_id=md.avito_chat_id
       WHERE md.status='no_reply_required'
         AND md.sent_at IS NULL
         AND d.status='open'
       ORDER BY md.id
       LIMIT 100
    """)).mappings().all()

    changed=[]
    missing_lost_stage=[]
    for r in rows:
        lost_stage=db.execute(text("""
          SELECT id FROM boris_crm_stages
           WHERE pipeline_id=:p AND semantic_type='lost'
           ORDER BY position,id LIMIT 1
        """),{"p":r["pipeline_id"]}).scalar()
        if not lost_stage:
            missing_lost_stage.append(int(r["deal_id"]))
            continue

        ref=f"mop_job_seeker_exclusion:{int(r['draft_id'])}:{int(r['deal_id'])}"
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),
            {"k":ref},
        )
        current=db.execute(text("""
          SELECT status FROM boris_crm_deals WHERE id=:d FOR UPDATE
        """),{"d":int(r["deal_id"])}).scalar()
        if current != "open":
            continue

        cancelled=[
            int(x[0]) for x in db.execute(text("""
              UPDATE boris_crm_tasks
                 SET status='cancelled',completed_at=COALESCE(completed_at,now())
               WHERE deal_id=:d AND status='open'
               RETURNING id
            """),{"d":int(r["deal_id"])}).fetchall()
        ]
        reason="Соискатель — нецелевой контакт для воронки продаж"
        db.execute(text("""
          UPDATE boris_crm_deals
             SET status='lost',stage_id=:s,lost_reason=:reason,
                 next_action_at=NULL,updated_at=now()
           WHERE id=:d AND status='open'
        """),{
            "s":int(lost_stage),"reason":reason,"d":int(r["deal_id"]),
        })
        if not db.execute(text("""
          SELECT 1 FROM boris_crm_activities
           WHERE source_ref=:ref LIMIT 1
        """),{"ref":ref}).first():
            db.execute(text("""
              INSERT INTO boris_crm_activities(
                owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                source,source_ref,actor_type,actor_id,metadata_json,created_at
              ) VALUES(
                :o,:d,:c,'lead_excluded','crm',
                'Исключён из продаж: соискатель',
                'MOP подтвердил, что обращение относится к поиску работы. '
                'Сделка закрыта как нецелевая, открытые задачи отменены.',
                'mop_job_seeker_reconcile',:ref,'system','boris',
                CAST(:meta AS jsonb),now()
              )
            """),{
                "o":r["owner_user_id"],"d":int(r["deal_id"]),
                "c":r.get("contact_id"),"ref":ref,
                "meta":json.dumps({
                    "draft_id":int(r["draft_id"]),
                    "evidence_event_id":int(r["evidence_event_id"]),
                    "account_id":str(r["account_id"]),
                    "avito_chat_id":str(r["avito_chat_id"]),
                    "cancelled_task_ids":cancelled,
                    "reason":"job_seeker",
                    "policy_version":"MOP_JOB_SEEKER_CRM_EXCLUSION_V1",
                    "external_action":False,
                    "owner_action_required":False,
                },ensure_ascii=False),
            })
        changed.append({
            "draft_id":int(r["draft_id"]),
            "deal_id":int(r["deal_id"]),
            "cancelled_task_ids":cancelled,
        })
    db.commit()
    return {
        "action":"exclude_job_seekers_from_sales_crm",
        "result":{
            "changed":len(changed),
            "items":changed,
            "missing_lost_stage":missing_lost_stage,
        },
        "passed":not missing_lost_stage,
        "verification":"exact MOP job_seeker_guard event only; CRM-only; no external action",
    }


def _safe_crm_overdue_queue_recovery(db) -> dict:
    """CRM_MANAGER_REMINDER_LIFECYCLE_V2.

    DB-only reminder lifecycle over canonical CRM tasks. It runs only for a fresh
    active paid client snapshot where CRM is an expected module. No Avito send,
    no call, no AI and no owner escalation. Repeated reminders are bucketed and
    idempotent; manager work remains visible in the existing /crm/tasks surface.
    """
    from sqlalchemy import bindparam, text
    from app.services.client_supervisor import load_supervisor_snapshot

    # CRM_ACTIVE_TENANT_OVERDUE_PRIORITY_V1:
    # Historical/inactive tenant backlog must never consume the bounded recovery
    # window before active paid CRM clients are considered. Resolve fresh
    # supervisor eligibility per account first; only then apply LIMIT 500 inside
    # the eligible tenant set. This remains bounded without starving live clients.
    total_overdue=int(db.execute(text("""
      SELECT count(*)
        FROM boris_crm_tasks t
        JOIN boris_crm_deals d ON d.id=t.deal_id
       WHERE d.status='open' AND t.status='open'
         AND t.due_at<now()-interval '1 hour'
    """)).scalar() or 0)
    candidate_accounts=[
        str(r[0]) for r in db.execute(text("""
          SELECT DISTINCT d.avito_account_id
            FROM boris_crm_tasks t
            JOIN boris_crm_deals d ON d.id=t.deal_id
           WHERE d.status='open' AND t.status='open'
             AND t.due_at<now()-interval '1 hour'
             AND COALESCE(d.avito_account_id,'')<>''
           ORDER BY d.avito_account_id
        """)).all() if r and str(r[0] or '').strip()
    ]
    eligibility={}
    for aid in candidate_accounts:
        snap=load_supervisor_snapshot(db,aid,max_age_seconds=300)
        eligibility[aid]=bool(
            snap and snap.get("client_state")=="active"
            and "crm" in list(snap.get("expected_modules") or [])
        )
    active_accounts=[aid for aid in candidate_accounts if eligibility.get(aid)]

    rows=[]
    eligible_total=0
    if active_accounts:
        _eligible_count=text("""
          SELECT count(*)
            FROM boris_crm_tasks t
            JOIN boris_crm_deals d ON d.id=t.deal_id
           WHERE d.status='open' AND t.status='open'
             AND t.due_at<now()-interval '1 hour'
             AND d.avito_account_id IN :active_accounts
        """).bindparams(bindparam("active_accounts", expanding=True))
        eligible_total=int(db.execute(
            _eligible_count,{"active_accounts":active_accounts}
        ).scalar() or 0)
        _eligible_rows=text("""
          SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,t.assigned_user_id,
                 t.due_at,d.avito_account_id,d.avito_chat_id,d.responsible_user_id
            FROM boris_crm_tasks t
            JOIN boris_crm_deals d ON d.id=t.deal_id
           WHERE d.status='open' AND t.status='open'
             AND t.due_at<now()-interval '1 hour'
             AND d.avito_account_id IN :active_accounts
           ORDER BY t.due_at,t.id LIMIT 500
        """).bindparams(bindparam("active_accounts", expanding=True))
        rows=db.execute(
            _eligible_rows,{"active_accounts":active_accounts}
        ).mappings().all()

    now=datetime.now(timezone.utc)
    reminded=0
    skipped_inactive=max(0,total_overdue-eligible_total)
    levels={}
    for r in rows:
        aid=str(r.get("avito_account_id") or "")

        due=r.get("due_at")
        if due is None:
            continue
        if due.tzinfo is None:
            due=due.replace(tzinfo=timezone.utc)
        age_sec=max(0,int((now-due).total_seconds()))
        if age_sec < 4*3600:
            level="manager_reminder"; interval_sec=4*3600
        elif age_sec < 24*3600:
            level="manager_repeat"; interval_sec=4*3600
        elif age_sec < 72*3600:
            level="manager_urgent"; interval_sec=12*3600
        else:
            level="manager_critical"; interval_sec=12*3600
        bucket=max(1,int(age_sec//interval_sec))
        target_user_id=r.get("assigned_user_id") or r.get("responsible_user_id")
        ref=f"brain_crm_overdue:{int(r['id'])}:{interval_sec}:{bucket}"

        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{"k":ref})
        if db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{"r":ref}).first():
            continue
        prior=int(db.execute(text("""
          SELECT count(*) FROM boris_crm_activities
           WHERE source='brain_crm_overdue_reminder'
             AND COALESCE(metadata_json->>'task_id','')=:task_id
        """),{"task_id":str(int(r["id"]))}).scalar() or 0)
        reminder_count=prior+1
        hours=max(1,age_sec//3600)
        body=(
          f"Задача просрочена примерно на {hours} ч. "
          f"BORIS напоминает ответственному сотруднику. "
          f"Внутренняя эскалация: {level}. "
          "Владельцу действие не требуется."
        )
        meta=json.dumps({
          "task_id":int(r["id"]),"account_id":aid,
          "assigned_user_id":int(target_user_id) if target_user_id is not None else None,
          "due_at":due.isoformat(),"overdue_seconds":age_sec,
          "escalation_level":level,"reminder_count":reminder_count,
          "external_action":False,"owner_action_required":False,
          "surface":"crm_tasks",
        },ensure_ascii=False)
        db.execute(text("""
          INSERT INTO boris_crm_activities(
            owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
            source,source_ref,actor_type,actor_id,metadata_json,created_at
          ) VALUES(
            :o,:d,:c,'task_reminder','crm',:title,:body,
            'brain_crm_overdue_reminder',:ref,'system','boris',CAST(:meta AS jsonb),now()
          )
        """),{
          "o":r["owner_user_id"],"d":r["deal_id"],"c":r["contact_id"],
          "title":"Напоминание менеджеру: просрочена CRM-задача",
          "body":body,"ref":ref,"meta":meta,
        })
        reminded+=1
        levels[level]=int(levels.get(level) or 0)+1

    db.commit()
    return {
      "action":"remind_overdue_crm_work",
      "result":{
        "reminded":reminded,"eligible_rows":len(rows),
        "eligible_total":eligible_total,
        "eligible_backlog_remaining":max(0,eligible_total-len(rows)),
        "candidate_accounts":len(candidate_accounts),
        "eligible_accounts":len(active_accounts),
        "scanned":len(rows),"skipped_inactive_or_crm_off":skipped_inactive,
        "levels":levels,
      },
      "passed":True,
      "verification":"fresh paid client snapshot + CRM expected; DB-only manager reminder; no owner/external action",
    }


def run_safe_recovery(db) -> dict:
    """Run bounded, idempotent L1 recovery. One failed action cannot stop others."""
    actions=[]
    runners=(
        ("marketing",lambda:_safe_marketer_runtime_recovery(db)),
        ("crm",lambda:_safe_crm_next_action_recovery(db)),
        ("crm_assignment_phone_handoff",lambda:_safe_crm_assignment_phone_handoff_recovery(db)),
        ("crm_task_enrich",lambda:_safe_crm_task_existing_analysis_enrich(db)),
        ("crm_task_evidence",lambda:_safe_crm_task_evidence_reconcile(db)),
        ("crm_calltracking_links",lambda:_safe_calltracking_orphan_task_link_repair(db)),
        ("crm_calltracking_outcomes",lambda:_safe_calltracking_task_outcome_reconcile(db)),
        ("crm_reactivation_handoff",lambda:_safe_crm_reactivation_handoff(db)),
        ("crm_job_seeker_exclusion",lambda:_safe_mop_job_seeker_crm_exclusion(db)),
        ("crm_overdue",lambda:_safe_crm_overdue_queue_recovery(db)),
        ("mop",lambda:_safe_mop_recovery(db)),
        ("feed_factory",_safe_feed_recovery),
        ("email",_safe_email_recovery),
        ("sitebuild",_safe_sitebuild_recovery),
        ("prospecting",_safe_prospecting_scheduler_recovery),
        ("social",lambda:_safe_social_recovery(db)),
        ("reactivation",lambda:_safe_reactivation_recovery(db)),
        ("rop",lambda:_safe_rop_scheduler_recovery(db)),
    )
    for module,fn in runners:
        try:
            item=fn() or {"passed":False}
            item["module"]=module
            item["error_type"]=None
        except Exception as exc:
            item={"module":module,"action":"safe_recovery","passed":False,"error_type":type(exc).__name__}
        actions.append(item)
    return {
        "status":"pass" if all(bool(x.get("passed")) for x in actions) else "degraded",
        "actions":actions,
        "changed_actions":sum(1 for x in actions if (x.get("result") or {}).get("changed") or (x.get("result") or {}).get("delivery_unknown_recovered") or (x.get("result") or {}).get("created") or (x.get("result") or {}).get("answered_externally") or (x.get("result") or {}).get("human_required") or (x.get("result") or {}).get("restarted") or (x.get("result") or {}).get("reminded") or (x.get("result") or {}).get("surfaced")),
        "checked_at":datetime.now(timezone.utc).isoformat(),
    }
