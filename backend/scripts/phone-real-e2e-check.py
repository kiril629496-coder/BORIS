#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv

load_dotenv(str(ROOT/'.env'), override=True)

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.mcn_core import current_mcn_accounts
from app.services.telephony_core import active_phone_entitlements, phone_client_readiness, provision_paid_phone_entitlement


def _readiness(account_id: str) -> dict[str, Any]:
    # Prefer the live backend process so diagnostics run with exactly the same
    # runtime permissions/environment as the owner dashboard.
    try:
        url=f"http://127.0.0.1:8000/api/telephony/client/readiness?account_id={quote(account_id,safe='')}"
        with urlopen(url, timeout=5) as resp:
            payload=json.loads(resp.read().decode('utf-8'))
        if isinstance(payload,dict) and payload.get('status')=='ok':
            return payload
    except Exception:
        pass
    return phone_client_readiness(account_id)


def safe_report(account_id: str) -> dict[str, Any]:
    readiness = _readiness(account_id)
    gates = dict(readiness.get('telephony_gates') or {})
    runtime = dict(readiness.get('runtime_evidence') or {})
    media = dict(readiness.get('media_diagnostics') or {})
    turn = dict(media.get('turn_runtime') or {})
    telphin = dict(readiness.get('telphin_sip_readiness') or {})
    mcn_ready = dict(readiness.get('mcn_readiness') or {})
    mcn_transport = dict(readiness.get('mcn_transport') or {})
    mcn_network = dict(readiness.get('mcn_network') or {})
    provider = dict(readiness.get('provider_evidence') or {})
    provider_key = str(provider.get('provider') or '').strip().lower()

    checks = {
        'provider_selected': bool(gates.get('provider_selected')),
        'provider_connected': bool(gates.get('provider_connected')),
        'telphin_sip_registered': bool(telphin.get('ready')) if provider_key == 'telphin' else None,
        'mcn_trunk_and_did_ready': bool(mcn_ready.get('ready')) if provider_key == 'mcn' else None,
        'mcn_transport_registered': bool(mcn_transport.get('ready')) if provider_key == 'mcn' else None,
        'mcn_network_ready': bool(mcn_network.get('ready')) if provider_key == 'mcn' else None,
        'device_online': bool(gates.get('device_online')),
        'turn_runtime_ready': bool(turn.get('runtime_ready')) if turn else None,
        'real_provider_call_seen': bool(gates.get('real_provider_call_seen')),
        'real_inbound_seen': bool(gates.get('real_inbound_seen')),
        'real_outbound_seen': bool(gates.get('real_outbound_seen')),
        'real_answer_seen': bool(gates.get('real_answer_seen')),
        'real_call_completed': bool(gates.get('real_call_completed')),
        'real_media_provider': bool(gates.get('real_media_provider')),
        'real_hold_completed': bool(gates.get('real_hold_completed')),
        'real_resume_completed': bool(gates.get('real_resume_completed')),
        'real_crm_linked': bool(gates.get('real_crm_linked')),
        'real_cleanup_completed': bool(gates.get('real_cleanup_completed')),
        'mcn_carrier_linked': bool(gates.get('mcn_carrier_linked')) if provider_key == 'mcn' else None,
        'recording_received': bool(gates.get('recording_received')),
        'transcription_completed': bool(gates.get('transcription_completed')),
        'ai_analysis_completed': bool(gates.get('ai_analysis_completed')),
        'real_inbound_e2e_chain': bool(gates.get('real_inbound_e2e_chain')),
        'real_outbound_lifecycle': bool(gates.get('real_outbound_lifecycle')),
    }
    required = [v for v in checks.values() if v is not None]
    complete = bool(required) and all(required)
    return {
        'status': 'verified' if complete else 'incomplete',
        'account_id': account_id,
        'provider': provider.get('provider'),
        'provider_status': provider.get('status'),
        'recording_policy': readiness.get('recording_policy'),
        'checks': checks,
        'remaining': list(readiness.get('telephony_remaining_human') or []),
        'primary_next_action': readiness.get('primary_next_action'),
        'runtime_evidence': {
            'online_devices': runtime.get('online_devices'),
            'calls': runtime.get('calls'),
            'recordings': runtime.get('recordings'),
            'transport': runtime.get('transport'),
            'e2e_chain': runtime.get('e2e_chain'),
        },
        'truth': 'read-only evidence report; it never originates a call and never treats source/UI readiness as real telephony proof',
    }


