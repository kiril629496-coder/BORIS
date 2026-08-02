"""
Единый сервис отправки почты BORIS AI.

Все модули проекта отправляют письма только через него.
Собственные подключения smtplib в других файлах не создаются.

Переменные окружения:
  SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_PORT (по умолчанию 465) — транспорт
  EMAIL_FROM_ADDRESS  — адрес в поле From (по умолчанию SMTP_USER)
  EMAIL_FROM_NAME     — отображаемое имя отправителя
  EMAIL_REPLY_TO      — адрес для ответа (если пусто, заголовок не ставится)
  EMAIL_PROVIDER      — справочно, на логику не влияет

Пароли, тела писем и коды подтверждения в лог не попадают.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

logger = logging.getLogger(__name__)

DEFAULT_PORT = 465
DEFAULT_TIMEOUT = 20


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def smtp_config() -> dict:
    try:
        port = int(_env("SMTP_PORT") or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return {
        "host": _env("SMTP_HOST"),
        "user": _env("SMTP_USER"),
        "password": _env("SMTP_PASS") or _env("SMTP_PASSWORD"),
        "port": port,
    }


def is_configured() -> bool:
    cfg = smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


def sender() -> tuple:
    """(from_address, from_name, reply_to). From по умолчанию — SMTP_USER."""
    cfg = smtp_config()
    return (
        _env("EMAIL_FROM_ADDRESS") or cfg["user"],
        _env("EMAIL_FROM_NAME"),
        _env("EMAIL_REPLY_TO"),
    )


def _as_list(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def build_message(to, subject, body, html=None, from_address=None,
                  from_name=None, reply_to=None, headers=None) -> EmailMessage:
    addr, name, reply = sender()
    addr = from_address or addr
    name = from_name if from_name is not None else name
    reply = reply_to if reply_to is not None else reply

    recipients = _as_list(to)
    msg = EmailMessage()
    msg["From"] = formataddr((name, addr)) if name else addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = addr.split("@")[-1] if "@" in addr else None
    msg["Message-ID"] = make_msgid(domain=domain)
    if reply:
        msg["Reply-To"] = reply
    for key, value in (headers or {}).items():
        if value:
            msg[key] = value
    msg.set_content(body or "")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send_email(to, subject, body, html=None, from_address=None,
               from_name=None, reply_to=None, headers=None) -> tuple:
    """
    Возвращает (ok, reason, message_id).
    reason: ok | not_configured | no_recipient | <тип исключения>
    """
    cfg = smtp_config()
    if not is_configured():
        logger.warning("email_service: SMTP не настроен, письмо не отправлено")
        return False, "not_configured", ""

    recipients = _as_list(to)
    if not recipients:
        logger.warning("email_service: получатель не указан")
        return False, "no_recipient", ""

    msg = build_message(to, subject, body, html=html, from_address=from_address,
                        from_name=from_name, reply_to=reply_to, headers=headers)
    message_id = msg.get("Message-ID", "")

    try:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=DEFAULT_TIMEOUT) as server:
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg, to_addrs=recipients)
    except Exception as exc:
        code = getattr(exc, "smtp_code", None)
        reason = type(exc).__name__ if code is None else "%s:%s" % (type(exc).__name__, code)
        logger.warning("email_service: отправка не удалась (%s)", reason)
        return False, reason, message_id

    logger.info("email_service: письмо отправлено, получателей %d", len(recipients))
    return True, "ok", message_id


def test_smtp_connection() -> tuple:
    """Проверка соединения и логина без отправки письма. (ok, reason)"""
    cfg = smtp_config()
    if not is_configured():
        return False, "not_configured"
    try:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=DEFAULT_TIMEOUT) as server:
            server.login(cfg["user"], cfg["password"])
            server.noop()
    except Exception as exc:
        return False, type(exc).__name__
    return True, "ok"
