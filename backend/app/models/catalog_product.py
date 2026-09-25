"""Товар или услуга бизнеса — вершина каталога ЕЦО.

Один товар порождает десятки объявлений (интент × гео × аккаунт), но сущность
остаётся одна: campaign_items ссылаются на product_id/variant_id, а не копируют
товар под каждый Avito-аккаунт.

⛔ Ни одного UNIQUE на бизнес-поля: sku у клиентов повторяется и приходит из
импорта грязным. Уникальность обеспечивает только id.
"""
from sqlalchemy import (BigInteger, Column, DateTime, Index, Integer, Numeric,
                        String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

PRODUCT_KINDS = ("product", "service")
PRODUCT_STATUSES = ("draft", "needs_data", "active", "archived")


class CatalogProduct(Base):
    __tablename__ = "catalog_products"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    account_id = Column(String(255), index=True)   # аккаунт-источник, не владелец

    sku = Column(String(128), index=True)
    name = Column(String(512), nullable=False)
    kind = Column(String(16), nullable=False, server_default="product")
    category = Column(String(255))                 # человеческая группа: «Кухни»
    avito_category_id = Column(String(64))         # определяется category_resolver
    status = Column(String(24), nullable=False, server_default="draft", index=True)

    description = Column(Text)
    price = Column(Numeric(12, 2))
    price_from = Column(Numeric(12, 2))
    price_to = Column(Numeric(12, 2))
    currency = Column(String(8), server_default="RUB")

    attrs = Column(JSONB)        # материалы, размеры, фасады — без выдумок
    tags = Column(JSONB)
    geo = Column(JSONB)          # города/зоны, где товар продаётся

    source = Column(String(32), server_default="manual")  # manual|import|feed|parser
    source_ref = Column(String(512))
    content_hash = Column(String(64), index=True)

    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), index=True)


Index("ix_catalog_products_owner_live", CatalogProduct.owner_user_id,
      CatalogProduct.deleted_at, CatalogProduct.id)
Index("ix_catalog_products_owner_sku", CatalogProduct.owner_user_id,
      CatalogProduct.sku)
