# -*- coding: utf-8 -*-
"""Multi-channel lead notifications: Web Push, MAX group and low-noise email.

External I/O is performed only by the durable outbox dispatcher in the worker.
MOP/CRM business paths only enqueue idempotent events.
"""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.crypto_utils import encrypt_secret, decrypt_secret
from app.services.exception_observability import observe_suppressed

MAX_API = "https://platform-api2.max.ru"
OWNER_MAX_TRANSPORT_ACCOUNT_ID = os.getenv("BORIS_OWNER_MAX_TRANSPORT_ACCOUNT_ID") or os.getenv("BORIS_PLANETA_MAX_ACCOUNT_ID") or "planetazayavki_65985"
YANDEX_METRIKA_COUNTER_ID = str(os.getenv("YANDEX_METRIKA_COUNTER_ID") or "111297252").strip()
WEBPUSH_SUBJECT = os.getenv("BORIS_WEBPUSH_VAPID_SUBJECT") or "mailto:support@boris-ai.pro"
WEBPUSH_PRIVATE_KEY = os.getenv("BORIS_WEBPUSH_VAPID_PRIVATE_KEY_FILE") or ""
WEBPUSH_PUBLIC_KEY = os.getenv("BORIS_WEBPUSH_VAPID_PUBLIC_KEY") or ""
MAX_CA_BUNDLE = os.getenv("BORIS_MAX_CA_BUNDLE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "certs", "max-platform-ca-bundle.pem"
)


def _max_verify():
    """Use the MAX/Минцифры trust bundle when present; never disable TLS verification."""
    return MAX_CA_BUNDLE if os.path.isfile(MAX_CA_BUNDLE) else True


def ensure_schema() -> None:
    db = SessionLocal()
    try:
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.commit()
    finally:
        db.close()


def public_key() -> str:
    return str(os.getenv("BORIS_WEBPUSH_VAPID_PUBLIC_KEY") or WEBPUSH_PUBLIC_KEY or "")


def get_settings(account_id: str) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("""
          SELECT account_id,web_push_enabled,max_enabled,max_chat_id,max_leadership_chat_id,
                 (max_bot_token_enc IS NOT NULL AND max_bot_token_enc<>'') max_token_configured,
                 email_enabled,email_to,email_mode,updated_at
          FROM lead_notification_settings WHERE account_id=:a
        """), {"a": account_id}).mappings().first()
        subs = int(db.execute(text(
            "SELECT count(*) FROM lead_push_subscriptions WHERE account_id=:a AND active=true"
        ), {"a": account_id}).scalar() or 0)
        if not row:
            return {
                "account_id": account_id, "web_push_enabled": True, "max_enabled": False,
                "max_chat_id": "", "max_leadership_chat_id": "", "max_token_configured": False, "email_enabled": False,
                "email_to": "", "email_mode": "critical_only", "active_push_subscriptions": subs,
                "web_push_ready": bool(public_key() and (os.getenv("BORIS_WEBPUSH_VAPID_PRIVATE_KEY_FILE") or WEBPUSH_PRIVATE_KEY)),
            }
        out = dict(row)
        out["active_push_subscriptions"] = subs
        out["web_push_ready"] = bool(public_key() and (os.getenv("BORIS_WEBPUSH_VAPID_PRIVATE_KEY_FILE") or WEBPUSH_PRIVATE_KEY))
        return out
    finally:
        db.close()


def save_settings(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    email_mode = str(payload.get("email_mode") or "critical_only").strip().lower()
    if email_mode not in {"critical_only", "all"}:
        email_mode = "critical_only"
    raw_token = str(payload.get("max_bot_token") or "").strip()
    clear_token = bool(payload.get("clear_max_token"))
    db = SessionLocal()
    try:
        db.execute(text("""
          INSERT INTO lead_notification_settings(
            account_id,web_push_enabled,max_enabled,max_chat_id,max_leadership_chat_id,max_bot_token_enc,
            email_enabled,email_to,email_mode
          ) VALUES(:a,:wp,:me,:mc,:mlc,:mt,:ee,:et,:em)
          ON CONFLICT(account_id) DO UPDATE SET
            web_push_enabled=excluded.web_push_enabled,
            max_enabled=excluded.max_enabled,
            max_chat_id=excluded.max_chat_id,
            max_leadership_chat_id=CASE WHEN :has_mlc THEN excluded.max_leadership_chat_id ELSE lead_notification_settings.max_leadership_chat_id END,
            max_bot_token_enc=CASE
              WHEN :clear_token THEN NULL
              WHEN :has_token THEN excluded.max_bot_token_enc
              ELSE lead_notification_settings.max_bot_token_enc END,
            email_enabled=excluded.email_enabled,
            email_to=excluded.email_to,
            email_mode=excluded.email_mode,
            updated_at=now()
        """), {
            "a": account_id,
            "wp": bool(payload.get("web_push_enabled", True)),
            "me": bool(payload.get("max_enabled", False)),
            "mc": str(payload.get("max_chat_id") or "").strip()[:120] or None,
            "mlc": str(payload.get("max_leadership_chat_id") or "").strip()[:120] or None,
            "has_mlc": "max_leadership_chat_id" in payload,
            "mt": encrypt_secret(raw_token) if raw_token else None,
            "clear_token": clear_token,
            "has_token": bool(raw_token),
            "ee": bool(payload.get("email_enabled", False)),
            "et": str(payload.get("email_to") or "").strip()[:500] or None,
            "em": email_mode,
        })
        db.commit()
    finally:
        db.close()
    return get_settings(account_id)


def register_subscription(account_id: str, user_id: int | None, subscription: dict[str, Any], user_agent: str = "") -> dict[str, Any]:
    ensure_schema()
    endpoint = str((subscription or {}).get("endpoint") or "").strip()
    keys = (subscription or {}).get("keys") or {}
    if not endpoint.startswith("https://") or not str(keys.get("p256dh") or "").strip() or not str(keys.get("auth") or "").strip():
        return {"status": "invalid_subscription"}
    raw = json.dumps(subscription, ensure_ascii=False, separators=(",", ":"))
    h = hashlib.sha256(endpoint.encode()).hexdigest()
    db = SessionLocal()
    try:
        db.execute(text("""
          INSERT INTO lead_push_subscriptions(account_id,user_id,endpoint_hash,subscription_enc,user_agent,active,last_error)
          VALUES(:a,:u,:h,:s,:ua,true,NULL)
          ON CONFLICT(account_id,endpoint_hash) DO UPDATE SET
            user_id=COALESCE(excluded.user_id,lead_push_subscriptions.user_id),
            subscription_enc=excluded.subscription_enc,user_agent=excluded.user_agent,
            active=true,last_error=NULL,updated_at=now()
        """), {"a": account_id, "u": user_id, "h": h, "s": encrypt_secret(raw), "ua": str(user_agent or "")[:500]})
        db.commit()
    finally:
        db.close()
    return {"status": "ok", "endpoint_fingerprint": h[:12]}


def unsubscribe(account_id: str, endpoint: str) -> dict[str, Any]:
    ensure_schema()
    h = hashlib.sha256(str(endpoint or "").encode()).hexdigest()
    db = SessionLocal()
    try:
        n = db.execute(text("""
          UPDATE lead_push_subscriptions SET active=false,updated_at=now()
          WHERE account_id=:a AND endpoint_hash=:h AND active=true
        """), {"a": account_id, "h": h}).rowcount
        db.commit()
        return {"status": "ok", "changed": int(n or 0)}
    finally:
        db.close()


def queue_event(account_id: str, event_key: str, event_type: str, title: str, body: str,
                *, severity: str = "normal", url: str = "", payload: dict | None = None) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("""
          INSERT INTO lead_notification_outbox(account_id,event_key,event_type,severity,title,body,url,payload_json)
          VALUES(:a,:k,:e,:s,:t,:b,:u,CAST(:p AS jsonb))
          ON CONFLICT(event_key) DO NOTHING RETURNING id
        """), {
            "a": account_id, "k": str(event_key)[:300], "e": str(event_type)[:80],
            "s": "critical" if severity == "critical" else "normal",
            "t": str(title)[:240], "b": str(body)[:3500], "u": str(url or "")[:1000] or None,
            "p": json.dumps(payload or {}, ensure_ascii=False),
        }).scalar()
        db.commit()
        return {"status": "queued" if row else "duplicate", "id": int(row) if row else None}
    finally:
        db.close()


