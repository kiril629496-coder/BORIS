import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
SUP = os.path.join(BASE, "app/api/support.py")
SITE = os.path.join(BASE, "app/api/sitebuild.py")

SUP_OLD = '''def _send_email(subject, body):
    h = os.environ.get("SMTP_HOST"); u = os.environ.get("SMTP_USER"); p = os.environ.get("SMTP_PASS")
    to = os.environ.get("SUPPORT_EMAIL", u)
    if not (h and u and p):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject; msg["From"] = u; msg["To"] = to
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(h, port) as s:
        s.login(u, p); s.sendmail(u, [to], msg.as_string())'''

SUP_NEW = '''def _send_email(subject, body):
    """Письмо на служебный ящик поддержки. Отправка — через единый EmailService."""
    from app.services import email_service as _es
    to = os.environ.get("SUPPORT_EMAIL") or os.environ.get("SMTP_USER")
    if not to:
        return
    _es.send_email(to, subject, body)'''

SITE_OLD = '''        try:
            import smtplib
            from email.mime.text import MIMEText
            recipients = ["ostapenko-kirill-86@yandex.ru", "eliseev-ko@mail.ru"]
            body = text.replace("<b>", "").replace("</b>", "")
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = "Новая заявка на сайт — БОРИС"
            msg["From"] = smtp_user
            msg["To"] = ", ".join(recipients)
            port = int(os.environ.get("SMTP_PORT", "465"))
            with smtplib.SMTP_SSL(smtp_host, port) as srv:
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_user, recipients, msg.as_string())
        except Exception as e:
            print("site_order email failed:", e)'''

SITE_NEW = '''        try:
            from app.services import email_service as _es
            recipients = ["ostapenko-kirill-86@yandex.ru", "eliseev-ko@mail.ru"]
            body = text.replace("<b>", "").replace("</b>", "")
            _es.send_email(recipients, "Новая заявка на сайт — БОРИС", body)
        except Exception as e:
            print("site_order email failed:", e)'''

MARK = "email_service as _es"
stamp = str(int(time.time()))
fails = []

for path, old, new in ((SUP, SUP_OLD, SUP_NEW), (SITE, SITE_OLD, SITE_NEW)):
    name = os.path.basename(path)
    src = open(path, encoding="utf-8").read()
    if MARK in src:
        print("  %-14s уже переведён, пропуск" % name)
        continue
    found = src.count(old)
    if found != 1:
        fails.append("%s: якорь найден %d раз" % (name, found))
        print("  %-14s ОШИБКА: якорь найден %d раз" % (name, found))
        continue
    shutil.copy2(path, path + ".before_emailA_" + stamp)
    open(path, "w", encoding="utf-8").write(src.replace(old, new))
    try:
        py_compile.compile(path, doraise=True)
        print("  %-14s ok, компилируется" % name)
    except Exception as exc:
        shutil.copy2(path + ".before_emailA_" + stamp, path)
        fails.append("%s: %s, откачен" % (name, type(exc).__name__))
        print("  %-14s СИНТАКСИС СЛОМАН, ОТКАЧЕН" % name)

print("ИТОГ:", "ВСЁ ОК" if not fails else "ЕСТЬ ОШИБКИ: " + "; ".join(fails))
