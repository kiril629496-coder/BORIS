from sqlalchemy import Column, Integer, String
from app.db.base import Base

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)

class Storage(Base):
    __tablename__ = "storage"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True, default="default")
    key = Column(String, index=True)  # например: "templates", "gen_settings", "gen_ads"
    value = Column(String)  # JSON-строка
