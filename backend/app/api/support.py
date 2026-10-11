import os, json, time, smtplib
from email.mime.text import MIMEText
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from app.telegram_bot import send_telegram_message
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api.auth import get_current_user
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/support", tags=["support"])

# антиспам ПО КЛИЕНТУ, а не общий на весь сервис:
# раньше одно обращение блокировало всех остальных на 5 секунд
_last: dict = {}
STATUSES = ["новое", "в работе", "решено"]

# Часы работы поддержки (московское время). Сервер живёт в UTC, поэтому +3.
WORK_FROM, WORK_TO, MSK_OFFSET = 9, 18, 3


def _work_now() -> bool:
    h = (datetime.utcnow().hour + MSK_OFFSET) % 24
    return WORK_FROM <= h < WORK_TO


def _hours_text() -> str:
    return "Поддержка работает ежедневно с %d:00 до %d:00 по Москве." % (WORK_FROM, WORK_TO)


class SupportMsg(BaseModel):
    name: str = ""
    contact: str = ""
    text: str
    account_id: str = ""
    email: str = ""


class ReplyMsg(BaseModel):
    ticket_id: int
    text: str


class StatusMsg(BaseModel):
    ticket_id: int
    status: str


def _send_email(subject, body, tenant_scope):
    """Письмо на служебный ящик поддержки. Отправка — через единый EmailService."""
    from app.services import email_service as _es
    to = os.environ.get("SUPPORT_EMAIL") or os.environ.get("SMTP_USER")
    if not to:
        return
    _es.send_email(to, subject, body, tenant_scope=tenant_scope)


def _notify_client(db, account_id: str, text_msg: str):
    """Ответ поддержки — атомарно в кабинет клиента и в Telegram, если подключён."""
    if not account_id:
        return
    from app.services.notification_store import append_notification
    append_notification(db,account_id,{"ts":datetime.utcnow().replace(microsecond=0).isoformat(),"text":text_msg,"related_results":[],"read":False},commit=True)
    try:
        chat=db.execute(text("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"),{"a":account_id}).fetchone()
        if chat and chat[0]:
            send_telegram_message(chat[0],text_msg)
    except Exception as e:
        print("support client notify err",e)


@router.post("/message")
def support_message(m: SupportMsg):
    if not m.text.strip():
        return {"status": "error", "detail": "empty"}
    who = (m.account_id or m.contact or m.email or "anon").strip()
    now = time.time()
    if now - _last.get(who, 0) < 5:
        return {"status": "error", "detail": "too_fast"}
    _last[who] = now
    if len(_last) > 5000:
        _last.clear()

    db = SessionLocal()
    try:
        tid = db.execute(text("""INSERT INTO support_tickets
            (account_id, user_email, name, contact, text, status)
            VALUES (:a,:e,:n,:c,:t,'новое') RETURNING id"""),
            {"a": m.account_id or None, "e": m.email or None,
             "n": m.name or None, "c": m.contact or None, "t": m.text}).fetchone()[0]
        db.commit()
    finally:
        db.close()

    body = (f"\U0001F4AC Обращение №{tid}\n\nОт: {m.name or '-'}\nКонтакт: {m.contact or '-'}\n"
            f"Аккаунт: {m.account_id or '-'}\n\n{m.text}")
    chat = os.environ.get("SUPPORT_CHAT_ID") or os.environ.get("DIRECTOR_CHAT_ID")
    if chat:
        try:
            send_telegram_message(chat, body)
        except Exception as e:
            print("support tg err", e)
    try:
        _send_email(f"Поддержка Борис — обращение №{tid}", body, tenant_scope=f"support:{m.account_id or tid}")
    except Exception as e:
        print("support mail err", e)
    if _work_now():
        tail = "Ответим в течение рабочего дня — ответ придёт сюда, в кабинет, и в Telegram."
    else:
        tail = _hours_text() + " Сейчас нерабочее время — ответим, как начнём день."
    return {"status": "ok", "номер": tid, "часы": _hours_text(), "рабочее_время": _work_now(),
            "сообщение": f"Обращение №{tid} принято. " + tail}


@router.get("/hours")
def support_hours():
    """График работы — чтобы кабинет показывал его до отправки обращения."""
    return {"status": "ok", "часы": _hours_text(), "рабочее_время": _work_now(),
            "с": WORK_FROM, "до": WORK_TO}


