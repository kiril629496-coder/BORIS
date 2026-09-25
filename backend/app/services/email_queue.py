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
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.services import email_service as _es
from app.services.exception_observability import observe_suppressed

logger = logging.getLogger(__name__)

BACKOFF_STEPS = (60, 120, 300, 900, 1800)
AUTH_BACKOFF_STEPS = (900, 1800, 3600, 7200, 14400)

# Recipient/sender/content failures are permanent for this message. Authentication
# is intentionally NOT here: providers may temporarily reject repeated logins or
# require a cooldown while credentials remain valid. An auth failure happens
# before DATA acceptance, so retrying the same queued message is duplicate-safe.
PERMANENT_ERRORS = {
    "SMTPRecipientsRefused",
    "SMTPNotSupportedError",
    "SMTPSenderRefused",
    "UnicodeEncodeError",
}


def is_authentication_error(reason: str) -> bool:
    name, _, _code = (reason or "").partition(":")
    return name == "SMTPAuthenticationError"


def is_permanent(reason: str) -> bool:
    """Permanent message failure; temporary SMTP auth rejection is recoverable."""
    name, _, code = (reason or "").partition(":")
    if is_authentication_error(reason):
        return False
    return name in PERMANENT_ERRORS or code[:1] == "5"
BATCH_SIZE = 20
PROVIDER = "yandex360"
OWNER_DAILY_CAP_GUARD = "OWNER_OUTREACH_DAILY_VOLUME_GUARD_V3"


class OwnerDailyVolumeDeferred(Exception):
    """Internal control-flow signal: DB safety cap deferred rows without SMTP."""

    def __init__(self, queue_ids):
        self.queue_ids = [int(x) for x in (queue_ids or [])]
        super().__init__("owner daily outreach cap reached")


def _is_owner_daily_cap_guard(exc: Exception) -> bool:
    """Trust only the exact PostgreSQL guard marker; never swallow other DB errors."""
    return OWNER_DAILY_CAP_GUARD in str(exc or "")


def _defer_owner_daily_cap(db, queue_ids) -> None:
    """Move capped owner outreach to next day's 09:00 Moscow window.

    This update does not change status/ref/content and therefore does not bypass
    the DB-final volume trigger. Attempts stay unchanged because SMTP never began.
    """
    ids=[int(x) for x in (queue_ids or [])]
    if not ids:
        return
    next_at=db.execute(text("""
      SELECT timezone(
        'UTC',
        (
          date_trunc('day', timezone('Europe/Moscow',NOW()))
          + interval '1 day 9 hours'
        ) AT TIME ZONE 'Europe/Moscow'
      )
    """)).scalar()
    db.execute(text("""
      UPDATE email_queue
         SET next_attempt_at=:nxt,updated_at=NOW()
       WHERE id=ANY(:ids) AND status='queued'
    """),{"nxt":next_at,"ids":ids})
    for qid in ids:
        _log_event(
            db,qid,"deferred_daily_cap",
            details="owner outreach daily cap reached; no SMTP attempt; deferred to next 09:00 Europe/Moscow"
        )
    db.commit()


def backoff_delay(attempts: int) -> int:
    """Normal transient-delivery backoff."""
    if attempts < 1:
        attempts = 1
    return BACKOFF_STEPS[min(attempts - 1, len(BACKOFF_STEPS) - 1)]


