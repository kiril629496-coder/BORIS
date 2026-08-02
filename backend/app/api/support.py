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


def _send_email(subject, body):
    """Письмо на служебный ящик поддержки. Отправка — через единый EmailService."""
    from app.services import email_service as _es
    to = os.environ.get("SUPPORT_EMAIL") or os.environ.get("SMTP_USER")
    if not to:
        return
    _es.send_email(to, subject, body)


def _notify_client(db, account_id: str, text_msg: str):
    """Ответ поддержки — в кабинет клиента и в его Telegram, если подключён."""
    if not account_id:
        return
    row = db.query(Storage).filter(Storage.account_id == account_id,
                                   Storage.key == "notifications").first()
    items = []
    if row and row.value:
        try:
            items = json.loads(row.value)
        except Exception:
            items = []
    items.append({"ts": datetime.utcnow().replace(microsecond=0).isoformat(),
                  "text": text_msg, "related_results": [], "read": False})
    val = json.dumps(items[-50:], ensure_ascii=False)
    if row:
        row.value = val
    else:
        db.add(Storage(account_id=account_id, key="notifications", value=val))
    try:
        chat = db.execute(text("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"),
                          {"a": account_id}).fetchone()
        if chat and chat[0]:
            send_telegram_message(chat[0], text_msg)
    except Exception as e:
        print("support client notify err", e)


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
        _send_email(f"Поддержка Борис — обращение №{tid}", body)
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
                except Exception:
                    pass
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
