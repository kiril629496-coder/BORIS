from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from app.db.base import Base


class SavedPrompt(Base):
    __tablename__ = "saved_prompts"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    purpose = Column(String, default="banner")  # banner | description | other
    created_at = Column(DateTime, default=datetime.utcnow)