def retry_delay(reason: str, attempts: int) -> int:
    """Provider-auth cooldown is deliberately much slower than transport retry."""
    if attempts < 1:
        attempts = 1
    steps = AUTH_BACKOFF_STEPS if is_authentication_error(reason) else BACKOFF_STEPS
    return steps[min(attempts - 1, len(steps) - 1)]


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
                  expires_at=None, attachments=None, mailbox_id=None) -> dict:
    """
    Кладёт письмо в очередь. Возвращает {id, status, duplicate}.
    При idempotency_key повторный вызов не создаёт второе письмо.
    """
    # Ensure tracking schema BEFORE opening the queue write transaction.
    # This avoids DDL lock waits while the same process is holding a fresh
    # email_queue row that references the tracking table.
    if source == "prospect_campaign":
        from app.services import email_tracking as _tracking
        _tracking.ensure_schema()
    db = SessionLocal()
    try:
        if idempotency_key:
            found = db.execute(text(
                "SELECT id, status FROM email_queue WHERE idempotency_key = :k"
            ), {"k": idempotency_key}).fetchone()
            if found:
                # Mandatory open tracking applies to every not-yet-sent prospect
                # campaign row, including an idempotent retry that found an older
                # queued row created before the current worker process.
                if source == "prospect_campaign" and str(found[1]) in {"queued", "sending"}:
                    from app.services import email_tracking as _tracking
                    old_mail = db.execute(
                        text("SELECT html_body,text_body FROM email_queue WHERE id=:i"),
                        {"i": int(found[0])},
                    ).first()
                    tracked_html = _tracking.ensure_tracker_and_inject(
                        db, int(found[0]),
                        old_mail[0] if old_mail else html,
                        old_mail[1] if old_mail else body,
                    )
                    db.execute(
                        text("UPDATE email_queue SET html_body=:h,updated_at=NOW() WHERE id=:i"),
                        {"h": tracked_html, "i": int(found[0])},
                    )
                _log_event(db, found[0], "duplicate")
                db.commit()
                return {"id": found[0], "status": found[1], "duplicate": True}

        row = db.execute(text(
            "INSERT INTO email_queue (idempotency_key, source, to_addresses, subject, "
            "text_body, html_body, from_address, from_name, reply_to, headers, "
            "status, max_attempts, next_attempt_at, ref_type, ref_id, expires_at, attachments, mailbox_id) "
            "VALUES (:k, :src, :to, :subj, :body, :html, :fa, :fn, :rt, :hd, "
            "'queued', :maxa, now(), :rtp, :rid, :exp, CAST(:att AS JSONB), :mb) "
            "ON CONFLICT DO NOTHING RETURNING id"
        ), {"k": idempotency_key, "src": source, "to": _join(to), "subj": subject,
            "body": body, "html": html, "fa": from_address, "fn": from_name,
            "rt": reply_to, "hd": json.dumps(headers, ensure_ascii=False) if headers else None,
            "maxa": max_attempts, "rtp": ref_type, "rid": ref_id,
            "exp": expires_at, "att": json.dumps(attachments, ensure_ascii=False) if attachments else None, "mb": mailbox_id}).fetchone()

        if row is None:
            db.rollback()
            return {"id": None, "status": "duplicate", "duplicate": True}

        queue_id = row[0]
        if source == "prospect_campaign":
            # Hard rule: every prospect-campaign email gets a first-party
            # open-tracking pixel before it can leave the queue.
            from app.services import email_tracking as _tracking
            tracked_html = _tracking.ensure_tracker_and_inject(
                db, int(queue_id), html, body
            )
            db.execute(
                text("UPDATE email_queue SET html_body=:h,updated_at=NOW() WHERE id=:i"),
                {"h": tracked_html, "i": int(queue_id)},
            )
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


def _mailbox_health_before_smtp(db, mailbox_id):
    if not mailbox_id:
        return {'allowed':True}
    row=db.execute(text("""SELECT status,smtp_last_error,imap_last_error
      FROM client_mailboxes WHERE id=:m"""),{'m':int(mailbox_id)}).mappings().first()
    if not row or str(row.get('status') or '')!='active':
        return {'allowed':False,'reason':'mailbox_inactive'}
    if row.get('smtp_last_error'):
        return {'allowed':False,'reason':'mailbox_smtp_unhealthy','error':str(row.get('smtp_last_error') or '')[:160]}
    if row.get('imap_last_error'):
        return {'allowed':False,'reason':'mailbox_imap_unhealthy','error':str(row.get('imap_last_error') or '')[:160]}
    return {'allowed':True}


