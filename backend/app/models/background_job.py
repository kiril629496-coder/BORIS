"""Очередь фоновых задач BORIS. Схема совпадает с уже созданной таблицей."""
from sqlalchemy import (BigInteger, Column, DateTime, Index, Integer, String,
                        Text, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

JOB_STATUSES = ("queued", "awaiting_confirmation", "running", "validating",
                "completed", "partial", "failed", "cancelled")


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id = Column(BigInteger, primary_key=True, index=True)
    kind = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, server_default="queued", index=True)
    owner_user_id = Column(Integer, index=True)
    account_id = Column(String(255), index=True)
    payload_json = Column(JSONB)
    fingerprint = Column(String(128), unique=True)
    run_id = Column(String(64), index=True)
    progress_done = Column(Integer, nullable=False, server_default="0")
    progress_total = Column(Integer, nullable=False, server_default="0")
    attempts = Column(Integer, nullable=False, server_default="0")
    est_kopeks = Column(Integer)
    actual_kopeks = Column(Integer)
    error_text = Column(Text)
    result_json = Column(JSONB)
    scheduled_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
