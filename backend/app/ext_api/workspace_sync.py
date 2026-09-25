# -*- coding: utf-8 -*-
"""Mirror BORIS OPS development truth into durable Control Workspace.
No AI calls. Idempotent. Called by dispatcher; failures must never stop recovery.
"""
import datetime, json, re, uuid
from pathlib import Path
from . import db
from app.services.exception_observability import observe_suppressed

_PROVIDER_WATCH_STATE = Path(
    "/var/lib/boris-integrity/workstream-scopes/development_provider_watch.json"
)

def _loads(v, default):
    try: return json.loads(v or '')
    except Exception: return default

def ensure_system_brain_workspace():
    """Ensure exactly one durable global Brain workspace. DB-only, no AI/provider calls."""
    from datetime import datetime, timezone
    from app.db.session import SessionLocal
    from app.models.control_plane import ControlWorkspaceThread, ControlWorkspaceEvent
    from sqlalchemy import text
    import uuid

    ref="system-brain:owner-global"
    s=SessionLocal()
    created=False; deduped=0
    try:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),{"k":f"workspace-ref:{ref}"})
        rows=(s.query(ControlWorkspaceThread)
              .filter(ControlWorkspaceThread.external_chat_ref==ref)
              .order_by(ControlWorkspaceThread.created_at.asc(),ControlWorkspaceThread.id.asc())
              .all())
        if rows:
            row=rows[0]
        else:
            row=ControlWorkspaceThread(
                id=str(uuid.uuid4()),thread_type="project",title="Центральный Мозг BORIS",
                external_chat_ref=ref,status="active",
                summary="Глобальная owner-командная ветка единого Control Plane",
                current_state_json={
                    "memory_mode":"durable_system_brain",
                    "current_step":"Мозг контролирует общее состояние BORIS",
                    "next_step":"Выполнять безопасный план и принимать глобальные команды владельца",
                },
                plan_json=[],
                source_json={"surface":"control-plane","source":"system-brain","owner_only":True},
            )
            s.add(row); s.flush()
            s.add(ControlWorkspaceEvent(
                thread_id=row.id,event_type="brain_workspace_created",actor="boris_brain",
                text="Автоматически создана единая durable-ветка Центрального Мозга.",
                payload_json={"external_chat_ref":ref,"self_heal":True},
            ))
            rows=[row]; created=True

        changed=False
        source=dict(row.source_json or {})
        state=dict(row.current_state_json or {})
        desired_source={**source,"surface":"control-plane","source":"system-brain","owner_only":True}
        if row.thread_type!="project": row.thread_type="project"; changed=True
        if row.status!="active": row.status="active"; changed=True
        if row.title!="Центральный Мозг BORIS": row.title="Центральный Мозг BORIS"; changed=True
        if row.summary!="Глобальная owner-командная ветка единого Control Plane":
            row.summary="Глобальная owner-командная ветка единого Control Plane"; changed=True
        if desired_source!=source: row.source_json=desired_source; changed=True
        if state.get("memory_mode")!="durable_system_brain":
            state["memory_mode"]="durable_system_brain"; changed=True
        if not state.get("current_step"):
            state["current_step"]="Мозг контролирует общее состояние BORIS"; changed=True
        if not state.get("next_step"):
            state["next_step"]="Выполнять безопасный план и принимать глобальные команды владельца"; changed=True
        row.current_state_json=state
        if changed and not created:
            row.last_event_at=datetime.now(timezone.utc)
            s.add(ControlWorkspaceEvent(
                thread_id=row.id,event_type="brain_workspace_repaired",actor="boris_brain",
                text="Единая ветка Мозга автоматически приведена к каноническому состоянию.",
                payload_json={"external_chat_ref":ref,"self_heal":True},
            ))

        for dup in rows[1:]:
            dup_source=dict(dup.source_json or {})
            dup_source.update({"source":"system-brain-duplicate","surface":"control-plane-archive",
                               "duplicate_of":str(row.id),"owner_only":True})
            dup.source_json=dup_source
            dup.external_chat_ref=f"system-brain:duplicate:{dup.id}"
            dup.status="archived"
            dup.last_event_at=datetime.now(timezone.utc)
            s.add(ControlWorkspaceEvent(
                thread_id=dup.id,event_type="brain_workspace_deduped",actor="boris_brain",
                text="Дублирующая ветка Мозга архивирована; история сохранена.",
                payload_json={"canonical_thread_id":str(row.id),"self_heal":True},
            ))
            deduped+=1
        s.commit()
        return {"status":"pass","created":created,"deduped":deduped,"thread_id":str(row.id)}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _workspace_autopilot_runtime_state(st, *, autopilot_enabled=True, wait_reason=None,
                                      blocked_reason=None, provider_wait_reason=None):
    """Truthful owner-visible state for a mirrored development job."""
    st=str(st or '').strip().lower()
    if st=='completed':
        return 'done'
    if not autopilot_enabled:
        return 'paused'
    if st in ('blocked','failed') or bool(blocked_reason):
        return 'blocked'
    if st in ('waiting','queued') or bool(wait_reason) or bool(provider_wait_reason):
        return 'waiting'
    return 'working'


