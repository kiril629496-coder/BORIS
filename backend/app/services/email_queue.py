"""
Очередь отправки писем BORIS AI поверх PostgreSQL.

Задача: пользовательская операция не должна ломаться из-за временного сбоя SMTP.
enqueue_email() кладёт письмо в email_queue и (по умолчанию) сразу пробует отправить.
Не получилось — письмо остаётся в очереди, воркер добьёт его с растущей паузой.

Статусы: queued -> sending -> sent | failed -> dead (исчерпаны попытки).
Защита от двойной отправки: idempotency_key с уникальным индексом
плюс захват строк через FOR UPDATE SKIP LOCKED, как в app/api/tasks.py.

Секреты и тела писем в лог не пишутся.
"""

import json
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import email_service as _es

logger = logging.getLogger(__name__)

BACKOFF_STEPS = (60, 120, 300, 900, 1800)

# Постоянные отказы: повторять бессмысленно, письмо сразу уходит в dead.
PERMANENT_ERRORS = {
    "SMTPRecipientsRefused",
    "SMTPNotSupportedError",
    "SMTPAuthenticationError",
    "SMTPSenderRefused",
    "UnicodeEncodeError",
}


def is_permanent(reason: str) -> bool:
    """Постоянная ошибка: известный класс отказа либо SMTP-код 5xx."""
    name, _, code = (reason or "").partition(":")
    return name in PERMANENT_ERRORS or code[:1] == "5"
BATCH_SIZE = 20
PROVIDER = "yandex360"


def backoff_delay(attempts: int) -> int:
    """Пауза до следующей попытки в секундах. Растёт и упирается в потолок."""
    if attempts < 1:
        attempts = 1
    return BACKOFF_STEPS[min(attempts - 1, len(BACKOFF_STEPS) - 1)]