@router.get("/my")
def my_tickets(account_id: str = None, current=Depends(get_current_user)):
    """Свои обращения вместе с перепиской."""
    acc = account_id or getattr(current, "account_id", None)
    db = SessionLocal()
    try:
        rows = db.execute(text("""SELECT id, text, status, created_at, updated_at
                                  FROM support_tickets
                                  WHERE account_id=:a OR user_email=:e
                                  ORDER BY id DESC LIMIT 100"""),
                          {"a": acc, "e": getattr(current, "email", None)}).fetchall()
        ids = [r[0] for r in rows]
        reps = {}
        if ids:
            for r in db.execute(text("""SELECT ticket_id, author, text, created_at
                                        FROM support_replies WHERE ticket_id = ANY(:ids)
                                        ORDER BY id"""), {"ids": ids}).fetchall():
                reps.setdefault(r[0], []).append({
                    "кто": "поддержка" if r[1] == "support" else "вы",
                    "текст": r[2], "когда": r[3].isoformat() if r[3] else None})
        return {"status": "ok", "обращения": [{
            "номер": r[0], "текст": r[1], "статус": r[2],
            "создано": r[3].isoformat() if r[3] else None,
            "ответы": reps.get(r[0], [])} for r in rows]}
    finally:
        db.close()


@router.get("/all")
def all_tickets(status: str = None, current=Depends(get_current_user)):
    """Все обращения — только владельцу."""
    if getattr(current, "role", None) != "owner":
        return {"status": "error", "message": "Доступ запрещён"}
    db = SessionLocal()
    try:
        sql = """SELECT id, account_id, name, contact, text, status, created_at
                 FROM support_tickets"""
        params = {}
        if status:
            sql += " WHERE status=:s"; params["s"] = status
        sql += " ORDER BY CASE status WHEN 'новое' THEN 0 WHEN 'в работе' THEN 1 ELSE 2 END, id DESC LIMIT 300"
        rows = db.execute(text(sql), params).fetchall()
        counts = dict(db.execute(text("SELECT status, count(*) FROM support_tickets GROUP BY status")).fetchall())
        ids = [r[0] for r in rows]
        reps = {}
        if ids:
            for r in db.execute(text("""SELECT ticket_id, author, text, created_at
                                        FROM support_replies WHERE ticket_id = ANY(:ids)
                                        ORDER BY id"""), {"ids": ids}).fetchall():
                reps.setdefault(r[0], []).append({
                    "кто": r[1], "текст": r[2],
                    "когда": r[3].isoformat() if r[3] else None})
        return {"status": "ok", "по_статусам": counts, "обращения": [{
            "номер": r[0], "account_id": r[1], "имя": r[2], "контакт": r[3],
            "текст": r[4], "статус": r[5],
            "создано": r[6].isoformat() if r[6] else None,
            "ответы": reps.get(r[0], [])} for r in rows]}
    finally:
        db.close()


@router.post("/reply")
def support_reply(m: ReplyMsg, current=Depends(get_current_user)):
    """Ответ по обращению. Владелец отвечает клиенту, клиент дописывает своё."""
    if not m.text.strip():
        return {"status": "error", "message": "Пустой ответ"}
    is_owner = getattr(current, "role", None) == "owner"
    db = SessionLocal()
    try:
        t = db.execute(text("SELECT account_id, user_email FROM support_tickets WHERE id=:i"),
                       {"i": m.ticket_id}).fetchone()
        if not t:
            return {"status": "error", "message": "Обращение не найдено"}
        if not is_owner and t[0] != getattr(current, "account_id", None) \
                and t[1] != getattr(current, "email", None):
            return {"status": "error", "message": "Это не ваше обращение"}
        db.execute(text("""INSERT INTO support_replies (ticket_id, author, text)
                           VALUES (:i,:a,:t)"""),
                   {"i": m.ticket_id, "a": "support" if is_owner else "client", "t": m.text})
        db.execute(text("""UPDATE support_tickets SET updated_at=NOW(),
                           status = CASE WHEN :o THEN 'в работе' ELSE status END WHERE id=:i"""),
                   {"i": m.ticket_id, "o": is_owner})
        if is_owner:
            _notify_client(db, t[0], f"\U0001F4AC Ответ поддержки по обращению №{m.ticket_id}:\n\n{m.text}")
        else:
            chat = os.environ.get("SUPPORT_CHAT_ID") or os.environ.get("DIRECTOR_CHAT_ID")
            if chat:
                try:
                    send_telegram_message(chat, f"\U0001F4AC Клиент дописал по обращению №{m.ticket_id}:\n\n{m.text}")
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=212)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/status")
def support_status(m: StatusMsg, current=Depends(get_current_user)):
    if getattr(current, "role", None) != "owner":
        return {"status": "error", "message": "Доступ запрещён"}
    if m.status not in STATUSES:
        return {"status": "error", "message": "Неизвестный статус"}
    db = SessionLocal()
    try:
        t = db.execute(text("SELECT account_id FROM support_tickets WHERE id=:i"),
                       {"i": m.ticket_id}).fetchone()
        db.execute(text("UPDATE support_tickets SET status=:s, updated_at=NOW() WHERE id=:i"),
                   {"s": m.status, "i": m.ticket_id})
        if m.status == "решено" and t:
            _notify_client(db, t[0], f"\u2705 Обращение №{m.ticket_id} закрыто. Если вопрос остался — напишите ещё раз.")
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

