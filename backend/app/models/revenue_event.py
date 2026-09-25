from sqlalchemy import Column, BigInteger, String, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db.base import Base

class RevenueEvent(Base):
    __tablename__ = "revenue_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(String(255), nullable=False, index=True)
    visitor_id = Column(String(96), nullable=False, index=True)
    session_id = Column(String(96), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    source = Column(String(128))
    medium = Column(String(128))
    campaign = Column(String(255))
    content = Column(String(255))
    term = Column(String(255))
    yclid = Column(String(255))
    landing_url = Column(Text)
    page_url = Column(Text)
    referrer = Column(Text)
    payload = Column(JSONB, nullable=False, default=dict)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

Index("ix_revenue_events_account_session_time", RevenueEvent.account_id, RevenueEvent.session_id, RevenueEvent.occurred_at)