def _workspace_progress_status(st, *, autopilot_enabled=True, wait_reason=None,
                               blocked_reason=None, order_status=None):
    """Stable progress mode used by the lightweight OPS mirror."""
    st=str(st or '').strip().lower()
    order_status=str(order_status or '').strip().lower()
    if st=='completed':
        return 'verified_done'
    if not autopilot_enabled:
        return 'paused'
    if order_status=='running':
        return 'running'
    if _provider_wait_text(blocked_reason) or _provider_wait_text(wait_reason):
        return 'waiting'
    if blocked_reason or st in ('blocked','failed') or order_status in ('blocked_infra','blocked_human'):
        return 'blocked'
    if wait_reason or st in ('waiting','queued','planning') or order_status=='queued':
        return 'waiting'
    if st in ('review','acceptance') or order_status=='review_requested':
        return 'review'
    if st in ('fixing','returned'):
        return 'fixing'
    if st in ('testing','regression'):
        return 'testing'
    if st=='running' or order_status=='running':
        return 'running'
    return 'unknown'


def _workspace_capacity_wait_reason(st, *, order_status=None, executor_capacity=None,
                                    review_capacity=None):
    """Expose scheduler back-pressure without pretending the job is executing."""
    st=str(st or '').strip().lower()
    order_status=str(order_status or '').strip().lower()
    if order_status=='running':
        return None
    if st in ('review','acceptance'):
        if review_capacity is not None and int(review_capacity) <= 0:
            return "Сервер временно занят: BORIS ждёт безопасный слот проверки и продолжит автоматически"
        return None
    if st in ('planning','running','testing','fixing'):
        if executor_capacity is not None and int(executor_capacity) <= 0:
            return "Сервер временно занят: BORIS ждёт безопасный слот исполнителя и продолжит автоматически"
    return None
def _utc_value(raw):
    if not raw:
        return None
    if isinstance(raw,datetime.datetime):
        value=raw
    else:
        try:
            value=datetime.datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        except Exception:
            return None
    if value.tzinfo is None:
        value=value.replace(tzinfo=datetime.timezone.utc)
    else:
        value=value.astimezone(datetime.timezone.utc)
    return value


def _provider_wait_text(value):
    low=str(value or "").strip().lower()
    return bool(low) and (
        "waiting_free_coding_provider" in low
        or low.startswith("provider_unavailable:")
        or "all free development providers unavailable" in low
        or "review provider quota" in low
        or "daily quota" in low
        or "rate_limited" in low
    )