# ==================== BORIS MESSENGER SUPPORT CENTER V1 ====================

from typing import Any, Optional as _SupportOptional
from fastapi import HTTPException, Query
from pydantic import Field
from app.services.platform_roles import PLATFORM_OWNER_ID, is_private_platform_owner

_SUPPORT_STATUS_TO_PUBLIC = {
    "новое": "NEW",
    "в работе": "IN_PROGRESS",
    "ждём клиента": "WAIT_CLIENT",
    "ждем клиента": "WAIT_CLIENT",
    "ждём команды": "WAIT_INTERNAL",
    "ждем команды": "WAIT_INTERNAL",
    "решено": "RESOLVED",
    "NEW": "NEW",
    "IN_PROGRESS": "IN_PROGRESS",
    "WAIT_CLIENT": "WAIT_CLIENT",
    "WAIT_INTERNAL": "WAIT_INTERNAL",
    "RESOLVED": "RESOLVED",
}
_SUPPORT_ALLOWED_STATUS = {"NEW", "IN_PROGRESS", "WAIT_CLIENT", "WAIT_INTERNAL", "RESOLVED"}
_SUPPORT_ALLOWED_PRIORITY = {"P0", "P1", "P2"}


def _support_public_status(value: str | None) -> str:
    return _SUPPORT_STATUS_TO_PUBLIC.get(str(value or "").strip(), "NEW")


def _support_require_account(db, user, account_id: str) -> None:
    from app.api.internal_messenger import _access as _messenger_access
    if not _messenger_access(db, user, account_id, "can_view"):
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")


def _support_context(body) -> dict:
    raw = dict(getattr(body, "context", {}) or {})
    allowed = {
        "current_url", "section", "browser", "device", "pwa", "frontend_build",
        "backend_build", "timezone", "recent_error_id", "module",
    }
    return {k: str(v)[:1000] for k, v in raw.items() if k in allowed and v is not None}


class SupportThreadOpen(BaseModel):
    account_id: str = Field(min_length=1, max_length=255)
    category: str = Field(default="Нужна помощь", max_length=64)
    module: str = Field(default="", max_length=64)
    priority: str = Field(default="P2", max_length=8)
    context: dict[str, Any] = Field(default_factory=dict)


class SupportStatusUpdate(BaseModel):
    status: str = Field(min_length=2, max_length=32)
    assigned_user_id: _SupportOptional[int] = None


