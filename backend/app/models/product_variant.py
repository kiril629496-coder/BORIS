"""Вариант исполнения товара: размер, цвет, комплектация.

Именно вариант связывается с объявлением, если он есть; иначе объявление
ссылается прямо на товар. Поэтому обе ссылки в campaign_items nullable.
"""
from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(BigInteger, primary_key=True, index=True)
    product_id = Column(BigInteger, ForeignKey("catalog_products.id",
                                               ondelete="CASCADE"),
                        nullable=False, index=True)
    name = Column(String(512), nullable=False)
    sku = Column(String(128), index=True)
    attrs = Column(JSONB)
    price = Column(Numeric(12, 2))
    status = Column(String(24), nullable=False, server_default="active")
    position = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), index=True)


Index("ix_product_variants_live", ProductVariant.product_id,
      ProductVariant.deleted_at)
