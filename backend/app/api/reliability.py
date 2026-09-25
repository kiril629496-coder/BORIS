"""BORIS Reliability / System Guardian API.

Owner gets fleet-wide operational visibility and controls. Client users can only
see reliability information for their own account and cannot mutate controls.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob
from app.models.reliability import ReliabilityEvent, ReliabilityFlag, ReliabilityKillSwitch
from app.models.control_plane import ControlRequirement, ControlExecution, ControlIncident
from app.models.storage import Storage
from app.services.reliability import queue_health, record_event, system_health

router = APIRouter(prefix="/api/reliability", tags=["reliability"])


def _is_owner(user) -> bool:
    return str(getattr(user, "role", "") or "").lower() == "owner"


def _scope_account(user, requested: Optional[str]) -> Optional[str]:
    own = str(getattr(user, "account_id", "") or "").strip()
    if _is_owner(user):
        return str(requested or "").strip() or None
    if not own:
        raise HTTPException(status_code=403, detail="Account scope required")
    if requested and str(requested) != own:
        raise HTTPException(status_code=403, detail="Cross-account access denied")
    return own


def _owner_only(user):
    if not _is_owner(user):
        raise HTTPException(status_code=403, detail="Owner access required")


class KillSwitchBody(BaseModel):
    module: str
    account_id: str = "*"
    blocked: bool
    reason: str = ""


class FlagBody(BaseModel):
    key: str
    scope_type: str = "global"
    scope_id: str = "*"
    enabled: bool
    config: dict = {}


@router.get("/health")
def health(account_id: Optional[str] = Query(None), user=Depends(get_current_user)):
    _owner_only(user)
    account_id = _scope_account(user, account_id)
    db = SessionLocal()
    try:
        base = system_health(db)
        if not _is_owner(user):
            # Clients get only safe account-level state, never fleet internals/errors.
            jobs = db.query(BackgroundJob).filter(BackgroundJob.account_id == account_id)
            base["queue"] = {
                "state": "ok",
                "queued": jobs.filter(BackgroundJob.status == "queued").count(),
                "running": jobs.filter(BackgroundJob.status.in_(("running", "validating"))).count(),
                "failed_24h": jobs.filter(
                    BackgroundJob.status == "failed",
                    BackgroundJob.finished_at >= datetime.now(timezone.utc) - timedelta(hours=24),
                ).count(),
            }
            base.pop("circuits", None)
            base.pop("open_circuits", None)
        base["account_id"] = account_id
        return base
    finally:
        db.close()


@router.get("/guardian")
def guardian(account_id: Optional[str] = Query(None), user=Depends(get_current_user)):
    _owner_only(user)
    account_id = _scope_account(user, account_id)
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        base = system_health(db)
        q = db.query(BackgroundJob)
        if account_id:
            q = q.filter(BackgroundJob.account_id == account_id)
        queued = q.filter(BackgroundJob.status == "queued").count()
        running = q.filter(BackgroundJob.status.in_(("running", "validating"))).count()
        failed = q.filter(BackgroundJob.status == "failed", BackgroundJob.finished_at >= now - timedelta(hours=24)).count()
        stale = q.filter(
            BackgroundJob.status.in_(("running", "validating")),
            BackgroundJob.started_at < now - timedelta(minutes=20),
        ).count()
        storage = base.get("storage") or {}
        backup = base.get("backup") or {}
        workers = base.get("workers") or {}
        database = base.get("database") or {}
        db_state = str(database.get("state") or "ok")
        db_idle = int(database.get("idle_in_transaction_30s") or 0)
        db_oldest = int(database.get("oldest_idle_transaction_sec") or 0)
        checks = [
            {"key": "database", "name": "База данных", "state": "critical" if db_state == "critical" else ("warning" if db_state == "degraded" else "ok"),
             "message": (f"Есть зависшие транзакции: {db_idle}, старейшая {db_oldest} сек" if db_idle else "PostgreSQL отвечает · зависших транзакций нет")},
            {"key": "storage", "name": "Диск сервера", "state": "critical" if storage.get("state") == "critical" else ("warning" if storage.get("state") == "degraded" else "ok"),
             "message": f"Свободно {storage.get('free_gb', '—')} ГБ · занято {storage.get('used_pct', '—')}%"},
            {"key": "backup", "name": "Резервная копия БД", "state": "critical" if backup.get("state") == "critical" else ("warning" if backup.get("state") == "degraded" else "ok"),
             "message": f"Последняя копия {backup.get('age_hours', '—')} ч назад · {backup.get('size_mb', '—')} МБ"},
            {"key": "queue", "name": "Очередь BORIS", "state": "critical" if stale else ("warning" if failed else "ok"),
             "message": f"В очереди {queued}, выполняется {running}, ошибок за 24ч {failed}"},
            {"key": "stale_jobs", "name": "Зависшие задачи", "state": "critical" if stale else "ok",
             "message": f"Зависших задач: {stale}" if stale else "Зависших задач нет"},
            {"key": "workers", "name": "Рабочие процессы",
             "state": "critical" if workers.get("state") == "critical" else ("warning" if workers.get("state") == "degraded" else "ok"),
             "message": f"Воркеров зарегистрировано {workers.get('registered', 0)}, не отвечают {workers.get('stale', 0)}"},
        ]
        if _is_owner(user):
            switches = db.query(ReliabilityKillSwitch).filter(ReliabilityKillSwitch.blocked.is_(True)).all()
            checks.append({"key": "kill_switches", "name": "Аварийные выключатели",
                           "state": "warning" if switches else "ok",
                           "message": f"Активно выключателей: {len(switches)}" if switches else "Все модули разрешены"})
            # Global rollout truth comes from the authoritative production contract,
            # not from counting UI modules. One card answers whether every configured
            # account is really on the same policy and runtime generation.
            rollout_row=(db.query(Storage).filter(Storage.account_id=='__platform__',Storage.key=='global_policy_rollout_latest')
                         .order_by(Storage.id.desc()).first())
            rollout={}
            if rollout_row:
                try: rollout=json.loads(rollout_row.value or '{}')
                except Exception: rollout={}
            checked_at=None
            try: checked_at=datetime.fromisoformat(str(rollout.get('checked_at') or '').replace('Z','+00:00'))
            except Exception: checked_at=None
            if checked_at:
                checked_at = checked_at.replace(tzinfo=timezone.utc) if checked_at.tzinfo is None else checked_at.astimezone(timezone.utc)
            age_min=((now-checked_at).total_seconds()/60.0) if checked_at else None
            rollout_ok=(str(rollout.get('status') or '')=='pass' and int(rollout.get('accounts') or 0)>0 and int(rollout.get('passed_accounts') or 0)==int(rollout.get('accounts') or 0))
            rollout_state='critical' if not rollout_ok else ('warning' if age_min is None or age_min>15 else 'ok')
            total=int(rollout.get('accounts') or 0); passed=int(rollout.get('passed_accounts') or 0)
            version=str(rollout.get('policy_version') or '—')
            checks.append({"key":"global_rollout","name":"Глобальные корректировки","state":rollout_state,
                           "message":(f"Политика {version} · {passed}/{total} аккаунтов синхронно" if total else "Нет подтверждённого общего rollout"),
                           "details":{"checked_at":rollout.get('checked_at'),"runtime_self_heal":rollout.get('runtime_self_heal') or {},"reconciled_accounts":len(rollout.get('reconciled_accounts') or {})}})
            # CONTROL_PLANE_GUARDIAN_PROJECTION_V1: System Guardian exposes the
            # canonical owner-rule plane rather than making the owner inspect a
            # separate backend. Open P0/P1 incidents are critical; lower-priority
            # unresolved deviations keep the system warning until verified.
            _cp_active=db.query(ControlRequirement).filter(ControlRequirement.status=='active').count()
            _cp_open=db.query(ControlIncident).filter(ControlIncident.state=='open').all()
            _cp_p01=sum(1 for x in _cp_open if str(x.priority or '') in {'P0','P1'})
            _cp_pass=db.query(ControlExecution).filter(ControlExecution.status=='pass').count()
            _cp_blocked=db.query(ControlExecution).filter(ControlExecution.status=='blocked').count()
            _cp_state='critical' if _cp_p01 else ('warning' if _cp_open else 'ok')
            checks.append({
                "key":"control_plane","name":"Правила BORIS","state":_cp_state,
                "message":f"Активных правил {_cp_active} · подтверждено {_cp_pass} · заблокировано {_cp_blocked} · открытых инцидентов {len(_cp_open)}",
                "details":{"active_requirements":_cp_active,"verified_executions":_cp_pass,"blocked_executions":_cp_blocked,"open_incidents":len(_cp_open),"p0_p1":_cp_p01}
            })
            # MARKETER_HEALTH_LATEST_EVIDENCE_V1: account-health is a projection of
            # the newest authoritative evidence, not a sticky incident flag. A later
            # successful virtual_marketer_runtime must clear an older account-health
            # incident/deferred state so owner UI cannot remain red after recovery.
            health_rows=(db.query(Storage).filter(Storage.key=='virtual_marketer_account_health')
                         .order_by(Storage.id.desc()).all())
            runtime_rows=(db.query(Storage).filter(Storage.key=='virtual_marketer_runtime')
                          .order_by(Storage.id.desc()).all())
            runtime_latest={}
            for rr in runtime_rows:
                aid=str(rr.account_id or '')
                if not aid or aid in runtime_latest: continue
                try: runtime_latest[aid]=json.loads(rr.value or '{}')
                except Exception: runtime_latest[aid]={}
            marketer_health={}
            for hr in health_rows:
                aid=str(hr.account_id or '')
                if not aid or aid in marketer_health: continue
                try: mh=json.loads(hr.value or '{}')
                except Exception: mh={}
                try: ts=datetime.fromisoformat(str(mh.get('checked_at') or '').replace('Z','+00:00'))
                except Exception: ts=None
                rt=runtime_latest.get(aid) or {}
                try: rts=datetime.fromisoformat(str(rt.get('finished_at') or rt.get('last_run_at') or '').replace('Z','+00:00'))
                except Exception: rts=None
                # Normalize both sides before comparing mixed legacy/current evidence.
                if ts:
                    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                if rts:
                    rts = rts.replace(tzinfo=timezone.utc) if rts.tzinfo is None else rts.astimezone(timezone.utc)
                if rts and (ts is None or rts > ts) and str(rt.get('status') or '').lower() in {'ok','pass','healthy'}:
                    mh={
                        'state':'ok',
                        'reason':str(rt.get('mode') or 'latest virtual marketer cycle completed successfully')[:500],
                        'stage':'virtual_marketer_runtime',
                        'checked_at':rts.isoformat(),
                        'reconciled_from_runtime':True,
                    }
                    ts=rts
                # Only current operational evidence participates. Old incidents
                # must not keep the fleet yellow after a later successful cycle.
                if ts:
                    # RELIABILITY_GUARDIAN_TZ_NORMALIZE_V1: persisted events may contain
                    # either legacy naive UTC timestamps or offset-aware ISO timestamps.
                    # Normalize before arithmetic so one malformed/legacy event cannot 500
                    # the global reliability dashboard.
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                if ts and (now-ts).total_seconds()<=7200:
                    marketer_health[aid]=mh
            counts={s:sum(1 for x in marketer_health.values() if str(x.get('state') or '')==s) for s in ('ok','deferred','incident','systemic')}
            marketer_state='critical' if counts['systemic'] else ('warning' if counts['incident'] or counts['deferred'] else 'ok')
            marketer_message=(f"Аккаунтов: OK {counts['ok']} · отложено безопасно {counts['deferred']} · инцидентов {counts['incident']} · системных {counts['systemic']}")
            checks.append({"key":"marketer_accounts","name":"ИИ-маркетолог по аккаунтам","state":marketer_state,"message":marketer_message,
                           "details":{"counts":counts,"incident_accounts":[a for a,x in marketer_health.items() if str(x.get('state') or '') in {'incident','systemic'}]}})

            # Position Monitor is a separate read-only capability. Missing an
            # identity-verified cabinet session must be visible fleet-wide but must
            # not falsely mark the entire AI marketer as broken: official Avito API
            # has no search-position field, so this capability legitimately waits
            # for an isolated browser session for that specific tenant.
            try:
                from app.services.avito_position_monitor import active_ai_accounts
                _position_accounts = active_ai_accounts()
            except Exception:
                _position_accounts = []
            _position_health = {}
            if _position_accounts:
                _ph_rows=(db.query(Storage).filter(Storage.key=='avito_position_health',Storage.account_id.in_(_position_accounts))
                          .order_by(Storage.id.desc()).all())
                for _hr in _ph_rows:
                    _aid=str(_hr.account_id or '')
                    if not _aid or _aid in _position_health: continue
                    try: _position_health[_aid]=json.loads(_hr.value or '{}')
                    except Exception: _position_health[_aid]={}
            _pc={s:0 for s in ('ok','partial','deferred','incident','unknown')}
            for _aid in _position_accounts:
                _state=str((_position_health.get(_aid) or {}).get('state') or 'unknown')
                if _state not in _pc: _state='unknown'
                _pc[_state]+=1
            _position_state='critical' if _pc['incident'] else ('warning' if _pc['partial'] or _pc['deferred'] or _pc['unknown'] else 'ok')
            checks.append({
                "key":"avito_positions","name":"Позиции объявлений Avito","state":_position_state,
                "message":f"AI-аккаунтов {_position_accounts.__len__()} · полный замер {_pc['ok']} · частичный {_pc['partial']} · ждут изолированную сессию {_pc['deferred']} · инцидентов {_pc['incident']}",
                "details":{
                    "counts":_pc,
                    "waiting_session_accounts":[a for a in _position_accounts if str((_position_health.get(a) or {}).get('state') or 'unknown') in {'deferred','unknown'}],
                    "partial_accounts":[a for a in _position_accounts if str((_position_health.get(a) or {}).get('state') or '')=='partial'],
                    "source":"identity_verified_avito_pro_browser_read",
                    "official_api_position_field":False,
                }
            })
        state = "critical" if any(x["state"] == "critical" for x in checks) else ("degraded" if any(x["state"] == "warning" for x in checks) else "ok")
        return {"state": state, "checks": checks, "account_id": account_id}
    finally:
        db.close()


@router.get("/capacity")
def capacity(user=Depends(get_current_user)):
    """Owner-only fleet pressure: queues by module and top noisy tenants."""
    _owner_only(user)
    db = SessionLocal()
    try:
        base = system_health(db)
        from app.services.reliability import capacity_plan
        plan = capacity_plan(db)
        return {"state": base.get("state"), "queue": base.get("queue"),
                "breakdown": base.get("queue_breakdown"), "workers": base.get("workers"),
                "capacity": base.get("capacity"), "recommendation": base.get("capacity_recommendation"),
                "retry_budgets": base.get("retry_budgets"),
                "open_circuits": base.get("open_circuits"), "plan": plan}
    finally:
        db.close()


@router.get("/events")
def events(account_id: Optional[str] = Query(None), module: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500), user=Depends(get_current_user)):
    _owner_only(user)
    account_id = _scope_account(user, account_id)
    db = SessionLocal()
    try:
        q = db.query(ReliabilityEvent)
        if account_id:
            q = q.filter(ReliabilityEvent.account_id == account_id)
        if module:
            q = q.filter(ReliabilityEvent.module == module)
        rows = q.order_by(ReliabilityEvent.id.desc()).limit(limit).all()
        return [{"id": r.id, "trace_id": r.trace_id, "account_id": r.account_id, "module": r.module,
                 "event_type": r.event_type, "severity": r.severity, "state": r.state,
                 "message": r.message, "details": r.details_json or {},
                 "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
    finally:
        db.close()


def _dead_letter_retryability(job: BackgroundJob) -> tuple[bool, str]:
    """Return whether Guardian may safely requeue this exact durable job.

    A terminal Avito publication result is evidence, not a failed queue action.
    Replaying it cannot repair rejected ads and must never become a second
    publication attempt. A pending-review upload is different: its payload still
    points at the already-submitted upload and requeue only rechecks that result.
    """
    result = dict(job.result_json or {})
    payload = dict(job.payload_json or {})
    stage = str(result.get("stage") or payload.get("stage") or "")
    if job.kind == "campaign_deliver":
        if stage == "publication_pending_review" and int(payload.get("upload_id") or result.get("upload_id") or 0) > 0:
            return True, "recheck_existing_upload"
        if stage in {"publication_partial", "publication_failed", "delivered"}:
            return False, "terminal_publication_result"
    return job.status in ("failed", "partial", "cancelled"), "retry_job"


@router.get("/dead-letter")
def dead_letter(account_id: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500), user=Depends(get_current_user)):
    _owner_only(user)
    account_id = _scope_account(user, account_id)
    db = SessionLocal()
    try:
        q = db.query(BackgroundJob).filter(BackgroundJob.status.in_(("failed", "partial")))
        if account_id:
            q = q.filter(BackgroundJob.account_id == account_id)
        rows = q.order_by(BackgroundJob.id.desc()).limit(limit).all()
        items = []
        for j in rows:
            retryable, retry_action = _dead_letter_retryability(j)
            items.append({"id": j.id, "kind": j.kind, "account_id": j.account_id, "status": j.status,
                          "attempts": int(j.attempts or 0), "trace_id": str((j.payload_json or {}).get("_trace_id") or ""),
                          "created_at": j.created_at.isoformat() if j.created_at else None,
                          "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                          "retryable": retryable, "retry_action": retry_action,
                          "error": "Ошибка сохранена. Повторный запуск доступен владельцу." if j.status == "failed" else "Завершено частично"})
        return items
    finally:
        db.close()


@router.post("/dead-letter/{job_id}/retry")
def retry_dead_letter(job_id: int, user=Depends(get_current_user)):
    _owner_only(user)
    db = SessionLocal()
    try:
        j = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).with_for_update().first()
        if j is None:
            raise HTTPException(status_code=404, detail="Job not found")
        retryable, retry_action = _dead_letter_retryability(j)
        if not retryable:
            raise HTTPException(status_code=409, detail="Terminal publication result cannot be replayed; fix rejected items and run canonical preparation again")
        j.status = "queued"
        j.started_at = None
        j.finished_at = None
        j.scheduled_at = None
        j.error_text = None
        record_event(db, "jobs", "dead_letter_retry", f"Повторный запуск задачи #{j.id}", account_id=j.account_id,
                     details={"job_id": j.id, "kind": j.kind, "attempts_before_retry": int(j.attempts or 0)})
        db.commit()
        return {"ok": True, "job_id": j.id, "status": j.status}
    finally:
        db.close()


@router.get("/kill-switches")
def kill_switches(user=Depends(get_current_user)):
    _owner_only(user)
    db = SessionLocal()
    try:
        rows = db.query(ReliabilityKillSwitch).order_by(ReliabilityKillSwitch.module, ReliabilityKillSwitch.account_id).all()
        return [{"module": r.module, "account_id": r.account_id, "blocked": bool(r.blocked), "reason": r.reason or "",
                 "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]
    finally:
        db.close()


@router.post("/kill-switches")
def set_kill_switch(body: KillSwitchBody, user=Depends(get_current_user)):
    _owner_only(user)
    module = body.module.strip().lower()[:64]
    account_id = body.account_id.strip()[:255] or "*"
    if not module:
        raise HTTPException(status_code=422, detail="module required")
    db = SessionLocal()
    try:
        row = db.query(ReliabilityKillSwitch).filter(ReliabilityKillSwitch.module == module, ReliabilityKillSwitch.account_id == account_id).first()
        if row is None:
            row = ReliabilityKillSwitch(module=module, account_id=account_id)
            db.add(row)
        row.blocked = bool(body.blocked)
        row.reason = body.reason[:2000]
        row.updated_by = int(user.id)
        record_event(db, "system", "kill_switch_changed", f"{module}: {'OFF' if body.blocked else 'ON'}",
                     severity="warning" if body.blocked else "info", account_id=None if account_id == "*" else account_id,
                     details={"module": module, "scope": account_id, "blocked": bool(body.blocked)})
        db.commit()
        return {"ok": True, "module": module, "account_id": account_id, "blocked": bool(row.blocked)}
    finally:
        db.close()


@router.get("/flags")
def flags(user=Depends(get_current_user)):
    _owner_only(user)
    db = SessionLocal()
    try:
        rows = db.query(ReliabilityFlag).order_by(ReliabilityFlag.key, ReliabilityFlag.scope_type, ReliabilityFlag.scope_id).all()
        return [{"key": r.key, "scope_type": r.scope_type, "scope_id": r.scope_id, "enabled": bool(r.enabled), "config": r.config_json or {}} for r in rows]
    finally:
        db.close()


@router.post("/flags")
def set_flag(body: FlagBody, user=Depends(get_current_user)):
    _owner_only(user)
    key = body.key.strip()[:128]
    scope_type = body.scope_type.strip().lower()
    scope_id = body.scope_id.strip()[:255] or "*"
    if not key or scope_type not in ("global", "account"):
        raise HTTPException(status_code=422, detail="invalid flag scope")
    if scope_type == "global":
        scope_id = "*"
    db = SessionLocal()
    try:
        row = db.query(ReliabilityFlag).filter(ReliabilityFlag.key == key, ReliabilityFlag.scope_type == scope_type, ReliabilityFlag.scope_id == scope_id).first()
        if row is None:
            row = ReliabilityFlag(key=key, scope_type=scope_type, scope_id=scope_id)
            db.add(row)
        row.enabled = bool(body.enabled)
        row.config_json = body.config or {}
        row.updated_by = int(user.id)
        record_event(db, "system", "feature_flag_changed", f"{key}: {'ON' if body.enabled else 'OFF'}",
                     account_id=None if scope_id == "*" else scope_id,
                     details={"key": key, "scope_type": scope_type, "scope_id": scope_id, "enabled": bool(body.enabled)})
        db.commit()
        return {"ok": True, "key": key, "scope_type": scope_type, "scope_id": scope_id, "enabled": bool(row.enabled)}
    finally:
        db.close()