@router.post("/thread/open")
def open_support_thread(body: SupportThreadOpen, current=Depends(get_current_user)):
    from app.api import internal_messenger as _im
    db = SessionLocal()
    try:
        account_id = str(body.account_id or "").strip()
        _support_require_account(db, current, account_id)
        priority = str(body.priority or "P2").upper()
        if priority not in _SUPPORT_ALLOWED_PRIORITY:
            priority = "P2"

        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                   {"k": f"support-thread:{account_id}"})
        ticket = db.execute(text("""
            SELECT id,internal_conversation_id,status
              FROM support_tickets
             WHERE account_id=:a
               AND internal_conversation_id IS NOT NULL
               AND COALESCE(status,'NEW') NOT IN ('решено','RESOLVED')
             ORDER BY updated_at DESC NULLS LAST,id DESC
             LIMIT 1
        """), {"a": account_id}).mappings().first()

        if ticket:
            ticket_id = int(ticket["id"])
            conversation_id = int(ticket["internal_conversation_id"])
        else:
            conversation_id = int(db.execute(text("""
                INSERT INTO internal_conversations(account_id,kind,title,created_by_user_id)
                VALUES (:a,'GROUP','Поддержка BORIS',:u)
                RETURNING id
            """), {"a": account_id, "u": int(current.id)}).scalar_one())
            member_ids = [int(current.id)]
            if int(PLATFORM_OWNER_ID) not in member_ids:
                member_ids.append(int(PLATFORM_OWNER_ID))
            for uid in member_ids:
                db.execute(text("""
                    INSERT INTO internal_conversation_members(conversation_id,user_id,role)
                    VALUES (:c,:u,:r)
                    ON CONFLICT (conversation_id,user_id) DO NOTHING
                """), {"c": conversation_id, "u": uid,
                       "r": "OWNER" if uid == int(PLATFORM_OWNER_ID) else "MEMBER"})
            context = _support_context(body)
            if body.module:
                context["module"] = str(body.module)[:64]
            ticket_id = int(db.execute(text("""
                INSERT INTO support_tickets(
                    account_id,user_email,name,contact,text,status,
                    internal_conversation_id,support_category,priority,module,context_json,
                    last_client_message_at
                )
                SELECT :a,:e,COALESCE(NULLIF(tm.name,''),u.email),NULL,'','NEW',
                       :c,:cat,:p,:m,CAST(:ctx AS JSONB),NULL
                  FROM users u
                  LEFT JOIN team_memberships tm
                    ON tm.user_id=u.id AND tm.account_id=:a
                 WHERE u.id=:u
                RETURNING id
            """), {
                "a": account_id,
                "e": str(getattr(current, "email", "") or "") or None,
                "c": conversation_id,
                "cat": str(body.category or "Нужна помощь")[:64],
                "p": priority,
                "m": str(body.module or "")[:64] or None,
                "ctx": json.dumps(context, ensure_ascii=False),
                "u": int(current.id),
            }).scalar_one())
            _im._record_system_event(db, conversation_id, int(current.id), "Чат поддержки BORIS открыт")
            _im.emit_messenger_event(
                db, account_id, "internal.conversation.created", "conversation", conversation_id,
                {"kind": "GROUP", "title": "Поддержка BORIS",
                 "support_ticket_id": ticket_id, "support": True},
            )

        db.execute(text("""
            INSERT INTO internal_conversation_members(conversation_id,user_id,role)
            VALUES (:c,:u,'MEMBER')
            ON CONFLICT (conversation_id,user_id) DO NOTHING
        """), {"c": conversation_id, "u": int(current.id)})
        db.execute(text("""
            INSERT INTO internal_conversation_members(conversation_id,user_id,role)
            VALUES (:c,:u,'OWNER')
            ON CONFLICT (conversation_id,user_id) DO NOTHING
        """), {"c": conversation_id, "u": int(PLATFORM_OWNER_ID)})
        db.execute(text("""
            UPDATE support_tickets
               SET support_category=COALESCE(NULLIF(:cat,''),support_category),
                   priority=COALESCE(NULLIF(:p,''),priority),
                   module=COALESCE(NULLIF(:m,''),module),
                   context_json=COALESCE(context_json,'{}'::jsonb) || CAST(:ctx AS JSONB),
                   updated_at=NOW()
             WHERE id=:i
        """), {
            "cat": str(body.category or "")[:64],
            "p": priority,
            "m": str(body.module or "")[:64],
            "ctx": json.dumps(_support_context(body), ensure_ascii=False),
            "i": ticket_id,
        })
        db.commit()
        return {
            "status": "ok",
            "ticket_id": ticket_id,
            "conversation_id": conversation_id,
            "title": "Поддержка BORIS",
            "ticket_status": _support_public_status(ticket["status"] if ticket else "NEW"),
            "priority": priority,
        }
    finally:
        db.close()


