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
                        print("[crm] telegram ->", tg_chat, "|", head, flush=True)
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


def digest(dry=False):
    """Одна сводка утром: что запланировано на сегодня и что просрочено.

    Отдельно от поштучных напоминаний: сигнал ко времени нужен, чтобы не
    пропустить встречу, а сводка — чтобы утром увидеть весь день целиком.
    """
    db = SessionLocal()
    try:
        rows = db.execute(sql("""
            SELECT a.owner_user_id, a.name, a.telegram_chat_id,
                   t.due_date, t.due_time, t.task_type, t.title
              FROM crm_tasks t
              JOIN accounts a ON a.account_id = t.account_id
             WHERE t.status = 'planned'
               AND t.due_date <= CURRENT_DATE
             ORDER BY a.owner_user_id, t.due_date, COALESCE(t.due_time, '99:99')
        """)).fetchall()

        from datetime import date as _d
        today = _d.today()
        by_owner = {}
        for r in rows:
            by_owner.setdefault(r[0], {"chat": r[2], "lines": []})
            if r[2] and not by_owner[r[0]]["chat"]:
                by_owner[r[0]]["chat"] = r[2]
            late = " (просрочено)" if r[3] < today else ""
            when = (r[4] or "").strip()
            by_owner[r[0]]["lines"].append(
                "\u2022 " + (when + " — " if when else "") + str(r[6] or "задача") +
                " · " + str(r[1] or "") + late)

        for owner, info in by_owner.items():
            if not info["lines"]:
                continue
            text = ("\U0001F4C5 <b>Задачи на сегодня</b>\n\n" +
                    "\n".join(info["lines"][:20]) +
                    "\n\nhttps://boris-ai.pro/messages")
            if dry:
                print("[dry digest] владелец", owner, "|", len(info["lines"]), "задач", flush=True)
                continue
            row = db.execute(sql("SELECT email FROM users WHERE id = :i"),
                             {"i": owner}).fetchone()
            if row and row[0]:
                from app.services.email_queue import enqueue_email
                plain = text.replace("<b>", "").replace("</b>", "")
                enqueue_email(row[0], "BORIS: задачи на сегодня", plain)
            if info["chat"]:
                try:
                    from app.telegram_bot import send_telegram_message
                    send_telegram_message(str(info["chat"]), text)
                except Exception as e:
                    print("[digest] telegram не ушёл:", str(e)[:120], flush=True)
            print("[digest] владельцу", owner, "отправлено задач:",
                  len(info["lines"]), flush=True)

        if not by_owner:
            print("[digest] задач на сегодня нет", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    if "digest" in sys.argv[1:]:
        digest(dry=("dry" in sys.argv[1:]))
    else:
        run(dry=("dry" in sys.argv[1:]))