def _workspace_provider_retry_at(provider_state):
    """Next truthful automatic free-provider action, UTC ISO.

    Provider retry_at is a conservative transport latch. The production watcher
    can schedule an earlier bounded health-probe (for example immediately after
    a known daily quota reset). Owner UI must show that actual next automatic
    action, not a later cooldown timestamp that makes BORIS look idle longer
    than it really is. Fall back to provider retry_at if watcher state is absent.
    """
    now=datetime.datetime.now(datetime.timezone.utc)
    try:
        watch=json.loads(_PROVIDER_WATCH_STATE.read_text(encoding="utf-8"))
    except Exception:
        watch={}
    watch_retry=_utc_value(
        watch.get("next_auto_retry_at") if isinstance(watch,dict) else None
    )
    if watch_retry and watch_retry>now:
        return watch_retry.isoformat()

    # Same durable reset marker used by the production watcher, but available
    # from provider state even when the watcher's protected state file is not
    # readable by a diagnostics process. The watcher probes reset+15s, so the
    # owner sees that real next action instead of aiprov's longer safety latch.
    gemini=(provider_state.get("gemini_cli") or {}) if isinstance(provider_state,dict) else {}
    note=str(gemini.get("note") or "")
    marker=re.search(r"reset_epoch=([0-9]{9,13})", note)
    if marker and "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED" in note:
        try:
            reset=datetime.datetime.fromtimestamp(
                float(marker.group(1)), datetime.timezone.utc
            ) + datetime.timedelta(seconds=15)
            if reset>now:
                return reset.isoformat()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=226)

    values=[]
    for name in ("gemini_cli","claude_code"):
        raw=(provider_state.get(name) or {}).get("retry_at") if isinstance(provider_state,dict) else None
        value=_utc_value(raw)
        if value and value>now:
            values.append(value)
    return min(values).isoformat() if values else None


