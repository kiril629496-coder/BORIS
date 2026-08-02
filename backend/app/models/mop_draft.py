"""Модели единой записи ответа AI-МОПа. Таблицы уже созданы миграцией 01.08."""
from sqlalchemy import (
    BigInteger, Column, DateTime, Integer, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class MopDraft(Base):
    """Единственная запись ответа. С ней работают все каналы."""
    __tablename__ = "mop_drafts"

    id = Column(BigInteger, primary_key=True)
    account_id = Column(String(64), nullable=False)
    avito_chat_id = Column(String(64), nullable=False)
    avito_message_id = Column(String(64), nullable=False)
    item_id = Column(String(32))
    item_title = Column(String(255))
    client_name = Column(String(128))
    incoming_text = Column(Text, nullable=False)
    ai_summary = Column(Text)
    reply_text = Column(Text)
    reply_author = Column(String(24))
    status = Column(String(24), nullable=False, default="new")
    outgoing_text_hash = Column(String(64))
    locked_at = Column(DateTime(timezone=True))
    locked_by = Column(String(64))
    sent_at = Column(DateTime(timezone=True))
    send_error = Column(Text)
    regen_count = Column(SmallInteger, default=0)
    lead_id = Column(BigInteger)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class MopDraftCard(Base):
    """Где карточка показана. Новый канал = новая строка, схема не меняется."""
    __tablename__ = "mop_draft_cards"

    id = Column(BigInteger, primary_key=True)
    draft_id = Column(BigInteger, nullable=False)
    channel = Column(String(16), nullable=False)
    chat_id = Column(String(64), nullable=False, default="")
    thread_id = Column(Integer, nullable=False, default=0)
    external_message_id = Column(String(64))
    rendered_status = Column(String(24))
    last_render_hash = Column(String(64))
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class MopDraftEvent(Base):
    """Append-only история. Ничего не обновляется и не удаляется."""
    __tablename__ = "mop_draft_events"

    id = Column(BigInteger, primary_key=True)
    draft_id = Column(BigInteger, nullable=False)
    event = Column(String(32), nullable=False)
    from_status = Column(String(24))
    to_status = Column(String(24))
    channel = Column(String(16))
    actor_type = Column(String(16))
    actor_id = Column(String(64))
    payload = Column(Text)
    # ВНИМАНИЕ: имя metadata занято SQLAlchemy, поэтому атрибут называется meta
    meta = Column("metadata", JSONB)
    at = Column(DateTime(timezone=True), server_default=func.now())


class MopAwaiting(Base):
    """Запасной путь ввода текста, когда reply не распознан."""
    __tablename__ = "mop_awaiting"

    id = Column(BigInteger, primary_key=True)
    channel = Column(String(16), nullable=False)
    user_key = Column(String(64), nullable=False)
    conversation_key = Column(String(64), nullable=False, default="")
    thread_key = Column(String(32))
    draft_id = Column(BigInteger, nullable=False)
    action = Column(String(24), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)


class TgRoute(Base):
    """Маршруты тем. Город берётся отсюда по account_id, из текста — никогда."""
    __tablename__ = "tg_routes"

    id = Column(BigInteger, primary_key=True)
    account_id = Column(String(64), nullable=False)
    chat_id = Column(String(64), nullable=False)
    thread_key = Column(String(24), nullable=False)
    thread_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
