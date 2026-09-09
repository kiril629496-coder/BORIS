"""BORIS Control Plane persistent models.

Canonical desired-state registry, executions, evidence, incidents and module
contracts. Business modules remain owners of their domain actions; Control Plane
owns the rule lifecycle and proof that desired state matches actual state.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class ControlRequirement(Base):
    __tablename__ = "control_requirements"
    id = Column(UUID(as_uuid=False), primary_key=True)
    key = Column(String(160), nullable=False, unique=True, index=True)
    title = Column(String(500), nullable=False)
    raw_owner_text = Column(Text)
    normalized_rule = Column(Text, nullable=False)
    requirement_type = Column(String(32), nullable=False, index=True)
    scope_type = Column(String(24), nullable=False, server_default="global", index=True)
    scope_id = Column(String(255), nullable=False, server_default="*", index=True)
    module = Column(String(64), nullable=False, server_default="system", index=True)
    priority = Column(String(16), nullable=False, server_default="normal", index=True)
    status = Column(String(24), nullable=False, server_default="active", index=True)
    desired_state_json = Column(JSONB, nullable=False, server_default="{}")
    conditions_json = Column(JSONB, nullable=False, server_default="[]")
    actions_json = Column(JSONB, nullable=False, server_default="[]")
    constraints_json = Column(JSONB, nullable=False, server_default="[]")
    verification_json = Column(JSONB, nullable=False, server_default="[]")
    dependencies_json = Column(JSONB, nullable=False, server_default="[]")
    source_json = Column(JSONB, nullable=False, server_default="{}")
    version = Column(Integer, nullable=False, server_default="1")
    superseded_by = Column(UUID(as_uuid=False))
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_applied_at = Column(DateTime(timezone=True))
    last_verified_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_control_req_scope_module", "scope_type", "scope_id", "module", "status"),
    )


class ControlRequirementVersion(Base):
    __tablename__ = "control_requirement_versions"
    id = Column(BigInteger, primary_key=True)
    requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot_json = Column(JSONB, nullable=False)
    change_reason = Column(Text)
    changed_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("requirement_id", "version", name="uq_control_req_version"),)


class ControlExecution(Base):
    __tablename__ = "control_executions"
    id = Column(UUID(as_uuid=False), primary_key=True)
    requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    correlation_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(255), index=True)
    module = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, server_default="received", index=True)
    plan_json = Column(JSONB, nullable=False, server_default="{}")
    actual_state_json = Column(JSONB, nullable=False, server_default="{}")
    attempt = Column(Integer, nullable=False, server_default="0")
    max_attempts = Column(Integer, nullable=False, server_default="3")
    idempotency_key = Column(String(160), nullable=False, unique=True, index=True)
    block_reason = Column(Text)
    error_code = Column(String(96))
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ControlEvidence(Base):
    __tablename__ = "control_evidence"
    id = Column(BigInteger, primary_key=True)
    execution_id = Column(UUID(as_uuid=False), ForeignKey("control_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    level = Column(String(8), nullable=False, index=True)  # L1..L6
    evidence_type = Column(String(48), nullable=False, index=True)
    source = Column(String(96), nullable=False)
    passed = Column(Boolean, nullable=False, server_default="false", index=True)
    payload_json = Column(JSONB, nullable=False, server_default="{}")
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class ControlIncident(Base):
    __tablename__ = "control_incidents"
    id = Column(UUID(as_uuid=False), primary_key=True)
    requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="SET NULL"), index=True)
    execution_id = Column(UUID(as_uuid=False), ForeignKey("control_executions.id", ondelete="SET NULL"), index=True)
    incident_type = Column(String(64), nullable=False, index=True)
    priority = Column(String(8), nullable=False, server_default="P2", index=True)
    state = Column(String(24), nullable=False, server_default="open", index=True)
    module = Column(String(64), nullable=False, server_default="system", index=True)
    account_id = Column(String(255), index=True)
    symptom = Column(Text, nullable=False)
    root_cause = Column(Text, nullable=False, server_default="unknown")
    remediation = Column(Text)
    prevention = Column(Text)
    details_json = Column(JSONB, nullable=False, server_default="{}")
    opened_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ControlModuleContract(Base):
    __tablename__ = "control_module_contracts"
    id = Column(BigInteger, primary_key=True)
    module = Column(String(64), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, server_default="true")
    contract_json = Column(JSONB, nullable=False, server_default="{}")
    capabilities_json = Column(JSONB, nullable=False, server_default="{}")
    verification_stale_seconds = Column(Integer, nullable=False, server_default="3600")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ControlOwnerInstruction(Base):
    """Immutable intake ledger: every owner command is captured before it can be lost."""
    __tablename__ = "control_owner_instructions"
    id = Column(UUID(as_uuid=False), primary_key=True)
    idempotency_key = Column(String(160), nullable=False, unique=True, index=True)
    user_id = Column(Integer, index=True)
    source = Column(String(32), nullable=False, server_default="command_center", index=True)
    source_ref = Column(String(160), index=True)
    account_id = Column(String(255), index=True)
    raw_text = Column(Text, nullable=False)
    intent = Column(String(32), nullable=False, index=True)
    requirement_type = Column(String(32), index=True)
    scope_type = Column(String(24), nullable=False, server_default="global")
    scope_id = Column(String(255), nullable=False, server_default="*")
    modules_json = Column(JSONB, nullable=False, server_default="[]")
    linked_requirement_ids_json = Column(JSONB, nullable=False, server_default="[]")
    status = Column(String(24), nullable=False, server_default="captured", index=True)
    compiler_json = Column(JSONB, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class ControlExecutionStep(Base):
    __tablename__ = "control_execution_steps"
    id = Column(BigInteger, primary_key=True)
    execution_id = Column(UUID(as_uuid=False), ForeignKey("control_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    step_no = Column(Integer, nullable=False)
    name = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False, server_default="pending", index=True)
    input_json = Column(JSONB, nullable=False, server_default="{}")
    output_json = Column(JSONB, nullable=False, server_default="{}")
    error_code = Column(String(96))
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("execution_id", "step_no", name="uq_control_execution_step_no"),)


class ControlExecutionAttempt(Base):
    __tablename__ = "control_execution_attempts"
    id = Column(BigInteger, primary_key=True)
    execution_id = Column(UUID(as_uuid=False), ForeignKey("control_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id = Column(BigInteger, ForeignKey("control_execution_steps.id", ondelete="CASCADE"), index=True)
    attempt_no = Column(Integer, nullable=False)
    strategy = Column(String(96), nullable=False, server_default="default")
    status = Column(String(32), nullable=False, server_default="started", index=True)
    error_code = Column(String(96))
    error_message = Column(Text)
    result_json = Column(JSONB, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("execution_id", "step_id", "attempt_no", name="uq_control_execution_attempt"),)


class ControlRuleConflict(Base):
    __tablename__ = "control_rule_conflicts"
    id = Column(UUID(as_uuid=False), primary_key=True)
    left_requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    right_requirement_id = Column(UUID(as_uuid=False), ForeignKey("control_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    field = Column(String(160), nullable=False)
    left_value_json = Column(JSONB, nullable=False)
    right_value_json = Column(JSONB, nullable=False)
    state = Column(String(24), nullable=False, server_default="open", index=True)
    resolution = Column(Text)
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("left_requirement_id", "right_requirement_id", "field", name="uq_control_rule_conflict_pair"),)

# WORKSPACE_THREAD_REGISTRY_V1
class ControlWorkspaceThread(Base):
    __tablename__ = "control_workspace_threads"
    id = Column(UUID(as_uuid=False), primary_key=True)
    thread_type = Column(String(24), nullable=False, server_default="task", index=True)
    title = Column(String(500), nullable=False)
    account_id = Column(String(255), index=True)
    external_chat_ref = Column(String(255), index=True)
    status = Column(String(24), nullable=False, server_default="active", index=True)
    summary = Column(Text, nullable=False, server_default="")
    current_state_json = Column(JSONB, nullable=False, server_default="{}")
    plan_json = Column(JSONB, nullable=False, server_default="[]")
    source_json = Column(JSONB, nullable=False, server_default="{}")
    created_by = Column(Integer, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), index=True)
    last_event_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    __table_args__ = (Index("ix_control_workspace_scope", "thread_type", "account_id", "status", "last_event_at"),)

class ControlWorkspaceEvent(Base):
    __tablename__ = "control_workspace_events"
    id = Column(BigInteger, primary_key=True)
    thread_id = Column(UUID(as_uuid=False), ForeignKey("control_workspace_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, server_default="note", index=True)
    actor = Column(String(64), nullable=False, server_default="owner")
    text = Column(Text, nullable=False, server_default="")
    payload_json = Column(JSONB, nullable=False, server_default="{}")
    instruction_id = Column(UUID(as_uuid=False), ForeignKey("control_owner_instructions.id", ondelete="SET NULL"), index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
