"""Autonomous reconciliation of transient control-plane incidents."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.control_plane import ControlIncident
import uuid
from app.services.brain_world_state import build_module_state
from app.services.reliability import record_event
from app.services.exception_observability import observe_suppressed

READ_INCIDENT_TYPES = {"MODULE_STATE_READ_FAILED", "BUSINESS_STATE_READ_FAILED", "MODULE_ADAPTER_MISSING"}
DEGRADED_INCIDENT_TYPES = {"MODULE_STATE_DEGRADED", "BUSINESS_STATE_DEGRADED"}
RECONCILABLE_INCIDENT_TYPES = READ_INCIDENT_TYPES | DEGRADED_INCIDENT_TYPES
UNHEALTHY_DIAGNOSIS = {"degraded", "blocked", "critical"}


def _resolution_decision(incident_type: str, state: dict) -> tuple[bool, str]:
    """Decide from fresh deterministic evidence, never from incident age alone."""
    if not state or state.get("state_error"):
        return False, "state_unavailable"
    diagnosis=state.get("diagnosis") or {}
    classification=str(diagnosis.get("classification") or "")
    if not diagnosis or classification in {"MODULE_STATE_UNAVAILABLE", "BUSINESS_DIAGNOSIS_FAILED"}:
        return False, "diagnosis_unavailable"

    if incident_type in READ_INCIDENT_TYPES:
        # The read failure itself is gone even when the recovered business state
        # truthfully says degraded. A separate BUSINESS_STATE_DEGRADED incident
        # owns that business problem.
        return True, "fresh_readback_verified"

    if incident_type in DEGRADED_INCIDENT_TYPES:
        diagnosis_health=str(diagnosis.get("health") or "pass").lower()
        if diagnosis_health in UNHEALTHY_DIAGNOSIS:
            return False, f"diagnosis_still_{diagnosis_health}"
        return True, "business_postcondition_recovered"

    return False, "incident_type_not_reconcilable"




def _sync_reactivation_external_incidents(db, now, account_ids=None) -> dict:
    """Open/update/resolve one account incident from fresh reactivation capability truth.

    Partial 402 is intentionally informational/external: healthy chats continue.
    We only create an owner-facing incident after the provider condition is
    persistent, and resolve it automatically after a verified healthy read.
    """
    from sqlalchemy import text
    from app.reactivation_avito_health import effective_capability
    from app.api.messenger import get_manager_balance
    accounts=([str(x) for x in account_ids] if account_ids is not None else [str(r[0]) for r in db.execute(text("select distinct account_id from storage where key in ('reactivation_avito_capability_v1','reactivation_inbox_capability_v1') and account_id is not null")).all()])
    opened=updated=resolved=0
    active_types={"REACTIVATION_AVITO_EXTERNAL_ACCESS","REACTIVATION_AVITO_PARTIAL_ACCESS"}
    for account_id in accounts:
        cap=effective_capability(db,account_id) or {}; cls=str(cap.get("classification") or "")
        # An inactive/expired MOP is an expected stop, never an incident. Resolve
        # any old provider-access alert when the client contour is no longer active.
        service_active=bool((get_manager_balance(account_id) or {}).get("active"))
        persistence=cap.get("persistence") or {}; persistent=bool(persistence.get("persistent")) and service_active
        wanted=None
        if persistent and cls=="avito_messenger_subscription_required": wanted="REACTIVATION_AVITO_EXTERNAL_ACCESS"
        elif persistent and cls=="avito_messenger_history_partial_402": wanted="REACTIVATION_AVITO_PARTIAL_ACCESS"
        rows=db.query(ControlIncident).filter(ControlIncident.module=="reactivation",ControlIncident.account_id==account_id,ControlIncident.state=="open",ControlIncident.incident_type.in_(active_types)).all()
        if wanted:
            keep=next((x for x in rows if x.incident_type==wanted),None)
            for old in rows:
                if old is keep: continue
                old.state="resolved"; old.resolved_at=now; old.updated_at=now
                d=dict(old.details_json or {}); d.update({"resolved_by":"capability_transition","resolved_classification":cls,"verified_at":cap.get("checked_at")}); old.details_json=d; resolved+=1
            details={"classification":cls,"checked_at":cap.get("checked_at"),"source":cap.get("source"),"synced":cap.get("synced"),"transport_errors":cap.get("transport_errors"),"persistent":persistent,"consecutive":persistence.get("consecutive"),"service_continues":wanted.endswith("PARTIAL_ACCESS"),"auto_reprobe":True}
            if keep:
                keep.details_json=details; keep.updated_at=now; updated+=1
            else:
                partial=wanted.endswith("PARTIAL_ACCESS")
                db.add(ControlIncident(id=str(uuid.uuid4()),incident_type=wanted,priority="P2",state="open",module="reactivation",account_id=account_id,symptom=("Avito ограничивает часть чатов; доступные диалоги продолжают работать." if partial else "Avito временно не даёт доступ к API Мессенджера для проверенных чатов."),root_cause=cls,remediation="BORIS автоматически перепроверяет доступ и безопасно продолжит/восстановит работу без ручного перезапуска.",prevention="Минутный inbox-контроль, точная блокировка чатов, запрет слепых повторов и платной генерации на недоступный транспорт.",details_json=details,opened_at=now,updated_at=now)); opened+=1
        else:
            for old in rows:
                old.state="resolved"; old.resolved_at=now; old.updated_at=now
                d=dict(old.details_json or {}); d.update({"resolved_by":"fresh_capability_readback","resolved_classification":cls,"verified_at":cap.get("checked_at")}); old.details_json=d; resolved+=1
    return {"opened":opened,"updated":updated,"resolved":resolved}

def _sync_daily_outcome_incidents(db, now) -> dict:
    """Persist only daily outcome failures that self-heal can no longer repair.

    Temporary lag stays in module health and is retried automatically. A
    ControlIncident is opened only when the domain health explicitly says
    owner_action_required=True / critical. Incidents are keyed by service date
    so yesterday's missed SLA cannot disappear just because a new day started.
    """
    from app.services.control_plane import open_incident
    from app.services.reliability import email_delivery_health
    from app.models.reliability import ReliabilityHeartbeat

    opened=updated=resolved=0

    def sync_one(*, incident_type, module, account_id, service_date, active, symptom, details, priority="P1"):
        nonlocal opened,updated,resolved
        rows=db.query(ControlIncident).filter(
            ControlIncident.incident_type==incident_type,
            ControlIncident.module==module,
            ControlIncident.account_id==account_id,
            ControlIncident.state=="open",
        ).order_by(ControlIncident.opened_at.asc()).all()
        same=[r for r in rows if str((r.details_json or {}).get("service_date") or "")==str(service_date or "")]
        if active:
            if same:
                row=same[-1]
                payload=dict(row.details_json or {})
                payload.update(details or {})
                payload["service_date"]=service_date
                payload["last_verified_at"]=now.isoformat()
                row.details_json=payload
                row.updated_at=now
                row.symptom=symptom
                updated+=1
            else:
                open_incident(
                    db,incident_type=incident_type,symptom=symptom,module=module,
                    account_id=account_id,priority=priority,
                    details={"service_date":service_date,"last_verified_at":now.isoformat(),**(details or {})},
                )
                opened+=1
        else:
            # Resolve only the same service-date incident. Older missed days are
            # historical failures and must remain visible until explicitly
            # handled; a healthy new day is not proof that yesterday was fixed.
            for row in same:
                row.state="resolved"; row.resolved_at=now; row.updated_at=now
                payload=dict(row.details_json or {})
                payload.update({"resolved_by":"daily_outcome_recovered","resolved_at":now.isoformat()})
                row.details_json=payload
                resolved+=1

    # Email: only critical daily status requires owner action. During the send
    # window a behind-plan state is self-healed by the canonical email timer and
    # deliberately does not create an owner incident.
    try:
        email=email_delivery_health()
        for item in list(email.get("daily_outreach") or []):
            owner_id=int(item.get("owner_id") or 0)
            if owner_id<=0:
                continue
            critical=bool(item.get("owner_action_required") and str(item.get("state") or "")=="critical")
            service_date=str(item.get("service_date") or "")
            sync_one(
                incident_type="OWNER_EMAIL_DAILY_PLAN_FAILED",
                module="email_outreach",
                account_id=f"owner:{owner_id}",
                service_date=service_date,
                active=critical,
                symptom=(
                    f"Email-план за {service_date or 'текущий день'} не выполнен и автоматический догон уже невозможен."
                ),
                details={"daily_outreach":item},
                priority="P1",
            )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=159)

    # Social: consume the canonical worker heartbeat. We escalate only a daily
    # outcome explicitly marked owner_action_required (for example a previous
    # day slot that never obtained both TG and VK delivery IDs).
    try:
        hb=db.query(ReliabilityHeartbeat).filter(
            ReliabilityHeartbeat.module=="runtime",
            ReliabilityHeartbeat.worker_id=="social_posting_scheduler",
        ).first()
        hbd=dict(hb.details_json or {}) if hb else {}
        social_daily_checks = [
            ("ocean_daily_delivery", "u2", "Океан Клиентов и Борис"),
            ("dushi_daily_delivery", "u2:f938caa3a944", "От Души"),
            ("manicure_daily_delivery", "u2:270dbf472f7f", "Маникюр Евпатория"),
        ]
        transport_projects=list((hbd.get("transport_readiness") or {}).get("projects") or [])
        for details_key, incident_account_id, project_label in social_daily_checks:
            daily=dict(hbd.get(details_key) or {})
            service_date=str(daily.get("failed_service_date") or daily.get("local_date") or "")
            project_id=str(daily.get("project_id") or "")
            transport_project=next((dict(x) for x in transport_projects if isinstance(x,dict) and str(x.get("project_id") or "")==project_id),{})
            vk=dict(transport_project.get("vk") or {})
            external_delivery_wait=bool(
                not vk.get("ready") and vk.get("retryable_external")
                and str(vk.get("dependency_state") or "")=="waiting_external"
            )
            critical=bool(
                daily.get("owner_action_required")
                and str(daily.get("state") or "")=="critical"
                and not external_delivery_wait
            )
            if service_date:
                sync_one(
                    incident_type="OWNER_SOCIAL_DAILY_PLAN_FAILED",
                    module="social",
                    account_id=incident_account_id,
                    service_date=service_date,
                    active=critical,
                    symptom=(
                        f"План публикаций «{project_label}» за {service_date} не выполнен, "
                        "и обычный автоматический догон уже не может закрыть пропуск."
                    ),
                    details={
                        "daily_delivery":daily,"project_name":project_label,
                        **({
                            "dependency_state":"waiting_external",
                            "owner_action_required":False,
                            "external_reason":str(vk.get("reason") or "external_transport_wait"),
                        } if external_delivery_wait else {}),
                    },
                    priority="P1",
                )

        # Pre-slot static readiness: after deterministic self-heal, a remaining
        # blocker is actionable before the next scheduled generation window.
        readiness=dict(hbd.get("pre_slot_readiness") or {})
        for project in list(readiness.get("projects") or []):
            if not isinstance(project,dict):
                continue
            pid=str(project.get("project_id") or "")
            pname=str(project.get("project") or pid)
            next_slot=str(project.get("next_generation_slot_local") or "")
            service_date=(next_slot.split(" ",1)[0] if next_slot else now.date().isoformat())
            reasons=list(project.get("blocked_reasons") or [])
            active=bool(reasons)
            _project_blockers=[
                x for x in (readiness.get("blocked") or [])
                if isinstance(x,dict) and str(x.get("project_id") or "")==pid
            ]
            _dependency_states={
                str(x.get("dependency_state") or "")
                for x in _project_blockers if x.get("dependency_state")
            }
            dependency_state=(
                "waiting_external"
                if _project_blockers and _dependency_states=={"waiting_external"}
                else None
            )
            sync_one(
                incident_type="OWNER_SOCIAL_PRE_SLOT_NOT_READY",
                module="social",
                account_id=("u2:"+pid if pid else "u2"),
                service_date="pre_slot",
                active=active,
                symptom=(
                    f"«{pname}» не готов к следующему Social-слоту"
                    + (f" {next_slot}" if next_slot else "")
                    + (": " + ", ".join(str(x) for x in reasons) if reasons else ".")
                ),
                details={
                    "project_name":pname,
                    "project_id":pid,
                    "next_generation_slot_local":next_slot,
                    "blocked_reasons":reasons,
                    "pre_slot_readiness":project,
                    **({
                        "dependency_state":dependency_state,
                        "owner_action_required":False,
                    } if dependency_state else {}),
                },
                priority="P1",
            )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=263)

    # Active paid Social pre-slot readiness: this covers non-owner paid
    # clients before their generation window. One project/account is isolated
    # from the rest; incidents close automatically when fresh readiness is green.
    try:
        paid_readiness=dict(hbd.get("active_paid_pre_slot_readiness") or {})
        for project in list(paid_readiness.get("projects") or []):
            if not isinstance(project,dict) or not project.get("monitored"):
                continue
            acc=str(project.get("account_id") or "")
            pid=str(project.get("project_id") or "")
            pname=str(project.get("project") or pid)
            reasons=list(project.get("blocked_reasons") or [])
            next_slot=str(project.get("next_generation_slot_local") or "")
            minutes=project.get("minutes_to_next_slot")
            active=bool(reasons)
            incident_account=(acc+":"+pid if acc and pid else acc or pid)
            sync_one(
                incident_type="SOCIAL_PAID_PRE_SLOT_NOT_READY",
                module="social",
                account_id=incident_account,
                service_date="pre_slot",
                active=active,
                symptom=(
                    f"Платный Social-проект «{pname}» не готов к следующему слоту"
                    + (f" {next_slot}" if next_slot else "")
                    + (": " + ", ".join(str(x) for x in reasons) if reasons else ".")
                ),
                details={
                    "source_account_id":acc,
                    "project_id":pid,
                    "project_name":pname,
                    "next_generation_slot_local":next_slot,
                    "minutes_to_next_slot":minutes,
                    "blocked_reasons":reasons,
                    "owner_action_required":bool(project.get("owner_action_required")),
                    "pre_slot_readiness":project,
                },
                priority="P1" if active and (minutes is None or int(minutes) <= 360) else "P2",
            )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=305)

    try:
        transport_hb=db.query(ReliabilityHeartbeat).filter(
            ReliabilityHeartbeat.module=="runtime",
            ReliabilityHeartbeat.worker_id=="social_transport_readiness",
        ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
        transport=dict(transport_hb.details_json or {}) if transport_hb else {}
        for project in list(transport.get("projects") or []):
            if not isinstance(project,dict):
                continue
            source_account=str(project.get("account_id") or "u2")
            pid=str(project.get("project_id") or "")
            pname=str(project.get("project") or pid)
            active=bool(
                not project.get("ready")
                and project.get("owner_action_required")
            )
            reasons=[]
            for key in ("telegram","vk"):
                part=dict(project.get(key) or {})
                if part and not part.get("ready"):
                    reasons.append(str(part.get("reason") or key+"_not_ready"))
            sync_one(
                incident_type="OWNER_SOCIAL_TRANSPORT_NOT_READY",
                module="social",
                account_id=(source_account+":"+pid if pid else source_account),
                service_date="transport",
                active=active,
                symptom=(
                    f"«{pname}»: потеряно право публикации в Social-транспорт"
                    + (": " + ", ".join(reasons) if reasons else ".")
                ),
                details={
                    "source_account_id":source_account,
                    "project_name":pname,"project_id":pid,
                    "transport_readiness":project,
                    "blocked_reasons":reasons,
                },
                priority="P1",
            )

        # An active paid Social subscription without posting_projects is not a
        # runtime transport fault; it is an onboarding/configuration gap. Keep
        # one durable incident per account and resolve it automatically as soon
        # as a project appears.
        active_gap_accounts=set()
        for gap in list(transport.get("subscription_gaps") or []):
            if not isinstance(gap,dict):
                continue
            acc=str(gap.get("account_id") or "")
            if not acc:
                continue
            active_gap_accounts.add(acc)
            sync_one(
                incident_type="SOCIAL_SUBSCRIPTION_PROJECT_MISSING",
                module="social",
                account_id=acc,
                service_date="configuration",
                active=True,
                symptom=(
                    "Активная Social-подписка есть, но проект публикации и "
                    "назначения Telegram/VK не настроены."
                ),
                details={
                    "subscription_gap":gap,
                    "dependency_state":"waiting_external",
                    "owner_action_required":False,
                    "required_fields":list(
                        gap.get("required_external_fields")
                        or ["channel_tg","vk_owner_id"]
                    ),
                    "self_heal_after_configuration":True,
                    "onboarding_scaffold":"posting_onboarding_scaffold_v1",
                    "auto_resume":"materialize_project_when_destinations_present",
                },
                # External publication destinations cannot be safely invented.
                # BORIS prepares the paid contour itself and waits only for the
                # real client/platform destinations, then auto-materializes it.
                priority="P2",
            )

        old_gaps=db.query(ControlIncident).filter(
            ControlIncident.incident_type=="SOCIAL_SUBSCRIPTION_PROJECT_MISSING",
            ControlIncident.module=="social",
            ControlIncident.state=="open",
        ).all()
        for old_gap in old_gaps:
            acc=str(old_gap.account_id or "")
            if acc and acc not in active_gap_accounts:
                sync_one(
                    incident_type="SOCIAL_SUBSCRIPTION_PROJECT_MISSING",
                    module="social",
                    account_id=acc,
                    service_date="configuration",
                    active=False,
                    symptom="Social posting project configured.",
                    details={"resolved_by":"subscription_project_present"},
                    priority="P1",
                )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=406)

    return {"opened":opened,"updated":updated,"resolved":resolved}


def run_once(limit: int = 20) -> dict:
    """Resolve only incidents whose exact failure class is disproved by fresh readback."""
    from app.db.session import SessionLocal

    db=SessionLocal()
    try:
        rows=(db.query(ControlIncident).filter(
            ControlIncident.state=="open",
            ControlIncident.incident_type.in_(RECONCILABLE_INCIDENT_TYPES),
        ).order_by(ControlIncident.opened_at.asc()).limit(max(1,min(int(limit or 20),100))).all())
        now=datetime.now(timezone.utc)
        external=_sync_reactivation_external_incidents(db, now)
        daily_outcomes=_sync_daily_outcome_incidents(db, now)
        if not rows:
            db.commit()
            return {
                "status":"ok","checked":0,"resolved":0,"still_unhealthy":0,
                "reactivation_external":external,
                "daily_outcomes":daily_outcomes,
            }

        projections={}
        for row in rows:
            key=(str(row.module or ""), str(row.account_id) if row.account_id else None)
            if key not in projections:
                try:
                    projections[key]=build_module_state(db,key[0],key[1])
                except Exception as exc:
                    projections[key]={"state_error":type(exc).__name__}

        now=datetime.now(timezone.utc)
        resolved=0
        still_unhealthy=0
        for row in rows:
            key=(str(row.module or ""), str(row.account_id) if row.account_id else None)
            state=projections.get(key) or {}
            can_resolve,reason=_resolution_decision(str(row.incident_type or ""),state)
            if not can_resolve:
                if reason.startswith("diagnosis_still_"):
                    still_unhealthy += 1
                continue

            diagnosis=state.get("diagnosis") or {}
            details=dict(row.details_json or {})
            if row.incident_type in READ_INCIDENT_TYPES:
                row.root_cause=str(details.get("error_type") or row.root_cause or "transient_adapter_read_error")[:2000]
                row.remediation="Свежая повторная проверка адаптера успешно восстановила детерминированное чтение."
                row.prevention="Автономный reconciler повторяет readback; бизнес-деградация отслеживается отдельным incident."
            else:
                # Preserve the original business root cause for truthful audit.
                row.root_cause=str(row.root_cause or (details.get("diagnosis") or {}).get("classification") or "business_state_degraded")[:2000]
                row.remediation="Свежая детерминированная диагностика подтвердила, что прежняя бизнес-деградация больше не существует."
                row.prevention="Degraded incident закрывается только после diagnosis health вне degraded/blocked/critical."

            row.state="resolved"
            row.resolved_at=now
            row.updated_at=now
            details.update({
                "reconciled_at":now.isoformat(),
                "reconciled_health":state.get("health"),
                "reconciled_diagnosis":diagnosis.get("classification"),
                "reconcile_reason":reason,
            })
            row.details_json=details
            record_event(
                db,row.module or "system","incident.resolved",
                "Control-plane incident resolved by fresh deterministic post-condition.",
                severity="info",state="resolved",account_id=row.account_id,
                details={
                    "control_incident_id":row.id,
                    "incident_type":row.incident_type,
                    "resolution":reason,
                    "diagnosis":diagnosis.get("classification"),
                },
            )
            resolved += 1

        db.commit()
        return {
            "status":"ok",
            "checked":len(rows),
            "resolved":resolved,
            "still_unhealthy":still_unhealthy,
            "reactivation_external":external,
            "daily_outcomes":daily_outcomes,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
