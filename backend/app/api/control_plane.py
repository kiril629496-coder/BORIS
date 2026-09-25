"""Owner-facing BORIS Control Plane API."""
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.models.control_plane import ControlEvidence, ControlExecution, ControlIncident, ControlRequirement, ControlRequirementVersion, ControlOwnerInstruction, ControlRuleConflict, ControlExecutionStep, ControlExecutionAttempt, ControlWorkspaceThread, ControlWorkspaceEvent
from app.models.task import Task
from app.services.control_plane import (
    add_evidence, classify_owner_instruction, create_requirement, effective_config,
    reconcile, seed_invariants, serialize_requirement, start_execution,
    transition_execution, update_requirement, initialize_execution_steps, detect_rule_conflicts, capture_owner_instruction, execute_requirement_once, resolve_owner_instruction,
)
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/control-plane", tags=["control-plane"])


def _owner(user):
    if str(getattr(user, "role", "") or "").lower() != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")


def _platform_admin(user):
    from app.services.platform_roles import is_private_platform_owner
    if not is_private_platform_owner(user):
        raise HTTPException(status_code=403, detail="Platform admin access required")


def _account_scoped_supervisor_access(db, user, account_id: str):
    """Allow platform admin globally; everyone else only inside explicitly owned/granted account."""
    from sqlalchemy import text as _sql_text
    from app.models.account import Account
    from app.services.platform_roles import is_private_platform_owner
    if is_private_platform_owner(user):
        return
    aid=str(account_id or "").strip()
    if not aid:
        raise HTTPException(status_code=403, detail="Account scope required")
    uid=getattr(user,"id",None)
    direct=(db.query(Account)
            .filter(Account.account_id==aid)
            .filter((Account.owner_user_id==uid) | (Account.account_id==getattr(user,"account_id",None)))
            .first())
    if direct:
        return
    granted=db.execute(_sql_text(
        "SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE LIMIT 1"
    ),{"u":uid,"a":aid}).first()
    if not granted:
        raise HTTPException(status_code=403, detail="Нет доступа к этому аккаунту")


def _serialize_execution_detail(db, x: ControlExecution) -> dict:
    evidence_rows=db.query(ControlEvidence).filter(ControlEvidence.execution_id==x.id).order_by(ControlEvidence.id.asc()).all()
    steps=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==x.id).order_by(ControlExecutionStep.step_no.asc()).all()
    attempts=db.query(ControlExecutionAttempt).filter(ControlExecutionAttempt.execution_id==x.id).order_by(ControlExecutionAttempt.id.asc()).all()
    return {
        "id":x.id,"requirement_id":x.requirement_id,"correlation_id":x.correlation_id,"account_id":x.account_id,"module":x.module,
        "status":x.status,"plan":x.plan_json or {},"actual_state":x.actual_state_json or {},"attempt":x.attempt,"max_attempts":x.max_attempts,
        "error_code":x.error_code,"error_message":x.error_message,"block_reason":x.block_reason,
        "created_at":x.created_at.isoformat() if x.created_at else None,"started_at":x.started_at.isoformat() if x.started_at else None,"finished_at":x.finished_at.isoformat() if x.finished_at else None,
        "steps":[{"id":q.id,"step_no":q.step_no,"name":q.name,"status":q.status,"input":q.input_json or {},"output":q.output_json or {},"error_code":q.error_code} for q in steps],
        "attempts":[{"id":q.id,"step_id":q.step_id,"attempt_no":q.attempt_no,"strategy":q.strategy,"status":q.status,"error_code":q.error_code,"result":q.result_json or {}} for q in attempts],
        "evidence":[{"id":q.id,"level":q.level,"type":q.evidence_type,"source":q.source,"passed":q.passed,"payload":q.payload_json or {},"observed_at":q.observed_at.isoformat() if q.observed_at else None} for q in evidence_rows],
    }

def _human_status(value: str | None) -> str:
    return {"pass":"Работает","executing":"Исправляется","retrying":"Исправляется","waiting_external":"Ждём внешнюю систему","waiting_owner":"Нужны данные","blocked":"Заблокировано","failed":"Ошибка","degraded":"Ошибка"}.get(str(value or ""),"Не проверено")


class RequirementCreate(BaseModel):
    key: str = Field(min_length=3, max_length=160)
    title: str = Field(min_length=3, max_length=500)
    normalized_rule: str = Field(min_length=3)
    requirement_type: str = "business_rule"
    scope_type: str = "global"
    scope_id: str = "*"
    module: str = "system"
    priority: str = "normal"
    raw_owner_text: Optional[str] = None
    desired_state: dict[str, Any] = {}
    conditions: list[Any] = []
    actions: list[Any] = []
    constraints: list[Any] = []
    verification: list[Any] = []
    dependencies: list[Any] = []
    source: dict[str, Any] = {}


class RequirementPatch(BaseModel):
    patch: dict[str, Any]
    reason: str = "owner update"


class IntakeBody(BaseModel):
    text: str = Field(min_length=2)
    account_id: Optional[str] = None
    source: str = "external_owner"
    source_ref: Optional[str] = None


class ExecutionBody(BaseModel):
    account_id: Optional[str] = None
    plan: dict[str, Any] = {}
    idempotency_key: Optional[str] = None


class TransitionBody(BaseModel):
    status: str
    actual_state: Optional[dict[str, Any]] = None
    reason: Optional[str] = None
    error_code: Optional[str] = None


class EvidenceBody(BaseModel):
    level: str
    evidence_type: str
    source: str
    passed: bool
    payload: dict[str, Any] = {}


class ReconcileBody(BaseModel):
    module: str
    account_id: Optional[str] = None
    actual_state: dict[str, Any]


class IntakeResolveBody(BaseModel):
    target_requirement_id: str
    action: str
    patch: dict[str, Any] = {}
    reason: str = "owner instruction resolution"


