from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, func
from app.db.base import Base

class MessengerPrompt(Base):
    __tablename__ = "messenger_prompts"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True)
    label = Column(String)                      # напр. "Стихи на свадьбу"
    item_ids = Column(Text, nullable=True)       # JSON-список item_id, к которым привязан промпт (пусто = общий)
    custom_instructions = Column(Text, nullable=True)  # ручной текст инструкций от владельца
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MessengerPromptSource(Base):
    """Источники самообучения промпта — ссылка/PDF/картинка, вытянутое содержимое суммаризировано."""
    __tablename__ = "messenger_prompt_sources"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, index=True)
    source_type = Column(String)                 # url | pdf | image
    source_ref = Column(Text)                     # сама ссылка или путь к файлу
    extracted_summary = Column(Text, nullable=True)  # то, что реально попадёт в промпт ИИ
    created_at = Column(DateTime, server_default=func.now())
