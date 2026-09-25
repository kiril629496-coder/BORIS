"""BORIS reliability control plane.

Small canonical tables for operational health, feature flags, kill switches and
circuit-breaker state. They do not replace business engines or background_jobs.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class ReliabilityEvent(Base):
    __tablename__ = "reliability_events"
    id = Column(BigInteger, primary_key=True)
    trace_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(255), index=True)
    module = Column(String(64), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, server_default="info", index=True)
    state = Column(String(32), nullable=False, server_default="open", index=True)
    message = Column(Text)
    details_json = Column(JSONB)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_reliability_events_module_created", "module", "created_at"),
        Index("ix_reliability_events_account_created", "account_id", "created_at"),
    )


class ReliabilityFlag(Base):
    __tablename__ = "reliability_flags"
    id = Column(BigInteger, primary_key=True)
    key = Column(String(128), nullable=False)
    scope_type = Column(String(16), nullable=False, server_default="global")
    scope_id = Column(String(255), nullable=False, server_default="*")
    enabled = Column(Boolean, nullable=False, server_default="true")
    config_json = Column(JSONB)
    updated_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("key", "scope_type", "scope_id", name="uq_reliability_flag_scope"),)


class ReliabilityKillSwitch(Base):
    __tablename__ = "reliability_kill_switches"
    id = Column(BigInteger, primary_key=True)
    module = Column(String(64), nullable=False)
    account_id = Column(String(255), nullable=False, server_default="*")
    blocked = Column(Boolean, nullable=False, server_default="false")
    reason = Column(Text)
    updated_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("module", "account_id", name="uq_reliability_kill_module_account"),)


class ReliabilityCircuit(Base):
    __tablename__ = "reliability_circuits"
    id = Column(BigInteger, primary_key=True)
    dependency = Column(String(96), nullable=False, unique=True, index=True)
    state = Column(String(16), nullable=False, server_default="closed")
    consecutive_failures = Column(Integer, nullable=False, server_default="0")
    opened_until = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    last_failure_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ReliabilityHeartbeat(Base):
    """Last-known liveness for internal workers and functional contours."""
    __tablename__ = "reliability_heartbeats"
    id = Column(BigInteger, primary_key=True)
    module = Column(String(64), nullable=False, index=True)
    worker_id = Column(String(128), nullable=False)
    state = Column(String(16), nullable=False, server_default="ok")
    account_id = Column(String(255), nullable=False, server_default="*", index=True)
    details_json = Column(JSONB)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("module", "worker_id", "account_id", name="uq_reliability_heartbeat_worker"),
        Index("ix_reliability_heartbeats_module_seen", "module", "last_seen_at"),
    )


class ReliabilityRetryBudget(Base):
    """Cluster-wide minute bucket for retry/provider tenant budgets."""
    __tablename__ = "reliability_retry_budgets"
    id = Column(BigInteger, primary_key=True)
    budget_key = Column(String(255), nullable=False)
    minute_bucket = Column(BigInteger, nullable=False)
    used = Column(Integer, nullable=False, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("budget_key", "minute_bucket", name="uq_reliability_retry_budget_minute"),
        Index("ix_reliability_retry_budget_bucket", "minute_bucket"),
    )