def queue_mop_events(db, draft_id: int, analysis: dict | None) -> list[dict[str, Any]]:
    """Queue meaningful lead events; no external I/O and no duplicated spam."""
    analysis = analysis if isinstance(analysis, dict) else {}
    d = db.execute(text("""
      SELECT id,account_id,avito_chat_id,avito_message_id,item_title,client_name,incoming_text,created_at
      FROM mop_drafts WHERE id=:i
    """), {"i": draft_id}).mappings().first()
    if not d:
        return []
    account_id = str(d["account_id"])
    name = db.execute(text("SELECT coalesce(name,account_id) FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar() or account_id
    prior = int(db.execute(text(
        "SELECT count(*) FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c AND id<:i"
    ), {"a": account_id, "c": d["avito_chat_id"], "i": draft_id}).scalar() or 0)
    prior_phone = bool(db.execute(text("""
      SELECT 1 FROM mop_drafts
      WHERE account_id=:a AND avito_chat_id=:c AND id<:i
        AND lower(coalesce(ai_summary,'')) LIKE '%\"phone_received\": true%'
      LIMIT 1
    """), {"a": account_id, "c": d["avito_chat_id"], "i": draft_id}).first())
    client = str(d.get("client_name") or "Клиент")
    item = str(d.get("item_title") or "").strip()
    incoming = " ".join(str(d.get("incoming_text") or "").split())[:500]
    suffix = (" · " + item[:100]) if item else ""
    url = "/messages?account_id=%s&chat_id=%s" % (account_id, d["avito_chat_id"])
    out = []
    if prior == 0:
        out.append(queue_event(
            account_id, f"mop:new:{draft_id}", "new_lead",
            f"🆕 Новый лид · {name}",
            f"{client}{suffix}\n{incoming}" if incoming else f"{client}{suffix}",
            url=url, payload={"draft_id": draft_id, "chat_id": d["avito_chat_id"]},
        ))
    if analysis.get("phone_received") is True and not prior_phone:
        out.append(queue_event(
            account_id, f"mop:phone:{draft_id}", "hot_lead",
            f"🔥 Горячий лид · {name}",
            f"{client}: контакт получен. Нужна быстрая обработка.{suffix}\n{incoming}" if incoming else f"{client}: контакт получен. Нужна быстрая обработка.{suffix}",
            severity="critical", url=url, payload={"draft_id": draft_id, "chat_id": d["avito_chat_id"]},
        ))
    if analysis.get("human_handoff") is True:
        reason = str(analysis.get("handoff_reason") or "нужен сотрудник")[:300]
        out.append(queue_event(
            account_id, f"mop:human:{draft_id}", "human_required",
            f"👤 Нужен сотрудник · {name}",
            f"{client}: {reason}.{suffix}\n{incoming}" if incoming else f"{client}: {reason}.{suffix}",
            severity="critical", url=url, payload={"draft_id": draft_id, "chat_id": d["avito_chat_id"]},
        ))
    return out


def queue_stage_event(db, deal_id: int, account_id: str, old_stage: str, new_stage: str,
                      status: str, lost_reason: str | None = None) -> dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return {"status": "no_account"}
    name = db.execute(text("SELECT coalesce(name,account_id) FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar() or account_id
    d = db.execute(text("""
      SELECT d.title,c.display_name contact_name,c.primary_phone
      FROM boris_crm_deals d LEFT JOIN boris_crm_contacts c ON c.id=d.contact_id
      WHERE d.id=:d
    """), {"d": deal_id}).mappings().first() or {}
    activity_id = db.execute(text("""
      SELECT id FROM boris_crm_activities
      WHERE deal_id=:d AND activity_type='stage_change'
      ORDER BY id DESC LIMIT 1
    """), {"d": deal_id}).scalar()
    sev = "critical" if status in {"won", "lost"} else "normal"
    body = f"{d.get('contact_name') or d.get('title') or ('Лид #'+str(deal_id))}: {old_stage or '—'} → {new_stage or '—'}"
    if lost_reason:
        body += f" · Причина: {lost_reason}"
    return queue_event(
        account_id, f"crm:stage:{activity_id or (str(deal_id)+':'+old_stage+':'+new_stage+':'+status)}", "lead_status_changed",
        ("✅ Сделка закрыта" if status == "won" else "❌ Лид потерян" if status == "lost" else "🔄 Статус лида изменён") + f" · {name}",
        body, severity=sev, url=f"/dashboard/crm/deal/{deal_id}", payload={"deal_id": deal_id, "status": status},
    )


def max_status(account_id: str, live: bool = True) -> dict[str, Any]:
    """Diagnose MAX connection without exposing secrets."""
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("""
          SELECT max_enabled,max_chat_id,max_leadership_chat_id,max_bot_token_enc,
                 max_webhook_secret_hash,updated_at
          FROM lead_notification_settings WHERE account_id=:a
        """), {"a": account_id}).mappings().first()
    finally:
        db.close()
    owner_route_db = SessionLocal()
    try:
        owner_route = owner_route_db.execute(text(
            "SELECT chat_id,chat_title,updated_at FROM lead_notification_max_owner_routes WHERE account_id=:a"
        ), {"a": account_id}).mappings().first()
    finally:
        owner_route_db.close()
    if not row:
        return {
            "status": "not_configured", "token_configured": False,
            "token_valid": False, "webhook_registered": False,
            "chat_connected": False, "bot_in_chat": False,
            "leadership_chat_connected": False, "leadership_bot_in_chat": False,
            "owner_chat_connected": bool(owner_route), "owner_bot_in_chat": None,
        }
    token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
    out = {
        "status": "configured" if token else "token_required",
        "enabled": bool(row.get("max_enabled")),
        "token_configured": bool(token),
        "token_valid": None,
        "bot": None,
        "webhook_registered": bool(row.get("max_webhook_secret_hash")),
        "webhook_remote_confirmed": None,
        "chat_connected": bool(str(row.get("max_chat_id") or "").strip()),
        "chat_id_present": bool(str(row.get("max_chat_id") or "").strip()),
        "leadership_chat_connected": bool(str(row.get("max_leadership_chat_id") or "").strip()),
        "leadership_bot_in_chat": None,
        "owner_chat_connected": bool(owner_route and str(owner_route.get("chat_id") or "").strip()),
        "owner_bot_in_chat": None,
        "owner_chat_title": str((owner_route or {}).get("chat_title") or "")[:120],
        "chat": None,
        "bot_in_chat": None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }
    if not live or not token:
        return out

    headers = {"Authorization": token}
    try:
        r = requests.get(MAX_API + "/me", headers=headers, timeout=10, verify=_max_verify())
        out["token_valid"] = r.status_code == 200
        if r.status_code == 200:
            try:
                p = r.json() or {}
            except Exception:
                p = {}
            out["bot"] = {
                "user_id": p.get("user_id"),
                "first_name": p.get("first_name") or p.get("name"),
                "username": p.get("username"),
                "is_bot": bool(p.get("is_bot", True)),
            }
        else:
            out["status"] = "invalid_token" if r.status_code == 401 else "max_error"
            out["token_http_status"] = int(r.status_code)
            return out
    except Exception as exc:
        out["status"] = "max_unavailable"
        out["token_valid"] = False
        out["error_type"] = type(exc).__name__
        return out

    base = str(os.getenv("BORIS_PUBLIC_WEB_URL") or "https://boris-ai.pro").rstrip("/")
    expected_url = base + "/api/public/lead-notifications/max/webhook/" + str(account_id)
    try:
        r = requests.get(MAX_API + "/subscriptions", headers=headers, timeout=10, verify=_max_verify())
        if r.status_code == 200:
            try:
                payload = r.json() or {}
            except Exception:
                payload = {}
            subscriptions = payload.get("subscriptions") or []
            out["webhook_remote_confirmed"] = any(
                str((s or {}).get("url") or "").rstrip("/") == expected_url.rstrip("/")
                for s in subscriptions if isinstance(s, dict)
            )
        else:
            out["webhook_remote_confirmed"] = False
            out["subscriptions_http_status"] = int(r.status_code)
    except Exception:
        out["webhook_remote_confirmed"] = False

    chat_id = str(row.get("max_chat_id") or "").strip()
    if chat_id:
        try:
            r = requests.get(
                MAX_API + "/chats/" + chat_id + "/members/me",
                headers=headers, timeout=10, verify=_max_verify(),
            )
            out["bot_in_chat"] = r.status_code == 200
            out["chat_membership_http_status"] = int(r.status_code)
            if r.status_code == 200:
                try:
                    member = r.json() or {}
                except Exception:
                    member = {}
                out["chat"] = {
                    "bot_user_id": member.get("user_id"),
                    "bot_username": member.get("username"),
                    "is_bot": bool(member.get("is_bot", True)),
                }
        except Exception:
            out["bot_in_chat"] = False

    leadership_chat_id = str(row.get("max_leadership_chat_id") or "").strip()
    if leadership_chat_id:
        try:
            rr = requests.get(
                MAX_API + "/chats/" + leadership_chat_id + "/members/me",
                headers=headers, timeout=10, verify=_max_verify(),
            )
            out["leadership_bot_in_chat"] = rr.status_code == 200
        except Exception:
            out["leadership_bot_in_chat"] = False

    owner_chat_id = str((owner_route or {}).get("chat_id") or "").strip()
    if owner_chat_id:
        try:
            rr = requests.get(
                MAX_API + "/chats/" + owner_chat_id + "/members/me",
                headers=headers, timeout=10, verify=_max_verify(),
            )
            out["owner_bot_in_chat"] = rr.status_code == 200
        except Exception:
            out["owner_bot_in_chat"] = False

    any_ready = bool(
        (out.get("chat_connected") and out.get("bot_in_chat"))
        or (out.get("leadership_chat_connected") and out.get("leadership_bot_in_chat"))
        or (out.get("owner_chat_connected") and out.get("owner_bot_in_chat"))
    )
    if out.get("token_valid") and out.get("webhook_remote_confirmed") and any_ready:
        out["status"] = "ready"
    elif out.get("token_valid") and out.get("webhook_remote_confirmed") and not any_ready:
        out["status"] = "waiting_for_group"
    elif out.get("token_valid") and not out.get("webhook_remote_confirmed"):
        out["status"] = "webhook_missing"
    elif out.get("chat_connected") and out.get("bot_in_chat") is False:
        out["status"] = "bot_not_in_chat"
    return out


def reset_max_webhook(account_id: str) -> dict[str, Any]:
    """Delete the exact MAX webhook subscription and recreate it cleanly."""
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT max_bot_token_enc FROM lead_notification_settings WHERE account_id=:a"), {"a": account_id}).mappings().first() or {}
    finally:
        db.close()
    token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
    if not token:
        return {"status": "token_required"}
    base = str(os.getenv("BORIS_PUBLIC_WEB_URL") or "https://boris-ai.pro").rstrip("/")
    webhook_url = base + "/api/public/lead-notifications/max/webhook/" + str(account_id)
    delete_status = None
    delete_success = None
    try:
        rr = requests.delete(
            MAX_API + "/subscriptions",
            params={"url": webhook_url},
            headers={"Authorization": token},
            timeout=12, verify=_max_verify(),
        )
        delete_status = int(rr.status_code)
        try:
            pp = rr.json() or {}
        except Exception:
            pp = {}
        delete_success = pp.get("success")
    except Exception as exc:
        return {"status": "max_unavailable", "phase": "delete", "message": type(exc).__name__}
    created = connect_max_webhook(account_id)
    return {
        "status": created.get("status"),
        "delete_http_status": delete_status,
        "delete_success": delete_success,
        "created": created,
    }


def connect_max_webhook(account_id: str) -> dict[str, Any]:
    """Register production MAX webhook. chat_id is learned from bot_added."""
    import secrets
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT max_bot_token_enc FROM lead_notification_settings WHERE account_id=:a"), {"a": account_id}).mappings().first() or {}
    finally:
        db.close()
    token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
    if not token:
        return {"status": "token_required"}
    secret = secrets.token_urlsafe(32).replace("=", "")
    secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    base = str(os.getenv("BORIS_PUBLIC_WEB_URL") or "https://boris-ai.pro").rstrip("/")
    webhook_url = base + "/api/public/lead-notifications/max/webhook/" + str(account_id)
    try:
        r = requests.post(
            MAX_API + "/subscriptions",
            headers={"Authorization": token, "Content-Type": "application/json"},
            json={
                "url": webhook_url,
                "update_types": ["bot_added", "bot_started", "message_created", "bot_removed", "chat_title_changed"],
                "secret": secret,
            },
            timeout=12, verify=_max_verify(),
        )
        try:
            payload = r.json() or {}
        except Exception:
            payload = {}
        if r.status_code == 401:
            return {"status": "invalid_token", "http_status": 401, "message": "MAX отклонил токен бота"}
        if r.status_code != 200 or payload.get("success") is False:
            return {"status": "max_error", "http_status": int(r.status_code), "message": str(payload.get("message") or "MAX webhook setup failed")[:300]}
    except Exception as exc:
        return {"status": "max_unavailable", "message": type(exc).__name__}
    db = SessionLocal()
    try:
        db.execute(text("""
          UPDATE lead_notification_settings
             SET max_webhook_secret_hash=:h,max_enabled=true,updated_at=now()
           WHERE account_id=:a
        """), {"a": account_id, "h": secret_hash})
        db.commit()
    finally:
        db.close()
    return {"status": "waiting_for_group", "webhook_registered": True, "chat_id": get_settings(account_id).get("max_chat_id") or ""}


def handle_max_webhook(account_id: str, supplied_secret: str, update: dict[str, Any]) -> dict[str, Any]:
    """Validate MAX webhook and learn/remove the group chat_id."""
    import hmac
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT max_webhook_secret_hash,max_chat_id,max_leadership_chat_id,max_bot_token_enc FROM lead_notification_settings WHERE account_id=:a"), {"a": account_id}).mappings().first()
        if not row or not row.get("max_webhook_secret_hash"):
            return {"status": "not_configured"}
        got = hashlib.sha256(str(supplied_secret or "").encode()).hexdigest()
        if not hmac.compare_digest(got, str(row.get("max_webhook_secret_hash") or "")):
            return {"status": "unauthorized"}
        typ = str((update or {}).get("update_type") or "")
        message = (update or {}).get("message") or {}
        recipient = message.get("recipient") or {} if isinstance(message, dict) else {}
        sender = message.get("sender") or {} if isinstance(message, dict) else {}
        chat_id = str(
            (update or {}).get("chat_id")
            or (message.get("chat_id") if isinstance(message, dict) else "")
            or (recipient.get("chat_id") if isinstance(recipient, dict) else "")
            or ""
        ).strip()
        user_id = str(
            ((update or {}).get("user") or {}).get("user_id")
            or (sender.get("user_id") if isinstance(sender, dict) else "")
            or ""
        ).strip()
        print(f"MAX_WEBHOOK_EVENT account={account_id} type={typ} chat_id={'yes' if chat_id else 'no'} user_id={'yes' if user_id else 'no'}", flush=True)

        if typ == "message_created" and not bool(sender.get("is_bot") if isinstance(sender, dict) else False):
            token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
            msg_body = message.get("body") or {} if isinstance(message, dict) else {}
            msg_text = str(msg_body.get("text") or "").strip() if isinstance(msg_body, dict) else ""
            if chat_id and msg_text:
                try:
                    from app.services.max_boris_control import handle_message
                    handled = handle_message(account_id, chat_id, user_id, msg_text)
                except Exception:
                    handled = {"handled": False}
                if handled.get("handled"):
                    reply = str(handled.get("reply") or "Готово.")[:3900]
                    if token:
                        try:
                            rr = requests.post(
                                MAX_API + "/messages",
                                params={"chat_id": chat_id},
                                headers={"Authorization": token, "Content-Type": "application/json"},
                                json={"text": reply, "notify": True},
                                timeout=8, verify=_max_verify(),
                            )
                            return {
                                "status": "command_replied" if 200 <= rr.status_code < 300 else "command_reply_failed",
                                "chat_id": chat_id,
                                "command_handled": True,
                            }
                        except Exception:
                            return {"status": "command_reply_failed", "chat_id": chat_id, "command_handled": True}
                    return {"status": "command_handled_no_transport", "chat_id": chat_id}
            return {"status": "message_received", "chat_id": chat_id}

        if typ == "bot_started" and chat_id:
            token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
            if token:
                try:
                    requests.post(
                        MAX_API + "/messages",
                        params={"chat_id": chat_id},
                        headers={"Authorization": token, "Content-Type": "application/json"},
                        json={
                            "text": "✅ BORIS AI работает.\n\nДля подключения уведомлений Планеты добавьте меня в группу «Планета Мебели оповещения».",
                            "notify": True,
                        },
                        timeout=8, verify=_max_verify(),
                    )
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=650)
            return {"status": "started", "chat_id": chat_id}
        if typ == "bot_added" and chat_id:
            token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
            title = str(
                (update or {}).get("title")
                or ((update or {}).get("chat") or {}).get("title")
                or ""
            ).strip()
            if token and not title:
                try:
                    rr = requests.get(
                        MAX_API + "/chats/" + chat_id,
                        headers={"Authorization": token},
                        timeout=8, verify=_max_verify(),
                    )
                    if rr.status_code == 200:
                        pp = rr.json() or {}
                        title = str(pp.get("title") or pp.get("name") or "").strip()
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=670)
            low_title = title.lower()
            is_leadership = "руковод" in low_title
            is_planeta = "планета" in low_title or "альбина" in low_title
            is_owner = ("boris" in low_title or "борис" in low_title) and any(
                token in low_title for token in ("управлен", "владел", "кирилл", "owner")
            )
            if is_leadership:
                db.execute(text("""
                  UPDATE lead_notification_settings
                     SET max_leadership_chat_id=:c,max_enabled=true,updated_at=now()
                   WHERE account_id=:a
                """), {"a": account_id, "c": chat_id})
                route = "leadership"
                body = "Чат руководства найден. Сюда будут приходить деньги, статистика, РОП, реактивация и итог виртуального маркетолога."
                try:
                    from app.services.max_boris_control import register_chat
                    register_chat(chat_id, account_id, "unbound", chat_title=title, max_user_id=user_id)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=689)
            elif is_planeta:
                db.execute(text("""
                  UPDATE lead_notification_settings
                     SET max_chat_id=:c,max_enabled=true,updated_at=now()
                   WHERE account_id=:a
                """), {"a": account_id, "c": chat_id})
                route = "main"
                body = "Рабочий чат Планеты найден. Сюда будут приходить лиды, задачи, реактивация и оценки РОПа для Альбины."
                try:
                    from app.services.max_boris_control import register_chat
                    register_chat(chat_id, account_id, "client", boris_account_id=account_id, chat_title=title, max_user_id=user_id)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=702)
            elif is_owner:
                db.execute(text("""
                  INSERT INTO lead_notification_max_owner_routes(account_id,chat_id,chat_title,updated_at)
                  VALUES(:a,:c,:t,now())
                  ON CONFLICT(account_id) DO UPDATE SET chat_id=excluded.chat_id,chat_title=excluded.chat_title,updated_at=now()
                """), {"a": account_id, "c": chat_id, "t": title[:200]})
                db.execute(text("UPDATE lead_notification_settings SET max_enabled=true,updated_at=now() WHERE account_id=:a"), {"a": account_id})
                db.execute(text("""
                  UPDATE lead_notification_outbox
                     SET status='pending',next_attempt_at=now(),last_error=NULL,updated_at=now()
                   WHERE account_id=:a AND status='waiting_configuration'
                     AND payload_json->>'max_channel'='owner'
                """), {"a": account_id})
                route = "owner"
                body = "Чат владельца найден. Сюда будут сразу приходить новые заявки с сайта, ежедневная статистика Метрики и команды управления BORIS."
                try:
                    from app.services.max_boris_control import register_chat
                    register_chat(chat_id, account_id, "owner", chat_title=title, max_user_id=user_id)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=722)
            else:
                try:
                    from app.services.max_boris_control import register_chat
                    register_chat(chat_id, account_id, "unbound", chat_title=title, max_user_id=user_id)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=728)
                db.commit()
                if token:
                    try:
                        requests.post(
                            MAX_API + "/messages",
                            params={"chat_id": chat_id},
                            headers={"Authorization": token, "Content-Type": "application/json"},
                            json={
                                "text": "✅ BORIS AI добавлен в группу, но группа пока не привязана к аккаунту. Для личного пульта владельца назовите группу «BORIS — Управление». Клиентские группы BORIS привязывает отдельно к конкретному аккаунту.",
                                "notify": True,
                            },
                            timeout=8, verify=_max_verify(),
                        )
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=743)
                return {"status": "unbound_group", "chat_id": chat_id, "route": "unbound", "title": title}
            db.commit()
            queue_event(
                account_id,
                "max:connected:" + route + ":" + chat_id,
                "channel_connected",
                "✅ MAX подключён к BORIS",
                body,
                severity="critical",
                url="/settings/notifications?account_id=" + account_id,
                payload={"max_channel": route, "delivery_scope": "max_only", "chat_title": title},
            )
            return {"status": "connected", "chat_id": chat_id, "route": route, "title": title}
        if typ == "bot_removed" and chat_id:
            try:
                from app.services.max_boris_control import unregister_chat
                unregister_chat(chat_id)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=762)
            owner_chat = db.execute(text("SELECT chat_id FROM lead_notification_max_owner_routes WHERE account_id=:a"), {"a": account_id}).scalar()
            if str(owner_chat or "") == chat_id:
                db.execute(text("DELETE FROM lead_notification_max_owner_routes WHERE account_id=:a AND chat_id=:c"), {"a": account_id, "c": chat_id})
                db.commit()
                return {"status": "removed", "chat_id": chat_id, "route": "owner"}
            if str(row.get("max_leadership_chat_id") or "") == chat_id:
                db.execute(text("""
                  UPDATE lead_notification_settings
                     SET max_leadership_chat_id=NULL,
                         max_enabled=(max_chat_id IS NOT NULL AND max_chat_id<>''),
                         updated_at=now()
                   WHERE account_id=:a
                """), {"a": account_id})
                db.commit()
                return {"status": "removed", "chat_id": chat_id, "route": "leadership"}
            if str(row.get("max_chat_id") or "") == chat_id:
                db.execute(text("""
                  UPDATE lead_notification_settings
                     SET max_chat_id=NULL,
                         max_enabled=(max_leadership_chat_id IS NOT NULL AND max_leadership_chat_id<>''),
                         updated_at=now()
                   WHERE account_id=:a
                """), {"a": account_id})
                db.commit()
                return {"status": "removed", "chat_id": chat_id, "route": "main"}
        return {"status": "ignored", "update_type": typ}
    finally:
        db.close()


