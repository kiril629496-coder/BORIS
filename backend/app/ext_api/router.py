# -*- coding: utf-8 -*-
"""BORIS AI Agent Gateway — HTTP-контур.

Роутер не содержит бизнес-логики. Он устанавливает личность агента, ведёт
request_id и идемпотентность, отдаёт работу оркестратору и приводит любой
исход — включая падение — к единому контракту ответа. Аутентификация
выполняется ВНУТРИ обёртки, чтобы 401/403/429 тоже приходили в контракте,
а не голым FastAPI-ответом.
"""
import time, uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import db, auth, registry, orchestrator, health, agents, dev, costs, adapters, repo, ops, a2a, schema, devflow
from .errors import ApiError, log_internal

router = APIRouter(prefix="/api/v1", tags=["agent-gateway"])
GATEWAY_VERSION = "1.0.0"


def qval(v, default):
    """FastAPI подставляет Query-объект, когда функцию зовут напрямую (тесты,
    внутренние вызовы). В SQL такой объект не уходит — берём значение."""
    return v if isinstance(v, (int, str)) else default


def jsonable(x):
    """Приводит ответ к тому, что реально умеет сериализовать starlette.

    Из базы приходят datetime и Decimal —json.dumps на них падает 500-й,
    и падает уже ПОСЛЕ того, как работа выполнена. Чистим на выходе.
    """
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, Decimal):
        return float(x)
    return str(x)


def new_request_id(incoming=None):
    if incoming and 8 <= len(str(incoming)) <= 40:
        return str(incoming)
    return "req_" + uuid.uuid4().hex[:16]


def run_endpoint(method, path, authorization, x_request_id, fn, need_auth=True):
    rid = new_request_id(x_request_id)
    started = time.time()
    key = None
    try:
        if need_auth:
            key = auth.resolve(authorization)
        body, status = fn(key, rid)
        if isinstance(body, dict):
            body.setdefault("request_id", rid)
        auth.audit(key, method, path, status, request_id=rid, started=started,
                   response=(body if status >= 400 else None))
        return JSONResponse(jsonable(body), status_code=status,
                            headers={"X-Request-Id": rid})
    except ApiError as e:
        b = e.body(request_id=rid, status="blocked" if e.http in (403, 424, 501) else "failed")
        auth.audit(key, method, path, e.http, request_id=rid, started=started,
                   error=e.code, response=b)
        return JSONResponse(jsonable(b), status_code=e.http,
                            headers={"X-Request-Id": rid})
    except Exception as e:
        log_internal(rid, f"{method} {path}", e)
        b = ApiError("INTERNAL").body(request_id=rid)
        auth.audit(key, method, path, 500, request_id=rid, started=started,
                   error="INTERNAL", response=b)
        return JSONResponse(jsonable(b), status_code=500,
                            headers={"X-Request-Id": rid})


def check_actor(key, actor):
    """Агент не может представиться чужим именем: тело сверяется с ключом."""
    name = (actor or {}).get("name")
    if name and str(name).lower() != str(auth.actor_of(key)["name"]).lower():
        raise ApiError("FORBIDDEN_SCOPE",
                       f"ключ выпущен для агента «{auth.actor_of(key)['name']}», "
                       f"в теле запроса указан «{name}»")


# -------------------------------------------------------------------- схемы

class Actor(BaseModel):
    type: Optional[str] = "ai_agent"
    name: Optional[str] = None


class CommandIn(BaseModel):
    operation: str
    # агент может прислать и «antonybatono_78732», и числовой id — принимаем оба
    account_id: Optional[Union[str, int]] = None
    params: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    confirm: Optional[bool] = False
    dry_run: Optional[bool] = False
    actor: Optional[Actor] = None


class HeartbeatIn(BaseModel):
    status: str = Field(..., description="available | limited | unavailable")
    note: Optional[str] = None
    priority: Optional[int] = None


class HandoffIn(BaseModel):
    to: str = Field(..., description="chatgpt, claude или auto")
    kind: Optional[str] = Field(None, description="для to=auto: code, architecture, qa, planning…")
    mode: Optional[str] = Field("auto", description="auto | claude_first | chatgpt_first")
    job_id: Optional[Union[str, int]] = None
    reason: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    actor: Optional[Actor] = None


class DevJobIn(BaseModel):
    title: str
    goal: str
    acceptance_criteria: List[str]
    scope: Optional[Dict[str, Any]] = None
    priority: Optional[int] = 50
    depends_on: Optional[List[str]] = None
    locks: Optional[List[str]] = Field(None, description="занимаемые ресурсы: файлы, модули, аккаунты")
    reviewer_agent: Optional[str] = None


class DevBatchIn(BaseModel):
    jobs: List[Dict[str, Any]]
    batch_id: Optional[str] = None


class DevPhaseIn(BaseModel):
    phase: str
    state: str = Field(..., description="pending | running | pass | fail | skipped | blocked")
    evidence: Optional[str] = Field(None, description="обязателен для state=pass")
    files_changed: Optional[List[str]] = None
    tests: Optional[List[Dict[str, Any]]] = None
    errors: Optional[List[Dict[str, Any]]] = None
    decisions: Optional[List[Dict[str, Any]]] = None
    notes: Optional[str] = None


class DevCriteriaIn(BaseModel):
    index: int
    done: bool = True
    evidence: Optional[str] = None


class DevCompleteIn(BaseModel):
    summary: Optional[str] = None