@router.post("/bootstrap")
def bootstrap(user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        created = seed_invariants(db); db.commit()
        return {"status": "pass", "invariants_created": created}
    finally: db.close()


@router.post("/intake/compile")
def compile_instruction(body: IntakeBody, user=Depends(get_current_user)):
    _owner(user)
    return classify_owner_instruction(body.text)


@router.post("/intake")
def owner_intake_capture(body: IntakeBody, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        result=capture_owner_instruction(db,text=body.text,user_id=getattr(user,"id",None),source=body.source,source_ref=body.source_ref,account_id=body.account_id)
        db.commit(); return {"status":"pass",**result}
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=500,detail=f"owner_intake_failed:{type(exc).__name__}")
    finally: db.close()


@router.post("/intake/{instruction_id}/resolve")
def owner_intake_resolve(instruction_id: str, body: IntakeResolveBody, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        try:
            result=resolve_owner_instruction(db,instruction_id=instruction_id,target_requirement_id=body.target_requirement_id,action=body.action,patch=body.patch,changed_by=getattr(user,"id",None),reason=body.reason)
            db.commit(); return {"status":"pass",**result}
        except ValueError as exc:
            db.rollback(); raise HTTPException(status_code=409,detail=str(exc))
    finally: db.close()


@router.get("/intake")
def owner_intake(intent: Optional[str] = None, status: Optional[str] = None, limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        q = db.query(ControlOwnerInstruction)
        if intent: q = q.filter(ControlOwnerInstruction.intent == intent)
        if status: q = q.filter(ControlOwnerInstruction.status == status)
        rows = q.order_by(ControlOwnerInstruction.created_at.desc()).limit(limit).all()
        return [{"id": x.id, "source": x.source, "source_ref": x.source_ref, "account_id": x.account_id, "text": x.raw_text, "intent": x.intent, "requirement_type": x.requirement_type, "scope_type": x.scope_type, "scope_id": x.scope_id, "modules": x.modules_json or [], "requirement_ids": x.linked_requirement_ids_json or [], "status": x.status, "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]
    finally: db.close()


@router.get("/requirements")
def requirements(module: Optional[str] = None, scope_type: Optional[str] = None, scope_id: Optional[str] = None, status: Optional[str] = None, limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        q = db.query(ControlRequirement)
        if module: q = q.filter(ControlRequirement.module == module)
        if scope_type: q = q.filter(ControlRequirement.scope_type == scope_type)
        if scope_id: q = q.filter(ControlRequirement.scope_id == scope_id)
        if status: q = q.filter(ControlRequirement.status == status)
        return [serialize_requirement(x) for x in q.order_by(ControlRequirement.updated_at.desc()).limit(limit).all()]
    finally: db.close()


@router.post("/requirements")
def requirement_create(body: RequirementCreate, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        try:
            row = create_requirement(db, **body.model_dump(), created_by=getattr(user, "id", None))
            db.commit(); return serialize_requirement(row)
        except ValueError as exc:
            db.rollback(); raise HTTPException(status_code=409 if "exists" in str(exc) else 422, detail=str(exc))
    finally: db.close()


@router.patch("/requirements/{requirement_id}")
def requirement_patch(requirement_id: str, body: RequirementPatch, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        row = db.query(ControlRequirement).filter(ControlRequirement.id == requirement_id).first()
        if not row: raise HTTPException(status_code=404, detail="Requirement not found")
        try: update_requirement(db, row, body.patch, changed_by=getattr(user, "id", None), reason=body.reason)
        except ValueError as exc: db.rollback(); raise HTTPException(status_code=409, detail=str(exc))
        db.commit(); return serialize_requirement(row)
    finally: db.close()


@router.get("/requirements/{requirement_id}/history")
def requirement_history(requirement_id: str, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        rows = db.query(ControlRequirementVersion).filter(ControlRequirementVersion.requirement_id == requirement_id).order_by(ControlRequirementVersion.version.desc()).all()
        return [{"version": r.version, "snapshot": r.snapshot_json, "reason": r.change_reason, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
    finally: db.close()


@router.get("/effective-config/{module}")
def get_effective_config(module: str, account_id: Optional[str] = Query(None), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try: return effective_config(db, module=module, account_id=account_id)
    finally: db.close()


@router.post("/requirements/{requirement_id}/execute-now")
def execution_run_now(requirement_id: str, body: ExecutionBody, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        req=db.query(ControlRequirement).filter(ControlRequirement.id==requirement_id).first()
        if not req: raise HTTPException(status_code=404,detail="Requirement not found")
        try:
            row=execute_requirement_once(db,req,account_id=body.account_id,idempotency_key=body.idempotency_key)
            db.commit()
        except ValueError as exc:
            db.rollback(); raise HTTPException(status_code=409,detail=str(exc))
        return {"id":row.id,"requirement_id":row.requirement_id,"status":row.status,"account_id":row.account_id,"module":row.module,"block_reason":row.block_reason,"error_code":row.error_code,"actual_state":row.actual_state_json or {}}
    finally: db.close()


@router.post("/requirements/{requirement_id}/execute")
def execution_start(requirement_id: str, body: ExecutionBody, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        req = db.query(ControlRequirement).filter(ControlRequirement.id == requirement_id).first()
        if not req: raise HTTPException(status_code=404, detail="Requirement not found")
        row = start_execution(db, req, account_id=body.account_id, plan=body.plan, idempotency_key=body.idempotency_key)
        steps = initialize_execution_steps(db, row)
        db.commit()
        return {"id": row.id, "status": row.status, "correlation_id": row.correlation_id, "idempotency_key": row.idempotency_key, "steps": [{"id": x.id, "step_no": x.step_no, "name": x.name, "status": x.status} for x in steps]}
    finally: db.close()


@router.post("/executions/{execution_id}/transition")
def execution_transition(execution_id: str, body: TransitionBody, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        row = db.query(ControlExecution).filter(ControlExecution.id == execution_id).first()
        if not row: raise HTTPException(status_code=404, detail="Execution not found")
        try: transition_execution(db, row, body.status, actual_state=body.actual_state, reason=body.reason, error_code=body.error_code)
        except ValueError as exc: db.rollback(); raise HTTPException(status_code=409, detail=str(exc))
        db.commit(); return {"id": row.id, "status": row.status}
    finally: db.close()


@router.post("/executions/{execution_id}/evidence")
def evidence_add(execution_id: str, body: EvidenceBody, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        row = db.query(ControlExecution).filter(ControlExecution.id == execution_id).first()
        if not row: raise HTTPException(status_code=404, detail="Execution not found")
        try: ev = add_evidence(db, row, **body.model_dump())
        except ValueError as exc: db.rollback(); raise HTTPException(status_code=422, detail=str(exc))
        db.commit(); return {"id": ev.id, "level": ev.level, "passed": ev.passed}
    finally: db.close()


@router.post("/reconcile")
def reconcile_state(body: ReconcileBody, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        result = reconcile(db, module=body.module, account_id=body.account_id, actual_state=body.actual_state); db.commit(); return result
    finally: db.close()


@router.get("/executions")
def executions(status: Optional[str] = None, module: Optional[str] = None, requirement_id: Optional[str] = None, limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        q = db.query(ControlExecution)
        if status: q = q.filter(ControlExecution.status == status)
        if module: q = q.filter(ControlExecution.module == module)
        if requirement_id: q = q.filter(ControlExecution.requirement_id == requirement_id)
        rows = q.order_by(ControlExecution.created_at.desc()).limit(limit).all()
        return [{"id": x.id, "requirement_id": x.requirement_id, "correlation_id": x.correlation_id, "account_id": x.account_id, "module": x.module, "status": x.status, "attempt": x.attempt, "error_code": x.error_code, "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]
    finally: db.close()


@router.get("/executions/{execution_id}")
def execution_detail(execution_id: str, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        row=db.query(ControlExecution).filter(ControlExecution.id==execution_id).first()
        if not row: raise HTTPException(status_code=404,detail="Execution not found")
        return _serialize_execution_detail(db,row)
    finally: db.close()


@router.get("/regressions")
def regressions(limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        kinds=("CONFIG_DRIFT","STALE_VERIFICATION","MODULE_STATE_READ_FAILED","CONTROL_ROLLBACK_FAILED","KPI_FAILED_BUT_NO_ACTION","ACTIVE_BUT_NEVER_EXECUTED")
        rows=(db.query(ControlIncident).filter(ControlIncident.incident_type.in_(kinds))
              .order_by(ControlIncident.updated_at.desc()).limit(limit).all())
        return [{"id":x.id,"type":x.incident_type,"priority":x.priority,"state":x.state,"module":x.module,"account_id":x.account_id,"requirement_id":x.requirement_id,"execution_id":x.execution_id,"symptom":x.symptom,"root_cause":x.root_cause,"remediation":x.remediation,"prevention":x.prevention,"details":x.details_json or {},"opened_at":x.opened_at.isoformat() if x.opened_at else None,"resolved_at":x.resolved_at.isoformat() if x.resolved_at else None} for x in rows]
    finally: db.close()


@router.get("/module-health")
def module_health(module: Optional[str]=None, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        from app.models.reliability import ReliabilityHeartbeat
        from app.models.background_job import BackgroundJob
        from app.services.reliability import job_module
        contracts=db.query(__import__('app.models.control_plane',fromlist=['ControlModuleContract']).ControlModuleContract)
        if module: contracts=contracts.filter(__import__('app.models.control_plane',fromlist=['ControlModuleContract']).ControlModuleContract.module==module)
        out=[]; now=datetime.now(timezone.utc)
        for c in contracts.order_by(__import__('app.models.control_plane',fromlist=['ControlModuleContract']).ControlModuleContract.module).all():
            rules=db.query(ControlRequirement).filter(ControlRequirement.status=='active',ControlRequirement.module==c.module).count()
            hb=db.query(ReliabilityHeartbeat).filter(ReliabilityHeartbeat.module==c.module).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
            latest=db.query(ControlExecution).filter(ControlExecution.module==c.module).order_by(ControlExecution.created_at.desc()).first()
            last_success=db.query(ControlExecution).filter(ControlExecution.module==c.module,ControlExecution.status=='pass').order_by(ControlExecution.finished_at.desc()).first()
            last_failure=db.query(ControlExecution).filter(ControlExecution.module==c.module,ControlExecution.status.in_(("failed","blocked","degraded"))).order_by(ControlExecution.finished_at.desc()).first()
            jobs=db.query(BackgroundJob).filter(BackgroundJob.status.in_(("queued","running","validating"))).all()
            depth=sum(1 for j in jobs if job_module(j.kind)==c.module)
            hb_age=None
            if hb and hb.last_seen_at:
                seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc); hb_age=int((now-seen).total_seconds())
            open_inc=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.module==c.module).count()
            status='healthy' if open_inc==0 and (hb_age is None or hb_age<=max(300,int(c.verification_stale_seconds or 3600))) else 'degraded'
            out.append({"module":c.module,"status":status,"last_heartbeat":hb.last_seen_at.isoformat() if hb and hb.last_seen_at else None,"heartbeat_age_sec":hb_age,"last_success":last_success.finished_at.isoformat() if last_success and last_success.finished_at else None,"last_failure":last_failure.finished_at.isoformat() if last_failure and last_failure.finished_at else None,"latest_execution_status":latest.status if latest else None,"queue_depth":depth,"active_rules":rules,"open_incidents":open_inc,"capabilities":c.capabilities_json or {}})
        return out
    finally: db.close()


@router.get("/evidence/{execution_id}")
def evidence(execution_id: str, user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        rows = db.query(ControlEvidence).filter(ControlEvidence.execution_id == execution_id).order_by(ControlEvidence.id.asc()).all()
        return [{"id": x.id, "level": x.level, "type": x.evidence_type, "source": x.source, "passed": x.passed, "payload": x.payload_json, "observed_at": x.observed_at.isoformat() if x.observed_at else None} for x in rows]
    finally: db.close()


@router.get("/conflicts")
def conflicts(state: Optional[str] = "open", refresh: bool = Query(False), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        if refresh:
            detect_rule_conflicts(db); db.commit()
        q=db.query(ControlRuleConflict)
        if state: q=q.filter(ControlRuleConflict.state==state)
        rows=q.order_by(ControlRuleConflict.detected_at.desc()).limit(500).all()
        return [{"id":x.id,"left_requirement_id":x.left_requirement_id,"right_requirement_id":x.right_requirement_id,"field":x.field,"left":(x.left_value_json or {}).get("value"),"right":(x.right_value_json or {}).get("value"),"state":x.state,"resolution":x.resolution,"detected_at":x.detected_at.isoformat() if x.detected_at else None} for x in rows]
    finally: db.close()


@router.get("/executions/{execution_id}/steps")
def execution_steps(execution_id: str, user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        steps=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==execution_id).order_by(ControlExecutionStep.step_no.asc()).all()
        attempts=db.query(ControlExecutionAttempt).filter(ControlExecutionAttempt.execution_id==execution_id).order_by(ControlExecutionAttempt.id.asc()).all()
        return {"steps":[{"id":x.id,"step_no":x.step_no,"name":x.name,"status":x.status,"input":x.input_json or {},"output":x.output_json or {},"error_code":x.error_code} for x in steps],"attempts":[{"id":x.id,"step_id":x.step_id,"attempt_no":x.attempt_no,"strategy":x.strategy,"status":x.status,"error_code":x.error_code,"result":x.result_json or {}} for x in attempts]}
    finally: db.close()


@router.get("/incidents")
def incidents(state: Optional[str] = "open", limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user)):
    _owner(user); db = SessionLocal()
    try:
        q = db.query(ControlIncident)
        if state: q = q.filter(ControlIncident.state == state)
        rows = q.order_by(ControlIncident.opened_at.desc()).limit(limit).all()
        return [{"id": x.id, "type": x.incident_type, "priority": x.priority, "state": x.state, "module": x.module, "account_id": x.account_id, "requirement_id": x.requirement_id, "execution_id": x.execution_id, "symptom": x.symptom, "root_cause": x.root_cause, "remediation": x.remediation, "prevention": x.prevention, "details": x.details_json or {}, "opened_at": x.opened_at.isoformat() if x.opened_at else None, "resolved_at": x.resolved_at.isoformat() if x.resolved_at else None} for x in rows]
    finally: db.close()


@router.get("/health")
def control_health(user=Depends(get_current_user)):
    _owner(user); db=SessionLocal()
    try:
        from app.models.control_plane import ControlModuleContract
        now=datetime.now(timezone.utc)
        rules=db.query(ControlRequirement).filter(ControlRequirement.status=='active').all()
        counts={"active":len(rules),"working":0,"violated":0,"blocked":0,"waiting_external":0,"waiting_owner":0,"stale":0,"unverified":0}
        for req in rules:
            latest=db.query(ControlExecution).filter(ControlExecution.requirement_id==req.id).order_by(ControlExecution.created_at.desc()).first()
            open_inc=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.requirement_id==req.id).count()
            if latest:
                if latest.status=='blocked': counts["blocked"]+=1
                if latest.status=='waiting_external': counts["waiting_external"]+=1
                if latest.status=='waiting_owner': counts["waiting_owner"]+=1
            contract=db.query(ControlModuleContract).filter(ControlModuleContract.module==req.module).first()
            stale_sec=max(60,int((contract.verification_stale_seconds if contract else 3600) or 3600))
            lv=req.last_verified_at
            if lv is not None and lv.tzinfo is None: lv=lv.replace(tzinfo=timezone.utc)
            if not lv:
                counts["unverified"]+=1
            elif (now-lv).total_seconds()>stale_sec:
                counts["stale"]+=1
            if open_inc:
                counts["violated"]+=1
            elif lv and (now-lv).total_seconds()<=stale_sec:
                counts["working"]+=1
        p0=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.priority=='P0').count()
        owner_policy_types=('MONEY_GUARD_MISSING_LIMIT','MONEY_GUARD_MISSING_HARD_CAP')
        p1_internal=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.priority=='P1',~ControlIncident.incident_type.in_(owner_policy_types)).count()
        waiting_owner_policy=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.incident_type.in_(owner_policy_types)).count()
        auto_fixed_today=db.query(ControlIncident).filter(ControlIncident.state=='resolved',ControlIncident.resolved_at>=now.replace(hour=0,minute=0,second=0,microsecond=0)).count()
        modules_total=db.query(ControlModuleContract).filter(ControlModuleContract.enabled.is_(True)).count()
        return {"status":"degraded" if p0 or p1_internal or counts["violated"] else "pass","rules":counts,"critical_incidents":{"P0":p0,"P1":p1_internal},"waiting_dependencies":{"owner_money_policy":waiting_owner_policy},"modules_total":modules_total,"auto_fixed_today":auto_fixed_today,"checked_at":now.isoformat()}
    finally: db.close()


@router.get("/world-state")
def world_state(account_id: Optional[str] = Query(None), user=Depends(get_current_user)):
    """Canonical BORIS System Brain snapshot for owner cockpit and automation."""
    _owner(user); db = SessionLocal()
    try:
        from app.services.brain_world_state import build_world_state
        return build_world_state(db, account_id=account_id)
    finally:
        db.close()


@router.get("/action-plan")
def action_plan(account_id: Optional[str] = Query(None), client_scope: bool = Query(False), user=Depends(get_current_user)):
    """Deterministic NOW/NEXT/WAITING/BLOCKED plan from current business state."""
    _platform_admin(user); db = SessionLocal()
    try:
        from app.services.brain_action_planner import build_action_plan
        plan=build_action_plan(db, account_id=account_id)
        if client_scope and account_id:
            # CLIENT_ACTION_PLAN_EXPECTED_SCOPE_V1: the owner client-control
            # screen must show only services this client is actually entitled to
            # receive. Never project a healthy/unhealthy global module into an
            # unrelated client merely because an adapter exists in the registry.
            from app.services.client_supervisor import load_supervisor_snapshot, build_client_state
            cstate=load_supervisor_snapshot(db,str(account_id)) or build_client_state(db,str(account_id))
            expected=set(cstate.get("expected_modules") or [])
            items=[x for x in (plan.get("items") or []) if str(x.get("module") or "") in expected]
            plan["items"]=items
            plan["routine_plan"]=[x for x in (plan.get("routine_plan") or []) if str(x.get("module") or "") in expected]
            plan["queue"]={k:[x for x in (plan.get("queue") or {}).get(k,[]) if str(x.get("module") or "") in expected] for k in ("NOW","NEXT","WAITING","BLOCKED","DONE")}
            plan["client_scope"]={"client_state":cstate.get("client_state"),"expected_modules":sorted(expected)}
        return plan
    finally:
        db.close()


@router.get("/acceptance")
def acceptance(user=Depends(get_current_user)):
    """Machine-readable BORIS Brain definition-of-done report."""
    _owner(user); db = SessionLocal()
    try:
        from app.services.brain_acceptance import build_acceptance_report
        return build_acceptance_report(db)
    finally:
        db.close()


@router.get("/traces")
def traces(account_id: Optional[str] = Query(None), hours: int = Query(24, ge=1, le=720), limit: int = Query(100, ge=1, le=500), user=Depends(get_current_user)):
    """Recent cross-module business traces and breakpoints."""
    _owner(user); db = SessionLocal()
    try:
        from app.services.brain_trace import recent_traces
        return recent_traces(db, account_id=account_id, hours=hours, limit=limit)
    finally:
        db.close()


@router.get("/traces/{account_id}/{chat_id}")
def trace_detail(account_id: str, chat_id: str, user=Depends(get_current_user)):
    """Full Messages -> MOP -> CRM -> Telephony -> ROP factual trace."""
    _owner(user); db = SessionLocal()
    try:
        from app.services.brain_trace import build_trace
        return build_trace(db, account_id, chat_id)
    finally:
        db.close()


@router.get("/selftest")
def selftest(user=Depends(get_current_user)):
    """Production-safe BORIS Brain chaos/self-test report."""
    _owner(user); db = SessionLocal()
    try:
        from app.services.brain_selftest import run_selftest
        return run_selftest(db)
    finally:
        db.close()


# WORKSPACE_THREAD_API_V1
class WorkspaceThreadBody(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    thread_type: str = "task"
    account_id: Optional[str] = None
    external_chat_ref: Optional[str] = None
    status: str = "active"
    summary: str = ""
    current_state: dict[str, Any] = {}
    plan: list[Any] = []
    source: dict[str, Any] = {}

class WorkspaceThreadPatch(BaseModel):
    title: Optional[str] = None
    account_id: Optional[str] = None
    external_chat_ref: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    current_state: Optional[dict[str, Any]] = None
    plan: Optional[list[Any]] = None

class WorkspaceEventBody(BaseModel):
    event_type: str = "note"
    actor: str = "owner"
    text: str = ""
    payload: dict[str, Any] = {}

class WorkspacePromptBody(BaseModel):
    text: str = Field(min_length=2)
    event_type: str = "owner_prompt"
    command_id: Optional[str] = Field(default=None, min_length=8, max_length=128)

class WorkspaceAutopilotBody(BaseModel):
    enabled: bool = True

class WorkspaceProgressBody(BaseModel):
    progress_percent: Optional[float] = Field(default=None, ge=0, le=100)
    current_step: Optional[str] = Field(default=None, max_length=2000)
    next_step: Optional[str] = Field(default=None, max_length=2000)
    note: Optional[str] = Field(default=None, max_length=6000)
    actor: str = Field(default="boris", max_length=64)
    evidence: dict[str, Any] = {}

def _workspace_thread_dict(x: ControlWorkspaceThread, event_count=None):
    out={"id":x.id,"thread_type":x.thread_type,"title":x.title,"account_id":x.account_id,"external_chat_ref":x.external_chat_ref,"status":x.status,"summary":x.summary or "","current_state":x.current_state_json or {},"plan":x.plan_json or [],"source":x.source_json or {},"created_at":x.created_at.isoformat() if x.created_at else None,"updated_at":x.updated_at.isoformat() if x.updated_at else None,"last_event_at":x.last_event_at.isoformat() if x.last_event_at else None}
    if event_count is not None: out["event_count"]=event_count
    return out


def _workspace_projection_match(x: ControlWorkspaceThread, projection: str | None) -> bool:
    """Owner UI projection only; never creates a second source of truth."""
    p=str(projection or "").strip().lower()
    if not p:
        return True
    source=x.source_json or {}
    state=x.current_state_json or {}
    surface=str(source.get("surface") or "")
    source_kind=str(source.get("source") or "")
    ref=str(x.external_chat_ref or "")
    if p=="client":
        return x.thread_type=="client"
    if p=="brain":
        return ref=="system-brain:owner-global" or surface=="control-plane" or source_kind=="system-brain"
    if p=="development":
        if x.thread_type=="client":
            return False
        if surface=="control-plane" or source_kind=="system-brain":
            return False
        if state.get("dev_job_id") or ref.startswith("ops-job:"):
            return True
        # RECOVERED_CONTEXT_IS_MEMORY_NOT_EXECUTION_V1:
        # Старые ветки, восстановленные только из истории чатов, не являются
        # живыми development jobs. Они остаются в durable memory/Brain, но не
        # должны показываться в окне онлайн-разработки как "автопилот не запущен".
        if source_kind=="recovered_context" and not state.get("ops_job_id"):
            return False
        if surface in {"development","journal"}:
            return True
        if source_kind in {"legacy_task","control_execution"}:
            return False
        if x.account_id:
            return False
        return x.thread_type in {"task","chat","project"}
    raise HTTPException(status_code=422,detail="invalid_workspace_projection")


def _workspace_status_from_runtime(value: str | None) -> str:
    v=str(value or "").strip().lower()
    if v in {"done","completed","complete","pass","passed","success","succeeded","verified"}:
        return "done"
    if v in {"blocked","failed","error","cancelled","canceled","degraded"}:
        return "blocked"
    if v in {"waiting","waiting_external","waiting_owner","deferred","paused","pending_external"}:
        return "waiting"
    return "active"



def _workspace_executor_key() -> dict:
    # Internal server-to-server identity for the already deployed autonomous
    # BORIS OPS/A2A contour. This is not an external API credential.
    return {
        "scope_set": {"read", "write", "tasks"},
        "actor_type": "autonomous-dev",
        "actor_name": "workspace-bridge",
        "name": "workspace-bridge",
        "id": None,
        "max_items_per_call": 200,
        "allowed_accounts": None,
    }


def _workspace_enqueue_executor(row: ControlWorkspaceThread, *, command_id: str, text_value: str) -> dict:
    """Exactly-once bridge: workspace prompt -> ext_dev_job -> A2A executor order."""
    import hashlib, json
    from app.ext_api import db as ext_db, dev as ext_dev, a2a as ext_a2a

    cid=str(command_id).strip()
    # ext_dev_jobs.request_id is varchar(40): keep a deterministic compact key.
    digest=hashlib.sha256(f"{row.id}:{cid}".encode("utf-8")).hexdigest()[:34]
    request_id=f"ws_{digest}"
    existing=ext_db.one("SELECT id,status FROM ext_dev_jobs WHERE request_id=:r ORDER BY id DESC LIMIT 1", r=request_id)
    key=_workspace_executor_key()
    raw_command=str(text_value or "").strip()
    cmd_lower=raw_command.lower()
    explicit_read_only=(
        any(marker in cmd_lower for marker in ("read-only","read only","только чтение"))
        and any(marker in cmd_lower for marker in ("ничего не измен","не изменять","только провер","только фактическ"))
    )
    first_line=" ".join(raw_command.splitlines()[:1]).strip()
    title=(f"WORKSPACE · {row.title} · {first_line}" if first_line else f"WORKSPACE · {row.title}")[:300]
    scope_paths=["backend/app","backend/tests","frontend"]
    scope={
        "source":"workspace",
        "workspace_thread_id":row.id,
        "thread_type":row.thread_type,
        "account_id":row.account_id,
        "command_id":cid,
        "read_only":bool(explicit_read_only),
        "autonomous_dev":True,
        "server_owned":True,
        # WORKSPACE_AUTOPILOT_V2: every owner command becomes a real server-owned
        # development lifecycle. The browser is only a control surface; executor,
        # reviewer, apply/deploy and recovery continue without the owner online.
        "workspace_autopilot":True,
        "workspace_autopilot_mode":"until_done",
    }
    if explicit_read_only:
        scope["inspection_paths"]=scope_paths
    else:
        scope["paths"]=scope_paths
    target=(f" Только аккаунт {row.account_id}." if row.account_id else "")
    goal=(
        "Команда владельца из постоянного Workspace BORIS. Выполнить её фактически, "
        "а не ответить отчётом. Использовать существующие модули/операции BORIS, "
        "проверить реальный runtime и записать evidence."
        + target
        + "\n\nКОМАНДА ВЛАДЕЛЬЦА:\n" + raw_command
        + "\n\nПРАВИЛА: не выдумывать PASS/проценты; не менять чужие аккаунты; "
          "не публиковать наружу и не делать необратимые/неограниченные денежные действия, "
          "если это не задано владельцем явно и безопасно; при настоящем внешнем блокере "
          "зафиксировать точную причину. После исправления проверить пользовательский сценарий."
    )
    acceptance=[
        "Фактически выполнена команда владельца, а не только сохранён текст или дан совет",
        "Есть проверяемое evidence: runtime/DB/API/tests/browser в зависимости от задачи",
        "Изменения ограничены этой workspace-веткой и указанным account_id",
        "Если выполнение невозможно безопасно — сохранён конкретный блокер и уже выполнено всё независимое от него",
    ]
    forbidden=[
        "выдумывать прогресс, PASS или результат без evidence",
        "вносить изменения в другой клиентский account_id",
        "внешняя публикация без явного разрешения владельца в самой команде/действующем правиле",
        "неограниченное денежное действие или обход money guard",
        "несвязанный рефакторинг работающих production-контуров",
    ]
    if explicit_read_only:
        acceptance[2]="Подтверждено, что production-файлы и данные не изменялись"
        forbidden.append("любые изменения production-файлов/данных: read-only команда")

    if existing:
        job_id=int(existing["id"])
        job=ext_dev.get(job_id)
    else:
        job=ext_dev.create(
            key, request_id, title, goal, acceptance,
            scope=scope, owner="gpt-executor", reviewer="claude-lead", priority=85,
            locks=([] if explicit_read_only else scope_paths),
        )
        job_id=int(str(job["dev_job_id"]).replace("dev_",""))
        try:
            ext_dev.update_phase(
                key, job["dev_job_id"], "PLANNING", "running",
                notes="workspaceAutopilot: server-owned lifecycle",
            )
            job=ext_dev.get(job_id)
        except Exception:
            # The order remains the source of execution truth; if phase marking
            # races with a recovery worker, do not duplicate or fail intake.
            job=ext_dev.get(job_id)

    # Truthful provider gate at intake time: if no free autonomous coding
    # provider is available, do not show a cosmetically runnable Job. The
    # dispatcher keeps reproving availability and will resume it automatically.
    try:
        from app.ext_api import aiprov as ext_aiprov
        if (
            str(job.get("status") or "") not in {"completed","cancelled"}
            and not ext_aiprov.development_candidates()
            and not str(job.get("wait_reason") or "").startswith("UNSCOPED_DEV_JOB")
        ):
            ext_db.q(
                "UPDATE ext_dev_jobs SET status='waiting',assigned_agent=NULL,"
                "wait_reason='WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically',updated_at=NOW() "
                "WHERE id=:i", i=job_id
            )
            job=ext_dev.get(job_id)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=742)

    order_row=ext_db.one(
        "SELECT id,status,assigned_to,claimed_by,blocked_reason FROM ext_a2a_orders "
        "WHERE dev_job_id=:d ORDER BY id DESC LIMIT 1", d=job_id
    )
    if not order_row and str(job.get("status") or "") not in {"completed","cancelled"}:
        order=ext_a2a.create_order(
            key, f"dev_{job_id}", str(job.get("current_phase") or "PLANNING"), goal,
            scope=scope, acceptance=acceptance, forbidden=forbidden,
            required_qa=["runtime","db_query","tests"],
            required_evidence=["фактический вывод runtime/DB/API/tests"],
            assigned_to="gpt-executor", max_iterations=8, priority=85,
        )
        order_id=int(order["order_id"])
        order_status=str(order.get("status") or "queued")
        assigned_to=str(order.get("assigned_to") or "gpt-executor")
    else:
        order_id=int(order_row["id"]) if order_row else None
        order_status=str(order_row.get("status") or "") if order_row else None
        assigned_to=str(order_row.get("assigned_to") or "gpt-executor") if order_row else "gpt-executor"

    return {
        "command_id":cid,
        "request_id":request_id,
        "dev_job_id":f"dev_{job_id}",
        "order_id":order_id,
        "executor":"gpt-executor",
        "executor_status":str(job.get("status") or "queued"),
        "current_phase":job.get("current_phase"),
        "order_status":order_status,
        "assigned_to":assigned_to,
        "account_id":row.account_id,
    }


def _workspace_provider_waiting_text(*values):
    low=" ".join(str(v or "").strip().lower() for v in values)
    return bool(low.strip()) and (
        "waiting_free_coding_provider" in low
        or "provider_unavailable" in low
        or "all free development providers unavailable" in low
        or "review provider quota" in low
        or "daily quota" in low
        or "rate_limited" in low
    )


def _workspace_free_provider_wait_snapshot():
    """DB-only global development-provider wait truth for owner Workspace.

    The executor lifecycle can be in REVIEW/PLANNING while every allowed free
    provider is cooling. Job.wait_reason is not guaranteed to carry that global
    condition, so the Workspace refresher must read the provider registry too.
    """
    try:
        from app.ext_api import aiprov as _aiprov
        if _aiprov.development_candidates():
            return None, None
        reason=_aiprov.development_wait_reason()
        retry_values=[]
        for name in ("gemini_cli","claude_code"):
            raw=(_aiprov.state(name) or {}).get("retry_at")
            if not raw:
                continue
            if isinstance(raw,datetime):
                value=raw
            else:
                try:
                    value=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
                except Exception:
                    continue
            if value.tzinfo is None:
                value=value.replace(tzinfo=timezone.utc)
            else:
                value=value.astimezone(timezone.utc)
            retry_values.append(value)
        retry_at=min(retry_values).isoformat() if retry_values else None
        return reason, retry_at
    except Exception:
        return None, None


def _workspace_progress_status(exec_status, *, autopilot_enabled=True, blocker=None, wait_reason=None, order_status=None):
    """Truthful progress mode for owner UI; live A2A RUNNING overrides stale parent wait."""
    st=str(exec_status or "").strip().lower()
    order=str(order_status or "").strip().lower()
    if st=="completed":
        return "verified_done"
    if not autopilot_enabled:
        return "paused"
    if order=="running":
        return "running"
    if _workspace_provider_waiting_text(blocker, wait_reason):
        return "waiting"
    if blocker or st in {"blocked","failed","blocked_infra","blocked_human"}:
        return "blocked"
    if wait_reason or st in {"waiting","queued","planning"}:
        return "waiting"
    if st in {"review","review_requested","acceptance"}:
        return "review"
    if st in {"returned","fixing"}:
        return "fixing"
    if st in {"testing","regression"}:
        return "testing"
    if st=="running":
        return "running"
    return "unknown"


def _workspace_refresh_executor_state(db, row: ControlWorkspaceThread, *, emit_event: bool=True) -> dict:
    """Refresh workspace from the authoritative executor/contract truth."""
    state=dict(row.current_state_json or {})
    if str(state.get("execution_mode") or "") == "client_operation":
        from app.services.client_execution_contract import sync_contract_from_job
        state, changed = sync_contract_from_job(db, state)
        row.current_state_json = state
        contract = state.get("execution_contract") or {}
        row.status = "blocked" if str(contract.get("status") or "") in {"blocked", "failed"} else "active"
        if changed and emit_event:
            db.add(ControlWorkspaceEvent(
                thread_id=row.id,event_type="execution_contract_sync",actor="boris",
                text=(f"Строгий контракт: {contract.get('status') or 'unknown'} · "
                      f"результаты {contract.get('criteria_done') or 0}/{contract.get('criteria_total') or 0}"),
                payload_json={
                    "execution_id":contract.get("execution_id"),
                    "command_id":contract.get("command_id"),
                    "status":contract.get("status"),
                    "criteria_done":contract.get("criteria_done"),
                    "criteria_total":contract.get("criteria_total"),
                },
            ))
            row.last_event_at=datetime.now(timezone.utc)
        return {"changed":changed,"state":state}
    raw_dev=state.get("dev_job_id")
    if not raw_dev and state.get("executor_status")=="enqueue_failed":
        attempts=int(state.get("execution_enqueue_attempts") or 0)
        cid=str(state.get("last_command_id") or "")
        cmd=str(state.get("last_command_text") or "")
        if attempts < 3 and cid and cmd:
            try:
                execution=_workspace_enqueue_executor(row,command_id=cid,text_value=cmd)
                state.update(execution)
                state["execution_enqueue_error"]=None
                state["execution_enqueue_attempts"]=attempts+1
                row.current_state_json=state
                raw_dev=state.get("dev_job_id")
                if emit_event:
                    db.add(ControlWorkspaceEvent(
                        thread_id=row.id,event_type="execution_requeued",actor="boris",
                        text=f"Самовосстановление: команда повторно поставлена исполнителю · {execution.get('dev_job_id')}",
                        payload_json=execution,
                    ))
                    row.last_event_at=datetime.now(timezone.utc)
            except Exception as retry_exc:
                state["execution_enqueue_attempts"]=attempts+1
                state["execution_enqueue_error"]=type(retry_exc).__name__
                row.current_state_json=state
                if emit_event and attempts+1>=3:
                    db.add(ControlWorkspaceEvent(
                        thread_id=row.id,event_type="execution_escalated",actor="boris",
                        text="Автопостановка команды не удалась после ограниченных повторов; нужен разбор инфраструктуры.",
                        payload_json={"command_id":cid,"attempts":attempts+1,"error_type":type(retry_exc).__name__},
                    ))
                    row.last_event_at=datetime.now(timezone.utc)
                return {"changed":True,"state":state,"error":type(retry_exc).__name__}
    if not raw_dev:
        return {"changed":False,"state":state}
    try:
        import json
        from app.ext_api import db as ext_db
        dev_id=int(str(raw_dev).replace("dev_",""))
        job=ext_db.one(
            "SELECT id,status,current_phase,assigned_agent,wait_reason,blocked_reason,"
            "phases_json,acceptance_json,scope_json,qa_status,deployment_status,updated_at "
            "FROM ext_dev_jobs WHERE id=:i", i=dev_id
        )
        if not job:
            return {"changed":False,"state":state,"error":"dev_job_missing"}
        order=ext_db.one(
            "SELECT id,status,assigned_to,claimed_by,blocked_reason,iteration,max_iterations,updated_at "
            "FROM ext_a2a_orders WHERE dev_job_id=:d ORDER BY id DESC LIMIT 1", d=dev_id
        )
        try: phases=json.loads(job.get("phases_json") or "{}") if isinstance(job.get("phases_json"),str) else (job.get("phases_json") or {})
        except Exception: phases={}
        try: criteria=json.loads(job.get("acceptance_json") or "[]") if isinstance(job.get("acceptance_json"),str) else (job.get("acceptance_json") or [])
        except Exception: criteria=[]
        try: scope=json.loads(job.get("scope_json") or "{}") if isinstance(job.get("scope_json"),str) else (job.get("scope_json") or {})
        except Exception: scope={}
        autopilot_enabled=scope.get("workspace_autopilot") is not False
        autopilot_mode=str(scope.get("workspace_autopilot_mode") or "until_done")
        phase_pass=sum(1 for v in phases.values() if isinstance(v,dict) and v.get("state")=="pass")
        criteria_done=sum(1 for v in criteria if isinstance(v,dict) and v.get("done"))
        exec_status=str(job.get("status") or "queued")
        order_status=str(order.get("status") or "").strip().lower() if order else None
        runtime_status="running" if order_status=="running" else exec_status
        # WORKSPACE_ORDER_BLOCKER_TRUTH_V1: latest A2A order is execution truth.
        # Parent dev_job blockers can legitimately lag behind scope/dedup/provider
        # transitions. Never overwrite the owner's current blocker with that stale
        # parent diagnosis when the child is explicitly blocked.
        order_blocked=str((order or {}).get("blocked_reason") or "").strip()
        parent_blocked=str(job.get("blocked_reason") or "").strip()
        blocked=(
            ""
            if order_status=="running"
            else (
                order_blocked
                if order_status in {"blocked_infra","blocked_human"} and order_blocked
                else parent_blocked
            )
        )
        owner_action_required=bool(order_status=="blocked_human")
        wait=str(job.get("wait_reason") or "")
        global_provider_wait, provider_retry_at=_workspace_free_provider_wait_snapshot()
        # A currently claimed order is factual execution even if the provider
        # registry changed while it was running. Likewise a real non-provider
        # blocker must stay BLOCKED rather than being hidden by a global quota.
        can_apply_global_provider_wait=bool(
            global_provider_wait
            and order_status!="running"
            and exec_status not in {"completed","cancelled","failed","blocked"}
            and not owner_action_required
            and not blocked
        )
        effective_wait="" if order_status=="running" else (wait or (global_provider_wait if can_apply_global_provider_wait else ""))
        provider_waiting=_workspace_provider_waiting_text(blocked, effective_wait)
        phase=str(job.get("current_phase") or "")
        before={k:state.get(k) for k in (
            "executor_status","order_status","owner_action_required","current_phase","phase_passed","phase_total",
            "criteria_done","criteria_total","qa_status","deployment_status","executor_blocker","executor_wait_reason",
            "provider_retry_at","autopilot_state","progress_status"
        )}
        state.update({
            "dev_job_id":f"dev_{dev_id}",
            "order_id":int(order["id"]) if order else state.get("order_id"),
            "executor":"gpt-executor",
            "assigned_to":str((order or {}).get("claimed_by") or (order or {}).get("assigned_to") or job.get("assigned_agent") or "gpt-executor"),
            "executor_status":runtime_status,
            "dev_job_status":exec_status,
            "order_status":order_status,
            "owner_action_required":owner_action_required,
            "current_phase":phase,
            "phase_passed":phase_pass,
            "phase_total":len(phases),
            "criteria_done":criteria_done,
            "criteria_total":len(criteria),
            "qa_status":job.get("qa_status"),
            "deployment_status":job.get("deployment_status"),
            "executor_blocker":blocked or None,
            "executor_wait_reason":effective_wait or None,
            "provider_retry_at":provider_retry_at if provider_waiting else None,
            "autopilot_enabled":autopilot_enabled,
            "autopilot_mode":autopilot_mode,
            "autopilot_state":(
                "done" if exec_status=="completed" else
                ("paused" if not autopilot_enabled else
                 ("working" if order_status=="running" else
                  ("waiting" if provider_waiting else
                   ("blocked" if blocked or exec_status in {"blocked","failed"} or order_status in {"blocked_infra","blocked_human"} else
                    ("waiting" if runtime_status in {"waiting","queued","planning"} or bool(effective_wait) else "working")))))
            ),
            "autopilot_iteration":int((order or {}).get("iteration") or 0),
            "autopilot_iteration_limit":int((order or {}).get("max_iterations") or 0) if order else None,
            "last_executor_sync_at":datetime.now(timezone.utc).isoformat(),
        })
        phase_ru={
            "PLANNING":"Планирование","BACKEND":"Серверная часть","FRONTEND":"Интерфейс",
            "UX":"Проверка удобства","INTEGRATION":"Связи модулей","TESTING":"Тестирование",
            "REGRESSION":"Проверка, что старое не сломалось","ACCEPTANCE":"Приёмка",
            "DEPLOYMENT":"Выкладка","PRODUCTION_QA":"Проверка на рабочем сервере","QUEUE":"Очередь",
        }.get(phase,phase or "Очередь")
        status_ru={
            "queued":"в очереди","planning":"планируется","running":"выполняется",
            "review":"проверяется","review_requested":"проверяется","returned":"исправляется",
            "waiting":"ожидает","blocked":"заблокировано","failed":"ошибка",
            "completed":"выполнено","cancelled":"остановлено",
        }.get(runtime_status,runtime_status)
        if row.thread_type!="client":
            state["current_step"]=f"Автопилот BORIS: {phase_ru} · {status_ru}"
            if exec_status=="completed":
                state["progress_percent"]=100.0
                state["progress_status"]=_workspace_progress_status(runtime_status, autopilot_enabled=autopilot_enabled, blocker=blocked, wait_reason=effective_wait, order_status=order_status)
                state["next_step"]="Нет — команда выполнена и результат подтверждён проверками"
            elif not autopilot_enabled:
                state["progress_percent"]=None
                state["progress_status"]=_workspace_progress_status(runtime_status, autopilot_enabled=autopilot_enabled, blocker=blocked, wait_reason=effective_wait, order_status=order_status)
                state["next_step"]="Автопилот на паузе. После включения BORIS продолжит с текущего места"
            elif provider_waiting:
                state["progress_percent"]=None
                state["progress_status"]="waiting"
                state["next_step"]=effective_wait or "BORIS ждёт восстановления бесплатного ИИ и продолжит автоматически"
            elif blocked or exec_status in {"blocked","failed"} or order_status in {"blocked_infra","blocked_human"}:
                state["progress_percent"]=None
                state["progress_status"]=_workspace_progress_status(runtime_status, autopilot_enabled=autopilot_enabled, blocker=blocked, wait_reason=effective_wait, order_status=order_status)
                state["next_step"]=blocked or effective_wait or "Разобрать зафиксированный блокер"
            else:
                state["progress_percent"]=None
                state["progress_status"]=_workspace_progress_status(runtime_status, autopilot_enabled=autopilot_enabled, blocker=blocked, wait_reason=effective_wait, order_status=order_status)
                state["next_step"]="BORIS сам продолжает задачу; следующий факт появится после проверки результата"
        else:
            state["current_step"]=f"Последняя команда: {phase or 'QUEUE'} · {exec_status}"
            state["next_step"]=(
                "Команда выполнена; BORIS продолжает клиентский контур"
                if exec_status=="completed"
                else (blocked or effective_wait or "Исполнитель продолжает команду")
            )
        after={k:state.get(k) for k in before}
        changed=before!=after
        row.current_state_json=state
        if row.thread_type!="client":
            if exec_status=="completed":
                row.status=_workspace_status_from_runtime(exec_status)
            elif not autopilot_enabled or provider_waiting:
                row.status="waiting"
            elif owner_action_required or blocked or order_status in {"blocked_infra","blocked_human"}:
                row.status="blocked"
            else:
                row.status=_workspace_status_from_runtime(exec_status)
        else:
            row.status="active"
        if changed and emit_event:
            db.add(ControlWorkspaceEvent(
                thread_id=row.id,event_type="executor_sync",actor="boris",
                text=f"Автопилот BORIS: {status_ru} · {phase_ru} · критерии {criteria_done}/{len(criteria)}",
                payload_json={
                    "dev_job_id":f"dev_{dev_id}","order_id":int(order["id"]) if order else None,
                    "executor_status":exec_status,"order_status":order_status,"phase":phase,
                    "phase_passed":phase_pass,"phase_total":len(phases),
                    "criteria_done":criteria_done,"criteria_total":len(criteria),
                    "qa_status":job.get("qa_status"),"deployment_status":job.get("deployment_status"),
                    "blocked_reason":blocked or None,"wait_reason":effective_wait or None,"provider_retry_at":provider_retry_at if provider_waiting else None,
                },
            ))
            row.last_event_at=datetime.now(timezone.utc)
        return {"changed":changed,"state":state}
    except Exception as exc:
        err=type(exc).__name__
        if state.get("executor_sync_error")!=err:
            state["executor_sync_error"]=err
            row.current_state_json=state
            if emit_event:
                db.add(ControlWorkspaceEvent(
                    thread_id=row.id,event_type="executor_sync_error",actor="boris",
                    text="Не удалось прочитать состояние исполнителя; команда не считается выполненной.",
                    payload_json={"error_type":err,"dev_job_id":raw_dev},
                ))
                row.last_event_at=datetime.now(timezone.utc)
        return {"changed":False,"state":state,"error":err}


def _workspace_sync_runtime_refs(db, *, user=None, limit: int=500, account_id: str | None=None) -> dict:
    """Idempotently materialize AND refresh legacy Task / ControlExecution threads.

    The workspace is an owner-facing truth projection, not a one-time import.
    Existing durable threads are updated only when factual runtime state changes;
    every such change emits one runtime_sync event with before/after evidence.
    """
    import json, uuid
    limit=max(1,min(int(limit or 500),2000))
    now=datetime.now(timezone.utc)
    created_tasks=created_execs=updated_tasks=updated_execs=unchanged_tasks=unchanged_execs=0
    bootstrapped_tasks=bootstrapped_execs=0

    tq=db.query(Task)
    if account_id: tq=tq.filter(Task.account_id==account_id)
    tasks=tq.order_by(Task.updated_at.desc(),Task.created_at.desc()).limit(limit).all()

    xq=db.query(ControlExecution)
    if account_id: xq=xq.filter(ControlExecution.account_id==account_id)
    _raw_execs=xq.order_by(ControlExecution.updated_at.desc(),ControlExecution.created_at.desc()).limit(limit).all()
    # WORKSPACE_EXECUTION_SCOPE_DEDUPE_V1: repeated hourly executions are runtime
    # history, not separate owner tasks. Materialize only the newest execution
    # for each requirement+account scope so the journal cannot grow duplicates.
    execs=[]; _seen_exec_scopes=set()
    for x in _raw_execs:
        _scope=(str(x.requirement_id or x.module or "unknown"), str(x.account_id or "__global__"))
        if _scope in _seen_exec_scopes:
            continue
        _seen_exec_scopes.add(_scope); execs.append(x)

    def _execution_workspace_ref(x):
        return f"execution-scope:{x.requirement_id or x.module or 'unknown'}:{x.account_id or '__global__'}"

    exec_ids=[x.id for x in execs]
    steps_by_exec={}
    if exec_ids:
        for s in db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id.in_(exec_ids)).order_by(ControlExecutionStep.execution_id,ControlExecutionStep.step_no).all():
            steps_by_exec.setdefault(s.execution_id,[]).append(s)
    req_ids={x.requirement_id for x in execs if x.requirement_id}
    reqs={r.id:r for r in db.query(ControlRequirement).filter(ControlRequirement.id.in_(req_ids)).all()} if req_ids else {}

    evidence_by_exec={}
    if exec_ids:
        for ev in db.query(ControlEvidence).filter(ControlEvidence.execution_id.in_(exec_ids)).order_by(ControlEvidence.execution_id,ControlEvidence.observed_at.asc(),ControlEvidence.id.asc()).all():
            bucket=evidence_by_exec.setdefault(ev.execution_id,{"total":0,"passed":0,"last":None})
            bucket["total"]+=1
            if bool(ev.passed): bucket["passed"]+=1
            bucket["last"]=ev

    refs=[f"task:{t.id}" for t in tasks]+[_execution_workspace_ref(x) for x in execs]
    existing={}
    if refs:
        existing={r.external_chat_ref:r for r in db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.external_chat_ref.in_(refs)).all()}

    def numeric_progress(payload):
        for key in ("progress_percent","progress_pct","completion_pct","percent","progress"):
            raw=(payload or {}).get(key)
            try:
                n=float(raw)
            except Exception:
                continue
            if n==n:
                return max(0.0,min(100.0,round(n,1)))
        return None

    def upsert_runtime(*,ref,title,account_id_value,summary,source,state,target_status,kind):
        nonlocal created_tasks,created_execs,updated_tasks,updated_execs,unchanged_tasks,unchanged_execs,bootstrapped_tasks,bootstrapped_execs
        row=existing.get(ref)
        # WORKSPACE_EXECUTION_RUNTIME_NOT_TASK_V1: ControlExecution projections must never
        # re-enter the owner/development task registry; stable runtime rows preserve history.
        # WORKSPACE_RUNTIME_NOT_OWNER_TASK_V2: legacy BackgroundJob Task and
        # ControlExecution are telemetry projections, not owner/development tasks.
        # Keep both as runtime so the development registry cannot be polluted.
        desired_thread_type="runtime"
        if row is None:
            state=dict(state)
            state["last_runtime_sync_at"]=now.isoformat()
            row=ControlWorkspaceThread(
                id=str(uuid.uuid4()),thread_type=desired_thread_type,title=title[:500],
                account_id=account_id_value,external_chat_ref=ref,status=target_status,
                summary=summary,current_state_json=state,plan_json=[],source_json=source,
                created_by=getattr(user,"id",None) if user is not None else None,
            )
            db.add(row); db.flush(); existing[ref]=row
            db.add(ControlWorkspaceEvent(
                thread_id=row.id,event_type="materialized",actor="boris",text=summary,
                payload_json={**source,"progress_percent":state.get("progress_percent"),"runtime_status":target_status},
            ))
            row.last_event_at=now
            if kind=="task": created_tasks+=1
            else: created_execs+=1
            return row

        before_state=dict(row.current_state_json or {})
        merged=dict(before_state); merged.update(state)
        source_merged=dict(row.source_json or {}); source_merged.update(source)
        comparable_before={k:v for k,v in before_state.items() if k!="last_runtime_sync_at"}
        comparable_after={k:v for k,v in merged.items() if k!="last_runtime_sync_at"}
        changed=(
            row.status!=target_status
            or row.thread_type!=desired_thread_type
            or row.account_id!=account_id_value
            or row.title!=title[:500]
            or comparable_after!=comparable_before
        )
        if not changed:
            if kind=="task": unchanged_tasks+=1
            else: unchanged_execs+=1
            return row

        bootstrap=(
            "last_runtime_sync_at" not in before_state
            and str((row.source_json or {}).get("source") or "") in {"legacy_task","control_execution"}
        )
        before_progress=before_state.get("progress_percent")
        merged["last_runtime_sync_at"]=now.isoformat()
        row.status=target_status
        row.thread_type=desired_thread_type
        row.account_id=account_id_value
        row.title=title[:500]
        row.summary=summary
        row.current_state_json=merged
        row.source_json=source_merged
        row.last_event_at=now

        if bootstrap:
            if kind=="task": bootstrapped_tasks+=1
            else: bootstrapped_execs+=1
            return row
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,event_type="runtime_sync",actor="boris",
            text=f"Runtime обновлён: {before_progress if before_progress is not None else '—'}% → {merged.get('progress_percent') if merged.get('progress_percent') is not None else '—'}%; статус {target_status}.",
            payload_json={
                "source":source.get("source"),"runtime_ref":ref,
                "before_progress_percent":before_progress,
                "progress_percent":merged.get("progress_percent"),
                "runtime_status":target_status,
                "current_step":merged.get("current_step"),
                "next_step":merged.get("next_step"),
            },
        ))
        if kind=="task": updated_tasks+=1
        else: updated_execs+=1
        return row

    for t in tasks:
        try: payload=json.loads(t.payload) if t.payload else {}
        except Exception: payload={}
        try: result=json.loads(t.result) if t.result else {}
        except Exception: result={}
        if not isinstance(payload,dict): payload={}
        if not isinstance(result,dict): result={}
        raw_status=str(t.status or "")
        target_status=_workspace_status_from_runtime(raw_status)
        pct=100.0 if target_status=="done" else (numeric_progress(result) if numeric_progress(result) is not None else numeric_progress(payload))
        title=str(payload.get("title") or payload.get("name") or payload.get("direction") or payload.get("product_name") or result.get("title") or t.task_type or f"Задача {t.id}")
        block_reason=str(t.error_message or result.get("error") or result.get("error_message") or result.get("block_reason") or "").strip()
        result_note=str(result.get("message") or result.get("summary") or result.get("status_text") or "").strip()
        current=str(payload.get("current_step") or result.get("current_step") or payload.get("phase") or result.get("phase") or (f"Ошибка: {block_reason}" if block_reason else f"Legacy task: {raw_status or 'unknown'}"))
        next_value="Нет" if target_status=="done" else str(payload.get("next_step") or result.get("next_step") or (block_reason if target_status=="blocked" and block_reason else ("Исправить блокер / повторить безопасно" if target_status=="blocked" else "Продолжить/проверить фактическое выполнение")))
        state={
            "legacy_task_id":t.id,"progress_percent":pct,
            "progress_status":"measured" if pct is not None else "unknown",
            "current_step":current,"next_step":next_value,
            "runtime_status":raw_status,"block_reason":block_reason or None,
            "result_summary":result_note or None,"memory_mode":"durable_workspace",
        }
        upsert_runtime(
            ref=f"task:{t.id}",title=title,account_id_value=t.account_id,
            summary=f"Системная задача #{t.id} · {t.task_type}",
            source={"source":"legacy_task","task_id":t.id},state=state,
            target_status=target_status,kind="task",
        )

    done_step_status={"done","pass","completed","verified"}
    for x in execs:
        req=reqs.get(x.requirement_id)
        title=req.title if req else f"{x.module}: выполнение требования"
        steps=steps_by_exec.get(x.id,[])
        done=sum(1 for s in steps if str(s.status or "").lower() in done_step_status)
        target_status=_workspace_status_from_runtime(x.status)
        pct=100.0 if target_status=="done" else (round(done*100.0/len(steps),1) if steps else None)
        next_pending=next((s for s in steps if str(s.status or "").lower() not in done_step_status),None)
        current=(f"{next_pending.step_no}. {next_pending.name} · {next_pending.status}" if next_pending else f"Control Plane execution: {x.status}")
        next_value="Нет" if target_status=="done" else str(x.block_reason or (next_pending.name if next_pending else "Продолжить выполнение"))
        ev_info=evidence_by_exec.get(x.id) or {"total":0,"passed":0,"last":None}
        last_ev=ev_info.get("last")
        state={
            "execution_id":x.id,"requirement_id":x.requirement_id,
            "progress_percent":pct,"progress_status":"measured" if pct is not None else "unknown",
            "current_step":current,"next_step":next_value,
            "steps_done":done,"steps_total":len(steps),"attempt":x.attempt,
            "runtime_status":x.status,"block_reason":x.block_reason or x.error_message or None,
            "error_code":x.error_code or None,
            "evidence_total":int(ev_info.get("total") or 0),
            "evidence_passed":int(ev_info.get("passed") or 0),
            "last_evidence_type":getattr(last_ev,"evidence_type",None) if last_ev else None,
            "last_evidence_level":getattr(last_ev,"level",None) if last_ev else None,
            "last_evidence_passed":bool(getattr(last_ev,"passed",False)) if last_ev else None,
            "last_evidence_at":last_ev.observed_at.isoformat() if last_ev and last_ev.observed_at else None,
            "memory_mode":"durable_workspace",
        }
        upsert_runtime(
            ref=_execution_workspace_ref(x),title=title,account_id_value=x.account_id,
            summary=f"Control Plane execution · {x.module} · {x.status}",
            source={"source":"control_execution","execution_id":x.id,"requirement_id":x.requirement_id},
            state=state,target_status=target_status,kind="execution",
        )

    return {
        "created":{"tasks":created_tasks,"executions":created_execs},
        "bootstrapped":{"tasks":bootstrapped_tasks,"executions":bootstrapped_execs},
        "updated":{"tasks":updated_tasks,"executions":updated_execs},
        "unchanged":{"tasks":unchanged_tasks,"executions":unchanged_execs},
        "scanned":{"tasks":len(tasks),"executions":len(execs)},
    }

@router.get("/workspace/threads")
def workspace_threads(q: Optional[str]=None, account_id: Optional[str]=None, thread_type: Optional[str]=None, status: Optional[str]=None, projection: Optional[str]=None, sync_runtime: bool=Query(True), refresh_executor: bool=Query(True), limit: int=Query(100,ge=1,le=2000), user=Depends(get_current_user)):
    _platform_admin(user); db=SessionLocal()
    try:
        # WORKSPACE_RUNTIME_AUTO_SYNC_V1: initial journal load materializes real
        # Task/ControlExecution work. Live polling may skip the expensive global
        # materialization and refresh only the rows it asks for.
        if sync_runtime:
            _workspace_sync_runtime_refs(db,user=user,limit=max(500,limit),account_id=account_id)
            db.commit()
        if projection and projection not in {"development","client","brain"}:
            raise HTTPException(status_code=422,detail="invalid_workspace_projection")
        query=db.query(ControlWorkspaceThread)
        if account_id: query=query.filter(ControlWorkspaceThread.account_id==account_id)
        if thread_type: query=query.filter(ControlWorkspaceThread.thread_type==thread_type)
        if status=="open": query=query.filter(ControlWorkspaceThread.status.in_(("active","waiting","blocked")))
        elif status: query=query.filter(ControlWorkspaceThread.status==status)
        if q:
            needle=f"%{q.strip()}%"
            query=query.filter((ControlWorkspaceThread.title.ilike(needle)) | (ControlWorkspaceThread.summary.ilike(needle)) | (ControlWorkspaceThread.external_chat_ref.ilike(needle)))
        fetch_limit=2000 if projection else limit
        rows=query.order_by(ControlWorkspaceThread.last_event_at.desc(),ControlWorkspaceThread.updated_at.desc()).limit(fetch_limit).all()
        if projection:
            rows=[x for x in rows if _workspace_projection_match(x,projection)][:limit]
        if refresh_executor:
            for x in rows:
                _workspace_refresh_executor_state(db,x)
            db.commit()
        ids=[x.id for x in rows]
        event_counts={}
        if ids:
            event_counts={str(tid):int(cnt) for tid,cnt in db.query(ControlWorkspaceEvent.thread_id,func.count(ControlWorkspaceEvent.id)).filter(ControlWorkspaceEvent.thread_id.in_(ids)).group_by(ControlWorkspaceEvent.thread_id).all()}
        return [_workspace_thread_dict(x,event_counts.get(str(x.id),0)) for x in rows]
    finally: db.close()

# WORKSPACE_CANONICAL_SCOPE_ALIAS_V1: recovered/neighbor chats that describe the
# same Avito money autopilot must attach to one canonical workspace instead of
# creating another task with a different title. Keep this intentionally narrow:
# Yandex Direct and content/feed remain separate scopes.
# WORKSPACE_CANONICAL_SCOPE_REGISTRY_V2: owner-visible task ownership is
# explicit and mutually exclusive. Keep aliases narrow: client workspaces may
# mention KPI/content as telemetry, but they never become implementation owners.
def _workspace_canonical_task_scope(title: str, summary: str = "") -> str | None:
    blob=(str(title or "")+" "+str(summary or "")).lower()
    registry = (
        ("yandex_direct", ("яндекс директ", "yandex direct", "ии-директолог")),
        ("lead_quality", ("фильтр соискателей", "job-seeker", "job seeker")),
        ("avito_content_feed", ("feed factory", "avito_feed_validator", "проверка фото и карточек", "фото категории")),
        ("posting_reliability", ("автопостинг boris", "p0_vk_posting", "p0_tg_posting")),
        ("client_control_center", ("центр управления клиентами", "онлайн-центр управления по каждому клиенту")),
        ("control_plane_supervisor", ("единый мозг boris", "центральный мозг boris", "control plane")),
    )
    for scope, aliases in registry:
        if any(x in blob for x in aliases):
            return scope
    direct_alias=any(x in blob for x in (
        "автопилот ставок", "kpi-автопилот", "кпи-автопилот",
        "cpx_safety", "продвижение: защита от повторения старой аварии",
    ))
    marketer_money=("ии-маркетолог" in blob and any(x in blob for x in ("kpi","кпи","cpl","став","бюджет","cpx")))
    if direct_alias or marketer_money:
        return "avito_money_policy"
    return None


def _workspace_find_canonical_scope(db, scope: str):
    if not scope:
        return None
    rows=(db.query(ControlWorkspaceThread)
          .filter(ControlWorkspaceThread.thread_type=="task",
                  ControlWorkspaceThread.status.in_(("active","waiting","blocked","done")))
          .order_by(ControlWorkspaceThread.updated_at.desc()).all())
    for row in rows:
        if str((row.source_json or {}).get("canonical_scope") or "")==scope:
            return row
    return None


@router.post("/workspace/threads")
def workspace_thread_create(body: WorkspaceThreadBody, user=Depends(get_current_user)):
    _platform_admin(user); db=SessionLocal()
    try:
        import uuid
        if body.thread_type not in {"task","client","chat","project"}: raise HTTPException(status_code=422,detail="invalid_thread_type")
        if body.status not in {"active","waiting","blocked","done","archived"}: raise HTTPException(status_code=422,detail="invalid_status")
        canonical_scope=_workspace_canonical_task_scope(body.title,body.summary) if body.thread_type=="task" else None
        if canonical_scope:
            from sqlalchemy import text as _sql_text
            db.execute(_sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"),{"k":f"workspace-task-scope:{canonical_scope}"})
            existing_scope=_workspace_find_canonical_scope(db,canonical_scope)
            if existing_scope:
                db.add(ControlWorkspaceEvent(thread_id=existing_scope.id,event_type="scope_alias_hit",actor="boris",text=f"Новая формулировка задачи «{body.title[:180]}» привязана к существующему canonical scope; дубль не создан.",payload_json={"canonical_scope":canonical_scope,"requested_title":body.title,"external_chat_ref":body.external_chat_ref}))
                existing_scope.last_event_at=datetime.now(timezone.utc); db.commit()
                return {"status":"canonical_exists","thread":_workspace_thread_dict(existing_scope)}
        if body.thread_type=="client":
            if not body.account_id:
                raise HTTPException(status_code=422,detail="client_account_required")
            # CLIENT_WORKSPACE_SINGLETON_V1: serialize creation across both API
            # replicas so one client can never get split persistent memory.
            from sqlalchemy import text as _sql_text
            db.execute(_sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                       {"k":f"workspace-client:{body.account_id}"})
            existing_client=(db.query(ControlWorkspaceThread)
                             .filter(ControlWorkspaceThread.thread_type=="client",
                                     ControlWorkspaceThread.account_id==body.account_id)
                             .order_by(ControlWorkspaceThread.last_event_at.desc())
                             .first())
            if existing_client:
                return {"status":"exists","thread":_workspace_thread_dict(existing_client)}
        if body.external_chat_ref:
            # WORKSPACE_REF_SINGLETON_V1: external refs (including the global
            # system-brain ref) are exactly-once even across API replicas/tabs.
            from sqlalchemy import text as _sql_text
            db.execute(_sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                       {"k":f"workspace-ref:{body.external_chat_ref}"})
            existing=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.external_chat_ref==body.external_chat_ref).first()
            if existing: return {"status":"exists","thread":_workspace_thread_dict(existing)}
        initial_state=dict(body.current_state or {})
        if body.thread_type=="task":
            initial_state.setdefault("progress_percent",None)
            initial_state.setdefault("progress_status","captured")
            initial_state.setdefault("current_step","Задача зафиксирована")
            initial_state.setdefault("next_step","BORIS начнёт выполнение после первой команды и дальше продолжит сам")
            initial_state.setdefault("autopilot_enabled",True)
            initial_state.setdefault("autopilot_mode","until_done")
            initial_state.setdefault("autopilot_state","ready")
            initial_state.setdefault("memory_mode","durable_workspace")
        elif body.thread_type=="client":
            initial_state.setdefault("memory_mode","durable_client_workspace")
            # CLIENT_WORKSPACE_MONEY_OBSERVER_ONLY_V1: client workspaces own
            # context/readiness, never a second Avito money executor. All bid,
            # CPX, budget and CPL writes belong to the one canonical money thread.
            initial_state.setdefault("money_execution_owner","65caf765-57d4-4edc-9fce-1f24f75882cd")
            initial_state.setdefault("money_execution_mode","observe_only")
            initial_state.setdefault("no_separate_money_engine",True)
        source_payload=dict(body.source or {})
        if body.thread_type=="client":
            source_payload.setdefault("canonical_money_owner_thread_id","65caf765-57d4-4edc-9fce-1f24f75882cd")
            source_payload.setdefault("money_execution_mode","observe_only")
            source_payload.setdefault("no_separate_money_engine",True)
        if canonical_scope: source_payload.setdefault("canonical_scope",canonical_scope)
        row=ControlWorkspaceThread(id=str(uuid.uuid4()),thread_type=body.thread_type,title=body.title,account_id=body.account_id,external_chat_ref=body.external_chat_ref,status=body.status,summary=body.summary,current_state_json=initial_state,plan_json=body.plan,source_json=source_payload,created_by=getattr(user,"id",None))
        db.add(row); db.flush(); db.add(ControlWorkspaceEvent(thread_id=row.id,event_type="created",actor="owner",text=body.summary or body.title,payload_json={"source":body.source})); db.commit(); db.refresh(row)
        return {"status":"created","thread":_workspace_thread_dict(row,1)}
    finally: db.close()

@router.get("/workspace/threads/{thread_id}")
def workspace_thread_detail(thread_id: str, user=Depends(get_current_user)):
    _platform_admin(user); db=SessionLocal()
    try:
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        _workspace_refresh_executor_state(db,row)
        db.commit(); db.refresh(row)
        events=db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==thread_id).order_by(ControlWorkspaceEvent.id.asc()).limit(1000).all()
        return {"thread":_workspace_thread_dict(row,len(events)),"events":[{"id":e.id,"event_type":e.event_type,"actor":e.actor,"text":e.text,"payload":e.payload_json or {},"instruction_id":e.instruction_id,"created_at":e.created_at.isoformat() if e.created_at else None} for e in events]}
    finally: db.close()

@router.patch("/workspace/threads/{thread_id}")
def workspace_thread_patch(thread_id: str, body: WorkspaceThreadPatch, user=Depends(get_current_user)):
    _platform_admin(user); db=SessionLocal()
    try:
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        _workspace_refresh_executor_state(db,row,emit_event=False)
        patch=body.model_dump(exclude_none=True)
        state=dict(row.current_state_json or {})
        if patch.get("status")=="done":
            # OWNER_CANNOT_FAKE_EXECUTOR_COMPLETION_V2: any runtime-linked task
            # (workspace dev job, legacy Task, ControlExecution, BORIS OPS mirror)
            # may become done only from canonical runtime truth. Owner clicks never
            # manufacture terminal state or 100% progress.
            if state.get("dev_job_id") and state.get("executor_status")!="completed":
                raise HTTPException(status_code=409,detail="execution_not_completed")
            runtime_ref=str(row.external_chat_ref or "")
            if runtime_ref.startswith(("task:","execution:","ops-job:")):
                try: canonical_pct=float(state.get("progress_percent"))
                except Exception: canonical_pct=-1.0
                if row.status!="done" or canonical_pct < 100.0:
                    raise HTTPException(status_code=409,detail="runtime_evidence_not_completed")
        for name,value in patch.items():
            setattr(row,{"current_state":"current_state_json","plan":"plan_json"}.get(name,name),value)
        if patch.get("status")=="done":
            state=dict(row.current_state_json or {})
            # Manual workflow close is not factual execution evidence. Never
            # manufacture 100% from an owner click.
            state["owner_closed_at"]=datetime.now(timezone.utc).isoformat()
            state["last_progress_actor"]="owner"
            if not state.get("dev_job_id"):
                state["progress_status"]="owner_closed_unverified"
            row.current_state_json=state
        db.add(ControlWorkspaceEvent(thread_id=row.id,event_type="state_update",actor="owner",text="Состояние рабочей ветки обновлено",payload_json=patch))
        row.last_event_at=datetime.now(timezone.utc); db.commit(); db.refresh(row)
        return _workspace_thread_dict(row)
    finally: db.close()

@router.post("/workspace/threads/{thread_id}/events")
def workspace_thread_event(thread_id: str, body: WorkspaceEventBody, user=Depends(get_current_user)):
    _platform_admin(user); db=SessionLocal()
    try:
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        ev=ControlWorkspaceEvent(thread_id=row.id,event_type=body.event_type[:32],actor=body.actor[:64],text=body.text,payload_json=body.payload)
        db.add(ev); row.last_event_at=datetime.now(timezone.utc); db.commit(); db.refresh(ev)
        return {"status":"pass","event_id":ev.id,"thread_id":row.id}
    finally: db.close()

@router.post("/workspace/threads/{thread_id}/progress")
def workspace_thread_progress(thread_id: str, body: WorkspaceProgressBody, user=Depends(get_current_user)):
    """Persist owner notes without allowing owner-entered percentages to become factual progress.

    WORKSPACE_NO_OWNER_FAKE_PROGRESS_V1: canonical progress is produced by runtime
    synchronization/evidence. This owner-authenticated endpoint may record an estimate,
    but an estimate never marks work done and never replaces measured progress.
    """
    _platform_admin(user); db=SessionLocal()
    try:
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        state=dict(row.current_state_json or {})
        if body.progress_percent is not None:
            state["owner_reported_progress_percent"]=round(float(body.progress_percent),1)
            state["owner_reported_progress_at"]=datetime.now(timezone.utc).isoformat()
            if state.get("progress_percent") is None:
                state["progress_status"]="owner_reported_not_verified"
        if body.current_step is not None: state["owner_note_current_step"]=body.current_step
        if body.next_step is not None: state["owner_note_next_step"]=body.next_step
        if body.note is not None: state["owner_progress_note"]=body.note
        state["last_owner_note_at"]=datetime.now(timezone.utc).isoformat()
        row.current_state_json=state
        text_value=body.note or body.current_step or body.next_step or (
            f"Оценка владельца: {body.progress_percent:.1f}% (не подтверждена runtime)" if body.progress_percent is not None else "Комментарий владельца сохранён"
        )
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,event_type="owner_progress_note",actor="owner",text=text_value,
            payload_json={"owner_reported_progress_percent":body.progress_percent,
                          "current_step":body.current_step,"next_step":body.next_step,
                          "evidence":body.evidence or {},"canonical_progress_unchanged":True},
        ))
        row.last_event_at=datetime.now(timezone.utc)
        db.commit(); db.refresh(row)
        return {"status":"pass","thread":_workspace_thread_dict(row),
                "progress_truth":"owner estimate stored separately; canonical progress unchanged"}
    finally: db.close()


@router.post("/workspace/threads/{thread_id}/autopilot")
def workspace_thread_autopilot(thread_id: str, body: WorkspaceAutopilotBody, user=Depends(get_current_user)):
    """Turn Workspace autonomous continuation on/off without losing current evidence."""
    _platform_admin(user); db=SessionLocal()
    try:
        import json
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        state=dict(row.current_state_json or {})
        enabled=bool(body.enabled)
        state["autopilot_enabled"]=enabled
        state["autopilot_mode"]="until_done"
        state["autopilot_state"]="working" if enabled else "paused"
        state["autopilot_changed_at"]=datetime.now(timezone.utc).isoformat()
        raw_dev=state.get("dev_job_id")
        resume_result=None
        if enabled and not raw_dev:
            # Existing Workspace rows predate executable dev_job links. One click
            # must actually start work, not merely paint an "autopilot" badge.
            seed_command=str(state.get("last_command_text") or row.summary or row.title or "").strip()
            auto_command_id=f"autostart-{row.id}"
            try:
                execution=_workspace_enqueue_executor(
                    row, command_id=auto_command_id, text_value=seed_command,
                )
                state.update(execution)
                state["last_command_id"]=auto_command_id
                state["last_command_text"]=seed_command[:6000]
                state["last_command_at"]=datetime.now(timezone.utc).isoformat()
                raw_dev=execution.get("dev_job_id")
                resume_result={"status":"started","execution":execution}
            except Exception as start_exc:
                state["autopilot_state"]="blocked"
                state["execution_enqueue_error"]=type(start_exc).__name__
                resume_result={"status":"start_failed","error_type":type(start_exc).__name__}
        if raw_dev:
            from app.ext_api import db as ext_db
            dev_id=int(str(raw_dev).replace("dev_",""))
            job=ext_db.one("SELECT id,status,scope_json FROM ext_dev_jobs WHERE id=:i",i=dev_id)
            if job:
                try:
                    scope=json.loads(job.get("scope_json") or "{}") if isinstance(job.get("scope_json"),str) else (job.get("scope_json") or {})
                except Exception:
                    scope={}
                scope=dict(scope or {})
                scope["workspace_autopilot"]=enabled
                scope["workspace_autopilot_mode"]="until_done"
                scope["workspace_thread_id"]=row.id
                ext_db.q(
                    "UPDATE ext_dev_jobs SET scope_json=:s,updated_at=NOW() WHERE id=:i",
                    s=json.dumps(scope,ensure_ascii=False),i=dev_id,
                )
                if enabled and str(job.get("status") or "") not in {"completed","cancelled","failed"}:
                    try:
                        from app.ext_api import lead as ext_lead
                        resume_result=ext_lead.resume_autopilot_job(f"dev_{dev_id}")
                    except Exception as resume_exc:
                        resume_result={"status":"resume_deferred","error_type":type(resume_exc).__name__}
        if not raw_dev:
            if enabled and isinstance(resume_result,dict) and resume_result.get("status")=="start_failed":
                state["current_step"]="Автопилот не запущен: техническая ошибка постановки исполнителю"
                state["next_step"]="BORIS должен восстановить постановку автоматически; задача не считается выполняемой"
            else:
                state["current_step"]="Автопилот включён; для старта нужна первая команда по задаче" if enabled else "Автопилот на паузе"
                state["next_step"]="Дайте первую команду — дальше BORIS будет продолжать сам" if enabled else "Включите автопилот, когда будете готовы продолжить"
        start_failed=bool(enabled and isinstance(resume_result,dict) and resume_result.get("status")=="start_failed")
        row.current_state_json=state
        row.status=("blocked" if start_failed else ("active" if enabled else "waiting"))
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,
            event_type=("autopilot_start_failed" if start_failed else ("autopilot_enabled" if enabled else "autopilot_paused")),
            actor="owner",
            text=(
                "Автопилот не запустился; BORIS зафиксировал техническую ошибку постановки и не считает задачу выполняемой"
                if start_failed else
                ("Автопилот включён: BORIS продолжает задачу сам до подтверждённого результата" if enabled else "Автопилот поставлен на паузу после текущего шага")
            ),
            payload_json={"enabled":enabled,"mode":"until_done","dev_job_id":raw_dev,"resume":resume_result},
        ))
        row.last_event_at=datetime.now(timezone.utc)
        db.commit(); db.refresh(row)
        if raw_dev:
            _workspace_refresh_executor_state(db,row)
            db.commit(); db.refresh(row)
        return {"status":"pass","thread":_workspace_thread_dict(row),"resume":resume_result}
    finally: db.close()


@router.post("/workspace/threads/{thread_id}/prompt")
def workspace_thread_prompt(thread_id: str, body: WorkspacePromptBody, user=Depends(get_current_user)):
    """Capture a command and route business work to a strict artifact contract.

    Client content/marketing commands are NOT development jobs. They execute via
    the durable background queue and can be completed only by command-scoped
    artifacts. Code/system commands retain the autonomous BORIS OPS path.
    """
    _platform_admin(user); db=SessionLocal()
    try:
        import uuid
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        command_id=str(body.command_id or uuid.uuid4())
        source_ref=f"workspace:{thread_id}:{command_id}"
        result=capture_owner_instruction(
            db,text=body.text,user_id=getattr(user,"id",None),source="workspace",
            source_ref=source_ref,account_id=row.account_id,
        )

        previous_owner_events=(db.query(ControlWorkspaceEvent)
            .filter(ControlWorkspaceEvent.thread_id==row.id,ControlWorkspaceEvent.actor=="owner")
            .order_by(ControlWorkspaceEvent.id.desc()).limit(8).all())
        history_texts=[str(x.text or "") for x in reversed(previous_owner_events)]

        execution=None
        enqueue_error=None
        operational=False
        contract=None
        if row.thread_type=="client":
            try:
                from app.services.client_execution_contract import (
                    is_client_operation, build_contract, load_contract,
                    save_contract, enqueue_contract_job,
                )
                operational=is_client_operation(body.text,history_texts)
                if operational:
                    contract=load_contract(db,str(row.account_id or ""),command_id)
                    if not contract:
                        contract=build_contract(
                            thread_id=row.id,account_id=str(row.account_id or ""),
                            command_id=command_id,command_text=body.text,
                            history_texts=history_texts,
                        )
                        save_contract(db,contract)
                        job=enqueue_contract_job(db,contract,owner_user_id=getattr(user,"id",None))
                        contract["job_id"]=int(job.id)
                        save_contract(db,contract)
                    execution={
                        "execution_mode":"client_operation",
                        "execution_id":contract.get("execution_id"),
                        "job_id":contract.get("job_id"),
                        "executor":"client-operation",
                        "executor_status":contract.get("status") or "queued",
                        "account_id":row.account_id,
                        "command_id":command_id,
                    }
            except Exception as contract_exc:
                enqueue_error=type(contract_exc).__name__
                execution=None

        if not operational and execution is None:
            try:
                execution=_workspace_enqueue_executor(row,command_id=command_id,text_value=body.text)
            except Exception as exec_exc:
                enqueue_error=type(exec_exc).__name__

        state=dict(row.current_state_json or {})
        state["last_command_id"]=command_id
        state["last_command_text"]=body.text[:6000]
        state["last_command_at"]=datetime.now(timezone.utc).isoformat()
        state["account_id"]=row.account_id
        state["autopilot_enabled"]=True
        state["autopilot_mode"]="until_done"
        state["autopilot_state"]="working"
        state["execution_enqueue_attempts"]=int(state.get("execution_enqueue_attempts") or 0)+1
        if execution:
            if operational and contract:
                state.update({
                    "execution_mode":"client_operation",
                    "execution_contract":contract,
                    "execution_contract_id":contract.get("execution_id"),
                    "execution_contract_status":contract.get("status"),
                    "executor":"client-operation",
                    "executor_status":contract.get("status") or "queued",
                    "criteria_done":int(contract.get("criteria_done") or 0),
                    "criteria_total":int(contract.get("criteria_total") or 0),
                    "current_step":contract.get("current_step"),
                    "next_step":contract.get("next_step"),
                    "dev_job_id":None,"order_id":None,"order_status":None,
                    "executor_wait_reason":contract.get("wait_reason"),
                    "executor_blocker":contract.get("blocker"),
                })
            else:
                state.update(execution)
                state["execution_mode"]="development"
                state["progress_status"]=_workspace_progress_status(
                    execution.get("executor_status"),autopilot_enabled=True,
                    order_status=execution.get("order_status"),
                ) if row.thread_type!="client" else state.get("progress_status")
                if row.thread_type!="client":
                    state["progress_percent"]=None
                    state["current_step"]=f"BORIS OPS: {execution.get('current_phase') or 'QUEUE'} · {execution.get('executor_status') or 'queued'}"
                    state["next_step"]="Исполнитель взял команду; фактический прогресс появится только из OPS/evidence"
                else:
                    state["current_step"]=f"Последняя команда поставлена исполнителю · {execution.get('executor_status') or 'queued'}"
                    state["next_step"]="BORIS OPS выполняет команду по этому клиенту"
            state["execution_enqueue_error"]=None
            state["execution_enqueue_attempts"]=1
        else:
            state["execution_enqueue_error"]=enqueue_error or "unknown"
            state["executor_status"]="enqueue_failed"
            state["current_step"]="Команда сохранена, но постановка исполнителю не подтверждена"
            state["next_step"]="BORIS должен повторить постановку безопасно; выполненной команда не считается"
        row.current_state_json=state
        row.status="active" if execution or row.thread_type=="client" else "blocked"

        existing_ev=None
        if result.get("deduplicated") and result.get("instruction_id"):
            existing_ev=db.query(ControlWorkspaceEvent).filter(
                ControlWorkspaceEvent.thread_id==row.id,
                ControlWorkspaceEvent.instruction_id==result.get("instruction_id"),
                ControlWorkspaceEvent.event_type==body.event_type[:32],
            ).order_by(ControlWorkspaceEvent.id.asc()).first()
        event_payload={"intake":result,"execution":execution,"enqueue_error":enqueue_error,"command_id":command_id}
        if contract:
            event_payload["execution_contract"]={
                "execution_id":contract.get("execution_id"),"status":contract.get("status"),
                "criteria_total":contract.get("criteria_total"),"requirements":contract.get("requirements"),
            }
        if existing_ev is not None:
            payload=dict(existing_ev.payload_json or {}); payload.update(event_payload)
            existing_ev.payload_json=payload; existing_ev.text=body.text
            row.last_event_at=datetime.now(timezone.utc)
            db.commit(); db.refresh(existing_ev)
            if not execution:
                raise HTTPException(status_code=503,detail=f"executor_enqueue_failed:{enqueue_error or 'unknown'}:{command_id}")
            response=dict(result or {})
            response.update({"status":"queued","thread_id":row.id,"event_id":existing_ev.id,
                             "command_id":command_id,"execution":execution,"replayed":True})
            return response

        ev=ControlWorkspaceEvent(
            thread_id=row.id,event_type=body.event_type[:32],actor="owner",text=body.text,
            payload_json=event_payload,instruction_id=result.get("instruction_id"),
        )
        db.add(ev)
        queued_text=(
            f"Строгий контракт {contract.get('execution_id')} · наряд {contract.get('job_id')}. "
            "Готово будет только после появления обязательных артефактов."
            if operational and contract else
            (f"Команда поставлена исполнителю {execution.get('executor')}: "
             f"{execution.get('dev_job_id')} · наряд {execution.get('order_id')}" if execution else
             "Команда записана, но исполнительный наряд не подтверждён; выполненной она не считается.")
        )
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,event_type="execution_contract_queued" if operational and execution else ("execution_queued" if execution else "execution_enqueue_failed"),
            actor="boris",text=queued_text,
            payload_json=execution or {"command_id":command_id,"error_type":enqueue_error},
            instruction_id=result.get("instruction_id"),
        ))
        row.last_event_at=datetime.now(timezone.utc)
        db.commit(); db.refresh(ev)
        if not execution:
            raise HTTPException(status_code=503,detail=f"executor_enqueue_failed:{enqueue_error or 'unknown'}:{command_id}")
        response=dict(result or {})
        response.update({"status":"queued","thread_id":row.id,"event_id":ev.id,
                         "command_id":command_id,"execution":execution})
        return response
    except HTTPException: raise
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=500,detail=f"workspace_prompt_failed:{type(exc).__name__}")
    finally: db.close()


@router.get("/workspace/threads/{thread_id}/resume-pack")
def workspace_thread_resume_pack(thread_id: str, user=Depends(get_current_user)):
    """Return a portable recovery package for continuing this work in any chat/UI."""
    _platform_admin(user); db=SessionLocal()
    try:
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==thread_id).first()
        if not row: raise HTTPException(status_code=404,detail="thread_not_found")
        events=db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==thread_id).order_by(ControlWorkspaceEvent.id.desc()).limit(80).all()
        events=list(reversed(events))
        lines=[
            "BORIS WORKSPACE RECOVERY PACK",
            f"thread_id: {row.id}",
            f"type: {row.thread_type}",
            f"title: {row.title}",
            f"status: {row.status}",
            f"account_id: {row.account_id or '-'}",
            f"external_chat_ref: {row.external_chat_ref or '-'}",
            "",
            "SUMMARY:", row.summary or "-",
            "",
            "CURRENT_STATE:", __import__('json').dumps(row.current_state_json or {},ensure_ascii=False,indent=2),
            "",
            "PLAN:", __import__('json').dumps(row.plan_json or [],ensure_ascii=False,indent=2),
            "",
            "SOURCE:", __import__('json').dumps(row.source_json or {},ensure_ascii=False,indent=2),
            "",
            "RECENT_EVENTS:",
        ]
        # For BORIS OPS-backed tasks, recover the live ledger as well as the cached workspace state.
        if str(row.external_chat_ref or "").startswith("ops-job:"):
            try:
                from app.ext_api import dev as ext_dev
                ops_id=int(str(row.external_chat_ref).split(":",1)[1])
                live=ext_dev.get(ops_id)
                safe={k:live.get(k) for k in ("dev_job_id","title","goal","status","current_phase","priority","wait_reason","blocked_reason","phases","acceptance_criteria","deployment_status","qa_status","files_changed","tests","errors","decisions")}
                lines += ["", "BORIS_OPS_LIVE_LEDGER:", __import__('json').dumps(safe,ensure_ascii=False,indent=2,default=str)]
            except Exception as exc:
                lines += ["", "BORIS_OPS_LIVE_LEDGER:", f"unavailable:{type(exc).__name__}"]
        for e in events:
            stamp=e.created_at.isoformat() if e.created_at else ""
            lines.append(f"[{stamp}] {e.actor}/{e.event_type}: {e.text}")
        lines += ["", "CONTINUE_RULE:", "Продолжай эту рабочую ветку с указанного состояния. Не начинай заново. Проверяй фактическое состояние BORIS перед изменениями и записывай существенный прогресс обратно в workspace journal."]
        return {"status":"pass","thread":_workspace_thread_dict(row,len(events)),"resume_text":"\n".join(lines),"events_included":len(events)}
    finally: db.close()


class WorkspaceRestoreChatBody(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    external_chat_ref: str = Field(min_length=2, max_length=255)
    summary: str = ""
    account_id: Optional[str] = None
    thread_type: str = "chat"
    last_messages: list[Any] = []


@router.post("/workspace/restore-chat")
def workspace_restore_chat(body: WorkspaceRestoreChatBody, user=Depends(get_current_user)):
    """Idempotently restore/import an external chat into the durable workspace registry."""
    _platform_admin(user); db=SessionLocal()
    try:
        import uuid
        ref=body.external_chat_ref.strip()
        # WORKSPACE_RESTORE_CANONICAL_SCOPE_V1: a neighboring/recovered chat is
        # an interface to the canonical task, not a new task registry entry.
        canonical_scope=_workspace_canonical_task_scope(body.title,body.summary)
        if canonical_scope:
            from sqlalchemy import text as _sql_text
            db.execute(_sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"),{"k":f"workspace-task-scope:{canonical_scope}"})
            canonical=_workspace_find_canonical_scope(db,canonical_scope)
            if canonical:
                payload={"canonical_scope":canonical_scope,"external_chat_ref":ref,"requested_title":body.title,"messages":body.last_messages[-30:]}
                db.add(ControlWorkspaceEvent(thread_id=canonical.id,event_type="chat_scope_linked",actor="owner",text=f"Соседний чат «{body.title[:180]}» привязан к канонической задаче; отдельная task-карточка не создана.",payload_json=payload))
                canonical.last_event_at=datetime.now(timezone.utc); db.commit()
                return {"status":"linked_to_canonical","created":False,"thread":_workspace_thread_dict(canonical),"canonical_scope":canonical_scope}
        row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.external_chat_ref==ref).first()
        created=False
        if row is None:
            _restored_type=body.thread_type if body.thread_type in {"task","client","chat","project"} else "chat"
            _restored_state={"memory_mode":"durable_client_workspace" if _restored_type=="client" else "durable_workspace","restored":True,"current_step":"Контекст чата восстановлен","next_step":"Продолжить с последней зафиксированной точки"}
            _restored_source={"surface":"restore_chat","external_chat_ref":ref}
            if _restored_type=="client":
                # CLIENT_WORKSPACE_MONEY_OBSERVER_ONLY_V1: restore has exactly
                # the same ownership rule as ordinary client creation.
                _restored_state.update({"money_execution_owner":"65caf765-57d4-4edc-9fce-1f24f75882cd","money_execution_mode":"observe_only","no_separate_money_engine":True})
                _restored_source.update({"canonical_money_owner_thread_id":"65caf765-57d4-4edc-9fce-1f24f75882cd","money_execution_mode":"observe_only","no_separate_money_engine":True})
            row=ControlWorkspaceThread(
                id=str(uuid.uuid4()), thread_type=_restored_type,
                title=body.title.strip(), account_id=body.account_id, external_chat_ref=ref, status="active",
                summary=body.summary.strip(), current_state_json=_restored_state,
                plan_json=[], source_json=_restored_source, created_by=getattr(user,"id",None),
            )
            db.add(row); db.flush(); created=True
        else:
            row.status="active"
            if body.summary.strip(): row.summary=body.summary.strip()
            if body.account_id and not row.account_id: row.account_id=body.account_id
        payload={"external_chat_ref":ref,"messages":body.last_messages[-30:]}
        db.add(ControlWorkspaceEvent(thread_id=row.id,event_type="chat_restored",actor="owner",text=body.summary.strip() or f"Восстановлен чат: {body.title.strip()}",payload_json=payload))
        row.last_event_at=datetime.now(timezone.utc)
        db.commit(); db.refresh(row)
        return {"status":"created" if created else "restored","thread":_workspace_thread_dict(row,db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==row.id).count())}
    finally: db.close()


@router.get("/workspace/activity")
def workspace_activity(account_id: Optional[str]=None, projection: Optional[str]=None, sync_runtime: bool=Query(True), limit: int=Query(80,ge=1,le=300), user=Depends(get_current_user)):
    """Unified factual activity feed without per-row N+1 database reads."""
    _platform_admin(user); db=SessionLocal()
    try:
        if sync_runtime:
            _workspace_sync_runtime_refs(db,user=user,limit=max(500,limit),account_id=account_id)
            db.commit()
        if projection and projection not in {"development","client","brain"}:
            raise HTTPException(status_code=422,detail="invalid_workspace_projection")
        scan_limit=2000 if projection else limit
        rows=[]
        eq=db.query(ControlWorkspaceEvent,ControlWorkspaceThread).join(ControlWorkspaceThread,ControlWorkspaceThread.id==ControlWorkspaceEvent.thread_id)
        if account_id: eq=eq.filter(ControlWorkspaceThread.account_id==account_id)
        for e,t in eq.order_by(ControlWorkspaceEvent.created_at.desc()).limit(scan_limit).all():
            if projection and not _workspace_projection_match(t,projection):
                continue
            rows.append({"source":"workspace","id":str(e.id),"thread_id":t.id,"thread_title":t.title,"account_id":t.account_id,"kind":e.event_type,"status":t.status,"title":e.text or t.title,"created_at":e.created_at.isoformat() if e.created_at else None,"payload":e.payload_json or {}})

        tq=db.query(Task)
        if account_id: tq=tq.filter(Task.account_id==account_id)
        task_rows=tq.order_by(Task.updated_at.desc(),Task.created_at.desc()).limit(scan_limit).all()
        task_refs=[f"task:{t.id}" for t in task_rows]
        task_links={r.external_chat_ref:r for r in db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.external_chat_ref.in_(task_refs)).all()} if task_refs else {}
        for t in task_rows:
            try: payload=__import__('json').loads(t.payload) if t.payload else {}
            except Exception: payload={}
            if not isinstance(payload,dict): payload={}
            title=str(payload.get("title") or payload.get("name") or payload.get("direction") or payload.get("product_name") or t.task_type)
            linked=task_links.get(f"task:{t.id}")
            if projection and (not linked or not _workspace_projection_match(linked,projection)):
                continue
            rows.append({"source":"task","id":str(t.id),"thread_id":linked.id if linked else None,"thread_title":linked.title if linked else None,"account_id":t.account_id,"kind":t.task_type,"status":t.status,"title":title,"created_at":(t.updated_at or t.created_at).isoformat() if (t.updated_at or t.created_at) else None,"payload":{"depends_on_task_id":t.depends_on_task_id,"error_message":t.error_message}})

        xq=db.query(ControlExecution)
        if account_id: xq=xq.filter(ControlExecution.account_id==account_id)
        exec_rows=xq.order_by(ControlExecution.updated_at.desc(),ControlExecution.created_at.desc()).limit(scan_limit).all()
        exec_ids=[x.id for x in exec_rows]
        req_ids={x.requirement_id for x in exec_rows if x.requirement_id}
        req_titles={r.id:r.title for r in db.query(ControlRequirement).filter(ControlRequirement.id.in_(req_ids)).all()} if req_ids else {}
        step_stats={}
        if exec_ids:
            for eid,total,done in db.query(
                ControlExecutionStep.execution_id,
                func.count(ControlExecutionStep.id),
                func.count(ControlExecutionStep.id).filter(ControlExecutionStep.status.in_(("done","pass","completed","verified")))
            ).filter(ControlExecutionStep.execution_id.in_(exec_ids)).group_by(ControlExecutionStep.execution_id).all():
                step_stats[str(eid)]=(int(total or 0),int(done or 0))
        def _activity_exec_ref(x):
            return f"execution-scope:{x.requirement_id or x.module or 'unknown'}:{x.account_id or '__global__'}"
        exec_refs=[_activity_exec_ref(x) for x in exec_rows]+[f"execution:{x.id}" for x in exec_rows]
        exec_links={r.external_chat_ref:r for r in db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.external_chat_ref.in_(exec_refs)).all()} if exec_refs else {}
        for x in exec_rows:
            total,done=step_stats.get(str(x.id),(0,0))
            linked=exec_links.get(_activity_exec_ref(x)) or exec_links.get(f"execution:{x.id}")
            if projection and (not linked or not _workspace_projection_match(linked,projection)):
                continue
            rows.append({"source":"execution","id":x.id,"thread_id":linked.id if linked else None,"thread_title":linked.title if linked else None,"account_id":x.account_id,"kind":x.module,"status":x.status,"title":req_titles.get(x.requirement_id) or f"{x.module}: выполнение требования","created_at":(x.updated_at or x.created_at).isoformat() if (x.updated_at or x.created_at) else None,"payload":{"requirement_id":x.requirement_id,"correlation_id":x.correlation_id,"steps_done":done,"steps_total":total,"attempt":x.attempt,"error_code":x.error_code,"block_reason":x.block_reason}})

        def key(v):
            try: return datetime.fromisoformat(str(v.get("created_at") or "").replace("Z","+00:00")).timestamp()
            except Exception: return 0
        rows.sort(key=key,reverse=True)
        return {"status":"pass","items":rows[:limit],"counts":{"workspace":sum(1 for x in rows if x["source"]=="workspace"),"tasks":sum(1 for x in rows if x["source"]=="task"),"executions":sum(1 for x in rows if x["source"]=="execution")}}
    finally: db.close()


@router.post("/workspace/materialize")
def workspace_materialize(limit: int=Query(500,ge=1,le=2000), user=Depends(get_current_user)):
    """Idempotently materialize and synchronize runtime tasks/executions."""
    _platform_admin(user); db=SessionLocal()
    try:
        sync=_workspace_sync_runtime_refs(db,user=user,limit=limit)
        db.commit()
        return {"status":"pass","sync":sync,"workspace_total":db.query(ControlWorkspaceThread).count()}
    finally: db.close()


@router.get("/workspace/executions/{execution_id}/drilldown")
def workspace_execution_drilldown(execution_id: str, user=Depends(get_current_user)):
    """Evidence-first execution drill-down for the owner journal."""
    _platform_admin(user); db=SessionLocal()
    try:
        x=db.query(ControlExecution).filter(ControlExecution.id==execution_id).first()
        if not x: raise HTTPException(status_code=404,detail="execution_not_found")
        steps=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==x.id).order_by(ControlExecutionStep.step_no).all()
        attempts=db.query(ControlExecutionAttempt).filter(ControlExecutionAttempt.execution_id==x.id).order_by(ControlExecutionAttempt.created_at.asc()).all()
        evidence=db.query(ControlEvidence).filter(ControlEvidence.execution_id==x.id).order_by(ControlEvidence.observed_at.asc()).all()
        return {"status":"pass","execution":{"id":x.id,"status":x.status,"module":x.module,"account_id":x.account_id,"requirement_id":x.requirement_id,"block_reason":x.block_reason,"error_code":x.error_code,"error_message":x.error_message,"created_at":x.created_at.isoformat() if x.created_at else None,"updated_at":x.updated_at.isoformat() if x.updated_at else None},"steps":[{"id":s.id,"step_no":s.step_no,"name":s.name,"status":s.status,"error_code":s.error_code,"error_message":s.error_message,"output":s.output_json or {},"started_at":s.started_at.isoformat() if s.started_at else None,"finished_at":s.finished_at.isoformat() if s.finished_at else None} for s in steps],"attempts":[{"id":a.id,"step_id":a.step_id,"attempt_no":a.attempt_no,"strategy":a.strategy,"status":a.status,"error_code":a.error_code,"error_message":a.error_message,"result":a.result_json or {},"created_at":a.created_at.isoformat() if a.created_at else None} for a in attempts],"evidence":[{"id":e.id,"level":e.level,"type":e.evidence_type,"source":e.source,"passed":e.passed,"payload":e.payload_json or {},"created_at":e.observed_at.isoformat() if e.observed_at else None} for e in evidence]}
    finally: db.close()


@router.get("/workspace/executions/{execution_id}/detail")
def workspace_execution_detail(execution_id: str, user=Depends(get_current_user)):
    """Owner drill-down for one factual Control Plane execution."""
    _platform_admin(user); db=SessionLocal()
    try:
        x=db.query(ControlExecution).filter(ControlExecution.id==execution_id).first()
        if not x: raise HTTPException(status_code=404,detail="execution_not_found")
        req=db.query(ControlRequirement).filter(ControlRequirement.id==x.requirement_id).first()
        steps=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==x.id).order_by(ControlExecutionStep.step_no.asc()).all()
        attempts=db.query(ControlExecutionAttempt).filter(ControlExecutionAttempt.execution_id==x.id).order_by(ControlExecutionAttempt.created_at.asc()).all()
        evidence=db.query(ControlEvidence).filter(ControlEvidence.execution_id==x.id).order_by(ControlEvidence.observed_at.asc()).all()
        return {"status":"pass","execution":{"id":x.id,"status":x.status,"module":x.module,"account_id":x.account_id,"attempt":x.attempt,"max_attempts":x.max_attempts,"requirement_id":x.requirement_id,"requirement_title":req.title if req else None,"plan":x.plan_json or {},"actual_state":x.actual_state_json or {},"block_reason":x.block_reason,"error_code":x.error_code,"error_message":x.error_message,"created_at":x.created_at.isoformat() if x.created_at else None,"updated_at":x.updated_at.isoformat() if x.updated_at else None,"finished_at":x.finished_at.isoformat() if x.finished_at else None},"steps":[{"id":s.id,"step_no":s.step_no,"name":s.name,"status":s.status,"input":s.input_json or {},"output":s.output_json or {},"error_code":s.error_code,"error_message":s.error_message,"started_at":s.started_at.isoformat() if s.started_at else None,"finished_at":s.finished_at.isoformat() if s.finished_at else None} for s in steps],"attempts":[{"id":a.id,"step_id":a.step_id,"attempt_no":a.attempt_no,"strategy":a.strategy,"status":a.status,"error_code":a.error_code,"error_message":a.error_message,"result":a.result_json or {},"created_at":a.created_at.isoformat() if a.created_at else None,"finished_at":a.finished_at.isoformat() if a.finished_at else None} for a in attempts],"evidence":[{"id":e.id,"level":e.level,"type":e.evidence_type,"source":e.source,"passed":e.passed,"payload":e.payload_json or {},"observed_at":e.observed_at.isoformat() if e.observed_at else None} for e in evidence]}
    finally: db.close()




