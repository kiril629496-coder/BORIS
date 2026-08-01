# -*- coding: utf-8 -*-
"""
Модели системы мониторинга источников BORIS.

ВАЖНО: таблицы создаются скриптом sql/monitoring_v1.sql, а НЕ create_all().
Модели описывают уже существующие таблицы для чтения и записи из кода.

Путь импорта Base снят с сервера: app/db/session.py.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.session import Base   # путь подтверждён на сервере 29.07


def _uuid() -> str:
    return str(uuid.uuid4())


class MonitorRun(Base):
    __tablename__ = "monitor_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    platform = Column(String(32))
    mode = Column(String(16), nullable=False, default="scheduled")
    status = Column(String(16), nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    scanned_sources = Column(Integer, nullable=False, default=0)
    scanned_messages = Column(Integer, nullable=False, default=0)
    matched_messages = Column(Integer, nullable=False, default=0)
    new_messages = Column(Integer, nullable=False, default=0)
    duplicate_messages = Column(Integer, nullable=False, default=0)
    sent_alerts = Column(Integer, nullable=False, default=0)
    flood_wait_seconds = Column(Integer, nullable=False, default=0)
    errors = Column(JSONB, nullable=False, default=list)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    platform = Column(String(32), nullable=False)
    external_author_id = Column(String(64), nullable=False)   # стабильный id, не username
    username = Column(String(128))
    display_name = Column(String(255))
    contact_type = Column(String(16), nullable=False, default="person")
    person_id = Column(UUID(as_uuid=False))                   # РЕЗЕРВ, в v1 не заполняется
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    messages_count = Column(Integer, nullable=False, default=0)
    leads_count = Column(Integer, nullable=False, default=0)
    is_blocked = Column(Boolean, nullable=False, default=False)
    payload = Column(JSONB, nullable=False, default=dict)
    notes = Column(Text)


class MonitorSource(Base):
    __tablename__ = "monitor_sources"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    account_id = Column(String(255))
    platform = Column(String(32), nullable=False)
    source_kind = Column(String(32), nullable=False)
    external_id = Column(String(128))
    username = Column(String(128))
    title = Column(String(255))
    url = Column(Text)
    topic_id = Column(Integer)
    topic_title = Column(String(255))
    parent_id = Column(UUID(as_uuid=False), ForeignKey("monitor_sources.id", ondelete="CASCADE"))
    is_active = Column(Boolean, nullable=False, default=True)
    last_seen_external_id = Column(BigInteger)
    last_scanned_at = Column(DateTime(timezone=True))
    error_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    payload = Column(JSONB, nullable=False, default=dict)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MonitorRule(Base):
    __tablename__ = "monitor_rules"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    account_id = Column(String(255))
    name = Column(String(255), nullable=False)
    category = Column(String(128))
    rule_type = Column(String(16), nullable=False, default="keyword")
    params = Column(JSONB, nullable=False, default=dict)
    platforms = Column(JSONB)
    source_ids = Column(JSONB)
    source_kinds = Column(JSONB)
    weights = Column(JSONB, nullable=False, default=dict)
    min_score = Column(Integer, nullable=False, default=40)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def as_row(self) -> dict:
        """Форма, которую принимает SearchRule.from_row() из ядра правил."""
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "rule_type": self.rule_type, "params": self.params or {},
            "platforms": self.platforms, "source_ids": self.source_ids,
            "source_kinds": self.source_kinds, "weights": self.weights or {},
            "min_score": self.min_score, "is_active": self.is_active,
        }


class MonitorMessage(Base):
    __tablename__ = "monitor_messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    platform = Column(String(32), nullable=False)
    source_id = Column(UUID(as_uuid=False), ForeignKey("monitor_sources.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(UUID(as_uuid=False), ForeignKey("monitor_runs.id", ondelete="SET NULL"))
    external_message_id = Column(String(64), nullable=False)
    external_author_id = Column(String(64))
    contact_id = Column(UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"))
    author_name = Column(String(255))
    author_username = Column(String(128))
    topic_id = Column(Integer)
    topic_title = Column(String(255))
    text = Column(Text)
    posted_at = Column(DateTime(timezone=True))
    url = Column(Text)

    matched_rule_id = Column(UUID(as_uuid=False), ForeignKey("monitor_rules.id", ondelete="SET NULL"))
    matched_terms = Column(JSONB, nullable=False, default=list)
    score_total = Column(Integer, nullable=False, default=0)
    score_breakdown = Column(JSONB, nullable=False, default=dict)

    language = Column(String(8))
    intent = Column(String(32))
    priority = Column(String(8))
    duplicates_group = Column(String(64))          # РЕЗЕРВ, в v1 не заполняется

    summary = Column(Text)
    suggested_reply = Column(Text)                 # готовится, автоматически НЕ отправляется
    ai = Column(JSONB, nullable=False, default=dict)
    payload = Column(JSONB, nullable=False, default=dict)

    status = Column(String(24), nullable=False, default="new")
    sent_at = Column(DateTime(timezone=True))
    collected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Lead(Base):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    contact_id = Column(UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"))
    message_id = Column(UUID(as_uuid=False), ForeignKey("monitor_messages.id", ondelete="SET NULL"))
    account_id = Column(String(255))
    deal_id = Column(UUID(as_uuid=False))           # РЕЗЕРВ под deals
    title = Column(String(255))
    status = Column(String(24), nullable=False, default="new")
    manager_email = Column(String(255))
    score_total = Column(Integer)
    suggested_reply = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at = Column(DateTime(timezone=True))
