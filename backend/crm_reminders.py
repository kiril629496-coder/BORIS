# -*- coding: utf-8 -*-
"""Напоминания по задачам мини-CRM.

Запускается кроном каждые пять минут. Находит задачи, у которых подошло
время, и отправляет письмо владельцу аккаунта. Если у задачи стоит флаг
«напомнить клиенту» — пишет покупателю в Авито той же функцией, которой
пользуется AI-МОП.

Отметки об отправке обязательны: без них крон будет слать одно и то же
каждые пять минут — ровно так 06.08 автопостинг выпустил лавину старых
черновиков.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from sqlalchemy import text as sql
from app.db.session import SessionLocal

TYPE_LABEL = {
    "call": "Позвонить", "write": "Написать", "photo": "Отправить фото",
    "offer": "Отправить КП", "payment": "Запросить оплату",
    "delivery": "Уточнить доставку", "other": "Напоминание",
}


def _due_moment(due_date, due_time, before_min):
    """Момент, когда пора напомнить: время задачи минус запас."""
    hhmm = (due_time or "").strip()
    if not hhmm:
        return None
    try:
        h, m = hhmm.split(":")[0:2]
        at = datetime.combine(due_date, datetime.min.time()).replace(
            hour=int(h), minute=int(m))
    except Exception:
        return None
    return at - timedelta(minutes=int(before_min or 0))


def _mark_manager(db, tid):
    db.execute(sql("UPDATE crm_tasks SET notified_manager_at = now() WHERE id = :i"),
               {"i": tid})
    db.commit()


def _send_tg(chat, head, acc_name, comment):
    """Telegram-канал напоминания. Адрес берём из настроек АККАУНТА,
    а не из захардкоженного реестра — у каждого клиента он свой."""
    from app.telegram_bot import send_telegram_message
    text = ("\u23f0 <b>" + head + "</b>\n" + (acc_name or "") +
            (("\n" + comment.strip()) if comment else "") +
            "\n\nhttps://boris-ai.pro/messages")
    send_telegram_message(str(chat), text)


def run(dry=False):
    db = SessionLocal()
    sent_mail = sent_client = 0
    try:
        rows = db.execute(sql("""
            SELECT t.id, t.account_id, t.avito_chat_id, t.due_date, t.due_time,
                   t.task_type, t.title, t.comment, t.notify_before_min,
                   t.notify_client, t.notified_manager_at, t.notified_client_at,
                   a.name, a.owner_user_id, a.telegram_chat_id
              FROM crm_tasks t
              JOIN accounts a ON a.account_id = t.account_id
             WHERE t.status = 'planned'
               AND t.due_date = CURRENT_DATE
               AND COALESCE(t.due_time, '') <> ''
        """)).fetchall()

        now = datetime.now()
        for r in rows:
            (tid, acc_id, chat_id, due_date, due_time, ttype, title, comment,
             before, notify_client, done_mgr, done_cli, acc_name, owner_id,
             tg_chat) = r

            moment = _due_moment(due_date, due_time, before)
            if not moment or now < moment:
                continue

            label = TYPE_LABEL.get(ttype, "Напоминание")
            head = "%s — %s в %s" % (label, title or "задача", due_time)

            if not done_mgr:
                to = None
                if owner_id:
                    row = db.execute(sql("SELECT email FROM users WHERE id = :i"),
                                     {"i": owner_id}).fetchone()
                    to = row[0] if row else None
                if to:
                    body = ("%s\n\nАккаунт: %s\n%s\n\n"
                            "Открыть переписку: https://boris-ai.pro/messages") % (
                        head, acc_name or acc_id, (comment or "").strip())
                    if dry:
                        print("[dry] письмо ->", to, "|", head, flush=True)
                    else:
                        from app.services.email_queue import enqueue_email
                        enqueue_email(to, "BORIS: " + head, body)
                        _mark_manager(db, tid)
                    sent_mail += 1
                else:
                    print("[crm] у задачи", tid, "некому слать: нет почты владельца", flush=True)

            if tg_chat and not done_mgr:
                if dry:
                    print("[dry] telegram ->", tg_chat, "|", head, flush=True)
                else:
                    try:
                        _send_tg(tg_chat, head, acc_name, comment or "")
                        _mark_manager(db, tid)
                    except Exception as e:
                        print("[crm] telegram не ушёл:", str(e)[:120], flush=True)

            if notify_client and not done_cli and chat_id:
                text = ("Напоминаем: %s в %s. Если планы изменились — "
                        "напишите, перенесём." % (title or "встреча", due_time))
                if dry:
                    print("[dry] клиенту ->", chat_id, "|", text, flush=True)
                else:
                    try:
                        from app.api.messenger import send_message
                        send_message(acc_id, chat_id, text)
                        db.execute(sql("UPDATE crm_tasks SET notified_client_at = now() "
                                       "WHERE id = :i"), {"i": tid})
                        db.commit()
                    except Exception as e:
                        print("[crm] клиенту не ушло:", str(e)[:120], flush=True)
                sent_client += 1

        print("[crm] проверено задач: %d | писем: %d | клиентам: %d"
              % (len(rows), sent_mail, sent_client), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    run(dry=("dry" in sys.argv[1:]))
