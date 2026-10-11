# -*- coding: utf-8 -*-
"""Client pilot lifecycle sweep.

Runs from the existing owner-report scheduler. It never grants or revokes paid
access: product gates expire themselves from authoritative clocks. The sweep
only projects state and emits idempotent owner-facing BORIS notifications.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.notification_store import append_notification


def sweep_client_pilots() -> dict:
    db = SessionLocal()
    checked = 0
    ending_soon = 0
    expired = 0
    notified = 0
    converted_to_paid = 0
    failures = []
    try:
        account_ids = [
            str(r[0])
            for r in db.execute(text(
                "select account_id from accounts "
                "where account_id is not null order by id"
            )).fetchall()
            if r and r[0]
        ]
        now = datetime.now(timezone.utc)
        for aid in account_ids:
            checked += 1
            try:
                receipt = publish_owner_pilot_notification(db, aid, now=now)
                if not receipt.get("handled"):
                    receipt = publish_legacy_client_pilot_notification(db, aid, now=now)
                state = receipt.get("state")
                ending_soon += int(state == "ENDING_SOON")
                expired += int(state == "EXPIRED")
                converted_to_paid += int(state == "CONVERTED_TO_PAID")
                notified += int(receipt.get("status") == "stored")
                db.commit()
            except Exception as exc:
                db.rollback()
                failures.append({
                    "account_id": aid,
                    "error": type(exc).__name__ + ":" + str(exc)[:160],
                })
        return {
            "status": "ok" if not failures else "partial",
            "checked": checked,
            "ending_soon": ending_soon,
            "expired": expired,
            "notified": notified,
            "converted_to_paid": converted_to_paid,
            "external_delivery_proven": False,
            "failures": failures[:20],
        }
    finally:
        db.close()

def owner_pilot_notification_contract():
    """Installed existing-scheduler notification adapter; no notification is sent."""
    return {"contract": "boris.owner_pilot.notifications", "version": 2,
            "existing_scheduler": True, "durable_event_idempotency": True,
            "legacy_client_pilot_v1_durable_receipts": True,
            "events": ["ENDING_SOON", "EXPIRED", "CONVERTED_TO_PAID"]}


def publish_owner_pilot_notification(db, account_id: str, *, now=None):
    """Append to existing internal notification storage, atomically with receipt.

    This function never commits its caller transaction or calls external delivery.
    The receipt lives in the current pilot, so eviction from the 50-item UI list
    cannot cause duplicate lifecycle notifications.
    """
    from app.api.billing import _load_billing, _save_billing, owner_pilot_state
    from app.models.storage import Storage
    aid = str(account_id or "").strip()
    current = now or datetime.now(timezone.utc)
    initial = _load_billing(aid, db=db) or {}
    initial_raw = initial.get("owner_pilot") or {}
    if not isinstance(initial_raw, dict) or initial_raw.get("contract_version") != 2:
        return {"handled": False, "status": "legacy_or_no_pilot"}
    if not db.execute(text("SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"),
                      {"a": aid}).first():
        return {"handled": True, "status": "account_not_found"}
    db.query(Storage).filter(Storage.account_id == aid, Storage.key == "billing").populate_existing().with_for_update().all()
    billing = _load_billing(aid, db=db) or {}
    pilot = owner_pilot_state(billing, now=current)
    raw = dict(billing.get("owner_pilot") or {})
    state = str(pilot.get("state") or "")
    pilot_id = str(pilot.get("pilot_id") or "")
    if not pilot_id or state not in {"ENDING_SOON", "EXPIRED", "CONVERTED_TO_PAID"}:
        return {"handled": True, "status": "no_event", "state": state}
    if state == "CONVERTED_TO_PAID" and not (raw.get("paid_conversion") or {}).get("converted"):
        return {"handled": True, "status": "conversion_not_proven", "state": state}
    receipts = dict(raw.get("notification_receipts") or {})
    event_id = "owner-pilot:" + aid + ":" + pilot_id + ":" + state
    if isinstance(receipts.get(state), dict) and receipts[state].get("event_id") == event_id:
        return {"handled": True, "status": "duplicate", "state": state,
                "event_id": event_id, "external_delivery_proven": False}
    messages = {
        "ENDING_SOON": ("Пилот BORIS заканчивается менее чем через 24 часа. "
                        "Проверьте выбранные продукты и оплату. Данные и история сохраняются; "
                        "оплаченные права продолжают действовать по своим срокам."),
        "EXPIRED": ("Срок пилота BORIS завершён. Доступ по пилоту истёк; данные и история сохраняются. "
                    "Действующие оплаченные продукты продолжают работать по своим правам. "
                    "Для продолжения остальных выбранных продуктов подтвердите оплату."),
        "CONVERTED_TO_PAID": ("Переход пилота BORIS к оплате подтверждён: после начисления проверены "
                              "действующие оплаченные права всех выбранных продуктов."),
    }
    kind = {"ENDING_SOON": "client_pilot_ending_soon", "EXPIRED": "client_pilot_expired",
            "CONVERTED_TO_PAID": "client_pilot_converted_to_paid"}[state]
    append_notification(db, aid, {
        "ts": current.isoformat(timespec="seconds"), "text": messages[state],
        "related_results": [], "read": False,
        "severity": "INFO" if state == "CONVERTED_TO_PAID" else "ACTION_REQUIRED",
        "kind": kind, "pilot_id": pilot_id, "pilot_state": state,
        "expires_at": pilot.get("expires_at"), "services": list(pilot.get("services") or []),
        "external_delivery_proven": False,
    }, idempotency_key=event_id, commit=False)
    receipts[state] = {"event_id": event_id, "stored_at": current.isoformat(),
                       "channel": "BORIS_notification_store", "external_delivery_proven": False}
    raw["notification_receipts"] = receipts
    _save_billing(aid, {"owner_pilot": raw}, db=db, commit=False)
    return {"handled": True, "status": "stored", "state": state, "event_id": event_id,
            "channel": "BORIS_notification_store", "external_delivery_proven": False}

def publish_legacy_client_pilot_notification(db, account_id: str, *, now=None):
    """Durable lifecycle receipt for the existing stored client_pilot_v1.

    Only pilot notification metadata and the existing internal notification list
    are written in the caller transaction. No paid access or external delivery.
    """
    import hashlib
    import json
    from app.api.admin_clients import _pilot_state, _utc_dt
    from app.models.storage import Storage
    from app.services.notification_store import _load_locked
    aid = str(account_id or "").strip()
    current = _utc_dt(now) if now is not None else datetime.now(timezone.utc)
    initial = db.query(Storage).filter(
        Storage.account_id == aid, Storage.key == "client_pilot_v1"
    ).order_by(Storage.id.desc()).first()
    if initial is None:
        return {"handled": False, "status": "no_stored_legacy_pilot"}
    if not db.execute(text("SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"),
                      {"a": aid}).first():
        return {"handled": True, "status": "account_not_found"}
    row = db.query(Storage).filter(
        Storage.account_id == aid, Storage.key == "client_pilot_v1"
    ).order_by(Storage.id.desc()).populate_existing().with_for_update().first()
    if row is None:
        return {"handled": False, "status": "no_stored_legacy_pilot"}
    try:
        raw = json.loads(row.value or "{}")
    except (TypeError, ValueError):
        return {"handled": True, "status": "invalid_legacy_pilot"}
    if not isinstance(raw, dict) or not current:
        return {"handled": True, "status": "invalid_legacy_pilot"}
    state = _pilot_state(raw, now=current)
    starts = _utc_dt(raw.get("starts_at")) or _utc_dt(raw.get("started_at"))
    expires = _utc_dt(raw.get("expires_at"))
    if state not in {"ENDING_SOON", "EXPIRED", "CONVERTED_TO_PAID"}:
        return {"handled": True, "status": "no_event", "state": state}
    if not starts or not expires or expires <= starts:
        return {"handled": True, "status": "invalid_legacy_identity", "state": state}
    services = raw.get("services") if isinstance(raw.get("services"), list) else []
    if state == "CONVERTED_TO_PAID":
        evidence = raw.get("paid_conversion")
        evidence = evidence if isinstance(evidence, dict) else {}
        scopes = evidence.get("scope_evidence")
        scopes = scopes if isinstance(scopes, dict) else {}
        conversion_proven = (
            evidence.get("converted") is True and evidence.get("payment_proven") is True
            and evidence.get("all_selected_covered") is True and bool(services)
            and not evidence.get("missing_services")
            and all(isinstance(scopes.get(p), dict) and scopes[p].get("covered") is True
                    for p in services if isinstance(p, str))
            and all(isinstance(p, str) for p in services)
        )
        if not conversion_proven:
            return {"handled": True, "status": "conversion_not_proven", "state": state}
    identity = hashlib.sha256(json.dumps(
        [aid, row.id, str(raw.get("pilot_id") or ""), starts.isoformat(), expires.isoformat()],
        ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    event_id = "client-pilot-v1:" + aid + ":" + identity + ":" + state
    receipts = raw.get("notification_receipts")
    receipts = dict(receipts) if isinstance(receipts, dict) else {}
    previous = receipts.get(state)
    if isinstance(previous, dict) and previous.get("event_id") == event_id:
        return {"handled": True, "status": "duplicate", "state": state,
                "event_id": event_id, "external_delivery_proven": False}
    kind = {"ENDING_SOON": "client_pilot_ending_soon", "EXPIRED": "client_pilot_expired",
            "CONVERTED_TO_PAID": "client_pilot_converted_to_paid"}[state]
    legacy_key = (("client-pilot-ending:" if state == "ENDING_SOON" else "client-pilot-expired:")
                  + aid + ":" + expires.isoformat()) if state != "CONVERTED_TO_PAID" else None
    # Adoption prevents a second event when upgrading an existing retained item.
    # A timestamp before this pilot's start cannot prove this pilot's event.
    _, items = _load_locked(db, aid)
    adopted = next((item for item in items
                    if legacy_key and item.get("notification_id") == legacy_key
                    and item.get("kind") == kind and _utc_dt(item.get("ts"))
                    and starts <= _utc_dt(item.get("ts")) <= current), None)
    messages = {
        "ENDING_SOON": ("Пилот BORIS заканчивается менее чем через 24 часа. "
                        "Проверьте выбранные продукты и оплату. Данные и история сохраняются; "
                        "оплаченные права продолжают действовать по своим срокам."),
        "EXPIRED": ("Срок пилота BORIS завершён. Доступ по пилоту истёк; данные и история сохраняются. "
                    "Действующие оплаченные продукты продолжают работать по своим правам. "
                    "Для продолжения остальных выбранных продуктов подтвердите оплату."),
        "CONVERTED_TO_PAID": ("Переход пилота BORIS к оплате подтверждён: после начисления проверены "
                              "действующие оплаченные права всех выбранных продуктов."),
    }
    if adopted is None:
        append_notification(db, aid, {
            "ts": current.isoformat(timespec="seconds"), "text": messages[state],
            "related_results": [], "read": False,
            "severity": "INFO" if state == "CONVERTED_TO_PAID" else "ACTION_REQUIRED",
            "kind": kind, "pilot_id": str(raw.get("pilot_id") or identity),
            "pilot_state": state, "expires_at": expires.isoformat(), "services": services,
            "pilot_source": "client_pilot_v1", "external_delivery_proven": False,
        }, idempotency_key=event_id, commit=False)
    receipts[state] = {
        "event_id": event_id, "stored_at": current.isoformat(),
        "channel": "BORIS_notification_store", "external_delivery_proven": False,
        "adopted_notification_id": legacy_key if adopted is not None else None,
    }
    raw["notification_receipts"] = receipts
    row.value = json.dumps(raw, ensure_ascii=False)
    db.flush()
    return {"handled": True, "status": "adopted" if adopted is not None else "stored",
            "state": state, "event_id": event_id, "channel": "BORIS_notification_store",
            "external_delivery_proven": False}
