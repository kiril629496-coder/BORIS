from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request, Header, Depends
from fastapi.responses import Response, StreamingResponse
from app.api.auth import get_current_user_or_internal, get_current_user, require_owner
from app.services.telephony_core import (
    ensure_schema, provider_catalog, provider_status, register_device, heartbeat_device, update_device_push_token, clear_device_push_token, device_runtime_health, set_device_extension,
    list_devices, list_calls, call_metrics, request_outbound_call, create_call, apply_event,
    route_list, route_save, resolve_route, queue_call_command, command_list,
    recording_upsert, recording_upsert_for_provider_call, recording_list, callback_guardian, request_media_session, activate_media_session, renew_media_session, release_media_session, report_media_transport_proof,
    provider_config_public, provider_credentials, provider_webhook_secret, save_provider_config, verify_provider_connection, ingest_provider_event, verify_generic_webhook, claim_signed_native_webhook,
    event_feed, audit_list, add_cost, cost_breakdown, attach_recording_file, process_pending_recordings,
    revoke_device, client_bootstrap, call_detail, save_call_disposition, ringing_for_device, call_targets, sweep_ring_targets, sweep_stale_devices, push_outbox, ack_mobile_push, telephony_analytics, evaluate_call_quality, quality_dashboard,
    ingest_transcript_chunk, add_realtime_ai_suggestion, realtime_call_context, dismiss_realtime_suggestion,
    telephony_report_settings, save_telephony_report_settings, build_rop_phone_report, deliver_rop_phone_report, coaching_plans, link_coaching_sparring, complete_coaching_plan, copilot_settings, save_copilot_settings, copilot_guard, human_call_policy, set_recording_notice_status, recording_policy_settings, save_recording_policy_settings, afterhours_settings, save_afterhours_settings, afterhours_status, autoanswer_decision, autoanswer_analytics, autoanswer_readiness, autoanswer_preview, afterhours_receptionist_turn, finalize_afterhours_callback, afterhours_cost_estimate, minute_package, save_minute_package, record_minute_usage, minute_usage_dashboard, voice_agent_settings, save_voice_agent_settings, voice_agent_guard, voice_agent_start, voice_agent_context, voice_agent_complete, voice_agent_dialog_turn, voice_agent_handoff, voice_agent_transcript, voice_agent_qualification, update_voice_agent_qualification, voice_agent_next_question, voice_agent_finalize_qualification, voice_agent_ingest_customer_turn, voice_agent_objection_response, minute_package_forecast, minute_package_guardian, minute_alerts, phone_pricing_lab, phone_owner_economics, voice_agent_disclosure, voice_agent_callback_phone, voice_agent_safe_complete, voice_agent_lifecycle_turn, voice_agent_inbound_lifecycle_start, voice_agent_fallback_ivr, voice_agent_post_call_finalize, callback_funnel_analytics, phone_funnel_rop_report, phone_source_roi, phone_package_recommendation, phone_owner_dashboard, phone_client_readiness, client_release_status,
    active_phone_entitlements, phone_entitlement_status, provision_paid_phone_entitlement, revoke_phone_entitlement,
)

router = APIRouter(prefix='/api/telephony', tags=['telephony'])
public_router = APIRouter(prefix='/api/telephony/public', tags=['telephony-public'])

def _safe_recording_link_failure(link):
    """Never expose provider SDK/HTTP error text from public recording webhooks."""
    import re
    status=re.sub(r'[^A-Za-z0-9._:-]+','_',str(getattr(link,'status',None) or 'recording_link_failed'))[:100] or 'recording_link_failed'
    return {'status':status,'recording':False,'message':'Не удалось безопасно получить запись у оператора','error_code':'provider_'+status}

def _actor_user_id(current_user, fallback=None):
    if current_user is None: return fallback
    return getattr(current_user,'id',None) or (current_user.get('id') if isinstance(current_user,dict) else None)

_MCN_WATCH_ACCOUNT = "__mcn_mailbox_watch__"
_MCN_WATCH_KEY = "mcn_mailbox_auto_onboard_runtime"


def _require_private_platform_owner(user=Depends(get_current_user)):
    """Strict identity gate for BORIS-wide MCN onboarding and legal disclosure."""
    from app.services.platform_roles import is_private_platform_owner
    if not is_private_platform_owner(user):
        raise HTTPException(403, 'Доступ только для приватного владельца платформы')
    return user


def _require_active_phone_entitlement(account_id: str) -> dict:
    """Commercial boundary for user-triggered Phone provider operations."""
    state = phone_entitlement_status(str(account_id or "").strip())
    if not bool(state.get("active")):
        raise HTTPException(
            409,
            {
                "code": "phone_entitlement_required",
                "message": "Для подключения оператора нужен активный оплаченный BORIS Phone.",
            },
        )
    return state


