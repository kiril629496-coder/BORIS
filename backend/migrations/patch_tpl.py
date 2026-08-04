import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
ML = os.path.join(BASE, "app/services/mailer.py")
RF = os.path.join(BASE, "app/services/email_queue_ref.py")
stamp = str(int(time.time()))

ML_PAIRS = [
("""    прежние ожидающие письма гасятся, устаревший код не доставляется.
    Без verification_id поведение прежнее — прямая отправка.
    \"\"\"
    subject = "БОРИС — код подтверждения почты"
    body = (
        "Здравствуйте!\\n\\n"
        "Код подтверждения для входа в БОРИС:\\n\\n"
        "    {code}\\n\\n"
        "Код действует 10 минут и используется один раз.\\n"
        "Если вы не регистрировались в БОРИСе, просто удалите это письмо.\\n\\n"
        "boris-ai.pro"
    ).format(code=code)

    if not verification_id:
        return send_mail(to, subject, body)

    from app.services import email_queue_ref as _ref
    try:
        _ref.cancel_pending_verification(to)
        res = _ref.enqueue_verification(to, subject, body, verification_id)""",
 """    прежние ожидающие письма гасятся, устаревший код не доставляется.
    Без verification_id письмо тоже идёт через очередь, но без привязки.
    \"\"\"
    from app.email_templates import render
    from app.services.verification import CODE_TTL_MINUTES
    subject, body, html = render(
        "verify_email", {"code": code, "ttl_minutes": CODE_TTL_MINUTES})[:3]

    from app.services import email_queue as _eq
    from app.services import email_queue_ref as _ref
    try:
        if verification_id:
            _ref.cancel_pending_verification(to)
            res = _ref.enqueue_verification(to, subject, body, verification_id, html=html)
        else:
            res = _eq.enqueue_email(to, subject, body, html=html, source="auth")"""),
]

RF_PAIRS = [
("def enqueue_verification(to, subject, body, verification_id) -> dict:",
 "def enqueue_verification(to, subject, body, verification_id, html=None) -> dict:"),
('    return enqueue_email(to, subject, body, source="auth",',
 '    return enqueue_email(to, subject, body, html=html, source="auth",'),
]


def patch(path, pairs):
    name = os.path.basename(path)
    src = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if src.count(old) != 1:
            print("  %-22s ЯКОРЬ %d раз" % (name, src.count(old)))
            return False
        src = src.replace(old, new)
    shutil.copy2(path, path + ".before_tplpatch_" + stamp)
    open(path, "w", encoding="utf-8").write(src)
    try:
        py_compile.compile(path, doraise=True)
        print("  %-22s ok" % name)
        return True
    except Exception as exc:
        shutil.copy2(path + ".before_tplpatch_" + stamp, path)
        print("  %-22s СЛОМАН, ОТКАЧЕН (%s)" % (name, type(exc).__name__))
        return False


if "email_templates" in open(ML, encoding="utf-8").read():
    print("  патч шаблона уже применён, пропуск")
else:
    ok = patch(ML, ML_PAIRS)
    ok = patch(RF, RF_PAIRS) and ok
    print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
