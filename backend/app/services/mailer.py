"""
Отправка письма конкретному получателю.

В проекте уже есть отправка через SMTP_SSL (app/api/support.py, app/api/sitebuild.py),
но там нет параметра получателя — письма уходят на фиксированный адрес владельца.
Этот модуль нужен, чтобы писать клиенту. Существующие модули НЕ трогаются.

Переменные окружения те же, что уже используются проектом:
SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_PORT (по умолчанию 465).
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_PORT = 465


def is_configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def send_mail(to: str, subject: str, body: str) -> tuple:
    """
    Возвращает (ok, reason). Пароль SMTP и тело письма в лог не попадают.
    reason: not_configured | ok | <тип исключения>
    """
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")

    if not (host and user and password):
        logger.warning("mailer: SMTP не настроен, письмо не отправлено")
        return False, "not_configured"

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    port = int(os.environ.get("SMTP_PORT", str(DEFAULT_PORT)))

    try:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.send_message(msg)
    except Exception as exc:
        logger.warning("mailer: отправка не удалась (%s)", type(exc).__name__)
        return False, type(exc).__name__

    return True, "ok"


def send_verification_code(to: str, code: str) -> tuple:
    """Письмо с кодом подтверждения. Код в лог не пишется."""
    subject = "БОРИС — код подтверждения почты"
    body = (
        "Здравствуйте!\n\n"
        "Код подтверждения для входа в БОРИС:\n\n"
        "    {code}\n\n"
        "Код действует 10 минут и используется один раз.\n"
        "Если вы не регистрировались в БОРИСе, просто удалите это письмо.\n\n"
        "boris-ai.pro"
    ).format(code=code)
    return send_mail(to, subject, body)
