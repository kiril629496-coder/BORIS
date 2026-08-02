import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
EQ = os.path.join(BASE, "app/services/email_queue.py")
ML = os.path.join(BASE, "app/services/mailer.py")
AU = os.path.join(BASE, "app/api/auth.py")
stamp = str(int(time.time()))
MARK = "ref_type"

EQ_PAIRS = [
("""                  max_attempts=5, send_now=True) -> dict:""",
 """                  max_attempts=5, send_now=True, ref_type=None, ref_id=None,
                  expires_at=None) -> dict:"""),
("""            "status, max_attempts, next_attempt_at) "
            "VALUES (:k, :src, :to, :subj, :body, :html, :fa, :fn, :rt, :hd, "
            "'queued', :maxa, now()) \"""",
 """            "status, max_attempts, next_attempt_at, ref_type, ref_id, expires_at) "
            "VALUES (:k, :src, :to, :subj, :body, :html, :fa, :fn, :rt, :hd, "
            "'queued', :maxa, now(), :rtp, :rid, :exp) \""""),
("""            "maxa": max_attempts}).fetchone()""",
 """            "maxa": max_attempts, "rtp": ref_type, "rid": ref_id,
            "exp": expires_at}).fetchone()"""),
("""    queue_id, to_addr, subject, body, html, fa, fn, rt, hd, attempts, max_attempts = row""",
 """    (queue_id, to_addr, subject, body, html, fa, fn, rt, hd, attempts,
     max_attempts, ref_type, ref_id, expires_at) = row"""),
("""    ok, reason, message_id = _es.send_email(""",
 """    if ref_type == "verification":
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

    ok, reason, message_id = _es.send_email("""),
("""               "from_name, reply_to, headers, attempts, max_attempts")""",
 """               "from_name, reply_to, headers, attempts, max_attempts, "
               "ref_type, ref_id, expires_at")"""),
]

ML_PAIRS = [
("""def send_verification_code(to: str, code: str) -> tuple:
    \"\"\"Письмо с кодом подтверждения. Код в лог не пишется.\"\"\"""",
 """def send_verification_code(to: str, code: str, verification_id: str = None) -> tuple:
    \"\"\"
    Письмо с кодом подтверждения. Код в лог не пишется.
    С verification_id письмо идёт через очередь с привязкой к записи:
    прежние ожидающие письма гасятся, устаревший код не доставляется.
    Без verification_id поведение прежнее — прямая отправка.
    \"\"\""""),
("""    ).format(code=code)
    return send_mail(to, subject, body)""",
 """    ).format(code=code)

    if not verification_id:
        return send_mail(to, subject, body)

    from app.services import email_queue_ref as _ref
    try:
        _ref.cancel_pending_verification(to)
        res = _ref.enqueue_verification(to, subject, body, verification_id)
    except Exception as exc:
        logger.warning("mailer: очередь недоступна (%s)", type(exc).__name__)
        return False, "queue_error"

    status = res.get("status")
    if res.get("duplicate"):
        return True, "duplicate"
    if status == "sent":
        return True, "sent"
    if status in ("queued", "retrying"):
        return True, "queued"
    if status in ("cancelled", "expired"):
        return True, "cancelled"
    return False, "send_failed\""""),
]

AU_PAIRS = [
("""            _sent, _why = _ml.send_verification_code(user.email, _code)""",
 """            _sent, _why = _ml.send_verification_code(user.email, _code, verification_id=_vid)"""),
("""        sent, why = _ml.send_verification_code(user.email, code)""",
 """        sent, why = _ml.send_verification_code(user.email, code, verification_id=vid)"""),
]


def patch(path, pairs):
    name = os.path.basename(path)
    src = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if src.count(old) != 1:
            print("  %-18s ЯКОРЬ %d раз: %s" % (name, src.count(old), old.strip()[:45]))
            return False
        src = src.replace(old, new)
    shutil.copy2(path, path + ".before_c_" + stamp)
    open(path, "w", encoding="utf-8").write(src)
    try:
        py_compile.compile(path, doraise=True)
        print("  %-18s ok" % name)
        return True
    except Exception as exc:
        shutil.copy2(path + ".before_c_" + stamp, path)
        print("  %-18s СЛОМАН, ОТКАЧЕН (%s)" % (name, type(exc).__name__))
        return False


if MARK in open(EQ, encoding="utf-8").read():
    print("  патч C уже применён, пропуск")
else:
    ok = patch(EQ, EQ_PAIRS)
    ok = patch(ML, ML_PAIRS) and ok
    ok = patch(AU, AU_PAIRS) and ok
    print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
