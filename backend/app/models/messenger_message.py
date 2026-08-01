from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, func
from app.db.base import Base

class MessengerMessage(Base):
    """Локальная копия истории переписки Avito Messenger — не только кэш для скорости,
    но и фундамент для будущего анализа (доп.продажи, что сработало, самообучение)."""
    __tablename__ = "messenger_messages"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True)
    avito_chat_id = Column(String, index=True)
    avito_message_id = Column(String, unique=True, index=True)
    item_id = Column(String, nullable=True, index=True)
    item_title = Column(String, nullable=True)
    item_owner_id = Column(String, nullable=True)  # владелец объявления (для отсечки диалогов-покупок)
    direction = Column(String)
    msg_type = Column(String, nullable=True)  # user | seller | system
    content_type = Column(String, nullable=True)  # text | voice | image
    media_ref = Column(String, nullable=True)     # voice_id | image_id
    text = Column(Text, nullable=True)
    avito_created_at = Column(Integer, nullable=True)
    analyzed_for_upsell = Column(Boolean, default=False)
    stored_at = Column(DateTime, server_default=func.now())