@router.get("/workspace/programming-journal")
def workspace_programming_journal(limit: int=Query(200,ge=1,le=500), user=Depends(get_current_user)):
    """Owner truth view for autonomous development: queue, progress and blockers.

    This endpoint deliberately reads the server-side development ledger directly;
    chat history is never treated as execution evidence.
    """
    _platform_admin(user)
    try:
        from app.ext_api import db as ext_db
        jobs=ext_db.rows("""SELECT id,title,status,priority,assigned_agent,wait_reason,
                                    created_at,updated_at
                               FROM ext_dev_jobs
                              WHERE status NOT IN ('cancelled')
                              ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'review' THEN 1
                                                   WHEN 'waiting' THEN 2 WHEN 'queued' THEN 3
                                                   WHEN 'completed' THEN 9 ELSE 5 END,
                                       priority DESC NULLS LAST, updated_at DESC
                              LIMIT :lim""", lim=limit)
        counts={str(r['status']):int(r['n']) for r in ext_db.rows(
            "SELECT status,COUNT(*) n FROM ext_dev_jobs GROUP BY status")}
        orders={str(r['status']):int(r['n']) for r in ext_db.rows(
            "SELECT status,COUNT(*) n FROM ext_a2a_orders GROUP BY status")}
        open_orders=ext_db.rows("""SELECT id,dev_job_id,status,assigned_to,claimed_by,
                                           blocked_reason,updated_at
                                      FROM ext_a2a_orders
                                     WHERE status IN ('queued','running','review_requested','blocked_infra')
                                     ORDER BY updated_at DESC LIMIT :lim""", lim=min(limit,200))
        def iso(v): return v.isoformat() if hasattr(v,'isoformat') else (str(v) if v else None)
        items=[]
        for j in jobs:
            st=str(j.get('status') or '')
            reason=str(j.get('wait_reason') or '')
            if st=='completed': pct=100; truth='verified_done'
            elif st in ('running','review'): pct=None; truth='in_progress'
            elif st in ('waiting','queued'): pct=0; truth='not_executing'
            else: pct=None; truth='unknown'
            items.append({'id':j.get('id'),'title':j.get('title'),'status':st,
                          'priority':j.get('priority'),'assigned_agent':j.get('assigned_agent'),
                          'wait_reason':reason or None,'progress_percent':pct,
                          'progress_truth':truth,'created_at':iso(j.get('created_at')),
                          'updated_at':iso(j.get('updated_at'))})
        blocked_provider=int((ext_db.one("SELECT COUNT(*) n FROM ext_dev_jobs WHERE status='waiting' AND COALESCE(wait_reason,'') LIKE 'WAITING_FREE_CODING_PROVIDER%'") or {}).get("n") or 0)
        return {'status':'pass','source_of_truth':'ext_dev_jobs/ext_a2a_orders',
                'counts':counts,'order_counts':orders,
                'summary':{'visible_jobs':len(items),'waiting_free_provider':blocked_provider,
                           'executing_now':counts.get('running',0),
                           'completed':counts.get('completed',0)},
                'jobs':items,'open_orders':[dict(x) for x in open_orders],
                'rule':'0% means queued/waiting, 100% only completed; no invented progress'}
    except Exception as exc:
        raise HTTPException(status_code=500,detail=f"programming_journal_failed:{type(exc).__name__}")

