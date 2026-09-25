"""Кампания Ad Campaign Workspace.

ВАЖНО (поправка Кирилла 14.08): кампания НЕ привязана к одному Avito-аккаунту.
`default_account_id` — лишь значение по умолчанию для новых элементов.
Фактическая принадлежность объявления живёт в CampaignItem.account_id.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.db.base import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    default_account_id = Column(String, index=True, nullable=True)
    owner_user_id = Column(Integer, nullable=True)
    scenario_type = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    status = Column(String, default="draft", index=True)
    category_id = Column(String, nullable=True)
    strategy_json = Column(Text, nullable=True)
    settings_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
