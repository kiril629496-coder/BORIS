"""Факт бизнеса на любом уровне: BUSINESS → CATEGORY → PRODUCT → VARIANT.

Нижний уровень переопределяет верхний, поэтому «доставка по Сочи бесплатная»
пишется один раз на бизнес и не переспрашивается в каждой карточке.

⛔ BORIS не выдумывает коммерческие факты: если гарантии нет — строки нет, а не
пустое значение, которое потом попадёт в объявление.
"""
from sqlalchemy import (BigInteger, Column, DateTime, Index, Integer, String,
                        Text, func)

from app.db.base import Base

FACT_SCOPES = ("business", "category", "product", "variant")


class CatalogFact(Base):
    __tablename__ = "catalog_facts"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    scope_type = Column(String(16), nullable=False)
    scope_id = Column(String(128), nullable=False, server_default="")
    key = Column(String(128), nullable=False)
    value = Column(Text)
    source = Column(String(32), server_default="user")  # user|import|client_facts|ai
    source_ref = Column(String(512))
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True))


Index("ix_catalog_facts_lookup", CatalogFact.owner_user_id,
      CatalogFact.scope_type, CatalogFact.scope_id, CatalogFact.key)
