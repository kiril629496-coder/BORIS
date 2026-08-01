from sqlalchemy import Column, Integer, String, DateTime, func
from app.db.base import Base

class Banner(Base):
    __tablename__ = "banners"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True)
    filename = Column(String)          # имя файла на диске
    folder = Column(String, index=True)  # папка-тема, напр. "brushchatka"
    theme_label = Column(String, nullable=True)  # человекочитаемое "Брусчатка"
    prompt = Column(String, nullable=True)        # промт генерации, если был
    source = Column(String, nullable=True)   # full_ai | pillow
    format = Column(String, nullable=True)   # infographic | extended | max_carousel
    created_at = Column(DateTime, server_default=func.now())
