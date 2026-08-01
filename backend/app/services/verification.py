"""
Коды подтверждения email и лимиты попыток/отправок.

Redis в проекте нет, поэтому окна лимитов считаются в PostgreSQL
по таблицам auth_rate_events и auth_blocks (миграция 001).

Все функции принимают уже открытую сессию `db` первым аргументом —
модуль намеренно не импортирует SessionLocal, чтобы не зависеть от
расположения фабрики сессий и не открывать вторую сессию поверх вызывающей.
Коммитит вызывающий код.
"""

import datetime
import logging
import secrets

import bcrypt
from sqlalchemy import text

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SEC = 180
MAX_EMAILS_PER_EMAIL_HOUR = 5
MAX_EMAILS_PER_IP_HOUR = 10
MAX_REGISTER_PER_IP_HOUR = 10
BLOCK_MINUTES = 30


# ---------------------------------------------------------------- утилиты

def normalize_email(email: str) -> str:
    """Только регистр и пробелы. Плюс-теги и точки НЕ трогаем:
    QA регистрирует qa+<ts>-<rand>@borisqa.ru, срезание схлопнуло бы их в один адрес."""
    return (email or "").strip().lower()


def mask_email(email: str) -> str:
    """k***@yandex.ru — для показа на экране ввода кода."""
    email = email or ""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return "*@" + domain
    return local[0] + "*" * max(1, len(local) - 1) + "@" + domain


def generate_code() -> str:
    """Криптографически случайные шесть цифр, равномерно, с ведущими нулями."""
    return "{:06d}".format(secrets.randbelow(1000000))


def new_verification_id() -> str:
    return secrets.token_urlsafe(32)


def hash_code(code: str) -> str:
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_code(code: str, code_hash: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode("utf-8"), (code_hash or "").encode("utf-8"))
    except Exception:
        return False


def _now():
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------- лимиты

def log_event(db, kind: str, rate_key: str, ip: str = "") -> None:
    db.execute(
        text("INSERT INTO auth_rate_events (kind, rate_key, ip, created_at) "
             "VALUES (:k, :rk, :ip, :ts)"),
        {"k": kind, "rk": rate_key or "", "ip": ip or None, "ts": _now()},
    )


def count_events(db, kind: str, rate_key: str = "", ip: str = "", minutes: int = 60) -> int:
    since = _now() - datetime.timedelta(minutes=minutes)
    if rate_key:
        row = db.execute(
            text("SELECT count(*) FROM auth_rate_events "
                 "WHERE kind = :k AND rate_key = :rk AND created_at >= :since"),
            {"k": kind, "rk": rate_key, "since": since},
        ).scalar()
    else:
        row = db.execute(
            text("SELECT count(*) FROM auth_rate_events "
                 "WHERE kind = :k AND ip = :ip AND created_at >= :since"),
            {"k": kind, "ip": ip, "since": since},
        ).scalar()
    return int(row or 0)


def seconds_since_last(db, kind: str, rate_key: str):
    """Сколько секунд прошло с последнего события. None — событий не было."""
    row = db.execute(
        text("SELECT max(created_at) FROM auth_rate_events "
             "WHERE kind = :k AND rate_key = :rk"),
        {"k": kind, "rk": rate_key},
    ).scalar()
    if row is None:
        return None
    return (_now() - row).total_seconds()


def block(db, block_key: str, reason: str, minutes: int = BLOCK_MINUTES) -> None:
    db.execute(
        text("INSERT INTO auth_blocks (block_key, reason, blocked_until, created_at) "
             "VALUES (:bk, :r, :until, :ts)"),
        {"bk": block_key, "r": reason, "until": _now() + datetime.timedelta(minutes=minutes),
         "ts": _now()},
    )


def blocked_until(db, block_key: str):
    """Возвращает datetime окончания активной блокировки или None."""
    return db.execute(
        text("SELECT max(blocked_until) FROM auth_blocks "
             "WHERE block_key = :bk AND blocked_until > :now"),
        {"bk": block_key, "now": _now()},
    ).scalar()


# ---------------------------------------------------------------- коды

def invalidate_active(db, user_id: int) -> int:
    """Гасит все ещё живые коды пользователя. Возвращает число погашенных."""
    res = db.execute(
        text("UPDATE email_verifications SET used_at = :ts "
             "WHERE user_id = :uid AND used_at IS NULL"),
        {"ts": _now(), "uid": user_id},
    )
    return int(res.rowcount or 0)


def issue(db, user_id: int, email: str, request_ip: str = "") -> tuple:
    """
    Выпускает новый код: гасит предыдущие, пишет хеш, возвращает
    (verification_id, code). Сам код наружу отдаётся ТОЛЬКО почтовому модулю.
    """
    invalidate_active(db, user_id)

    code = generate_code()
    vid = new_verification_id()
    now = _now()

    db.execute(
        text("INSERT INTO email_verifications "
             "(verification_id, user_id, email, code_hash, expires_at, "
             " attempts_count, max_attempts, sent_at, created_at, request_ip) "
             "VALUES (:vid, :uid, :em, :ch, :exp, 0, :maxa, :sent, :created, :ip)"),
        {"vid": vid, "uid": user_id, "em": email, "ch": hash_code(code),
         "exp": now + datetime.timedelta(minutes=CODE_TTL_MINUTES),
         "maxa": MAX_ATTEMPTS, "sent": now, "created": now, "ip": request_ip or None},
    )
    return vid, code


def get_active(db, verification_id: str):
    """Последняя неиспользованная запись по публичному id."""
    return db.execute(
        text("SELECT id, verification_id, user_id, email, code_hash, expires_at, "
             "       attempts_count, max_attempts, used_at "
             "  FROM email_verifications WHERE verification_id = :vid"),
        {"vid": verification_id},
    ).mappings().first()


def bump_attempt(db, row_id: int) -> int:
    return int(db.execute(
        text("UPDATE email_verifications SET attempts_count = attempts_count + 1 "
             "WHERE id = :id RETURNING attempts_count"),
        {"id": row_id},
    ).scalar() or 0)


def mark_used(db, row_id: int) -> bool:
    """
    Помечает код использованным. Возвращает True, только если это сделал
    именно этот вызов — условие used_at IS NULL в самом UPDATE.
    Отсюда идемпотентность verify-email: повторный запрос вернёт False,
    и триал второй раз не выдастся.
    """
    res = db.execute(
        text("UPDATE email_verifications SET used_at = :ts "
             "WHERE id = :id AND used_at IS NULL"),
        {"ts": _now(), "id": row_id},
    )
    return int(res.rowcount or 0) == 1