def sync_ops_jobs(limit=500):
    jobs=db.rows("""SELECT j.id,j.title,j.status,j.current_phase,j.priority,j.wait_reason,j.blocked_reason,
                           j.phases_json,j.acceptance_json,j.qa_status,j.deployment_status,j.scope_json,j.updated_at,
                           o.status AS order_status,o.blocked_reason AS order_blocked_reason
                      FROM ext_dev_jobs j
                      LEFT JOIN LATERAL (
                          SELECT x.status,x.blocked_reason
                            FROM ext_a2a_orders x
                           WHERE x.dev_job_id=j.id
                           ORDER BY x.id DESC LIMIT 1
                      ) o ON TRUE
                     WHERE j.status<>'cancelled'
                     ORDER BY j.updated_at DESC LIMIT :lim""",lim=limit)
    # DEVELOPMENT_PROVIDER_WAIT_VISIBLE_V1: if the global free coding provider
    # is temporarily unavailable, review/running rows are not actually moving.
    # Show them as waiting in owner Workspace until dispatcher recovers a
    # provider; do not rewrite the underlying development lifecycle state.
    try:
        from . import aiprov, sched
        free_candidates=aiprov.development_candidates()
        provider_wait_reason=(
            aiprov.development_wait_reason()
            if not free_candidates else None
        )
        provider_retry_at=(
            _workspace_provider_retry_at(aiprov.state())
            if provider_wait_reason else None
        )
        caps=sched.limits()
        executor_capacity=caps.get("MAX_EXECUTOR_WORKERS")
        review_capacity=caps.get("MAX_REVIEW_WORKERS")
    except Exception:
        provider_wait_reason=None
        provider_retry_at=None
        executor_capacity=None
        review_capacity=None
    created=updated=unchanged=0
    for j in jobs:
        scope=_loads(j.get('scope_json'),{}) or {}
        # WORKSPACE_EXECUTION_SINGLE_THREAD_V1: a Job created from an existing
        # workspace thread must update that original thread. Creating an
        # additional ops-job:* mirror would show the owner the same task twice.
        if scope.get('source')=='workspace' and scope.get('workspace_thread_id'):
            unchanged+=1
            continue
        phases=_loads(j.get('phases_json'),{}) or {}; criteria=_loads(j.get('acceptance_json'),[]) or []
        pp=sum(1 for v in phases.values() if isinstance(v,dict) and v.get('state')=='pass')
        cd=sum(1 for v in criteria if isinstance(v,dict) and v.get('done'))
        st=str(j.get('status') or '')
        terminal=st in ('completed','cancelled')
        autopilot_enabled=scope.get('workspace_autopilot') is not False
        capacity_wait_reason=_workspace_capacity_wait_reason(
            st,
            order_status=j.get('order_status'),
            executor_capacity=executor_capacity,
            review_capacity=review_capacity,
        )
        effective_wait=(
            None if terminal else
            (j.get('wait_reason') or provider_wait_reason or capacity_wait_reason or None)
        )
        order_status=str(j.get('order_status') or '').strip().lower() or None
        runtime_status='running' if order_status=='running' else st
        if order_status=='running':
            # The live worker is execution truth. A stale parent/provider wait
            # must not make the owner UI say "waiting" while work is running.
            effective_wait=None
        # ORDER_BLOCKER_OWNER_TRUTH_V1: the newest A2A order is the execution
        # source of truth. Parent dev_job blockers may lag behind a scope/dedup/
        # provider transition, so owner UI must display the child blocker for an
        # actually blocked order instead of an older parent diagnosis.
        order_blocker=str(j.get('order_blocked_reason') or '').strip()
        effective_blocker=(
            None
            if terminal or order_status=='running'
            else (
                order_blocker
                if order_status in ('blocked_infra','blocked_human') and order_blocker
                else (j.get('blocked_reason') or None)
            )
        )
        provider_related=bool(
            (not terminal) and provider_retry_at and (
                (provider_wait_reason and effective_wait==provider_wait_reason)
                or _provider_wait_text(effective_wait)
                or _provider_wait_text(effective_blocker)
            )
        )
        autopilot_state=(
            _workspace_autopilot_runtime_state(
                st,
                autopilot_enabled=autopilot_enabled,
                wait_reason=None,
                blocked_reason=None,
                provider_wait_reason=None,
            )
            if terminal
            else (
                'working'
                if order_status=='running'
                else (
                    'waiting'
                    if provider_related
                    else _workspace_autopilot_runtime_state(
                        st,
                        autopilot_enabled=autopilot_enabled,
                        wait_reason=effective_wait,
                        blocked_reason=effective_blocker,
                        provider_wait_reason=None,
                    )
                )
            )
        )
        ws_status=(
            'done' if autopilot_state=='done' else
            ('blocked' if autopilot_state=='blocked' else
             ('waiting' if autopilot_state in ('waiting','paused') else 'active'))
        )
        state={'ops_job_id':j['id'],'dev_job_id':f"dev_{j['id']}",
               'executor_status':runtime_status,'dev_job_status':st,'order_status':order_status,
               'owner_action_required':order_status=='blocked_human',
               'current_phase':j.get('current_phase'),
               'executor_wait_reason':effective_wait,
               'provider_retry_at':provider_retry_at if provider_related else None,
               'executor_blocker':effective_blocker,
               'autopilot_enabled':autopilot_enabled,'autopilot_mode':str(scope.get('workspace_autopilot_mode') or 'until_done'),
               'autopilot_state':autopilot_state,
               'progress_status':_workspace_progress_status(
                   runtime_status,
                   autopilot_enabled=autopilot_enabled,
                   wait_reason=effective_wait,
                   blocked_reason=effective_blocker,
                   order_status=order_status,
               ),
               'progress_percent':100 if st=='completed' else None,
               'current_step':f"{j.get('current_phase') or 'QUEUE'} · {runtime_status}",
               'next_step':'Нет' if st=='completed' else (effective_blocker or effective_wait or 'Следующая фаза BORIS OPS'),
               'phase_passed':pp,'phase_total':len(phases),
               'ops_phase_passed':pp,'ops_phase_total':len(phases),'criteria_done':cd,'criteria_total':len(criteria),
               'qa_status':j.get('qa_status'),'deployment_status':j.get('deployment_status'),'memory_mode':'durable_workspace'}
        summary=f"BORIS OPS job #{j['id']} · фазы {pp}/{len(phases)} · критерии {cd}/{len(criteria)}"
        ref=f"ops-job:{j['id']}"; current=db.one("SELECT id,status,summary,current_state_json,source_json FROM control_workspace_threads WHERE external_chat_ref=:r ORDER BY created_at LIMIT 1",r=ref)
        state_s=json.dumps(state,ensure_ascii=False)
        if not current:
            tid=str(uuid.uuid4())
            db.q("""INSERT INTO control_workspace_threads
                    (id,thread_type,title,external_chat_ref,status,summary,current_state_json,plan_json,source_json,last_event_at)
                    VALUES (:id,'task',:t,:r,:s,:sm,CAST(:st AS jsonb),'[]'::jsonb,CAST(:src AS jsonb),COALESCE(:at,NOW()))""",
                 id=tid,t=str(j['title'])[:500],r=ref,s=ws_status,sm=summary,st=state_s,
                 src=json.dumps({'surface':'boris_ops','ops_job_id':j['id'],'priority':j.get('priority')},ensure_ascii=False),at=j.get('updated_at'))
            db.q("""INSERT INTO control_workspace_events(thread_id,event_type,actor,text,payload_json)
                    VALUES (:id,'ops_synced','boris_ops',:txt,CAST(:pl AS jsonb))""",id=tid,txt=summary,pl=json.dumps({'ops_job_id':j['id'],'status':st,'phase':j.get('current_phase')},ensure_ascii=False))
            created+=1
        else:
            old_state=current.get('current_state_json') or {}
            old_source=current.get('source_json') or {}
            # WORKSPACE_SCOPE_DEDUPE_STICKY_V2: delegation metadata is durable in
            # source_json because runtime workspace refreshers legitimately
            # rebuild current_state_json. Periodic OPS mirroring must honor either
            # location and must also keep the owner-visible mirror cancelled.
            delegated = (
                (isinstance(old_state, dict) and (old_state.get('deduped_into') or old_state.get('duplicate_execution_disabled')))
                or (isinstance(old_source, dict) and (old_source.get('canonical_owner_thread_id') or old_source.get('canonical_money_owner') or old_source.get('duplicate_execution_disabled')))
            )
            if delegated:
                if current.get('status') != 'cancelled':
                    db.q("UPDATE control_workspace_threads SET status='cancelled',updated_at=NOW() WHERE id=:id", id=current['id'])
                    updated+=1
                else:
                    unchanged+=1
                continue
            if current.get('status')!=ws_status or current.get('summary')!=summary or old_state!=state:
                db.q("""UPDATE control_workspace_threads SET status=:s,summary=:sm,current_state_json=CAST(:st AS jsonb),
                         updated_at=NOW(),last_event_at=COALESCE(:at,NOW()) WHERE id=:id""",
                     s=ws_status,sm=summary,st=state_s,at=j.get('updated_at'),id=current['id'])
                updated+=1
            else: unchanged+=1

    # OWNER_CANCELLED_OPS_MIRROR_SELF_HEAL_V1: cancelled canonical dev jobs are
    # intentionally excluded from the normal mirror query above. Existing owner
    # mirrors must still stop looking active/waiting/blocked after cancellation.
    # Never create a mirror for a cancelled job and never rewrite done history.
    cancelled_mirrors=db.rows("""SELECT t.id
                                  FROM control_workspace_threads t
                                  JOIN ext_dev_jobs j
                                    ON t.external_chat_ref=('ops-job:'||j.id::text)
                                 WHERE j.status='cancelled'
                                   AND t.status IN ('active','waiting','blocked')
                                 ORDER BY t.updated_at DESC
                                 LIMIT :lim""",lim=limit)
    for mirror in cancelled_mirrors:
        db.q("UPDATE control_workspace_threads SET status='cancelled',updated_at=NOW() WHERE id=:id",id=mirror['id'])
        updated+=1
    return {'jobs':len(jobs),'created':created,'updated':updated,'unchanged':unchanged,
            'cancelled_mirrors':len(cancelled_mirrors)}



