"""Tenant-scoped employee Messenger API.

This router deliberately reuses users.user_account_access and team roles. It is
separate from the customer Inbox/Avito state machine and never sends outbound
provider messages.
"""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import List, Literal, Optional
from uuid import UUID, uuid4
from app.services import messenger_voice as voice_service
from app.services import messenger_video as video_service
from app.services import messenger_calls as calls_service
from app.services import messenger_telemetry as telemetry_service
from app.services import messenger_idempotency

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.auth import get_current_user
from app.api.legal import require_messenger_legal_user
from app.db.session import SessionLocal
from app.services import lead_notifications as notification_service
from app.services.messenger_rate_limit import enforce as enforce_messenger_rate
from app.services.messenger_security_audit import append_security_audit
from app.services.platform_roles import PLATFORM_OWNER_ID, is_private_platform_owner

router = APIRouter(prefix="/api/internal-messenger", tags=["internal-messenger"], dependencies=[Depends(require_messenger_legal_user)])
logger = logging.getLogger(__name__)
ATTACHMENT_ROOT = Path("/root/BORIS/data/internal-messenger-attachments")
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
GENERAL_CHAT_TITLE = "Общий чат BORIS"
ATTACHMENT_ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif",
    "application/pdf", "text/plain", "text/csv", "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword", "application/vnd.ms-excel",
}
ATTACHMENT_DENIED_EXTENSIONS = {
    ".exe", ".msi", ".com", ".scr", ".bat", ".cmd", ".ps1", ".psm1",
    ".sh", ".bash", ".zsh", ".fish", ".js", ".mjs", ".cjs", ".jar",
    ".apk", ".dmg", ".pkg", ".app", ".hta", ".vbs", ".vbe", ".wsf",
    ".html", ".htm", ".xhtml", ".svg",
}


