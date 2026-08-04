# -*- coding: utf-8 -*-
"""Модели реактивации. Схема создана migrations/patch_reactivation.py."""
from sqlalchemy import (Column, BigInteger, Integer, String, Boolean, Text,
                        Numeric, DateTime, ForeignKey, func)
from sqlalchemy.dialects.postgresql import JSONB
from app.db.base import Base


class ReactivationSettings(Base):
    __tablename__ = "reactivation_settings"
    id = Column(BigInteger, primary_key=True)
    account_id = Column(String, nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=False)
    mode = Column(String(16), nullable=False, default="recommend")
    reasons_enabled = Column(JSONB, nullable=False, default=list)
    daily_limit = Column(Integer, nullable=False, default=10)
    min_interval_minutes = Column(Integer, nullable=False, default=5)
    max_interval_minutes = Column(Integer, nullable=False, default=15)
    max_attempts_per_dialog = Column(Integer, nullable=False, default=2)
    attempts_window_days = Column(Integer, nullable=False, default=90)
    cooldown_days = Column(Integer, nullable=False, default=7)
    allowed_hours_from = Column(Integer, nullable=False, default=10)
    allowed_hours_to = Column(Integer, nullable=False, default=20)
    timezone = Column(String(48), nullable=False, default="Europe/Moscow")
    disabled_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class ReactivationCandidate(Base):
    __tablename__ = "reactivation_candidates"
    id = Column(BigInteger, primary_key=True)
    account_id = Column(String, nullable=False)
    avito_chat_id = Column(String, nullable=False)
    cycle_no = Column(Integer, nullable=False, default=1)
    primary_reason = Column(String(32), nullable=False)
    matched_reasons = Column(JSONB, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="candidate")
    outcome = Column(String(24))
    score = Column(Integer, nullable=False, default=0)
    confidence = Column(Numeric(4, 3))
    lead_temperature = Column(String(8))
    phone_received = Column(Boolean, nullable=False, default=False)
    purchase_confirmed = Column(Boolean, nullable=False, default=False)
    do_not_contact = Column(Boolean, nullable=False, default=False)
    summary = Column(Text)
    evidence_message_ids = Column(JSONB, nullable=False, default=list)
    recommended_contact_at = Column(DateTime(timezone=True))
    watch_since = Column(DateTime(timezone=True), nullable=False)
    last_analyzed_message_id = Column(BigInteger)
    last_analyzed_at = Column(DateTime(timezone=True))
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class ReactivationMessage(Base):
    __tablename__ = "reactivation_messages"
    id = Column(BigInteger, primary_key=True)
    candidate_id = Column(BigInteger, ForeignKey("reactivation_candidates.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String, nullable=False)
    avito_chat_id = Column(String, nullable=False)
    attempt_number = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(64), nullable=False)
    generated_text = Column(Text)
    edited_text = Column(Text)
    final_text = Column(Text)
    text_hash = Column(String(64))
    status = Column(String(24), nullable=False, default="draft")
    scheduled_at = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))
    avito_message_id = Column(String)
    locked_at = Column(DateTime(timezone=True))
    locked_by = Column(String(64))
    error_code = Column(String(32))
    error_message = Column(Text)
    replied_at = Column(DateTime(timezone=True))
    converted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class ReactivationEvent(Base):
    __tablename__ = "reactivation_events"
    id = Column(BigInteger, primary_key=True)
    candidate_id = Column(BigInteger, ForeignKey("reactivation_candidates.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(BigInteger, ForeignKey("reactivation_messages.id", ondelete="CASCADE"))
    event = Column(String(32), nullable=False)
    from_status = Column(String(24))
    to_status = Column(String(24))
    channel = Column(String(16))
    actor_type = Column(String(16), nullable=False, default="system")
    actor_id = Column(String(64))
    payload = Column(Text)
    meta = Column("metadata", JSONB)
    at = Column(DateTime(timezone=True), server_default=func.now())
