"""
Совместимая обёртка над единым EmailService.

Публичный интерфейс модуля сохранён без изменений:
  is_configured() -> bool
  send_mail(to, subject, body) -> (ok, reason)
  send_verification_code(to, code) -> (ok, reason)

Вся фактическая отправка выполняется в app/services/email_service.py.
Собственного подключения smtplib здесь больше нет.
"""

import logging

from app.services import email_service as _es

logger = logging.getLogger(__name__)

DEFAULT_PORT = _es.DEFAULT_PORT


def is_configured() -> bool:
    return _es.is_configured()


def send_mail(to: str, subject: str, body: str) -> tuple:
    """
    Возвращает (ok, reason). Пароль SMTP и тело письма в лог не попадают.
    reason: not_configured | no_recipient | ok | <тип исключения>
    """
    ok, reason, _message_id = _es.send_email(to, subject, body)
    return ok, reason


def send_verification_code(to: str, code: str, verification_id: str = None) -> tuple:
    """
    Письмо с кодом подтверждения. Код в лог не пишется.
    С verification_id письмо идёт через очередь с привязкой к записи:
    прежние ожидающие письма гасятся, устаревший код не доставляется.
    Без verification_id письмо тоже идёт через очередь, но без привязки.
    """
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
            res = _eq.enqueue_email(to, subject, body, html=html, source="auth")
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
    return False, "send_failed"
