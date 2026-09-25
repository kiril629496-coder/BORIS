from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.auth import get_current_user_or_internal
from app.services.mcn_core import (
    status as mcn_status,
    readiness as mcn_readiness,
    trunk_upsert,
    trunk_list,
    did_upsert,
    did_list,
    onboarding_upsert,
    tariff_rate_resolve,
    reconcile_carrier_cdr,
)

router = APIRouter(prefix="/api/mcn", tags=["mcn-telephony"])

def _uid(user):
    if user is None:
        return None
    return getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)


def _role(user):
    if user is None:
        return None
    return getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)


def _assert_account(account_id: str, current_user, owner_only: bool = False) -> None:
    if current_user is None:
        return
    uid = _uid(current_user)
    role = _role(current_user)
    if uid is None:
        raise HTTPException(403, "Нет доступа к аккаунту")
    from app.db.session import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a"), {"a": account_id}).first()
        if not row:
            raise HTTPException(404, "Аккаунт не найден")
        owner = row[0]
        # BORIS platform owner intentionally sees the full client portfolio in
        # /api/accounts/list. MCN must use the same product scope; otherwise the
        # owner sees a client in the UI but gets 403 on its telephony setup.
        if role == "owner":
            return
        if owner is not None and int(owner) == int(uid):
            return
        if owner_only:
            raise HTTPException(403, "Настройка оператора доступна только владельцу аккаунта")
        allowed = db.execute(text("""
        SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE
        """), {"u": int(uid), "a": account_id}).first()
        if not allowed:
            raise HTTPException(403, "Нет доступа к аккаунту")
    finally:
        db.close()


def _assert_phone_entitlement(account_id: str) -> dict:
    """MCN mutation gate: carrier setup is a paid BORIS Phone capability."""
    from app.services.telephony_core import phone_entitlement_status
    state = phone_entitlement_status(str(account_id or "").strip())
    if not bool(state.get("active")):
        raise HTTPException(
            409,
            {
                "code": "phone_entitlement_required",
                "message": "Для подключения MCN нужен активный оплаченный BORIS Phone.",
            },
        )
    return state


