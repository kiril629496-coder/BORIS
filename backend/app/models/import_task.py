from sqlalchemy import Column, Integer, String, Text
from app.db.base import Base


class ImportTask(Base):
    __tablename__ = "import_tasks"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default="pending")
    progress = Column(Integer, default=0)   # 👈 НОВОЕ
    results = Column(Text, nullable=True)