def _mcn_onboarding_raw_state() -> dict:
    import json as _json
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    db = SessionLocal()
    try:
        row = db.execute(_sql_text("""
            SELECT value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": _MCN_WATCH_ACCOUNT, "k": _MCN_WATCH_KEY}).first()
    finally:
        db.close()
    if not row or not row[0]:
        return {}
    try:
        payload = _json.loads(str(row[0]))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _mcn_eligible_phone_accounts() -> list[dict]:
    """Owner-safe list of currently paid Phone targets; no provider secrets."""
    rows = active_phone_entitlements()
    return [{
        "account_id": str(row.get("account_id") or "")[:160],
        "display_name": str(row.get("display_name") or row.get("account_id") or "")[:240],
        "paid_until": str(row.get("paid_until") or "")[:80] or None,
        "entitlement_source": str(row.get("entitlement_source") or row.get("source") or "")[:80] or None,
    } for row in rows if str(row.get("account_id") or "").strip()]


def _mcn_onboarding_safe_projection(raw: dict|None=None) -> dict:
    src = raw if isinstance(raw, dict) else _mcn_onboarding_raw_state()
    action = src.get("primary_next_action") if isinstance(src.get("primary_next_action"), dict) else {}
    operational = src.get("operational_reply") if isinstance(src.get("operational_reply"), dict) else {}
    draft = src.get("company_card_draft") if isinstance(src.get("company_card_draft"), dict) else {}
    sent_evidence = src.get("company_card_sent_evidence") if isinstance(src.get("company_card_sent_evidence"), dict) else {}
    candidate = src.get("candidate_evidence") if isinstance(src.get("candidate_evidence"), dict) else {}
    candidate_fields = candidate.get("fields_found") if isinstance(candidate.get("fields_found"), dict) else {}
    safe_candidate = {
        "uid": str(candidate.get("uid") or "")[:80] or None,
        "date": str(candidate.get("date") or "")[:160] or None,
        "sender_domain": str(candidate.get("sender_domain") or "")[:120] or None,
        "subject": str(candidate.get("subject") or "")[:300] or None,
        "auth_mode": str(candidate.get("auth_mode") or "")[:40] or None,
        "fields_found": {
            "registrar": bool(candidate_fields.get("registrar")),
            "username": bool(candidate_fields.get("username")),
            "password": bool(candidate_fields.get("password")),
            "did": bool(candidate_fields.get("did")),
            "source_ip": bool(candidate_fields.get("source_ip")),
        },
    }
    safe_action = {
        "code": str(action.get("code") or "")[:120] or None,
        "actor": str(action.get("actor") or "")[:40] or None,
        "owner_action_required": bool(action.get("owner_action_required")),
        "text": str(action.get("text") or "")[:800] or None,
    }
    safe_operational = {
        "code": str(operational.get("code") or "")[:120] or None,
        "date": str(operational.get("date") or "")[:160] or None,
        "subject": str(operational.get("subject") or "")[:300] or None,
        "sender_domain": str(operational.get("sender_domain") or "")[:120] or None,
        "requirements": [str(x)[:80] for x in (operational.get("requirements") or [])[:10]],
        "provider_path": [str(x)[:80] for x in (operational.get("provider_path") or [])[:10]],
    }
    safe_draft = {
        "status": str(draft.get("status") or "")[:80] or None,
        "reason": str(draft.get("reason") or "")[:120] or None,
        "ready": bool(draft.get("ready")),
        "sent": bool(draft.get("sent")),
        "delivery_ambiguous": bool(draft.get("delivery_ambiguous")),
        "card_fingerprint": str(draft.get("card_fingerprint") or "")[:64] or None,
    }
    safe_sent_evidence = {
        "sent": bool(sent_evidence.get("sent")),
        "source": str(sent_evidence.get("source") or "")[:80] or None,
        "reason": str(sent_evidence.get("reason") or "")[:120] or None,
        "sent_at": str(sent_evidence.get("sent_at") or "")[:100] or None,
        "delivery_ambiguous": bool(sent_evidence.get("delivery_ambiguous")),
    }
    approval_supported = bool(
        safe_action["code"] == "mcn_company_card_send_approval"
        and safe_action["owner_action_required"]
        and safe_draft["ready"]
        and bool(safe_draft["card_fingerprint"])
        and safe_operational["sender_domain"] == "mcn.ru"
    )
    manual_delivery_confirmation_supported = bool(
        safe_action["code"] == "mcn_company_card_delivery_verify"
        and safe_action["owner_action_required"]
        and safe_sent_evidence["delivery_ambiguous"]
        and safe_sent_evidence["reason"] == "unverified_manual_attachment_after_request"
        and bool(safe_sent_evidence["sent_at"])
        and safe_operational["sender_domain"] == "mcn.ru"
    )
    eligible_phone_accounts = (
        _mcn_eligible_phone_accounts()
        if safe_action["code"] == "mcn_phone_account_binding_required"
        else []
    )
    account_binding_supported = bool(
        safe_action["code"] == "mcn_phone_account_binding_required"
        and safe_action["owner_action_required"]
        and safe_candidate["sender_domain"] == "mcn.ru"
        and bool(safe_candidate["uid"])
        and bool(safe_candidate["date"])
        and len(eligible_phone_accounts) >= 2
    )
    phone_entitlement_required = bool(
        safe_action["code"] == "mcn_phone_entitlement_required"
        and safe_action["owner_action_required"]
    )
    return {
        "status": str(src.get("status") or "not_initialized")[:80],
        "reason": str(src.get("reason") or "")[:120] or None,
        "phone_account_status": str(src.get("phone_account_status") or "")[:80] or None,
        "owner_action_required": bool(src.get("owner_action_required")),
        "primary_next_action": safe_action,
        "operational_reply": safe_operational,
        "company_card_draft": safe_draft,
        "company_card_sent_evidence": safe_sent_evidence,
        "candidate_evidence": safe_candidate,
        "eligible_phone_accounts": eligible_phone_accounts,
        "approval_supported": approval_supported,
        "manual_delivery_confirmation_supported": manual_delivery_confirmation_supported,
        "account_binding_supported": account_binding_supported,
        "phone_entitlement_required": phone_entitlement_required,
        "truth": "safe onboarding projection; no SIP password, DID, registrar or banking values are returned",
    }


def _run_mcn_company_card_send(mailbox_id: int) -> dict:
    import json as _json
    import subprocess as _subprocess
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[2]
    script = root / "scripts" / "phone-mcn-company-card-send.py"
    python_bin = root / "venv" / "bin" / "python"
    try:
        cp = _subprocess.run(
            [
                str(python_bin), str(script),
                "--mailbox-id", str(int(mailbox_id)),
                "--apply",
                "--confirm-share-banking",
            ],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except _subprocess.TimeoutExpired:
        return {"status": "delivery_ambiguous", "reason": "sender_timeout", "retry_blocked": True}
    except Exception as exc:
        return {
            "status": "send_runner_error",
            "reason": type(exc).__name__[:120],
            "retry_blocked": True,
        }
    try:
        raw = _json.loads((cp.stdout or "").strip() or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "status": str(raw.get("status") or "invalid_sender_result")[:80],
        "reason": str(raw.get("reason") or "")[:160] or None,
        "message_id": str(raw.get("message_id") or "")[:500] or None,
        "sent_copy_saved": bool(raw.get("sent_copy_saved")),
        "send_state_persisted": bool(raw.get("send_state_persisted")),
        "retry_blocked": bool(raw.get("retry_blocked")),
        "recipient_domain_verified": bool(raw.get("recipient_domain_verified")),
        "returncode": int(cp.returncode),
    }


def _run_mcn_account_binding(
    mailbox_id: int,
    account_id: str,
    expected_uid: str,
    expected_date: str,
) -> dict:
    """Re-scan the current MCN letter and atomically apply it to one paid Phone account."""
    import json as _json
    import subprocess as _subprocess
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[2]
    script = root / "scripts" / "phone-mcn-mailbox-onboard.py"
    python_bin = root / "venv" / "bin" / "python"
    try:
        cp = _subprocess.run(
            [
                str(python_bin), str(script),
                "--mailbox-id", str(int(mailbox_id)),
                "--limit", "40",
                "--account-id", str(account_id),
                "--expected-uid", str(expected_uid),
                "--expected-date", str(expected_date),
                "--apply",
            ],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except _subprocess.TimeoutExpired:
        return {"status": "blocked", "reason": "mcn_binding_timeout", "returncode": 124}
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": ("mcn_binding_runner_error:" + type(exc).__name__)[:160],
            "returncode": 125,
        }
    try:
        raw = _json.loads((cp.stdout or "").strip() or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    return {
        "status": str(raw.get("status") or "blocked")[:80],
        "reason": str(raw.get("reason") or "")[:160] or None,
        "account_id": str(raw.get("account_id") or account_id)[:160],
        "message_uid": str(message.get("uid") or "")[:80] or None,
        "message_date": str(message.get("date") or "")[:160] or None,
        "provider_config_status": str(
            ((raw.get("provider_config") or {}).get("status"))
            if isinstance(raw.get("provider_config"), dict) else ""
        )[:80] or None,
        "readiness_ready": bool(
            (raw.get("readiness") or {}).get("ready")
            if isinstance(raw.get("readiness"), dict) else False
        ),
        "returncode": int(cp.returncode),
        "truth": "MCN credentials were consumed server-side and are not returned",
    }


def _assert_internal_local(request: Request):
    host=request.client.host if request.client else None
    if host not in {'127.0.0.1','::1','localhost'}:
        raise HTTPException(403,'Internal telephony endpoint')

def _assert_telephony_account_access(account_id: str, current_user, owner_only: bool=False):
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    uid=_actor_user_id(current_user); role=getattr(current_user,'role',None) if current_user is not None else None
    if isinstance(current_user,dict): role=current_user.get('role',role)
    # Internal trusted caller is permitted; authenticated users are scoped below.
    if uid is None:
        if owner_only and role not in (None,'owner'): raise HTTPException(403,'Только владелец')
        return
    db=SessionLocal()
    try:
        row=db.execute(_sql_text('SELECT owner_user_id FROM accounts WHERE account_id=:a'),{'a':account_id}).first()
        if not row: raise HTTPException(404,'Аккаунт не найден')
        if owner_only:
            if role!='owner' or int(row[0] or 0)!=int(uid): raise HTTPException(403,'Нет доступа к внутренней экономике аккаунта')
            return
        if int(row[0] or 0)==int(uid): return
        allowed=db.execute(_sql_text('SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE'),{'u':uid,'a':account_id}).first()
        if not allowed: raise HTTPException(403,'Нет доступа к аккаунту')
    finally: db.close()


def _assert_account_owner(account_id: str, current_user):
    """Allow the real owner of this BORIS client account, without granting platform-owner economics."""
    if current_user is None:
        return
    uid=_actor_user_id(current_user)
    if uid is None:
        raise HTTPException(403,'Только владелец аккаунта')
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    db=SessionLocal()
    try:
        owner=db.execute(_sql_text('SELECT owner_user_id FROM accounts WHERE account_id=:a'),{'a':account_id}).scalar()
        if owner is None:
            raise HTTPException(404,'Аккаунт не найден')
        if int(owner)!=int(uid):
            raise HTTPException(403,'Подключение оператора доступно только владельцу аккаунта')
    finally:
        db.close()


def _assert_device_access(account_id: str, device_id: str, current_user, owner_ok: bool=True):
    # Managers may operate only devices registered to the same BORIS user.
    if current_user is None: return
    uid=_actor_user_id(current_user); role=getattr(current_user,'role',None)
    if isinstance(current_user,dict): role=current_user.get('role',role)
    if owner_ok and role=='owner': return
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    db=SessionLocal()
    try:
        row=db.execute(_sql_text('SELECT user_id,revoked_at FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':account_id,'d':device_id}).first()
        if not row: raise HTTPException(404,'Устройство не найдено')
        if row[1] is not None: raise HTTPException(403,'Устройство отключено')
        if uid is None or row[0] is None or int(row[0])!=int(uid): raise HTTPException(403,'Нет доступа к устройству')
    finally: db.close()


def _assert_call_control_access(account_id: str, call_id: str, current_user, device_id: str|None=None):
    """Employee-level isolation for destructive/live call controls.

    Account visibility is not sufficient authority to control another manager's
    live call. Platform/account owners may operate the fleet. A manager may act
    only on a call already bound to that user, or on a ringing target delivered
    to one of that same user's authenticated devices.
    """
    if current_user is None:
        return
    uid=_actor_user_id(current_user)
    role=getattr(current_user,'role',None)
    if isinstance(current_user,dict): role=current_user.get('role',role)
    if role=='owner':
        return
    if uid is None:
        raise HTTPException(403,'Нет доступа к управлению звонком')
    d=str(device_id or '').strip()
    if d:
        _assert_device_access(account_id,d,current_user,owner_ok=False)
    from app.db.session import SessionLocal
    from sqlalchemy import text as _sql_text
    db=SessionLocal()
    try:
        call=db.execute(_sql_text('SELECT user_id,device_id,state FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':account_id,'c':call_id}).mappings().first()
        if not call:
            raise HTTPException(404,'Звонок не найден')
        if call.get('user_id') is not None and int(call.get('user_id'))==int(uid):
            if not d or not call.get('device_id') or str(call.get('device_id'))==d:
                return
        if d:
            targeted=db.execute(_sql_text("""SELECT 1 FROM telephony_call_targets
              WHERE account_id=:a AND call_id=:c AND device_id=:d
                AND status IN ('ringing','answered') LIMIT 1"""),{'a':account_id,'c':call_id,'d':d}).first()
            if targeted:
                return
        raise HTTPException(403,'Этот звонок назначен другому сотруднику или устройству')
    finally:
        db.close()


@router.get('/status')
def status(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return provider_status(account_id)

@router.get('/providers')
def providers():
    return {'status':'ok','items':provider_catalog()}

@router.get('/calls')
def calls(account_id: str, days: int=30, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return {'status':'ok','items':list_calls(account_id,days,limit),'metrics':call_metrics(account_id,days)}

@router.get('/devices')
def devices(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    items=list_devices(account_id)
    uid=_actor_user_id(current_user); role=getattr(current_user,'role',None) if current_user is not None else None
    if isinstance(current_user,dict): role=current_user.get('role',role)
    # Managers see only their own registered devices. Account owner sees the fleet.
    if current_user is not None and role!='owner' and uid is not None:
        items=[x for x in items if str(x.get('user_id') or '')==str(uid)]
    return {'status':'ok','items':items}

@router.get('/devices/ringing')
def device_ringing(account_id: str, device_id: str, current_user=Depends(get_current_user_or_internal)):
    if not device_id: raise HTTPException(400,'device_id обязателен')
    _assert_telephony_account_access(account_id,current_user); _assert_device_access(account_id,device_id,current_user); return {'status':'ok','items':ringing_for_device(account_id,device_id)}

@router.post('/devices/register')
def device_register(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    result=register_device(account_id, _actor_user_id(current_user,body.get('user_id')), str(body.get('name') or 'BORIS Web'),
        str(body.get('platform') or 'web'), str(body.get('app_version') or ''), body.get('capabilities') or {},
        body.get('device_id'), body.get('push_kind'), body.get('push_token'))
    if result.get('status') in {'invalid_push_kind','invalid_push_token','push_platform_mismatch','push_not_supported_for_platform'}:
        raise HTTPException(400,'Некорректная конфигурация push для устройства')
    return result

@router.post('/devices/push-token')
def device_push_token(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip()
    if not account_id or not device_id: raise HTTPException(400,'account_id и device_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    result=update_device_push_token(account_id,device_id,str(body.get('push_kind') or ''),str(body.get('push_token') or ''))
    if result.get('status') in {'invalid_push_kind','invalid_push_token','push_platform_mismatch','push_not_supported_for_platform'}: raise HTTPException(400,'Некорректный push token для платформы')
    return result

@router.post('/devices/push-receipt')
def device_push_receipt(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip()
    if not account_id or not device_id: raise HTTPException(400,'account_id и device_id обязательны')
    _assert_telephony_account_access(account_id,current_user); _assert_device_access(account_id,device_id,current_user)
    result=ack_mobile_push(account_id,device_id,body.get('push_id'),str(body.get('via') or 'native'),str(body.get('receipt_token') or ''))
    if result.get('status')=='invalid_push_id': raise HTTPException(400,'Некорректный push_id')
    return result

@router.post('/devices/push-token/clear')
def device_push_token_clear(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip()
    if not account_id or not device_id: raise HTTPException(400,'account_id и device_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return clear_device_push_token(account_id,device_id)

@router.post('/devices/{device_id}/extension')
def device_extension(device_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    result=set_device_extension(account_id,device_id,body.get('extension'))
    if result.get('status')=='invalid_extension': raise HTTPException(400,result.get('message'))
    return result

@router.get('/devices/{device_id}/health')
def device_health(device_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return device_runtime_health(account_id,device_id)

@router.post('/devices/heartbeat')
def device_heartbeat(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or ''); d=str(body.get('device_id') or ''); _assert_telephony_account_access(a,current_user); _assert_device_access(a,d,current_user); return heartbeat_device(a,d,str(body.get('presence') or 'online'))

@router.post('/call')
def start_call(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); to=str(body.get('to_number') or '').strip()
    if not account_id or not to: raise HTTPException(400,'account_id и to_number обязательны')
    _assert_telephony_account_access(account_id,current_user)
    if body.get('device_id'): _assert_device_access(account_id,str(body.get('device_id')),current_user)
    result=request_outbound_call(account_id,to,_actor_user_id(current_user,body.get('user_id')),body.get('device_id'),body.get('source'),body.get('source_ref'),body.get('idempotency_key'))
    if result.get('status')!='ok':
        return result
    return result

# Protected synthetic/runtime QA hooks. Real provider webhooks will get separate signed public adapters.
@router.post('/internal/call')
def internal_create_call(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    return {'status':'ok','call':create_call(account_id,str(body.get('direction') or 'inbound'),str(body.get('from_number') or ''),
        str(body.get('to_number') or ''),body.get('provider'),body.get('provider_call_id'),body.get('user_id'),body.get('device_id'),
        body.get('source'),body.get('source_ref'),body.get('metadata') or {})}

@router.post('/internal/event')
def internal_event(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    return apply_event(account_id,str(body.get('event_type') or ''),body.get('call_id'),body.get('provider'),body.get('provider_call_id'),
        body.get('provider_event_id'),body.get('payload') or {})


@router.get('/routes')
def routes(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return {'status':'ok','items':route_list(account_id)}

@router.post('/routes')
def save_route(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return route_save(account_id,body)

@router.get('/routes/resolve')
def route_resolve(account_id: str, source: str|None=None, source_ref: str|None=None, to_number: str|None=None, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return resolve_route(account_id,source,source_ref,to_number)

@router.post('/calls/{call_id}/control')
def control_call(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    device_id=str(body.get('device_id') or '').strip() or None
    _assert_call_control_access(account_id,call_id,current_user,device_id)
    return queue_call_command(account_id,call_id,str(body.get('command') or ''),body.get('payload') or {},_actor_user_id(current_user,body.get('user_id')),device_id,body.get('idempotency_key'))


@router.post('/calls/{call_id}/disposition')
def disposition(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    # Post-call outcome/callback mutates CRM state. A manager may update only a
    # call assigned to that manager; account-wide read permission is insufficient.
    _assert_call_control_access(account_id,call_id,current_user,None)
    uid=_actor_user_id(current_user)
    return save_call_disposition(account_id,call_id,body.get('qualification'),body.get('summary'),body.get('next_action'),body.get('callback_at'),uid)

@router.get('/commands')
def commands(account_id: str, call_id: str|None=None, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return {'status':'ok','items':command_list(account_id,call_id,limit)}

@router.get('/recordings')
def recordings(account_id: str, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return {'status':'ok','items':recording_list(account_id,limit)}

@router.post('/internal/recording')
def internal_recording(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    if not account_id or not call_id: raise HTTPException(400,'account_id и call_id обязательны')
    return recording_upsert(account_id,call_id,body.get('provider'),body.get('provider_recording_id'),body.get('source_url'),body.get('duration_sec'))

@router.post('/internal/callback-guardian')
def callback_guard(request: Request, body: dict=Body(default={})):
    _assert_internal_local(request)
    return callback_guardian(str(body.get('account_id') or '').strip() or None)


@router.get('/provider-config')
def get_provider_config(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); _assert_account_owner(account_id,current_user); return provider_config_public(account_id)

@router.post('/provider-config')
def set_provider_config(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); provider=str(body.get('provider') or '').strip()
    if not account_id or not provider: raise HTTPException(400,'account_id и provider обязательны')
    _assert_telephony_account_access(account_id,current_user); _assert_account_owner(account_id,current_user)
    _require_active_phone_entitlement(account_id)
    public_config=body.get('public_config') if 'public_config' in body else None
    return save_provider_config(account_id,provider,body.get('credentials'),public_config,body.get('webhook_secret'),_actor_user_id(current_user,body.get('actor_user_id')))

@router.post('/provider-config/verify')
def verify_provider_config(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user); _assert_account_owner(account_id,current_user)
    _require_active_phone_entitlement(account_id)
    return verify_provider_connection(account_id,_actor_user_id(current_user,body.get('actor_user_id')))

def _stream_access_still_allowed(account_id: str, current_user) -> bool:
    # Long-lived SSE must not retain account visibility after an access grant is
    # revoked. Re-read account ownership/user_account_access periodically rather
    # than trusting only the authorization decision made when the stream opened.
    try:
        _assert_telephony_account_access(account_id,current_user)
        return True
    except HTTPException:
        return False


@router.get('/events')
def events(account_id: str, after_id: int=0, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); items=event_feed(account_id,after_id,limit)
    return {'status':'ok','items':items,'next_after_id':items[-1]['id'] if items else after_id}

@router.get('/events/stream')
async def events_stream(request: Request, account_id: str, after_id: int=0, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    import asyncio, json as _json
    try: last_event_id=max(0,int(str(request.headers.get('last-event-id') or '0').strip() or 0))
    except Exception: last_event_id=0
    resume_after=max(0,int(after_id),last_event_id)
    async def gen():
        cursor=resume_after; idle_cycles=0; heartbeat_at=asyncio.get_running_loop().time()+15.0
        yield 'retry: 2000\n\n'
        while True:
            if await request.is_disconnected(): break
            items=event_feed(account_id,cursor,100)
            if items:
                # Re-check authorization before disclosing any newly arrived batch.
                # A grant may be revoked between heartbeat checks; event arrival
                # must never create a disclosure window for an already-open stream.
                if not _stream_access_still_allowed(account_id,current_user):
                    break
                for item in items:
                    cursor=max(cursor,int(item.get('id') or 0)); payload=_json.dumps(item,ensure_ascii=False,default=str,separators=(',',':'))
                    yield f"id: {cursor}\nevent: telephony\ndata: {payload}\n\n"
                idle_cycles=0; sleep_for=0.5
            else:
                idle_cycles=min(idle_cycles+1,20)
                # Reduce PostgreSQL polling pressure for idle phones without harming active-call responsiveness.
                sleep_for=0.5 if idle_cycles<4 else (1.0 if idle_cycles<10 else 2.0)
            now=asyncio.get_running_loop().time()
            if now>=heartbeat_at:
                if not _stream_access_still_allowed(account_id,current_user):
                    break
                yield f": heartbeat {cursor}\n\n"; heartbeat_at=now+15.0
            await asyncio.sleep(sleep_for)
    return StreamingResponse(gen(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no','Connection':'keep-alive'})

@router.get('/audit')
def audit(account_id: str, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return {'status':'ok','items':audit_list(account_id,limit)}

@router.get('/push-outbox')
def push_queue(account_id: str, limit: int=100, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return {'status':'ok','items':push_outbox(account_id,limit)}

@router.get('/costs')
def costs(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return cost_breakdown(account_id,days)

@router.get('/analytics')
def analytics(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return telephony_analytics(account_id,days)

@router.post('/internal/cost')
def internal_cost(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    return add_cost(account_id,body.get('call_id'),str(body.get('category') or 'telephony'),float(body.get('amount_rub') or 0),
                    body.get('provider'),body.get('units'),body.get('unit_name'),body.get('idempotency_key'),body.get('metadata') or {})

@public_router.post('/webhook/{account_id}/{provider}')
async def generic_provider_webhook(account_id: str, provider: str, request: Request, x_boris_signature: str|None=Header(default=None), x_boris_timestamp: str|None=Header(default=None), x_boris_nonce: str|None=Header(default=None)):
    raw=await request.body()
    if len(raw)>262144: raise HTTPException(413,'payload too large')
    if not verify_generic_webhook(account_id,raw,x_boris_signature or '',x_boris_timestamp,x_boris_nonce,provider=provider):
        raise HTTPException(401,'invalid or replayed signature')
    try:
        import json
        event=json.loads(raw.decode('utf-8'))
    except Exception:
        raise HTTPException(400,'invalid json')
    result=ingest_provider_event(account_id,provider,event)
    if result.get('status') in {'invalid_provider','invalid_event'}:
        raise HTTPException(400,result)
    return result


@public_router.post('/native/zadarma/{account_id}')
async def zadarma_native_webhook(account_id: str, request: Request):
    """Signed Zadarma PBX NOTIFY_* ingress into the canonical telephony engine."""
    raw=await request.body()
    if len(raw)>262144: raise HTTPException(413,'payload too large')
    content_type=str(request.headers.get('content-type') or '').lower()
    if 'application/x-www-form-urlencoded' not in content_type and 'multipart/form-data' not in content_type:
        raise HTTPException(415,'Zadarma native webhook requires form payload')
    if 'multipart/form-data' in content_type:
        try:
            form=await request.form(); payload={str(k):str(v) for k,v in form.items()}
        except Exception:
            raise HTTPException(400,'invalid form payload')
    else:
        from urllib.parse import parse_qs
        try:
            parsed=parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=False)
            payload={str(k):str(v[-1] if v else '') for k,v in parsed.items()}
        except Exception:
            raise HTTPException(400,'invalid form payload')
    # Zadarma validates webhook URLs with ?zd_echo=... before sending calls.
    # Keep the echo handshake read-only and independent from provider credentials.
    echo=str(request.query_params.get('zd_echo') or '').strip()
    if echo:
        return Response(content=echo,media_type='text/plain')
    provider,credentials=provider_credentials(account_id)
    if str(provider or '').strip().lower()!='zadarma' or not credentials:
        raise HTTPException(404,'Zadarma is not configured for this account')
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter('zadarma')
    if not adapter or not hasattr(adapter,'verify_webhook_signature'):
        raise HTTPException(503,'Zadarma native adapter unavailable')
    signature=(request.headers.get('signature') or request.headers.get('x-zadarma-signature') or payload.pop('signature',None) or '')
    if not adapter.verify_webhook_signature(payload,signature,credentials):
        raise HTTPException(401,'invalid Zadarma signature')
    replay=claim_signed_native_webhook(account_id,'zadarma',raw)
    if replay=='duplicate': return {'status':'duplicate','replayed':True}
    if replay!='claimed': raise HTTPException(503,'webhook replay guard unavailable')
    try:
        normalized=adapter.normalize_webhook(payload,{str(k).lower():str(v) for k,v in request.headers.items()})
    except ValueError:
        raise HTTPException(400,'invalid provider webhook payload')
    if normalized.get('kind')=='recording.ready':
        provider_call_id=str(normalized.get('provider_call_id') or '').strip()
        provider_recording_id=normalized.get('provider_recording_id')
        link=adapter.recording_link(provider_call_id=provider_call_id,provider_recording_id=provider_recording_id,credentials=credentials)
        if not link.ok:
            return _safe_recording_link_failure(link)
        return recording_upsert_for_provider_call(account_id,'zadarma',provider_call_id,provider_recording_id,str(link.payload.get('url') or ''),None)
    result=ingest_provider_event(account_id,'zadarma',normalized)
    if result.get('status') in {'invalid_provider','invalid_event'}:
        raise HTTPException(400,result)
    return result


@public_router.post('/native/novofon/{account_id}')
async def novofon_native_webhook(account_id: str, request: Request):
    """Signed Novofon v1 PBX webhook ingress into the canonical telephony engine."""
    raw=await request.body()
    if len(raw)>262144: raise HTTPException(413,'payload too large')
    content_type=str(request.headers.get('content-type') or '').lower()
    if 'application/x-www-form-urlencoded' not in content_type and 'multipart/form-data' not in content_type:
        raise HTTPException(415,'Novofon native webhook requires form payload')
    if 'multipart/form-data' in content_type:
        try:
            form=await request.form(); payload={str(k):str(v) for k,v in form.items()}
        except Exception:
            raise HTTPException(400,'invalid form payload')
    else:
        from urllib.parse import parse_qs
        try:
            parsed=parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=False)
            payload={str(k):str(v[-1] if v else '') for k,v in parsed.items()}
        except Exception:
            raise HTTPException(400,'invalid form payload')
    provider,credentials=provider_credentials(account_id)
    if str(provider or '').strip().lower()!='novofon' or not credentials:
        raise HTTPException(404,'Novofon is not configured for this account')
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter('novofon')
    if not adapter or not hasattr(adapter,'verify_webhook_signature'):
        raise HTTPException(503,'Novofon native adapter unavailable')
    signature=(request.headers.get('signature') or request.headers.get('x-novofon-signature') or request.headers.get('x-zadarma-signature') or payload.pop('signature',None) or '')
    if not adapter.verify_webhook_signature(payload,signature,credentials):
        raise HTTPException(401,'invalid Novofon signature')
    replay=claim_signed_native_webhook(account_id,'novofon',raw)
    if replay=='duplicate': return {'status':'duplicate','replayed':True}
    if replay!='claimed': raise HTTPException(503,'webhook replay guard unavailable')
    try:
        normalized=adapter.normalize_webhook(payload,{str(k).lower():str(v) for k,v in request.headers.items()})
    except ValueError:
        raise HTTPException(400,'invalid provider webhook payload')
    if normalized.get('kind')=='recording.ready':
        provider_call_id=str(normalized.get('provider_call_id') or '').strip()
        provider_recording_id=normalized.get('provider_recording_id')
        link=adapter.recording_link(provider_call_id=provider_call_id,provider_recording_id=provider_recording_id,credentials=credentials)
        if not link.ok:
            return _safe_recording_link_failure(link)
        return recording_upsert_for_provider_call(account_id,'novofon',provider_call_id,provider_recording_id,str(link.payload.get('url') or ''),None)
    result=ingest_provider_event(account_id,'novofon',normalized)
    if result.get('status') in {'invalid_provider','invalid_event'}:
        raise HTTPException(400,result)
    return result


@public_router.post('/native/mango/{account_id}/{event_kind:path}')
async def mango_native_webhook(account_id: str, event_kind: str, request: Request):
    """MANGO OFFICE Realtime ingress: events/call, events/summary, events/recording and ping."""
    raw=await request.body()
    if len(raw)>262144: raise HTTPException(413,'payload too large')
    content_type=str(request.headers.get('content-type') or '').lower()
    if 'application/x-www-form-urlencoded' not in content_type:
        raise HTTPException(415,'MANGO native webhook requires form payload')
    from urllib.parse import parse_qs
    try:
        parsed=parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=False)
        form={str(k):str(v[-1] if v else '') for k,v in parsed.items()}
        raw_json=str(form.get('json') or '')
        import json
        payload=json.loads(raw_json) if raw_json else {}
    except Exception:
        raise HTTPException(400,'invalid MANGO form/json payload')
    if not isinstance(payload,dict): raise HTTPException(400,'invalid MANGO json payload')
    provider,credentials=provider_credentials(account_id)
    if str(provider or '').strip().lower()!='mango' or not credentials:
        raise HTTPException(404,'MANGO is not configured for this account')
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter('mango')
    if not adapter or not hasattr(adapter,'verify_webhook_signature'):
        raise HTTPException(503,'MANGO native adapter unavailable')
    if not adapter.verify_webhook_signature(raw_json,form.get('vpbx_api_key') or '',form.get('sign') or '',credentials):
        raise HTTPException(401,'invalid MANGO signature')
    kind=str(event_kind or '').strip('/').lower()
    if kind.startswith('events/'):
        kind=kind.split('/',1)[1]
    if kind not in {'call','summary','recording','ping'}:
        raise HTTPException(404,'unsupported MANGO event path')
    replay=claim_signed_native_webhook(account_id,'mango',raw,scope=kind)
    if replay=='duplicate': return {'status':'duplicate','replayed':True}
    if replay!='claimed': raise HTTPException(503,'webhook replay guard unavailable')
    payload['_event_kind']=kind
    try:
        normalized=adapter.normalize_webhook(payload,{str(k).lower():str(v) for k,v in request.headers.items()})
    except ValueError:
        raise HTTPException(400,'invalid provider webhook payload')
    if normalized.get('kind')=='ping':
        return {'status':'ok'}
    if normalized.get('kind')=='ignored':
        return {'status':'ignored','reason':normalized.get('reason')}
    if normalized.get('kind')=='recording.ready':
        provider_call_id=str(normalized.get('provider_call_id') or '').strip()
        provider_recording_id=str(normalized.get('provider_recording_id') or '').strip()
        link=adapter.recording_link(provider_recording_id=provider_recording_id,credentials=credentials)
        if not link.ok:
            return _safe_recording_link_failure(link)
        return recording_upsert_for_provider_call(account_id,'mango',provider_call_id,provider_recording_id,str(link.payload.get('url') or ''),None)
    result=ingest_provider_event(account_id,'mango',normalized)
    if result.get('status') in {'invalid_provider','invalid_event'}:
        raise HTTPException(400,result)
    return result


@public_router.post('/native/uis/{account_id}')
async def uis_native_webhook(account_id: str, request: Request):
    """UIS configurable HTTP notification ingress protected by a BORIS shared secret."""
    raw=await request.body()
    if len(raw)>262144: raise HTTPException(413,'payload too large')
    try:
        import json, hmac
        payload=json.loads(raw.decode('utf-8'))
    except Exception:
        raise HTTPException(400,'invalid json')
    if not isinstance(payload,dict): raise HTTPException(400,'invalid UIS webhook payload')
    provider,secret=provider_webhook_secret(account_id,'uis')
    if provider!='uis' or not secret:
        raise HTTPException(404,'UIS webhook is not configured for this account')
    supplied=str(request.headers.get('x-boris-provider-secret') or payload.pop('boris_secret',None) or '').strip()
    if not supplied or not hmac.compare_digest(str(secret),supplied):
        raise HTTPException(401,'invalid UIS webhook secret')
    replay=claim_signed_native_webhook(account_id,'uis',raw)
    if replay=='duplicate': return {'status':'duplicate','replayed':True}
    if replay!='claimed': raise HTTPException(503,'webhook replay guard unavailable')
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter('uis')
    if not adapter:
        raise HTTPException(503,'UIS native adapter unavailable')
    try:
        normalized=adapter.normalize_webhook(payload,{str(k).lower():str(v) for k,v in request.headers.items()})
    except ValueError:
        raise HTTPException(400,'invalid provider webhook payload')
    if normalized.get('kind')=='recording.ready':
        return recording_upsert_for_provider_call(account_id,'uis',str(normalized.get('provider_call_id') or ''),normalized.get('provider_recording_id'),str(normalized.get('source_url') or ''),None)
    result=ingest_provider_event(account_id,'uis',normalized)
    if result.get('status') in {'invalid_provider','invalid_event'}:
        raise HTTPException(400,result)
    return result


@router.post('/internal/recording-file')
def internal_recording_file(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip(); call_id=str(body.get('call_id') or '').strip(); path=str(body.get('local_path') or '').strip()
    if not account_id or not call_id or not path: raise HTTPException(400,'account_id, call_id и local_path обязательны')
    return attach_recording_file(account_id,call_id,path,body.get('duration_sec'),body.get('provider'),body.get('provider_recording_id'))

@router.post('/internal/process-recordings')
def internal_process_recordings(request: Request, body: dict=Body(default={})):
    _assert_internal_local(request)
    return process_pending_recordings(int(body.get('limit') or 1))


@router.get('/bootstrap')
def bootstrap(account_id: str, platform: str='web', app_version: str='0.0.0', current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return client_bootstrap(account_id,platform,app_version)

@router.post('/media/session')
def media_session(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    if not account_id or not device_id or not call_id: raise HTTPException(400,'account_id, device_id и call_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return request_media_session(account_id,device_id,call_id)

@router.post('/media/session/activate')
def media_session_activate(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    try: lease_id=int(body.get('media_lease_id'))
    except Exception: raise HTTPException(400,'media_lease_id обязателен')
    if not account_id or not device_id or not call_id: raise HTTPException(400,'account_id, device_id и call_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return activate_media_session(account_id,device_id,call_id,lease_id)

@router.post('/media/session/renew')
def media_session_renew(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    try: lease_id=int(body.get('media_lease_id'))
    except Exception: raise HTTPException(400,'media_lease_id обязателен')
    if not account_id or not device_id or not call_id: raise HTTPException(400,'account_id, device_id и call_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return renew_media_session(account_id,device_id,call_id,lease_id,180)

@router.post('/media/session/proof')
def media_session_proof(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    try: lease_id=int(body.get('media_lease_id'))
    except Exception: raise HTTPException(400,'media_lease_id обязателен')
    if not account_id or not device_id or not call_id: raise HTTPException(400,'account_id, device_id и call_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return report_media_transport_proof(account_id,device_id,call_id,lease_id,body.get('stats') or {})

@router.post('/media/session/release')
def media_session_release(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); device_id=str(body.get('device_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    try: lease_id=int(body.get('media_lease_id'))
    except Exception: raise HTTPException(400,'media_lease_id обязателен')
    if not account_id or not device_id or not call_id: raise HTTPException(400,'account_id, device_id и call_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    _assert_device_access(account_id,device_id,current_user)
    return release_media_session(account_id,device_id,call_id,lease_id,'client_release')

@router.get('/calls/{call_id}')
def get_call(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return call_detail(account_id,call_id)

@router.get('/calls/{call_id}/targets')
def get_call_targets(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return {'status':'ok','items':call_targets(account_id,call_id)}

@router.get('/calls/{call_id}/realtime')
def realtime(call_id: str, account_id: str, chunk_limit: int=80, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return realtime_call_context(account_id,call_id,chunk_limit)

@router.post('/calls/{call_id}/suggestions/{suggestion_id}/dismiss')
def dismiss_suggestion(call_id: str, suggestion_id: int, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    return dismiss_realtime_suggestion(account_id,call_id,suggestion_id)

@router.post('/internal/transcript-chunk')
def internal_transcript_chunk(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    if not account_id or not call_id: raise HTTPException(400,'account_id и call_id обязательны')
    return ingest_transcript_chunk(account_id,call_id,int(body.get('seq') or 0),str(body.get('text') or ''),body.get('speaker'),body.get('start_ms'),body.get('end_ms'),bool(body.get('is_final',True)),str(body.get('source') or 'stream'),body.get('source_event_id'))

@router.post('/internal/ai-suggestion')
def internal_ai_suggestion(request: Request, body: dict=Body(...)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip(); call_id=str(body.get('call_id') or '').strip()
    if not account_id or not call_id: raise HTTPException(400,'account_id и call_id обязательны')
    return add_realtime_ai_suggestion(account_id,call_id,str(body.get('text') or ''),str(body.get('kind') or 'next_best_action'),body.get('trigger_chunk_id'),body.get('evidence') or [],body.get('model'),body.get('idempotency_key'))

@router.post('/internal/multidevice-guardian')
def multidevice_guardian(request: Request, body: dict=Body(default={})):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip() or None
    return {'status':'ok','devices':sweep_stale_devices(account_id),'ringing':sweep_ring_targets(account_id)}

@router.post('/devices/{device_id}/revoke')
def revoke(device_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return revoke_device(account_id,device_id,_actor_user_id(current_user,body.get('actor_user_id')))


@router.post('/calls/{call_id}/quality')
def quality_evaluate(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    return evaluate_call_quality(account_id,call_id,bool(body.get('force',False)))

@router.get('/quality')
def quality(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return quality_dashboard(account_id,days)

@router.get('/reports/settings')
def report_settings(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return telephony_report_settings(account_id)

@router.post('/reports/settings')
def report_settings_save(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return save_telephony_report_settings(account_id,body)

@router.get('/reports/preview')
def report_preview(account_id: str, days: int=1, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return build_rop_phone_report(account_id,days)

@router.post('/reports/send-test')
def report_send_test(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return deliver_rop_phone_report(account_id,None,int(body.get('days') or 1),body.get('channels'))

@router.get('/coaching')
def coaching(account_id: str, status: str='open', current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return coaching_plans(account_id,status)

@router.post('/coaching/{plan_id}/link-sparring')
def coaching_link(plan_id: int, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip(); sid=str(body.get('session_id') or '').strip()
    if not account_id or not sid: raise HTTPException(400,'account_id и session_id обязательны')
    _assert_telephony_account_access(account_id,current_user)
    return link_coaching_sparring(account_id,plan_id,sid)

@router.post('/coaching/{plan_id}/complete')
def coaching_complete(plan_id: int, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    return complete_coaching_plan(account_id,plan_id,int(body.get('followup_score') or 0))

@router.get('/recording/settings')
def recording_settings_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return recording_policy_settings(account_id)

@router.post('/recording/settings')
def recording_settings_save(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return save_recording_policy_settings(account_id,body,_actor_user_id(current_user,body.get('actor_user_id')))

@router.get('/calls/{call_id}/human-policy')
def call_human_policy(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return human_call_policy(account_id,call_id)

@router.post('/calls/{call_id}/recording-notice')
def call_recording_notice(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user)
    return set_recording_notice_status(account_id,call_id,str(body.get('status') or ''))

@router.get('/copilot/settings')
def copilot_settings_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return copilot_settings(account_id)

@router.post('/copilot/settings')
def copilot_settings_save(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return save_copilot_settings(account_id,body)

@router.get('/calls/{call_id}/copilot-guard')
def copilot_guard_get(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return copilot_guard(account_id,call_id)

@router.get('/afterhours/settings')
def afterhours_settings_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return afterhours_settings(account_id)

@router.post('/afterhours/settings')
def afterhours_settings_save(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    try: return save_afterhours_settings(account_id,body)
    except (ValueError,AssertionError): raise HTTPException(400,'Некорректное рабочее расписание')

@router.get('/afterhours/status')
def afterhours_status_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return afterhours_status(account_id)


@router.get('/autoanswer/preview')
def autoanswer_preview_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return autoanswer_preview(account_id)

@router.get('/autoanswer/readiness')
def autoanswer_readiness_get(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return autoanswer_readiness(account_id)

@router.get('/autoanswer/analytics')
def autoanswer_analytics_get(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    return autoanswer_analytics(account_id,days)

@router.get('/autoanswer/decision')
def autoanswer_decision_get(account_id: str, ring_seconds: int=0, human_available: bool=True, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    return autoanswer_decision(account_id,ring_seconds,human_available)

@router.post('/internal/afterhours/{call_id}/turn')
def afterhours_turn(call_id: str, request: Request, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    return afterhours_receptionist_turn(account_id,call_id,str(body.get('text') or ''))

@router.post('/internal/afterhours/{call_id}/finalize')
def afterhours_finalize(call_id: str, request: Request, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    _assert_internal_local(request)
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    return finalize_afterhours_callback(account_id,call_id)

@router.get('/afterhours/cost-estimate')
def afterhours_cost(account_id: str, minutes: float=2.0, tts_chars: int=500, usd_rub: float=80.0, provider_rub_per_min: float|None=None, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return afterhours_cost_estimate(minutes,tts_chars,usd_rub,provider_rub_per_min)

@router.get('/billing/minutes')
def billing_minutes(account_id: str, days: int=31, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    x=minute_usage_dashboard(account_id,days)
    return {'status':'ok','package':x.get('package'),'by_usage_kind':[{k:v for k,v in row.items() if k not in {'ai_cost_rub','provider_cost_rub','infra_cost_rub'}} for row in (x.get('by_usage_kind') or [])]}

@router.post('/billing/minutes/package')
def billing_minutes_package(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id=str(body.get('account_id') or '').strip()
    if not account_id: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    try:return save_minute_package(account_id,int(body.get('package_minutes')),str(body.get('overage_mode') or 'notify'),int(body.get('soft_limit_percent') or 90))
    except (ValueError,AssertionError,TypeError):raise HTTPException(400,'Допустимые пакеты: 300, 500, 700, 1000, 1500, 2000 минут')

@router.get('/minutes/forecast')
def minute_forecast(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return minute_package_forecast(account_id)

@router.get('/minutes/pricing-lab')
def minute_pricing_lab(account_id: str, days: int=31, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return phone_pricing_lab(account_id,days)

@router.get('/voice-agent/settings')
def va_settings(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_settings(account_id)

@router.post('/voice-agent/settings')
def va_settings_save(body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return save_voice_agent_settings(a,body)

@router.get('/calls/{call_id}/voice-agent/guard')
def va_guard(call_id: str, account_id: str, mode: str='voice_agent', current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_guard(account_id,call_id,mode)

@router.post('/calls/{call_id}/voice-agent/start')
def va_start(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_start(a,call_id,str(body.get('mode') or 'voice_agent'))

@router.get('/calls/{call_id}/voice-agent/context')
def va_context(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_context(account_id,call_id)

@router.post('/calls/{call_id}/voice-agent/complete')
def va_complete(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_safe_complete(a,call_id,str(body.get('topic') or ''),str(body.get('summary') or ''),str(body.get('qualification') or ''),str(body.get('next_action') or ''))

@router.post('/calls/{call_id}/voice-agent/turn')
def va_turn(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_lifecycle_turn(a,call_id,str(body.get('text') or ''))

@router.post('/calls/{call_id}/voice-agent/handoff')
def va_handoff(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_handoff(a,call_id,str(body.get('reason') or 'client_requested'))

@router.get('/calls/{call_id}/voice-agent/transcript')
def va_transcript(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_transcript(account_id,call_id)

@router.get('/calls/{call_id}/voice-agent/qualification')
def va_qualification(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_qualification(account_id,call_id)

@router.post('/calls/{call_id}/voice-agent/qualification')
def va_qualification_save(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return update_voice_agent_qualification(a,call_id,body)

@router.get('/calls/{call_id}/voice-agent/next-question')
def va_next_question(call_id: str, account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return voice_agent_next_question(account_id,call_id)

@router.post('/calls/{call_id}/voice-agent/finalize-qualification')
def va_finalize_qualification(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_finalize_qualification(a,call_id)

@router.post('/calls/{call_id}/voice-agent/customer-turn')
def va_customer_turn(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_ingest_customer_turn(a,call_id,str(body.get('text') or ''))

@router.post('/calls/{call_id}/voice-agent/objection')
def va_objection(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip()
    if not a: raise HTTPException(400,'account_id обязателен')
    _assert_telephony_account_access(a,current_user,owner_only=True)
    return voice_agent_objection_response(a,call_id,str(body.get('text') or ''))

@router.get('/billing/alerts')
def billing_alerts(account_id: str, limit: int=20, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return minute_alerts(account_id,limit)

@router.get('/billing/summary')
def billing_summary(account_id: str, days: int=31, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user)
    x=minute_usage_dashboard(account_id,days); f=minute_package_forecast(account_id); alerts=minute_alerts(account_id,10)
    pkg=x.get('package') or {}
    usage={'package_minutes':pkg.get('package_minutes'),'used_minutes':pkg.get('used_minutes'),'remaining_minutes':pkg.get('remaining_minutes'),'usage_percent':pkg.get('usage_percent'),'cycle_start':pkg.get('cycle_start'),'cycle_end':pkg.get('cycle_end'),'bundled_features':pkg.get('bundled_features') or []}
    forecast={'projected_cycle_minutes':f.get('projected_minutes'),'projected_overage_minutes':f.get('projected_overage_minutes'),'recommended_package_minutes':f.get('recommended_package_minutes'),'cycle':f.get('cycle')}
    return {'status':'ok','usage':usage,'forecast':forecast,'alerts':alerts.get('items') or []}

@router.get('/owner/economics')
def owner_economics(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True)
    from app.services.telephony_core import phone_owner_economics, phone_pricing_lab
    return {'status':'ok','economics':phone_owner_economics(account_id,days),'pricing_lab':phone_pricing_lab(account_id,days)}


@router.post('/calls/{call_id}/voice-agent/disclosure')
def va_disclosure(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip(); _assert_telephony_account_access(a,current_user,owner_only=True); return voice_agent_disclosure(a,call_id)

@router.post('/calls/{call_id}/voice-agent/callback-phone')
def va_callback_phone(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip(); _assert_telephony_account_access(a,current_user,owner_only=True); return voice_agent_callback_phone(a,call_id,str(body.get('text') or ''))

@router.post('/calls/{call_id}/voice-agent/inbound-start')
def va_inbound_start(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip(); _assert_telephony_account_access(a,current_user,owner_only=True); return voice_agent_inbound_lifecycle_start(a,call_id)

@router.post('/calls/{call_id}/voice-agent/fallback')
def va_fallback(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip(); _assert_telephony_account_access(a,current_user,owner_only=True); return voice_agent_fallback_ivr(a,call_id,str(body.get('text') or ''))

@router.post('/calls/{call_id}/voice-agent/post-call')
def va_post_call(call_id: str, body: dict=Body(...), current_user=Depends(get_current_user_or_internal)):
    a=str(body.get('account_id') or '').strip(); _assert_telephony_account_access(a,current_user,owner_only=True); return voice_agent_post_call_finalize(a,call_id,bool(body.get('force',False)))

@router.get('/owner/funnel')
def owner_phone_funnel(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return phone_funnel_rop_report(account_id,days)

@router.get('/owner/dashboard')
def owner_phone_dashboard(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return phone_owner_dashboard(account_id)

@router.get('/owner/source-roi')
def owner_source_roi(account_id: str, days: int=30, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user,owner_only=True); return phone_source_roi(account_id,days)

@router.get('/client/readiness')
def client_readiness(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return phone_client_readiness(account_id)

@router.get('/client/release')
def client_release(account_id: str, platform: str='web', app_version: str='0.0.0', current_user=Depends(get_current_user_or_internal)):
    _assert_telephony_account_access(account_id,current_user); return client_release_status(platform,app_version)


@router.get('/mcn/onboarding')
def mcn_onboarding_status(current_user=Depends(_require_private_platform_owner)):
    """Platform-owner safe view of the global MCN onboarding contour."""
    return _mcn_onboarding_safe_projection()




@router.get('/platform/phone-entitlement')
def platform_phone_entitlement_status(
    account_id: str,
    current_user=Depends(_require_private_platform_owner),
):
    """Read-only commercial status for one BORIS Phone account."""
    return phone_entitlement_status(account_id)


@router.post('/platform/phone-entitlement/activate')
def platform_phone_entitlement_activate(
    body: dict=Body(...),
    current_user=Depends(_require_private_platform_owner),
):
    """Activate/renew Phone only from explicit commercial evidence.

    This route never invents a price or paid period. A future paid_until and a
    non-empty commercial reference are mandatory. The reference itself is not
    returned by the service.
    """
    if body.get('confirm_paid_phone') is not True:
        raise HTTPException(400, 'Требуется явное подтверждение оплаченного Phone-модуля')
    account_id = str(body.get('account_id') or '').strip()
    paid_until = body.get('paid_until')
    commercial_ref = str(body.get('commercial_ref') or '').strip()
    price_rub = body.get('price_rub')
    allow_zero_price = body.get('confirm_zero_price_phone') is True
    if not account_id or not paid_until or not commercial_ref:
        raise HTTPException(400, 'Нужны account_id, будущий paid_until и commercial_ref')
    actor_id = _actor_user_id(current_user)
    result = provision_paid_phone_entitlement(
        account_id,
        paid_until,
        commercial_ref,
        actor_user_id=actor_id,
        price_rub=price_rub,
        source='platform_owner_confirmed',
        allow_zero_price=allow_zero_price,
    )
    status = str(result.get('status') or '')
    if status == 'account_not_found':
        raise HTTPException(404, 'Аккаунт BORIS не найден')
    if status in {
        'invalid_account','commercial_reference_required','future_paid_until_required',
        'invalid_price','price_required','zero_price_confirmation_required',
        'would_shorten_paid_period',
    }:
        raise HTTPException(409, status)
    if not bool(result.get('active')):
        raise HTTPException(409, status or 'phone_entitlement_not_active')
    try:
        import telephony_guardian_runner as _phone_guardian
        _phone_guardian.mcn_mailbox_autoonboard_once(force_refresh=True)
    except Exception:
        pass
    return {
        'status':'ok',
        'entitlement':result,
        'onboarding':_mcn_onboarding_safe_projection(),
        'truth':'explicit paid Phone entitlement; no price or paid period is invented',
    }


@router.post('/platform/phone-entitlement/revoke')
def platform_phone_entitlement_revoke(
    body: dict=Body(...),
    current_user=Depends(_require_private_platform_owner),
):
    if body.get('confirm_revoke_phone') is not True:
        raise HTTPException(400, 'Требуется явное подтверждение отключения Phone-модуля')
    account_id = str(body.get('account_id') or '').strip()
    reason = str(body.get('reason') or 'commercial_revoked').strip()
    if not account_id:
        raise HTTPException(400, 'Нужен account_id')
    result = revoke_phone_entitlement(
        account_id,
        actor_user_id=_actor_user_id(current_user),
        reason=reason,
    )
    return {
        'status':'ok',
        'entitlement':result,
        'truth':'Phone entitlement revoked explicitly; provider credentials are not returned',
    }

@router.post('/mcn/account-binding/confirm')
def mcn_account_binding_confirm(
    body: dict=Body(...),
    current_user=Depends(_require_private_platform_owner),
):
    """Bind the exact current MCN SIP letter to one already-paid Phone account."""
    if body.get('confirm_account_binding') is not True:
        raise HTTPException(400, 'Требуется явное подтверждение привязки линии MCN к выбранному Phone-аккаунту')

    account_id = str(body.get('account_id') or '').strip()
    expected_code = str(body.get('expected_action_code') or '').strip()
    expected_uid = str(body.get('expected_candidate_uid') or '').strip()
    expected_date = str(body.get('expected_candidate_date') or '').strip()
    if (
        not account_id
        or expected_code != 'mcn_phone_account_binding_required'
        or not expected_uid
        or not expected_date
    ):
        raise HTTPException(400, 'Нужно выбрать аккаунт и подтвердить текущее SIP-письмо MCN')

    raw = _mcn_onboarding_raw_state()
    safe = _mcn_onboarding_safe_projection(raw)
    action = safe.get('primary_next_action') if isinstance(safe.get('primary_next_action'), dict) else {}
    candidate = safe.get('candidate_evidence') if isinstance(safe.get('candidate_evidence'), dict) else {}
    if (
        str(action.get('code') or '') != expected_code
        or str(candidate.get('uid') or '') != expected_uid
        or str(candidate.get('date') or '') != expected_date
        or str(candidate.get('sender_domain') or '') != 'mcn.ru'
    ):
        raise HTTPException(409, 'Состояние MCN изменилось. Обновите экран перед выбором аккаунта')
    if not safe.get('account_binding_supported'):
        raise HTTPException(409, 'Сейчас нет безопасной неоднозначной привязки MCN, которую можно подтвердить')

    eligible = {
        str(item.get('account_id') or '')
        for item in (safe.get('eligible_phone_accounts') or [])
        if isinstance(item, dict)
    }
    if account_id not in eligible:
        raise HTTPException(409, 'Выбранный аккаунт больше не имеет активного оплаченного Phone-модуля')

    mailbox_id = raw.get('mailbox_id')
    try:
        mailbox_id = int(mailbox_id)
    except (TypeError, ValueError):
        mailbox_id = 0
    if mailbox_id <= 0:
        raise HTTPException(409, 'Рабочая почта MCN не определена')

    actor_id = _actor_user_id(current_user)
    from app.services.telephony_core import _audit
    _audit(
        _MCN_WATCH_ACCOUNT,
        'mcn.account_binding.owner_approved',
        result='approved',
        actor_user_id=actor_id,
        provider='mcn',
        metadata={
            'account_id': account_id[:160],
            'candidate_uid': expected_uid[:80],
            'candidate_date': expected_date[:160],
        },
    )

    result = _run_mcn_account_binding(
        mailbox_id,
        account_id,
        expected_uid,
        expected_date,
    )
    result_status = str(result.get('status') or 'blocked')[:80]
    _audit(
        _MCN_WATCH_ACCOUNT,
        'mcn.account_binding.apply',
        result=result_status,
        actor_user_id=actor_id,
        provider='mcn',
        metadata={
            'account_id': account_id[:160],
            'candidate_uid': expected_uid[:80],
            'candidate_date': expected_date[:160],
            'returncode': int(result.get('returncode') or 0),
        },
    )

    if result_status != 'ok':
        reason = str(result.get('reason') or 'mcn_binding_failed')[:160]
        if reason == 'stale_mcn_candidate_evidence':
            raise HTTPException(409, 'Письмо MCN изменилось. BORIS ничего не применил; обновите экран')
        raise HTTPException(409, 'BORIS не применил MCN к аккаунту: ' + reason)

    try:
        import telephony_guardian_runner as _phone_guardian
        _phone_guardian.mcn_mailbox_autoonboard_once()
    except Exception:
        pass

    return {
        'status': 'ok',
        'account_id': account_id,
        'binding': result,
        'onboarding': _mcn_onboarding_safe_projection(),
        'truth': 'exact current MCN letter applied server-side; SIP secrets are never returned',
    }


@router.post('/mcn/company-card/send')
def mcn_company_card_send(body: dict=Body(...), current_user=Depends(_require_private_platform_owner)):
    """Explicit owner approval to disclose the prepared company card to MCN.

    This route is intentionally platform-owner only. It never accepts document
    contents or banking values from the request; it sends only the server-side
    validated card already prepared by BORIS.
    """
    confirm = body.get('confirm_share_banking') is True
    if not confirm:
        raise HTTPException(400, 'Требуется явное подтверждение передачи банковских и юридических реквизитов MCN')

    expected_code = str(body.get('expected_action_code') or '').strip()
    expected_reply_date = str(body.get('expected_provider_reply_date') or '').strip()
    expected_card_fingerprint = str(body.get('expected_card_fingerprint') or '').strip().lower()
    if (
        expected_code != 'mcn_company_card_send_approval'
        or not expected_reply_date
        or len(expected_card_fingerprint) != 64
        or any(ch not in '0123456789abcdef' for ch in expected_card_fingerprint)
    ):
        raise HTTPException(400, 'Нужно подтвердить текущее действие, дату ответа MCN и версию карточки')

    raw = _mcn_onboarding_raw_state()
    safe = _mcn_onboarding_safe_projection(raw)
    action = safe.get('primary_next_action') if isinstance(safe.get('primary_next_action'), dict) else {}
    operational = safe.get('operational_reply') if isinstance(safe.get('operational_reply'), dict) else {}
    draft = safe.get('company_card_draft') if isinstance(safe.get('company_card_draft'), dict) else {}
    current_code = str(action.get('code') or '')
    current_reply_date = str(operational.get('date') or '')
    current_card_fingerprint = str(draft.get('card_fingerprint') or '').strip().lower()
    if (
        current_code != expected_code
        or current_reply_date != expected_reply_date
        or current_card_fingerprint != expected_card_fingerprint
    ):
        raise HTTPException(409, 'Состояние MCN или карточка изменились. Обновите экран перед подтверждением')

    if not safe.get('approval_supported'):
        raise HTTPException(409, 'Карточка ещё не готова к безопасной отправке MCN')

    mailbox_id = raw.get('mailbox_id')
    try:
        mailbox_id = int(mailbox_id)
    except (TypeError, ValueError):
        mailbox_id = 0
    if mailbox_id <= 0:
        raise HTTPException(409, 'Рабочая почта MCN не определена')

    from app.services.telephony_core import _audit
    actor_id = _actor_user_id(current_user)
    _audit(
        _MCN_WATCH_ACCOUNT,
        'mcn.company_card.owner_approved',
        result='approved',
        actor_user_id=actor_id,
        provider='mcn',
        metadata={
            'action_code': expected_code,
            'provider_reply_date': expected_reply_date[:160],
            'card_fingerprint': expected_card_fingerprint,
        },
    )

    send_result = _run_mcn_company_card_send(mailbox_id)
    send_status = str(send_result.get('status') or 'unknown')[:80]
    _audit(
        _MCN_WATCH_ACCOUNT,
        'mcn.company_card.send',
        result=send_status,
        actor_user_id=actor_id,
        provider='mcn',
        metadata={
            'action_code': expected_code,
            'provider_reply_date': expected_reply_date[:160],
            'card_fingerprint': expected_card_fingerprint,
            'retry_blocked': bool(send_result.get('retry_blocked')),
            'recipient_domain_verified': bool(send_result.get('recipient_domain_verified')),
            'sent_copy_saved': bool(send_result.get('sent_copy_saved')),
            'send_state_persisted': bool(send_result.get('send_state_persisted')),
        },
    )

    # Refresh the durable guardian projection immediately so the owner does not
    # continue seeing a stale "approve send" action after SMTP acceptance.
    try:
        import telephony_guardian_runner as _phone_guardian
        _phone_guardian.mcn_mailbox_autoonboard_once()
    except Exception:
        # External send result is authoritative. A guardian refresh failure must
        # never trigger a resend; the minute guardian will retry the projection.
        pass

    refreshed = _mcn_onboarding_safe_projection()
    return {
        'status': send_status,
        'send': send_result,
        'onboarding': refreshed,
        'truth': 'owner-approved exactly-once send; no banking values are returned',
    }


@router.post('/mcn/company-card/manual-delivery/confirm')
def mcn_company_card_manual_delivery_confirm(
    body: dict=Body(...),
    current_user=Depends(_require_private_platform_owner),
):
    """Confirm a company-card message that the owner already sent outside BORIS.

    This action never sends mail and never accepts document/banking contents.
    It only binds the owner's assertion to the exact current MCN request and
    exact Sent-folder evidence already discovered by BORIS.
    """
    if body.get('confirm_manual_delivery') is not True:
        raise HTTPException(400, 'Требуется явное подтверждение, что карточка уже была отправлена вручную')

    expected_code = str(body.get('expected_action_code') or '').strip()
    expected_reply_date = str(body.get('expected_provider_reply_date') or '').strip()
    expected_sent_at = str(body.get('expected_sent_at') or '').strip()
    if (
        expected_code != 'mcn_company_card_delivery_verify'
        or not expected_reply_date
        or not expected_sent_at
    ):
        raise HTTPException(400, 'Нужно подтвердить текущее действие, ответ MCN и найденное отправленное письмо')

    raw = _mcn_onboarding_raw_state()
    safe = _mcn_onboarding_safe_projection(raw)
    action = safe.get('primary_next_action') if isinstance(safe.get('primary_next_action'), dict) else {}
    operational = safe.get('operational_reply') if isinstance(safe.get('operational_reply'), dict) else {}
    evidence = safe.get('company_card_sent_evidence') if isinstance(safe.get('company_card_sent_evidence'), dict) else {}
    if (
        str(action.get('code') or '') != expected_code
        or str(operational.get('date') or '') != expected_reply_date
        or str(evidence.get('sent_at') or '') != expected_sent_at
    ):
        raise HTTPException(409, 'Состояние MCN или найденное письмо изменились. Обновите экран перед подтверждением')
    if not safe.get('manual_delivery_confirmation_supported'):
        raise HTTPException(409, 'Сейчас нет неподтверждённой ручной отправки, которую можно безопасно подтвердить')

    mailbox_id = raw.get('mailbox_id')
    try:
        mailbox_id = int(mailbox_id)
    except (TypeError, ValueError):
        mailbox_id = 0
    raw_operational = raw.get('operational_reply') if isinstance(raw.get('operational_reply'), dict) else {}
    request_message_id = str(raw_operational.get('message_id') or '').strip()
    if mailbox_id <= 0 or not request_message_id:
        raise HTTPException(409, 'Не удалось однозначно привязать подтверждение к текущему запросу MCN')

    from app.services.mcn_core import confirm_mcn_company_card_manual_delivery
    from app.services.telephony_core import _audit
    actor_id = _actor_user_id(current_user)
    confirmed = confirm_mcn_company_card_manual_delivery(
        mailbox_id,
        request_message_id,
        expected_sent_at,
        actor_id,
    )
    _audit(
        _MCN_WATCH_ACCOUNT,
        'mcn.company_card.manual_delivery_confirmed',
        result='confirmed',
        actor_user_id=actor_id,
        provider='mcn',
        metadata={
            'action_code': expected_code,
            'provider_reply_date': expected_reply_date[:160],
            'sent_at': str(confirmed.get('sent_at') or '')[:100],
            'mailbox_id': mailbox_id,
        },
    )

    try:
        import telephony_guardian_runner as _phone_guardian
        _phone_guardian.mcn_mailbox_autoonboard_once()
    except Exception:
        # The durable owner assertion is authoritative and the minute guardian
        # will refresh the safe projection without sending anything.
        pass

    refreshed = _mcn_onboarding_safe_projection()
    return {
        'status': 'confirmed',
        'manual_delivery': {
            'confirmed': True,
            'sent_at': str(confirmed.get('sent_at') or '')[:100] or None,
        },
        'onboarding': refreshed,
        'truth': 'manual delivery confirmation only; no mail was sent and no banking values are returned',
    }