def reconcile_workspace_scope_duplicates(limit=2000):
    """Ownerless cleanup for historical execution-card duplication.

    WORKSPACE_SCOPE_DEDUPE_SELF_HEAL_V1: ControlExecution history belongs to
    runtime telemetry, not to the owner task queue. If an old code path writes
    execution:<id> again, compact it into the stable execution-scope row. This
    is DB-only and never executes a business/provider action.
    """
    from datetime import datetime, timezone
    from app.db.session import SessionLocal
    from app.models.control_plane import ControlWorkspaceThread, ControlWorkspaceEvent

    s=SessionLocal(); scanned=runtime_retyped=compacted=errors=0
    try:
        rows=(s.query(ControlWorkspaceThread)
              .order_by(ControlWorkspaceThread.updated_at.desc())
              .limit(int(limit)).all())
        for row in rows:
            src=dict(row.source_json or {})
            source_kind=str(src.get("source") or "")
            if source_kind not in {"control_execution","legacy_task"}:
                continue
            scanned+=1
            try:
                if row.thread_type!="runtime":
                    row.thread_type="runtime"; runtime_retyped+=1
                # Legacy BackgroundJob Task is runtime telemetry too, but has no
                # execution-scope compaction contract. Retype it and preserve history.
                if source_kind=="legacy_task":
                    continue
                ref=str(row.external_chat_ref or "")
                if not ref.startswith("execution:") or row.status not in {"active","waiting","blocked"}:
                    continue
                req=src.get("requirement_id") or "unknown"; acc=row.account_id or "__global__"
                stable_ref=f"execution-scope:{req}:{acc}"
                stable=(s.query(ControlWorkspaceThread)
                        .filter(ControlWorkspaceThread.external_chat_ref==stable_ref).first())
                if not stable:
                    continue
                state=dict(row.current_state_json or {})
                state.update({"archived_historical_execution":True,"execution_disabled":True,
                              "merged_into_thread_id":str(stable.id),"merged_into_ref":stable_ref,
                              "next_step":"Нет — состояние ведётся в execution-scope"})
                src.update({"merged_duplicate":True,"execution_disabled":True,
                            "merged_into_thread_id":str(stable.id),"merged_into_ref":stable_ref})
                row.status="done"; row.summary="Исторический Control Plane execution; автоматически объединён с execution-scope"
                row.current_state_json=state; row.source_json=src; row.last_event_at=datetime.now(timezone.utc)
                s.add(ControlWorkspaceEvent(thread_id=row.id,event_type="runtime_compacted",actor="boris_brain",
                    text="Повторная execution-карточка автоматически свернута в стабильный execution-scope.",
                    payload_json={"merged_into_thread_id":str(stable.id),"merged_into_ref":stable_ref}))
                compacted+=1
            except Exception:
                errors+=1; s.rollback()
        s.commit()
        return {"scanned":scanned,"runtime_retyped":runtime_retyped,"compacted":compacted,"errors":errors}
    finally:
        s.close()


