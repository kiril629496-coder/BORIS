"""Furniture DNA и Scene DNA — паспорта изделия и помещения.

immutable_features — признаки, которые НЕЛЬЗЯ менять между кадрами одной
фотосессии («духовка справа», «семь верхних секций», «направление досок
east-west»). Именно они проверяются валидатором и попадают в негативные
ограничения промпта.

Версия растёт при каждом изменении: старые кадры помнят, по какой версии
они сделаны, иначе воспроизвести результат невозможно.
"""
from sqlalchemy import (BigInteger, Column, DateTime, Index, Integer, String,
                        func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

DNA_KINDS = ("furniture", "scene")


class DnaProfile(Base):
    __tablename__ = "dna_profiles"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(16), nullable=False)
    product_id = Column(BigInteger, index=True)
    session_id = Column(BigInteger, index=True)
    name = Column(String(255))
    version = Column(Integer, nullable=False, server_default="1")
    payload = Column(JSONB, nullable=False)
    immutable_features = Column(JSONB)
    source = Column(String(16), server_default="ai")   # ai | user
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


Index("ix_dna_owner_kind", DnaProfile.owner_user_id, DnaProfile.kind,
      DnaProfile.product_id)
