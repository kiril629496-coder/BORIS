"""Deterministic BORIS Brain action planner.

Transforms factual module diagnosis into an explicit owner-safe plan. It does not
execute money/external/high-risk actions. Plans are derived from adapter evidence
and are suitable for NOW/NEXT/WAITING/BLOCKED owner cockpit rendering.
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone, timedelta
import json
import subprocess
from sqlalchemy import text

from app.services.brain_world_state import build_world_state
from app.models.control_plane import ControlIncident
from app.models.reliability import ReliabilityHeartbeat
from app.models.storage import Storage


ACTION_META = {
    # Observe/diagnose-only actions are safe NOW work. They must never be
    # classified as owner-policy blockers merely because no mutating executor
    # is registered.
    "retry_failed_drafts": {"level":"L2","reversible":True,"requires_owner_policy":True,"executor":"mop"},
    "inspect_mop_queue": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"mop"},
    "resume_when_provider_available": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"mop","external_dependency":True},
    "ensure_manager_handoff_task": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"crm"},
    "dispatch_to_mop": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"messages"},
    "repair_mop_binding": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"messages"},
    "repair_crm_relationships": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"crm"},
    "create_or_repair_next_action": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"crm"},
    "surface_overdue_tasks": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"crm"},
    "run_calltracking_crm_sync": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"telephony"},
    "ensure_callback_task": {"level":"L2","reversible":True,"requires_owner_policy":True,"executor":"telephony"},
    "retry_analysis_pipeline": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"telephony"},
    "run_missing_call_audits": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"rop"},
    "restart_rop_auto_scheduler": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"rop"},
    "refresh_or_reconnect_direct": {"level":"L3","reversible":True,"requires_owner_policy":True,"executor":"direct","external_dependency":True},
    "sync_direct_state": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"direct"},
    "run_marketing_diagnosis": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"marketing"},
    "observe_existing_plan": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"marketing"},
    "inspect_feed_failures": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"feed_factory"},
    "recover_stale_feed_jobs": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"feed_factory"},
    "run_stats_collection": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"analytics"},
    "restart_reactivation_scheduler": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"reactivation"},
    "recover_reactivation_transport": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"reactivation"},
    "inspect_reactivation_errors": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"reactivation"},
    "reconcile_reactivation_backlog": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"reactivation"},
    "run_email_queue_recovery": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"email"},
    "inspect_email_failures": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"email"},
    "retry_reply_alerts": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"telegram"},
    "restore_sitebuild_storage": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"sitebuild"},
    "restart_social_scheduler": {"level":"L1","reversible":True,"requires_owner_policy":False,"executor":"social"},
    "inspect_social_contract_blockers": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"social"},
    # SOCIAL_DELIVERY_SLA_AUTONOMOUS_INSPECTION_V1: read/diagnose + bounded
    # scheduler self-heal is already executed by the five-minute System Brain
    # cycle. It must not send the owner into WAITING merely because this safe
    # diagnostic action was missing from the planner registry.
    "inspect_social_delivery_sla": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"social"},
    "continue_account_scoped_kpi_recovery": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"marketing"},
    "surface_owner_policy_and_continue_nonmoney_recovery": {"level":"L0","reversible":True,"requires_owner_policy":False,"executor":"marketing"},
    "owner_policy_required": {"level":"L3","reversible":True,"requires_owner_policy":True,"executor":"marketing"},
}


def _steps(classification: str, action: str | None, module: str) -> list[dict[str, Any]]:
    common=[
        {"name":"read_business_state","kind":"observe","status":"ready","verification":"adapter readback"},
        {"name":"confirm_diagnosis","kind":"diagnose","status":"ready","verification":"deterministic evidence"},
    ]
    if not action:
        return common + [{"name":"no_action_required","kind":"done","status":"done","verification":"business state healthy"}]
    meta=dict(ACTION_META.get(action) or {"level":"L0","reversible":True,"requires_owner_policy":True,"executor":module})
    common.append({"name":action,"kind":"act","status":"blocked_policy" if meta.get("requires_owner_policy") else "ready","meta":meta,"verification":"domain post-condition readback"})
    common.append({"name":"runtime_verify","kind":"verify","status":"waiting","verification":"NO EVIDENCE = NO PASS"})
    common.append({"name":"reassess","kind":"recover","status":"waiting","verification":"change strategy if post-condition still fails"})
    return common


AUTO_CYCLE_MINUTES = 5
AUTO_RECOVERY_MODULES = {
    "marketing","crm","mop","feed_factory","email","sitebuild","social","reactivation","rop"
}

def _compact_recovery_result(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    raw=dict(item.get("result") or {})
    facts={}
    for key in (
        "changed","reminded","created","updated","restarted","surfaced",
        "delivery_unknown_recovered","answered_externally","human_required",
        "stale_sending_recovered","receipts_backfilled","delivery_reconciled",
    ):
        if key in raw:
            facts[key]=raw.get(key)
    return {
        "action":item.get("action"),
        "passed":bool(item.get("passed")),
        "dependency_state":item.get("dependency_state"),
        "verification":item.get("verification"),
        "facts":facts,
    }

def _systemd_timer_next_run(timer_name: str) -> datetime | None:
    """Read the exact next systemd timer fire time without mutating runtime."""
    try:
        out=subprocess.check_output(
            ["/usr/bin/systemctl","list-timers",timer_name,"--all","--no-legend","--no-pager"],
            text=True,timeout=3,
        )
        line=next((x.strip() for x in out.splitlines() if x.strip()),"")
        parts=line.split()
        if len(parts)>=4 and parts[1]!="-" and parts[2]!="-" and parts[3]=="UTC":
            return datetime.fromisoformat(f"{parts[1]}T{parts[2]}+00:00")
    except Exception:
        return None
    return None


def _systemd_timer_last_trigger(timer_name: str) -> datetime | None:
    """Read the last real trigger time. Used only when systemd has not assigned the next fire yet."""
    try:
        raw=subprocess.check_output(
            ["/usr/bin/systemctl","show",timer_name,"-p","LastTriggerUSec","--value"],
            text=True,timeout=3,
        ).strip()
        if not raw or raw in {"n/a","-"}:
            return None
        return datetime.strptime(raw,"%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _systemd_timer_service_running(timer_name: str) -> bool:
    service_name=(str(timer_name)[:-6]+".service") if str(timer_name).endswith(".timer") else str(timer_name)
    try:
        p=subprocess.run(
            ["/usr/bin/systemctl","is-active","--quiet",service_name],
            timeout=3,check=False,
        )
        return p.returncode==0
    except Exception:
        return False


def _guardian_execution_schedule(db) -> dict[str, Any]:
    """Actual five-minute System Brain schedule + last completed recovery proof."""
    now=datetime.now(timezone.utc)
    hb=(db.query(ReliabilityHeartbeat)
        .filter(ReliabilityHeartbeat.module=="control_plane", ReliabilityHeartbeat.worker_id=="guardian")
        .order_by(ReliabilityHeartbeat.last_seen_at.desc()).first())
    details=dict(getattr(hb,"details_json",None) or {}) if hb else {}
    seen=getattr(hb,"last_seen_at",None) if hb else None
    if seen is not None and seen.tzinfo is None:
        seen=seen.replace(tzinfo=timezone.utc)
    interval=timedelta(minutes=AUTO_CYCLE_MINUTES)
    next_at=_systemd_timer_next_run("boris-system-brain.timer")
    if next_at is None:
        base=seen or now
        next_at=base+interval
        while next_at <= now:
            next_at += interval
    safe=dict(details.get("safe_recovery") or {})
    recovery_actions=list(safe.get("actions") or [])
    last_by_module={}
    for action in recovery_actions:
        module=str(action.get("module") or "")
        if module and module not in last_by_module:
            last_by_module[module]=_compact_recovery_result(action)
    return {
        "scheduler":"boris-system-brain.timer",
        "every_minutes":AUTO_CYCLE_MINUTES,
        "state":str(getattr(hb,"state","unknown") or "unknown") if hb else "missing",
        "last_cycle_at":seen.isoformat() if seen else None,
        "last_cycle_completed":bool(safe),
        "last_cycle_status":safe.get("status") or details.get("status") or getattr(hb,"state",None),
        "next_run_at":next_at.isoformat(),
        "next_runs":[(next_at+(interval*i)).isoformat() for i in range(12)],
        "last_safe_results":last_by_module,
        "proof_source":"reliability_heartbeats:control_plane/guardian",
    }

def _parse_iso(value):
    try:
        dt=datetime.fromisoformat(str(value or "").replace("Z","+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def _marketing_execution_schedule(db, account_id: str | None) -> dict[str, Any]:
    """Truthful execution schedule for the hourly AI-marketer domain worker."""
    now=datetime.now(timezone.utc)
    next_at=_systemd_timer_next_run("boris-ai-marketer.timer")
    if next_at is None:
        next_at=now.replace(minute=5,second=0,microsecond=0)
        if next_at <= now:
            next_at += timedelta(hours=1)
    runtime={}
    if account_id:
        row=(db.query(Storage)
             .filter(Storage.account_id==str(account_id),Storage.key=="virtual_marketer_runtime")
             .order_by(Storage.id.desc()).first())
        if row:
            try: runtime=json.loads(row.value or "{}")
            except Exception: runtime={}
    last_at=_parse_iso(runtime.get("finished_at") or runtime.get("last_run_at"))
    status=str(runtime.get("status") or "")
    waiting_owner=bool(runtime.get("owner_action_required")) or status=="blocked"
    facts={
        "planned":int(runtime.get("planned") or 0),
        "applied":int(runtime.get("applied") or 0),
        "failed":int(runtime.get("failed") or 0),
    }
    if runtime.get("money_block_reason"):
        facts["money_block_reason"]=runtime.get("money_block_reason")
    if runtime.get("owner_action"):
        facts["owner_action"]=runtime.get("owner_action")
    last_result=None
    if last_at:
        last_result={
            "action":"hourly_ai_marketer_cycle",
            "passed":int(runtime.get("failed") or 0)==0,
            "dependency_state":"waiting_owner" if waiting_owner else None,
            "verification":"virtual_marketer_runtime persisted after canonical hourly cycle",
            "facts":facts,
        }
    return {
        "scheduler":"boris-ai-marketer.timer",
        "every_minutes":60,
        "state":status or ("missing" if not last_at else "unknown"),
        "last_cycle_at":last_at.isoformat() if last_at else None,
        "last_cycle_completed":bool(last_at),
        "last_cycle_status":status or None,
        "next_run_at":next_at.isoformat(),
        "next_runs":[(next_at+timedelta(hours=i)).isoformat() for i in range(6)],
        "last_module_result":last_result,
        "proof_source":"storage:virtual_marketer_runtime",
    }


def _reactivation_execution_schedule(db, account_id: str | None) -> dict[str, Any]:
    """Account-scoped proof + exact next safe recheck for Avito reactivation."""
    payloads=[]
    if account_id:
        for key in ("reactivation_inbox_capability_v1","reactivation_avito_capability_v1"):
            row=(db.query(Storage)
                 .filter(Storage.account_id==str(account_id),Storage.key==key)
                 .order_by(Storage.id.desc()).first())
            if not row:
                continue
            try:
                payload=json.loads(row.value or "{}")
            except Exception:
                payload={}
            checked=_parse_iso(payload.get("checked_at"))
            payloads.append((checked or datetime.min.replace(tzinfo=timezone.utc),key,payload))
    payloads.sort(key=lambda x:x[0],reverse=True)
    checked_at=None; source_key=None; capability={}
    if payloads:
        checked_at,source_key,capability=payloads[0]
        if checked_at.year<=1:
            checked_at=None
    classification=str(capability.get("classification") or "")
    state=str(capability.get("state") or "")
    if classification=="avito_messenger_history_partial_402":
        dependency_state="partial_external"
    elif classification=="avito_messenger_subscription_required" or state=="waiting_external":
        dependency_state="waiting_external"
    else:
        dependency_state=None
    facts={}
    for key in ("synced","transport_errors","chat_count","history_probe_failures"):
        if capability.get(key) is not None:
            facts[key]=capability.get(key)
    if classification:
        facts["classification"]=classification
    next_at=_systemd_timer_next_run("boris-reactivation.timer")
    if next_at is None and checked_at is not None:
        next_at=checked_at+timedelta(minutes=15)
    last_result=None
    if checked_at is not None:
        last_result={
            "action":"reactivation_access_recheck",
            "passed":True,
            "dependency_state":dependency_state,
            "verification":"account-scoped Avito capability probe persisted after read-only recheck",
            "facts":facts,
        }
    return {
        "scheduler":"boris-reactivation.timer",
        "every_minutes":15,
        "state":state or ("missing" if checked_at is None else "unknown"),
        "last_cycle_at":checked_at.isoformat() if checked_at else None,
        "last_cycle_completed":bool(checked_at),
        "last_cycle_status":classification or state or None,
        "next_run_at":next_at.isoformat() if next_at else None,
        # The timer has RandomizedDelaySec, therefore only the nearest fire time
        # is exact. Later timestamps must be recalculated after the next trigger.
        "next_runs":[next_at.isoformat()] if next_at else [],
        "last_module_result":last_result,
        "proof_source":f"storage:{source_key}" if source_key else "storage:reactivation_capability",
    }


def _mop_execution_schedule(db, account_id: str | None) -> dict[str, Any]:
    """Exact scheduler evidence for MOP external-wait retries."""
    hb=(db.query(ReliabilityHeartbeat)
        .filter(ReliabilityHeartbeat.module=="messenger",ReliabilityHeartbeat.worker_id=="poller")
        .order_by(ReliabilityHeartbeat.last_seen_at.desc()).first())
    details=dict(getattr(hb,"details_json",None) or {}) if hb else {}
    seen=getattr(hb,"last_seen_at",None) if hb else None
    if seen is not None and seen.tzinfo is None:
        seen=seen.replace(tzinfo=timezone.utc)
    last_at=_parse_iso(details.get("provider_wait_recovery_last_at")) or seen
    next_at=_parse_iso(details.get("provider_wait_recovery_next_at"))
    waiting_external=0
    provider_waiting=0
    if account_id:
        waiting_external=int(db.execute(text(
            "SELECT count(*) FROM mop_drafts WHERE account_id=:a AND status='waiting_external'"
        ),{"a":str(account_id)}).scalar() or 0)
        provider_waiting=int(db.execute(text("""
          SELECT count(*) FROM mop_drafts d
           WHERE d.account_id=:a AND d.status='waiting_external'
             AND EXISTS (
               SELECT 1 FROM mop_draft_events e
                WHERE e.draft_id=d.id AND e.event='provider_deferred'
                  AND (
                    coalesce(e.metadata->>'reason','') IN ('ai_provider_or_budget_deferred','local_provider_busy_timeout')
                    OR lower(coalesce(e.payload,'')) LIKE '%лимит ai%'
                    OR lower(coalesce(e.payload,'')) LIKE '%provider temporarily deferred%'
                    OR lower(coalesce(e.payload,'')) LIKE '%mop_provider_deferred_wait_v1%'
                  )
             )
        """),{"a":str(account_id)}).scalar() or 0)
    last_result=None
    if last_at:
        recovery=dict(details.get("provider_wait_recovery") or {})
        per_account=dict((recovery.get("accounts") or {}).get(str(account_id or "")) or {})
        facts={"waiting_external":waiting_external,"provider_waiting":provider_waiting}
        for key in ("checked","requeued","cooldown","answered_externally","superseded","stale_handoff","no_message_evidence"):
            if per_account.get(key) is not None:
                facts[key]=per_account.get(key)
        last_result={
            "action":"mop_external_wait_recheck",
            "passed":True,
            "dependency_state":"waiting_external" if waiting_external else None,
            "verification":"messenger poller persisted provider-wait recovery heartbeat; account queue re-read from mop_drafts",
            "facts":facts,
        }
    return {
        "scheduler":"messenger/poller provider-wait recovery",
        "every_minutes":2,
        "state":str(getattr(hb,"state","missing") or "missing") if hb else "missing",
        "last_cycle_at":last_at.isoformat() if last_at else None,
        "last_cycle_completed":bool(last_at),
        "last_cycle_status":str(getattr(hb,"state",None) or "unknown") if hb else "missing",
        "next_run_at":next_at.isoformat() if next_at else None,
        "next_runs":[next_at.isoformat()] if next_at else [],
        "last_module_result":last_result,
        "proof_source":"reliability_heartbeats:messenger/poller + mop_drafts",
    }


def _attach_execution(db, item: dict[str, Any], monitor_schedule: dict[str, Any]) -> None:
    module=str(item.get("module") or "")
    lane=str(item.get("lane") or "")
    owner=bool(item.get("owner_action_required"))
    auto=bool(lane=="NOW" and not owner and module in AUTO_RECOVERY_MODULES)
    auto_recheck=bool(module in {"reactivation","mop"} and lane=="BLOCKED" and not owner)
    if module=="marketing" and auto:
        domain_schedule=_marketing_execution_schedule(db,item.get("account_id"))
    elif module=="reactivation" and auto_recheck:
        domain_schedule=_reactivation_execution_schedule(db,item.get("account_id"))
    elif module=="mop" and auto_recheck:
        domain_schedule=_mop_execution_schedule(db,item.get("account_id"))
    else:
        domain_schedule=monitor_schedule
    has_domain_clock=bool(auto or auto_recheck)
    item["execution"]={
        "auto_execute":auto,
        "auto_recheck":auto_recheck,
        "monitor_every_minutes":AUTO_CYCLE_MINUTES,
        "monitor_scheduler":monitor_schedule.get("scheduler"),
        "monitor_planned_for":monitor_schedule.get("next_run_at"),
        "monitor_next_runs":monitor_schedule.get("next_runs") or [],
        "execute_every_minutes":domain_schedule.get("every_minutes") if has_domain_clock else None,
        "planned_for":domain_schedule.get("next_run_at") if has_domain_clock else None,
        "next_runs":domain_schedule.get("next_runs") if has_domain_clock else [],
        "scheduler":domain_schedule.get("scheduler"),
        "last_cycle_at":domain_schedule.get("last_cycle_at"),
        "last_cycle_status":domain_schedule.get("last_cycle_status"),
        "last_module_result":(
            domain_schedule.get("last_module_result")
            if (module=="marketing" and auto) or auto_recheck
            else (monitor_schedule.get("last_safe_results") or {}).get(module)
        ),
        "verification_after_action":True,
        "owner_required":owner,
        "proof_source":domain_schedule.get("proof_source"),
    }

ROUTINE_TASK_TEXT = {
    "mop":"Проверяет новые сообщения клиента, готовит безопасный ответ и передаёт менеджеру только то, что действительно требует человека.",
    "messages":"Синхронизирует новые сообщения и проверяет, что входящие не остались без обработки.",
    "crm":"Проверяет сделки и просроченные следующие шаги; создаёт или восстанавливает безопасные задачи менеджеру.",
    "marketing":"Сверяет KPI, лиды, расход и цену лида; выполняет разрешённые улучшения и не тратит деньги без политики владельца.",
    "reactivation":"Проверяет очередь старых диалогов и доступ Avito; продолжает реактивацию, когда внешний доступ позволяет.",
    "rop":"Проверяет работу менеджеров, звонки, задачи и отклонения от плана.",
    "social":"Проверяет очередь публикаций и доставку постов; автоматически поднимает безопасное восстановление при сбое.",
    "email":"Проверяет очередь email-рассылки, ошибки отправки и повторно запускает безопасные доставки.",
    "analytics":"Обновляет фактическую статистику, лиды, расходы и показатели для контроля KPI.",
    "telephony":"Синхронизирует звонки и контроль следующих действий после звонка.",
    "feed_factory":"Проверяет очереди подготовки/публикации объявлений и восстанавливает зависшие безопасные задачи.",
    "prospecting":"Ищет свежий спрос на разработку, убирает дубли, квалифицирует лиды и передаёт разрешённые контакты в CRM.",
    "sitebuild":"Проверяет рабочее состояние сайта/хранилища и восстанавливает безопасные внутренние сбои.",
}

def _generic_timer_schedule(timer_name: str, every_minutes: int | None = None) -> dict[str, Any]:
    # SYSTEMD_TIMER_RUNNING_NOW_TRUTH_V1: OnUnitActiveSec timers can temporarily
    # expose "- -" for NEXT while their service is executing. That is not
    # "unknown schedule": the module is doing the promised work right now.
    next_at=_systemd_timer_next_run(timer_name)
    running_now=False
    last_at=None
    if next_at is None:
        running_now=_systemd_timer_service_running(timer_name)
        last_at=_systemd_timer_last_trigger(timer_name)
        # SYSTEMD_TIMER_POSTRUN_RETRY_V1: immediately after a oneshot exits,
        # systemd may need a few milliseconds to calculate the next
        # OnUnitActiveSec timestamp. Retry read-only twice instead of showing a
        # fake "unknown time" to the owner.
        if not running_now:
            import time as _time
            for _delay in (0.08,0.16):
                _time.sleep(_delay)
                next_at=_systemd_timer_next_run(timer_name)
                if next_at is not None:
                    break
    return {
        "scheduler":timer_name,
        "every_minutes":every_minutes,
        "next_run_at":next_at.isoformat() if next_at else None,
        "next_runs":[next_at.isoformat()] if next_at else [],
        "running_now":running_now,
        "last_cycle_at":last_at.isoformat() if last_at else None,
        "last_cycle_status":"running" if running_now else None,
        "last_module_result":None,
        "proof_source":"systemd:list-timers + service-state" if running_now else "systemd:list-timers",
    }

def _mop_routine_schedule(db) -> dict[str, Any]:
    now=datetime.now(timezone.utc)
    hb=(db.query(ReliabilityHeartbeat)
        .filter(ReliabilityHeartbeat.module=="messenger",ReliabilityHeartbeat.worker_id=="poller")
        .order_by(ReliabilityHeartbeat.last_seen_at.desc()).first())
    details=dict(getattr(hb,"details_json",None) or {}) if hb else {}
    seen=getattr(hb,"last_seen_at",None) if hb else None
    if seen is not None and seen.tzinfo is None:
        seen=seen.replace(tzinfo=timezone.utc)
    interval_sec=max(5,int(details.get("interval_sec") or 15))
    next_at=(seen+timedelta(seconds=interval_sec)) if seen else None
    while next_at is not None and next_at<=now:
        next_at+=timedelta(seconds=interval_sec)
    last_result=None
    if seen:
        last_result={
            "action":"messenger_poll_cycle",
            "passed":str(getattr(hb,"state","") or "")=="ok",
            "dependency_state":None,
            "verification":"messenger poller heartbeat persisted after real sync cycle",
            "facts":{
                "in_flight":details.get("in_flight"),
                "sync_in_flight":details.get("sync_in_flight"),
                "cycle_sec":details.get("cycle_sec"),
            },
        }
    return {
        "scheduler":"messenger/poller",
        "every_seconds":interval_sec,
        "every_minutes":round(interval_sec/60,2),
        "next_run_at":next_at.isoformat() if next_at else None,
        "next_runs":[(next_at+timedelta(seconds=interval_sec*i)).isoformat() for i in range(6)] if next_at else [],
        "last_cycle_at":seen.isoformat() if seen else None,
        "last_cycle_status":str(getattr(hb,"state",None) or "missing") if hb else "missing",
        "last_module_result":last_result,
        "proof_source":"reliability_heartbeats:messenger/poller",
    }

def _build_routine_plan(db, world: dict[str, Any], account_id: str | None, monitor_schedule: dict[str, Any]) -> list[dict[str, Any]]:
    if not account_id:
        return []
    timer_map={
        "rop":("boris-rop-auto.timer",10),
        "email":("boris-email-queue.timer",1),
        "social":("boris-social-guardian.timer",2),
        "analytics":("boris-daily-stats-collector.timer",60),
        "telephony":("boris-call-digest.timer",10),
        "prospecting":("boris-lead-radar.timer",1),
    }
    out=[]
    for mod in world.get("modules") or []:
        module=str(mod.get("module") or "")
        if not module:
            continue
        if module=="marketing":
            schedule=_marketing_execution_schedule(db,account_id)
            kind="execute_and_verify"
        elif module=="reactivation":
            schedule=_reactivation_execution_schedule(db,account_id)
            kind="execute_and_verify"
        elif module in {"mop","messages"}:
            schedule=_mop_routine_schedule(db)
            kind="execute_and_verify"
        elif module in timer_map:
            timer,every=timer_map[module]
            schedule=_generic_timer_schedule(timer,every)
            kind="execute_and_verify"
        else:
            schedule=monitor_schedule
            kind="monitor_and_selfheal"
        out.append({
            "module":module,
            "task":ROUTINE_TASK_TEXT.get(module,"Проверяет фактическое состояние модуля и запускает безопасное восстановление при отклонении."),
            "execution_kind":kind,
            "scheduler":schedule.get("scheduler"),
            "planned_for":schedule.get("next_run_at"),
            "next_runs":schedule.get("next_runs") or [],
            "every_minutes":schedule.get("every_minutes"),
            "every_seconds":schedule.get("every_seconds"),
            "last_cycle_at":schedule.get("last_cycle_at"),
            "last_cycle_status":schedule.get("last_cycle_status"),
            "last_module_result":schedule.get("last_module_result"),
            "running_now":bool(schedule.get("running_now")),
            "proof_source":schedule.get("proof_source"),
        })
    return out


def build_action_plan(db, account_id: str | None = None) -> dict[str, Any]:
    # BRAIN_ACTION_PLAN_SCOPE_TYPE_GUARD_V1: this boundary is used by HTTP/chat
    # callers and must never let an accidentally supplied world-state dict leak
    # into SQL filters (psycopg cannot adapt dict). Fail closed with a precise
    # contract error instead of turning Owner Cockpit/Brain planning into a 500.
    if account_id is not None and not isinstance(account_id, str):
        raise TypeError("account_id must be str or None")
    world=build_world_state(db,account_id=account_id)
    items=[]
    # MONEY_POLICY_BLOCKERS_V1: a missing daily budget is not a module crash.
    # It is an explicit owner-policy dependency. Keep it visible in WAITING so
    # BORIS continues all free/non-money work without repeatedly asking the
    # owner to operate the system.
    money_q=db.query(ControlIncident).filter(
        ControlIncident.state=="open",
        ControlIncident.incident_type.in_(("MONEY_GUARD_MISSING_LIMIT","MONEY_GUARD_MISSING_HARD_CAP")),
    )
    if account_id:
        money_q=money_q.filter(ControlIncident.account_id==account_id)
    for inc in money_q.all():
        details=dict(inc.details_json or {})
        field=str(details.get("field") or "daily_budget_limit_rub")
        if field=="daily_budget_limit_rub":
            owner_text=(
                "Подтвердите дневной рекламный лимит для аккаунта. Сейчас лимит не задан (0 ₽). "
                "После сохранения BORIS сам продолжит рекламную работу на ближайшем системном цикле."
            )
        elif field=="hard_max_bid_rub":
            owner_text=(
                "Подтвердите максимальную разрешённую ставку для рекламы. "
                "После сохранения BORIS сам продолжит работу на ближайшем системном цикле."
            )
        else:
            owner_text=(
                f"Подтвердите денежный лимит «{field}». "
                "После сохранения BORIS сам продолжит работу на ближайшем системном цикле."
            )
        items.append({
            "module":"marketing","account_id":inc.account_id,"health":"waiting_owner",
            "classification":"OWNER_MONEY_POLICY_REQUIRED","confidence":1.0,
            "evidence_for":[inc.symptom, f"{field}={details.get('value')}", "money actions fail-closed; non-money work continues"],
            "action":"owner_policy_required","lane":"WAITING","owner_action_required":True,
            "owner_action_text":owner_text,
            "policy_field":field,"incident_id":str(inc.id),
            "steps":_steps("OWNER_MONEY_POLICY_REQUIRED","owner_policy_required","marketing"),
        })
    for module in world.get("modules") or []:
        diag=dict(module.get("diagnosis") or {})
        health=str(module.get("health") or "pass")
        if health == "pass":
            continue
        action=diag.get("safe_action")
        module_name=str(module.get("module") or "system")
        meta=(dict(ACTION_META[str(action)]) if action and str(action) in ACTION_META else ({"level":"L4","reversible":False,"requires_owner_policy":True,"executor":module_name,"unregistered_action":True} if action else {}))
        # SAFE_ACTION_OWNER_DEPENDENCY_SPLIT_V2: one diagnosis can contain two
        # independent truths: BORIS has safe work it must continue NOW, while a
        # separate money/external decision really waits for the owner. Never
        # mark the safe work non-executable merely because the dependency exists.
        action_requires_owner=bool(meta.get("requires_owner_policy"))
        diagnosis_requires_owner=bool(diag.get("owner_action_required"))
        dependency_state=str(diag.get("dependency_state") or "")
        external=bool(meta.get("external_dependency")) or dependency_state in {"waiting_external","partial_external"}
        if diagnosis_requires_owner and action and not action_requires_owner:
            owner_key=(module_name,str(module.get("account_id") or account_id or ""))
            existing_owner_keys={(str(x.get("module") or ""),str(x.get("account_id") or "")) for x in items if x.get("lane")=="WAITING" and x.get("owner_action_required")}
            if owner_key not in existing_owner_keys:
                items.append({
                    "module":module_name,
                    "account_id":module.get("account_id"),
                    "health":"waiting_owner",
                    "classification":diag.get("classification"),
                    "confidence":diag.get("confidence"),
                    "evidence_for":diag.get("evidence_for") or [],
                    "action":"owner_policy_required" if module_name=="marketing" else None,
                    "lane":"WAITING",
                    "owner_action_required":True,
                    "owner_action_text":diag.get("owner_action_text"),
                    "steps":_steps(str(diag.get("classification") or "UNKNOWN"),"owner_policy_required" if module_name=="marketing" else None,module_name),
                })
            lane="NOW"
            requires_owner=False
        else:
            requires_owner=bool(diagnosis_requires_owner or action_requires_owner)
            if external:
                lane="BLOCKED"
            elif action and not action_requires_owner:
                lane="NOW"
            elif requires_owner:
                lane="WAITING"
            elif action:
                lane="NOW"
            else:
                lane="NEXT"
        items.append({
            "module":module_name,
            "account_id":module.get("account_id"),
            "health":health,
            "classification":diag.get("classification"),
            "confidence":diag.get("confidence"),
            "evidence_for":diag.get("evidence_for") or [],
            "action":action,
            "lane":lane,
            "owner_action_required":requires_owner,
            "owner_action_text":diag.get("owner_action_text"),
            "owner_dependency_present":diagnosis_requires_owner and not requires_owner,
            "steps":_steps(str(diag.get("classification") or "UNKNOWN"),str(action) if action else None,module_name),
        })
    execution_schedule=_guardian_execution_schedule(db)
    for item in items:
        _attach_execution(db,item,execution_schedule)
    routine_plan=_build_routine_plan(db,world,account_id,execution_schedule)
    return {
        "status":world.get("status"),
        "account_id":account_id,
        "queue":{
            "NOW":[x for x in items if x["lane"]=="NOW"],
            "NEXT":[x for x in items if x["lane"]=="NEXT"],
            "WAITING":[x for x in items if x["lane"]=="WAITING"],
            "BLOCKED":[x for x in items if x["lane"]=="BLOCKED"],
            "DONE":[],
        },
        "items":items,
        "routine_plan":routine_plan,
        "world_summary":world.get("summary") or {},
        "execution_schedule":execution_schedule,
        "checked_at":world.get("checked_at"),
    }