def _explicit_development_autostart_candidate(row):
    """Only explicitly owner-opted-in development rows may self-start.

    This intentionally excludes recovered history and generic legacy tasks.
    The flag is written by the Development UI at thread creation time, so a
    browser/network interruption between "create thread" and the first prompt
    cannot leave the owner as the manual start button.
    """
    state=dict(row.current_state_json or {})
    source=dict(row.source_json or {})
    return bool(
        row.thread_type in ("task","project","chat")
        and row.status in ("active","waiting","blocked")
        and str(source.get("surface") or "") == "development"
        and source.get("owner_only") is True
        and source.get("autopilot") is True
        and state.get("autopilot_enabled") is not False
        and not state.get("dev_job_id")
        and not state.get("ops_job_id")
        and state.get("executor_status") != "enqueue_failed"
    )


def _self_start_explicit_development_thread(db, row):
    """Create the missing durable executor job for one explicit autopilot row.

    Exactly-once is provided by _workspace_enqueue_executor's deterministic
    request_id. This helper performs no coding-provider call: when the free
    provider is unavailable, the created job/order truthfully enters WAITING
    and the dispatcher resumes it later.
    """
    if not _explicit_development_autostart_candidate(row):
        return {"eligible":False,"started":False,"failed":False}
    from datetime import datetime, timezone
    from app.api.control_plane import _workspace_enqueue_executor
    from app.models.control_plane import ControlWorkspaceEvent
    state=dict(row.current_state_json or {})
    command_id=str(state.get("last_command_id") or f"background-autostart-{row.id}")
    command=str(state.get("last_command_text") or row.summary or row.title or "").strip()
    if not command:
        return {"eligible":True,"started":False,"failed":False,"reason":"empty_command"}
    try:
        execution=_workspace_enqueue_executor(
            row, command_id=command_id, text_value=command
        )
        state.update(execution)
        state["last_command_id"]=command_id
        state["last_command_text"]=command[:6000]
        state["autopilot_enabled"]=True
        state["autopilot_mode"]="until_done"
        state["autopilot_state"]="working"
        state["execution_enqueue_error"]=None
        state["execution_enqueue_attempts"]=max(
            1, int(state.get("execution_enqueue_attempts") or 0)
        )
        row.current_state_json=state
        row.status="active"
        row.last_event_at=datetime.now(timezone.utc)
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,
            event_type="autopilot_self_started",
            actor="boris",
            text=(
                "BORIS сам восстановил пропущенный старт автопилота; "
                f"создана исполняемая работа {execution.get('dev_job_id')}."
            ),
            payload_json={
                "reason":"explicit_development_autopilot_orphan",
                "owner_action_required":False,
                "execution":execution,
            },
        ))
        return {
            "eligible":True,"started":True,"failed":False,
            "dev_job_id":execution.get("dev_job_id"),
            "order_id":execution.get("order_id"),
        }
    except Exception as exc:
        # Persist the same recovery state used by the existing bounded
        # enqueue-failure retry path. Subsequent dispatcher ticks retry the
        # deterministic command at most three times; no duplicate job is made.
        state["last_command_id"]=command_id
        state["last_command_text"]=command[:6000]
        state["autopilot_enabled"]=True
        state["autopilot_mode"]="until_done"
        state["autopilot_state"]="waiting"
        state["executor_status"]="enqueue_failed"
        state["execution_enqueue_error"]=type(exc).__name__
        state["execution_enqueue_attempts"]=max(
            1, int(state.get("execution_enqueue_attempts") or 0) + 1
        )
        row.current_state_json=state
        row.status="waiting"
        row.last_event_at=datetime.now(timezone.utc)
        db.add(ControlWorkspaceEvent(
            thread_id=row.id,
            event_type="autopilot_self_start_failed",
            actor="boris",
            text=(
                "BORIS обнаружил пропущенный старт автопилота и поставил "
                "его на ограниченное автоматическое восстановление."
            ),
            payload_json={
                "reason":"explicit_development_autopilot_orphan",
                "owner_action_required":False,
                "error_type":type(exc).__name__,
                "command_id":command_id,
            },
        ))
        return {
            "eligible":True,"started":False,"failed":True,
            "error_type":type(exc).__name__,
        }


