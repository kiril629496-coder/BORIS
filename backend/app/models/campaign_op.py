"""Массовая операция кампании: prepare -> diff -> confirm -> apply -> rollback.

Логика публикации и отката берётся из существующего KPI-контура
(kpi_ai_prepare_execute / kpi_confirm_publish / kpi_publish_changes /
kpi_publish_rollback). Здесь хранится сама операция на уровне кампании.
rollback_json — структурированный diff затронутых полей, НЕ копия кампании.
"""
from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer,
                        String, Text, func)

from app.db.base import Base


class CampaignOp(Base):
    __tablename__ = "campaign_ops"

    id = Column(BigInteger, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    account_id = Column(String, nullable=True)
    operation_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="prepared", index=True)
    affected_count = Column(Integer, nullable=False, default=0)
    affected_refs = Column(Text)
    diff_json = Column(Text)
    rollback_json = Column(Text)
    queue_json = Column(Text)
    progress_done = Column(Integer, nullable=False, default=0)
    progress_total = Column(Integer, nullable=False, default=0)
    actor = Column(String, nullable=False, default="user")
    error_text = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now(), onupdate=func.now())