def _deliver(db, row) -> str:
    """Одна попытка отправки уже захваченной строки. Возвращает новый статус."""
    (queue_id, to_addr, subject, body, html, fa, fn, rt, hd, attempts,
     max_attempts, ref_type, ref_id, expires_at, attachments, mailbox_id) = row
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
        # Validity check is read-only evidence. End that transaction before SMTP/IMAP.
        db.commit()

    if attachments and isinstance(attachments, str):
        try: attachments = json.loads(attachments)
        except Exception: attachments = None

    if ref_type == "prospect_campaign_member":
        from app.services import email_tracking as _tracking
        _tracking.ensure_schema()
        if not _tracking.tracking_ready(db, int(queue_id), html):
            # DETECT -> SELF-HEAL -> VERIFY. A legacy/stale queued row may have
            # been created before tracking injection. Repair the SAME durable
            # queue row before SMTP instead of making the owner requeue it.
            try:
                healed_html = _tracking.ensure_tracker_and_inject(
                    db, int(queue_id), html, body
                )
                db.execute(
                    text("UPDATE email_queue SET html_body=:h,updated_at=NOW() WHERE id=:i AND status='sending'"),
                    {"h": healed_html, "i": int(queue_id)},
                )
                db.commit()
                html = healed_html
                if _tracking.tracking_ready(db, int(queue_id), html):
                    _log_event(
                        db, queue_id, "open_tracking_self_healed",
                        details="mandatory first-party tracker restored before SMTP",
                    )
                    db.commit()
                else:
                    raise RuntimeError("tracker_verify_failed")
            except Exception as exc:
                db.rollback()
                db.execute(text("""UPDATE email_queue
                  SET status='cancelled',
                      idempotency_key=LEFT(COALESCE(idempotency_key,'prospect')||':sup:'||id::text,160),
                      last_error='OPEN_TRACKING_REQUIRED',next_attempt_at=NULL,updated_at=NOW()
                  WHERE id=:id AND status='sending'"""), {"id": queue_id})
                _log_event(db, queue_id, "open_tracking_guard_blocked",
                           details=("self-heal failed: "+type(exc).__name__)[:500])
                db.commit()
                logger.warning("email#%s blocked: mandatory open tracking self-heal failed", queue_id)
                return "cancelled"
        try:
            member_id=int(str(ref_id or "0"))
        except Exception:
            member_id=0
        from app.services.prospect_campaigns import owner_queue_pre_send_guard
        guard=owner_queue_pre_send_guard(
            db,member_id,queue_id,subject,body,html,attachments or []
        )
        if not guard.get("allowed"):
            reason=str(guard.get("reason") or "content_guard_blocked")
            db.execute(text("""UPDATE email_queue
              SET status='cancelled',
                  idempotency_key=LEFT(COALESCE(idempotency_key,'prospect')||':sup:'||id::text,160),
                  last_error=:err,next_attempt_at=NULL,updated_at=NOW()
              WHERE id=:id AND status='sending'"""),
              {"id":queue_id,"err":("CONTENT_GUARD:"+reason)[:255]})
            _log_event(
                db,queue_id,"content_guard_blocked",
                details=json.dumps(guard,ensure_ascii=False,default=str)[:2000],
            )
            db.commit()
            logger.warning("email#%s content guard blocked before SMTP: %s",queue_id,reason)
            return "cancelled"
        # Release member/campaign locks before external SMTP/IMAP.
        db.commit()

    if mailbox_id:
        health=_mailbox_health_before_smtp(db,mailbox_id)
        if not health.get('allowed'):
            err=('MAILBOX_HEALTH_BLOCKED:'+str(health.get('reason') or 'unknown'))[:255]
            db.execute(text("""UPDATE email_queue
              SET status='queued',last_error=:e,next_attempt_at=NOW()+INTERVAL '60 minutes',updated_at=NOW()
              WHERE id=:i AND status='sending'"""),{'e':err,'i':queue_id})
            _log_event(db,queue_id,'mailbox_health_blocked',details=str(health)[:500])
            db.commit()
            return 'retrying'
        from app.services.client_mailboxes import send_outbound
        ok, reason, message_id = send_outbound(
            int(mailbox_id), to_addr, subject, body, html=html,
            reply_to=rt, headers=headers, attachments=attachments,from_name=fn)
    else:
        ok, reason, message_id = _es.send_email(
            to_addr, subject, body, html=html,
            from_address=fa, from_name=fn, reply_to=rt, headers=headers, attachments=attachments,
            tenant_scope=f"email_queue:{int(queue_id)}")

    attempts = (attempts or 0) + 1

    if not ok and str(reason or '').startswith("delivery_unknown:"):
        db.execute(text(
            "UPDATE email_queue SET status='delivery_unknown', attempts=:a, updated_at=now(), "
            "provider_message_id=COALESCE(:mid,provider_message_id), last_error=:err, next_attempt_at=NULL WHERE id=:id"
        ), {"a": attempts, "mid": message_id or None, "err": str(reason)[:255], "id": queue_id})
        _log_event(db, queue_id, "delivery_unknown", message_id, details=str(reason)[:255])
        db.commit()
        logger.warning("email#%s delivery_unknown: автоматический повтор запрещён", queue_id)
        return "delivery_unknown"

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

    # Authentication failure occurs before SMTP DATA acceptance, so it is safe
    # to retry the SAME durable queue row. Keep it as the single health probe and
    # use a long cooldown instead of killing the lead or enqueueing fresh leads.
    if is_authentication_error(reason):
        delay = retry_delay(reason, attempts)
        db.execute(text(
            "UPDATE email_queue SET status='queued', attempts=:a, updated_at=now(), "
            "last_error=:err, next_attempt_at=:nxt WHERE id=:id"
        ), {"a": attempts, "err": reason[:255], "id": queue_id,
            "nxt": datetime.now() + timedelta(seconds=delay)})
        _log_event(db, queue_id, "auth_backoff",
                   details="%s, cooldown %d c, attempt %d" % (reason, delay, attempts))
        db.commit()
        logger.warning("email#%s auth cooldown %d c (%s)", queue_id, delay, reason)
        return "retrying"

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

    delay = retry_delay(reason, attempts)
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
               "ref_type, ref_id, expires_at, attachments, mailbox_id")


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
        ids=[int(r[0]) for r in rows]
        try:
            db.execute(text("UPDATE email_queue SET status='sending', updated_at=now() "
                            "WHERE id = ANY(:ids)"), {"ids": ids})
            db.commit()
        except IntegrityError as exc:
            if not _is_owner_daily_cap_guard(exc):
                db.rollback()
                raise
            # EMAIL_OWNER_DAILY_CAP_DEFER_V1: the DB-final trigger is the
            # authoritative decision. SMTP has not started, so defer safely,
            # keep attempts unchanged and let the worker continue other mail.
            db.rollback()
            _defer_owner_daily_cap(db, ids)
            raise OwnerDailyVolumeDeferred(ids)
    else:
        db.commit()
    return rows


