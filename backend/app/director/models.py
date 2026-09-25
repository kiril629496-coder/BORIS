from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from app.db.base import Base


class DirectConnection(Base):
    __tablename__ = "direct_connections"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, unique=True, index=True)
    yandex_login = Column(String, nullable=True)
    client_login = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    token_encrypted = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="NOT_CONNECTED", index=True)
    oauth_nonce = Column(String, nullable=True)
    oauth_nonce_expires_at = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(String, nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DirectEntity(Base):
    __tablename__ = "direct_entities"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)
    external_id = Column(String, nullable=False)
    parent_external_id = Column(String, nullable=True)
    name = Column(String, nullable=True)
    status = Column(String, nullable=True)
    state_json = Column(Text, nullable=False, default="{}")
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "entity_type", "external_id", name="uq_direct_entity_scope"),)


class DirectStat(Base):
    __tablename__ = "direct_stats_daily"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    day = Column(String, nullable=False, index=True)
    campaign_id = Column(String, nullable=False, default="", index=True)
    impressions = Column(Integer, nullable=True)
    clicks = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    conversions = Column(Float, nullable=True)
    revenue = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=False, default="{}")
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "day", "campaign_id", name="uq_direct_stat_scope"),)


class DirectKpiConfig(Base):
    __tablename__ = "direct_kpi_configs"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, unique=True, index=True)
    target_leads_daily = Column(Float, nullable=True)
    target_cpl = Column(Float, nullable=True)
    max_cpl = Column(Float, nullable=True)
    target_sales_daily = Column(Float, nullable=True)
    target_cac = Column(Float, nullable=True)
    monthly_budget = Column(Float, nullable=True)
    daily_hard_limit = Column(Float, nullable=True)
    autonomy_mode = Column(String, nullable=False, default="ADVISOR")
    primary_goal = Column(String, nullable=False, default="LEADS")
    ai_writes_enabled = Column(Boolean, nullable=False, default=False)
    max_single_change_percent = Column(Float, nullable=False, default=20.0)
    business_brief_json = Column(Text, nullable=False, default="{}")
    setup_status = Column(String, nullable=False, default="NOT_STARTED")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DirectAction(Base):
    __tablename__ = "direct_ai_actions"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=True)
    action_type = Column(String, nullable=False)
    risk_level = Column(String, nullable=False, default="LOW")
    confidence = Column(Float, nullable=False, default=0.0)
    reason = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False, default="{}")
    before_json = Column(Text, nullable=False, default="{}")
    after_json = Column(Text, nullable=False, default="{}")
    status = Column(String, nullable=False, default="PLANNED", index=True)
    api_response_json = Column(Text, nullable=False, default="{}")
    error_message = Column(Text, nullable=True)
    rollback_available = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    executed_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)


class DirectQueryStat(Base):
    __tablename__ = "direct_query_stats"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    day = Column(String, nullable=False, index=True)
    campaign_id = Column(String, nullable=False, default="", index=True)
    ad_group_id = Column(String, nullable=False, default="", index=True)
    query = Column(Text, nullable=False)
    criterion_id = Column(String, nullable=False, default="")
    criterion_type = Column(String, nullable=True)
    impressions = Column(Integer, nullable=True)
    clicks = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    conversions = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=False, default="{}")
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "day", "campaign_id", "ad_group_id", "query", "criterion_id", name="uq_direct_query_stat_scope"),)


class DirectSliceStat(Base):
    __tablename__ = "direct_slice_stats"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    day = Column(String, nullable=False, index=True)
    campaign_id = Column(String, nullable=False, default="", index=True)
    dimension_type = Column(String, nullable=False, index=True)
    dimension_value = Column(String, nullable=False, index=True)
    impressions = Column(Integer, nullable=True)
    clicks = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    conversions = Column(Float, nullable=True)
    revenue = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=False, default="{}")
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "day", "campaign_id", "dimension_type", "dimension_value", name="uq_direct_slice_scope"),)