def reconcile_workspace_executions(limit=300):
    """Background reconcile for owner workspace commands.

    Runs from boris-dispatcher, so execution status/self-heal does not depend on
    the owner opening /journal or /dashboard/command-center. Explicit
    development-autopilot rows also self-start if the browser disappeared
    between creating the row and enqueuing its first command.
    """
    from app.db.session import SessionLocal
    from app.models.control_plane import ControlWorkspaceThread
    from app.api.control_plane import _workspace_refresh_executor_state

    s=SessionLocal()
    scanned=changed=errors=0
    autostart_eligible=autostarted=autostart_failed=0
    try:
        rows=(s.query(ControlWorkspaceThread)
              .filter(ControlWorkspaceThread.status.in_(("active","waiting","blocked")))
              .order_by(ControlWorkspaceThread.last_event_at.desc())
              .limit(int(limit)).all())
        for row in rows:
            state=dict(row.current_state_json or {})
            candidate=_explicit_development_autostart_candidate(row)
            if not (
                candidate
                or state.get("dev_job_id")
                or state.get("executor_status")=="enqueue_failed"
            ):
                continue
            scanned+=1
            if candidate:
                autostart_eligible+=1
                try:
                    start=_self_start_explicit_development_thread(s,row)
                except Exception:
                    errors+=1
                    s.rollback()
                    continue
                if start.get("started"):
                    autostarted+=1
                    changed+=1
                if start.get("failed"):
                    autostart_failed+=1
                    changed+=1
                    # Do not burn all bounded enqueue retries in one dispatcher
                    # tick. The ordinary enqueue_failed path owns later retries.
                    continue
                state=dict(row.current_state_json or {})
            if not (
                state.get("dev_job_id")
                or state.get("executor_status")=="enqueue_failed"
            ):
                continue
            try:
                out=_workspace_refresh_executor_state(s,row,emit_event=True)
                if out.get("changed"):
                    changed+=1
            except Exception:
                errors+=1
                s.rollback()
                continue
        s.commit()
        return {
            "scanned":scanned,
            "changed":changed,
            "errors":errors,
            "autostart_eligible":autostart_eligible,
            "autostarted":autostarted,
            "autostart_failed":autostart_failed,
        }
    finally:
        s.close()


