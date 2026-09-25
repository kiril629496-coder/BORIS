from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as SQLAlchemySession, sessionmaker

from app.db.base import Base
import logging
import os
import sys
from dotenv import load_dotenv
from app.services.exception_observability import observe_suppressed

_logger = logging.getLogger(__name__)

# DB_ENV_OPTIONAL_READ_V1: production root services may load the canonical
# root-only .env, while read-only QA/import tooling must not crash merely
# because it cannot read secrets. Existing process environment always remains
# authoritative; secret file permissions are never relaxed here.
_ENV_FILE = "/root/BORIS/backend/.env"
try:
    if os.access(_ENV_FILE, os.R_OK):
        load_dotenv(_ENV_FILE)
except OSError as exc:
    _logger.warning("DB_ENV_OPTIONAL_READ skipped env file: %s", type(exc).__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres"
)

# PostgreSQL max_connections is shared by every BORIS process. A blanket pool
# of 8 per process made a quiet host retain ~50 idle connections simply because
# API replicas, executors and small daemon loops all imported this module.
# Budget by runtime role instead: APIs need concurrency, the dedicated worker
# needs a few slots, and single-loop daemons normally need exactly one.
_RUNTIME_ROLE = str(os.getenv("BORIS_RUNTIME_ROLE", "daemon") or "daemon").strip().lower()
_ROLE_POOL_DEFAULTS = {
    "api": (6, 4),
    "worker": (4, 2),
    "all": (4, 2),  # compatibility for any legacy combined runtime
}
_DEFAULT_POOL_SIZE, _DEFAULT_MAX_OVERFLOW = _ROLE_POOL_DEFAULTS.get(_RUNTIME_ROLE, (1, 1))
_DB_APP_NAME = str(os.getenv("BORIS_DB_APPLICATION_NAME", "") or "").strip()
if not _DB_APP_NAME:
    _entry = os.path.basename(str(sys.argv[0] or "python")).replace(" ", "-")
    _DB_APP_NAME = f"boris-{_RUNTIME_ROLE}-{_entry}"[:60]

_DB_DISCONNECT_MARKERS = (
    "ssl connection has been closed unexpectedly",
    "server closed the connection unexpectedly",
    "connection has been closed unexpectedly",
    "connection already closed",
    "connection not open",
    "terminating connection due to administrator command",
)


def _is_disconnect_close_error(exc: BaseException) -> bool:
    """True only for a physical DB disconnect during Session.close cleanup."""
    if not isinstance(exc, DBAPIError):
        return False
    if bool(getattr(exc, "connection_invalidated", False)):
        return True
    original = getattr(exc, "orig", None)
    message = str(original if original is not None else exc).lower()
    return any(marker in message for marker in _DB_DISCONNECT_MARKERS)


class ResilientSession(SQLAlchemySession):
    """Do not turn a completed request into HTTP 500 because close() lost PostgreSQL.

    A disconnect can happen after query/commit succeeded but before SQLAlchemy's
    implicit rollback on Session.close(). Future checkouts are protected by
    pool_pre_ping; cleanup must therefore discard the broken pool generation and
    finish quietly. Non-disconnect database errors remain visible.
    """

    def close(self) -> None:
        try:
            return super().close()
        except DBAPIError as exc:
            if not _is_disconnect_close_error(exc):
                raise
            try:
                engine.dispose()
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=84)
            try:
                self.expunge_all()
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=88)
            _logger.warning(
                "DB_SESSION_CLOSE_DISCONNECT_RECOVERED runtime_role=%s error_type=%s",
                _RUNTIME_ROLE,
                type(exc).__name__,
            )
            return None


engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("BORIS_DB_POOL_SIZE", str(_DEFAULT_POOL_SIZE))),
    max_overflow=int(os.getenv("BORIS_DB_MAX_OVERFLOW", str(_DEFAULT_MAX_OVERFLOW))),
    pool_timeout=int(os.getenv("BORIS_DB_POOL_TIMEOUT", "15")),
    pool_pre_ping=True,
    pool_use_lifo=True,
    pool_recycle=int(os.getenv("BORIS_DB_POOL_RECYCLE", "240")),
    connect_args={
        "application_name": _DB_APP_NAME,
        "keepalives": 1,
        "keepalives_idle": int(os.getenv("BORIS_DB_KEEPALIVES_IDLE", "30")),
        "keepalives_interval": int(os.getenv("BORIS_DB_KEEPALIVES_INTERVAL", "10")),
        "keepalives_count": int(os.getenv("BORIS_DB_KEEPALIVES_COUNT", "3")),
    },
)

SessionLocal = sessionmaker(
    class_=ResilientSession,
    autocommit=False,
    autoflush=False,
    bind=engine
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()