def _join(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return ", ".join(str(part) for part in value)


def _log_event(db, queue_id, event_type, message_id=None, details=None):
    db.execute(text(
        "INSERT INTO email_delivery_events "
        "(email_queue_id, event_type, provider, provider_message_id, details) "
        "VALUES (:qid, :ev, :pr, :mid, :det)"
    ), {"qid": queue_id, "ev": event_type, "pr": PROVIDER,
        "mid": message_id or None, "det": details or None})


def enqueue_email(to, subject, body, html=None, from_address=None, from_name=None,
                  reply_to=None, headers=None, source=None, idempotency_key=None,
                  max_attempts=5, send_now=True, ref_type=None, ref_id=None,
                  expires_at=None) -> dict:
    """
    Кладёт письмо в очередь. Возвращает {id, status, duplicate}.
    При idempotency_key повторный вызов не создаёт второе письмо.
    """
    db = SessionLocal()
    try:
        if idempotency_key:
            found = db.execute(text(
                "SELECT id, status FROM email_queue WHERE idempotency_key = :k"
            ), {"k": idempotency_key}).fetchone()
            if found:
                _log_event(db, found[0], "duplicate")
                db.commit()
                return {"id": found[0], "status": found[1], "duplicate": True}

        row = db.execute(text(
            "INSERT INTO email_queue (idempotency_key, source, to_addresses, subject, "
            "text_body, html_body, from_address, from_name, reply_to, headers, "
            "status, max_attempts, next_attempt_at, ref_type, ref_id, expires_at) "
            "VALUES (:k, :src, :to, :subj, :body, :html, :fa, :fn, :rt, :hd, "
            "'queued', :maxa, now(), :rtp, :rid, :exp) "
            "ON CONFLICT DO NOTHING RETURNING id"
        ), {"k": idempotency_key, "src": source, "to": _join(to), "subj": subject,
            "body": body, "html": html, "fa": from_address, "fn": from_name,
            "rt": reply_to, "hd": json.dumps(headers, ensure_ascii=False) if headers else None,
            "maxa": max_attempts, "rtp": ref_type, "rid": ref_id,
            "exp": expires_at}).fetchone()

        if row is None:
            db.rollback()
            return {"id": None, "status": "duplicate", "duplicate": True}

        queue_id = row[0]
        _log_event(db, queue_id, "queued")
        db.commit()
    except Exception as exc:
        db.rollback()
        db.close()
        logger.warning("email_queue: постановка не удалась (%s)", type(exc).__name__)
        return {"id": None, "status": "error", "duplicate": False}
    db.close()

    if send_now:
        sent = process_one(queue_id)
        return {"id": queue_id, "status": sent, "duplicate": False}
    return {"id": queue_id, "status": "queued", "duplicate": False}


def _deliver(db, row) -> str:
    """Одна попытка отправки уже захваченной строки. Возвращает новый статус."""
    (queue_id, to_addr, subject, body, html, fa, fn, rt, hd, attempts,
     max_attempts, ref_type, ref_id, expires_at) = row
    headers = None
    if hd:
        try:
            headers = json.loads(hd)
        except Exception:
            headers = None

    if ref_type == "verification":
        from app.services import email_queue_ref as _ref
        stop = _ref.verification_still_valid(db, ref_id, expires_at)
        if stop:
            db.execute(text(
                "UPDATE email_queue SET status=:st, updated_at=now(), "
                "next_attempt_at=NULL WHERE id=:id"), {"st": stop, "id": queue_id})
            _log_event(db, queue_id, stop, details="код уже недействителен")
            db.commit()
            logger.info("email#%s %s: код неактуален, SMTP не вызывался", queue_id, stop)
            return stop

    ok, reason, message_id = _es.send_email(
        to_addr, subject, body, html=html,
        from_address=fa, from_name=fn, reply_to=rt, headers=headers)

    attempts = (attempts or 0) + 1

    if ok:
        db.execute(text(
            "UPDATE email_queue SET status='sent', attempts=:a, sent_at=now(), "
            "updated_at=now(), provider_message_id=:mid, last_error=NULL, "
            "next_attempt_at=NULL WHERE id=:id"
        ), {"a": attempts, "mid": message_id, "id": queue_id})
        _log_event(db, queue_id, "sent", message_id)
        logger.info("email#%s отправлено", queue_id)
        db.commit()
        return "sent"

    permanent = is_permanent(reason)
    if permanent or attempts >= (max_attempts or 5):
        why = "постоянная ошибка" if permanent else "исчерпаны попытки"
        db.execute(text(
            "UPDATE email_queue SET status='dead', attempts=:a, updated_at=now(), "
            "last_error=:err, next_attempt_at=NULL WHERE id=:id"
        ), {"a": attempts, "err": reason[:255], "id": queue_id})
        _log_event(db, queue_id, "failed", details="%s: %s" % (why, reason))
        db.commit()
        logger.warning("email#%s dead: %s (%s)", queue_id, why, reason)
        return "dead"

    delay = backoff_delay(attempts)
    db.execute(text(
        "UPDATE email_queue SET status='queued', attempts=:a, updated_at=now(), "
        "last_error=:err, next_attempt_at=:nxt WHERE id=:id"
    ), {"a": attempts, "err": reason[:255], "id": queue_id,
        "nxt": datetime.now() + timedelta(seconds=delay)})
    _log_event(db, queue_id, "retrying", details="%s, пауза %d c" % (reason, delay))
    logger.warning("email#%s retry через %d c (%s)", queue_id, delay, reason)
    db.commit()
    return "retrying"


SELECT_COLS = ("id, to_addresses, subject, text_body, html_body, from_address, "
               "from_name, reply_to, headers, attempts, max_attempts, "
               "ref_type, ref_id, expires_at")


def _claim(db, queue_id=None, limit=1) -> list:
    """Захват строк через FOR UPDATE SKIP LOCKED — два воркера не возьмут одно письмо."""
    if queue_id is not None:
        rows = db.execute(text(
            "SELECT " + SELECT_COLS + " FROM email_queue WHERE id=:id AND status='queued' "
            "FOR UPDATE SKIP LOCKED"
        ), {"id": queue_id}).fetchall()
    else:
        rows = db.execute(text(
            "SELECT " + SELECT_COLS + " FROM email_queue WHERE status='queued' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
            "ORDER BY id ASC LIMIT :lim FOR UPDATE SKIP LOCKED"
        ), {"lim": limit}).fetchall()
    if rows:
        db.execute(text("UPDATE email_queue SET status='sending', updated_at=now() "
                        "WHERE id = ANY(:ids)"), {"ids": [r[0] for r in rows]})
    db.commit()
    return rows


def process_one(queue_id) -> str:
    db = SessionLocal()
    try:
        rows = _claim(db, queue_id=queue_id)
        if not rows:
            return "busy"
        return _deliver(db, rows[0])
    except Exception as exc:
        db.rollback()
        logger.warning("email#%s обработка не удалась (%s)", queue_id, type(exc).__name__)
        return "error"
    finally:
        db.close()


def process_batch(limit=BATCH_SIZE) -> dict:
    """Проход воркера. Возвращает счётчики по статусам."""
    result = {"sent": 0, "retrying": 0, "dead": 0, "error": 0}
    db = SessionLocal()
    try:
        rows = _claim(db, limit=limit)
        for row in rows:
            try:
                status = _deliver(db, row)
                result[status] = result.get(status, 0) + 1
            except Exception as exc:
                db.rollback()
                result["error"] += 1
                logger.warning("email#%s упало (%s)", row[0], type(exc).__name__)
    finally:
        db.close()
    return result


HEARTBEAT = "/root/BORIS/backend/.email_worker_heartbeat"


def touch_heartbeat():
    """Отметка живого воркера — по ней Health Monitor видит, что cron работает."""
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as fh:
            fh.write(datetime.now().isoformat())
    except Exception:
        pass


def queue_stats() -> dict:
    """Счётчики очереди для админки и мониторинга. Без ручного SQL."""
    stats = {"queued": 0, "sending": 0, "sent": 0, "failed": 0, "dead": 0,
             "retrying": 0, "duplicates": 0, "avg_send_time": None,
             "stuck": 0, "last_sent_at": None, "last_error": None,
             "worker_last_run": None}
    db = SessionLocal()
    try:
        for status, count in db.execute(text(
                "SELECT status, count(*) FROM email_queue GROUP BY status")).fetchall():
            stats[status] = count
        stats["retrying"] = db.execute(text(
            "SELECT count(*) FROM email_queue WHERE status='queued' AND attempts > 0")).scalar() or 0
        stats["stuck"] = db.execute(text(
            "SELECT count(*) FROM email_queue WHERE status IN ('queued','sending') "
            "AND created_at < now() - interval '30 minutes'")).scalar() or 0
        stats["duplicates"] = db.execute(text(
            "SELECT count(*) FROM email_delivery_events WHERE event_type='duplicate'")).scalar() or 0
        avg = db.execute(text(
            "SELECT avg(extract(epoch from (sent_at - created_at))) "
            "FROM email_queue WHERE sent_at IS NOT NULL")).scalar()
        stats["avg_send_time"] = round(float(avg), 2) if avg is not None else None
        last_sent = db.execute(text(
            "SELECT max(sent_at) FROM email_queue WHERE sent_at IS NOT NULL")).scalar()
        stats["last_sent_at"] = last_sent.isoformat() if last_sent else None
        err = db.execute(text(
            "SELECT last_error FROM email_queue WHERE last_error IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 1")).scalar()
        stats["last_error"] = err
    finally:
        db.close()
    try:
        stats["worker_last_run"] = datetime.fromtimestamp(os.path.getmtime(HEARTBEAT)).isoformat()
    except Exception:
        stats["worker_last_run"] = None
    stats["processing"] = stats.get("sending", 0)
    if stats["worker_last_run"]:
        age = (datetime.now() - datetime.fromisoformat(stats["worker_last_run"])).total_seconds()
        stats["worker_age_min"] = round(age / 60, 1)
        stats["worker_alive"] = age < 300
    else:
        stats["worker_age_min"] = None
        stats["worker_alive"] = False
    return stats