def _validate_attachment_magic(content_type: str, file_name: str, prefix: bytes) -> None:
    """Fail closed when browser MIME/extension disagree with file content."""
    ctype = str(content_type or "").lower().strip()
    ext = Path(str(file_name or "")).suffix.lower()
    head = bytes(prefix or b"")[:4096]
    low = head.lstrip().lower()
    if ext in ATTACHMENT_DENIED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Исполняемые и активные файлы запрещены")
    if ctype not in ATTACHMENT_ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Этот тип файла не поддерживается")
    if any(low.startswith(marker) for marker in (b"<html", b"<!doctype html", b"<script", b"<svg", b"<?xml")):
        raise HTTPException(status_code=415, detail="Активное web-содержимое запрещено")

    valid = False
    if ctype == "image/jpeg":
        valid = head.startswith(b"\xff\xd8\xff")
    elif ctype == "image/png":
        valid = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif ctype == "image/gif":
        valid = head.startswith((b"GIF87a", b"GIF89a"))
    elif ctype == "image/webp":
        valid = len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    elif ctype in {"image/heic", "image/heif"}:
        valid = len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}
    elif ctype == "application/pdf":
        valid = head.startswith(b"%PDF-")
    elif ctype in {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        valid = head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    elif ctype in {"application/msword", "application/vnd.ms-excel"}:
        valid = head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif ctype in {"text/plain", "text/csv"}:
        valid = b"\x00" not in head
        if valid:
            try:
                head.decode("utf-8")
            except UnicodeDecodeError:
                valid = False
    if not valid:
        raise HTTPException(status_code=415, detail="Содержимое файла не соответствует заявленному типу")


def _access(db, user, account_id: str, permission: str = "can_view") -> bool:
    # Cross-tenant access stays fail-closed for ordinary account owners/managers.
    # The single strict private BORIS platform owner is an explicit support/admin
    # principal and may enter a selected tenant Messenger workspace. Never use
    # role="owner" alone for this exception.
    if is_private_platform_owner(user):
        return bool(db.execute(text(
            "SELECT 1 FROM accounts WHERE account_id=:a LIMIT 1"
        ), {"a": account_id}).first())
    column = permission if permission in {"can_view", "can_reply", "can_manage_team"} else "can_view"
    row = db.execute(text(
        f"SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u "
        f"UNION SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u AND {column}=TRUE LIMIT 1"),
        {"a": account_id, "u": getattr(user, "id", None)}).first()
    return bool(row)


def _member(db, conversation_id: int, user_id: int):
    return db.execute(text(
        "SELECT c.id,c.account_id,c.kind,c.title,c.created_by_user_id,c.updated_at,m.role,m.last_read_message_id "
        "FROM internal_conversations c JOIN internal_conversation_members m ON m.conversation_id=c.id "
        "WHERE c.id=:c AND m.user_id=:u"), {"c": conversation_id, "u": user_id}).mappings().first()


def _require_member(db, conversation_id: int, user):
    row = _member(db, conversation_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if not _access(db, user, row["account_id"]):
        raise HTTPException(status_code=403, detail="Чат недоступен для этого аккаунта")
    return row


def _require_reply(db, user, account_id: str):
    if not _access(db, user, account_id, "can_reply"):
        raise HTTPException(status_code=403, detail="Роль только для просмотра: отправка и изменения недоступны")


def _require_team_manage(db, user, account_id: str):
    if not _access(db, user, account_id, "can_manage_team"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для управления рабочими группами")


def _iso(value):
    return value.isoformat() if value else None


def emit_messenger_event(db, account_id: str, event_type: str, entity_type: str, entity_id=None, payload=None) -> int:
    row = db.execute(text(
        "INSERT INTO internal_messenger_events(account_id,event_type,entity_type,entity_id,payload_json) "
        "VALUES (:a,:t,:et,:eid,CAST(:p AS JSONB)) RETURNING id"
    ), {
        "a": account_id,
        "t": str(event_type)[:96],
        "et": str(entity_type)[:64],
        "eid": str(entity_id)[:128] if entity_id is not None else None,
        "p": json.dumps(payload or {}, ensure_ascii=False, default=str),
    }).first()
    return int(row[0])



def _event_for_memberships(event: dict, conversation_ids: set[int]) -> dict | None:
    """An inaccessible private event must not exist in the caller's feed."""
    event_type = str(event.get("type") or "")
    if not event_type.startswith("internal."):
        # These account-wide invalidations need no entity or private payload.
        if event_type in {"team.changed", "crm.task.changed", "crm.stage.changed",
                          "client.assignment.changed", "client.ai_mode.changed",
                          "client.message.changed"}:
            return {**event, "entity_type": "account", "entity_id": None, "payload": {}}
        return None
    payload = event.get("payload") or {}
    raw = payload.get("conversation_id")
    if raw is None and event.get("entity_type") == "conversation":
        raw = event.get("entity_id")
    try:
        allowed = int(raw) in conversation_ids
    except (TypeError, ValueError):
        allowed = False
    return event if allowed else None


def _stream_session_active(db, user) -> bool:
    from app.services.auth_sessions import validate_session
    row = db.execute(text("SELECT security_epoch,is_active,status FROM users WHERE id=:u"),
                     {"u": user.id}).mappings().first()
    if not row or not row["is_active"] or row["status"] == "blocked":
        return False
    sid = str(getattr(user, "_auth_session_id", "") or "")
    return bool(sid) and validate_session(
        db, user_id=user.id, session_id=sid,
        token_epoch=int(getattr(user, "_auth_security_epoch", 0) or 0),
        current_epoch=int(row["security_epoch"] or 0))


def _messenger_event_feed(db, account_id: str, after_id: int, limit: int = 100, *, user_id: int):
    # Membership is checked before LIMIT, for polling and every SSE replay.
    # Resolve message identity from the canonical row, never a claimed payload.
    rows = db.execute(text(
        "SELECT e.id,e.event_type,e.entity_type,e.entity_id,e.payload_json,e.created_at "
        "FROM internal_messenger_events e WHERE e.account_id=:a AND e.id>:after AND ("
        "e.event_type IN ('team.changed','crm.task.changed','crm.stage.changed',"
        "'client.assignment.changed','client.ai_mode.changed','client.message.changed') "
        "OR (e.event_type LIKE 'internal.%' AND EXISTS ("
        "SELECT 1 FROM internal_conversation_members m "
        "JOIN internal_conversations c ON c.id=m.conversation_id "
        "WHERE m.user_id=:u AND c.account_id=e.account_id AND ("
        "(e.entity_type='conversation' AND e.entity_id=c.id::text) OR "
        "(e.entity_type='message' AND EXISTS (SELECT 1 FROM internal_messages im "
        "WHERE im.id::text=e.entity_id AND im.conversation_id=c.id))"
        ")))) ORDER BY e.id ASC LIMIT :limit"
    ), {"a": account_id, "u": user_id, "after": max(0, int(after_id)), "limit": max(1, min(int(limit), 200))}).mappings().all()
    conversation_ids = set(db.execute(text("SELECT m.conversation_id FROM internal_conversation_members m JOIN internal_conversations c ON c.id=m.conversation_id WHERE m.user_id=:u AND c.account_id=:a"), {"u": user_id, "a": account_id}).scalars().all())
    events = [{
        "id": int(row["id"]),
        "type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "payload": row["payload_json"] or {},
        "created_at": _iso(row["created_at"]),
    } for row in rows]
    return [visible for event in events if (visible := _event_for_memberships(event, conversation_ids)) is not None]


def _is_general_conversation(db, conversation_id: int) -> bool:
    return bool(db.execute(text(
        "SELECT 1 FROM internal_conversations WHERE id=:c AND kind='GROUP' AND title=:t LIMIT 1"
    ), {"c": conversation_id, "t": GENERAL_CHAT_TITLE}).first())


def ensure_general_conversation_membership(db, account_id: str) -> int:
    """Ensure one account-wide team chat and reconcile membership from account access.

    The account owner is OWNER. Active users with can_manage_team are ADMIN.
    Other active users with can_view are MEMBER. Revoked/blocked users are removed.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "boris_general_chat:" + account_id})
    owner_row = db.execute(text(
        "SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"
    ), {"a": account_id}).first()
    if not owner_row or not owner_row[0]:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    owner_user_id = int(owner_row[0])
    existing = db.execute(text(
        "SELECT id FROM internal_conversations "
        "WHERE account_id=:a AND kind='GROUP' AND title=:t ORDER BY id LIMIT 1"
    ), {"a": account_id, "t": GENERAL_CHAT_TITLE}).first()
    if existing:
        conversation_id = int(existing[0])
    else:
        created = db.execute(text(
            "INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) "
            "VALUES (:a,'GROUP',:t,:u) RETURNING id"
        ), {"a": account_id, "t": GENERAL_CHAT_TITLE, "u": owner_user_id}).first()
        conversation_id = int(created[0])

    members = db.execute(text(
        "SELECT u.id AS user_id, "
        "CASE WHEN a.owner_user_id=u.id THEN 'OWNER' "
        "WHEN COALESCE(uaa.can_manage_team,FALSE)=TRUE THEN 'ADMIN' ELSE 'MEMBER' END AS role "
        "FROM users u "
        "LEFT JOIN accounts a ON a.account_id=:a AND a.owner_user_id=u.id "
        "LEFT JOIN user_account_access uaa ON uaa.account_id=:a AND uaa.user_id=u.id AND uaa.can_view=TRUE "
        "WHERE (a.owner_user_id IS NOT NULL OR uaa.user_id IS NOT NULL) "
        "AND COALESCE(u.is_active,TRUE)=TRUE AND COALESCE(u.status,'active')<>'blocked'"
    ), {"a": account_id}).mappings().all()
    desired = {int(row["user_id"]): str(row["role"]) for row in members}
    for user_id, role in desired.items():
        db.execute(text(
            "INSERT INTO internal_conversation_members(conversation_id,user_id,role) "
            "VALUES (:c,:u,:r) "
            "ON CONFLICT (conversation_id,user_id) DO UPDATE SET role=EXCLUDED.role"
        ), {"c": conversation_id, "u": user_id, "r": role})
    if desired:
        db.execute(text(
            "DELETE FROM internal_conversation_members "
            "WHERE conversation_id=:c AND user_id<>:platform_owner "
            "AND NOT (user_id = ANY(:ids))"
        ), {"c": conversation_id, "ids": list(desired), "platform_owner": PLATFORM_OWNER_ID})
    else:
        db.execute(text(
            "DELETE FROM internal_conversation_members "
            "WHERE conversation_id=:c AND user_id<>:platform_owner"
        ), {"c": conversation_id, "platform_owner": PLATFORM_OWNER_ID})
    db.execute(text(
        "UPDATE internal_conversations SET updated_at=GREATEST(updated_at,created_at) WHERE id=:c"
    ), {"c": conversation_id})
    return conversation_id


def _record_system_event(db, conversation_id: int, actor_user_id: int, text_value: str) -> int:
    created = db.execute(text(
        "INSERT INTO internal_messages(conversation_id,sender_user_id,body) VALUES (:c,:u,:b) RETURNING id"
    ), {"c": conversation_id, "u": actor_user_id, "b": f"Системное событие: {text_value}"}).first()
    db.execute(text("UPDATE internal_conversations SET updated_at=now() WHERE id=:c"), {"c": conversation_id})
    return int(created[0])


def _user_label(db, account_id: str, user_id: int) -> str:
    row = db.execute(text(
        "SELECT COALESCE(tm.name,NULLIF(split_part(u.email,'@',1),''),u.email,'Сотрудник') "
        "FROM users u LEFT JOIN team_memberships tm ON tm.account_id=:a AND tm.user_id=u.id AND tm.status='ACTIVE' "
        "WHERE u.id=:u LIMIT 1"
    ), {"a": account_id, "u": user_id}).first()
    return str(row[0] if row and row[0] else "Сотрудник")


def _attachment_storage_path(storage_ref: str, conversation_id: int | None = None) -> Path:
    parts = str(storage_ref or "").split(":")
    if len(parts) != 3 or parts[0] != "internal" or not parts[1].isdigit():
        raise HTTPException(status_code=400, detail="Некорректная ссылка на вложение")
    stored_conversation_id = int(parts[1])
    token = parts[2]
    if conversation_id is not None and stored_conversation_id != conversation_id:
        raise HTTPException(status_code=400, detail="Вложение относится к другому чату")
    if len(token) != 32 or any(ch not in "0123456789abcdef" for ch in token.lower()):
        raise HTTPException(status_code=400, detail="Некорректная ссылка на вложение")
    return ATTACHMENT_ROOT / str(stored_conversation_id) / token


class ConversationCreate(BaseModel):
    account_id: str
    kind: Literal["DM", "GROUP"]
    title: Optional[str] = None
    member_user_ids: List[int] = Field(default_factory=list)


class MemberCreate(BaseModel):
    user_id: int
    role: Literal["ADMIN", "MEMBER"] = "MEMBER"


class MemberRoleUpdate(BaseModel):
    role: Literal["ADMIN", "MEMBER"]


class OwnershipTransfer(BaseModel):
    new_owner_user_id: int


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class MessageCreate(BaseModel):
    client_message_id: Optional[UUID] = None
    message_type: Literal["text", "voice", "video", "video_note"] = "text"
    voice_asset_id: Optional[UUID] = None
    video_asset_id: Optional[UUID] = None
    body: str = Field(default="", max_length=20000)
    reply_to_message_id: Optional[int] = None


class CallStart(BaseModel):
    client_call_id: UUID
    call_type: Literal["audio", "video"] = "audio"


class CallTransition(BaseModel):
    action: Literal["accept", "decline", "connecting", "connected", "reconnecting", "end"]
    session_id: Optional[str] = Field(default=None, max_length=256)


class CallSignal(BaseModel):
    event_type: Literal["offer", "answer", "ice", "renegotiate", "ice_restart", "mute", "camera", "heartbeat"]
    payload: dict = Field(default_factory=dict)


class MessengerTelemetry(BaseModel):
    account_id: str = Field(min_length=1, max_length=255)
    event: str = Field(min_length=1, max_length=64)
    entity_type: Optional[Literal["call", "conversation", "media"]] = None
    entity_id: Optional[str] = Field(default=None, max_length=255)
    details: dict = Field(default_factory=dict)


class ReactionCreate(BaseModel):
    reaction: str = Field(min_length=1, max_length=32)


class AttachmentCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)
    storage_ref: str = Field(min_length=1, max_length=2000)
    size_bytes: Optional[int] = Field(default=None, ge=0)




@router.post("/telemetry")
def record_messenger_telemetry(body: MessengerTelemetry, user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_telemetry", f"user:{user.id}", limit=180, window_seconds=60)
    db = SessionLocal()
    try:
        if not _access(db, user, body.account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        try:
            telemetry_service.record(
                db,
                body.event,
                user_id=user.id,
                account_id=body.account_id,
                entity_type=body.entity_type or "client",
                entity_id=body.entity_id,
                details=body.details,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Неизвестное событие telemetry") from exc
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/readiness")
def internal_messenger_readiness(account_id: str, user=Depends(get_current_user)):
    """Read-only readiness for team chat/realtime/push. No provider calls or mutations."""
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        general = db.execute(text(
            "SELECT c.id,EXISTS(SELECT 1 FROM internal_conversation_members m WHERE m.conversation_id=c.id AND m.user_id=:u) "
            "FROM internal_conversations c WHERE c.account_id=:a AND c.kind='GROUP' AND c.title=:t ORDER BY c.id LIMIT 1"
        ), {"a": account_id, "u": user.id, "t": GENERAL_CHAT_TITLE}).first()
        # Readiness must not expose the cursor or rate of private events.
        event_stats = db.execute(text(
            "SELECT COALESCE(max(e.id),0), count(*) FILTER (WHERE e.created_at>=now()-interval '5 minutes') "
            "FROM internal_messenger_events e WHERE e.account_id=:a AND "
            "(e.event_type IN ('team.changed','crm.task.changed','crm.stage.changed','client.assignment.changed','client.ai_mode.changed','client.message.changed') OR (e.event_type LIKE 'internal.%' AND EXISTS (SELECT 1 FROM internal_conversation_members m JOIN internal_conversations c ON c.id=m.conversation_id WHERE m.user_id=:u AND c.account_id=e.account_id AND ((e.entity_type='conversation' AND e.entity_id=c.id::text) OR (e.entity_type='message' AND EXISTS (SELECT 1 FROM internal_messages im WHERE im.id::text=e.entity_id AND im.conversation_id=c.id))))))"
        ), {"a": account_id, "u": user.id}).first()
        max_event_id, events_5m = int(event_stats[0]), int(event_stats[1])
        unread = int(db.execute(text(
            "SELECT COALESCE(sum((SELECT count(*) FROM internal_messages im "
            "WHERE im.conversation_id=m.conversation_id AND im.id>COALESCE(m.last_read_message_id,0) "
            "AND im.sender_user_id<>:u AND im.deleted_at IS NULL)),0) "
            "FROM internal_conversation_members m JOIN internal_conversations c ON c.id=m.conversation_id "
            "WHERE m.user_id=:u AND c.account_id=:a"
        ), {"a": account_id, "u": user.id}).scalar() or 0)
        push_subscriptions = int(db.execute(text(
            "SELECT count(*) FROM lead_push_subscriptions WHERE account_id=:a AND user_id=:u AND active=TRUE"
        ), {"a": account_id, "u": user.id}).scalar() or 0)
        push_failed_24h = int(db.execute(text(
            "SELECT count(*) FROM lead_notification_deliveries d "
            "JOIN lead_notification_outbox o ON o.id=d.outbox_id "
            "WHERE o.account_id=:a AND d.channel='webpush' AND d.status='failed' "
            "AND d.created_at>=now()-interval '24 hours' "
            "AND EXISTS (SELECT 1 FROM lead_push_subscriptions s WHERE s.account_id=:a AND s.user_id=:u AND left(s.endpoint_hash,16)=d.target_key) "
            "AND (o.event_type<>'internal_message' OR EXISTS (SELECT 1 FROM internal_conversation_members m JOIN internal_conversations c ON c.id=m.conversation_id WHERE m.user_id=:u AND c.account_id=:a AND c.id::text=o.payload_json->>'internal_conversation_id'))"
        ), {"a": account_id, "u": user.id}).scalar() or 0)
        # This is the current user's Messenger queue, never tenant-wide private activity.
        push_pending = int(db.execute(text(
            "SELECT count(*) FROM lead_notification_outbox o "
            "WHERE o.account_id=:a AND o.status IN ('pending','retry','processing') "
            "AND o.payload_json->'target_user_ids' @> jsonb_build_array(CAST(:u AS integer)) "
            "AND (o.event_type<>'internal_message' OR EXISTS (SELECT 1 FROM internal_conversation_members m JOIN internal_conversations c ON c.id=m.conversation_id WHERE m.user_id=:u AND c.account_id=:a AND c.id::text=o.payload_json->>'internal_conversation_id'))"
        ), {"a": account_id, "u": user.id}).scalar() or 0)
        general_exists = bool(general)
        general_member = bool(general and general[1])
        state = "ready" if general_exists and general_member else "degraded"
        return {
            "status": state,
            "account_id": account_id,
            "general_chat": {"exists": general_exists, "member": general_member, "conversation_id": int(general[0]) if general else None},
            "realtime": {"event_cursor": max_event_id, "stream_route": "/api/internal-messenger/events/stream"},
            "unread": unread,
            "push": {
                "active_subscriptions": push_subscriptions,
                "failed_deliveries_24h": push_failed_24h,
                "pending_outbox": push_pending,
            },
            "observability": {
                "events_5m": events_5m,
                "event_cursor": max_event_id,
            },
        }
    finally:
        db.close()


@router.get("/people")
def list_people(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        rows = db.execute(text(
            "SELECT DISTINCT u.id AS user_id,u.email, "
            "COALESCE(tm.name, NULLIF(split_part(u.email,'@',1),''), 'Сотрудник') AS display_name, "
            "CASE WHEN a.owner_user_id=u.id THEN 'OWNER' ELSE COALESCE(uaa.role,tm.role,'MEMBER') END AS account_role, "
            "tm.last_login_at "
            "FROM users u "
            "LEFT JOIN accounts a ON a.account_id=:a AND a.owner_user_id=u.id "
            "LEFT JOIN user_account_access uaa ON uaa.account_id=:a AND uaa.user_id=u.id AND uaa.can_view=TRUE "
            "LEFT JOIN team_memberships tm ON tm.account_id=:a AND tm.user_id=u.id AND tm.status='ACTIVE' "
            "WHERE (a.owner_user_id IS NOT NULL OR uaa.user_id IS NOT NULL) "
            "AND u.id<>:current_user "
            "AND COALESCE(u.is_active,TRUE)=TRUE "
            "AND COALESCE(u.status,'active')<>'blocked' "
            "ORDER BY display_name,u.email"
        ), {"a": account_id, "current_user": int(user.id)}).mappings().all()
        now = datetime.now(timezone.utc)
        people = []
        for r in rows:
            last_login = r["last_login_at"]
            age_seconds = (now - last_login).total_seconds() if last_login else None
            presence = "онлайн" if age_seconds is not None and age_seconds <= 300 else "недавно" if age_seconds is not None and age_seconds <= 86400 else "офлайн"
            people.append({
                "user_id": r["user_id"],
                "email": r["email"],
                "display_name": r["display_name"],
                "account_role": r["account_role"],
                "presence": presence,
                "last_login_at": _iso(last_login),
            })
        return {"status": "ok", "account_id": account_id, "people": people}
    finally:
        db.close()


@router.get("/events")
def list_messenger_events(account_id: str, after_id: int = 0, limit: int = Query(default=100, ge=1, le=200), user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        items = _messenger_event_feed(db, account_id, after_id, limit, user_id=user.id)
        return {"status": "ok", "items": items, "next_after_id": items[-1]["id"] if items else after_id}
    finally:
        db.close()


@router.get("/events/stream")
async def stream_messenger_events(request: Request, account_id: str, after_id: int = 0, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    finally:
        db.close()
    try:
        last_event_id = max(0, int(str(request.headers.get("last-event-id") or "0").strip() or 0))
    except Exception:
        last_event_id = 0
    resume_after = max(0, int(after_id), last_event_id)

    async def event_stream():
        import asyncio
        cursor = resume_after
        idle_cycles = 0
        heartbeat_at = asyncio.get_running_loop().time() + 15.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            session = SessionLocal()
            try:
                if not _stream_session_active(session, user) or not _access(session, user, account_id):
                    break
                items = _messenger_event_feed(session, account_id, cursor, 100, user_id=user.id)
            finally:
                session.close()
            if items:
                for item in items:
                    cursor = max(cursor, int(item["id"]))
                    payload = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: messenger\ndata: {payload}\n\n"
                idle_cycles = 0
                sleep_for = 0.35
            else:
                idle_cycles = min(idle_cycles + 1, 20)
                sleep_for = 0.5 if idle_cycles < 4 else (1.0 if idle_cycles < 10 else 2.0)
            now = asyncio.get_running_loop().time()
            if now >= heartbeat_at:
                session = SessionLocal()
                try:
                    if not _stream_session_active(session, user) or not _access(session, user, account_id):
                        break
                finally:
                    session.close()
                yield f": heartbeat {cursor}\n\n"
                heartbeat_at = now + 15.0
            await asyncio.sleep(sleep_for)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/general/ensure")
def ensure_general_conversation(body: dict, user=Depends(get_current_user)):
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id обязателен")
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        conversation_id = ensure_general_conversation_membership(db, account_id)
        if is_private_platform_owner(user):
            db.execute(text(
                "INSERT INTO internal_conversation_members(conversation_id,user_id,role) "
                "VALUES (:c,:u,'ADMIN') "
                "ON CONFLICT (conversation_id,user_id) DO UPDATE SET role='ADMIN'"
            ), {"c": conversation_id, "u": int(user.id)})
        db.commit()
        return {"status": "ok", "account_id": account_id, "conversation_id": conversation_id, "title": GENERAL_CHAT_TITLE, "is_general": True}
    finally:
        db.close()


@router.get("/conversations")
def list_conversations(account_id: str, q: Optional[str] = Query(default=None, max_length=120), user=Depends(get_current_user)):
    if (q or "").strip():
        enforce_messenger_rate("messenger_search", f"user:{user.id}", limit=90, window_seconds=60)
    db = SessionLocal()
    try:
        if not _access(db, user, account_id):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        search = (q or "").strip()
        rows = db.execute(text(
            "SELECT c.id,c.kind,c.title,c.updated_at,m.role,m.last_read_message_id, "
            "CASE WHEN c.kind='DM' THEN COALESCE((SELECT COALESCE(tm.name,NULLIF(split_part(u2.email,'@',1),''),u2.email) FROM internal_conversation_members m2 JOIN users u2 ON u2.id=m2.user_id LEFT JOIN team_memberships tm ON tm.account_id=c.account_id AND tm.user_id=u2.id AND tm.status='ACTIVE' WHERE m2.conversation_id=c.id AND m2.user_id<>:u LIMIT 1), 'Личный чат') ELSE COALESCE(c.title,'Рабочая группа') END AS display_title, "
            "(SELECT count(*) FROM internal_messages um WHERE um.conversation_id=c.id AND um.id>COALESCE(m.last_read_message_id,0) AND um.sender_user_id<>:u AND um.deleted_at IS NULL) AS unread_count, "
            "(SELECT CASE WHEN im.deleted_at IS NOT NULL THEN 'Сообщение удалено' WHEN im.message_type='voice' THEN '🎙 Голосовое сообщение' ELSE im.body END FROM internal_messages im WHERE im.conversation_id=c.id ORDER BY im.created_at DESC,im.id DESC LIMIT 1) AS last_body, "
            "(SELECT im.created_at FROM internal_messages im WHERE im.conversation_id=c.id ORDER BY im.created_at DESC,im.id DESC LIMIT 1) AS last_at "
            "FROM internal_conversations c JOIN internal_conversation_members m ON m.conversation_id=c.id AND m.user_id=:u "
            "WHERE c.account_id=:a AND (:q='' OR coalesce(c.title,'') ILIKE :like OR EXISTS (SELECT 1 FROM internal_messages sm WHERE sm.conversation_id=c.id AND sm.deleted_at IS NULL AND (sm.body ILIKE :like OR EXISTS (SELECT 1 FROM internal_voice_assets sv WHERE sv.message_id=sm.id AND sv.deleted_at IS NULL AND sv.transcription_status='ready' AND sv.transcription_text ILIKE :like)))) "
            "ORDER BY CASE WHEN c.kind='GROUP' AND c.title=:general_title THEN 0 ELSE 1 END, c.updated_at DESC,c.id DESC"), {"a": account_id, "u": user.id, "q": search, "like": f"%{search}%", "general_title": GENERAL_CHAT_TITLE}).mappings().all()
        return {"status": "ok", "conversations": [{"id": r["id"], "kind": r["kind"], "title": r["display_title"], "role": r["role"], "unread_count": int(r["unread_count"] or 0), "last_body": r["last_body"], "last_at": _iso(r["last_at"]), "updated_at": _iso(r["updated_at"]), "is_general": bool(r["kind"] == "GROUP" and r["title"] == GENERAL_CHAT_TITLE)} for r in rows]}
    finally:
        db.close()


@router.post("/conversations")
def create_conversation(body: ConversationCreate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        account_id = str(body.account_id or "").strip()
        if not account_id or not _access(db, user, account_id, "can_view"):
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        _require_reply(db, user, account_id)

        target_ids = [int(value) for value in body.member_user_ids if int(value) != int(user.id)]
        target_ids = list(dict.fromkeys(target_ids))
        member_ids = [int(user.id)] + target_ids
        title = str(body.title or "").strip()

        if body.kind == "DM":
            if len(target_ids) != 1:
                raise HTTPException(status_code=400, detail="Выберите одного сотрудника для личного чата")
            title = ""
        else:
            if not title:
                raise HTTPException(status_code=400, detail="Введите название группы")
            if len(title) > 160:
                raise HTTPException(status_code=400, detail="Название группы слишком длинное")
            if not target_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы одного участника")

        # Serialize duplicate clicks / simultaneous tabs for the exact creation
        # intent. This is a DB-scoped lock only; no external side effects exist.
        intent_key = (
            f"internal_chat_create:{account_id}:{int(user.id)}:{body.kind}:"
            + ",".join(str(value) for value in sorted(target_ids))
            + (f":{title.casefold()}" if body.kind == "GROUP" else "")
        )
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": intent_key})

        for member_id in target_ids:
            allowed = db.execute(text(
                "SELECT 1 FROM users u "
                "LEFT JOIN accounts a ON a.account_id=:a AND a.owner_user_id=u.id "
                "LEFT JOIN user_account_access uaa ON uaa.account_id=:a AND uaa.user_id=u.id AND uaa.can_view=TRUE "
                "WHERE u.id=:u AND (a.owner_user_id IS NOT NULL OR uaa.user_id IS NOT NULL) "
                "AND COALESCE(u.is_active,TRUE)=TRUE AND COALESCE(u.status,'active')<>'blocked' LIMIT 1"
            ), {"a": account_id, "u": member_id}).first()
            if not allowed:
                raise HTTPException(status_code=403, detail="Один из участников не имеет активного доступа к аккаунту")

        if body.kind == "DM":
            existing = db.execute(text(
                "SELECT c.id FROM internal_conversations c "
                "JOIN internal_conversation_members m1 ON m1.conversation_id=c.id "
                "JOIN internal_conversation_members m2 ON m2.conversation_id=c.id "
                "WHERE c.account_id=:a AND c.kind='DM' AND m1.user_id=:u AND m2.user_id=:v "
                "AND (SELECT count(*) FROM internal_conversation_members mx WHERE mx.conversation_id=c.id)=2 "
                "ORDER BY c.id LIMIT 1"
            ), {"a": account_id, "u": int(user.id), "v": target_ids[0]}).first()
            if existing:
                return {"status": "ok", "conversation_id": int(existing[0]), "deduplicated": True}
        else:
            # Protect double click / retry after an ambiguous browser response.
            recent = db.execute(text(
                "SELECT c.id FROM internal_conversations c "
                "WHERE c.account_id=:a AND c.kind='GROUP' AND c.created_by_user_id=:u "
                "AND lower(btrim(coalesce(c.title,'')))=lower(:t) "
                "AND c.created_at>=now()-interval '30 seconds' "
                "AND (SELECT count(*) FROM internal_conversation_members m WHERE m.conversation_id=c.id)=:n "
                "AND (SELECT count(*) FROM internal_conversation_members m WHERE m.conversation_id=c.id AND m.user_id=ANY(:ids))=:n "
                "ORDER BY c.id DESC LIMIT 1"
            ), {"a": account_id, "u": int(user.id), "t": title, "n": len(member_ids), "ids": member_ids}).first()
            if recent:
                return {"status": "ok", "conversation_id": int(recent[0]), "deduplicated": True}

        row = db.execute(text(
            "INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id) "
            "VALUES (:a,:k,:t,:u) RETURNING id"
        ), {"a": account_id, "k": body.kind, "t": title or None, "u": int(user.id)}).first()
        conversation_id = int(row[0])
        for idx, member_id in enumerate(member_ids):
            db.execute(text(
                "INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,:u,:r)"
            ), {"c": conversation_id, "u": member_id, "r": "OWNER" if idx == 0 else "MEMBER"})

        if body.kind == "GROUP":
            _record_system_event(db, conversation_id, int(user.id), f"Группа «{title}» создана")
        emit_messenger_event(
            db, account_id, "internal.conversation.created", "conversation", conversation_id,
            {"kind": body.kind, "title": title or None, "member_user_ids": member_ids},
        )
        append_security_audit(
            db,
            action="conversation_create",
            user_id=int(user.id),
            account_id=account_id,
            entity_type="conversation",
            entity_id=conversation_id,
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"kind": body.kind, "member_count": len(member_ids)},
        )
        db.commit()
        return {"status": "ok", "conversation_id": conversation_id, "deduplicated": False}
    finally:
        db.close()


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: int, body: ConversationUpdate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Общий чат управляется составом команды и не переименовывается вручную")
        if row["kind"] != "GROUP" or row["role"] not in {"OWNER", "ADMIN"}:
            raise HTTPException(status_code=403, detail="Только владелец или администратор группы может менять название")
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Название группы не может быть пустым")
        db.execute(text("UPDATE internal_conversations SET title=:t,updated_at=now() WHERE id=:c"), {"t": title, "c": conversation_id})
        _record_system_event(db, conversation_id, user.id, f"Группа переименована в «{title}»")
        emit_messenger_event(db, row["account_id"], "internal.conversation.updated", "conversation", conversation_id, {"title": title})
        db.commit()
        return {"status": "ok", "conversation_id": conversation_id, "title": title}
    finally:
        db.close()


@router.get("/conversations/{conversation_id}/members")
def list_members(conversation_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        if _is_general_conversation(db, conversation_id):
            ensure_general_conversation_membership(db, row["account_id"])
            db.commit()
        members = db.execute(text("SELECT m.user_id,m.role,u.email,u.role AS account_role,COALESCE(tm.name,NULLIF(split_part(u.email,'@',1),''),u.email) AS display_name,tm.last_login_at FROM internal_conversation_members m JOIN users u ON u.id=m.user_id LEFT JOIN team_memberships tm ON tm.account_id=:a AND tm.user_id=u.id AND tm.status='ACTIVE' WHERE m.conversation_id=:c ORDER BY CASE WHEN m.role='OWNER' THEN 0 WHEN m.role='ADMIN' THEN 1 ELSE 2 END,m.joined_at,m.user_id"), {"c": conversation_id, "a": row["account_id"]}).mappings().all()
        now = datetime.now(timezone.utc)
        result = []
        _support_thread = str(row.get("title") or "") == "Поддержка BORIS"
        for member in members:
            last_login = member["last_login_at"]
            age_seconds = (now - last_login).total_seconds() if last_login else None
            presence = "онлайн" if age_seconds is not None and age_seconds <= 300 else "недавно" if age_seconds is not None and age_seconds <= 86400 else "офлайн"
            item = {**dict(member), "last_login_at": _iso(last_login), "presence": presence}
            if _support_thread and int(member["user_id"]) == int(PLATFORM_OWNER_ID):
                item["display_name"] = "Поддержка BORIS"
                item["email"] = ""
                item["presence"] = "поддержка"
                item["support_identity"] = True
            result.append(item)
        return {"status": "ok", "conversation_id": conversation_id, "account_id": row["account_id"], "members": result}
    finally:
        db.close()


@router.post("/conversations/{conversation_id}/members")
def add_member(conversation_id: int, body: MemberCreate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Участники общего чата синхронизируются из команды автоматически")
        if row["kind"] != "GROUP" or row["role"] not in {"OWNER", "ADMIN"}:
            raise HTTPException(status_code=403, detail="Только владелец или администратор группы может добавлять участников")
        if row["role"] == "ADMIN" and body.role != "MEMBER":
            raise HTTPException(status_code=403, detail="Администратор может добавлять только обычных участников")
        if not db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u UNION SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u AND can_view=TRUE LIMIT 1"), {"a": row["account_id"], "u": body.user_id}).first():
            raise HTTPException(status_code=403, detail="Участник не имеет доступа к аккаунту")
        db.execute(text("INSERT INTO internal_conversation_members(conversation_id,user_id,role) VALUES (:c,:u,:r) ON CONFLICT (conversation_id,user_id) DO UPDATE SET role=:r"), {"c": conversation_id, "u": body.user_id, "r": body.role})
        label = _user_label(db, row["account_id"], body.user_id)
        _record_system_event(db, conversation_id, user.id, f"{label} добавлен в группу")
        emit_messenger_event(db, row["account_id"], "internal.conversation.member_added", "conversation", conversation_id, {"user_id": body.user_id, "role": body.role})
        append_security_audit(
            db, action="group_member_add", user_id=int(user.id),
            account_id=row["account_id"], entity_type="conversation", entity_id=int(conversation_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"target_user_id": int(body.user_id), "role": body.role},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Добавил участника в группу", object_kind="group_member",
            object_name=f"conversation:{conversation_id}:user:{int(body.user_id)}", after=f"role:{body.role}",
        )
        return {"status": "ok", "conversation_id": conversation_id, "user_id": body.user_id, "role": body.role}
    finally:
        db.close()


@router.patch("/conversations/{conversation_id}/members/{member_user_id}")
def change_member_role(conversation_id: int, member_user_id: int, body: MemberRoleUpdate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Роли общего чата синхронизируются из ролей команды")
        if row["kind"] != "GROUP" or row["role"] != "OWNER":
            raise HTTPException(status_code=403, detail="Только владелец группы может менять роли")
        target = db.execute(text(
            "SELECT role FROM internal_conversation_members WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": member_user_id}).mappings().first()
        if not target:
            raise HTTPException(status_code=404, detail="Участник не найден")
        if target["role"] == "OWNER":
            raise HTTPException(status_code=400, detail="Сначала передайте владение группой")
        db.execute(text(
            "UPDATE internal_conversation_members SET role=:r WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": member_user_id, "r": body.role})
        label = _user_label(db, row["account_id"], member_user_id)
        event_text = f"{label} назначен администратором" if body.role == "ADMIN" else f"{label} переведён в участники"
        _record_system_event(db, conversation_id, user.id, event_text)
        emit_messenger_event(db, row["account_id"], "internal.conversation.role_changed", "conversation", conversation_id, {"user_id": member_user_id, "role": body.role})
        append_security_audit(
            db, action="group_role_change", user_id=int(user.id),
            account_id=row["account_id"], entity_type="conversation", entity_id=int(conversation_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"target_user_id": int(member_user_id), "role": body.role},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Изменил роль участника группы", object_kind="group_role",
            object_name=f"conversation:{conversation_id}:user:{int(member_user_id)}", after=f"role:{body.role}",
        )
        return {"status": "ok", "conversation_id": conversation_id, "user_id": member_user_id, "role": body.role}
    finally:
        db.close()


@router.delete("/conversations/{conversation_id}/members/{member_user_id}")
def remove_member(conversation_id: int, member_user_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Участники общего чата синхронизируются из команды автоматически")
        if row["kind"] != "GROUP" or row["role"] not in {"OWNER", "ADMIN"}:
            raise HTTPException(status_code=403, detail="Только владелец или администратор группы может удалять участников")
        target = db.execute(text(
            "SELECT role FROM internal_conversation_members WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": member_user_id}).mappings().first()
        if not target:
            raise HTTPException(status_code=404, detail="Участник не найден")
        if target["role"] == "OWNER":
            raise HTTPException(status_code=400, detail="Владельца нельзя удалить. Сначала передайте владение группой")
        if row["role"] == "ADMIN" and target["role"] == "ADMIN":
            raise HTTPException(status_code=403, detail="Администратор не может удалить другого администратора")
        label = _user_label(db, row["account_id"], member_user_id)
        db.execute(text(
            "DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": member_user_id})
        _record_system_event(db, conversation_id, user.id, f"{label} удалён из группы")
        emit_messenger_event(db, row["account_id"], "internal.conversation.member_removed", "conversation", conversation_id, {"user_id": member_user_id})
        append_security_audit(
            db, action="group_member_remove", user_id=int(user.id),
            account_id=row["account_id"], entity_type="conversation", entity_id=int(conversation_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"target_user_id": int(member_user_id)},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Удалил участника из группы", object_kind="group_member",
            object_name=f"conversation:{conversation_id}:user:{int(member_user_id)}", after="removed",
        )
        return {"status": "ok", "conversation_id": conversation_id, "user_id": member_user_id, "removed": True}
    finally:
        db.close()


@router.post("/conversations/{conversation_id}/transfer-ownership")
def transfer_ownership(conversation_id: int, body: OwnershipTransfer, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Владение общим чатом закреплено за владельцем аккаунта")
        if row["kind"] != "GROUP" or row["role"] != "OWNER":
            raise HTTPException(status_code=403, detail="Только владелец группы может передать владение")
        if body.new_owner_user_id == user.id:
            raise HTTPException(status_code=400, detail="Вы уже владелец этой группы")
        target = db.execute(text(
            "SELECT role FROM internal_conversation_members WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": body.new_owner_user_id}).mappings().first()
        if not target:
            raise HTTPException(status_code=404, detail="Новый владелец должен быть участником группы")
        db.execute(text(
            "UPDATE internal_conversation_members SET role='ADMIN' WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": user.id})
        db.execute(text(
            "UPDATE internal_conversation_members SET role='OWNER' WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": body.new_owner_user_id})
        label = _user_label(db, row["account_id"], body.new_owner_user_id)
        _record_system_event(db, conversation_id, user.id, f"Владение группой передано: {label}")
        emit_messenger_event(db, row["account_id"], "internal.conversation.owner_changed", "conversation", conversation_id, {"owner_user_id": body.new_owner_user_id, "previous_owner_user_id": user.id})
        append_security_audit(
            db, action="group_owner_transfer", user_id=int(user.id),
            account_id=row["account_id"], entity_type="conversation", entity_id=int(conversation_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"new_owner_user_id": int(body.new_owner_user_id)},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Передал владение группой", object_kind="group_owner",
            object_name=f"conversation:{conversation_id}", before=f"user:{int(user.id)}", after=f"user:{int(body.new_owner_user_id)}",
        )
        return {
            "status": "ok",
            "conversation_id": conversation_id,
            "previous_owner_user_id": user.id,
            "owner_user_id": body.new_owner_user_id,
        }
    finally:
        db.close()


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int, q: Optional[str] = Query(default=None, max_length=120), limit: int = Query(default=100, ge=1, le=200), before_id: int = Query(default=0, ge=0), user=Depends(get_current_user)):
    if (q or "").strip():
        enforce_messenger_rate("messenger_search", f"user:{user.id}", limit=90, window_seconds=60)
    db = SessionLocal()
    try:
        _member_row = _require_member(db, conversation_id, user)
        _support_thread = str(_member_row.get("title") or "") == "Поддержка BORIS"
        search = (q or "").strip()
        rows = db.execute(text("SELECT m.id,m.message_type,m.sender_user_id,u.email,COALESCE(tm.name,NULLIF(split_part(u.email,'@',1),''),u.email,'Сотрудник') AS author_label,m.body,m.reply_to_message_id,m.edited_at,m.deleted_at,m.created_at,EXISTS(SELECT 1 FROM internal_message_pins p WHERE p.message_id=m.id) AS pinned,COALESCE((SELECT json_agg(json_build_object('reaction',r.reaction,'user_id',r.user_id)) FROM internal_message_reactions r WHERE r.message_id=m.id),'[]'::json) AS reactions,(SELECT count(*) FROM internal_conversation_members rm WHERE rm.conversation_id=m.conversation_id AND rm.user_id<>m.sender_user_id AND COALESCE(rm.last_read_message_id,0)>=m.id) AS read_by_count,COALESCE((SELECT json_agg(json_build_object('id',a.id,'file_name',a.file_name,'content_type',a.content_type,'size_bytes',a.size_bytes,'download_url','/api/internal-messenger/attachments/' || a.id::text,'created_at',a.created_at) ORDER BY a.id) FROM internal_message_attachments a WHERE a.message_id=m.id),'[]'::json) AS attachments FROM internal_messages m JOIN users u ON u.id=m.sender_user_id JOIN internal_conversations c ON c.id=m.conversation_id LEFT JOIN team_memberships tm ON tm.account_id=c.account_id AND tm.user_id=u.id AND tm.status='ACTIVE' WHERE m.conversation_id=:c AND (:before=0 OR m.id<:before) AND (:q='' OR (m.deleted_at IS NULL AND (m.body ILIKE :like OR EXISTS (SELECT 1 FROM internal_voice_assets v WHERE v.message_id=m.id AND v.deleted_at IS NULL AND v.transcription_status='ready' AND v.transcription_text ILIKE :like)))) ORDER BY m.id DESC LIMIT :fetch_limit"), {"c": conversation_id, "before": before_id, "q": search, "like": f"%{search}%", "fetch_limit": limit + 1}).mappings().all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        rows = list(reversed(rows))
        payload = []
        for r in rows:
            support_owner = _support_thread and int(r["sender_user_id"]) == int(PLATFORM_OWNER_ID)
            payload.append({
                "id": r["id"], "message_type": r["message_type"],
                "sender_user_id": r["sender_user_id"],
                "sender_email": "" if support_owner else r["email"],
                "author_label": "Поддержка BORIS" if support_owner else r["author_label"],
                "is_own": r["sender_user_id"] == user.id,
                "body": "Сообщение удалено" if r["deleted_at"] else r["body"],
                "reply_to_message_id": r["reply_to_message_id"],
                "edited_at": _iso(r["edited_at"]), "deleted_at": _iso(r["deleted_at"]),
                "created_at": _iso(r["created_at"]), "delivery_status": "DELIVERED",
                "read_by_count": int(r["read_by_count"] or 0), "pinned": bool(r["pinned"]),
                "reactions": r["reactions"], "attachments": r["attachments"],
            })
        return {"status": "ok", "messages": video_service.enrich(db, voice_service.enrich(db, payload)),
                "has_more": has_more, "next_before_id": int(rows[0]["id"]) if rows else before_id}
    finally:
        db.close()



def _support_message_notice(db, conversation_id: int, user, message_id: int, text_value: str):
    try:
        from app.api.support import support_messenger_sync
        return support_messenger_sync(db, int(conversation_id), int(user.id),
                                      str(text_value or ""), int(message_id))
    except Exception as exc:
        logger.warning("support_messenger_sync_failed conversation_id=%s message_id=%s error=%s",
                       conversation_id, message_id, type(exc).__name__)
        return None


def _queue_message_notice(default_account_id: str, notice: dict | None, *,
                          default_event_key: str, default_title: str,
                          default_body: str, default_url: str, default_payload: dict):
    try:
        if notice:
            notification_service.queue_event(
                default_account_id,
                str(notice.get("event_key") or default_event_key),
                str(notice.get("event_type") or "internal_message"),
                str(notice.get("title") or default_title),
                str(notice.get("body") or default_body),
                severity=str(notice.get("severity") or "normal"),
                url=str(notice.get("url") or default_url),
                payload=dict(notice.get("payload") or {}),
            )
        else:
            notification_service.queue_event(
                default_account_id, default_event_key, "internal_message",
                default_title, default_body, severity="normal",
                url=default_url, payload=default_payload,
            )
    except Exception as exc:
        logger.warning("internal_messenger_push_queue_failed event_key=%s error=%s",
                       default_event_key, type(exc).__name__)


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: int, body: MessageCreate, user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_send", f"user:{user.id}", limit=120, window_seconds=60, message="Слишком много сообщений. Подождите немного.")
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        _require_reply(db, user, row["account_id"])
        receipt, replay = messenger_idempotency.begin(db, conversation_id, user.id, body)
        if replay is not None:
            return replay
        if body.message_type == "voice":
            if not body.voice_asset_id:
                raise HTTPException(400, "Не выбрано голосовое сообщение")
            message_id = voice_service.commit(db, conversation_id, body, user)
            targets = [int(item[0]) for item in db.execute(text(
                "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c AND user_id<>:u"
            ), {"c": conversation_id, "u": user.id}).all()]
            response = {"status": "ok", "message": {"id": message_id, "conversation_id": conversation_id,
                "message_type": "voice", "sender_user_id": user.id, "body": "", "delivery_status": "DELIVERED"}}
            support_notice = _support_message_notice(db, conversation_id, user, message_id, "Голосовое сообщение")
            messenger_idempotency.finish(db, receipt, response)
            db.commit()
            sender_name = str(getattr(user, "email", "") or "Сотрудник").split("@", 1)[0]
            _queue_message_notice(
                row["account_id"], support_notice,
                default_event_key=f"internal-message:{message_id}",
                default_title=f"Новое голосовое · {sender_name}",
                default_body="Голосовое сообщение",
                default_url=f"/messenger?account_id={row['account_id']}&internal_conversation_id={conversation_id}",
                default_payload={"target_user_ids": targets, "internal_conversation_id": conversation_id,
                                 "sender_user_id": user.id, "message_type": "voice"},
            )
            return response
        if body.message_type in {"video", "video_note"}:
            if not body.video_asset_id:
                raise HTTPException(400, "Не выбрано видео")
            message_id = video_service.commit(db, conversation_id, body, user)
            targets = [int(item[0]) for item in db.execute(text(
                "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c AND user_id<>:u"
            ), {"c": conversation_id, "u": user.id}).all()]
            response = {"status": "ok", "message": {"id": message_id, "conversation_id": conversation_id,
                "message_type": body.message_type, "sender_user_id": user.id, "body": "", "delivery_status": "DELIVERED"}}
            _video_label = "Видеосообщение" if body.message_type == "video_note" else "Видео"
            support_notice = _support_message_notice(db, conversation_id, user, message_id, _video_label)
            messenger_idempotency.finish(db, receipt, response)
            db.commit()
            sender_name = str(getattr(user, "email", "") or "Сотрудник").split("@", 1)[0]
            _queue_message_notice(
                row["account_id"], support_notice,
                default_event_key=f"internal-message:{message_id}",
                default_title=f"Новое видео · {sender_name}",
                default_body=_video_label,
                default_url=f"/messenger?account_id={row['account_id']}&internal_conversation_id={conversation_id}",
                default_payload={"target_user_ids": targets, "internal_conversation_id": conversation_id,
                                 "sender_user_id": user.id, "message_type": body.message_type},
            )
            return response
        if body.voice_asset_id or body.video_asset_id:
            raise HTTPException(400, "Неверный тип сообщения")
        if not body.body.strip():
            raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
        if body.reply_to_message_id and not db.execute(text("SELECT 1 FROM internal_messages WHERE id=:m AND conversation_id=:c"), {"m": body.reply_to_message_id, "c": conversation_id}).first():
            raise HTTPException(status_code=400, detail="Ответ ссылается на другое сообщение")
        created = db.execute(text("INSERT INTO internal_messages(conversation_id,sender_user_id,body,reply_to_message_id) VALUES (:c,:u,:b,:r) RETURNING id,created_at"), {"c": conversation_id, "u": user.id, "b": body.body.strip(), "r": body.reply_to_message_id}).mappings().first()
        db.execute(text("UPDATE internal_conversations SET updated_at=now() WHERE id=:c"), {"c": conversation_id})
        db.execute(text("UPDATE internal_conversation_members SET last_read_message_id=GREATEST(COALESCE(last_read_message_id,0),:m) WHERE conversation_id=:c AND user_id=:u"), {"m": created["id"], "c": conversation_id, "u": user.id})
        targets = [int(item[0]) for item in db.execute(text(
            "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c AND user_id<>:u"
        ), {"c": conversation_id, "u": user.id}).all()]
        emit_messenger_event(db, row["account_id"], "internal.message.created", "message", created["id"], {"conversation_id": conversation_id, "sender_user_id": user.id, "body": body.body.strip(), "reply_to_message_id": body.reply_to_message_id})
        response = {"status": "ok", "message": {"id": created["id"], "conversation_id": conversation_id, "sender_user_id": user.id, "body": body.body.strip(), "reply_to_message_id": body.reply_to_message_id, "created_at": _iso(created["created_at"]), "delivery_status": "DELIVERED"}}
        support_notice = _support_message_notice(db, conversation_id, user, int(created["id"]), body.body.strip())
        messenger_idempotency.finish(db, receipt, response)
        db.commit()
        sender_name = str(getattr(user, "email", "") or "Сотрудник").split("@", 1)[0]
        _queue_message_notice(
            row["account_id"], support_notice,
            default_event_key=f"internal-message:{created['id']}",
            default_title=f"Новое сообщение · {sender_name}",
            default_body=body.body.strip()[:500],
            default_url=f"/messenger?account_id={row['account_id']}&internal_conversation_id={conversation_id}",
            default_payload={"target_user_ids": targets, "internal_conversation_id": conversation_id,
                             "sender_user_id": user.id},
        )
        return response
    finally:
        db.close()


@router.post("/conversations/{conversation_id}/read")
def mark_read(conversation_id: int, message_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        if not db.execute(text("SELECT 1 FROM internal_messages WHERE id=:m AND conversation_id=:c"), {"m": message_id, "c": conversation_id}).first():
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        advanced = db.execute(text("UPDATE internal_conversation_members SET last_read_message_id=:m WHERE conversation_id=:c AND user_id=:u AND COALESCE(last_read_message_id,0)<:m RETURNING last_read_message_id"), {"m": message_id, "c": conversation_id, "u": user.id}).first()
        if advanced:
            emit_messenger_event(db, row["account_id"], "internal.message.read", "conversation", conversation_id, {"user_id": user.id, "last_read_message_id": message_id})
        db.commit()
        return {"status": "ok", "conversation_id": conversation_id, "last_read_message_id": message_id}
    finally:
        db.close()


@router.get("/conversations/{conversation_id}/read-receipts")
def read_receipts(conversation_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _require_member(db, conversation_id, user)
        rows = db.execute(text(
            "SELECT m.user_id,u.email,m.role,m.last_read_message_id "
            "FROM internal_conversation_members m JOIN users u ON u.id=m.user_id "
            "WHERE m.conversation_id=:c ORDER BY m.joined_at,m.user_id"
        ), {"c": conversation_id}).mappings().all()
        return {
            "status": "ok",
            "conversation_id": conversation_id,
            "receipts": [
                {
                    "user_id": r["user_id"],
                    "email": r["email"],
                    "role": r["role"],
                    "last_read_message_id": r["last_read_message_id"],
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.patch("/messages/{message_id}")
def edit_message(message_id: int, body: MessageCreate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT m.id,m.conversation_id,m.sender_user_id,m.message_type FROM internal_messages m WHERE m.id=:m AND m.deleted_at IS NULL"), {"m": message_id}).mappings().first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row["conversation_id"], user)
        _require_reply(db, user, member["account_id"])
        if row["sender_user_id"] != user.id: raise HTTPException(status_code=403, detail="Редактировать можно только свои сообщения")
        if row["message_type"] != "text":
            raise HTTPException(400, "Медиа и системные сообщения нельзя редактировать")
        db.execute(text("UPDATE internal_messages SET body=:b,edited_at=now() WHERE id=:m"), {"b": body.body.strip(), "m": message_id})
        emit_messenger_event(db, member["account_id"], "internal.message.updated", "message", message_id, {"conversation_id": row["conversation_id"], "body": body.body.strip(), "editor_user_id": user.id})
        db.commit()
        return {"status": "ok", "message_id": message_id, "edited": True}
    finally: db.close()


@router.delete("/messages/{message_id}")
def delete_message(message_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT id,conversation_id,sender_user_id FROM internal_messages WHERE id=:m AND deleted_at IS NULL"), {"m": message_id}).mappings().first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row["conversation_id"], user)
        _require_reply(db, user, member["account_id"])
        if row["sender_user_id"] != user.id: raise HTTPException(status_code=403, detail="Удалять можно только свои сообщения")
        db.execute(text("UPDATE internal_messages SET deleted_at=now(),body='' WHERE id=:m"), {"m": message_id})
        db.execute(text("UPDATE internal_voice_assets SET deleted_at=now() WHERE message_id=:m"), {"m": message_id})
        video_service.mark_deleted(db, message_id)
        emit_messenger_event(db, member["account_id"], "internal.message.deleted", "message", message_id, {"conversation_id": row["conversation_id"], "deleted_by_user_id": user.id})
        append_security_audit(
            db, action="message_delete", user_id=int(user.id),
            account_id=member["account_id"], entity_type="message", entity_id=int(message_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"conversation_id": int(row["conversation_id"])},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=member["account_id"],
            action="Удалил сообщение", object_kind="message",
            object_name=f"message:{int(message_id)}", after="deleted",
        )
        return {"status": "ok", "message_id": message_id, "deleted": True}
    finally: db.close()


@router.post("/messages/{message_id}/reactions")
def react(message_id: int, body: ReactionCreate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT conversation_id FROM internal_messages WHERE id=:m"), {"m": message_id}).first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row[0], user)
        _require_reply(db, user, member["account_id"])
        db.execute(text("INSERT INTO internal_message_reactions(message_id,user_id,reaction) VALUES (:m,:u,:r) ON CONFLICT DO NOTHING"), {"m": message_id, "u": user.id, "r": body.reaction})
        emit_messenger_event(db, member["account_id"], "internal.message.reaction_changed", "message", message_id, {"conversation_id": row[0], "user_id": user.id, "reaction": body.reaction, "active": True})
        db.commit()
        return {"status": "ok", "message_id": message_id, "reaction": body.reaction}
    finally: db.close()


@router.delete("/messages/{message_id}/reactions/{reaction}")
def remove_reaction(message_id: int, reaction: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT conversation_id FROM internal_messages WHERE id=:m"), {"m": message_id}).first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row[0], user)
        _require_reply(db, user, member["account_id"])
        db.execute(text("DELETE FROM internal_message_reactions WHERE message_id=:m AND user_id=:u AND reaction=:r"), {"m": message_id, "u": user.id, "r": reaction})
        emit_messenger_event(db, member["account_id"], "internal.message.reaction_changed", "message", message_id, {"conversation_id": row[0], "user_id": user.id, "reaction": reaction, "active": False})
        db.commit()
        return {"status": "ok", "removed": True}
    finally: db.close()


@router.post("/messages/{message_id}/pin")
def pin(message_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT conversation_id FROM internal_messages WHERE id=:m"), {"m": message_id}).first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row[0], user)
        _require_team_manage(db, user, member["account_id"])
        if member["role"] not in {"OWNER", "ADMIN"}: raise HTTPException(status_code=403, detail="Закреплять могут только владелец и администратор")
        db.execute(text("INSERT INTO internal_message_pins(message_id,pinned_by_user_id) VALUES (:m,:u) ON CONFLICT DO NOTHING"), {"m": message_id, "u": user.id})
        emit_messenger_event(db, member["account_id"], "internal.message.pin_changed", "message", message_id, {"conversation_id": row[0], "pinned": True, "user_id": user.id})
        db.commit()
        return {"status": "ok", "pinned": True}
    finally: db.close()


@router.delete("/messages/{message_id}/pin")
def unpin(message_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT conversation_id FROM internal_messages WHERE id=:m"), {"m": message_id}) .first()
        if not row: raise HTTPException(status_code=404, detail="Сообщение не найдено")
        member = _require_member(db, row[0], user)
        _require_team_manage(db, user, member["account_id"])
        if member["role"] not in {"OWNER", "ADMIN"}: raise HTTPException(status_code=403, detail="Откреплять могут только владелец и администратор")
        db.execute(text("DELETE FROM internal_message_pins WHERE message_id=:m"), {"m": message_id})
        emit_messenger_event(db, member["account_id"], "internal.message.pin_changed", "message", message_id, {"conversation_id": row[0], "pinned": False, "user_id": user.id})
        db.commit()
        return {"status": "ok", "pinned": False}
    finally: db.close()




@router.post("/conversations/{conversation_id}/attachments/upload")
async def upload_attachment(conversation_id: int, file: UploadFile = File(...), user=Depends(get_current_user), attachment_kind: str = "file"):
    if attachment_kind not in {"file", "voice", "video", "video_note"}:
        raise HTTPException(status_code=422, detail="Недопустимый тип вложения")
    enforce_messenger_rate("messenger_upload", f"user:{user.id}", limit=30, window_seconds=600, message="Слишком много загрузок. Повторите позже.")
    if attachment_kind == "voice":
        return await voice_service.upload(conversation_id, file, user)
    if attachment_kind in {"video", "video_note"}:
        return await video_service.upload(conversation_id, file, user, attachment_kind)
    db = SessionLocal()
    try:
        member = _require_member(db, conversation_id, user)
        _require_reply(db, user, member["account_id"])
    finally:
        db.close()
    raw_name = str(file.filename or "file")
    if "/" in raw_name or "\\" in raw_name or "\x00" in raw_name or raw_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    file_name = Path(str(file.filename or "file")).name[:255] or "file"
    content_type = str(file.content_type or "application/octet-stream").lower()
    if Path(file_name).suffix.lower() in ATTACHMENT_DENIED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Исполняемые и скриптовые файлы запрещены")
    if content_type not in ATTACHMENT_ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Этот тип файла не поддерживается")
    target_dir = ATTACHMENT_ROOT / str(conversation_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    target = target_dir / token
    size = 0
    try:
        first_chunk = await file.read(1024 * 1024)
        if not first_chunk:
            raise HTTPException(status_code=400, detail="Пустой файл не поддерживается")
        _validate_attachment_magic(content_type, file_name, first_chunk[:4096])
        with target.open("wb") as output:
            size += len(first_chunk)
            if size > ATTACHMENT_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Файл больше 20 МБ")
            output.write(first_chunk)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > ATTACHMENT_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Файл больше 20 МБ")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {
        "status": "ok",
        "upload": {
            "file_name": file_name,
            "content_type": content_type,
            "storage_ref": f"internal:{conversation_id}:{token}",
            "size_bytes": size,
        },
    }


@router.post("/messages/{message_id}/attachments")
def add_attachment(message_id: int, body: AttachmentCreate, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT conversation_id FROM internal_messages WHERE id=:m AND deleted_at IS NULL"), {"m": message_id}).first()
        if not row:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        conversation_id = int(row[0])
        member = _require_member(db, conversation_id, user)
        _require_reply(db, user, member["account_id"])
        safe_name = Path(body.file_name).name[:255] or "file"
        content_type = str(body.content_type or "").lower().strip()
        if Path(safe_name).suffix.lower() in ATTACHMENT_DENIED_EXTENSIONS:
            raise HTTPException(status_code=415, detail="Исполняемые и активные файлы запрещены")
        if content_type not in ATTACHMENT_ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail="Этот тип файла не поддерживается")
        storage_path = _attachment_storage_path(body.storage_ref, conversation_id)
        if not storage_path.is_file():
            raise HTTPException(status_code=404, detail="Загруженный файл не найден")
        with storage_path.open("rb") as uploaded:
            _validate_attachment_magic(content_type, safe_name, uploaded.read(4096))
        actual_size = storage_path.stat().st_size
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": f"messenger-attachment:{message_id}:{body.storage_ref}"})
        existing = db.execute(text("SELECT id,file_name,content_type,size_bytes,created_at FROM internal_message_attachments WHERE message_id=:m AND storage_ref=:r ORDER BY id LIMIT 1"), {"m": message_id, "r": body.storage_ref}).mappings().first()
        if existing:
            return {"status": "ok", "attachment": {"id": existing["id"], "file_name": existing["file_name"], "content_type": existing["content_type"], "size_bytes": existing["size_bytes"], "download_url": f"/api/internal-messenger/attachments/{existing['id']}", "created_at": _iso(existing["created_at"])}}
        created = db.execute(text(
            "INSERT INTO internal_message_attachments(message_id,file_name,content_type,storage_ref,size_bytes) "
            "VALUES (:m,:n,:t,:r,:s) RETURNING id,created_at"
        ), {
            "m": message_id,
            "n": Path(body.file_name).name[:255] or "file",
            "t": content_type[:128],
            "r": body.storage_ref,
            "s": actual_size,
        }).mappings().first()
        db.commit()
        return {
            "status": "ok",
            "attachment": {
                "id": created["id"],
                "file_name": Path(body.file_name).name[:255] or "file",
                "content_type": content_type[:128],
                "size_bytes": actual_size,
                "download_url": f"/api/internal-messenger/attachments/{created['id']}",
                "created_at": _iso(created["created_at"]),
            },
        }
    finally:
        db.close()


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT a.id,a.file_name,a.content_type,a.storage_ref,m.conversation_id "
            "FROM internal_message_attachments a "
            "JOIN internal_messages m ON m.id=a.message_id "
            "WHERE a.id=:a AND m.deleted_at IS NULL"
        ), {"a": attachment_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Вложение не найдено")
        _require_member(db, int(row["conversation_id"]), user)
        storage_path = _attachment_storage_path(str(row["storage_ref"]), int(row["conversation_id"]))
        if not storage_path.is_file():
            raise HTTPException(status_code=404, detail="Файл вложения отсутствует")
        return FileResponse(
            storage_path,
            media_type=str(row["content_type"] or "application/octet-stream"),
            filename=str(row["file_name"] or "file"),
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
            content_disposition_type="attachment",
        )
    finally:
        db.close()



@router.post("/conversations/{conversation_id}/leave")
def leave_conversation(conversation_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = _require_member(db, conversation_id, user)
        if _is_general_conversation(db, conversation_id):
            raise HTTPException(status_code=409, detail="Общий чат компании покинуть нельзя")
        if row["kind"] != "GROUP":
            raise HTTPException(status_code=400, detail="Личный чат нельзя покинуть как группу")
        if row["role"] == "OWNER":
            raise HTTPException(status_code=409, detail="Владелец должен сначала передать владение группой")
        label = _user_label(db, row["account_id"], user.id)
        db.execute(text(
            "DELETE FROM internal_conversation_members WHERE conversation_id=:c AND user_id=:u"
        ), {"c": conversation_id, "u": user.id})
        _record_system_event(db, conversation_id, user.id, f"{label} покинул группу")
        emit_messenger_event(
            db, row["account_id"], "internal.conversation.member_removed",
            "conversation", conversation_id,
            {"user_id": user.id, "left": True},
        )
        append_security_audit(
            db, action="group_leave", user_id=int(user.id),
            account_id=row["account_id"], entity_type="conversation", entity_id=int(conversation_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Покинул группу", object_kind="group_member",
            object_name=f"conversation:{conversation_id}:user:{int(user.id)}", after="left",
        )
        return {"status": "ok", "conversation_id": conversation_id, "left": True}
    finally:
        db.close()


@router.get("/voice/capabilities")
def voice_capabilities(user=Depends(get_current_user)):
    return {
        "enabled": voice_service.enabled(),
        "local_transcription_enabled": voice_service.enabled("MESSENGER_LOCAL_TRANSCRIPTION_ENABLED"),
        "runtime_contract": voice_service.VOICE_RUNTIME_CONTRACT_VERSION,
    }


@router.get("/voice/{asset_id}")
def play_voice(asset_id: UUID, request: Request, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return voice_service.playback_response(db, asset_id, user, request.headers.get("range"))
    finally:
        db.close()


@router.post("/voice/{asset_id}/playback-ticket")
def create_voice_playback_ticket(asset_id: UUID, response: Response, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        ticket = voice_service.issue_playback_ticket(db, asset_id, user)
    finally:
        db.close()
    path = voice_service.stream_path(asset_id)
    response.set_cookie(
        key=voice_service.VOICE_STREAM_COOKIE,
        value=ticket["ticket"],
        max_age=int(ticket["expires_in"]),
        httponly=True,
        secure=True,
        samesite="strict",
        path=path,
    )
    return {
        "status": "ok",
        "stream_url": ticket["stream_url"],
        "expires_in": ticket["expires_in"],
    }


@router.get("/messages/{message_id}/transcription")
def get_voice_transcription(message_id: int, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return {"status": "ok", "voice": voice_service.public(voice_service.authorize(db, user, message_id=message_id))}
    finally:
        db.close()


@router.post("/messages/{message_id}/transcription/retry")
def retry_voice_transcription(message_id: int, user=Depends(get_current_user)):
    voice_service.require_enabled()
    enforce_messenger_rate("voice_retry", f"user:{user.id}", limit=5, window_seconds=600)
    db = SessionLocal()
    try:
        row = voice_service.authorize(db, user, message_id=message_id, lock=True)
        _require_reply(db, user, row["account_id"])
        if not voice_service.enabled("MESSENGER_LOCAL_TRANSCRIPTION_ENABLED"):
            raise HTTPException(503, "Расшифровка временно недоступна")
        if row["transcription_status"] != "failed":
            return {"status": row["transcription_status"]}
        voice_service.enqueue(db, row)
        db.execute(text("UPDATE background_jobs SET status='queued',attempts=0,scheduled_at=now(),error_text=NULL,updated_at=now() WHERE fingerprint=:f AND status='failed'"), {"f": voice_service.fingerprint(row)})
        db.execute(text("UPDATE internal_voice_assets SET transcription_status='queued',transcription_error_code=NULL WHERE message_id=:m"), {"m": message_id})
        emit_messenger_event(db, row["account_id"], "internal.message.updated", "message", message_id, {"conversation_id": row["conversation_id"]})
        append_security_audit(db, action="transcription_retry", user_id=user.id, account_id=row["account_id"], entity_type="message", entity_id=message_id)
        db.commit()
        return {"status": "queued"}
    finally:
        db.close()

# ---------------- BORIS Messenger V9: video + WebRTC calls ----------------

@router.get("/video/capabilities")
def video_capabilities(user=Depends(get_current_user)):
    return {
        "enabled": video_service.enabled(),
        "video_notes_enabled": video_service.enabled("MESSENGER_VIDEO_NOTE_ENABLED"),
        "runtime_contract": video_service.VIDEO_RUNTIME_CONTRACT_VERSION,
    }


@router.post("/video/{asset_id}/playback-ticket")
def create_video_playback_ticket(asset_id: UUID, response: Response, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        ticket = video_service.issue_playback_ticket(db, asset_id, user)
    finally:
        db.close()
    cookie_path = f"/api/messenger-workspace/video/{asset_id}/"
    response.set_cookie(
        key=video_service.VIDEO_STREAM_COOKIE,
        value=ticket["ticket"],
        max_age=int(ticket["expires_in"]),
        httponly=True,
        secure=True,
        samesite="strict",
        path=cookie_path,
    )
    return {
        "status": "ok",
        "stream_url": ticket["stream_url"],
        "thumbnail_url": ticket["thumbnail_url"],
        "expires_in": ticket["expires_in"],
    }


@router.get("/calls/capabilities")
def call_capabilities(user=Depends(get_current_user)):
    return {
        "enabled": calls_service.enabled(),
        "video_enabled": calls_service.enabled("MESSENGER_VIDEO_CALLS_ENABLED"),
        "turn_enabled": calls_service.enabled("MESSENGER_TURN_ENABLED") and bool(str(__import__("os").getenv("MESSENGER_TURN_URLS", "")).strip()),
        "runtime_contract": calls_service.CALL_RUNTIME_CONTRACT_VERSION,
        "ring_timeout_seconds": calls_service.CALL_RING_TIMEOUT_SECONDS,
        "heartbeat_timeout_seconds": calls_service.CALL_HEARTBEAT_TIMEOUT_SECONDS,
    }


@router.get("/calls/readiness")
def call_readiness(user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_call_readiness", f"user:{user.id}", limit=30, window_seconds=60)
    return calls_service.readiness(user, probe=True)


@router.get("/calls/ice-config")
def call_ice_config(user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_ice_config", f"user:{user.id}", limit=30, window_seconds=60)
    return calls_service.ice_config(user)


@router.post("/conversations/{conversation_id}/calls")
def start_call(conversation_id: int, body: CallStart, user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_call_start", f"user:{user.id}", limit=20, window_seconds=60,
                           message="Слишком много попыток звонка. Повторите позже.")
    db = SessionLocal()
    created = False
    try:
        call, created = calls_service.start(db, conversation_id, user, body.call_type, body.client_call_id)
        db.commit()
    finally:
        db.close()
    if created:
        try:
            notification_service.queue_event(
                call["account_id"], f"internal-call:{call['call_id']}", "internal_call",
                ("Входящий групповой видеозвонок" if call.get("call_mode")=="group" and call["call_type"]=="video"
                 else "Входящий групповой звонок" if call.get("call_mode")=="group"
                 else "Входящий видеозвонок" if call["call_type"] == "video" else "Входящий звонок"),
                f"{str(getattr(user, 'email', '') or 'Сотрудник').split('@', 1)[0]} звонит вам",
                severity="high",
                url=f"/messenger?account_id={call['account_id']}&internal_conversation_id={call['conversation_id']}&call_id={call['call_id']}",
                payload={"target_user_ids": list(call.get("target_user_ids") or ([call["peer_user_id"]] if call.get("peer_user_id") else [])),
                         "internal_conversation_id": call["conversation_id"],
                         "call_id": call["call_id"], "call_type": call["call_type"],
                         "call_mode": call.get("call_mode") or "direct", "sender_user_id": user.id},
            )
        except Exception as exc:
            logger.warning("internal_messenger_call_push_failed call_id=%s error=%s", call["call_id"], type(exc).__name__)
    return {"status": "ok", "call": call, "created": created}


@router.get("/calls/incoming")
def incoming_calls(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        items = calls_service.incoming(db, account_id, user)
        db.commit()
        return {"status": "ok", "items": items}
    finally:
        db.close()


@router.get("/calls/history")
def call_history(account_id: str, limit: int = Query(default=100, ge=1, le=200), user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        items = calls_service.history(db, account_id, user, limit)
        db.commit()
        return {"status": "ok", "items": items}
    finally:
        db.close()


@router.get("/calls/{call_id}/participants")
def call_participants(call_id: UUID, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        result = calls_service.participants(db, call_id, user)
        db.commit()
        return result
    finally:
        db.close()


@router.get("/calls/{call_id}")
def get_call(call_id: UUID, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = calls_service._authorized_call(db, call_id, user)
        calls_service.expire_stale(db, row["account_id"])
        row = calls_service._authorized_call(db, call_id, user)
        db.commit()
        return {"status": "ok", "call": calls_service._public(row)}
    finally:
        db.close()


@router.post("/calls/{call_id}/transition")
def transition_call(call_id: UUID, body: CallTransition, user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_call_transition", f"user:{user.id}", limit=120, window_seconds=60)
    db = SessionLocal()
    try:
        call = calls_service.transition(db, call_id, user, body.action, body.session_id)
        db.commit()
        return {"status": "ok", "call": call}
    finally:
        db.close()


@router.post("/calls/{call_id}/signal")
def signal_call(call_id: UUID, body: CallSignal, user=Depends(get_current_user)):
    enforce_messenger_rate("messenger_call_signal", f"user:{user.id}", limit=600, window_seconds=60)
    db = SessionLocal()
    try:
        result = calls_service.signal(db, call_id, user, body.event_type, body.payload)
        db.commit()
        return result
    finally:
        db.close()


@router.get("/calls/{call_id}/events")
def call_events(call_id: UUID, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
                user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        result = calls_service.events(db, call_id, user, after_seq, limit)
        db.commit()
        return result
    finally:
        db.close()

