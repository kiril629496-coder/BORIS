"""
Привязка писем очереди к verification-записям.

Вынесено отдельным модулем, чтобы email_queue.py не разрастался.
Импорты внутрь email_queue делаются лениво — циклической зависимости нет.
"""

import logging
from datetime import datetime

from sqlalchemy import text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def verification_still_valid(db, ref_id, expires_at):
    """
    None — письмо можно слать. Иначе новый статус:
      'expired'   — срок жизни кода истёк
      'cancelled' — записи нет, код использован или погашен выпуском нового
    Это НЕ ошибка SMTP, поэтому в dead такие письма не уходят.
    """
    if expires_at is not None and expires_at <= datetime.now():
        return "expired"
    if not ref_id:
        return None
    row = db.execute(text(
        "SELECT used_at, expires_at FROM email_verifications WHERE verification_id=:v"
    ), {"v": ref_id}).fetchone()
    if row is None:
        return "cancelled"
    used_at, code_expires = row
    if used_at is not None:
        return "cancelled"
    if code_expires is not None and code_expires <= datetime.now():
        return "expired"
    return None


def cancel_pending_verification(to_addr) -> int:
    """
    Гасит письма с кодом, ещё НЕ переданные в SMTP.
    status='sending' не трогаем: воркер уже мог начать отправку, и запись
    в базе её не остановит — там работает проверка внутри _deliver.
    Письма, ждущие повтора, это status='queued' с attempts > 0 — попадают сюда же.
    Колонка to_addresses имеет тип TEXT, сравнение строкой корректно.
    """
    from app.services.email_queue import _join, _log_event
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "UPDATE email_queue SET status='cancelled', updated_at=now(), "
            "next_attempt_at=NULL "
            "WHERE ref_type='verification' AND to_addresses=:to AND status='queued' "
            "RETURNING id"), {"to": _join(to_addr)}).fetchall()
        for (queue_id,) in rows:
            _log_event(db, queue_id, "cancelled", details="выпущен новый код")
        db.commit()
        return len(rows)
    except Exception as exc:
        db.rollback()
        logger.warning("email_queue: отмена прежних писем не удалась (%s)", type(exc).__name__)
        return 0
    finally:
        db.close()


def enqueue_verification(to, subject, body, verification_id, html=None) -> dict:
    """
    Письмо с кодом подтверждения. Дедлайн доставки берётся строго из самой
    verification-записи — формула TTL нигде не дублируется.
    Для мёртвой записи письмо не создаётся вовсе.
    """
    from app.services.email_queue import enqueue_email
    db = SessionLocal()
    try:
        stop = verification_still_valid(db, verification_id, None)
        expires_at = None
        if not stop:
            row = db.execute(text(
                "SELECT expires_at FROM email_verifications WHERE verification_id=:v"
            ), {"v": verification_id}).fetchone()
            if row:
                expires_at = row[0]
    finally:
        db.close()

    if stop:
        logger.info("email: письмо не ставится в очередь, код неактуален (%s)", stop)
        return {"id": None, "status": stop, "duplicate": False}

    return enqueue_email(to, subject, body, html=html, source="auth",
                         idempotency_key="auth-verification:%s" % verification_id,
                         ref_type="verification", ref_id=verification_id,
                         expires_at=expires_at)