@router.get("/owner/inbox")
def owner_support_inbox(
    status: str = Query(default=""),
    priority: str = Query(default=""),
    q: str = Query(default="", max_length=160),
    limit: int = Query(default=100, ge=1, le=300),
    current=Depends(get_current_user),
):
    if not is_private_platform_owner(current):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT t.id,t.account_id,COALESCE(a.name,t.account_id) account_name,
                   t.status,t.priority,t.support_category,t.module,t.assigned_user_id,
                   t.internal_conversation_id,t.created_at,t.updated_at,
                   t.first_response_at,t.last_client_message_at,t.last_support_message_at,
                   COALESCE(last_msg.body,t.text,'') last_text,
                   last_msg.created_at last_message_at,
                   last_msg.sender_user_id last_sender_user_id,
                   COALESCE(unread.n,0) unread_count
              FROM support_tickets t
              LEFT JOIN accounts a ON a.account_id=t.account_id
              LEFT JOIN LATERAL (
                   SELECT im.body,im.created_at,im.sender_user_id,im.id
                     FROM internal_messages im
                    WHERE im.conversation_id=t.internal_conversation_id
                      AND im.deleted_at IS NULL
                      AND COALESCE(im.message_type,'text')<>'system'
                      AND COALESCE(im.body,'') NOT LIKE 'Системное событие:%'
                    ORDER BY im.id DESC LIMIT 1
              ) last_msg ON TRUE
              LEFT JOIN LATERAL (
                   SELECT count(*)::int n
                     FROM internal_messages im
                     LEFT JOIN internal_conversation_members m
                       ON m.conversation_id=im.conversation_id AND m.user_id=:owner
                    WHERE im.conversation_id=t.internal_conversation_id
                      AND im.deleted_at IS NULL
                      AND COALESCE(im.message_type,'text')<>'system'
                      AND COALESCE(im.body,'') NOT LIKE 'Системное событие:%'
                      AND im.sender_user_id<>:owner
                      AND im.id>COALESCE(m.last_read_message_id,0)
              ) unread ON TRUE
             WHERE (:s='' OR
                    CASE t.status
                      WHEN 'новое' THEN 'NEW'
                      WHEN 'в работе' THEN 'IN_PROGRESS'
                      WHEN 'ждём клиента' THEN 'WAIT_CLIENT'
                      WHEN 'ждем клиента' THEN 'WAIT_CLIENT'
                      WHEN 'ждём команды' THEN 'WAIT_INTERNAL'
                      WHEN 'ждем команды' THEN 'WAIT_INTERNAL'
                      WHEN 'решено' THEN 'RESOLVED'
                      ELSE t.status END=:s)
               AND (:p='' OR t.priority=:p)
               AND (:q='' OR COALESCE(a.name,'') ILIKE :like
                         OR COALESCE(t.text,'') ILIKE :like
                         OR COALESCE(last_msg.body,'') ILIKE :like
                         OR CAST(t.id AS TEXT)=:q)
             ORDER BY
               CASE t.priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
               CASE WHEN COALESCE(unread.n,0)>0 THEN 0 ELSE 1 END,
               COALESCE(last_msg.created_at,t.updated_at,t.created_at) DESC,
               t.id DESC
             LIMIT :limit
        """), {
            "owner": int(PLATFORM_OWNER_ID),
            "s": str(status or "").upper(),
            "p": str(priority or "").upper(),
            "q": str(q or "").strip(),
            "like": f"%{str(q or '').strip()}%",
            "limit": int(limit),
        }).mappings().all()
        items = []
        for rr in rows:
            public_status = _support_public_status(rr["status"])
            requires_owner = bool(
                rr["last_message_at"] is not None
                and public_status not in {"RESOLVED", "WAIT_CLIENT"}
                and int(rr["last_sender_user_id"] or 0) != int(PLATFORM_OWNER_ID)
            )
            items.append({
                "ticket_id": int(rr["id"]),
                "account_id": rr["account_id"],
                "account_name": rr["account_name"],
                "conversation_id": int(rr["internal_conversation_id"]) if rr["internal_conversation_id"] else None,
                "status": public_status,
                "priority": rr["priority"] or "P2",
                "category": rr["support_category"] or "Нужна помощь",
                "module": rr["module"] or "",
                "last_text": str(rr["last_text"] or "")[:600],
                "last_message_at": rr["last_message_at"].isoformat() if rr["last_message_at"] else None,
                "updated_at": rr["updated_at"].isoformat() if rr["updated_at"] else None,
                "unread_count": int(rr["unread_count"] or 0),
                "requires_owner": requires_owner,
                "assigned_user_id": rr["assigned_user_id"],
            })
        counts = {
            "total": len(items),
            "unread": sum(1 for x in items if x["unread_count"] > 0),
            "requires_owner": sum(1 for x in items if x["requires_owner"]),
            "p0": sum(1 for x in items if x["priority"] == "P0" and x["status"] != "RESOLVED"),
            "p1": sum(1 for x in items if x["priority"] == "P1" and x["status"] != "RESOLVED"),
        }
        return {"status": "ok", "counts": counts, "items": items}
    finally:
        db.close()


@router.post("/owner/tickets/{ticket_id}/status")
def owner_support_status(ticket_id: int, body: SupportStatusUpdate, current=Depends(get_current_user)):
    if not is_private_platform_owner(current):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    new_status = str(body.status or "").upper()
    if new_status not in _SUPPORT_ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="Неизвестный статус поддержки")
    db = SessionLocal()
    notify = None
    try:
        row = db.execute(text("""
            SELECT id,account_id,internal_conversation_id,status
              FROM support_tickets
             WHERE id=:i
             FOR UPDATE
        """), {"i": int(ticket_id)}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        old_status = _support_public_status(row["status"])
        db.execute(text("""
            UPDATE support_tickets
               SET status=:s,
                   assigned_user_id=COALESCE(:assigned,assigned_user_id),
                   resolved_at=CASE WHEN :s='RESOLVED' THEN COALESCE(resolved_at,now()) ELSE NULL END,
                   updated_at=now()
             WHERE id=:i
        """), {"s": new_status, "assigned": body.assigned_user_id, "i": int(ticket_id)})

        if new_status == "RESOLVED" and old_status != "RESOLVED" and row["internal_conversation_id"]:
            from app.api import internal_messenger as _im
            conversation_id = int(row["internal_conversation_id"])
            message_id = _im._record_system_event(
                db, conversation_id, int(current.id), "Обращение решено"
            )
            _im.emit_messenger_event(
                db, row["account_id"], "internal.message.created", "message", message_id,
                {"conversation_id": conversation_id, "message_type": "system", "support": True},
            )
            targets = [int(x[0]) for x in db.execute(text("""
                SELECT user_id FROM internal_conversation_members
                 WHERE conversation_id=:c AND user_id<>:owner
            """), {"c": conversation_id, "owner": int(PLATFORM_OWNER_ID)}).all()]
            notify = {
                "account_id": row["account_id"],
                "event_key": f"support-resolved:{int(ticket_id)}",
                "title": "Поддержка BORIS · вопрос решён",
                "body": "Обращение отмечено как решённое.",
                "url": f"/messenger?section=chats&account_id={row['account_id']}&internal_conversation_id={conversation_id}&support_ticket_id={int(ticket_id)}",
                "payload": {
                    "target_user_ids": targets,
                    "support_ticket_id": int(ticket_id),
                    "internal_conversation_id": conversation_id,
                    "support": True,
                    "resolved": True,
                },
            }
        db.commit()
    finally:
        db.close()

    if notify:
        try:
            from app.services import lead_notifications as _notifications
            _notifications.queue_event(
                notify["account_id"], notify["event_key"], "support_resolved",
                notify["title"], notify["body"], severity="normal",
                url=notify["url"], payload=notify["payload"],
            )
        except Exception as exc:
            observe_suppressed(__name__, exc, line=548)

    return {"status": "ok", "ticket_id": int(ticket_id), "ticket_status": new_status}




@router.get("/owner/report")
def owner_support_report(
    days: int = Query(default=7, ge=1, le=90),
    current=Depends(get_current_user),
):
    """Owner support SLA/report summary for the selected period."""
    if not is_private_platform_owner(current):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    db = SessionLocal()
    try:
        summary = db.execute(text("""
            SELECT
              count(*)::int tickets,
              count(*) FILTER (
                WHERE resolved_at IS NOT NULL OR status IN ('RESOLVED','решено')
              )::int resolved,
              count(*) FILTER (WHERE priority='P0')::int p0,
              count(*) FILTER (WHERE priority='P1')::int p1,
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM (first_response_at-created_at))
              ) FILTER (WHERE first_response_at IS NOT NULL) median_first_response_sec,
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM (resolved_at-created_at))
              ) FILTER (WHERE resolved_at IS NOT NULL) median_resolution_sec
            FROM support_tickets
            WHERE created_at>=now()-make_interval(days=>:days)
        """), {"days": int(days)}).mappings().one()

        repeat_accounts = int(db.execute(text("""
            SELECT count(*) FROM (
              SELECT account_id
                FROM support_tickets
               WHERE created_at>=now()-make_interval(days=>:days)
                 AND COALESCE(account_id,'')<>''
               GROUP BY account_id
              HAVING count(*)>1
            ) x
        """), {"days": int(days)}).scalar_one() or 0)

        clients = [dict(r) for r in db.execute(text("""
            SELECT t.account_id,COALESCE(a.name,t.account_id,'Без аккаунта') account_name,
                   count(*)::int tickets,
                   count(*) FILTER (
                     WHERE t.resolved_at IS NOT NULL OR t.status IN ('RESOLVED','решено')
                   )::int resolved,
                   count(*) FILTER (WHERE t.priority IN ('P0','P1'))::int urgent,
                   max(t.updated_at) last_at
              FROM support_tickets t
              LEFT JOIN accounts a ON a.account_id=t.account_id
             WHERE t.created_at>=now()-make_interval(days=>:days)
             GROUP BY t.account_id,COALESCE(a.name,t.account_id,'Без аккаунта')
             ORDER BY count(*) DESC,max(t.updated_at) DESC
             LIMIT 30
        """), {"days": int(days)}).mappings().all()]
        for item in clients:
            if item.get("last_at"):
                item["last_at"] = item["last_at"].isoformat()

        return {
            "status": "ok",
            "days": int(days),
            "tickets": int(summary["tickets"] or 0),
            "resolved": int(summary["resolved"] or 0),
            "p0": int(summary["p0"] or 0),
            "p1": int(summary["p1"] or 0),
            "repeat_accounts": repeat_accounts,
            "median_first_response_sec": (
                round(float(summary["median_first_response_sec"]), 1)
                if summary["median_first_response_sec"] is not None else None
            ),
            "median_resolution_sec": (
                round(float(summary["median_resolution_sec"]), 1)
                if summary["median_resolution_sec"] is not None else None
            ),
            "clients": clients,
        }
    finally:
        db.close()


@router.get("/owner/tickets/{ticket_id}/diagnostics")
def owner_support_diagnostics(ticket_id: int, current=Depends(get_current_user)):
    """Safe owner diagnostics for one support ticket. No secrets, no external writes."""
    if not is_private_platform_owner(current):
        raise HTTPException(status_code=403, detail="Только для владельца платформы")
    db = SessionLocal()
    try:
        ticket = db.execute(text("""
            SELECT t.id,t.account_id,t.module,t.priority,t.context_json,
                   COALESCE(a.name,t.account_id) account_name
              FROM support_tickets t
              LEFT JOIN accounts a ON a.account_id=t.account_id
             WHERE t.id=:i
        """), {"i": int(ticket_id)}).mappings().first()
        if not ticket:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        account_id = str(ticket["account_id"] or "").strip()
        if not account_id:
            return {
                "status": "ok", "ticket_id": int(ticket_id),
                "account": {"account_id": "", "name": ticket["account_name"] or "Без аккаунта"},
                "diagnostics": {"state": "legacy_ticket", "message": "Старое обращение не привязано к клиентскому аккаунту."},
            }

        def _storage_json(key: str):
            raw = db.execute(text("""
                SELECT value FROM storage
                 WHERE account_id=:a AND key=:k
                 ORDER BY id DESC LIMIT 1
            """), {"a": account_id, "k": key}).scalar()
            if not raw:
                return {}
            try:
                value = json.loads(raw) if isinstance(raw, str) else raw
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}

        mop = db.execute(text("""
            SELECT
              count(*) FILTER (WHERE created_at>=date_trunc('day',now())) drafts_today,
              count(*) FILTER (WHERE sent_at>=date_trunc('day',now())) sent_today,
              count(*) FILTER (WHERE status='human_required') human_required_now,
              count(*) FILTER (WHERE status='waiting_external') waiting_external_now,
              count(*) FILTER (WHERE status IN ('pending','analyzing')) pending_now,
              count(*) FILTER (WHERE send_error IS NOT NULL AND created_at>=now()-interval '24 hours') send_errors_24h,
              max(sent_at) last_sent_at,
              max(created_at) last_incoming_at
            FROM mop_drafts WHERE account_id=:a
        """), {"a": account_id}).mappings().one()

        mop_reasons = [
            {"reason": str(r[0] or "")[:240], "count": int(r[1] or 0)}
            for r in db.execute(text("""
                SELECT COALESCE(send_error,''),count(*)
                  FROM mop_drafts
                 WHERE account_id=:a
                   AND status IN ('human_required','waiting_external')
                 GROUP BY COALESCE(send_error,'')
                 ORDER BY count(*) DESC
                 LIMIT 8
            """), {"a": account_id}).all()
        ]

        jobs = [dict(r) for r in db.execute(text("""
            SELECT id,kind,status,progress_done,progress_total,attempts,error_text,
                   scheduled_at,started_at,finished_at,updated_at
              FROM background_jobs
             WHERE account_id=:a
             ORDER BY updated_at DESC
             LIMIT 12
        """), {"a": account_id}).mappings().all()]
        for job in jobs:
            job["error_text"] = str(job.get("error_text") or "")[:500]
            for key in ("scheduled_at","started_at","finished_at","updated_at"):
                if job.get(key):
                    job[key] = job[key].isoformat()

        controls = [dict(r) for r in db.execute(text("""
            SELECT module,status,block_reason,error_code,error_message,started_at,finished_at,updated_at
              FROM control_executions
             WHERE account_id=:a
             ORDER BY updated_at DESC
             LIMIT 12
        """), {"a": account_id}).mappings().all()]
        for item in controls:
            item["block_reason"] = str(item.get("block_reason") or "")[:400]
            item["error_message"] = str(item.get("error_message") or "")[:400]
            for key in ("started_at","finished_at","updated_at"):
                if item.get(key):
                    item[key] = item[key].isoformat()

        notifications = [dict(r) for r in db.execute(text("""
            SELECT event_type,status,attempts,last_error,created_at,sent_at
              FROM lead_notification_outbox
             WHERE account_id=:a
               AND created_at>=now()-interval '24 hours'
             ORDER BY id DESC
             LIMIT 12
        """), {"a": account_id}).mappings().all()]
        for item in notifications:
            item["last_error"] = str(item.get("last_error") or "")[:400]
            for key in ("created_at","sent_at"):
                if item.get(key):
                    item[key] = item[key].isoformat()

        campaigns = [dict(r) for r in db.execute(text("""
            SELECT id,name,status,scenario_type,updated_at
              FROM campaigns
             WHERE default_account_id=:a
             ORDER BY updated_at DESC,id DESC
             LIMIT 8
        """), {"a": account_id}).mappings().all()]
        for item in campaigns:
            if item.get("updated_at"):
                item["updated_at"] = item["updated_at"].isoformat()

        kpi = _storage_json("kpi_settings")
        autopilot = _storage_json("autopilot_settings")
        context = ticket["context_json"] if isinstance(ticket["context_json"], dict) else {}

        return {
            "status": "ok",
            "ticket_id": int(ticket_id),
            "account": {"account_id": account_id, "name": ticket["account_name"] or account_id},
            "ticket": {
                "module": ticket["module"] or "",
                "priority": ticket["priority"] or "P2",
                "context": {k: context.get(k) for k in (
                    "current_url","section","browser","device","pwa","frontend_build",
                    "backend_build","timezone","recent_error_id","module"
                ) if context.get(k) is not None},
            },
            "runtime": {
                "backend_build": os.environ.get("BORIS_RELEASE_BUILD_ID") or "",
                "frontend_build": context.get("frontend_build") or "",
            },
            "kpi": {
                "target_leads_per_day": kpi.get("target_leads_per_day"),
                "red_cpl_rub": kpi.get("max_cost_per_lead_rub") or kpi.get("red_cpl_rub"),
                "daily_budget_limit_rub": kpi.get("daily_budget_limit_rub"),
                "hard_max_bid_rub": kpi.get("hard_max_bid_rub"),
            },
            "autopilot": {
                "mode": autopilot.get("mode"),
                "full_ai_marketer": autopilot.get("full_ai_marketer"),
                "trial_until": autopilot.get("full_ai_marketer_trial_until"),
            },
            "mop": {
                "drafts_today": int(mop["drafts_today"] or 0),
                "sent_today": int(mop["sent_today"] or 0),
                "human_required_now": int(mop["human_required_now"] or 0),
                "waiting_external_now": int(mop["waiting_external_now"] or 0),
                "pending_now": int(mop["pending_now"] or 0),
                "send_errors_24h": int(mop["send_errors_24h"] or 0),
                "last_sent_at": mop["last_sent_at"].isoformat() if mop["last_sent_at"] else None,
                "last_incoming_at": mop["last_incoming_at"].isoformat() if mop["last_incoming_at"] else None,
                "reasons": mop_reasons,
            },
            "jobs": jobs,
            "controls": controls,
            "notifications": notifications,
            "campaigns": campaigns,
        }
    finally:
        db.close()


def support_messenger_sync(db, conversation_id: int, sender_user_id: int, text_value: str, message_id: int) -> dict | None:
    ticket = db.execute(text("""
        SELECT t.id,t.account_id,t.status,t.priority,COALESCE(a.name,t.account_id) account_name
          FROM support_tickets t
          LEFT JOIN accounts a ON a.account_id=t.account_id
         WHERE t.internal_conversation_id=:c
         LIMIT 1
    """), {"c": int(conversation_id)}).mappings().first()
    if not ticket:
        return None
    is_support = int(sender_user_id) == int(PLATFORM_OWNER_ID)
    if is_support:
        db.execute(text("""
            UPDATE support_tickets
               SET status='WAIT_CLIENT',
                   first_response_at=COALESCE(first_response_at,now()),
                   last_support_message_at=now(),updated_at=now()
             WHERE id=:i
        """), {"i": int(ticket["id"])})
        targets = [int(x[0]) for x in db.execute(text("""
            SELECT user_id FROM internal_conversation_members
             WHERE conversation_id=:c AND user_id<>:owner
        """), {"c": int(conversation_id), "owner": int(PLATFORM_OWNER_ID)}).all()]
        return {
            "event_type": "support_reply",
            "event_key": f"support-reply:{int(ticket['id'])}:{int(message_id)}",
            "title": "Поддержка BORIS ответила",
            "body": str(text_value or "Новое сообщение поддержки")[:500],
            "severity": "normal",
            "url": f"/messenger?section=chats&account_id={ticket['account_id']}&internal_conversation_id={int(conversation_id)}&support_ticket_id={int(ticket['id'])}",
            "payload": {
                "target_user_ids": targets,
                "support_ticket_id": int(ticket["id"]),
                "internal_conversation_id": int(conversation_id),
                "support": True,
            },
        }
    db.execute(text("""
        UPDATE support_tickets
           SET status='NEW',
               text=CASE WHEN COALESCE(btrim(text),'')='' THEN :body ELSE text END,
               last_client_message_at=now(),updated_at=now()
         WHERE id=:i
    """), {"i": int(ticket["id"]), "body": str(text_value or "")[:10000]})
    return {
        "event_type": "support_ticket",
        "event_key": f"support-ticket:{int(ticket['id'])}:{int(message_id)}",
        "title": f"{ticket['account_name']} · новый вопрос",
        "body": str(text_value or "Новое сообщение в поддержку")[:500],
        "severity": "high" if str(ticket["priority"] or "P2") in {"P0", "P1"} else "normal",
        "url": f"/messenger?section=support&account_id={ticket['account_id']}&internal_conversation_id={int(conversation_id)}&support_ticket_id={int(ticket['id'])}",
        "payload": {
            "global_target_user_ids": [int(PLATFORM_OWNER_ID)],
            "support_ticket_id": int(ticket["id"]),
            "internal_conversation_id": int(conversation_id),
            "support": True,
            "account_id": ticket["account_id"],
        },
    }
