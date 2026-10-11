"""Объявление внутри кампании — источник истины нового Workspace.

storage (drafts / gen_ads / feed_items) остаётся для старого контура как
проекция; постоянного dual-write без версий здесь не будет.
hypothesis_id проставляется при создании структуры кампании и живёт до
аналитики (Campaign Learning Loop).
"""
from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer,
                        SmallInteger, String, Text, func)

from app.db.base import Base
from app.services.campaign_card_revisions import next_version, install as install_card_history


class CampaignItem(Base):
    __tablename__ = "campaign_items"

    id = Column(BigInteger, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    # Legacy field: historically used as feed/XML <Id>. Kept for compatibility.
    external_item_id = Column(String, index=True)
    # Canonical identity contract v1.
    feed_identity = Column(String, index=True)
    avito_item_id = Column(String, index=True)
    identity_status = Column(String, index=True)
    segment = Column(String)
    intent = Column(String)
    geo = Column(String)
    offer = Column(String)
    hypothesis_id = Column(String, index=True)
    # ЕЦО: связь с каталогом. Nullable — старые объявления без товара работают как раньше.
    product_id = Column(BigInteger, index=True)
    variant_id = Column(BigInteger, index=True)
    hero_media_id = Column(BigInteger)
    status = Column(String, nullable=False, default="draft", index=True)
    readiness_avito = Column(SmallInteger)
    readiness_sales = Column(SmallInteger)
    payload_json = Column(Text)
    source = Column(String, nullable=False, default="manual")
    source_ref = Column(String)
    content_hash = Column(String(64))
    analysis_hash = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": updated_at, "version_id_generator": next_version}


install_card_history()