def _record_delivery(outbox_id: int, channel: str, target: str, status: str, error: str = "") -> None:
    db = SessionLocal()
    try:
        db.execute(text("""
          INSERT INTO lead_notification_deliveries(outbox_id,channel,target_key,status,error)
          VALUES(:o,:c,:t,:s,:e)
          ON CONFLICT(outbox_id,channel,target_key) DO UPDATE SET status=excluded.status,error=excluded.error,created_at=now()
        """), {"o": outbox_id, "c": channel, "t": target[:240], "s": status, "e": error[:700] or None})
        db.commit()
    finally:
        db.close()


def _out_payload(out: dict) -> dict[str, Any]:
    raw = out.get("payload_json") or {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _send_webpush(out: dict, settings: dict) -> tuple[int, int]:
    if _out_payload(out).get("delivery_scope") == "max_only":
        return 0, 0
    if not settings.get("web_push_enabled"):
        return 0, 0
    keyfile = os.getenv("BORIS_WEBPUSH_VAPID_PRIVATE_KEY_FILE") or WEBPUSH_PRIVATE_KEY
    if not keyfile or not os.path.exists(keyfile):
        return 0, 0
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0, 0
    db = SessionLocal()
    try:
        rows = [dict(x) for x in db.execute(text("""
          SELECT s.id,s.endpoint_hash,s.subscription_enc
          FROM lead_push_subscriptions s
          JOIN users u ON u.id=s.user_id AND u.is_active=true AND coalesce(u.status,'active')<>'blocked'
          LEFT JOIN accounts a ON a.account_id=s.account_id
          WHERE s.account_id=:a AND s.active=true
            AND (
              u.role='owner'
              OR a.owner_user_id=u.id
              OR EXISTS (
                SELECT 1 FROM user_account_access ua
                WHERE ua.user_id=u.id AND ua.account_id=s.account_id AND ua.can_view=true
              )
            )
          ORDER BY s.id
        """), {"a": out["account_id"]}).mappings().all()]
    finally:
        db.close()
    ok = fail = 0
    wire = json.dumps({
        "title": out["title"], "body": out["body"], "url": out.get("url") or "/messages",
        "tag": "boris-lead-" + str(out["id"]), "event_type": out["event_type"],
        "severity": out["severity"],
    }, ensure_ascii=False)
    for sub in rows:
        target = str(sub["endpoint_hash"])[:16]
        try:
            info = json.loads(decrypt_secret(str(sub["subscription_enc"])))
            webpush(subscription_info=info, data=wire, vapid_private_key=keyfile, vapid_claims={"sub": WEBPUSH_SUBJECT}, timeout=10)
            _record_delivery(out["id"], "webpush", target, "sent")
            udb = SessionLocal()
            try:
                udb.execute(text("UPDATE lead_push_subscriptions SET last_success_at=now(),last_error=NULL,updated_at=now() WHERE id=:i"), {"i": sub["id"]}); udb.commit()
            finally: udb.close()
            ok += 1
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            err = f"{type(exc).__name__}:{status_code or ''}"
            _record_delivery(out["id"], "webpush", target, "failed", err)
            udb = SessionLocal()
            try:
                udb.execute(text("""
                  UPDATE lead_push_subscriptions SET active=CASE WHEN :gone THEN false ELSE active END,
                  last_error=:e,updated_at=now() WHERE id=:i
                """), {"gone": status_code in {404,410}, "e": err[:300], "i": sub["id"]}); udb.commit()
            finally: udb.close()
            fail += 1
    return ok, fail


def _send_max(out: dict, settings: dict) -> tuple[int, int]:
    if not settings.get("max_enabled"):
        return 0, 0
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT max_chat_id,max_leadership_chat_id,max_bot_token_enc FROM lead_notification_settings WHERE account_id=:a"), {"a": out["account_id"]}).mappings().first() or {}
    finally: db.close()
    payload = _out_payload(out)
    route = str(payload.get("max_channel") or "main").strip().lower()
    direct_chat_id = str(payload.get("max_chat_id") or "").strip()
    if route == "direct_chat" and direct_chat_id:
        chat_id = direct_chat_id
    elif route == "owner":
        odb = SessionLocal()
        try:
            owner_chat = odb.execute(text("SELECT chat_id FROM lead_notification_max_owner_routes WHERE account_id=:a"), {"a": out["account_id"]}).scalar()
        finally:
            odb.close()
        chat_id = str(owner_chat or "").strip()
    else:
        chat_id = str((row.get("max_leadership_chat_id") if route == "leadership" else row.get("max_chat_id")) or "").strip()
    token = decrypt_secret(str(row.get("max_bot_token_enc") or "")).strip()
    if not chat_id or not token:
        return 0, 0
    msg = f"{out['title']}\n\n{out['body']}"
    if out.get("url"):
        base = str(os.getenv("BORIS_PUBLIC_WEB_URL") or "https://boris-ai.pro").rstrip("/")
        msg += "\n\n" + base + str(out["url"])
    try:
        r = requests.post(
            MAX_API + "/messages",
            params={"chat_id": chat_id},
            headers={"Authorization": token, "Content-Type": "application/json"},
            json={"text": msg[:4000], "notify": True},
            timeout=10, verify=_max_verify(),
        )
        if 200 <= r.status_code < 300:
            _record_delivery(out["id"], "max", hashlib.sha256(chat_id.encode()).hexdigest()[:16], "sent")
            return 1, 0
        err = f"http_{r.status_code}"
    except Exception as exc:
        err = type(exc).__name__
    _record_delivery(out["id"], "max", hashlib.sha256(chat_id.encode()).hexdigest()[:16], "failed", err)
    return 0, 1


def _send_email(out: dict, settings: dict) -> tuple[int, int]:
    if _out_payload(out).get("delivery_scope") == "max_only":
        return 0, 0
    if not settings.get("email_enabled") or not settings.get("email_to"):
        return 0, 0
    mode = str(settings.get("email_mode") or "critical_only")
    if mode != "all" and out.get("severity") != "critical":
        return 0, 0
    from app.services.email_service import send_email
    ok, reason, _ = send_email(settings["email_to"], f"BORIS · {out['title']}", out["body"], tenant_scope=f"lead_notifications:{out.get('account_id') or settings.get('account_id') or 'system'}")
    target = hashlib.sha256(str(settings["email_to"]).encode()).hexdigest()[:16]
    _record_delivery(out["id"], "email", target, "sent" if ok else "failed", "" if ok else str(reason))
    return (1,0) if ok else (0,1)


def test_max_delivery(account_id: str) -> dict[str, Any]:
    """Send exactly one auditable MAX test without draining unrelated outbox rows."""
    diagnostics = max_status(account_id, live=True)
    if diagnostics.get("status") != "ready":
        return {"status": "not_ready", "diagnostics": diagnostics}

    queued = queue_event(
        account_id,
        "max:manual-test:" + str(account_id) + ":" + str(int(__import__("time").time() * 1000)),
        "max_test",
        "✅ BORIS ↔ MAX: тест связи",
        "Если вы видите это сообщение, BORIS успешно доставляет уведомления в эту MAX-группу.",
        severity="critical",
        url="/settings/notifications?account_id=" + str(account_id),
        payload={"max_test": True},
    )
    outbox_id = queued.get("id")
    if not outbox_id:
        return {"status": "queue_failed"}

    db = SessionLocal()
    try:
        out = dict(db.execute(text(
            "SELECT * FROM lead_notification_outbox WHERE id=:i"
        ), {"i": int(outbox_id)}).mappings().one())
        db.execute(text("""
          UPDATE lead_notification_outbox
             SET status='processing',attempts=attempts+1,updated_at=now()
           WHERE id=:i
        """), {"i": int(outbox_id)})
        db.commit()
    finally:
        db.close()

    settings = get_settings(account_id)
    sent, failed = _send_max(out, settings)
    db = SessionLocal()
    try:
        if sent > 0:
            db.execute(text("""
              UPDATE lead_notification_outbox
                 SET status='sent',sent_at=now(),last_error=NULL,next_attempt_at=NULL,updated_at=now()
               WHERE id=:i
            """), {"i": int(outbox_id)})
            result = "sent"
        else:
            db.execute(text("""
              UPDATE lead_notification_outbox
                 SET status='failed',last_error=:e,next_attempt_at=NULL,updated_at=now()
               WHERE id=:i
            """), {"i": int(outbox_id), "e": "max_test_failed:" + str(failed)})
            result = "failed"
        db.commit()
    finally:
        db.close()
    return {
        "status": result,
        "outbox_id": int(outbox_id),
        "sent": int(sent),
        "failed": int(failed),
        "diagnostics": max_status(account_id, live=True),
    }


def _notification_account_active(db, account_id: str, cache: dict[str,bool] | None = None) -> bool:
    """Best-effort paid/trial gate for noisy operational pushes.

    New/trial clients stay eligible via users.subscription_expires_at. Legacy
    paid clients stay eligible when billing has any future *_paid_until or a
    non-none tariff. Explicit tier=none with no future paid period is treated
    as inactive. Platform-owned accounts are always eligible.
    """
    aid=str(account_id or '').strip()
    if not aid: return False
    if cache is not None and aid in cache: return bool(cache[aid])
    row=db.execute(text("""
      SELECT coalesce(a.is_own,false) is_own,u.subscription_expires_at,
             (SELECT value FROM storage s WHERE s.account_id=a.account_id AND s.key='billing' ORDER BY s.id DESC LIMIT 1) billing
      FROM accounts a LEFT JOIN users u ON u.id=a.owner_user_id
      WHERE a.account_id=:a
    """),{'a':aid}).mappings().first()
    if not row:
        result=False
    elif bool(row.get('is_own')):
        result=True
    else:
        now=datetime.now(timezone.utc)
        trial=row.get('subscription_expires_at')
        if trial is not None and trial.tzinfo is None:
            trial=trial.replace(tzinfo=timezone.utc)
        result=bool(trial and trial>now)
        try:
            billing=json.loads(str(row.get('billing') or '{}'))
        except Exception:
            billing={}
        if isinstance(billing,dict):
            tier=str(billing.get('tier') or '').strip().lower()
            future_paid=False
            for k,v in billing.items():
                if not str(k).endswith('paid_until') or not v: continue
                try:
                    dt=datetime.fromisoformat(str(v).replace('Z','+00:00'))
                    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                    if dt>now: future_paid=True; break
                except Exception:
                    continue
            if future_paid or (tier and tier not in {'none','off','inactive'}):
                result=True
            elif tier in {'none','off','inactive'} and not future_paid and not result:
                result=False
    if cache is not None: cache[aid]=bool(result)
    return bool(result)



PLANETA_MAX_ACCOUNT_ID = os.getenv("BORIS_PLANETA_MAX_ACCOUNT_ID") or "planetazayavki_65985"


def _safe_json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _rub(value: Any) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ").replace(".00", "") + " ₽"
    except Exception:
        return "—"


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[float] = []
    weak = 0
    best_tip = ""
    weakest_score = 101.0
    for row in rows:
        a = _safe_json_obj(row.get("analysis"))
        score = a.get("score")
        if isinstance(score, (int, float)):
            score = float(score)
            scores.append(score)
            if score < 60:
                weak += 1
            if score < weakest_score:
                weakest_score = score
                best_tip = str(a.get("recommendation") or "").strip()
    return {
        "count": len(rows),
        "scored": len(scores),
        "avg": round(sum(scores) / len(scores), 1) if scores else None,
        "weak": weak,
        "tip": best_tip[:500],
    }


def _planeta_day_snapshot(db, day_local, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    account_id = PLANETA_MAX_ACCOUNT_ID
    snap_row = db.execute(text("""
      SELECT value FROM storage
      WHERE account_id=:a AND key=:k
      ORDER BY id DESC LIMIT 1
    """), {"a": account_id, "k": "daily_stats:" + day_local.isoformat()}).scalar()
    snap = _safe_json_obj(snap_row)
    items = snap.get("items") or []
    views = sum(int((x or {}).get("views") or 0) for x in items if isinstance(x, dict))
    contacts = sum(int((x or {}).get("contacts") or 0) for x in items if isinstance(x, dict))
    conversion = round(contacts / views * 100, 1) if views else 0.0
    balance = (snap.get("balance") or {}) if isinstance(snap.get("balance"), dict) else {}
    spending = (snap.get("spending") or {}) if isinstance(snap.get("spending"), dict) else {}
    ad_spend = float(spending.get("ad_spend_rub") or 0)
    all_spend = float(spending.get("all_spend_rub") or 0)
    cpl = round(ad_spend / contacts, 2) if contacts else None

    try:
        call_rows = [dict(x) for x in db.execute(text("""
          SELECT id,call_id,analysis,created_at FROM call_analysis
          WHERE account_id=:a AND created_at>=:s AND created_at<:e
          ORDER BY created_at DESC
        """), {"a": account_id, "s": start_utc, "e": end_utc}).mappings().all()]
    except Exception:
        call_rows = []
    try:
        chat_rows = [dict(x) for x in db.execute(text("""
          SELECT id,chat_id,analysis,created_at FROM chat_analysis
          WHERE account_id=:a AND created_at>=:s AND created_at<:e
          ORDER BY created_at DESC
        """), {"a": account_id, "s": start_utc, "e": end_utc}).mappings().all()]
    except Exception:
        chat_rows = []
    calls = _score_summary(call_rows)
    chats = _score_summary(chat_rows)

    try:
        react = dict(db.execute(text("""
          SELECT
            count(*) AS total,
            count(*) FILTER (WHERE status='replied' AND updated_at>=:s AND updated_at<:e) AS replied_today,
            count(*) FILTER (WHERE status='needs_review') AS needs_review,
            count(*) FILTER (WHERE status IN ('scheduled','approved')) AS scheduled,
            count(*) FILTER (WHERE phone_received=true AND updated_at>=:s AND updated_at<:e) AS phones_today,
            count(*) FILTER (WHERE purchase_confirmed=true AND updated_at>=:s AND updated_at<:e) AS purchases_today
          FROM reactivation_candidates WHERE account_id=:a
        """), {"a": account_id, "s": start_utc, "e": end_utc}).mappings().one())
    except Exception:
        react = {"total": 0, "replied_today": 0, "needs_review": 0, "scheduled": 0, "phones_today": 0, "purchases_today": 0}

    runtime_raw = db.execute(text("""
      SELECT value FROM storage WHERE account_id=:a AND key='virtual_marketer_runtime'
      ORDER BY id DESC LIMIT 1
    """), {"a": account_id}).scalar()
    runtime = _safe_json_obj(runtime_raw)
    log_raw = db.execute(text("""
      SELECT value FROM storage WHERE account_id=:a AND key='kpi_apply_log'
      ORDER BY id DESC LIMIT 1
    """), {"a": account_id}).scalar()
    try:
        action_log = json.loads(log_raw or "[]")
        if not isinstance(action_log, list):
            action_log = []
    except Exception:
        action_log = []

    from zoneinfo import ZoneInfo
    msk = ZoneInfo("Europe/Moscow")
    action_times: list[str] = []
    action_count = 0
    owner_funds = False
    for op in action_log:
        if not isinstance(op, dict):
            continue
        effect_status = str((op.get("effect") or {}).get("status") or "")
        if effect_status == "waiting_owner_funds":
            owner_funds = True
        ts = str(op.get("ts") or "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(msk)
            if local_dt.date() == day_local:
                action_count += 1
                action_times.append(local_dt.strftime("%H:%M"))
        except Exception:
            continue
    action_times = sorted(set(action_times))

    try:
        tasks = dict(db.execute(text("""
          SELECT
            count(*) FILTER (WHERE status='planned' AND due_date=:d) AS today,
            count(*) FILTER (WHERE status='planned' AND due_date<:d) AS overdue
          FROM crm_tasks WHERE account_id=:a
        """), {"a": account_id, "d": day_local}).mappings().one())
    except Exception:
        tasks = {"today": 0, "overdue": 0}

    return {
        "views": views,
        "contacts": contacts,
        "conversion": conversion,
        "wallet": balance.get("real"),
        "ad_spend": ad_spend,
        "all_spend": all_spend,
        "cpl": cpl,
        "calls": calls,
        "chats": chats,
        "react": react,
        "runtime": runtime,
        "marketer_actions": action_count,
        "marketer_times": action_times,
        "owner_funds": owner_funds,
        "tasks": tasks,
    }


def _queue_planeta_recent_rop(db, now_utc: datetime) -> int:
    """Near-real-time ROP score cards for Albina; old analyses are not replayed."""
    account_id = PLANETA_MAX_ACCOUNT_ID
    settings = db.execute(text("""
      SELECT max_enabled,max_chat_id,max_bot_token_enc
      FROM lead_notification_settings WHERE account_id=:a
    """), {"a": account_id}).mappings().first()
    if not settings or not settings.get("max_enabled") or not settings.get("max_chat_id") or not settings.get("max_bot_token_enc"):
        return 0
    queued = 0
    for kind, table, id_col, icon, label in (
        ("call", "call_analysis", "call_id", "📞", "звонок"),
        ("chat", "chat_analysis", "chat_id", "💬", "переписка"),
    ):
        try:
            rows = [dict(x) for x in db.execute(text(f"""
              SELECT id,{id_col} source_id,analysis,created_at
              FROM {table}
              WHERE account_id=:a AND created_at>=now()-interval '10 minutes'
              ORDER BY created_at DESC LIMIT 30
            """), {"a": account_id}).mappings().all()]
        except Exception:
            rows = []
        for row in rows:
            a = _safe_json_obj(row.get("analysis"))
            score = a.get("score")
            if not isinstance(score, (int, float)):
                continue
            score_int = int(round(float(score)))
            client = str(a.get("client_name") or "Клиент").strip()
            outcome = str(a.get("outcome") or "").strip()
            tip = str(a.get("recommendation") or "").strip()
            body = f"{client} · оценка {score_int}/100"
            if outcome:
                body += "\nИтог: " + outcome[:500]
            if tip:
                body += "\nПодсказка РОПа: " + tip[:700]
            result = queue_event(
                account_id,
                f"planeta:rop:{kind}:{row['id']}",
                f"rop_{kind}_score",
                f"{icon} РОП оценил {label}: {score_int}/100",
                body,
                severity="critical" if score_int < 60 else "normal",
                url=f"/rop?account_id={account_id}",
                payload={"max_channel": "main", "delivery_scope": "max_only", "score": score_int, "source_id": str(row.get("source_id") or "")},
            )
            queued += int(result.get("status") == "queued")
    return queued


def _queue_planeta_daily_max_reports(db, now_utc: datetime) -> int:
    """One evening Albina report + one leadership report, routed to two MAX chats."""
    from zoneinfo import ZoneInfo
    msk = ZoneInfo("Europe/Moscow")
    local_now = now_utc.astimezone(msk)
    if (local_now.hour, local_now.minute) < (19, 0):
        return 0
    day = local_now.date()
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=msk)
    end_local = start_local + __import__("datetime").timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    account_id = PLANETA_MAX_ACCOUNT_ID

    settings = db.execute(text("""
      SELECT max_enabled,max_chat_id,max_leadership_chat_id,max_bot_token_enc
      FROM lead_notification_settings WHERE account_id=:a
    """), {"a": account_id}).mappings().first()
    if not settings or not settings.get("max_enabled") or not settings.get("max_bot_token_enc"):
        return 0

    s = _planeta_day_snapshot(db, day, start_utc, end_utc)
    calls, chats, react, runtime, tasks = s["calls"], s["chats"], s["react"], s["runtime"], s["tasks"]
    avg_all_values = [x["avg"] for x in (calls, chats) if x.get("avg") is not None]
    avg_all = round(sum(avg_all_values) / len(avg_all_values), 1) if avg_all_values else None
    tips = [x.get("tip") for x in (calls, chats) if x.get("tip")]
    main_tip = tips[0] if tips else "Продолжать быстро обрабатывать новые обращения и закрыть просроченные задачи."

    queued = 0
    if settings.get("max_chat_id"):
        albina_body = (
            f"Обращения Avito: {s['contacts']} · просмотры: {s['views']} · конверсия: {s['conversion']}%\n"
            f"РОП: звонки {calls['count']} (ср. {calls['avg'] if calls['avg'] is not None else '—'}/100), "
            f"переписки {chats['count']} (ср. {chats['avg'] if chats['avg'] is not None else '—'}/100)\n"
            f"Низких оценок <60: {calls['weak'] + chats['weak']}\n"
            f"Реактивация: ответили сегодня {int(react.get('replied_today') or 0)}, ждут проверки {int(react.get('needs_review') or 0)}\n"
            f"Задачи: сегодня {int(tasks.get('today') or 0)}, просрочено {int(tasks.get('overdue') or 0)}\n"
            f"Подсказка РОПа: {main_tip[:700]}"
        )
        result = queue_event(
            account_id,
            f"planeta:daily:albina:{day.isoformat()}",
            "planeta_daily_albina",
            f"👩‍💼 Альбина · итог за {day.strftime('%d.%m')}",
            albina_body,
            severity="normal",
            url=f"/rop?account_id={account_id}",
            payload={"max_channel": "main", "delivery_scope": "max_only"},
        )
        queued += int(result.get("status") == "queued")

    if settings.get("max_leadership_chat_id"):
        times = ", ".join(s["marketer_times"][-8:]) if s["marketer_times"] else "—"
        advance_text = "⚠️ требуется пополнение аванса/баланса" if s["owner_funds"] else "отдельную сумму Avito API не отдаёт"
        cpl_text = _rub(s["cpl"]) if s["cpl"] is not None else "—"
        marketer_last = str(runtime.get("last_run_at") or "—")
        marketer_applied = int(runtime.get("applied") or 0)
        marketer_planned = int(runtime.get("planned") or 0)
        leadership_body = (
            f"💰 Кошелёк: {_rub(s['wallet'])}\n"
            f"Аванс: {advance_text}\n"
            f"Расход: реклама {_rub(s['ad_spend'])} · всего {_rub(s['all_spend'])}\n"
            f"📊 Просмотры {s['views']} · обращения {s['contacts']} · конверсия {s['conversion']}% · CPL {cpl_text}\n"
            f"🎧 РОП: звонки {calls['count']}, переписки {chats['count']}, средняя оценка {avg_all if avg_all is not None else '—'}/100, слабых {calls['weak'] + chats['weak']}\n"
            f"♻️ Реактивация: база {int(react.get('total') or 0)}, ответили сегодня {int(react.get('replied_today') or 0)}, ждут проверки {int(react.get('needs_review') or 0)}, телефонов {int(react.get('phones_today') or 0)}\n"
            f"🤖 Вирт. маркетолог: действий по журналу {s['marketer_actions']} · применено {marketer_applied} · запланировано {marketer_planned}\n"
            f"Время действий: {times}\n"
            f"Последний запуск: {marketer_last}"
        )
        result = queue_event(
            account_id,
            f"planeta:daily:leadership:{day.isoformat()}",
            "planeta_daily_leadership",
            f"📈 Планета Мебели · руководство · {day.strftime('%d.%m')}",
            leadership_body,
            severity="normal",
            url=f"/dashboard/command-center?account_id={account_id}",
            payload={"max_channel": "leadership", "delivery_scope": "max_only"},
        )
        queued += int(result.get("status") == "queued")
    return queued


def _metrika_api_snapshot(day_local) -> dict[str, Any]:
    token = str(os.getenv("YANDEX_METRIKA_TOKEN") or "").strip()
    if not token:
        return {"status": "token_required", "counter_id": YANDEX_METRIKA_COUNTER_ID}
    headers = {"Authorization": "OAuth " + token}
    params = {
        "ids": YANDEX_METRIKA_COUNTER_ID,
        "metrics": "ym:s:visits,ym:s:users,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
        "date1": day_local.isoformat(),
        "date2": day_local.isoformat(),
        "accuracy": "full",
    }
    try:
        r = requests.get("https://api-metrika.yandex.net/stat/v1/data", params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return {"status": "api_error", "http_status": int(r.status_code), "counter_id": YANDEX_METRIKA_COUNTER_ID}
        payload = r.json() or {}
        totals = list(payload.get("totals") or [])
        while len(totals) < 5:
            totals.append(0)
        out = {
            "status": "ok", "counter_id": YANDEX_METRIKA_COUNTER_ID,
            "visits": int(round(float(totals[0] or 0))),
            "users": int(round(float(totals[1] or 0))),
            "pageviews": int(round(float(totals[2] or 0))),
            "bounce_rate": round(float(totals[3] or 0), 1),
            "avg_seconds": int(round(float(totals[4] or 0))),
        }
        try:
            rr = requests.get(
                "https://api-metrika.yandex.net/stat/v1/data",
                params={
                    "ids": YANDEX_METRIKA_COUNTER_ID,
                    "metrics": "ym:s:visits",
                    "dimensions": "ym:s:lastTrafficSource",
                    "date1": day_local.isoformat(), "date2": day_local.isoformat(),
                    "sort": "-ym:s:visits", "limit": 5, "accuracy": "full",
                }, headers=headers, timeout=15,
            )
            if rr.status_code == 200:
                p2 = rr.json() or {}; src=[]
                for row in (p2.get("data") or [])[:5]:
                    dims=row.get("dimensions") or []; metrics=row.get("metrics") or []
                    name=str((dims[0] or {}).get("name") or "—") if dims else "—"
                    visits=int(round(float(metrics[0] or 0))) if metrics else 0
                    src.append({"name": name[:80], "visits": visits})
                out["sources"] = src
        except Exception:
            out["sources"] = []
        return out
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__, "counter_id": YANDEX_METRIKA_COUNTER_ID}


def _software_site_leads_for_day(day_local) -> int:
    path = "/root/BORIS/backend/data/software_site_leads.ndjson"
    try:
        count = 0
        tz = ZoneInfo("Europe/Moscow")
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    dt = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.astimezone(tz).date() == day_local:
                        count += 1
                except Exception:
                    continue
        return count
    except Exception:
        return 0


def _queue_owner_metrika_daily_max_report(db, now_utc: datetime) -> int:
    msk = ZoneInfo("Europe/Moscow")
    local_now = now_utc.astimezone(msk)
    if (local_now.hour, local_now.minute) < (19, 10):
        return 0
    day = local_now.date()
    owner_chat = db.execute(text(
        "SELECT chat_id FROM lead_notification_max_owner_routes WHERE account_id=:a"
    ), {"a": OWNER_MAX_TRANSPORT_ACCOUNT_ID}).scalar()
    if not owner_chat:
        return 0
    snap = _metrika_api_snapshot(day)
    if snap.get("status") != "ok":
        return 0
    avg = int(snap.get("avg_seconds") or 0)
    avg_text = f"{avg // 60}м {avg % 60}с" if avg >= 60 else f"{avg}с"
    sources = snap.get("sources") or []
    source_text = ", ".join(f"{x.get('name')}: {int(x.get('visits') or 0)}" for x in sources[:5]) or "—"
    site_leads = _software_site_leads_for_day(day)
    body = (
        f"Визиты: {int(snap.get('visits') or 0)}\n"
        f"Посетители: {int(snap.get('users') or 0)}\n"
        f"Просмотры страниц: {int(snap.get('pageviews') or 0)}\n"
        f"Отказы: {float(snap.get('bounce_rate') or 0):.1f}%\n"
        f"Среднее время: {avg_text}\n"
        f"Заявки с сайта разработки: {site_leads}\n"
        f"Источники трафика: {source_text}"
    )
    result = queue_event(
        OWNER_MAX_TRANSPORT_ACCOUNT_ID,
        f"owner:metrika:daily:{day.isoformat()}",
        "owner_metrika_daily",
        f"📊 Метрика · итог за {day.strftime('%d.%m')}",
        body,
        severity="normal",
        url="/software-dev/",
        payload={"max_channel": "owner", "delivery_scope": "max_only", "counter_id": YANDEX_METRIKA_COUNTER_ID},
    )
    return int(result.get("status") == "queued")


def queue_operational_notifications() -> dict[str, Any]:
    """Queue CRM/task/reactivation/callback reminders without external I/O.

    Idempotent event keys make repeated scans safe. Old backlog is summarized per
    account/day instead of exploding into hundreds of phone notifications.
    """
    ensure_schema()
    db = SessionLocal()
    queued = 0
    active_cache: dict[str,bool] = {}
    try:
        # 1) Mini-CRM tasks: today / due soon / overdue.
        rows = db.execute(text("""
          SELECT t.id,t.account_id,t.avito_chat_id,t.due_date,t.due_time,t.task_type,
                 t.title,t.comment,t.notify_before_min,a.name account_name
          FROM crm_tasks t
          JOIN accounts a ON a.account_id=t.account_id
          WHERE t.status='planned'
            AND t.due_date <= CURRENT_DATE + 1
          ORDER BY t.due_date,t.due_time NULLS LAST,t.id
          LIMIT 500
        """)).mappings().all()
        now = datetime.now(timezone.utc)
        today = now.date()
        for raw in rows:
            r = dict(raw)
            due_date = r.get("due_date")
            due_time = str(r.get("due_time") or "").strip()
            task_type = str(r.get("task_type") or "задача").strip()
            title = str(r.get("title") or task_type).strip()
            account_id = str(r.get("account_id") or "").strip()
            if not account_id or not due_date:
                continue
            if not _notification_account_active(db, account_id, active_cache):
                continue

            due_dt = None
            if due_time:
                try:
                    hh, mm = [int(x) for x in due_time[:5].split(":")]
                    due_dt = datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hh, minute=mm)
                except Exception:
                    due_dt = None

            event_kind = None
            event_title = None
            severity = "normal"
            event_key_suffix = None

            if due_date < today:
                event_kind = "task_overdue"
                event_title = "⚠️ Просрочена рабочая задача"
                severity = "critical"
                event_key_suffix = f"overdue:{due_date.isoformat()}:{due_time or '-'}"
            elif due_date == today:
                if due_dt is not None:
                    notify_before = max(0, int(r.get("notify_before_min") or 60))
                    if now >= due_dt - __import__("datetime").timedelta(minutes=notify_before):
                        if now > due_dt:
                            event_kind = "task_overdue"
                            event_title = "⚠️ Просрочена рабочая задача"
                            severity = "critical"
                            event_key_suffix = f"overdue:{due_date.isoformat()}:{due_time}"
                        else:
                            event_kind = "call_due" if task_type.lower() == "позвонить" else "task_due"
                            event_title = "📞 Скоро звонок" if event_kind == "call_due" else "⏰ Скоро задача"
                            severity = "critical" if event_kind == "call_due" else "normal"
                            event_key_suffix = f"due:{due_date.isoformat()}:{due_time}"
                else:
                    event_kind = "call_today" if task_type.lower() == "позвонить" else "task_today"
                    event_title = "📞 Сегодня звонок" if event_kind == "call_today" else "📌 Задача сегодня"
                    event_key_suffix = f"today:{due_date.isoformat()}"
            if not event_kind:
                continue
            when = (f" · {due_time}" if due_time else "")
            result = queue_event(
                account_id,
                f"crm-task:{r['id']}:{event_key_suffix}",
                event_kind,
                f"{event_title} · {r.get('account_name') or account_id}",
                f"{title}{when}",
                severity=severity,
                url=f"/messages?account_id={account_id}&chat_id={r.get('avito_chat_id') or ''}",
                payload={"task_id": int(r["id"]), "task_type": task_type},
            )
            queued += int(result.get("status") == "queued")

        # 2) Canonical BORIS CRM tasks: near deadline / recently overdue individually.
        canonical = db.execute(text("""
          SELECT t.id,t.deal_id,t.title,t.description,t.due_at,d.avito_account_id account_id,
                 coalesce(a.name,d.avito_account_id) account_name,c.display_name contact_name
          FROM boris_crm_tasks t
          JOIN boris_crm_deals d ON d.id=t.deal_id
          LEFT JOIN accounts a ON a.account_id=d.avito_account_id
          LEFT JOIN boris_crm_contacts c ON c.id=t.contact_id
          WHERE t.status='open'
            AND t.due_at IS NOT NULL
            AND d.avito_account_id IS NOT NULL
            AND t.due_at BETWEEN now()-interval '6 hours' AND now()+interval '1 hour'
          ORDER BY t.due_at,t.id
          LIMIT 500
        """)).mappings().all()
        for raw in canonical:
            r = dict(raw)
            due = r.get("due_at")
            account_id = str(r.get("account_id") or "")
            if not due or not account_id:
                continue
            if not _notification_account_active(db, account_id, active_cache):
                continue
            overdue = due < now
            event_type = "crm_task_overdue" if overdue else "crm_task_due"
            title = "⚠️ CRM-задача просрочена" if overdue else "⏰ CRM-задача скоро"
            body = str(r.get("title") or "CRM-задача")
            if r.get("contact_name"):
                body += f" · {r['contact_name']}"
            result = queue_event(
                account_id,
                f"boris-crm-task:{r['id']}:{int(due.timestamp())}:{event_type}",
                event_type,
                f"{title} · {r.get('account_name') or account_id}",
                body,
                severity="critical" if overdue else "normal",
                url=f"/dashboard/crm/deal/{r.get('deal_id')}",
                payload={"task_id": int(r["id"]), "deal_id": r.get("deal_id")},
            )
            queued += int(result.get("status") == "queued")

        # 3) Old CRM backlog: one daily digest per account, never one push per old task.
        backlog = db.execute(text("""
          SELECT d.avito_account_id account_id,coalesce(a.name,d.avito_account_id) account_name,count(*) n
          FROM boris_crm_tasks t
          JOIN boris_crm_deals d ON d.id=t.deal_id
          LEFT JOIN accounts a ON a.account_id=d.avito_account_id
          WHERE t.status='open' AND t.due_at<now()-interval '6 hours' AND d.avito_account_id IS NOT NULL
          GROUP BY d.avito_account_id,a.name
          HAVING count(*)>0
        """)).mappings().all()
        day_key = today.isoformat()
        for raw in backlog:
            r = dict(raw); account_id = str(r.get("account_id") or "")
            if not _notification_account_active(db, account_id, active_cache):
                continue
            result = queue_event(
                account_id,
                f"crm-backlog:{account_id}:{day_key}",
                "crm_backlog",
                f"📋 Просроченные задачи CRM · {r.get('account_name') or account_id}",
                f"В работе осталось просроченных задач: {int(r.get('n') or 0)}. BORIS собрал их одной сводкой, без спама.",
                severity="normal",
                url=f"/dashboard/crm?account_id={account_id}",
                payload={"count": int(r.get("n") or 0)},
            )
            queued += int(result.get("status") == "queued")

        # 4) Reactivation: replies are hot; review/scheduled are normal work.
        react = db.execute(text("""
          SELECT r.id,r.account_id,r.avito_chat_id,r.status,r.outcome,r.summary,r.updated_at,
                 r.recommended_contact_at,coalesce(a.name,r.account_id) account_name
          FROM reactivation_candidates r
          LEFT JOIN accounts a ON a.account_id=r.account_id
          WHERE (
              (r.status='replied' AND r.updated_at>=now()-interval '24 hours')
           OR (r.status='needs_review' AND r.updated_at>=now()-interval '6 hours')
           OR (r.status IN ('scheduled','approved') AND r.recommended_contact_at IS NOT NULL
               AND r.recommended_contact_at<=now()+interval '1 hour')
          )
          ORDER BY r.updated_at DESC
          LIMIT 300
        """)).mappings().all()
        for raw in react:
            r = dict(raw); account_id = str(r.get("account_id") or "")
            if not _notification_account_active(db, account_id, active_cache):
                continue
            status = str(r.get("status") or "")
            if status == "replied":
                et, ttl, sev = "reactivation_reply", "🔥 Ответ на реактивацию", "critical"
            elif status == "needs_review":
                et, ttl, sev = "reactivation_review", "♻️ Реактивация: нужна проверка", "normal"
            else:
                et, ttl, sev = "reactivation_due", "♻️ Пора обработать реактивацию", "normal"
            stamp = int((r.get("updated_at") or now).timestamp())
            body = str(r.get("summary") or r.get("outcome") or "Откройте карточку клиента.")[:1000]
            result = queue_event(
                account_id,
                f"reactivation:{r['id']}:{status}:{stamp}",
                et,
                f"{ttl} · {r.get('account_name') or account_id}",
                body,
                severity=sev,
                url=f"/reactivation?account_id={account_id}&chat_id={r.get('avito_chat_id') or ''}",
                payload={"candidate_id": int(r["id"]), "status": status},
            )
            queued += int(result.get("status") == "queued")

        # 5) Telephony callbacks: when BORIS Phone has a callback deadline.
        try:
            calls = db.execute(text("""
              SELECT c.id,c.account_id,c.callback_due_at,c.callback_status,
                     coalesce(a.name,c.account_id) account_name
              FROM telephony_calls c
              LEFT JOIN accounts a ON a.account_id=c.account_id
              WHERE c.callback_status='required' AND c.callback_due_at IS NOT NULL
                AND c.callback_due_at BETWEEN now()-interval '6 hours' AND now()+interval '1 hour'
              ORDER BY c.callback_due_at
              LIMIT 300
            """)).mappings().all()
        except Exception:
            calls = []
        for raw in calls:
            r = dict(raw); due = r.get("callback_due_at"); account_id = str(r.get("account_id") or "")
            if not due or not account_id:
                continue
            if not _notification_account_active(db, account_id, active_cache):
                continue
            overdue = due < now
            result = queue_event(
                account_id,
                f"phone-callback:{r['id']}:{int(due.timestamp())}:{'late' if overdue else 'due'}",
                "callback_overdue" if overdue else "callback_due",
                ("⚠️ Просрочен обратный звонок" if overdue else "📞 Пора перезвонить") + f" · {r.get('account_name') or account_id}",
                "Откройте BORIS Phone и свяжитесь с клиентом.",
                severity="critical",
                url=f"/phone-app?account_id={account_id}",
                payload={"call_id": str(r["id"])},
            )
            queued += int(result.get("status") == "queued")

        # 6) Планета Мебели: оценки РОПа сразу в рабочий чат Альбины.
        try:
            queued += _queue_planeta_recent_rop(db, now)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1705)

        # 7) Планета Мебели: один вечерний отчёт Альбине и один руководству.
        try:
            queued += _queue_planeta_daily_max_reports(db, now)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1711)

        # 8) Владелец: один ежедневный отчёт Яндекс Метрики в MAX.
        try:
            queued += _queue_owner_metrika_daily_max_report(db, now)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1717)

        # 9) Команды MAX: повторные напоминания из owner/client чатов.
        try:
            from app.services.max_boris_control import queue_due_reminders
            queued += int((queue_due_reminders(now) or {}).get("queued") or 0)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1724)

        return {"status": "ok", "queued": queued}
    finally:
        db.close()


