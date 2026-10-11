# -*- coding: utf-8 -*-
"""Owner feedback loop for live MOP inside BORIS Messenger."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api.auth import get_current_user
from app.api.legal import require_messenger_legal_user
from app.api.internal_messenger import _access
from app.api.messenger import get_manager_balance, _mop_client_visible_text
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/mop-control", tags=["mop-control"], dependencies=[Depends(require_messenger_legal_user)])

SETTINGS_KEY = "mop_control_settings"
FEEDBACK_PREFIX = "mop_control_feedback:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_value(db, account_id: str, key: str) -> dict:
    raw = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
    ), {"a": account_id, "k": key}).scalar()
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_json_value(db, account_id: str, key: str, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).order_by(Storage.id.desc()).first()
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=account_id, key=key, value=raw))


def _entitlement(account_id: str) -> dict:
    bal = get_manager_balance(account_id)
    return {
        "active": bool(bal.get("active")),
        "purchased": int(bal.get("purchased") or 0),
        "used": int(bal.get("used") or 0),
        "left": int(bal.get("left") or 0),
        "expired": bool(bal.get("expired")),
        "days_left": int(bal.get("days_left") or 0),
        "unlimited": bool(bal.get("unlimited")),
    }


def _settings(db, account_id: str) -> dict:
    raw = _load_json_value(db, account_id, SETTINGS_KEY)
    mode = str(raw.get("review_mode") or "all")
    if mode not in {"all", "smart"}:
        mode = "all"
    return {
        "review_mode": mode,
        "manual_message_alerts": bool(raw.get("manual_message_alerts", True)),
        "updated_at": raw.get("updated_at"),
    }


def _smart_review(incoming: str, reply: str, summary: str, draft_id: int) -> bool:
    low = " ".join((str(incoming or "") + " " + str(reply or "") + " " + str(summary or "")).lower().replace("ё", "е").split())
    markers = (
        "цена", "стоимост", "сколько", "налич", "скид", "акци", "срок",
        "достав", "телефон", "номер", "менеджер", "оператор", "человек",
        "жалоб", "претенз", "возврат", "handoff", "price", "availability",
    )
    return len(str(reply or "")) > 220 or any(x in low for x in markers) or int(draft_id) % 10 == 0


def _manual_disclosure_alerts(db, account_id: str, limit: int = 10) -> list[dict]:
    rows = db.execute(text(
        "SELECT mm.avito_chat_id,mm.avito_message_id,mm.text,mm.avito_created_at "
        "FROM messenger_messages mm "
        "WHERE mm.account_id=:a AND lower(coalesce(mm.direction,'')) IN ('out','outgoing') "
        "AND mm.avito_created_at>=extract(epoch from now()-interval '7 days')::bigint "
        "AND (lower(mm.text) LIKE '%это ии%' OR lower(mm.text) LIKE '%вам отвечает ии%' "
        "OR lower(mm.text) LIKE '%я бот%' OR lower(mm.text) LIKE '%бот отвечает%' "
        "OR lower(mm.text) LIKE '%нейросет%' OR lower(mm.text) LIKE '%искусственн%интеллект%') "
        "AND NOT EXISTS (SELECT 1 FROM mop_drafts d WHERE d.account_id=mm.account_id "
        "AND d.avito_chat_id=mm.avito_chat_id AND btrim(coalesce(d.reply_text,''))=btrim(coalesce(mm.text,''))) "
        "ORDER BY mm.avito_created_at DESC,mm.id DESC LIMIT :n"
    ), {"a": account_id, "n": int(limit)}).mappings().all()
    out=[]
    for r in rows:
        ts=int(r["avito_created_at"] or 0)
        created=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
        out.append({
            "chat_id": str(r["avito_chat_id"] or ""),
            "message_id": str(r["avito_message_id"] or ""),
            "text": str(r["text"] or ""),
            "created_at": created,
            "source": "manual_avito_outgoing",
        })
    return out


def _feedback_map(db, account_id: str, draft_ids: list[int]) -> dict[int, dict]:
    if not draft_ids:
        return {}
    rows = db.execute(text(
        "SELECT key,value FROM storage WHERE account_id=:a "
        "AND key LIKE :prefix ORDER BY id DESC"
    ), {"a": account_id, "prefix": FEEDBACK_PREFIX + "%"}).fetchall()
    out: dict[int, dict] = {}
    allowed = set(int(x) for x in draft_ids)
    for key, raw in rows:
        try:
            did = int(str(key).split(":")[-1])
        except Exception:
            continue
        if did not in allowed or did in out:
            continue
        try:
            data = json.loads(str(raw or "{}"))
            if isinstance(data, dict):
                out[did] = data
        except Exception:
            continue
    return out


class FeedbackBody(BaseModel):
    account_id: str
    draft_id: int
    action: Literal["approve", "correct", "reject", "handoff"]
    correction: str = Field(default="", max_length=1200)
    comment: str = Field(default="", max_length=1200)


class SettingsBody(BaseModel):
    account_id: str
    review_mode: Literal["all", "smart"] = "all"
    manual_message_alerts: bool = True


@router.get("/status")
def status(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        ent = _entitlement(account_id)
        cfg = _settings(db, account_id)
        pending = 0
        manual_alerts_count = 0
        if ent["active"]:
            pending = int(db.execute(text(
                "SELECT count(*) FROM mop_drafts d "
                "WHERE d.account_id=:a AND d.sent_at IS NOT NULL AND d.reply_author='ai' "
                "AND coalesce(d.reply_text,'')<>'' "
                "AND coalesce(d.sent_at,d.updated_at,d.created_at)>=now()-interval '7 days' "
                "AND NOT EXISTS (SELECT 1 FROM storage s WHERE s.account_id=:a "
                "AND s.key=('mop_control_feedback:' || d.id::text))"
            ), {"a": account_id}).scalar() or 0)
            if cfg.get("manual_message_alerts"):
                manual_alerts_count = len(_manual_disclosure_alerts(db, account_id, limit=20))
        return {
            "status": "ok",
            "account_id": account_id,
            "visible": True,
            "active": ent["active"],
            "state": "active" if ent["active"] else "not_activated",
            "entitlement": ent,
            "settings": cfg,
            "pending_reviews": pending,
            "manual_alerts_count": manual_alerts_count,
        }
    finally:
        db.close()


@router.get("/feed")
def feed(
    account_id: str,
    limit: int = Query(default=40, ge=1, le=100),
    user=Depends(get_current_user),
):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        ent = _entitlement(account_id)
        if not ent["active"]:
            return {"status": "ok", "account_id": account_id, "active": False, "items": [], "manual_alerts": []}
        cfg = _settings(db, account_id)
        fetch_limit = min(100, int(limit) * 3 if cfg.get("review_mode") == "smart" else int(limit))
        rows = db.execute(text(
            "SELECT d.id,d.avito_chat_id,d.avito_message_id,d.incoming_text,d.reply_text,"
            "d.sent_at,d.created_at,d.ai_summary,"
            "(SELECT mm.item_title FROM messenger_messages mm "
            " WHERE mm.account_id=d.account_id AND mm.avito_chat_id=d.avito_chat_id "
            " AND coalesce(mm.item_title,'')<>'' ORDER BY mm.avito_created_at DESC,mm.id DESC LIMIT 1) item_title "
            "FROM mop_drafts d WHERE d.account_id=:a AND d.sent_at IS NOT NULL "
            "AND d.reply_author='ai' AND coalesce(d.reply_text,'')<>'' "
            "AND coalesce(d.sent_at,d.updated_at,d.created_at)>=now()-interval '7 days' "
            "ORDER BY coalesce(d.sent_at,d.updated_at,d.created_at) DESC,d.id DESC LIMIT :n"
        ), {"a": account_id, "n": int(fetch_limit)}).mappings().all()
        ids = [int(r["id"]) for r in rows]
        feedback = _feedback_map(db, account_id, ids)
        items = []
        for r in rows:
            clean, _ = _mop_client_visible_text(str(r["reply_text"] or ""))
            fb = feedback.get(int(r["id"]))
            reply_text = clean or str(r["reply_text"] or "")
            if cfg.get("review_mode") == "smart" and not fb and not _smart_review(
                str(r["incoming_text"] or ""), reply_text, str(r["ai_summary"] or ""), int(r["id"])
            ):
                continue
            items.append({
                "draft_id": int(r["id"]),
                "chat_id": str(r["avito_chat_id"] or ""),
                "incoming": str(r["incoming_text"] or ""),
                "reply": reply_text,
                "item_title": str(r["item_title"] or ""),
                "sent_at": (r["sent_at"] or r["created_at"]).isoformat() if (r["sent_at"] or r["created_at"]) else None,
                "reviewed": bool(fb),
                "feedback": fb,
            })
        items = items[:int(limit)]
        manual_alerts = _manual_disclosure_alerts(db, account_id, limit=10) if cfg.get("manual_message_alerts") else []
        return {
            "status": "ok", "account_id": account_id, "active": True,
            "review_mode": cfg.get("review_mode"), "items": items,
            "manual_alerts": manual_alerts,
        }
    finally:
        db.close()


@router.post("/feedback")
def feedback(body: FeedbackBody, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, body.account_id, "can_reply"):
            raise HTTPException(status_code=403, detail="Нет права управлять МОП")
        ent = _entitlement(body.account_id)
        if not ent["active"]:
            raise HTTPException(status_code=402, detail="Модуль Контроль МОП не активирован")
        draft = db.execute(text(
            "SELECT id,incoming_text,reply_text,avito_chat_id FROM mop_drafts "
            "WHERE id=:i AND account_id=:a LIMIT 1"
        ), {"i": int(body.draft_id), "a": body.account_id}).mappings().first()
        if not draft:
            raise HTTPException(status_code=404, detail="Ответ МОП не найден")
        clean_reply, _ = _mop_client_visible_text(str(draft["reply_text"] or ""))
        correction = " ".join(str(body.correction or "").split()).strip()
        comment = " ".join(str(body.comment or "").split()).strip()
        if body.action == "correct" and not correction:
            raise HTTPException(status_code=400, detail="Введите правильный вариант ответа")
        if body.action == "reject" and not comment:
            raise HTTPException(status_code=400, detail="Напишите, что именно было неверно")
        item = {
            "draft_id": int(body.draft_id),
            "action": body.action,
            "incoming": str(draft["incoming_text"] or "")[:1200],
            "reply": clean_reply[:1200],
            "correction": correction[:1200],
            "comment": comment[:1200],
            "owner_user_id": int(getattr(user, "id", 0) or 0),
            "created_at": _now_iso(),
            "policy_version": "MOP_OWNER_FEEDBACK_V1",
        }
        _save_json_value(db, body.account_id, FEEDBACK_PREFIX + str(int(body.draft_id)), item)
        db.commit()
        return {"status": "ok", "feedback": item}
    finally:
        db.close()


@router.get("/memory")
def memory(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        profile = db.execute(text(
            "SELECT payload FROM mop_account_dialogue_brain WHERE account_id=:a LIMIT 1"
        ), {"a": account_id}).scalar()
        profile = profile if isinstance(profile, dict) else {}
        style = profile.get("style_profile") or {}
        rows = db.execute(text(
            "SELECT value FROM storage WHERE account_id=:a AND key LIKE :p ORDER BY id DESC LIMIT 30"
        ), {"a": account_id, "p": FEEDBACK_PREFIX + "%"}).fetchall()
        recent = []
        for (raw,) in rows:
            try:
                x = json.loads(str(raw or "{}"))
                if isinstance(x, dict):
                    recent.append(x)
            except Exception as _feedback_decode_exc:
                observe_suppressed(__name__, _feedback_decode_exc, line=307)
        return {
            "status": "ok",
            "account_id": account_id,
            "style": style,
            "recent_feedback": recent[:20],
        }
    finally:
        db.close()


@router.post("/settings")
def update_settings(body: SettingsBody, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, body.account_id, "can_reply"):
            raise HTTPException(status_code=403, detail="Нет права управлять модулем")
        ent = _entitlement(body.account_id)
        if not ent["active"]:
            raise HTTPException(status_code=402, detail="Модуль Контроль МОП не активирован")
        data = {
            "review_mode": body.review_mode,
            "manual_message_alerts": bool(body.manual_message_alerts),
            "updated_at": _now_iso(),
            "updated_by_user_id": int(getattr(user, "id", 0) or 0),
        }
        _save_json_value(db, body.account_id, SETTINGS_KEY, data)
        db.commit()
        return {"status": "ok", "settings": data}
    finally:
        db.close()
