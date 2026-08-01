from sqlalchemy import Column, Integer, String
from app.db.base import Base

class Storage(Base):
    __tablename__ = "storage"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True, default="default")
    key = Column(String, index=True)
    value = Column(String)
