import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
ES = os.path.join(BASE, "app/services/email_service.py")
EQ = os.path.join(BASE, "app/services/email_queue.py")
RUN = os.path.join(BASE, "email_queue_runner.py")
MARK = "PERMANENT_ERRORS"
stamp = str(int(time.time()))

ES_OLD = '''    except Exception as exc:
        logger.warning("email_service: отправка не удалась (%s)", type(exc).__name__)
        return False, type(exc).__name__, message_id'''
ES_NEW = '''    except Exception as exc:
        code = getattr(exc, "smtp_code", None)
        reason = type(exc).__name__ if code is None else "%s:%s" % (type(exc).__name__, code)
        logger.warning("email_service: отправка не удалась (%s)", reason)
        return False, reason, message_id'''

EQ_OLD_STEPS = "BACKOFF_STEPS = (60, 120, 300, 900, 3600)"
EQ_NEW_STEPS = '''BACKOFF_STEPS = (60, 120, 300, 900, 1800)

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
    return name in PERMANENT_ERRORS or code[:1] == "5"'''

EQ_OLD_DUP = '''            if found:
                return {"id": found[0], "status": found[1], "duplicate": True}'''
EQ_NEW_DUP = '''            if found:
                _log_event(db, found[0], "duplicate")
                db.commit()
                return {"id": found[0], "status": found[1], "duplicate": True}'''

EQ_OLD_DEAD = '''    if attempts >= (max_attempts or 5):
        db.execute(text(
            "UPDATE email_queue SET status='dead', attempts=:a, updated_at=now(), "
            "last_error=:err, next_attempt_at=NULL WHERE id=:id"
        ), {"a": attempts, "err": reason[:255], "id": queue_id})
        _log_event(db, queue_id, "failed", details=reason)
        db.commit()
        logger.warning("email_queue: письмо %s исчерпало попытки (%s)", queue_id, reason)
        return "dead"'''
EQ_NEW_DEAD = '''    permanent = is_permanent(reason)
    if permanent or attempts >= (max_attempts or 5):
        why = "постоянная ошибка" if permanent else "исчерпаны попытки"
        db.execute(text(
            "UPDATE email_queue SET status='dead', attempts=:a, updated_at=now(), "
            "last_error=:err, next_attempt_at=NULL WHERE id=:id"
        ), {"a": attempts, "err": reason[:255], "id": queue_id})
        _log_event(db, queue_id, "failed", details="%s: %s" % (why, reason))
        db.commit()
        logger.warning("email#%s dead: %s (%s)", queue_id, why, reason)
        return "dead"'''

EQ_LOG_1 = ('        logger.warning("email_queue: обработка %s не удалась (%s)", queue_id, type(exc).__name__)',
            '        logger.warning("email#%s обработка не удалась (%s)", queue_id, type(exc).__name__)')
EQ_LOG_2 = ('                logger.warning("email_queue: строка %s упала (%s)", row[0], type(exc).__name__)',
            '                logger.warning("email#%s упало (%s)", row[0], type(exc).__name__)')
EQ_LOG_4 = ('        _log_event(db, queue_id, "sent", message_id)',
            '        _log_event(db, queue_id, "sent", message_id)\n        logger.info("email#%s отправлено", queue_id)')
EQ_LOG_5 = ('    _log_event(db, queue_id, "retrying", details="%s, пауза %d c" % (reason, delay))',
            '    _log_event(db, queue_id, "retrying", details="%s, пауза %d c" % (reason, delay))\n    logger.warning("email#%s retry через %d c (%s)", queue_id, delay, reason)')

EQ_STATS = '''

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
    return stats
'''

RUN_OLD = '''    from app.services import email_queue as queue
    result = queue.process_batch()'''
RUN_NEW = '''    from app.services import email_queue as queue
    queue.touch_heartbeat()
    result = queue.process_batch()'''


def patch(path, pairs, append=None):
    name = os.path.basename(path)
    src = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if src.count(old) != 1:
            print("  %-22s ЯКОРЬ НЕ УНИКАЛЕН (%d): %s" % (name, src.count(old), old[:40]))
            return False
        src = src.replace(old, new)
    if append:
        src = src.rstrip("\n") + "\n" + append
    shutil.copy2(path, path + ".before_b3_" + stamp)
    open(path, "w", encoding="utf-8").write(src)
    try:
        py_compile.compile(path, doraise=True)
        print("  %-22s ok" % name)
        return True
    except Exception as exc:
        shutil.copy2(path + ".before_b3_" + stamp, path)
        print("  %-22s СИНТАКСИС СЛОМАН, ОТКАЧЕН (%s)" % (name, type(exc).__name__))
        return False


if MARK in open(EQ, encoding="utf-8").read():
    print("  патч B3 уже применён, пропуск")
else:
    ok = patch(ES, [(ES_OLD, ES_NEW)])
    ok = patch(EQ, [(EQ_OLD_STEPS, EQ_NEW_STEPS), (EQ_OLD_DUP, EQ_NEW_DUP),
                    (EQ_OLD_DEAD, EQ_NEW_DEAD), EQ_LOG_1, EQ_LOG_2, EQ_LOG_4, EQ_LOG_5],
              append=EQ_STATS) and ok
    ok = patch(RUN, [(RUN_OLD, RUN_NEW)]) and ok
    print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