class ApprovalIn(BaseModel):
    limit_rub: float = Field(..., description="потолок реальных расходов по этому разрешению")
    operations: Optional[List[str]] = None
    account_id: Optional[int] = None
    expires_hours: Optional[int] = None
    note: Optional[str] = None


class AdapterIn(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = Field(None, description="actions | subscription_sdk | api | mock")
    note: Optional[str] = None


class OpIn(BaseModel):
    name: str = Field(..., description="TEXT | IMAGES | BANNERS | FEED | PUBLISH | QA")
    status: str = Field(..., description="waiting | running | completed | blocked | failed | skipped")
    requested: Optional[int] = None
    actual: Optional[int] = None
    evidence: Optional[str] = None
    error_code: Optional[str] = None
    note: Optional[str] = None


class HandoffResolveIn(BaseModel):
    resolution: str = Field(..., description="resolved | retry_ready | blocked | needs_user_decision")
    note: Optional[str] = None


# -------------------------------------------------------------------- ручки

@router.get("/health/production")
def health_production(authorization: Optional[str] = Header(None),
                      x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        h = health.production()
        h["gateway_version"] = GATEWAY_VERSION
        return h, 200
    return run_endpoint("GET", "/health/production", authorization, x_request_id, go)


@router.get("/capabilities")
def capabilities(authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        c = health.capabilities()
        c["actor"] = auth.actor_of(key)
        c["scopes"] = sorted(key["scope_set"])
        c["accounts"] = ("все" if key["allowed_accounts"] is None
                         else sorted(key["allowed_accounts"]))
        return c, 200
    return run_endpoint("GET", "/capabilities", authorization, x_request_id, go)


@router.post("/commands")
def commands(body: CommandIn, authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None),
             idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    def go(key, rid):
        check_actor(key, body.actor.dict() if body.actor else None)
        idem = body.idempotency_key or idempotency_key
        if not idem:
            raise ApiError("VALIDATION_ERROR",
                           "idempotency_key обязателен — он защищает от повторного "
                           "выполнения production-операции")
        account_ref = (str(body.account_id) if body.account_id is not None else None)
        raw = {"operation": body.operation, "account_id": account_ref,
               "params": body.params or {}, "confirm": bool(body.confirm),
               "dry_run": bool(body.dry_run)}
        replay = auth.idem_lookup(key, idem, raw)
        if replay:
            replay["request_id"] = rid
            auth.audit(key, "POST", "/commands", 200, request_id=rid, command=body.operation,
                       idempotency_key=idem, status="replayed")
            return replay, 200

        job, err = orchestrator.create_job(
            key, rid, body.operation, account_ref, body.params or {},
            idempotency_key=idem, confirm=bool(body.confirm), dry_run=bool(body.dry_run))

        spec = registry.get(body.operation)
        if not err and spec and spec["mode"] == "sync":
            job = orchestrator.run_job(job["id"])

        pub = orchestrator.job_public(job)
        out = {"job_id": pub["job_id"], "status": pub["status"], "operation": pub["operation"],
               "account_id": pub["account_id"], "request_id": rid, "actor": pub["actor"],
               "next_action": pub["next_action"], "qa": pub["qa"], "result": pub["result"],
               "error": pub["error"], "preflight": pub["preflight"]}
        code = err.http if err else 200
        auth.idem_store(key, idem, raw, out, code, job_id=job["id"])
        auth.audit(key, "POST", "/commands", code, request_id=rid, command=body.operation,
                   account_id=pub["account_id"], job_id=job["id"], status=pub["status"],
                   dry_run=bool(body.dry_run), idempotency_key=idem,
                   request=raw, error=(err.code if err else None))
        return out, code
    return run_endpoint("POST", "/commands", authorization, x_request_id, go)


@router.get("/jobs")
def jobs_list(account_id: Optional[str] = None, status: Optional[str] = None,
              operation: Optional[str] = None, limit: int = Query(50, le=200),
              authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        sql, p = "SELECT * FROM ext_jobs WHERE 1=1", {}
        if account_id:
            acc = db.find_account(account_id)
            if not acc:
                raise ApiError("ACCOUNT_NOT_FOUND", f"аккаунт «{account_id}» не найден")
            auth.need_account(key, acc["id"])
            sql += " AND account_id=:a"; p["a"] = acc["id"]
        if status:
            sql += " AND status=:s"; p["s"] = status
        if operation:
            sql += " AND operation=:o"; p["o"] = operation
        sql += " ORDER BY id DESC LIMIT :l"; p["l"] = int(qval(limit, 50))
        allowed = key["allowed_accounts"]
        out = [orchestrator.job_public(j) for j in db.rows(sql, **p)
               if allowed is None or j["account_id"] in allowed]
        return {"jobs": out, "count": len(out)}, 200
    return run_endpoint("GET", "/jobs", authorization, x_request_id, go)


@router.get("/jobs/{job_id}")
def job_get(job_id: str, authorization: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        j = orchestrator.get_job(job_id)
        if not j:
            raise ApiError("JOB_NOT_FOUND")
        auth.need_account(key, j["account_id"])
        pub = orchestrator.job_public(j)
        pub["attempts_log"] = orchestrator.attempts_of(j["id"])
        if j.get("handoff_id"):
            try:
                pub["handoff"] = orchestrator.get_handoff(j["handoff_id"])
            except ApiError:
                pub["handoff"] = None
        return pub, 200
    return run_endpoint("GET", f"/jobs/{job_id}", authorization, x_request_id, go)


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: str, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = orchestrator.retry_job(key, job_id, rid)
        spec = registry.get(j["operation"])
        if spec and spec["cls"] in (registry.READ_ONLY, registry.SAFE_MUTATION):
            j = orchestrator.run_job(j["id"])
        pub = orchestrator.job_public(j)
        auth.audit(key, "POST", f"/jobs/{job_id}/retry", 200, request_id=rid, job_id=j["id"],
                   account_id=j["account_id"], status=pub["status"], command=j["operation"])
        return pub, 200
    return run_endpoint("POST", f"/jobs/{job_id}/retry", authorization, x_request_id, go)


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str, authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = orchestrator.cancel_job(key, job_id)
        pub = orchestrator.job_public(j)
        auth.audit(key, "POST", f"/jobs/{job_id}/cancel", 200, request_id=rid, job_id=j["id"],
                   account_id=j["account_id"], status=pub["status"], command=j["operation"])
        return pub, 200
    return run_endpoint("POST", f"/jobs/{job_id}/cancel", authorization, x_request_id, go)


@router.get("/clients/{account_id}/status")
def client_status(account_id: str, authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        acc = db.find_account(account_id)
        if not acc:
            raise ApiError("ACCOUNT_NOT_FOUND", f"аккаунт «{account_id}» не найден")
        auth.need_account(key, acc["id"])
        return orchestrator.client_status_of(acc), 200
    return run_endpoint("GET", f"/clients/{account_id}/status", authorization, x_request_id, go)


@router.post("/handoffs")
def handoff_create(body: HandoffIn, authorization: Optional[str] = Header(None),
                   x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        check_actor(key, body.actor.dict() if body.actor else None)
        target, why = body.to, None
        if body.to.lower() == "auto":
            chosen, why = agents.pick(body.kind, body.mode or "auto",
                                      exclude=[auth.actor_of(key)["name"]])
            if not chosen:
                raise ApiError("DEPENDENCY_UNAVAILABLE",
                               "нет доступного агента: оба не отметились как available")
            target = chosen["name"]
        h = orchestrator.create_handoff(key, rid, body.job_id, target, body.reason, body.context)
        if why:
            h["routed_by_controller"] = why
        auth.audit(key, "POST", "/handoffs", 201, request_id=rid, command="handoff",
                   job_id=(int(str(h["job_id"])[4:]) if h.get("job_id") else None),
                   response={"handoff_id": h["handoff_id"], "to": h["to"]})
        return h, 201
    return run_endpoint("POST", "/handoffs", authorization, x_request_id, go)


@router.get("/handoffs")
def handoff_list(to: Optional[str] = None, status: Optional[str] = None,
                 limit: int = Query(50, le=200),
                 authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        sql, p = "SELECT id FROM ext_handoffs WHERE 1=1", {}
        if to:
            sql += " AND to_actor=:t"; p["t"] = to
        if status:
            sql += " AND status=:s"; p["s"] = status
        sql += " ORDER BY id DESC LIMIT :l"; p["l"] = int(qval(limit, 50))
        return {"handoffs": [orchestrator.get_handoff(r["id"]) for r in db.rows(sql, **p)]}, 200
    return run_endpoint("GET", "/handoffs", authorization, x_request_id, go)


@router.get("/handoffs/{handoff_id}")
def handoff_get(handoff_id: int, authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return orchestrator.get_handoff(handoff_id), 200
    return run_endpoint("GET", f"/handoffs/{handoff_id}", authorization, x_request_id, go)


@router.post("/handoffs/{handoff_id}/resolve")
def handoff_resolve(handoff_id: int, body: HandoffResolveIn,
                    authorization: Optional[str] = Header(None),
                    x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        h = orchestrator.resolve_handoff(key, handoff_id, body.resolution, body.note)
        auth.audit(key, "POST", f"/handoffs/{handoff_id}/resolve", 200, request_id=rid,
                   command="handoff_resolve", response={"resolution": body.resolution})
        return h, 200
    return run_endpoint("POST", f"/handoffs/{handoff_id}/resolve",
                        authorization, x_request_id, go)


@router.get("/agents")
def agents_list(authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        reg = agents.registry()
        return {"agents": reg, "modes": list(agents.MODES),
                "specialization": agents.SPECIALIZATION,
                "note": "billing_mode=subscription — контрольное поле: агент работает "
                        "через подписочный контур пользователя, а не через отдельный API-биллинг"}, 200
    return run_endpoint("GET", "/agents", authorization, x_request_id, go)


@router.get("/agents/next")
def agents_next(kind: Optional[str] = None, mode: str = "auto",
                authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        chosen, why = agents.pick(kind, mode)
        return {"agent": chosen, "reason": why, "kind": kind, "mode": mode}, 200
    return run_endpoint("GET", "/agents/next", authorization, x_request_id, go)


@router.post("/agents/{agent_name}/heartbeat")
def agent_heartbeat(agent_name: str, body: HeartbeatIn,
                    authorization: Optional[str] = Header(None),
                    x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        a = agents.heartbeat(key, agent_name, body.status, body.note, body.priority)
        auth.audit(key, "POST", f"/agents/{agent_name}/heartbeat", 200, request_id=rid,
                   command="agent_heartbeat", status=body.status)
        return a, 200
    return run_endpoint("POST", f"/agents/{agent_name}/heartbeat",
                        authorization, x_request_id, go)


@router.get("/health")
def health_short(authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        h = health.production()
        return {"status": h["status"], "gateway_version": GATEWAY_VERSION,
                "database": h["checks"]["database"]["ok"],
                "jobs": h["checks"]["job_system"]["by_status"]}, 200
    return run_endpoint("GET", "/health", authorization, x_request_id, go)


# ------------------------------------------------------ development orchestrator

class DevelopmentTaskIn(BaseModel):
    task: str
    acceptance_criteria: Optional[List[str]] = None
    priority: Optional[int] = 50
    scope_paths: Optional[List[str]] = None


@router.post("/dev/start", operation_id="startDevelopmentTask")
def dev_start(body: DevelopmentTaskIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None),
              idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    def go(key, rid):
        auth.need_scope(key, "write")
        auth.need_scope(key, "tasks")
        if not idempotency_key:
            raise ApiError("VALIDATION_ERROR",
                           "Idempotency-Key обязателен для startDevelopmentTask")
        stable_raw = "%s:%s" % (
            auth.actor_of(key)["name"],
            str(idempotency_key),
        )
        stable = (
            "devstart:"
            + __import__("hashlib")
              .sha256(stable_raw.encode("utf-8"))
              .hexdigest()[:31]
        )
        out = devflow.start_development_task(
            key, stable, body.task, body.acceptance_criteria,
            priority=body.priority or 50, scope_paths=body.scope_paths)
        auth.audit(key, "POST", "/dev/start", 200, request_id=rid,
                   command="startDevelopmentTask",
                   idempotency_key=str(idempotency_key)[:120],
                   response={"DEV_JOB": out.get("DEV_JOB"), "STATUS": out.get("STATUS")})
        return out, 200
    return run_endpoint("POST", "/dev/start", authorization, x_request_id, go)


@router.post("/dev/run")
def dev_run(body: DevelopmentTaskIn, authorization: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None)):
    """Legacy alias. New integrations should call startDevelopmentTask."""
    def go(key, rid):
        auth.need_scope(key, "write")
        auth.need_scope(key, "tasks")
        out = devflow.run_development_task(
            key, rid, body.task, body.acceptance_criteria,
            priority=body.priority or 50, scope_paths=body.scope_paths)
        auth.audit(key, "POST", "/dev/run", 200, request_id=rid,
                   command="run_development_task",
                   response={"DEV_JOB": out.get("DEV_JOB"), "STATUS": out.get("STATUS")})
        return out, 200
    return run_endpoint("POST", "/dev/run", authorization, x_request_id, go)


@router.post("/dev/jobs")
def dev_create(body: DevJobIn, authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = dev.create(key, rid, body.title, body.goal, body.acceptance_criteria, body.scope,
                       reviewer=body.reviewer_agent, priority=body.priority or 50,
                       depends_on=body.depends_on, locks=body.locks)
        sch = dev.schedule()
        j = dev.get(j["id"])
        j["schedule"] = sch
        auth.audit(key, "POST", "/dev/jobs", 201, request_id=rid, command="dev_create",
                   response={"dev_job_id": j["dev_job_id"], "status": j["status"]})
        return j, 201
    return run_endpoint("POST", "/dev/jobs", authorization, x_request_id, go)


@router.post("/dev/jobs/batch")
def dev_batch(body: DevBatchIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        out = dev.create_batch(key, rid, body.jobs, body.batch_id)
        auth.audit(key, "POST", "/dev/jobs/batch", 201, request_id=rid, command="dev_batch",
                   affected=out["created"], response={"batch_id": out["batch_id"]})
        return out, 201
    return run_endpoint("POST", "/dev/jobs/batch", authorization, x_request_id, go)


@router.get("/dev/jobs")
def dev_list(status: Optional[str] = None, limit: int = Query(50, le=200),
             authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return {"jobs": dev.listing(int(qval(limit, 50)), status)}, 200
    return run_endpoint("GET", "/dev/jobs", authorization, x_request_id, go)


@router.get("/dev/queue")
def dev_queue(authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return dev.queue_view(), 200
    return run_endpoint("GET", "/dev/queue", authorization, x_request_id, go)


@router.post("/dev/schedule")
def dev_schedule(authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        return dev.schedule(), 200
    return run_endpoint("POST", "/dev/schedule", authorization, x_request_id, go)


@router.get("/dev/jobs/{dev_job_id}")
def dev_get(dev_job_id: str, authorization: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return dev.get(dev_job_id), 200
    return run_endpoint("GET", f"/dev/jobs/{dev_job_id}", authorization, x_request_id, go)


@router.post("/dev/jobs/{dev_job_id}/phase")
def dev_phase(dev_job_id: str, body: DevPhaseIn,
              authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = dev.update_phase(key, dev_job_id, body.phase, body.state, body.evidence,
                             body.files_changed, body.tests, body.errors, body.decisions,
                             body.notes)
        auth.audit(key, "POST", f"/dev/jobs/{dev_job_id}/phase", 200, request_id=rid,
                   command="dev_phase", status=body.state,
                   response={"phase": body.phase, "job_status": j["status"]})
        return j, 200
    return run_endpoint("POST", f"/dev/jobs/{dev_job_id}/phase", authorization, x_request_id, go)


@router.post("/dev/jobs/{dev_job_id}/criteria")
def dev_criteria(dev_job_id: str, body: DevCriteriaIn,
                 authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        return dev.mark_criteria(key, dev_job_id, body.index, body.done, body.evidence), 200
    return run_endpoint("POST", f"/dev/jobs/{dev_job_id}/criteria",
                        authorization, x_request_id, go)


@router.post("/dev/jobs/{dev_job_id}/complete")
def dev_complete(dev_job_id: str, body: DevCompleteIn,
                 authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = dev.complete(key, dev_job_id, body.summary)
        auth.audit(key, "POST", f"/dev/jobs/{dev_job_id}/complete", 200, request_id=rid,
                   command="dev_complete", status="completed")
        return j, 200
    return run_endpoint("POST", f"/dev/jobs/{dev_job_id}/complete",
                        authorization, x_request_id, go)


@router.get("/dev/jobs/{dev_job_id}/report")
def dev_report(dev_job_id: str, authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return dev.report(dev_job_id), 200
    return run_endpoint("GET", f"/dev/jobs/{dev_job_id}/report", authorization, x_request_id, go)


# ------------------------------------------------------------------ расходы

@router.get("/jobs/{job_id}/costs")
def job_costs(job_id: str, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        j = orchestrator.get_job(job_id)
        if not j:
            raise ApiError("JOB_NOT_FOUND")
        auth.need_account(key, j["account_id"])
        pub = orchestrator.job_public(j)
        rep = costs.report(job_id=j["id"])
        return {"job_id": pub["job_id"], "status": pub["status"], **rep,
                "rendered": costs.render(pub, rep)}, 200
    return run_endpoint("GET", f"/jobs/{job_id}/costs", authorization, x_request_id, go)


@router.post("/costs/approvals")
def costs_approve(body: ApprovalIn, authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        a = costs.grant(key, body.limit_rub, body.operations, body.account_id,
                        body.expires_hours, body.note)
        auth.audit(key, "POST", "/costs/approvals", 201, request_id=rid,
                   command="cost_approval", response={"approval_id": a["approval_id"],
                                                      "limit_rub": a["limit_rub"]})
        return a, 201
    return run_endpoint("POST", "/costs/approvals", authorization, x_request_id, go)


@router.get("/costs/approvals")
def costs_list(authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return {"approvals": [costs.get_approval(a["id"]) for a in costs.approvals()]}, 200
    return run_endpoint("GET", "/costs/approvals", authorization, x_request_id, go)


@router.post("/costs/approvals/{approval_id}/revoke")
def costs_revoke(approval_id: int, authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        return costs.revoke(key, approval_id), 200
    return run_endpoint("POST", f"/costs/approvals/{approval_id}/revoke",
                        authorization, x_request_id, go)


@router.get("/costs/summary")
def costs_summary(authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        rows = db.rows("""SELECT kind, COALESCE(SUM(amount_rub),0) AS total, COUNT(*) AS n
                          FROM ext_job_costs GROUP BY kind""")
        prices = {c: {"price_rub": p["price"], "unit": p["unit"], "note": p["note"]}
                  for c, p in costs.DEFAULT_PRICES.items()}
        for r in db.rows("SELECT * FROM ext_price_book"):
            prices[r["code"]] = {"price_rub": float(r["price_rub"]), "unit": r["unit"],
                                 "note": r.get("note")}
        return {"by_kind": {r["kind"]: {"total_rub": float(r["total"]), "records": r["n"]}
                            for r in rows},
                "price_book": prices,
                "rule": "работа агентов внутри подписки пользователя = 0 ₽ реальных денег; "
                        "любой переход на платный API, usage credits или overage "
                        "останавливается и требует явного разрешения человека"}, 200
    return run_endpoint("GET", "/costs/summary", authorization, x_request_id, go)


# ------------------------------------------------------------------ адаптеры

@router.get("/agents/adapters")
def adapters_list(authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return {"adapters": adapters.all_adapters(),
                "modes": list(adapters.MODES),
                "contract": ["create_job", "execute_job", "continue_job",
                             "handoff_job", "get_status", "cancel_job"],
                "rule": "режим api выключен всегда: это отдельный биллинг, "
                        "включается только человеком и только явно"}, 200
    return run_endpoint("GET", "/agents/adapters", authorization, x_request_id, go)


@router.post("/agents/{agent_name}/adapter")
def adapter_set(agent_name: str, body: AdapterIn,
                authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        a = adapters.set_config(key, agent_name, body.enabled, body.mode, body.note)
        auth.audit(key, "POST", f"/agents/{agent_name}/adapter", 200, request_id=rid,
                   command="adapter_set", response={"agent": a["agent"], "enabled": a["enabled"]})
        return a, 200
    return run_endpoint("POST", f"/agents/{agent_name}/adapter", authorization, x_request_id, go)


@router.post("/agents/{agent_name}/adapter/clear-reason")
def adapter_clear(agent_name: str, authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        return adapters.clear_reason(key, agent_name), 200
    return run_endpoint("POST", f"/agents/{agent_name}/adapter/clear-reason",
                        authorization, x_request_id, go)


# --------------------------------------------------------- дочерние операции

@router.post("/dev/jobs/{dev_job_id}/ops")
def dev_op(dev_job_id: str, body: OpIn, authorization: Optional[str] = Header(None),
           x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        j = dev.set_op(key, dev_job_id, body.name, body.status, body.requested,
                       body.actual, body.evidence, body.error_code, body.note)
        return j, 200
    return run_endpoint("POST", f"/dev/jobs/{dev_job_id}/ops", authorization, x_request_id, go)


# ------------------------------------------------------------------ доска

@router.get("/dev/board")
def dev_board(limit: int = Query(50, le=200), authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        rows = []
        for j in dev.listing(int(qval(limit, 50))):
            gates = dev.gate_report(j)
            done = len(j["required_phases"]) - len([g for g in gates
                                                    if g["gate"] in j["required_phases"]])
            rows.append({
                "job": j["dev_job_id"], "title": j["title"],
                "agent": j["assigned_agent"] or j["owner_agent"],
                "phase": j["current_phase"], "status": j["status"],
                "progress": f"{max(done, 0)}/{len(j['required_phases'])}",
                "error": j["blocked_reason"] or (j["errors"] or [{}])[-1].get("code")
                         if j["errors"] else j["blocked_reason"],
                "qa": j["qa_status"], "operations": {o["name"]: o["status"]
                                                     for o in (j["operations"] or [])},
                "cost_rub": j["cost"]["REAL_CASH_COST"]["TOTAL_RUB"],
                "next_action": (j.get("wait_reason") or
                                (f"фаза {j['current_phase']}" if j["status"] not in
                                 ("completed", "cancelled") else None)),
                "gates_missing": len(gates),
            })
        return {"rows": rows, "queue": dev.queue_view(int(qval(limit, 50))),
                "agents": agents.registry(),
                "adapters": adapters.all_adapters()}, 200
    return run_endpoint("GET", "/dev/board", authorization, x_request_id, go)


# ------------------------------------------------------------------ код проекта
#
# Агент читает файлы и ПРЕДЛАГАЕТ правку. Записать на диск может только
# человек ключом со scope admin и с confirm=true — и то через
# BACKUP -> WRITE -> VERIFY -> ROLLBACK-при-провале.

class RepoTreeIn(BaseModel):
    paths: Optional[List[str]] = None
    max_entries: Optional[int] = None


class RepoReadIn(BaseModel):
    paths: List[str]


class EditIn(BaseModel):
    path: str
    mode: Optional[str] = Field("replace", description="replace — по фрагменту; full — целиком")
    old: Optional[str] = None
    new: Optional[str] = None
    content: Optional[str] = None


class PatchIn(BaseModel):
    dev_job_id: str
    edits: List[EditIn]
    note: Optional[str] = None
    actor: Optional[Actor] = None


class PatchApplyIn(BaseModel):
    confirm: Optional[bool] = False
    skip_verify: Optional[bool] = False


@router.post("/repo/tree")
def repo_tree(body: RepoTreeIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return repo.tree(body.paths, int(body.max_entries or repo.MAX_TREE_ENTRIES)), 200
    return run_endpoint("POST", "/repo/tree", authorization, x_request_id, go)


@router.get("/repo/file")
def repo_file(path: str = Query(...), start: int = Query(1), end: Optional[int] = Query(None),
              authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return repo.read_file(qval(path, ""), qval(start, 1), qval(end, None)), 200
    return run_endpoint("GET", "/repo/file", authorization, x_request_id, go)


@router.post("/repo/files")
def repo_files(body: RepoReadIn, authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return repo.read_many(body.paths), 200
    return run_endpoint("POST", "/repo/files", authorization, x_request_id, go)


@router.post("/repo/patches")
def repo_propose(body: PatchIn, authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        check_actor(key, body.actor.dict() if body.actor else None)
        p = repo.propose(key, body.dev_job_id,
                         [e.dict() for e in body.edits], body.note)
        auth.audit(key, "POST", "/repo/patches", 201, request_id=rid,
                   command="patch_propose", status="proposed",
                   response={"patch_id": p["patch_id"], "files": p["files"]})
        return p, 201
    return run_endpoint("POST", "/repo/patches", authorization, x_request_id, go)


@router.get("/repo/patches")
def repo_patch_list(dev_job_id: Optional[str] = Query(None),
                    status: Optional[str] = Query(None),
                    authorization: Optional[str] = Header(None),
                    x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return {"patches": repo.list_patches(qval(dev_job_id, None),
                                             qval(status, None))}, 200
    return run_endpoint("GET", "/repo/patches", authorization, x_request_id, go)


@router.get("/repo/patches/{patch_id}")
def repo_patch_get(patch_id: int, authorization: Optional[str] = Header(None),
                   x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return repo.get_patch(patch_id), 200
    return run_endpoint("GET", f"/repo/patches/{patch_id}", authorization, x_request_id, go)


@router.post("/repo/patches/{patch_id}/apply")
def repo_patch_apply(patch_id: int, body: PatchApplyIn,
                     authorization: Optional[str] = Header(None),
                     x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "admin")
        p = repo.apply(key, patch_id, bool(body.confirm), bool(body.skip_verify))
        auth.audit(key, "POST", f"/repo/patches/{patch_id}/apply", 200, request_id=rid,
                   command="patch_apply", status=p["status"],
                   response={"patch_id": p["patch_id"], "files": p["files"],
                             "backup_dir": p["backup_dir"]})
        return p, 200
    return run_endpoint("POST", f"/repo/patches/{patch_id}/apply",
                        authorization, x_request_id, go)


@router.post("/repo/patches/{patch_id}/autonomous-apply")
def repo_patch_autonomous_apply(patch_id: int,
                                authorization: Optional[str] = Header(None),
                                x_request_id: Optional[str] = Header(None)):
    """No confirm/skip_verify body by design; role and policy are server-enforced."""
    def go(key, rid):
        auth.need_scope(key, "write")
        auth.need_autonomous_dev(key)
        p = repo.autonomous_apply(key, patch_id)
        auth.audit(key, "POST", f"/repo/patches/{patch_id}/autonomous-apply", 200,
                   request_id=rid, command="autonomous_patch_apply", status=p["status"],
                   response={"patch_id": p["patch_id"], "files": p["files"],
                             "backup_dir": p["backup_dir"]})
        return p, 200
    return run_endpoint("POST", f"/repo/patches/{patch_id}/autonomous-apply",
                        authorization, x_request_id, go)


# ------------------------------------------------------------------ схема

@router.get("/schema.yaml", include_in_schema=False)
def schema_yaml():
    """Описание ручек для GPT Actions. Без ключа — это просто документ;
    каждая описанная здесь операция всё равно требует Bearer-ключ."""
    return PlainTextResponse(schema.SCHEMA_YAML, media_type="application/yaml")


@router.get("/schema", include_in_schema=False)
def schema_alias():
    return PlainTextResponse(schema.SCHEMA_YAML, media_type="application/yaml")


# ------------------------------------------------------------------ сервер
#
# Руки исполнителя. Произвольного shell нет: белый список бинарей, argv без
# оболочки, изменяющее действие ровно одно — перезапуск своих же служб.

class ShellIn(BaseModel):
    command: str = Field(..., description="одна команда с аргументами, без конвейеров")
    cwd: Optional[str] = Field(None, description="каталог внутри проекта")
    timeout: Optional[int] = None


class RestartIn(BaseModel):
    unit: Optional[str] = "boris-backend"
    reason: Optional[str] = None


class BuildIn(BaseModel):
    target: str = Field("frontend", description="frontend | backend")
    timeout: Optional[int] = None


class TestsIn(BaseModel):
    suite: Optional[str] = "gateway"
    account: Optional[Union[str, int]] = None
    timeout: Optional[int] = None


class SqlIn(BaseModel):
    sql: str
    limit: Optional[int] = 100


@router.post("/ops/shell")
def ops_shell(body: ShellIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        return ops.shell(key, body.command, body.cwd, body.timeout), 200
    return run_endpoint("POST", "/ops/shell", authorization, x_request_id, go)


@router.get("/ops/facts")
def ops_facts(authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.facts(key), 200
    return run_endpoint("GET", "/ops/facts", authorization, x_request_id, go)


@router.get("/ops/secrets")
def ops_secrets(authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.secrets_presence(key), 200
    return run_endpoint("GET", "/ops/secrets", authorization, x_request_id, go)


@router.get("/ops/logs")
def ops_logs(unit: str = Query("boris-backend"), lines: int = Query(200),
             since: Optional[str] = Query(None),
             authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.logs(key, qval(unit, "boris-backend"), qval(lines, 200),
                        qval(since, None)), 200
    return run_endpoint("GET", "/ops/logs", authorization, x_request_id, go)


@router.get("/ops/service")
def ops_service(unit: str = Query("boris-backend"),
                authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.service_status(key, qval(unit, "boris-backend")), 200
    return run_endpoint("GET", "/ops/service", authorization, x_request_id, go)


@router.post("/ops/service/restart")
def ops_restart(body: RestartIn, authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        return ops.restart_service(key, body.unit or "boris-backend", body.reason), 200
    return run_endpoint("POST", "/ops/service/restart", authorization, x_request_id, go)


@router.get("/ops/http")
def ops_http(path: str = Query("/health"), port: int = Query(8000),
             authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.http_check(key, qval(path, "/health"), qval(port, 8000)), 200
    return run_endpoint("GET", "/ops/http", authorization, x_request_id, go)


@router.get("/ops/git")
def ops_git(what: str = Query("status"), limit: int = Query(20),
            authorization: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.git_info(key, qval(what, "status"), qval(limit, 20)), 200
    return run_endpoint("GET", "/ops/git", authorization, x_request_id, go)


@router.get("/ops/cron")
def ops_cron(authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.cron_info(key), 200
    return run_endpoint("GET", "/ops/cron", authorization, x_request_id, go)


@router.post("/ops/build")
def ops_build(body: BuildIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        return ops.build(key, body.target, body.timeout or 900), 200
    return run_endpoint("POST", "/ops/build", authorization, x_request_id, go)


@router.post("/ops/tests")
def ops_tests(body: TestsIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        return ops.tests(key, body.suite or "gateway", body.account, body.timeout or 900), 200
    return run_endpoint("POST", "/ops/tests", authorization, x_request_id, go)


@router.post("/ops/db/query")
def ops_db_query(body: SqlIn, authorization: Optional[str] = Header(None),
                 x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        return ops.db_query(key, body.sql, body.limit or 100), 200
    return run_endpoint("POST", "/ops/db/query", authorization, x_request_id, go)


@router.get("/ops/readiness")
def ops_readiness(authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return ops.readiness(key), 200
    return run_endpoint("GET", "/ops/readiness", authorization, x_request_id, go)


# ------------------------------------------------------ контур Lead ⇄ Executor

class OrderIn(BaseModel):
    dev_job_id: str
    phase: Optional[str] = None
    goal: str
    scope: Optional[Dict[str, Any]] = None
    acceptance_criteria: List[str]
    forbidden_changes: Optional[List[str]] = None
    required_qa: Optional[List[str]] = None
    required_evidence: Optional[List[str]] = None
    assigned_to: Optional[str] = "gpt-executor"
    max_iterations: Optional[int] = None
    priority: Optional[int] = 50


class ReviewIn(BaseModel):
    verdict: str = Field(..., description="ACCEPT | RETURN_WITH_FIXES | BLOCKED_HUMAN | BLOCKED_INFRA")
    note: Optional[str] = None
    fixes: Optional[List[Dict[str, Any]]] = Field(
        None, description="для RETURN_WITH_FIXES: failed_criterion, problem, required_correction")
    blocked_reason: Optional[str] = None


class ReviewRequestIn(BaseModel):
    summary: str
    evidence: str
    files_changed: Optional[List[str]] = None
    build: Optional[str] = None
    tests: Optional[str] = None
    runtime: Optional[str] = None
    acceptance_criteria: Optional[List[Dict[str, Any]]] = None
    blockers: Optional[List[str]] = None
    safety: Optional[str] = None


class ClaimIn(BaseModel):
    agent: Optional[str] = None
    limit: Optional[int] = 1


@router.post("/a2a/orders")
def a2a_create(body: OrderIn, authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        o = a2a.create_order(key, body.dev_job_id, body.phase, body.goal, body.scope,
                             body.acceptance_criteria, body.forbidden_changes,
                             body.required_qa, body.required_evidence,
                             body.assigned_to or "gpt-executor",
                             body.max_iterations, body.priority or 50)
        return o, 201
    return run_endpoint("POST", "/a2a/orders", authorization, x_request_id, go)


@router.get("/a2a/orders")
def a2a_list(status: Optional[str] = Query(None), dev_job_id: Optional[str] = Query(None),
             assigned_to: Optional[str] = Query(None), limit: int = Query(50),
             authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return {"orders": a2a.listing(qval(status, None), qval(dev_job_id, None),
                                      qval(assigned_to, None), qval(limit, 50))}, 200
    return run_endpoint("GET", "/a2a/orders", authorization, x_request_id, go)


@router.get("/a2a/orders/{order_id}")
def a2a_get(order_id: int, authorization: Optional[str] = Header(None),
            x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return a2a.get(order_id), 200
    return run_endpoint("GET", f"/a2a/orders/{order_id}", authorization, x_request_id, go)


@router.post("/a2a/claim")
def a2a_claim(body: ClaimIn, authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        who = body.agent or auth.actor_of(key)["name"]
        check_actor(key, {"name": who})
        return {"orders": a2a.claim(who, int(body.limit or 1))}, 200
    return run_endpoint("POST", "/a2a/claim", authorization, x_request_id, go)


@router.post("/a2a/orders/{order_id}/review-request")
def a2a_review_request(order_id: int, body: ReviewRequestIn,
                       authorization: Optional[str] = Header(None),
                       x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        return a2a.submit_review(order_id, auth.actor_of(key)["name"], body.summary,
                                 body.files_changed, body.build, body.tests, body.runtime,
                                 body.acceptance_criteria, body.evidence, body.blockers,
                                 body.safety), 200
    return run_endpoint("POST", f"/a2a/orders/{order_id}/review-request",
                        authorization, x_request_id, go)


@router.post("/a2a/orders/{order_id}/review")
def a2a_review(order_id: int, body: ReviewIn,
               authorization: Optional[str] = Header(None),
               x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "write")
        return a2a.review(key, order_id, body.verdict, body.note, body.fixes,
                          body.blocked_reason), 200
    return run_endpoint("POST", f"/a2a/orders/{order_id}/review",
                        authorization, x_request_id, go)


@router.get("/a2a/board")
def a2a_board(limit: int = Query(50), authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        return a2a.board(qval(limit, 50)), 200
    return run_endpoint("GET", "/a2a/board", authorization, x_request_id, go)


@router.get("/a2a/executor/probe")
def a2a_probe(authorization: Optional[str] = Header(None),
              x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        from . import executor
        return executor.probe(), 200
    return run_endpoint("GET", "/a2a/executor/probe", authorization, x_request_id, go)


@router.post("/a2a/executor/tick")
def a2a_tick(authorization: Optional[str] = Header(None),
             x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        from . import executor
        o = executor.once()
        return (o or {"note": "нарядов в очереди нет"}), 200
    return run_endpoint("POST", "/a2a/executor/tick", authorization, x_request_id, go)


@router.post("/a2a/executor/orders/{order_id}/execute")
def a2a_execute_order(order_id: int, authorization: Optional[str] = Header(None),
                      x_request_id: Optional[str] = Header(None)):
    """Deterministically claim+execute one order through the server executor pool."""
    def go(key, rid):
        auth.need_scope(key, "tasks")
        from . import executor
        o = executor.once_order(order_id)
        auth.audit(key, "POST", f"/a2a/executor/orders/{order_id}/execute", 200,
                   request_id=rid, command="a2a_execute_order",
                   response={"order_id": order_id, "status": o.get("status")})
        return o, 200
    return run_endpoint("POST", f"/a2a/executor/orders/{order_id}/execute",
                        authorization, x_request_id, go)


@router.get("/a2a/lead/probe")
def a2a_lead_probe(authorization: Optional[str] = Header(None),
                   x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "read")
        from . import lead, notify
        return {"lead": lead.probe(), "notifications": notify.status()}, 200
    return run_endpoint("GET", "/a2a/lead/probe", authorization, x_request_id, go)


@router.post("/a2a/lead/tick")
def a2a_lead_tick(authorization: Optional[str] = Header(None),
                  x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        auth.need_scope(key, "tasks")
        from . import lead
        o = lead.once()
        return (o or {"note": "нарядов на приёмке нет"}), 200
    return run_endpoint("POST", "/a2a/lead/tick", authorization, x_request_id, go)


class BrowserIn(BaseModel):
    paths: List[str]
    width: Optional[int] = 1440
    height: Optional[int] = 900
    port: Optional[int] = 3002


@router.post("/ops/browser")
def ops_browser(body: BrowserIn, authorization: Optional[str] = Header(None),
                x_request_id: Optional[str] = Header(None)):
    def go(key, rid):
        return ops.browser(key, body.paths, body.width or 1440, body.height or 900,
                           body.port or 3002), 200
    return run_endpoint("POST", "/ops/browser", authorization, x_request_id, go)
