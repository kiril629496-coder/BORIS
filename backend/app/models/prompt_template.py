"""Реестр версионированных промптов.

Промпты не живут строками в коде: furniture_master, furniture_angle,
furniture_detail, banner_offer имеют версии, и кадр запоминает, какой версией
он сделан. Изменение промпта создаёт v2, а не переписывает историю.
"""
from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Index, Integer,
                        String, Text, func)

from app.db.base import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, server_default="1")
    kind = Column(String(24), server_default="photo")
    body = Column(Text, nullable=False)
    notes = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


Index("ix_prompt_name_version", PromptTemplate.name, PromptTemplate.version)