def sync_client_action_plans(limit=200):
    """Persist factual per-client Brain plan into each active paid client workspace.

    Read/diagnose only: no external action, spend or AI call. A state transition
    creates one evidence event; unchanged plans stay quiet.
    """
    import hashlib
    from app.db.session import SessionLocal
    from app.services.brain_action_planner import build_action_plan
    from sqlalchemy import text
    s=SessionLocal(); updated=unchanged=errors=0
    try:
        # CLIENT_PLAN_CANONICAL_ACTIVE_SCOPE_V2: slot/billing alone is not the
        # client activity contract. A client may have an expired inbox/marketing
        # slot while another purchased module (for example MOP) is still paid.
        # Reuse Client Supervisor's paid-period/entitlement projection so the
        # owner window cannot silently drop an active client. Credentials and
        # reliability kill-switches remain independent safety gates.
        from app.services.client_supervisor import (
            build_client_state, candidate_accounts,
            load_supervisor_snapshot, save_supervisor_snapshot,
        )
        active=[]; client_states={}; snapshot_hits=0; snapshots_written=0
        for candidate in candidate_accounts(s):
            if len(active)>=int(limit): break
            aid=str(candidate)
            try:
                cstate=load_supervisor_snapshot(s,aid)
                if cstate is None:
                    cstate=build_client_state(s,aid)
                    save_supervisor_snapshot(s,cstate)
                    s.commit()
                    snapshots_written+=1
                else:
                    snapshot_hits+=1
            except Exception:
                s.rollback(); errors+=1; continue
            eligible=s.execute(text("""SELECT 1 FROM accounts a WHERE a.account_id=:a
              AND a.avito_client_id IS NOT NULL AND a.avito_client_secret IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM reliability_kill_switches r WHERE r.account_id=a.account_id
                AND r.blocked=true AND r.module IN ('actions','background','reports'))
              AND lower(a.account_id) NOT LIKE 'qa%%' AND lower(COALESCE(a.name,'')) NOT LIKE 'qa%%'"""),{'a':aid}).first()
            if not eligible: continue
            if str(cstate.get('client_state') or '')!='active': continue
            active.append(aid); client_states[aid]=cstate
        for aid in active:
            try:
                plan=build_action_plan(s,str(aid)); queue=plan.get('queue') or {}
                # CLIENT_PLAN_EXPECTED_MODULE_SCOPE_V1: the owner client window
                # must not resurrect historical work for a module that this
                # client did not purchase/enable. Supervisor paid-period and
                # entitlement evidence is the canonical expected-module scope.
                client_state=client_states.get(str(aid)) or build_client_state(s,str(aid))
                expected_modules=set(client_state.get('expected_modules') or [])
                compact={k:[
                    {'module':x.get('module'),'classification':x.get('classification'),'action':x.get('action'),
                     'owner_action_required':bool(x.get('owner_action_required')),'evidence_for':x.get('evidence_for') or []}
                    for x in (queue.get(k) or []) if str(x.get('module') or '') in expected_modules
                ] for k in ('NOW','NEXT','WAITING','BLOCKED')}
                digest=hashlib.sha256(json.dumps(compact,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
                row=s.execute(text("SELECT id,current_state_json FROM control_workspace_threads WHERE thread_type='client' AND account_id=:a ORDER BY updated_at DESC LIMIT 1"),{'a':aid}).mappings().first()
                if not row: continue
                state=dict(row.get('current_state_json') or {})
                if state.get('brain_plan_hash')==digest:
                    unchanged+=1; continue
                state.update({'brain_plan_hash':digest,'brain_status':plan.get('status'),'brain_action_plan':compact,'brain_checked_at':plan.get('checked_at'),'brain_plan_source':'deterministic_control_plane'})
                s.execute(text("UPDATE control_workspace_threads SET current_state_json=CAST(:st AS jsonb),updated_at=now(),last_event_at=now() WHERE id=:id"),{'st':json.dumps(state,ensure_ascii=False,default=str),'id':row['id']})
                counts={k:len(v) for k,v in compact.items()}
                s.execute(text("INSERT INTO control_workspace_events(thread_id,event_type,actor,text,payload_json) VALUES(:id,'client_plan_sync','boris_brain',:txt,CAST(:pl AS jsonb))"),{'id':row['id'],'txt':f"Живой план BORIS: NOW {counts['NOW']} · NEXT {counts['NEXT']} · WAITING {counts['WAITING']} · BLOCKED {counts['BLOCKED']}",'pl':json.dumps({'account_id':aid,'counts':counts,'status':plan.get('status'),'plan_hash':digest},ensure_ascii=False)})
                s.commit(); updated+=1
            except Exception:
                s.rollback(); errors+=1
        return {'active_clients':len(active),'updated':updated,'unchanged':unchanged,'errors':errors,
                'supervisor_snapshots':{'fresh_hits':snapshot_hits,'written':snapshots_written}}
    finally: s.close()