def _discover_real_mcn_accounts() -> list[str]:
    """Use the same canonical MCN resolver as the minute production watcher."""
    return current_mcn_accounts(include_synthetic=False)


def _normalize_global_mcn_action(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    action = payload.get('primary_next_action') if isinstance(payload, dict) else None
    if not isinstance(action, dict):
        return None
    code = str(action.get('code') or '')[:120]
    text_value = str(action.get('text') or '')[:500]
    if not code and not text_value:
        return None
    owner_required = bool(action.get('owner_action_required'))
    actor = str(action.get('actor') or ('owner' if owner_required else 'boris'))[:80]
    return {
        'code': code or ('owner_action' if owner_required else 'boris_waiting'),
        'actor': actor,
        'owner_action_required': owner_required,
        'text': text_value,
    }


def _global_mcn_next_action() -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id='__mcn_mailbox_watch__'
              AND key='mcn_mailbox_auto_onboard_runtime'
            ORDER BY id DESC LIMIT 1
        """)).first()
    finally:
        db.close()
    if not row or not row[0]:
        return None
    try:
        payload = json.loads(str(row[0]))
    except Exception:
        return None
    return _normalize_global_mcn_action(payload)


def _phone_commercial_activation_truth() -> dict[str, Any]:
    """Read-only truth for the canonical paid BORIS Phone entitlement path."""
    try:
        active = [
            dict(x) for x in active_phone_entitlements()
            if str(x.get("account_id") or "").strip()
        ]
    except Exception:
        active = []

    activation_service_ready = callable(provision_paid_phone_entitlement)
    activation_api_ready = False
    try:
        from fastapi.routing import APIRoute
        from app.api import telephony as telephony_api
        activation_api_ready = any(
            isinstance(route, APIRoute)
            and route.path == "/api/telephony/platform/phone-entitlement/activate"
            for route in telephony_api.router.routes
        )
    except Exception:
        activation_api_ready = False

    source_available = bool(activation_service_ready and activation_api_ready)
    return {
        "status": (
            "active_phone_entitlement_present"
            if active
            else "activation_path_available"
            if source_available
            else "missing_paid_phone_entitlement_contract"
        ),
        "active_phone_entitlements": len(active),
        "activation_service_ready": activation_service_ready,
        "activation_api_ready": activation_api_ready,
        "activation_path_ready": source_available,
        "truth": (
            "read-only commercial gate; canonical telephony_entitlements are used; "
            "account/Inbox existence never grants BORIS Phone"
        ),
    }


def _select_account_id(explicit: str | None, discovered: list[str]) -> tuple[str | None, str]:
    value=str(explicit or '').strip()
    if value:
        return value,'explicit'
    unique=sorted({str(x or '').strip() for x in discovered if str(x or '').strip()})
    if len(unique)==1:
        return unique[0],'auto_single_real_mcn'
    if not unique:
        return None,'no_real_mcn_account'
    return None,'multiple_real_mcn_accounts'


def main() -> int:
    ap = argparse.ArgumentParser(description='Read-only BORIS Phone real E2E evidence check')
    ap.add_argument('--account-id', help='Optional. If omitted, BORIS auto-selects the only current real MCN account.')
    args = ap.parse_args()
    discovered=[] if args.account_id else _discover_real_mcn_accounts()
    commercial = _phone_commercial_activation_truth()
    account_id,resolution=_select_account_id(args.account_id,discovered)
    if not account_id:
        persisted_action = _global_mcn_next_action() if resolution=='no_real_mcn_account' else None
        waiting_external = bool(
            persisted_action
            and persisted_action.get('owner_action_required') is False
            and persisted_action.get('actor') == 'boris'
        )
        report={
            'status':'waiting_external' if waiting_external else 'incomplete',
            'account_id':None,
            'account_resolution':resolution,
            'real_mcn_accounts':discovered,
            'phone_commercial_activation': commercial,
            'primary_next_action':persisted_action or {
                'code':'connect_mcn' if resolution=='no_real_mcn_account' else 'select_mcn_account',
                'actor':'owner',
                'owner_action_required':True,
                'text':'Сначала подключите реальные настройки MCN к нужному BORIS-аккаунту.' if resolution=='no_real_mcn_account'
                       else 'Найдено несколько реальных MCN-аккаунтов. Укажите нужный --account-id; BORIS не будет угадывать.'
            },
            'external_wait': waiting_external,
            'truth':'read-only evidence report; no real MCN account was guessed or fabricated',
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 2
    report = safe_report(account_id)
    report['account_resolution']=resolution
    report['phone_commercial_activation']=commercial
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report['status'] == 'verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
