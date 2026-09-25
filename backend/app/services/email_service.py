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
                  from_name=None, reply_to=None, headers=None, attachments=None) -> EmailMessage:
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
    for item in (attachments or []):
        path = item.get("path") if isinstance(item, dict) else str(item)
        filename = (item.get("filename") if isinstance(item, dict) else None) or os.path.basename(path)
        mime = (item.get("mime") if isinstance(item, dict) else None) or "application/pdf"
        maintype, _, subtype = mime.partition("/")
        with open(path, "rb") as fh:
            data = fh.read()
        if isinstance(item, dict) and item.get("inline") and item.get("cid") and html:
            html_part = msg.get_payload()[-1] if msg.is_multipart() else None
            if html_part is not None and hasattr(html_part, "add_related"):
                html_part.add_related(data, maintype=maintype or "image", subtype=subtype or "png", cid=f"<{item['cid']}>", filename=filename, disposition="inline")
                continue
        msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)
    return msg


class SMTPTransportFailure(RuntimeError):
    def __init__(self, phase: str, original: Exception):
        super().__init__(type(original).__name__)
        self.phase = str(phase or "unknown")
        self.original = original


def _smtp_transport_send(msg, *, tenant_scope: str, host: str, port: int,
                         username: str, password: str, use_ssl: bool = True,
                         starttls: bool = False, timeout: int = DEFAULT_TIMEOUT,
                         recipients=None) -> None:
    """Canonical physical SMTP writer for BORIS.

    Every caller must supply a non-empty tenant/service scope. The helper treats
    DATA acceptance as success even when SMTP session shutdown later fails.
    """
    tenant = str(tenant_scope or "").strip()
    if not tenant:
        raise ValueError("tenant_scope_required:EMAIL_SEND")
    smtp = None
    phase = "connect"
    accepted = False
    try:
        cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        smtp = cls(str(host), int(port), timeout=int(timeout))
        if starttls and not use_ssl:
            phase = "starttls"
            smtp.starttls()
        phase = "login"
        smtp.login(str(username), str(password))
        phase = "sending"
        smtp.send_message(msg, to_addrs=recipients)
        accepted = True
        phase = "accepted"
    except Exception as exc:
        raise SMTPTransportFailure(phase, exc) from exc
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                if not accepted:
                    try:
                        smtp.close()
                    except Exception as close_exc:
                        logger.warning("email_service: SMTP close failed after unsuccessful send (%s)", type(close_exc).__name__)


def send_email(to, subject, body, html=None, from_address=None,
               from_name=None, reply_to=None, headers=None, attachments=None,
               *, tenant_scope: str) -> tuple:
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
                        from_name=from_name, reply_to=reply_to, headers=headers, attachments=attachments)
    message_id = msg.get("Message-ID", "")

    try:
        _smtp_transport_send(
            msg,
            tenant_scope=tenant_scope,
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["user"],
            password=cfg["password"],
            use_ssl=True,
            starttls=False,
            timeout=DEFAULT_TIMEOUT,
            recipients=recipients,
        )
    except SMTPTransportFailure as wrapped:
        exc = wrapped.original
        code = getattr(exc, "smtp_code", None)
        reason = type(exc).__name__ if code is None else "%s:%s" % (type(exc).__name__, code)
        if wrapped.phase == "sending" and code is None:
            reason = "delivery_unknown:" + reason
        logger.warning("email_service: отправка не удалась (%s)", reason)
        return False, reason, message_id
    except Exception as exc:
        logger.warning("email_service: отправка не удалась (%s)", type(exc).__name__)
        return False, type(exc).__name__, message_id

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
