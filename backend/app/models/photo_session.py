"""AI-фотосессия товара.

Порядок жёсткий: REFERENCE -> Furniture DNA -> Scene DNA -> MASTER ->
подтверждение -> derived shots. Мастер-кадр принимает человек, и только после
этого генерируются остальные ракурсы, поэтому неудачная сцена стоит один кадр,
а не десять.

Деньги: est_kopeks — смета ДО запуска, actual_kopeks — факт из api_usage.
batch_confirmed_kopeks заполняется, только если человек заранее подтвердил
ВЕСЬ объём: без этого при исчерпании дневного лимита сессия встаёт на паузу
и спрашивает, а не продолжает тратить завтра сама.
"""
from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Index, Integer,
                        String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

SESSION_STATUSES = ("draft", "estimating", "awaiting_confirmation", "running",
                    "master_review", "deriving", "completed", "partial",
                    "failed", "cancelled", "paused_limit")
SESSION_MODES = ("preserve_product", "free_concept")


class PhotoSession(Base):
    __tablename__ = "photo_sessions"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    account_id = Column(String(255), index=True)
    product_id = Column(BigInteger, index=True)
    variant_id = Column(BigInteger)

    mode = Column(String(24), nullable=False, server_default="preserve_product")
    preset = Column(String(64))                 # современный, премиальный, ...
    status = Column(String(24), nullable=False, server_default="draft", index=True)

    shots_planned = Column(Integer, nullable=False, server_default="0")
    shots_done = Column(Integer, nullable=False, server_default="0")
    shots_rejected = Column(Integer, nullable=False, server_default="0")

    master_media_id = Column(BigInteger)
    furniture_dna_id = Column(BigInteger)
    scene_dna_id = Column(BigInteger)
    reference_media = Column(JSONB)             # id исходников клиента

    est_kopeks = Column(Integer)
    actual_kopeks = Column(Integer, nullable=False, server_default="0")
    batch_confirmed_kopeks = Column(Integer)    # заранее подтверждённая смета
    resume_policy = Column(String(16), nullable=False, server_default="paused_ask")

    run_id = Column(String(64), index=True)     # ключ резерва ai_budget
    job_id = Column(BigInteger)
    error_text = Column(Text)
    settings = Column(JSONB)

    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    finished_at = Column(DateTime(timezone=True))


Index("ix_photo_sessions_owner_live", PhotoSession.owner_user_id,
      PhotoSession.status, PhotoSession.id)