@router.get("/status")
def status(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account(account_id, current_user)
    return mcn_status(account_id)


@router.get("/readiness")
def readiness(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account(account_id, current_user)
    return mcn_readiness(account_id)


@router.get("/trunks")
def trunks(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account(account_id, current_user)
    return {"status": "ok", "items": trunk_list(account_id)}


@router.get("/setup")
def setup(account_id: str, current_user=Depends(get_current_user_or_internal)):
    """One safe owner-facing snapshot for MCN onboarding; secrets are never returned."""
    _assert_account(account_id, current_user)
    from app.services.asterisk_gateway import mcn_account_transport_health
    from app.services.mcn_network_guard import mcn_firewall_health
    return {
        "status": "ok",
        "operator": "mcn",
        "trunks": trunk_list(account_id),
        "dids": did_list(account_id),
        "readiness": mcn_readiness(account_id),
        "transport": mcn_account_transport_health(account_id),
        "network": mcn_firewall_health(),
    }


@router.post("/onboard")
def onboard(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    """Atomically save the MCN trunk + DID, then let BORIS apply and verify transport."""
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account(account_id, current_user, owner_only=True)
    _assert_phone_entitlement(account_id)

    trunk_payload = dict(payload.get("trunk") or {})
    did_payload = dict(payload.get("did") or {})
    if not did_payload:
        raise HTTPException(422, "did обязателен для атомарного подключения MCN")

    result = onboarding_upsert(account_id, trunk_payload, did_payload)
    if result.get("status") != "ok":
        raise HTTPException(400, result)

    actor_id = _uid(current_user)
    from app.services.telephony_core import save_provider_config, verify_provider_connection
    provider_cfg = save_provider_config(
        account_id, "mcn",
        credentials={"transport": "mcn_trunk", "account_id": account_id},
        public_config={"operator": "mcn", "account_id": account_id},
        actor_user_id=actor_id,
    )

    from app.services.asterisk_gateway import mcn_pjsip_guardian
    try:
        result["pjsip_guardian"] = mcn_pjsip_guardian()
    except Exception as exc:
        result["pjsip_guardian"] = {
            "status": "degraded",
            "error_code": type(exc).__name__[:120],
            "owner_action_required": False,
        }

    try:
        result["provider_verification"] = (
            verify_provider_connection(account_id, actor_id)
            if provider_cfg.get("status") == "ok"
            else {"status": "provider_config_failed", "connected": False}
        )
    except Exception as exc:
        result["provider_verification"] = {
            "status": "guardian_error",
            "error_code": type(exc).__name__[:120],
            "owner_action_required": False,
        }

    result["provider_config"] = {
        "status": provider_cfg.get("status"),
        "provider": "mcn",
    }
    result["readiness"] = mcn_readiness(account_id)
    result["pjsip_apply"] = "automatic_guardian"
    result["autofinalize"] = True
    result["atomic"] = True
    return result


@router.post("/trunks")
def save_trunk(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account(account_id, current_user, owner_only=True)
    _assert_phone_entitlement(account_id)
    result = trunk_upsert(account_id, payload)
    if result.get("status") not in {"ok"}:
        raise HTTPException(400, result)
    # MCN is the carrier behind BORIS's own Asterisk, not a second VATS. Selecting
    # an MCN trunk should therefore automatically select the BORIS MCN adapter;
    # the user must not repeat provider setup in another screen.
    from app.services.telephony_core import save_provider_config, verify_provider_connection
    actor_id = _uid(current_user)
    provider_cfg = save_provider_config(
        account_id, "mcn",
        credentials={"transport": "mcn_trunk", "account_id": account_id},
        public_config={"operator": "mcn", "account_id": account_id},
        actor_user_id=actor_id,
    )
    # Apply immediately when safe. The guardian refuses a disruptive restart
    # while any Asterisk channel is active and will retry from the runtime loop.
    from app.services.asterisk_gateway import mcn_pjsip_guardian
    try:
        result["pjsip_guardian"] = mcn_pjsip_guardian()
    except Exception as exc:
        result["pjsip_guardian"] = {"status": "degraded", "error_code": type(exc).__name__[:120],
                                     "owner_action_required": False}
    verification = None
    if provider_cfg.get("status") == "ok":
        # Verify after the safe apply attempt so a trunk that registered quickly
        # can become connected in the same owner action. Slow carrier registration
        # is retried by the runtime guardians without owner involvement.
        verification = verify_provider_connection(account_id, actor_id)
    result["provider_config"] = {
        "status": provider_cfg.get("status"),
        "provider": "mcn",
        "verification": verification,
    }
    result["pjsip_apply"] = "automatic_guardian"
    return result


@router.get("/dids")
def dids(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account(account_id, current_user)
    return {"status": "ok", "items": did_list(account_id)}


@router.post("/dids")
def save_did(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account(account_id, current_user, owner_only=True)
    _assert_phone_entitlement(account_id)
    result = did_upsert(account_id, payload)
    if result.get("status") != "ok":
        raise HTTPException(400, result)

    # MCN_DID_AUTOFINALIZE_V1:
    # The DID is normally the last owner-provided input. Do not make the owner
    # press another "verify" button or wait for the next periodic tick.
    # Apply/reconcile PJSIP immediately when safe, then run the same read-only
    # provider health check used by the background guardian. Neither step
    # originates a paid/test call.
    actor_id = _uid(current_user)
    from app.services.asterisk_gateway import mcn_pjsip_guardian
    from app.services.telephony_core import verify_provider_connection
    try:
        result["pjsip_guardian"] = mcn_pjsip_guardian()
    except Exception as exc:
        result["pjsip_guardian"] = {
            "status": "degraded",
            "error_code": type(exc).__name__[:120],
            "owner_action_required": False,
        }
    try:
        result["provider_verification"] = verify_provider_connection(account_id, actor_id)
    except Exception as exc:
        result["provider_verification"] = {
            "status": "guardian_error",
            "error_code": type(exc).__name__[:120],
            "owner_action_required": False,
        }
    result["readiness"] = mcn_readiness(account_id)
    result["autofinalize"] = True
    return result


@router.get("/tariffs/resolve")
def resolve_tariff(number: str, direction_type: str | None = None, at_date: str | None = None,
                   current_user=Depends(get_current_user_or_internal)):
    # Carrier tariff table is platform-level reference data; authenticated/internal access is sufficient.
    return {"status": "ok", "rate": tariff_rate_resolve(number, direction_type=direction_type, at_date=at_date)}


@router.post("/cdr/reconcile")
def reconcile(payload: dict = Body(default={}), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip() or None
    if account_id:
        _assert_account(account_id, current_user, owner_only=True)
    elif current_user is not None and _role(current_user) != "owner":
        raise HTTPException(403, "Только владелец")
    return reconcile_carrier_cdr(account_id=account_id, limit=int(payload.get("limit") or 500))