def recover_stale_sending(stale_minutes: int = 15, limit: int = 200) -> int:
    """Move stale SMTP executions to delivery_unknown without automatic resend.

    After a worker crash BORIS cannot know whether the remote SMTP server accepted
    the message. Retrying would risk duplicates, so ambiguity is terminal until
    an operator/provider reconciliation proves the outcome.
    """
    mins=max(5,min(int(stale_minutes),120)); lim=max(1,min(int(limit),2000))
    db=SessionLocal()
    try:
        rows=db.execute(text("""UPDATE email_queue SET status='delivery_unknown',
          last_error=concat_ws('; ',NULLIF(last_error,''),'stale sending lease: delivery outcome unknown'),
          next_attempt_at=NULL,updated_at=now()
          WHERE id IN (SELECT id FROM email_queue WHERE status='sending'
            AND updated_at<now()-(:m||' minutes')::interval ORDER BY updated_at,id LIMIT :l)
          RETURNING id"""),{'m':mins,'l':lim}).fetchall()
        for (qid,) in rows:
            _log_event(db,qid,'delivery_unknown',details='stale sending recovered without retry')
        db.commit(); return len(rows)
    finally: db.close()


def process_one(queue_id) -> str:
    db = SessionLocal()
    try:
        rows = _claim(db, queue_id=queue_id)
        if not rows:
            return "busy"
        return _deliver(db, rows[0])
    except OwnerDailyVolumeDeferred:
        return "daily_cap_deferred"
    except Exception as exc:
        db.rollback()
        logger.warning("email#%s обработка не удалась (%s)", queue_id, type(exc).__name__)
        return "error"
    finally:
        db.close()


def process_batch(limit=BATCH_SIZE) -> dict:
    """Process at most ``limit`` messages, claiming exactly one before SMTP.

    Claiming a whole batch as ``sending`` made untouched rows ambiguous after a
    worker crash. A one-row execution lease means only the message whose SMTP
    attempt actually started can ever become ``delivery_unknown``.
    """
    recovered_unknown = recover_stale_sending()
    result = {"sent": 0, "retrying": 0, "dead": 0, "error": 0,
              "daily_cap_deferred": 0,
              "delivery_unknown_recovered": recovered_unknown}
    cap=max(1,min(int(limit),500))
    for _ in range(cap):
        db = SessionLocal()
        row = None
        try:
            try:
                rows = _claim(db, limit=1)
            except OwnerDailyVolumeDeferred as deferred:
                result["daily_cap_deferred"] += len(deferred.queue_ids)
                continue
            if not rows:
                break
            row = rows[0]
            try:
                status = _deliver(db, row)
                result[status] = result.get(status, 0) + 1
            except Exception as exc:
                # The durable claim intentionally remains `sending`: remote SMTP
                # outcome may be ambiguous. Recovery later moves only this one
                # attempted message to delivery_unknown, never untouched rows.
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
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=535)


def queue_stats() -> dict:
    """Счётчики очереди для админки и мониторинга. Без ручного SQL."""
    stats = {"queued": 0, "sending": 0, "sent": 0, "failed": 0, "dead": 0,
             "delivery_unknown": 0, "retrying": 0, "duplicates": 0, "avg_send_time": None,
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