@router.get("/workspace/ops-bridge")
def workspace_ops_bridge(limit: int=Query(100,ge=1,le=300), user=Depends(get_current_user)):
    """Evidence-first BORIS OPS bridge for Journal and Client Control Center."""
    _platform_admin(user)
    try:
        import json
        from app.ext_api import db as ext_db
        jobs=ext_db.rows("""SELECT id,title,goal,status,current_phase,priority,assigned_agent,wait_reason,
                                    blocked_reason,phases_json,acceptance_json,qa_status,deployment_status,
                                    created_at,updated_at
                               FROM ext_dev_jobs WHERE status<>'cancelled'
                              ORDER BY updated_at DESC LIMIT :lim""",lim=limit)
        counts={str(x['status']):int(x['n']) for x in ext_db.rows("SELECT status,COUNT(*) n FROM ext_dev_jobs GROUP BY status")}
        orders={str(x['status']):int(x['n']) for x in ext_db.rows("SELECT status,COUNT(*) n FROM ext_a2a_orders GROUP BY status")}
        items=[]
        for j in jobs:
            try: phases=json.loads(j.get('phases_json') or '{}')
            except Exception: phases={}
            try: criteria=json.loads(j.get('acceptance_json') or '[]')
            except Exception: criteria=[]
            phase_pass=sum(1 for v in phases.values() if isinstance(v,dict) and v.get('state')=='pass')
            criteria_done=sum(1 for v in criteria if isinstance(v,dict) and v.get('done'))
            items.append({'id':j['id'],'title':j['title'],'status':j['status'],'current_phase':j.get('current_phase'),
                          'priority':j.get('priority'),'assigned_agent':j.get('assigned_agent'),'wait_reason':j.get('wait_reason'),
                          'blocked_reason':j.get('blocked_reason'),'phase_passed':phase_pass,'phase_total':len(phases),
                          'criteria_done':criteria_done,'criteria_total':len(criteria),'qa_status':j.get('qa_status'),
                          'deployment_status':j.get('deployment_status'),'updated_at':j.get('updated_at').isoformat() if j.get('updated_at') else None})
        return {'status':'pass','source_of_truth':'ext_dev_jobs/ext_a2a_orders','jobs':items,'counts':counts,'a2a_counts':orders,
                'ops':{'runtime':['shell','facts','logs','service','http','cron','build','tests','db_query','readiness','browser'],
                       'development':['jobs','queue','phases','criteria','complete','report','board','repo','patches'],
                       'a2a':['orders','claim','review','board','executor','lead']},
                'health':{'waiting_free_provider':int((ext_db.one("SELECT COUNT(*) n FROM ext_dev_jobs WHERE status='waiting' AND COALESCE(wait_reason,'') LIKE 'WAITING_FREE_CODING_PROVIDER%'") or {}).get("n") or 0),
                          'running':counts.get('running',0),'completed':counts.get('completed',0),
                          'open_a2a':sum(orders.get(k,0) for k in ('queued','running','review_requested','blocked_infra'))},
                'rule':'chat is not source of truth; OPS ledger/evidence and durable workspace are'}
    except Exception as exc:
        raise HTTPException(status_code=500,detail=f"ops_bridge_failed:{type(exc).__name__}")


@router.get("/workspace/client-supervisor")
def workspace_client_supervisor(account_id: str | None = Query(None), user=Depends(get_current_user)):
    """CLIENT_SUPERVISOR_BRAIN_COMPOSITE_V2: own-account view for clients; fleet only for platform admin."""
    from app.services.client_supervisor import build_client_state, build_fleet_state
    db=SessionLocal()
    try:
        if account_id:
            _account_scoped_supervisor_access(db,user,str(account_id))
            return build_client_state(db,str(account_id))
        _platform_admin(user)
        return build_fleet_state(db)
    finally:
        db.close()
