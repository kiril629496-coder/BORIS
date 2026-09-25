"""Один кадр фотосессии со всей родословной.

Хранит то, что позволяет ВОСПРОИЗВЕСТИ генерацию: модель, версию промпта,
версии Furniture/Scene DNA, размер, качество, request_id провайдера, факт
стоимости и результат валидатора. Consistency и коммерческая красота живут
раздельно: кадр может быть красивым и при этом непригодным, потому что
изменил само изделие.
"""
from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

SHOT_STATUSES = ("queued", "generating", "validating", "approved", "rejected",
                 "failed", "skipped")


class PhotoShot(Base):
    __tablename__ = "photo_shots"

    id = Column(BigInteger, primary_key=True, index=True)
    session_id = Column(BigInteger, ForeignKey("photo_sessions.id",
                                               ondelete="CASCADE"),
                        nullable=False, index=True)
    shot_code = Column(String(32), nullable=False)   # HERO_FRONT, DETAIL_FACADE...
    is_master = Column(Integer, nullable=False, server_default="0")
    status = Column(String(16), nullable=False, server_default="queued", index=True)

    media_id = Column(BigInteger, index=True)
    operation = Column(String(24))                   # generations | edits
    model = Column(String(64))
    quality = Column(String(16))
    size = Column(String(16))
    prompt_name = Column(String(64))
    prompt_version = Column(String(16))
    prompt_text = Column(Text)
    furniture_dna_version = Column(String(16))
    scene_dna_version = Column(String(16))
    reference_media_id = Column(BigInteger)          # мастер как reference

    request_id = Column(String(128), index=True)     # id от OpenAI
    cost_kopeks = Column(Integer)
    cost_rub = Column(Numeric(12, 4))

    consistency_score = Column(Integer)
    photo_score = Column(Integer)
    critical_errors = Column(JSONB)
    warnings = Column(JSONB)

    retry_of = Column(BigInteger)
    attempt = Column(Integer, nullable=False, server_default="1")
    error_text = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


Index("ix_photo_shots_session_status", PhotoShot.session_id, PhotoShot.status)