def dispatch_pending(limit: int = 20) -> dict[str, Any]:
    ensure_schema()
    # Worker crash/restart must not strand a claimed notification forever.
    rdb = SessionLocal()
    try:
        rdb.execute(text("""
          UPDATE lead_notification_outbox SET status='retry',next_attempt_at=now(),
                 last_error=COALESCE(last_error,'stale processing recovered'),updated_at=now()
          WHERE status='processing' AND updated_at<now()-interval '2 minutes'
        """))
        rdb.commit()
    finally:
        rdb.close()
    processed = sent = failed = waiting = 0
    for _ in range(max(1, min(int(limit or 20), 100))):
        db = SessionLocal()
        try:
            row = db.execute(text("""
              SELECT * FROM lead_notification_outbox
              WHERE status IN ('pending','retry')
                AND (next_attempt_at IS NULL OR next_attempt_at<=now())
              ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
            """)).mappings().first()
            if not row:
                db.rollback(); break
            out = dict(row)
            db.execute(text("UPDATE lead_notification_outbox SET status='processing',attempts=attempts+1,updated_at=now() WHERE id=:i"), {"i": out["id"]})
            db.commit()
        finally:
            db.close()
        processed += 1
        settings = get_settings(str(out["account_id"]))
        oks = fails = 0
        for fn in (_send_webpush, _send_max, _send_email):
            o, f = fn(out, settings); oks += o; fails += f
        payload = _out_payload(out)
        max_route = str(payload.get("max_channel") or "main").strip().lower()
        direct_chat_id = str(payload.get("max_chat_id") or "").strip()
        if max_route == "direct_chat" and direct_chat_id:
            max_target = direct_chat_id
        elif max_route == "owner":
            tdb = SessionLocal()
            try:
                max_target = tdb.execute(text(
                    "SELECT chat_id FROM lead_notification_max_owner_routes WHERE account_id=:a"
                ), {"a": str(out["account_id"])}).scalar()
            finally:
                tdb.close()
        else:
            max_target = settings.get("max_leadership_chat_id") if max_route == "leadership" else settings.get("max_chat_id")
        max_only = payload.get("delivery_scope") == "max_only"
        configured = bool(
            ((not max_only) and settings.get("web_push_enabled") and settings.get("active_push_subscriptions"))
            or (settings.get("max_enabled") and max_target and settings.get("max_token_configured"))
            or ((not max_only) and settings.get("email_enabled") and settings.get("email_to") and (settings.get("email_mode")=="all" or out.get("severity")=="critical"))
        )
        udb = SessionLocal()
        try:
            if oks > 0:
                udb.execute(text("UPDATE lead_notification_outbox SET status='sent',sent_at=now(),last_error=NULL,next_attempt_at=NULL,updated_at=now() WHERE id=:i"), {"i": out["id"]})
                sent += 1
            elif configured and int(out.get("attempts") or 0) < 4:
                udb.execute(text("UPDATE lead_notification_outbox SET status='retry',last_error=:e,next_attempt_at=now()+make_interval(secs=>LEAST(300,10*(2^LEAST(attempts,5)::int))),updated_at=now() WHERE id=:i"), {"i": out["id"], "e": f"delivery_failed:{fails}"})
                failed += 1
            elif configured:
                udb.execute(text("UPDATE lead_notification_outbox SET status='failed',last_error=:e,next_attempt_at=NULL,updated_at=now() WHERE id=:i"), {"i": out["id"], "e": f"delivery_failed:{fails}"})
                failed += 1
            else:
                udb.execute(text("UPDATE lead_notification_outbox SET status='waiting_configuration',last_error='no notification channel configured',next_attempt_at=NULL,updated_at=now() WHERE id=:i"), {"i": out["id"]})
                waiting += 1
            udb.commit()
        finally: udb.close()
    return {"status": "ok", "processed": processed, "sent": sent, "failed": failed, "waiting_configuration": waiting}
