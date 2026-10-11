from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, func
from app.db.base import Base

class InternalConversation(Base):
    __tablename__ = "internal_conversations"
    id = Column(BigInteger, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class InternalConversationMember(Base):
    __tablename__ = "internal_conversation_members"
    conversation_id = Column(BigInteger, ForeignKey("internal_conversations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String, nullable=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_read_message_id = Column(BigInteger, nullable=True)

class InternalMessage(Base):
    __tablename__ = "internal_messages"
    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(BigInteger, ForeignKey("internal_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False, default="")
    reply_to_message_id = Column(BigInteger, ForeignKey("internal_messages.id"), nullable=True)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class InternalMessageReaction(Base):
    __tablename__ = "internal_message_reactions"
    message_id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    reaction = Column(String(32), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class InternalMessagePin(Base):
    __tablename__ = "internal_message_pins"
    message_id = Column(BigInteger, primary_key=True)
    pinned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class InternalMessageAttachment(Base):
    __tablename__ = "internal_message_attachments"
    id = Column(BigInteger, primary_key=True)
    message_id = Column(BigInteger, ForeignKey("internal_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=False)
    storage_ref = Column(Text, nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
