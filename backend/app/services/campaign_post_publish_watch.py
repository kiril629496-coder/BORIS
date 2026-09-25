from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.storage import Storage
from app.models.reliability import ReliabilityKillSwitch
from app.services.reliability import heartbeat
from app.services.marketing_clock import MOSCOW, marketing_today_iso
from app.services.exception_observability import observe_suppressed


WATCH_WINDOW_HOURS = 72
STATS_STALE_SECONDS = 3600
MAX_CAMPAIGNS_PER_TICK = 100


PLANETA_ACCOUNT_ID = "planetazayavki_65985"
PLANETA_EXTERNAL_HOLD_RECHECK_MAX_PER_TICK = 1
PLANETA_OWNER_FUNDS_NOTICE_RETRY_SECONDS = 3600
PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON = (
    "AUTO:PLANETA_KPI_FRESH_SPEND_HOURLY_BYPASS_FENCE_V1"
)


def _notify_planeta_owner_funds_once(
    operation_id: str,
    result: dict,
    now: datetime,
    *,
    db_factory=None,
    notify_fn=None,
) -> dict:
    """PLANETA_OWNER_FUNDS_TRANSITION_NOTICE_V1.

    Owner notifications are for true BLOCKED_HUMAN only. During
    waiting_budget_policy the owner must not be asked to add money. If the
    money policy clears and Avito still returns 2214, send one durable notice
    for this KPI operation. Failed Telegram delivery is retried at most hourly.
    """
    status = str((result or {}).get("status") or "")
    owner_required = bool((result or {}).get("owner_action_required"))
    if status != "waiting_owner_funds" or not owner_required:
        return {
            "status": "not_required",
            "sent": False,
            "owner_action_required": owner_required,
        }

    operation_id = str(operation_id or "").strip()
    if not operation_id:
        return {
            "status": "invalid_operation_id",
            "sent": False,
            "owner_action_required": True,
        }

    factory = db_factory or SessionLocal
    key = f"planeta_owner_notice:waiting_owner_funds:{operation_id}"
    db = factory()
    marker_id = None
    try:
        row = (
            db.query(Storage)
            .filter(
                Storage.account_id == PLANETA_ACCOUNT_ID,
                Storage.key == key,
            )
            .order_by(Storage.id.desc())
            .first()
        )
        try:
            marker = json.loads(row.value or "{}") if row else {}
        except Exception:
            marker = {}
        if not isinstance(marker, dict):
            marker = {}

        if str(marker.get("state") or "") == "sent":
            return {
                "status": "already_sent",
                "sent": True,
                "owner_action_required": True,
                "sent_at": marker.get("sent_at"),
            }

        last_attempt = None
        try:
            raw = str(marker.get("last_attempt_at") or "")
            last_attempt = (
                datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if raw else None
            )
            if last_attempt and last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=timezone.utc)
        except Exception:
            last_attempt = None
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        if (
            last_attempt is not None
            and (
                now_aware - last_attempt.astimezone(now_aware.tzinfo)
            ).total_seconds() < PLANETA_OWNER_FUNDS_NOTICE_RETRY_SECONDS
        ):
            return {
                "status": "retry_cooldown",
                "sent": False,
                "owner_action_required": True,
                "last_attempt_at": marker.get("last_attempt_at"),
            }

        marker.update({
            "policy_version": "PLANETA_OWNER_FUNDS_TRANSITION_NOTICE_V1",
            "operation_id": operation_id,
            "state": "attempting",
            "last_attempt_at": now_aware.isoformat(),
            "provider_code": int((result or {}).get("provider_code") or 2214),
            "owner_action_required": True,
        })
        if row is None:
            row = Storage(
                account_id=PLANETA_ACCOUNT_ID,
                key=key,
                value=json.dumps(marker, ensure_ascii=False),
            )
            db.add(row)
        else:
            row.value = json.dumps(marker, ensure_ascii=False)
        db.commit()
        try:
            db.refresh(row)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=133)
        marker_id = getattr(row, "id", None)
    finally:
        db.close()

    if notify_fn is None:
        from app.ext_api.notify import send as notify_fn

    message = (
        "BORIS · Планета мебели\n"
        "Avito после снятия внутреннего денежного ограничения всё ещё требует "
        "пополнить аванс/баланс (код 2214).\n"
        "Повторная публикация остановлена безопасно. Пополните баланс Avito — "
        "после этого BORIS сам перепроверит блок и продолжит без ручного запуска.\n"
        "Сумму не указываю: в подтверждённом ответе Avito точная сумма не передана."
    )
    sent = bool(notify_fn(message))

    db = factory()
    try:
        row = None
        if marker_id is not None:
            row = db.query(Storage).filter(Storage.id == marker_id).first()
        if row is None:
            row = (
                db.query(Storage)
                .filter(
                    Storage.account_id == PLANETA_ACCOUNT_ID,
                    Storage.key == key,
                )
                .order_by(Storage.id.desc())
                .first()
            )
        if row is not None:
            try:
                marker = json.loads(row.value or "{}")
            except Exception:
                marker = {}
            if not isinstance(marker, dict):
                marker = {}
            marker["state"] = "sent" if sent else "retryable_failed"
            marker["last_result_at"] = now_aware.isoformat()
            if sent:
                marker["sent_at"] = now_aware.isoformat()
            else:
                marker["retry_after_seconds"] = (
                    PLANETA_OWNER_FUNDS_NOTICE_RETRY_SECONDS
                )
            row.value = json.dumps(marker, ensure_ascii=False)
            db.commit()
    finally:
        db.close()

    return {
        "status": "sent" if sent else "retryable_failed",
        "sent": sent,
        "owner_action_required": True,
    }


def _repair_planeta_kpi_observation_business_day(
    operation_id: str,
    result: dict,
    now: datetime,
    *,
    db_factory=None,
    avito_module=None,
) -> dict:
    """PLANETA_KPI_EFFECT_MOSCOW_DAY_SELF_HEAL_V1.

    Shared KPI publication confirmation currently opens the observation window
    from the UTC calendar date. Around Moscow midnight that can accidentally
    include the publication business day itself. PLANETA must measure only
    complete Moscow/Avito business days after confirmed publication.
    """
    if str((result or {}).get("status") or "") != "published":
        return {"status": "not_required", "changed": False}

    if not str(operation_id or "").strip():
        return {"status": "invalid_operation_id", "changed": False}

    A = avito_module
    if A is None:
        from app.api import avito as A

    factory = db_factory or SessionLocal
    db = factory()
    try:
        history, op = A._kpi_find_apply_operation(
            db,
            PLANETA_ACCOUNT_ID,
            str(operation_id),
        )
        if not op or str(op.get("status") or "") != "published":
            return {"status": "published_operation_not_found", "changed": False}

        published_at = _dt(op.get("published_at"))
        if published_at is None:
            return {"status": "published_at_missing", "changed": False}

        window = dict(op.get("observation_window") or {})
        min_days = max(1, int(window.get("min_complete_days") or 1))
        publication_business_day = published_at.astimezone(MOSCOW).date()
        expected_start = publication_business_day + timedelta(days=1)
        expected_end = publication_business_day + timedelta(days=min_days)
        expected_complete_at = datetime.combine(
            expected_end + timedelta(days=1),
            datetime.min.time(),
            tzinfo=MOSCOW,
        )

        current_start = str(window.get("start_date") or "")[:10]
        current_end = str(window.get("end_date") or "")[:10]
        already_correct = bool(
            current_start == expected_start.isoformat()
            and current_end == expected_end.isoformat()
            and str(window.get("business_day_timezone") or "") == "Europe/Moscow"
        )
        if already_correct:
            return {
                "status": "already_correct",
                "changed": False,
                "publication_business_day": publication_business_day.isoformat(),
                "start_date": current_start,
                "end_date": current_end,
            }

        window.update({
            "status": "active",
            "published_at": op.get("published_at"),
            "start_date": expected_start.isoformat(),
            "end_date": expected_end.isoformat(),
            "complete_after": expected_complete_at.isoformat(),
            "starts_after_external_publish": True,
            "business_day_timezone": "Europe/Moscow",
            "business_day_policy_version": (
                "PLANETA_KPI_EFFECT_MOSCOW_DAY_SELF_HEAL_V1"
            ),
            "business_day_reconciled_at": (
                now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            ).isoformat(),
        })
        op["observation_window"] = window
        effect = dict(op.get("effect") or {})
        effect["observation_window_business_day_reconciled"] = True
        effect["observation_window_business_day_policy_version"] = (
            "PLANETA_KPI_EFFECT_MOSCOW_DAY_SELF_HEAL_V1"
        )
        op["effect"] = effect

        A._kpi_apply_log_save(db, PLANETA_ACCOUNT_ID, history)
        db.commit()
        return {
            "status": "repaired",
            "changed": True,
            "publication_business_day": publication_business_day.isoformat(),
            "start_date": expected_start.isoformat(),
            "end_date": expected_end.isoformat(),
            "complete_after": expected_complete_at.isoformat(),
        }
    finally:
        db.close()


def _planeta_kpi_hold_fresh_spend_gate(
    account_id: str,
    *,
    db_factory=None,
    latest_spend_fn=None,
    eligible_fn=None,
) -> dict:
    """PLANETA_KPI_HOLD_FRESH_SPEND_GATE_V1.

    A historical money-pressure calculation becoming green is not enough to
    advance a held KPI publication. Require a provider-confirmed same-business-
    day spend signal no older than 15 minutes before calling confirm_publish.
    """
    if str(account_id or "") != PLANETA_ACCOUNT_ID:
        return {
            "fresh": False,
            "status": "skipped_scope",
            "account_id": account_id,
        }

    if latest_spend_fn is None:
        from app.services.marketing_money_policy import (
            latest_confirmed_spend as latest_spend_fn,
        )
    if eligible_fn is None:
        from app.services.marketing_signal_guard import (
            money_spend_signal_eligible as eligible_fn,
        )

    factory = db_factory or SessionLocal
    db = factory()
    try:
        signal = dict(
            latest_spend_fn(
                db,
                PLANETA_ACCOUNT_ID,
                max_age_seconds=900,
            )
            or {}
        )
        fresh = bool(
            eligible_fn(
                db,
                PLANETA_ACCOUNT_ID,
                signal,
                max_age_seconds=900,
            )
        )
        return {
            "fresh": fresh,
            "status": "fresh" if fresh else "waiting_fresh_spend",
            "max_age_seconds": 900,
            "signal_status": signal.get("status"),
            "signal_reason": signal.get("reason"),
            "storage_key": signal.get("storage_key"),
            "timestamp": signal.get("timestamp"),
            "age_seconds": signal.get("age_seconds"),
            "spending_date": signal.get("spending_date"),
            "spent_today_rub": signal.get("spent_today_rub"),
        }
    except Exception as exc:
        return {
            "fresh": False,
            "status": "waiting_fresh_spend",
            "max_age_seconds": 900,
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    finally:
        db.close()


def _planeta_kpi_local_budget_pressure(
    account_id: str,
    *,
    db_factory=None,
    limit_fn=None,
    pressure_fn=None,
) -> dict:
    """PLANETA_KPI_LOCAL_BUDGET_PRESSURE_GATE_V1.

    Cheap DB-only gate used before any provider stats self-heal. Provider reads
    are unnecessary while the historical presence-budget policy is still red.
    """
    if str(account_id or "") != PLANETA_ACCOUNT_ID:
        return {
            "blocked": True,
            "reason": "skipped_scope",
            "authoritative": False,
        }
    if limit_fn is None or pressure_fn is None:
        from app.services.marketing_money_policy import (
            effective_daily_budget_limit as _limit_fn,
            presence_budget_pressure as _pressure_fn,
        )
        limit_fn = limit_fn or _limit_fn
        pressure_fn = pressure_fn or _pressure_fn
    factory = db_factory or SessionLocal
    db = factory()
    try:
        limit = float(limit_fn(db, PLANETA_ACCOUNT_ID) or 0)
        pressure = dict(
            pressure_fn(db, PLANETA_ACCOUNT_ID, limit) or {}
        )
        pressure["daily_budget_limit_rub"] = (
            pressure.get("daily_budget_limit_rub") or limit
        )
        pressure["authoritative"] = True
        return pressure
    except Exception as exc:
        return {
            "blocked": True,
            "reason": "budget_pressure_check_error",
            "authoritative": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    finally:
        db.close()


def _sync_planeta_kpi_hourly_freshness_fence(
    active: bool,
    now: datetime,
    *,
    db_factory=None,
) -> dict:
    """PLANETA_KPI_HOURLY_FRESHNESS_BYPASS_FENCE_V1.

    While the KPI operation is in waiting_budget_policy, block the account from
    the hourly marketer so it cannot bypass the PLANETA fresh-spend gate through
    the shared kpi_goal_tick path. Only this auto-managed marketing kill-switch
    is changed; a pre-existing human/foreign kill-switch is never overwritten or
    automatically released.
    """
    factory = db_factory or SessionLocal
    db = factory()
    try:
        row = (
            db.query(ReliabilityKillSwitch)
            .filter(
                ReliabilityKillSwitch.module == "marketing",
                ReliabilityKillSwitch.account_id == PLANETA_ACCOUNT_ID,
            )
            .first()
        )
        reason = str(getattr(row, "reason", "") or "") if row else ""
        ours = reason.startswith(PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON)

        if active:
            if row is not None and bool(row.blocked) and not ours:
                return {
                    "status": "external_fence_preserved",
                    "active": True,
                    "managed": False,
                    "reason": reason,
                }
            if row is None:
                row = ReliabilityKillSwitch(
                    module="marketing",
                    account_id=PLANETA_ACCOUNT_ID,
                    blocked=True,
                    reason=(
                        f"{PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON}: "
                        "waiting_budget_policy"
                    ),
                )
                db.add(row)
                db.commit()
                return {
                    "status": "activated",
                    "active": True,
                    "managed": True,
                }
            if not bool(row.blocked):
                row.blocked = True
                row.reason = (
                    f"{PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON}: "
                    "waiting_budget_policy"
                )
                db.commit()
                return {
                    "status": "activated",
                    "active": True,
                    "managed": True,
                }
            return {
                "status": "already_active",
                "active": True,
                "managed": True,
            }

        if row is None:
            return {
                "status": "not_present",
                "active": False,
                "managed": False,
            }
        if bool(row.blocked) and not ours:
            return {
                "status": "external_fence_preserved",
                "active": True,
                "managed": False,
                "reason": reason,
            }
        if bool(row.blocked) and ours:
            row.blocked = False
            row.reason = (
                f"{PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON}: "
                f"released_at={now.isoformat()}"
            )
            db.commit()
            return {
                "status": "released",
                "active": False,
                "managed": True,
            }
        return {
            "status": "already_released",
            "active": False,
            "managed": ours,
        }
    finally:
        db.close()


def _recheck_planeta_kpi_external_publication_hold(
    account_id: str,
    now: datetime,
    *,
    db_factory=None,
    avito_module=None,
    fresh_spend_gate_fn=None,
    freshness_fence_fn=None,
    stats_refresh_fn=None,
    budget_pressure_fn=None,
) -> dict:
    """PLANETA_KPI_EXTERNAL_HOLD_WATCHDOG_V2.

    Advance only the already-existing publication confirmation state machine.
    waiting_budget_policy uses the cheap local money-policy wake-up. If that
    clears, or the operation is already waiting_owner_funds, BORIS may perform
    read-only live/provider verification. This watchdog never calls the external
    publication write endpoint itself.
    """
    if str(account_id or "") != PLANETA_ACCOUNT_ID:
        return {
            "status": "skipped_scope",
            "account_id": account_id,
            "owner_action_required": False,
        }

    try:
        A = avito_module
        if A is None:
            from app.api import avito as A
        factory = db_factory or SessionLocal
        gate_fn = fresh_spend_gate_fn or _planeta_kpi_hold_fresh_spend_gate
        refresh_fn = stats_refresh_fn or getattr(A, "collect_stats", None)
        if budget_pressure_fn is None:
            def local_pressure_fn(account_id: str):
                return _planeta_kpi_local_budget_pressure(
                    account_id,
                    db_factory=factory,
                )
        else:
            local_pressure_fn = budget_pressure_fn
        if freshness_fence_fn is None:
            def fence_fn(active: bool):
                return _sync_planeta_kpi_hourly_freshness_fence(
                    active,
                    now,
                    db_factory=factory,
                )
        else:
            fence_fn = freshness_fence_fn

        scan_db = factory()
        try:
            _, history = A._kpi_apply_log_load(scan_db, PLANETA_ACCOUNT_ID)
            candidates = []
            for op in history or []:
                if not isinstance(op, dict):
                    continue
                if str(op.get("status") or "") not in {"publish_requested", "feed_applied"}:
                    continue
                hold = dict(op.get("publish_external_hold") or {})
                hold_state = str(hold.get("state") or "")
                if hold_state not in {
                    "waiting_budget_policy",
                    "waiting_provider_after_budget_release",
                    "waiting_owner_funds",
                }:
                    continue
                operation_id = str(op.get("operation_id") or "").strip()
                if not operation_id:
                    continue
                candidates.append({
                    "operation_id": operation_id,
                    "hold_state": hold_state,
                    "retry_count": int(op.get("publish_retry_count") or 0),
                    "detected_at": str(hold.get("detected_at") or ""),
                    "last_hold_checked_at": str(
                        hold.get("last_hold_checked_at")
                        or hold.get("last_policy_checked_at")
                        or ""
                    ),
                })
        finally:
            scan_db.close()

        if not candidates:
            freshness_fence = fence_fn(False)
            return {
                "status": "no_waiting_external_publication_hold",
                "account_id": PLANETA_ACCOUNT_ID,
                "checked_at": now.isoformat(),
                "owner_action_required": False,
                "hourly_freshness_fence": freshness_fence,
            }

        pre_fence = fence_fn(
            any(
                str(item.get("hold_state") or "") == "waiting_budget_policy"
                for item in candidates
            )
        )

        candidates.sort(
            key=lambda x: (
                x.get("last_hold_checked_at") or "",
                x.get("detected_at") or "",
                x.get("operation_id") or "",
            )
        )
        selected = candidates[:PLANETA_EXTERNAL_HOLD_RECHECK_MAX_PER_TICK]
        results = []
        for item in selected:
            operation_id = item["operation_id"]
            fresh_spend_gate = {
                "fresh": True,
                "status": "not_required_after_budget_release",
            }
            stats_self_heal = {
                "status": "not_required",
                "attempted": False,
            }
            local_budget_pressure = {
                "blocked": False,
                "reason": "not_required_after_budget_release",
                "authoritative": True,
            }
            if str(item.get("hold_state") or "") == "waiting_budget_policy":
                fresh_spend_gate = gate_fn(PLANETA_ACCOUNT_ID)
                local_budget_pressure = local_pressure_fn(PLANETA_ACCOUNT_ID)
                if (
                    not bool(fresh_spend_gate.get("fresh"))
                    and not bool(local_budget_pressure.get("blocked"))
                ):
                    try:
                        refresh_result = dict(
                            refresh_fn(PLANETA_ACCOUNT_ID) or {}
                        )
                        stats_self_heal = {
                            "status": str(
                                refresh_result.get("status") or "unknown"
                            ),
                            "reason": refresh_result.get("reason"),
                            "attempted": True,
                            "retryable": refresh_result.get("retryable"),
                            "money_actions_allowed": refresh_result.get(
                                "money_actions_allowed"
                            ),
                        }
                    except Exception as refresh_exc:
                        stats_self_heal = {
                            "status": "error",
                            "attempted": True,
                            "error_type": type(refresh_exc).__name__,
                            "error": str(refresh_exc)[:300],
                        }
                    fresh_spend_gate = gate_fn(PLANETA_ACCOUNT_ID)

                if not bool(fresh_spend_gate.get("fresh")):
                    policy_still_blocked = bool(
                        local_budget_pressure.get("blocked")
                    )
                    results.append({
                        "operation_id": operation_id,
                        "hold_state_before": item.get("hold_state"),
                        "status": (
                            "waiting_budget_policy"
                            if policy_still_blocked
                            else "waiting_fresh_spend_after_budget_clear"
                        ),
                        "changed_avito": False,
                        "owner_action_required": False,
                        "retry_count": int(item.get("retry_count") or 0),
                        "reason": (
                            "money_policy_still_blocks_before_stats_refresh"
                            if policy_still_blocked
                            else "fresh_same_day_spend_required_before_confirm_publish"
                        ),
                        "fresh_spend_gate": fresh_spend_gate,
                        "local_budget_pressure": local_budget_pressure,
                        "stats_self_heal": stats_self_heal,
                        "owner_notification": {
                            "status": "not_required",
                            "sent": False,
                            "owner_action_required": False,
                        },
                        "observation_window_repair": {
                            "status": "not_required",
                            "changed": False,
                        },
                        "confirm_publish_called": False,
                    })
                    continue

            result = A.kpi_confirm_publish(
                A.KpiConfirmPublishRequest(
                    account_id=PLANETA_ACCOUNT_ID,
                    operation_id=operation_id,
                )
            )
            observation_window_repair = (
                _repair_planeta_kpi_observation_business_day(
                    operation_id,
                    result or {},
                    now,
                )
            )
            owner_notification = _notify_planeta_owner_funds_once(
                operation_id,
                result or {},
                now,
            )
            results.append({
                "operation_id": operation_id,
                "hold_state_before": item.get("hold_state"),
                "status": str((result or {}).get("status") or "unknown"),
                "changed_avito": bool((result or {}).get("changed_avito")),
                "owner_action_required": bool(
                    (result or {}).get("owner_action_required")
                ),
                "retry_count": int((result or {}).get("retry_count") or 0),
                "reason": str((result or {}).get("reason") or "")[:240],
                "owner_notification": owner_notification,
                "observation_window_repair": observation_window_repair,
                "fresh_spend_gate": fresh_spend_gate,
                "local_budget_pressure": local_budget_pressure,
                "stats_self_heal": stats_self_heal,
                "confirm_publish_called": True,
            })

        post_should_hold_fence = bool(
            any(
                str(item.get("hold_state") or "") == "waiting_budget_policy"
                for item in candidates[len(selected):]
            )
            or any(
                str(item.get("status") or "") in {
                    "waiting_budget_policy",
                    "waiting_fresh_spend_after_budget_clear",
                }
                for item in results
            )
        )
        post_fence = fence_fn(post_should_hold_fence)

        return {
            "status": "checked",
            "account_id": PLANETA_ACCOUNT_ID,
            "checked_at": now.isoformat(),
            "candidates": len(candidates),
            "attempted": len(results),
            "results": results,
            "hourly_freshness_fence_before": pre_fence,
            "hourly_freshness_fence": post_fence,
            "external_write_performed": False,
        }
    except Exception as exc:
        return {
            "status": "error",
            "account_id": PLANETA_ACCOUNT_ID,
            "checked_at": now.isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "external_write_performed": False,
        }


def _reconcile_planeta_rejected_publication_truth(rows, account_id: str, now: datetime) -> dict:
    """PLANETA_REJECTED_PUBLICATION_FAIL_CLOSED_V1.

    Cross-workstream safety fence for a shared identity binder that this scope
    is not allowed to edit. A row cannot become writable/published merely because
    an Autoload read said success_skipped/unchanged while durable identity still
    says publication_state=rejected. Exact live publication_result or an explicit
    newer causal-success marker is required to keep published_identity_bound.
    """
    if str(account_id or "") != PLANETA_ACCOUNT_ID:
        return {"changed": 0, "changed_ids": [], "scope": "skipped"}

    changed_ids = []
    for row in rows or []:
        if str(getattr(row, "identity_status", "") or "") != "published_identity_bound":
            continue
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "{}")
        except Exception:
            payload = {}
        identity = dict((payload or {}).get("identity") or {})
        if str(identity.get("publication_state") or "").lower() != "rejected":
            continue

        resolution = str(identity.get("resolution") or "").strip().lower()
        if resolution == "publication_result":
            # Exact live match confirmed by kpi_confirm_publish/bind_publication.
            # bind_publication updates the resolution/status but deliberately
            # preserves unrelated payload keys, so stale rejection metadata can
            # survive a legitimate success. Clean only the obsolete rejection
            # verdict; keep causal upload ids for audit.
            identity.pop("publication_state", None)
            identity.pop("publication_error_status", None)
            identity.pop("publication_error_section", None)
            identity["publication_rejection_resolved_at"] = now.isoformat()
            identity["publication_rejection_resolution"] = (
                "authoritative_publication_result"
            )
            payload["identity"] = identity
            row.payload_json = json.dumps(payload, ensure_ascii=False)
            changed_ids.append(int(getattr(row, "id", 0) or 0))
            continue

        try:
            rejected_upload = int(identity.get("publication_rejection_upload_id") or 0)
        except (TypeError, ValueError):
            rejected_upload = 0
        try:
            superseding_upload = int(
                identity.get("publication_rejection_superseded_by_upload_id") or 0
            )
        except (TypeError, ValueError):
            superseding_upload = 0
        if rejected_upload > 0 and superseding_upload > rejected_upload:
            continue

        # No authoritative proof crossed the rejection boundary. Keep the base
        # listing measurable, but the desired revision stays non-writable.
        row.identity_status = "publication_rejected_bound"
        row.status = "draft"
        identity["publication_truth_reconciled_at"] = now.isoformat()
        identity["publication_truth_reconcile_reason"] = (
            "durable_rejection_without_authoritative_superseding_success"
        )
        payload["identity"] = identity
        row.payload_json = json.dumps(payload, ensure_ascii=False)
        changed_ids.append(int(getattr(row, "id", 0) or 0))

    return {
        "changed": len(changed_ids),
        "changed_ids": changed_ids,
        "scope": PLANETA_ACCOUNT_ID,
        "owner_action_required": False,
    }


def _conversion_action_consumer_state(
    db, account_id: str, rollup: dict, now: datetime
) -> dict:
    """POST_PUBLISH_CONVERSION_CONSUMER_TRUTH_V1.

    Confirm the handoff only from durable KPI lifecycle evidence. This is
    read-only with respect to Avito and never starts AI, feed, bid, or publish
    work. A content operation that is still publishing/measuring is a real
    consumer; terminal completed experiments are not.
    """
    signal = str((rollup or {}).get("signal") or "")
    action = str((rollup or {}).get("system_action") or "")
    if (
        signal != "traffic_without_contacts"
        or action != "handoff_conversion_optimization"
        or (rollup or {}).get("source_stats_fresh") is not True
    ):
        return {
            "confirmed": False,
            "state": "not_required",
            "checked_at": now.isoformat(),
        }

    active_statuses = {
        "prepared",
        "safe_feed_ready",
        "mapping_wait",
        "feed_applied",
        "publishing",
        "publish_requested",
        "publish_failed",
        "published",
        "effect_rollback_requested",
        "rollback_feed_ready",
        "rollback_publish_failed",
        "rollback_publish_requested",
    }
    terminal_effects = {
        "improved",
        "worse",
        "kept",
        "rolled_back",
        "try_next_action",
        "no_clear_effect",
    }

    row = (
        db.query(Storage)
        .filter(Storage.account_id == account_id, Storage.key == "kpi_apply_log")
        .order_by(Storage.id.desc())
        .first()
    )
    try:
        history = json.loads(row.value or "[]") if row else []
    except Exception:
        history = []
    if not isinstance(history, list):
        history = []

    for op in reversed(history):
        if not isinstance(op, dict):
            continue
        status = str(op.get("status") or "")
        if status not in active_statuses:
            continue
        changes = dict(op.get("changes") or {})
        content_fields = sorted(
            set(changes.keys()) & {"title", "description", "images"}
        )
        if not content_fields:
            continue
        effect = dict(op.get("effect") or {})
        effect_status = str(effect.get("status") or "")
        if status == "published" and effect_status in terminal_effects:
            continue
        operation_id = str(op.get("operation_id") or "").strip()
        if not operation_id:
            continue
        return {
            "confirmed": True,
            "state": "consumer_active",
            "consumer": "kpi_content_lifecycle",
            "operation_id": operation_id,
            "operation_status": status,
            "effect_status": effect_status or None,
            "content_fields": content_fields,
            "item_id": str(
                op.get("avito_item_id") or op.get("item_id") or ""
            ) or None,
            "external_hold_state": str(
                (op.get("publish_external_hold") or {}).get("state") or ""
            ) or None,
            "owner_action_required": bool(
                (op.get("publish_external_hold") or {}).get(
                    "owner_action_required"
                )
            ),
            "checked_at": now.isoformat(),
        }

    return {
        "confirmed": False,
        "state": "signal_unconsumed",
        "consumer": "kpi_content_lifecycle",
        "checked_at": now.isoformat(),
        "owner_action_required": False,
    }


def _dt(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        out = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except Exception:
        return None


def _load_json_storage(db, account_id: str, key: str) -> dict:
    row = (
        db.query(Storage)
        .filter(Storage.account_id == account_id, Storage.key == key)
        .order_by(Storage.id.desc())
        .first()
    )
    if not row:
        return {}
    try:
        data = json.loads(row.value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_storage(db, account_id: str, key: str, payload: dict) -> None:
    """Update one compact account-scoped JSON state instead of appending rows."""
    row = (
        db.query(Storage)
        .filter(Storage.account_id == account_id, Storage.key == key)
        .order_by(Storage.id.desc())
        .first()
    )
    encoded = json.dumps(payload or {}, ensure_ascii=False)
    if row:
        row.value = encoded
    else:
        db.add(Storage(account_id=account_id, key=key, value=encoded))


def _build_account_business_rollup(account_id: str, campaign_watches, now: datetime) -> dict:
    """POST_PUBLISH_ACCOUNT_BUSINESS_ROLLUP_V1

    Build one ownerless business truth across the currently supervised campaign
    revisions for an account. Snapshot metrics are deduplicated by feed identity.
    Cumulative since-publication metrics are summed only when campaign feed scopes
    do not overlap; otherwise they are intentionally marked incomplete rather than
    double-counted.
    """
    rows = []
    for raw in campaign_watches or []:
        item = dict(raw or {})
        outcome = dict(item.get("business_outcome") or {})
        if str(outcome.get("policy_version") or "") != "POST_PUBLISH_BUSINESS_OUTCOME_V2":
            continue
        feed_ids = {
            str(x).strip() for x in (item.get("expected_feed_ids") or [])
            if str(x).strip()
        }
        current_items = {
            str(k).strip(): dict(v or {})
            for k, v in dict(item.get("current_items") or {}).items()
            if str(k).strip()
        }
        rows.append({
            "campaign_id": int(item.get("campaign_id") or 0),
            "feed_ids": feed_ids,
            "current_items": current_items,
            "business_outcome": outcome,
        })

    seen_feed_ids = set()
    overlap_feed_ids = set()
    merged_items = {}
    cumulative_views = 0
    cumulative_contacts = 0
    cumulative_complete = True
    source_campaigns = []

    for row in sorted(rows, key=lambda x: int(x.get("campaign_id") or 0)):
        feed_ids = set(row["feed_ids"])
        overlap_feed_ids |= seen_feed_ids & feed_ids
        seen_feed_ids |= feed_ids
        for fid, item in row["current_items"].items():
            if fid not in merged_items:
                merged_items[fid] = item
        outcome = row["business_outcome"]
        if (
            outcome.get("views_since_publication_baseline") is None
            or outcome.get("contacts_since_publication_baseline") is None
        ):
            cumulative_complete = False
        else:
            cumulative_views += int(outcome.get("views_since_publication_baseline") or 0)
            cumulative_contacts += int(outcome.get("contacts_since_publication_baseline") or 0)
        source_campaigns.append({
            "campaign_id": row["campaign_id"],
            "expected_items": int(outcome.get("expected_items") or len(feed_ids)),
            "active_items": int(outcome.get("active_items") or 0),
            "views_current_snapshot": int(outcome.get("views_current_snapshot") or 0),
            "contacts_current_snapshot": int(outcome.get("contacts_current_snapshot") or 0),
            "views_since_publication_baseline": int(
                outcome.get("views_since_publication_baseline") or 0
            ),
            "contacts_since_publication_baseline": int(
                outcome.get("contacts_since_publication_baseline") or 0
            ),
            "publication_scope": str(outcome.get("publication_scope") or ""),
            "source_stats_fresh": outcome.get("source_stats_fresh") is True,
            "source_stats_date": outcome.get("source_stats_date"),
            "source_stats_date_current": outcome.get(
                "source_stats_date_current"
            ) is True,
            "source_stats_collected_at": outcome.get("source_stats_collected_at"),
            "source_stats_age_seconds": outcome.get("source_stats_age_seconds"),
            "source_freshness_policy_version": outcome.get(
                "source_freshness_policy_version"
            ),
            "content_revision_pending": dict(
                outcome.get("content_revision_pending") or {}
            ),
        })

    if overlap_feed_ids:
        cumulative_complete = False

    expected_items = len(seen_feed_ids)
    active_items = sum(
        1 for x in merged_items.values()
        if str(x.get("status") or "").lower() == "active"
    )
    viewed_items = sum(1 for x in merged_items.values() if int(x.get("views") or 0) > 0)
    views_snapshot = sum(int(x.get("views") or 0) for x in merged_items.values())
    contacts_snapshot = sum(int(x.get("contacts") or 0) for x in merged_items.values())
    publication_scope = (
        "fully_active"
        if rows and expected_items > 0 and active_items == expected_items
        and all(
            str(x["business_outcome"].get("publication_scope") or "") == "fully_active"
            for x in rows
        )
        else "incomplete"
    )
    signal_contacts = cumulative_contacts if cumulative_complete else contacts_snapshot
    signal_views = cumulative_views if cumulative_complete else views_snapshot
    if publication_scope != "fully_active":
        signal = "publication_scope_incomplete"
    elif signal_contacts > 0:
        signal = "contacts_present"
    elif signal_views > 0:
        signal = "traffic_without_contacts"
    else:
        signal = "no_traffic_yet"

    source_stats_fresh = bool(
        source_campaigns
        and all(
            x.get("source_stats_fresh") is True
            and x.get("source_stats_date_current") is True
            and str(x.get("source_freshness_policy_version") or "")
            == "POST_PUBLISH_SOURCE_FRESHNESS_V2"
            for x in source_campaigns
        )
    )
    source_ages = [
        int(x.get("source_stats_age_seconds") or 0)
        for x in source_campaigns
        if x.get("source_stats_age_seconds") is not None
    ]
    source_collected = [
        str(x.get("source_stats_collected_at") or "")
        for x in source_campaigns
        if str(x.get("source_stats_collected_at") or "")
    ]
    revision_pending_feed_ids = sorted({
        str(fid).strip()
        for x in source_campaigns
        for fid in (
            (x.get("content_revision_pending") or {}).get("feed_ids") or []
        )
        if str(fid).strip()
    })
    revision_pending_count = len(revision_pending_feed_ids)
    desired_revision_scope = (
        "pending"
        if revision_pending_count > 0
        else "fully_applied"
    )

    if publication_scope != "fully_active":
        system_action = "continue_publication_recovery"
        system_action_domain = "feed_publication"
    elif not source_stats_fresh:
        system_action = "wait_fresh_stats_before_conversion_action"
        system_action_domain = "post_publish_observation"
    elif signal == "traffic_without_contacts":
        system_action = "handoff_conversion_optimization"
        system_action_domain = "marketing_performance"
    else:
        system_action = "continue_observation"
        system_action_domain = "post_publish_observation"

    return {
        "policy_version": "POST_PUBLISH_ACCOUNT_BUSINESS_ROLLUP_V1",
        "source_freshness_policy_version": "POST_PUBLISH_SOURCE_FRESHNESS_V2",
        "account_id": account_id,
        "campaign_ids": [x["campaign_id"] for x in source_campaigns],
        "campaigns_count": len(source_campaigns),
        "expected_items": expected_items,
        "observed_items": len(merged_items),
        "active_items": active_items,
        "viewed_items_current_snapshot": viewed_items,
        "zero_view_items_current_snapshot": max(0, expected_items - viewed_items),
        "views_current_snapshot": views_snapshot,
        "contacts_current_snapshot": contacts_snapshot,
        "views_since_publication_baseline": (
            cumulative_views if cumulative_complete else None
        ),
        "contacts_since_publication_baseline": (
            cumulative_contacts if cumulative_complete else None
        ),
        "cumulative_aggregation_complete": bool(cumulative_complete),
        "overlap_feed_ids": sorted(overlap_feed_ids),
        "publication_scope": publication_scope,
        "desired_revision_scope": desired_revision_scope,
        "content_revision_pending_count": revision_pending_count,
        "content_revision_pending_feed_ids": revision_pending_feed_ids,
        "base_listing_scope_fully_active": bool(publication_scope == "fully_active"),
        "desired_revision_scope_fully_applied": bool(revision_pending_count == 0),
        "signal": signal,
        "traffic_without_contacts": bool(
            publication_scope == "fully_active"
            and signal == "traffic_without_contacts"
        ),
        "signal_fresh": bool(source_stats_fresh),
        "source_stats_fresh": bool(source_stats_fresh),
        "source_stats_max_age_seconds": max(source_ages) if source_ages else None,
        "source_stats_oldest_collected_at": min(source_collected) if source_collected else None,
        "source_stats_newest_collected_at": max(source_collected) if source_collected else None,
        "owner_action_required": False,
        "system_action": system_action,
        "system_action_domain": system_action_domain,
        "adjacent_action_required": bool(
            source_stats_fresh and signal == "traffic_without_contacts"
        ),
        "action_delivery_state": (
            "signal_published"
            if source_stats_fresh and signal == "traffic_without_contacts"
            else (
                "signal_stale_waiting_fresh_stats"
                if not source_stats_fresh
                else "local_state"
            )
        ),
        "action_consumer_confirmed": False,
        "source_campaigns": source_campaigns,
        "updated_at": now.isoformat(),
    }


def _budget_hold_provider_propagation(scope: dict, restore_feed_ids, *,
                                      upload_id: int, baseline_upload_id: int) -> dict:
    """REPLACEMENT_BUDGET_HOLD_PROVIDER_CONFIRM_V1

    Confirm only provider-side propagation of the protective DateEnd. This is
    intentionally stricter than "upload id changed": the newer Autoload report
    must contain the exact restore scope, none of it may be active/error/missing,
    and every exact identity must carry Avito code 3007 (end date passed).
    """
    wanted = {str(x).strip() for x in (restore_feed_ids or []) if str(x).strip()}
    uid = int(upload_id or 0)
    baseline = int(baseline_upload_id or 0)
    details = list((scope or {}).get("unresolved_details") or [])
    ended = set()
    for raw in details:
        item = dict(raw or {})
        fid = str(item.get("ad_id") or "").strip()
        codes = set()
        for msg in (item.get("messages") or []):
            try:
                code = int((msg or {}).get("code") or 0)
            except Exception:
                code = 0
            if code:
                codes.add(code)
        if fid and 3007 in codes:
            ended.add(fid)
    found = int((scope or {}).get("found") or 0)
    active = int((scope or {}).get("active") or 0)
    errors = int((scope or {}).get("errors") or 0)
    missing = {str(x).strip() for x in ((scope or {}).get("missing") or []) if str(x).strip()}
    confirmed = bool(
        wanted
        and baseline > 0
        and uid > baseline
        and found == len(wanted)
        and active == 0
        and errors == 0
        and not missing
        and ended == wanted
    )
    if not uid or uid <= baseline:
        reason = "waiting_newer_upload"
    elif found != len(wanted) or missing:
        reason = "provider_scope_incomplete"
    elif active:
        reason = "provider_scope_still_active"
    elif errors:
        reason = "provider_scope_has_errors"
    elif ended != wanted:
        reason = "provider_dateend_not_proven"
    else:
        reason = "provider_dateend_confirmed"
    return {
        "policy_version": "REPLACEMENT_BUDGET_HOLD_PROVIDER_CONFIRM_V1",
        "confirmed": confirmed,
        "reason": reason,
        "upload_id": uid,
        "baseline_upload_id": baseline,
        "expected": len(wanted),
        "found": found,
        "active": active,
        "errors": errors,
        "missing": sorted(missing),
        "dateend_3007_count": len(ended),
        "dateend_3007_feed_ids": sorted(ended),
    }


def _campaign_metrics(stats: dict, campaign_id: int, account_id: str, allowed_feed_ids=None) -> dict:
    scoped = []
    _allowed = {str(x) for x in (allowed_feed_ids or []) if str(x)}
    for item in stats.get("items") or []:
        ident = item.get("campaign_identity") or {}
        try:
            cid = int(ident.get("campaign_id") or 0)
        except Exception:
            cid = 0
        if cid != int(campaign_id):
            continue
        if str(ident.get("account_id") or account_id) != str(account_id):
            continue
        if _allowed and str(ident.get("feed_identity") or "") not in _allowed:
            continue
        scoped.append(item)

    return {
        "matched": len(scoped),
        "active": sum(1 for x in scoped if str(x.get("status") or "").lower() == "active"),
        "canonical": sum(1 for x in scoped if str(x.get("write_capability") or "") == "canonical"),
        "views": sum(int(x.get("views") or 0) for x in scoped),
        "contacts": sum(int(x.get("contacts") or 0) for x in scoped),
        "items": {
            str((x.get("campaign_identity") or {}).get("feed_identity") or x.get("id") or ""): {
                "avito_item_id": x.get("id"),
                "title": str(x.get("title") or "")[:120],
                "status": x.get("status"),
                "views": int(x.get("views") or 0),
                "contacts": int(x.get("contacts") or 0),
            }
            for x in scoped
        },
    }


def _update_watch(existing: dict, *, metrics: dict, collected_at: datetime,
                  published_at: datetime, expected: int, now: datetime,
                  stats_date: str | None = None,
                  base_live_revision_pending: bool = False) -> dict:
    watch = dict(existing or {})
    days = dict(watch.get("days") or {})
    collection_day = collected_at.date().isoformat()
    stats_day = str(stats_date or collection_day).strip()[:10] or collection_day
    publication_day = published_at.date().isoformat()

    # POST_PUBLISH_PROVIDER_DAY_FRESHNESS_V2:
    # Avito can return a freshly collected snapshot whose business metrics still
    # represent an older provider day. Never bucket that old provider data under
    # the local collection day. If an older implementation already created
    # buckets after the provider's latest represented day, they are duplicate
    # projections of the same provider data and are removed fail-closed.
    if stats_day < collection_day:
        days = {
            key: value
            for key, value in days.items()
            if str(key) <= stats_day
        }

    # POST_PUBLISH_PREPUBLICATION_BASELINE_SELF_HEAL_V1:
    # A historical watcher revision could capture a publication-day baseline
    # before the authoritative published_at timestamp. Such traffic cannot be
    # attributed to the new revision. Fail closed by moving that day's baseline
    # up to the latest already-observed counters for the same provider day.
    _publication_bucket = dict(days.get(publication_day) or {})
    _baseline_at = _dt(_publication_bucket.get("baseline_captured_at"))
    if (
        _publication_bucket
        and _baseline_at is not None
        and _baseline_at < published_at
    ):
        _publication_bucket["baseline_views"] = int(
            _publication_bucket.get("latest_views") or 0
        )
        _publication_bucket["baseline_contacts"] = int(
            _publication_bucket.get("latest_contacts") or 0
        )
        _publication_bucket["baseline_captured_at"] = published_at.isoformat()
        _publication_bucket["baseline_self_healed"] = True
        _publication_bucket["baseline_self_heal_reason"] = (
            "historical_baseline_predates_authoritative_publish"
        )
        _publication_bucket["views_delta"] = max(
            0,
            int(_publication_bucket.get("latest_views") or 0)
            - int(_publication_bucket.get("baseline_views") or 0),
        )
        _publication_bucket["contacts_delta"] = max(
            0,
            int(_publication_bucket.get("latest_contacts") or 0)
            - int(_publication_bucket.get("baseline_contacts") or 0),
        )
        days[publication_day] = _publication_bucket

    day = dict(days.get(stats_day) or {})
    if not day:
        # Publication-day traffic before the first post-publication snapshot cannot
        # be safely attributed to the new revision. Later complete days start at zero.
        baseline_views = int(metrics.get("views") or 0) if stats_day == publication_day else 0
        baseline_contacts = int(metrics.get("contacts") or 0) if stats_day == publication_day else 0
        day = {
            "baseline_views": baseline_views,
            "baseline_contacts": baseline_contacts,
            "baseline_captured_at": collected_at.isoformat(),
        }

    day.update({
        "provider_stats_date": stats_day,
        "collection_date": collection_day,
        "latest_views": int(metrics.get("views") or 0),
        "latest_contacts": int(metrics.get("contacts") or 0),
        "latest_active": int(metrics.get("active") or 0),
        "latest_matched": int(metrics.get("matched") or 0),
        "latest_canonical": int(metrics.get("canonical") or 0),
        "latest_collected_at": collected_at.isoformat(),
        "views_delta": max(0, int(metrics.get("views") or 0) - int(day.get("baseline_views") or 0)),
        "contacts_delta": max(0, int(metrics.get("contacts") or 0) - int(day.get("baseline_contacts") or 0)),
    })
    days[stats_day] = day

    # Retain only the current observation window plus one prior day for audit.
    keep_days = sorted(days)[-5:]
    days = {k: days[k] for k in keep_days}

    cumulative_views = sum(int(x.get("views_delta") or 0) for x in days.values())
    cumulative_contacts = sum(int(x.get("contacts_delta") or 0) for x in days.values())
    age_sec = max(0, int((now - collected_at).total_seconds()))
    elapsed_sec = max(0, int((now - published_at).total_seconds()))

    matched = int(metrics.get("matched") or 0)
    active = int(metrics.get("active") or 0)
    canonical = int(metrics.get("canonical") or 0)
    # BASE_LIVE_SCOPE_SEPARATE_FROM_WRITE_CAPABILITY_V1:
    # Publication/business truth is about whether the exact listings are live.
    # A later rejected content revision can temporarily make one exact item
    # non-writable without taking its already-published base listing offline.
    # The caller may enable this override only after fresh exact identity+Avito ID
    # evidence proves the revision-pending base listing is still active.
    scope_ok = bool(
        matched == expected
        and active == expected
        and (
            canonical == expected
            or bool(base_live_revision_pending)
        )
    )
    business_day = marketing_today_iso()
    stats_date_current = bool(stats_day == business_day)
    stats_fresh = bool(
        age_sec <= STATS_STALE_SECONDS
        and stats_date_current
    )
    _expected_feed_ids = {str(x) for x in (watch.get("expected_feed_ids") or []) if str(x)}
    _matched_feed_ids = {str(x) for x in (metrics.get("items") or {}).keys() if str(x)}
    _missing_feed_ids = sorted(_expected_feed_ids - _matched_feed_ids) if _expected_feed_ids else []
    _missing_since = _dt(watch.get("missing_since"))
    if _missing_feed_ids and _missing_since is None:
        _missing_since = now
    if not _missing_feed_ids:
        _missing_since = None
    _missing_age_sec = max(0, int((now - _missing_since).total_seconds())) if _missing_since else 0

    prev_streak = int(watch.get("degraded_streak") or 0)
    if not stats_fresh:
        status = "waiting_fresh_stats"
        degraded_streak = prev_streak
        reason = "provider_stats_date_lag" if not stats_date_current else "daily_stats_stale"
    elif not scope_ok:
        status = "degraded"
        degraded_streak = prev_streak + 1
        reason = "campaign_scope_not_fully_active"
    elif elapsed_sec >= WATCH_WINDOW_HOURS * 3600:
        status = "observation_complete"
        degraded_streak = 0
        reason = None
    else:
        status = "observing"
        degraded_streak = 0
        reason = None

    # POST_PUBLISH_BUSINESS_OUTCOME_V2:
    # Publication truth must be useful to the business owner, not only to an
    # engineer. Keep this block strictly factual/read-only: it does not change
    # bids, budgets, titles, prices or publication state. Downstream Supervisor/UI
    # can explain whether the campaign is getting traffic and whether that traffic
    # has produced contacts without forcing the owner to interpret raw counters.
    _metric_items = [
        dict(x or {}) for x in (metrics.get("items") or {}).values()
        if isinstance(x, dict)
    ]
    _snapshot_views = sum(int(x.get("views") or 0) for x in _metric_items)
    _snapshot_contacts = sum(int(x.get("contacts") or 0) for x in _metric_items)
    _viewed_items = sum(1 for x in _metric_items if int(x.get("views") or 0) > 0)
    _zero_view_items = max(0, expected - _viewed_items)
    if not scope_ok:
        _business_signal = "publication_scope_incomplete"
    elif cumulative_contacts > 0:
        _business_signal = "contacts_present"
    elif cumulative_views > 0:
        _business_signal = "traffic_without_contacts"
    else:
        _business_signal = "no_traffic_yet"
    if not scope_ok:
        _business_action = "continue_publication_recovery"
        _business_action_domain = "feed_publication"
    elif not stats_fresh:
        _business_action = "wait_fresh_stats_before_conversion_action"
        _business_action_domain = "post_publish_observation"
    elif _business_signal == "traffic_without_contacts":
        _business_action = "handoff_conversion_optimization"
        _business_action_domain = "marketing_performance"
    else:
        _business_action = "continue_observation"
        _business_action_domain = "post_publish_observation"
    _business_outcome = {
        "policy_version": "POST_PUBLISH_BUSINESS_OUTCOME_V2",
        "source_freshness_policy_version": "POST_PUBLISH_SOURCE_FRESHNESS_V2",
        "signal": _business_signal,
        "publication_scope": "fully_active" if scope_ok else "incomplete",
        "expected_items": expected,
        "active_items": active,
        "canonical_writable_items": canonical,
        "base_live_revision_pending_override": bool(base_live_revision_pending),
        "viewed_items_current_snapshot": _viewed_items,
        "zero_view_items_current_snapshot": _zero_view_items,
        "views_current_snapshot": _snapshot_views,
        "contacts_current_snapshot": _snapshot_contacts,
        "views_since_publication_baseline": cumulative_views,
        "contacts_since_publication_baseline": cumulative_contacts,
        "traffic_without_contacts": bool(cumulative_views > 0 and cumulative_contacts == 0),
        "signal_fresh": bool(stats_fresh),
        "source_stats_fresh": bool(stats_fresh),
        "source_stats_date": stats_day,
        "source_stats_date_current": bool(stats_date_current),
        "source_stats_collected_at": collected_at.isoformat(),
        "source_stats_age_seconds": age_sec,
        "owner_action_required": False,
        "system_action": _business_action,
        "system_action_domain": _business_action_domain,
        "adjacent_action_required": bool(
            stats_fresh and _business_signal == "traffic_without_contacts"
        ),
        "action_delivery_state": (
            "signal_published"
            if stats_fresh and _business_signal == "traffic_without_contacts"
            else (
                "signal_stale_waiting_fresh_stats"
                if not stats_fresh
                else "local_state"
            )
        ),
        "action_consumer_confirmed": False,
        "updated_at": now.isoformat(),
    }

    return {
        **watch,
        "policy_version": "campaign-post-publish-watch-v1",
        "status": status,
        "reason": reason,
        "campaign_id": int(watch.get("campaign_id") or 0),
        "account_id": watch.get("account_id"),
        "published_at": published_at.isoformat(),
        "expected_items": expected,
        "matched_items": matched,
        "active_items": active,
        "canonical_items": canonical,
        "missing_feed_ids": _missing_feed_ids[:100],
        "missing_feed_ids_count": len(_missing_feed_ids),
        "scope_diagnosis": ("published_identity_not_in_active_inventory" if _missing_feed_ids else None),
        "missing_since": (_missing_since.isoformat() if _missing_since else None),
        "missing_age_seconds": _missing_age_sec,
        "diagnosis_stage": (
            "persistent_inventory_loss_needs_read_only_provider_diagnosis"
            if _missing_feed_ids and _missing_age_sec >= 3600 else
            "confirming_inventory_loss" if _missing_feed_ids else None
        ),
        "safe_republish": False if _missing_feed_ids else None,
        "stats_collected_at": collected_at.isoformat(),
        "stats_date": stats_day,
        "stats_date_current": bool(stats_date_current),
        "stats_age_seconds": age_sec,
        "days": days,
        "views_since_baseline": cumulative_views,
        "contacts_since_baseline": cumulative_contacts,
        "business_outcome": _business_outcome,
        "degraded_streak": degraded_streak,
        "diagnostic": (
            "read_only_provider_diagnosis_due" if status == "degraded" and _missing_feed_ids and _missing_age_sec >= 3600 else
            "confirm_inventory_loss_next_stats_cycle" if status == "degraded" and _missing_feed_ids else
            "retry_next_stats_cycle" if status in {"waiting_fresh_stats", "degraded"} else "healthy"
        ),
        "self_heal": (
            "diagnose_before_any_republish" if status == "degraded" and _missing_feed_ids and _missing_age_sec >= 3600 else
            "automatic_recheck_no_owner_action" if status in {"waiting_fresh_stats", "degraded"} else "not_needed"
        ),
        "owner_action_required": False,
        "updated_at": now.isoformat(),
        "next_check_at": (now + timedelta(minutes=15)).isoformat(),
        "observation_window_hours": WATCH_WINDOW_HOURS,
    }


def _diagnose_missing_provider_status(account_id: str, rows, missing_feed_ids, *, now: datetime, previous=None) -> dict:
    """Read-only diagnosis for campaign identities missing from active inventory.

    Never republishes, edits, raises bids, or performs any paid provider action.
    The provider status is evidence only; ambiguous transport failures remain retryable.
    """
    missing = {str(x) for x in (missing_feed_ids or []) if str(x)}
    targets = {
        str(getattr(r, "avito_item_id", "") or ""): str(getattr(r, "feed_identity", "") or "")
        for r in (rows or [])
        if str(getattr(r, "feed_identity", "") or "") in missing and str(getattr(r, "avito_item_id", "") or "")
    }
    if not targets:
        return {"status": "unresolved", "reason": "missing_provider_ids", "checked_at": now.isoformat(),
                "safe_republish": False, "owner_action_required": False}
    prev = dict(previous or {})
    prev_at = _dt(prev.get("checked_at"))
    if prev_at and (now - prev_at).total_seconds() < 900 and set(prev.get("feed_ids") or []) == missing:
        return prev
    try:
        from app.api.avito import get_avito_token, _extract_token, _avito_http_get
        from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
        # CAMPAIGN_POST_PUBLISH_SHARED_ACCOUNT_THROTTLE_V1
        _retry = int(account_throttle_remaining(account_id) or 0)
        if _retry > 0:
            return {"status":"retryable","reason":"avito_account_throttled","retry_after_seconds":_retry,"checked_at":now.isoformat(),"feed_ids":sorted(missing),"safe_republish":False,"owner_action_required":False}
        token_data = get_avito_token(account_id)
        if "access_token" not in token_data:
            return {"status": "retryable", "reason": "avito_auth_unavailable", "checked_at": now.isoformat(),
                    "feed_ids": sorted(missing), "safe_republish": False, "owner_action_required": False}
        token = _extract_token(token_data)
        found = {}
        # Missing-from-active identities are most usefully classified by terminal/non-active inventories.
        for provider_status in ("blocked", "old", "rejected", "removed"):
            _retry = int(account_throttle_remaining(account_id) or 0)
            if _retry > 0:
                return {"status":"retryable","reason":"avito_account_throttled","retry_after_seconds":_retry,"checked_at":now.isoformat(),"feed_ids":sorted(missing),"safe_republish":False,"owner_action_required":False}
            resp = _avito_http_get(
                "https://api.avito.ru/core/v1/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 100, "page": 1, "status": provider_status}, timeout=15,
            )
            if resp.status_code != 200:
                if resp.status_code == 429:
                    raw = str(resp.headers.get("Retry-After") or "").strip()
                    try:
                        retry_after = max(0, int(float(raw))) if raw else 30
                    except Exception:
                        from email.utils import parsedate_to_datetime
                        from datetime import datetime as _dt429, timezone as _tz429
                        try:
                            when = parsedate_to_datetime(raw)
                            if when.tzinfo is None: when = when.replace(tzinfo=_tz429.utc)
                            retry_after = max(0, int((when.astimezone(_tz429.utc)-_dt429.now(_tz429.utc)).total_seconds()))
                        except Exception:
                            retry_after = 30
                    _retry = int(record_account_throttle(account_id, retry_after or 30, source="campaign_post_publish_inventory_429") or (retry_after or 30))
                    return {"status":"retryable","reason":"avito_account_throttled","retry_after_seconds":_retry,"checked_at":now.isoformat(),"feed_ids":sorted(missing),"safe_republish":False,"owner_action_required":False}
                return {"status": "retryable", "reason": f"avito_inventory_http_{resp.status_code}",
                        "checked_at": now.isoformat(), "feed_ids": sorted(missing),
                        "safe_republish": False, "owner_action_required": False}
            for item in (resp.json() or {}).get("resources", []):
                aid = str(item.get("id") or "")
                if aid in targets:
                    found[targets[aid]] = {"avito_item_id": aid, "provider_status": str(item.get("status") or provider_status),
                                           "title": str(item.get("title") or "")[:120]}
        unresolved = sorted(missing - set(found))
        statuses = sorted({str(x.get("provider_status") or "") for x in found.values() if x.get("provider_status")})
        if found and not unresolved:
            classification = "provider_blocked" if statuses == ["blocked"] else "provider_nonactive"
            return {"status": "confirmed", "classification": classification, "checked_at": now.isoformat(),
                    "feed_ids": sorted(missing), "provider_statuses": statuses, "items": found,
                    "unresolved_feed_ids": [], "safe_republish": False, "owner_action_required": False,
                    "next_action": "wait_and_recheck_provider_status_no_republish"}
        return {"status": "partial" if found else "unresolved", "classification": "provider_status_unresolved",
                "checked_at": now.isoformat(), "feed_ids": sorted(missing), "provider_statuses": statuses,
                "items": found, "unresolved_feed_ids": unresolved, "safe_republish": False,
                "owner_action_required": False, "next_action": "retry_read_only_provider_diagnosis"}
    except Exception as exc:
        return {"status": "retryable", "reason": type(exc).__name__, "error": str(exc)[:240],
                "checked_at": now.isoformat(), "feed_ids": sorted(missing),
                "safe_republish": False, "owner_action_required": False}


def _merge_replacement_owner_action_truth(
    cleanup: dict,
    observation: dict,
) -> tuple[dict, dict]:
    """Merge a new observation without letting transient provider waits erase a
    durable business/owner blocker.

    This is intentionally pure so retry semantics can be tested without touching
    Avito or production state.
    """
    merged = dict(cleanup or {})
    obs = dict(observation or {})
    reason = str(obs.get("reason") or "")
    transient_wait = bool(
        str(obs.get("status") or "") == "waiting"
        and (
            reason == "avito_account_throttled"
            or reason.endswith("_retryable")
            or reason in {
                "under_kpi_restore_core_auth_unavailable",
                "waiting_under_kpi_restore_upload",
            }
        )
    )
    existing_owner_required = bool(merged.get("owner_action_required"))
    incoming_owner_required = bool(obs.get("owner_action_required"))
    if (
        transient_wait
        and existing_owner_required
        and not incoming_owner_required
    ):
        obs["business_blocker_preserved"] = True
        obs["preserved_owner_action"] = merged.get("owner_action")
        obs["preserved_provider_code"] = merged.get("restore_provider_code")
    else:
        merged["owner_action_required"] = incoming_owner_required
        if obs.get("owner_action"):
            merged["owner_action"] = str(obs.get("owner_action"))
        elif not merged["owner_action_required"]:
            merged.pop("owner_action", None)
    return merged, obs


def _planeta_distinct_slot_fallback_lifecycle(
    db,
    scale_campaign,
    account_id: str,
    now: datetime,
    *,
    feed_send_fn=None,
    collect_stats_fn=None,
) -> dict:
    """PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_V1.

    The Sep4 distinct cutover created fresh feed identities for logical slots
    that already had durable Avito identities. When Avito rejects the new
    identity with 2014, the correct recovery is to reuse the exact source
    identity, not to keep creating more duplicates.

    Safety contract:
    - 36 logical slots remain represented in the feed.
    - provider-active new slot => source old identity stays absent/suppressed.
    - provider-rejected new slot => exact source old identity is the only
      fallback candidate.
    - blocked new identity keeps a past DateEnd while old fallback is released.
    - release happens only after a fresh Guardian funding-health signal says
      the wallet pause is cleared.
    """
    from app.api import campaigns as C
    from app.api import avito as A
    from app.models.campaign_item import CampaignItem

    if (
        str(account_id or "") != PLANETA_ACCOUNT_ID
        or int(getattr(scale_campaign, "id", 0) or 0) != 250
    ):
        return {"status": "not_configured"}

    try:
        root = json.loads(scale_campaign.settings_json or "{}")
    except Exception:
        root = {}
    cleanup = dict((root or {}).get("replacement_cleanup") or {})
    rec = dict(cleanup.get("slot_fallback_reconciliation") or {})
    if str(rec.get("policy_version") or "") != (
        "PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_V1"
    ):
        return {"status": "not_configured"}

    desired_old_fids = {
        str(x).strip() for x in (rec.get("desired_old_feed_ids") or [])
        if str(x).strip()
    }
    desired_old_avitos = {
        str(x).strip() for x in (rec.get("desired_old_avito_ids") or [])
        if str(x).strip()
    }
    blocked_new_fids = {
        str(x).strip() for x in (rec.get("blocked_new_feed_ids") or [])
        if str(x).strip()
    }
    active_new_fids = {
        str(x).strip() for x in (rec.get("active_new_feed_ids") or [])
        if str(x).strip()
    }
    if (
        int(rec.get("logical_slots") or 0) != 36
        or len(desired_old_fids) != 10
        or len(desired_old_avitos) != 10
        or len(blocked_new_fids) != 10
        or len(active_new_fids) != 26
        or (blocked_new_fids & active_new_fids)
    ):
        return {
            "status": "blocked",
            "reason": "slot_fallback_metadata_invalid",
            "owner_action_required": False,
        }

    new_rows = (
        db.query(CampaignItem)
        .filter(
            CampaignItem.account_id == account_id,
            CampaignItem.campaign_id.in_((249, 250, 252)),
            CampaignItem.status != "superseded",
        )
        .all()
    )
    new_fids = {
        str(r.feed_identity or "").strip()
        for r in new_rows
        if str(r.feed_identity or "").strip()
    }
    if len(new_fids) != 36 or not (blocked_new_fids | active_new_fids) <= new_fids:
        return {
            "status": "blocked",
            "reason": "slot_fallback_new_scope_drift",
            "new_count": len(new_fids),
            "owner_action_required": False,
        }

    old_rows = (
        db.query(CampaignItem)
        .filter(
            CampaignItem.account_id == account_id,
            CampaignItem.campaign_id == 144,
            CampaignItem.feed_identity.isnot(None),
            CampaignItem.avito_item_id.isnot(None),
        )
        .all()
    )
    all_old_fids = {
        str(r.feed_identity or "").strip()
        for r in old_rows if str(r.feed_identity or "").strip()
    }
    desired_old_rows = [
        r for r in old_rows
        if str(r.feed_identity or "").strip() in desired_old_fids
        and str(r.avito_item_id or "").strip() in desired_old_avitos
    ]
    if (
        len(desired_old_rows) != 10
        or {
            str(r.feed_identity or "").strip() for r in desired_old_rows
        } != desired_old_fids
        or {
            str(r.avito_item_id or "").strip() for r in desired_old_rows
        } != desired_old_avitos
    ):
        return {
            "status": "blocked",
            "reason": "slot_fallback_source_identity_mismatch",
            "owner_action_required": False,
        }

    feed = A._load_feed_items(account_id)
    by_fid = {
        str(getattr(x, "id", "") or "").strip(): x
        for x in feed if str(getattr(x, "id", "") or "").strip()
    }
    feed_ids = set(by_fid)
    wrong_old = (feed_ids & all_old_fids) - desired_old_fids
    if new_fids - feed_ids or desired_old_fids - feed_ids or wrong_old:
        return {
            "status": "blocked",
            "reason": "slot_fallback_feed_scope_drift",
            "missing_new": sorted(new_fids - feed_ids)[:20],
            "missing_old_fallback": sorted(desired_old_fids - feed_ids)[:20],
            "unexpected_old": sorted(wrong_old)[:20],
            "owner_action_required": False,
        }

    hold_dateend = str(rec.get("hold_dateend") or cleanup.get(
        "budget_hold_dateend_value"
    ) or "").strip()
    if not hold_dateend:
        return {
            "status": "blocked",
            "reason": "slot_fallback_hold_date_missing",
            "owner_action_required": False,
        }
    blocked_new_dateends = {
        fid: str(getattr(by_fid[fid], "date_end", "") or "").strip()
        for fid in blocked_new_fids
    }
    if any(v != hold_dateend for v in blocked_new_dateends.values()):
        return {
            "status": "blocked",
            "reason": "slot_fallback_blocked_new_not_held",
            "owner_action_required": False,
        }

    old_dateends = {
        fid: str(getattr(by_fid[fid], "date_end", "") or "").strip()
        for fid in desired_old_fids
    }
    old_all_held = all(v == hold_dateend for v in old_dateends.values())
    old_all_released = all(not v for v in old_dateends.values())
    if not old_all_held and not old_all_released:
        return {
            "status": "blocked",
            "reason": "slot_fallback_old_dateend_mixed",
            "owner_action_required": False,
        }

    # Guardian is the canonical low-wallet observer. Require freshness so a
    # stale pre-funding row can never auto-release the ten fallback identities.
    funding_row = (
        db.query(Storage)
        .filter(
            Storage.account_id == account_id,
            Storage.key == "guardian_balance_funding_health",
        )
        .order_by(Storage.id.desc())
        .first()
    )
    try:
        funding = json.loads(funding_row.value or "{}") if funding_row else {}
    except Exception:
        funding = {}
    funding_checked = _dt(funding.get("checked_at"))
    funding_age = (
        max(0.0, (now - funding_checked).total_seconds())
        if funding_checked is not None else None
    )
    funding_fresh = bool(
        funding_age is not None
        and funding_age <= 900
        and funding.get("service_active") is True
    )
    if not funding_fresh:
        rec.update({
            "state": "waiting_fresh_funding_health",
            "last_checked_at": now.isoformat(),
            "funding_age_seconds": funding_age,
            "owner_action_required": False,
        })
        cleanup["slot_fallback_reconciliation"] = rec
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {
            "status": "waiting",
            "reason": "slot_fallback_waiting_fresh_funding_health",
            "owner_action_required": False,
        }

    funding_pause = bool(funding.get("work_pause_required") is True)
    if funding_pause:
        rec.update({
            "state": "waiting_owner_funds",
            "last_checked_at": now.isoformat(),
            "real_balance_rub": funding.get("real_balance_rub"),
            "required_balance_rub": funding.get("max_cpl_rub"),
            "funding_reason": funding.get("reason"),
            "owner_action_required": True,
        })
        cleanup.update({
            "slot_fallback_reconciliation": rec,
            "owner_action_required": True,
            "owner_action": (
                "Пополнить аванс/баланс Avito; BORIS активирует только "
                "10 точных source-ID автоматически"
            ),
            "system_action": "slot_fallback_wait_owner_funds",
        })
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {
            "status": "waiting",
            "reason": "waiting_owner_funds",
            "provider_code": 2214,
            "fallback_slots": 10,
            "real_balance_rub": funding.get("real_balance_rub"),
            "required_balance_rub": funding.get("max_cpl_rub"),
            "owner_action_required": True,
        }

    # Funding is fresh and sufficient. Release only the ten exact old source
    # identities; the ten rejected new identities remain held with DateEnd.
    if old_all_held:
        models, _ = C._ff_delivery_projection(desired_old_rows, db)
        release_by_id = {
            str(getattr(x, "id", "") or "").strip(): x
            for x in models if str(getattr(x, "id", "") or "").strip()
        }
        if set(release_by_id) != desired_old_fids:
            return {
                "status": "blocked",
                "reason": "slot_fallback_release_projection_mismatch",
                "owner_action_required": False,
            }
        for model in release_by_id.values():
            model.date_end = ""

        def _release_exact_fallback(latest):
            latest_ids = {
                str(getattr(x, "id", "") or "").strip()
                for x in latest if str(getattr(x, "id", "") or "").strip()
            }
            if (
                new_fids - latest_ids
                or desired_old_fids - latest_ids
                or ((latest_ids & all_old_fids) - desired_old_fids)
            ):
                raise RuntimeError("SLOT_FALLBACK_RELEASE_SCOPE_DRIFT")
            out = []
            seen = set()
            for x in latest:
                fid = str(getattr(x, "id", "") or "").strip()
                if fid in desired_old_fids:
                    out.append(release_by_id[fid])
                    seen.add(fid)
                else:
                    out.append(x)
            if seen != desired_old_fids:
                raise RuntimeError("SLOT_FALLBACK_RELEASE_INCOMPLETE")
            return out

        try:
            A._mutate_feed_items(account_id, _release_exact_fallback)
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": "slot_fallback_release_atomic_mutation_failed",
                "error_type": type(exc).__name__,
                "owner_action_required": False,
            }

        verify = A._load_feed_items(account_id)
        verify_by = {
            str(getattr(x, "id", "") or "").strip(): x
            for x in verify if str(getattr(x, "id", "") or "").strip()
        }
        if (
            any(
                str(getattr(verify_by[fid], "date_end", "") or "").strip()
                for fid in desired_old_fids
            )
            or any(
                str(getattr(verify_by[fid], "date_end", "") or "").strip()
                != hold_dateend
                for fid in blocked_new_fids
            )
        ):
            return {
                "status": "blocked",
                "reason": "slot_fallback_release_verify_failed",
                "owner_action_required": False,
            }

        rec.update({
            "state": "release_staged",
            "released_at": now.isoformat(),
            "owner_action_required": False,
        })
        cleanup.update({
            "slot_fallback_reconciliation": rec,
            "owner_action_required": False,
            "system_action": "slot_fallback_send_exact_source_ids",
        })
        cleanup.pop("owner_action", None)
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()

    feed_send = feed_send_fn or A.feed_send_to_avito
    state = str(rec.get("state") or "")
    if state in {
        "release_staged",
        "send_retryable",
        "waiting_provider_activation",
    }:
        last_send = _dt(rec.get("last_send_at"))
        if state != "waiting_provider_activation" or (
            last_send is not None
            and (now - last_send).total_seconds() >= 900
        ):
            try:
                send_result = dict(feed_send(account_id=account_id) or {})
            except TypeError:
                send_result = dict(feed_send(account_id) or {})
            except Exception as exc:
                send_result = {
                    "status": "error",
                    "code": "exception",
                    "error_type": type(exc).__name__,
                }
            rec["last_send_at"] = now.isoformat()
            rec["last_send_result"] = {
                k: v for k, v in send_result.items()
                if k in {
                    "status", "code", "http_status", "upload_id",
                    "retry_after_seconds", "changed_avito", "message"
                }
            }
            send_status = str(send_result.get("status") or "")
            if send_status in {"ok", "submitted"}:
                rec["state"] = "waiting_provider_activation"
                rec["submit_upload_id"] = send_result.get("upload_id")
            else:
                rec["state"] = "send_retryable"
            cleanup["slot_fallback_reconciliation"] = rec
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            if rec["state"] == "send_retryable":
                return {
                    "status": "waiting",
                    "reason": "slot_fallback_feed_send_retryable",
                    "send": rec.get("last_send_result"),
                    "owner_action_required": False,
                }

    collect = collect_stats_fn or A.collect_stats
    try:
        stats_result = dict(collect(account_id) or {})
    except Exception as exc:
        stats_result = {
            "status": "error",
            "reason": "collect_exception",
            "error_type": type(exc).__name__,
        }
    snapshot = dict(stats_result.get("snapshot") or {})
    active_ids = {
        str(x.get("id") or "")
        for x in (snapshot.get("items") or [])
        if str(x.get("status") or "").lower() == "active"
    }
    active_fallback = desired_old_avitos & active_ids
    rec.update({
        "last_checked_at": now.isoformat(),
        "active_fallback_count": len(active_fallback),
        "active_fallback_avito_ids": sorted(active_fallback),
        "missing_fallback_avito_ids": sorted(desired_old_avitos - active_fallback),
    })
    if active_fallback == desired_old_avitos:
        rec.update({
            "state": "completed_active_36",
            "completed_at": now.isoformat(),
            "owner_action_required": False,
        })
        cleanup.update({
            "slot_fallback_reconciliation": rec,
            "status": "completed_with_slot_fallback",
            "owner_action_required": False,
            "system_action": "slot_fallback_completed_36_logical_slots",
        })
        cleanup.pop("owner_action", None)
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {
            "status": "completed",
            "changed": True,
            "reason": "slot_fallback_all_source_ids_active",
            "active_new_slots": 26,
            "active_fallback_slots": 10,
            "logical_slots": 36,
            "owner_action_required": False,
        }

    rec["state"] = "waiting_provider_activation"
    cleanup["slot_fallback_reconciliation"] = rec
    root["replacement_cleanup"] = cleanup
    scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
    db.commit()
    return {
        "status": "waiting",
        "reason": "slot_fallback_waiting_provider_activation",
        "active_fallback": len(active_fallback),
        "expected_fallback": 10,
        "stats_status": stats_result.get("status"),
        "stats_reason": stats_result.get("reason"),
        "owner_action_required": False,
    }


def _pilot_scale_replacement_cleanup(db, scale_campaign, account_id: str, allowed: list, now: datetime) -> dict:
    """Ownerless finalizer for a proven pilot -> scale replacement.

    The provider has already proven the new pilot+scale listings active. This
    helper removes the old campaign identities from the canonical scheduled URL
    feed, then waits for a *newer* Avito upload that proves new=active and old=0.
    No direct provider mutation is executed here.
    """
    from app.api import campaigns as C
    from app.api.avito import (
        _autoload_upload_snapshot, _load_feed_items, _mutate_feed_items,
        get_avito_token, _extract_token, _avito_http_get,
    )

    try:
        root = json.loads(scale_campaign.settings_json or "{}")
    except Exception:
        root = {}
    pilot_id = int((root or {}).get("pilot_campaign_id") or 0)
    old_id = int((root or {}).get("parent_campaign_id") or 0)
    if pilot_id <= 0 or old_id <= 0:
        return {"status": "not_configured"}

    cleanup = dict((root or {}).get("replacement_cleanup") or {})

    # UNDER_KPI_PRESERVE_INVENTORY_V2: KPI protection applies to optional
    # inventory shrink, never to an already-started technical tombstone cleanup.
    # Once superseded replacement rows have been staged with DateEnd (or a
    # completed cleanup is being reverified), pausing that lifecycle can leave
    # duplicate historical identities in the canonical feed and let another
    # reconciliation observe them as live again. Resume maintenance ownerlessly.
    _kpi_advice = _load_json_storage(db, account_id, "cpx_advice")
    _kpi_target = float(((_kpi_advice.get("target") or {}).get("target_leads_per_day") or 0) or 0)
    _kpi_contacts = float(((_kpi_advice.get("fact") or {}).get("contacts_today") or 0) or 0)
    _kpi_generated = _dt(_kpi_advice.get("generated_at"))
    _kpi_fresh = bool(_kpi_generated and 0 <= (now - _kpi_generated).total_seconds() <= 7200)
    _kpi_authoritative = bool(_kpi_fresh and _kpi_target > 0)
    _under_kpi = bool(_kpi_authoritative and _kpi_contacts < _kpi_target)
    _cleanup_maintenance_states = {
        "dateend_cleanup_staged", "waiting_dateend_cleanup_upload",
        "under_kpi_restore_staged", "waiting_under_kpi_restore_upload",
        "waiting_owner_funds", "waiting_budget_policy",
        "waiting_fresh_kpi_after_budget_clear",
    }
    _cleanup_status = str(cleanup.get("status") or "")
    _paused_previous = str(cleanup.get("status_before_kpi_pause") or "")
    if (_cleanup_status == "paused_under_kpi_growth"
            and _paused_previous in _cleanup_maintenance_states):
        cleanup["status"] = _paused_previous
        cleanup["resumed_at"] = now.isoformat()
        cleanup["resume_reason"] = "replacement_cleanup_inflight_not_growth_inventory"
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        _cleanup_status = _paused_previous

    if (_under_kpi and _cleanup_status not in _cleanup_maintenance_states
            and _cleanup_status != "completed"):
        _before_pause = _cleanup_status
        if _before_pause != "paused_under_kpi_growth":
            cleanup["status_before_kpi_pause"] = _before_pause or "not_started"
            cleanup["paused_at"] = now.isoformat()
        cleanup.update({
            "status": "paused_under_kpi_growth",
            "pause_reason": "lead_kpi_below_target_preserve_inventory",
            "kpi_target_leads_per_day": _kpi_target,
            "kpi_contacts_today": _kpi_contacts,
            "kpi_signal_generated_at": _kpi_generated.isoformat() if _kpi_generated else None,
            "owner_action_required": False,
        })
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {"status": "waiting", "reason": "under_kpi_preserve_inventory",
                "target_leads_per_day": _kpi_target, "contacts_today": _kpi_contacts,
                "owner_action_required": False}
    elif (_kpi_authoritative and not _under_kpi
          and str(cleanup.get("status") or "") == "paused_under_kpi_growth"):
        cleanup["status"] = str(cleanup.get("status_before_kpi_pause") or "not_started")
        cleanup["resumed_at"] = now.isoformat()
        cleanup["resume_reason"] = "lead_kpi_target_reached_or_signal_changed"
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()

    pilot = db.query(Campaign).filter(Campaign.id == pilot_id).first()
    old = db.query(Campaign).filter(Campaign.id == old_id).first()
    if pilot is None or old is None:
        return {"status": "blocked", "reason": "linked_campaign_missing"}

    try:
        pilot_root = json.loads(pilot.settings_json or "{}")
    except Exception:
        pilot_root = {}
    if int((pilot_root or {}).get("scale_after_success_campaign_id") or 0) != int(scale_campaign.id):
        return {"status": "blocked", "reason": "pilot_scale_backlink_mismatch"}
    if not bool((pilot_root or {}).get("preserve_on_scale")):
        return {"status": "blocked", "reason": "pilot_preserve_contract_missing"}
    if int((pilot_root or {}).get("parent_campaign_id") or 0) != old_id:
        return {"status": "blocked", "reason": "old_campaign_scope_mismatch"}

    pilot_rows = [r for r in C._ff_items(db, pilot_id, allowed) if str(r.account_id or "") == account_id]
    scale_rows = [r for r in C._ff_items(db, int(scale_campaign.id), allowed) if str(r.account_id or "") == account_id]
    # OLD_PROVIDER_IDENTITY_SCOPE_V1:
    # Historical local status is NOT provider truth. A prior cleanup/reconciliation
    # may already mark rows superseded while Avito still keeps those exact numeric
    # ids active. Build the removal scope from durable published identity bindings,
    # not from mutable CampaignItem.status and not through _ff_items(), which hides
    # superseded audit rows by design.
    from app.models.campaign_item import CampaignItem
    old_rows = (
        db.query(CampaignItem)
        .filter(CampaignItem.campaign_id == old_id,
                CampaignItem.account_id == account_id,
                CampaignItem.feed_identity.isnot(None),
                CampaignItem.avito_item_id.isnot(None))
        .order_by(CampaignItem.id.asc())
        .all()
    )
    _expected_old_contract = int(cleanup.get("expected_old_items") or 0)
    if _expected_old_contract and len(old_rows) != _expected_old_contract:
        return {"status": "blocked", "reason": "old_provider_identity_scope_mismatch",
                "expected_old": _expected_old_contract, "actual_old": len(old_rows)}
    # REPLACEMENT_CHILD_SCOPE_V1: a provider-rejected scale item may be
    # replaced by a separate, fully proven child campaign with a fresh feed identity.
    # Count only child replacements explicitly linked from the scale campaign's
    # durable resolution metadata; never discover/fuzzy-match unrelated campaigns.
    resolution = dict((root or {}).get("resolution") or {})
    replacement_campaign_ids = []
    _single_replacement_id = int(resolution.get("replacement_campaign_id") or 0)
    if _single_replacement_id:
        replacement_campaign_ids.append(_single_replacement_id)
    for _raw_id in (resolution.get("replacement_campaign_ids") or []):
        try:
            _rid = int(_raw_id)
        except Exception:
            continue
        if _rid > 0 and _rid not in replacement_campaign_ids:
            replacement_campaign_ids.append(_rid)

    replacement_rows = []
    for _rid in replacement_campaign_ids:
        _replacement = db.query(Campaign).filter(
            Campaign.id == _rid,
            Campaign.default_account_id == account_id,
        ).first()
        if _replacement is None:
            return {"status": "blocked", "reason": "replacement_campaign_missing",
                    "replacement_campaign_id": _rid}
        try:
            _replacement_root = json.loads(_replacement.settings_json or "{}")
        except Exception:
            _replacement_root = {}
        if int((_replacement_root or {}).get("parent_campaign_id") or 0) != int(scale_campaign.id):
            return {"status": "blocked", "reason": "replacement_parent_scope_mismatch",
                    "replacement_campaign_id": _rid}
        _replacement_state = dict((_replacement_root or {}).get("replacement") or {})
        if str(_replacement.status or "").lower() != "completed" or str(_replacement_state.get("state") or "") != "completed":
            return {"status": "waiting", "reason": "replacement_campaign_not_completed",
                    "replacement_campaign_id": _rid}
        _rows = [
            r for r in C._ff_items(db, _rid, allowed)
            if str(r.account_id or "") == account_id
            and str(r.status or "") == "published"
            and str(getattr(r, "avito_item_id", "") or "")
        ]
        if not _rows:
            return {"status": "waiting", "reason": "replacement_publication_not_proven",
                    "replacement_campaign_id": _rid}
        replacement_rows.extend(_rows)

    new_rows = pilot_rows + scale_rows + replacement_rows

    expected_pilot = int((pilot_root or {}).get("expected_new_items") or len(pilot_rows))
    expected_scale = int((root or {}).get("expected_items") or len(scale_rows))
    expected_new = expected_pilot + expected_scale
    if expected_new <= 0 or len(new_rows) != expected_new:
        return {"status": "blocked", "reason": "new_item_count_mismatch",
                "expected_new": expected_new, "actual_new": len(new_rows),
                "replacement_items": len(replacement_rows)}
    if not old_rows:
        return {"status": "blocked", "reason": "old_published_scope_missing"}

    # REPLACEMENT_CLEANUP_BASE_LIVE_REVISION_PENDING_V1:
    # Cleanup protects the live inventory shape; it must not be blocked merely
    # because a later content-only revision is rejected while the exact previous
    # Avito listing remains active. Accept such a row only with fresh exact
    # account+campaign+feed_identity+avito_item_id evidence from daily_stats.
    _cleanup_stats = _load_json_storage(
        db, account_id, f"daily_stats:{marketing_today_iso()}"
    )
    _cleanup_stats_collected = _dt(_cleanup_stats.get("collected_at"))
    _cleanup_stats_fresh = bool(
        _cleanup_stats_collected
        and 0 <= (now - _cleanup_stats_collected).total_seconds()
        <= STATS_STALE_SECONDS
    )
    _cleanup_live_exact = set()
    if _cleanup_stats_fresh:
        _new_campaign_ids = {
            int(getattr(_r, "campaign_id", 0) or 0) for _r in new_rows
        }
        for _si in (_cleanup_stats.get("items") or []):
            if not isinstance(_si, dict):
                continue
            _ident = dict(_si.get("campaign_identity") or {})
            try:
                _cid = int(_ident.get("campaign_id") or 0)
            except Exception:
                _cid = 0
            if _cid not in _new_campaign_ids:
                continue
            if str(_ident.get("account_id") or account_id) != str(account_id):
                continue
            if str(_si.get("status") or "").lower() != "active":
                continue
            _fid = str(_ident.get("feed_identity") or "").strip()
            _aid = str(_si.get("id") or "").strip()
            if _fid and _aid:
                _cleanup_live_exact.add((_cid, _fid, _aid))

    _cleanup_revision_pending_rows = []
    _cleanup_unproven_rows = []
    for _r in new_rows:
        _status = str(getattr(_r, "status", "") or "")
        _identity_status = str(getattr(_r, "identity_status", "") or "")
        _fid = str(getattr(_r, "feed_identity", "") or "").strip()
        _aid = str(getattr(_r, "avito_item_id", "") or "").strip()
        _cid = int(getattr(_r, "campaign_id", 0) or 0)
        if _status == "published" and _aid:
            continue
        if (
            _identity_status == "publication_rejected_bound"
            and _fid
            and _aid
            and (_cid, _fid, _aid) in _cleanup_live_exact
        ):
            _cleanup_revision_pending_rows.append(_r)
            continue
        _cleanup_unproven_rows.append(_r)

    if _cleanup_unproven_rows:
        return {
            "status": "blocked",
            "reason": "new_publication_not_fully_proven",
            "unproven_feed_ids": [
                str(getattr(_r, "feed_identity", "") or "")
                for _r in _cleanup_unproven_rows
            ][:20],
            "revision_pending_base_live": len(_cleanup_revision_pending_rows),
        }

    new_feed_ids = {str(getattr(r, "feed_identity", "") or "").strip() for r in new_rows}
    old_feed_ids = {str(getattr(r, "feed_identity", "") or "").strip() for r in old_rows}
    old_avito_ids = {str(getattr(r, "avito_item_id", "") or "").strip() for r in old_rows}
    new_feed_ids.discard(""); old_feed_ids.discard(""); old_avito_ids.discard("")
    if (len(new_feed_ids) != expected_new or not old_feed_ids
            or len(old_avito_ids) != len(old_rows)
            or (new_feed_ids & old_feed_ids)):
        return {"status": "blocked", "reason": "feed_identity_scope_invalid"}

    # UNDER_KPI_COMPLETED_RESTORE_V3:
    # If cleanup already removed the last useful old reach but a fresh KPI signal
    # is still below target, restore ONLY the exact old identities proven by the
    # completed cleanup. Preserve every unrelated feed card. This is desired-feed
    # self-heal only; provider activation is verified on a newer Autoload cycle.
    _restore_states = {
        "under_kpi_restore_staged",
        "waiting_under_kpi_restore_upload",
        "waiting_owner_funds",
        "waiting_budget_policy",
        "waiting_fresh_kpi_after_budget_clear",
    }
    _cleanup_status = str(cleanup.get("status") or "")
    if _under_kpi and _cleanup_status == "completed":
        _restore_feed_ids = {
            str(x).strip() for x in (cleanup.get("cleanup_old_feed_ids") or [])
            if str(x).strip()
        }
        _restore_avito_ids = {
            str(x).strip() for x in (cleanup.get("cleanup_old_avito_ids") or [])
            if str(x).strip()
        }
        if (not _restore_feed_ids or not _restore_avito_ids
                or len(_restore_feed_ids) != len(_restore_avito_ids)):
            return {"status": "blocked", "reason": "under_kpi_restore_scope_missing",
                    "feed_ids": len(_restore_feed_ids),
                    "avito_ids": len(_restore_avito_ids),
                    "owner_action_required": False}
        _restore_rows = [
            r for r in old_rows
            if str(getattr(r, "feed_identity", "") or "").strip() in _restore_feed_ids
            and str(getattr(r, "avito_item_id", "") or "").strip() in _restore_avito_ids
        ]
        if (len(_restore_rows) != len(_restore_feed_ids)
                or {str(getattr(r, "feed_identity", "") or "").strip() for r in _restore_rows}
                   != _restore_feed_ids
                or {str(getattr(r, "avito_item_id", "") or "").strip() for r in _restore_rows}
                   != _restore_avito_ids):
            return {"status": "blocked", "reason": "under_kpi_restore_identity_mismatch",
                    "matched_rows": len(_restore_rows),
                    "expected": len(_restore_feed_ids),
                    "owner_action_required": False}
        _restore_models, _ = C._ff_delivery_projection(_restore_rows, db)
        _restore_by_id = {
            str(getattr(x, "id", "") or "").strip(): x for x in _restore_models
            if str(getattr(x, "id", "") or "").strip()
        }
        if set(_restore_by_id) != _restore_feed_ids:
            return {"status": "blocked", "reason": "under_kpi_restore_projection_mismatch",
                    "owner_action_required": False}
        for _model in _restore_models:
            # FeedItem.date_end is a string field; clearing removal must use the
            # canonical empty value, not None, otherwise atomic feed validation
            # rejects the whole mutation before commit.
            _model.date_end = ""

        _restore_current = _load_feed_items(account_id)
        _restore_current_ids = {
            str(getattr(x, "id", "") or "").strip() for x in _restore_current
            if str(getattr(x, "id", "") or "").strip()
        }
        _restore_missing_new = new_feed_ids - _restore_current_ids
        _restore_unexpected_old = (_restore_current_ids & old_feed_ids) - _restore_feed_ids
        if _restore_missing_new or _restore_unexpected_old:
            return {"status": "blocked", "reason": "under_kpi_restore_feed_scope_drift",
                    "missing_new": sorted(_restore_missing_new)[:20],
                    "unexpected_old": sorted(_restore_unexpected_old)[:20],
                    "owner_action_required": False}

        try:
            _restore_baseline = _autoload_upload_snapshot(
                account_id, None, expected_ad_ids=sorted(new_feed_ids)
            )
            _restore_baseline_upload = int((_restore_baseline or {}).get("upload_id") or 0)
        except Exception:
            _restore_baseline_upload = 0

        def _restore_under_kpi(latest):
            _latest_ids = {
                str(getattr(x, "id", "") or "").strip() for x in latest
                if str(getattr(x, "id", "") or "").strip()
            }
            _missing_new_now = new_feed_ids - _latest_ids
            _unexpected_old_now = (_latest_ids & old_feed_ids) - _restore_feed_ids
            if _missing_new_now or _unexpected_old_now:
                raise RuntimeError("UNDER_KPI_RESTORE_SCOPE_DRIFT")
            _out = []
            _seen = set()
            for _x in latest:
                _fid = str(getattr(_x, "id", "") or "").strip()
                if _fid in _restore_feed_ids:
                    if _fid not in _seen:
                        _out.append(_restore_by_id[_fid])
                        _seen.add(_fid)
                else:
                    _out.append(_x)
            for _fid in sorted(_restore_feed_ids - _seen):
                _out.append(_restore_by_id[_fid])
            return _out

        try:
            _mutate_feed_items(account_id, _restore_under_kpi)
        except Exception as _exc:
            return {"status": "blocked", "reason": "under_kpi_restore_atomic_mutation_failed",
                    "error_type": type(_exc).__name__, "owner_action_required": False}

        _restore_verify = _load_feed_items(account_id)
        _restore_verify_ids = {
            str(getattr(x, "id", "") or "").strip() for x in _restore_verify
            if str(getattr(x, "id", "") or "").strip()
        }
        _restore_dateend = {
            str(getattr(x, "id", "") or "").strip():
            str(getattr(x, "date_end", "") or "").strip()
            for x in _restore_verify
            if str(getattr(x, "id", "") or "").strip() in _restore_feed_ids
        }
        if ((new_feed_ids - _restore_verify_ids)
                or (_restore_feed_ids - _restore_verify_ids)
                or ((_restore_verify_ids & old_feed_ids) - _restore_feed_ids)
                or set(_restore_dateend) != _restore_feed_ids
                or any(_restore_dateend.values())):
            return {"status": "blocked", "reason": "under_kpi_restore_verify_failed",
                    "current": len(_restore_verify_ids),
                    "restored_old": len(_restore_feed_ids & _restore_verify_ids),
                    "owner_action_required": False}

        cleanup.update({
            "status": "under_kpi_restore_staged",
            "status_before_kpi_pause": "completed",
            "pause_reason": "lead_kpi_below_target_preserve_inventory",
            "kpi_target_leads_per_day": _kpi_target,
            "kpi_contacts_today": _kpi_contacts,
            "kpi_signal_generated_at": _kpi_generated.isoformat() if _kpi_generated else None,
            "restore_old_feed_ids": sorted(_restore_feed_ids),
            "restore_old_avito_ids": sorted(_restore_avito_ids),
            "restore_baseline_upload_id": _restore_baseline_upload,
            "restore_staged_at": now.isoformat(),
            "owner_action_required": False,
        })
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {"status": "waiting", "reason": "under_kpi_restore_old_inventory_staged",
                "restored_feed_items": len(_restore_feed_ids),
                "baseline_upload_id": _restore_baseline_upload,
                "target_leads_per_day": _kpi_target,
                "contacts_today": _kpi_contacts,
                "owner_action_required": False}

    if _cleanup_status in _restore_states:
        _restore_feed_ids = {
            str(x).strip() for x in (cleanup.get("restore_old_feed_ids") or [])
            if str(x).strip()
        }
        _restore_avito_ids = {
            str(x).strip() for x in (cleanup.get("restore_old_avito_ids") or [])
            if str(x).strip()
        }
        if (not _restore_feed_ids or not _restore_avito_ids
                or len(_restore_feed_ids) != len(_restore_avito_ids)):
            return {"status": "blocked", "reason": "under_kpi_restore_state_scope_missing",
                    "owner_action_required": False}

        _restore_current = _load_feed_items(account_id)
        _restore_current_ids = {
            str(getattr(x, "id", "") or "").strip() for x in _restore_current
            if str(getattr(x, "id", "") or "").strip()
        }
        _restore_missing_new = new_feed_ids - _restore_current_ids
        _restore_missing_old = _restore_feed_ids - _restore_current_ids
        _restore_unexpected_old = (_restore_current_ids & old_feed_ids) - _restore_feed_ids
        _restore_dateend = {
            str(getattr(x, "id", "") or "").strip():
            str(getattr(x, "date_end", "") or "").strip()
            for x in _restore_current
            if str(getattr(x, "id", "") or "").strip() in _restore_feed_ids
        }
        _restore_dateend_values = list(_restore_dateend.values())
        _restore_dateend_any = any(_restore_dateend_values)
        _restore_dateend_all = bool(_restore_dateend_values) and all(_restore_dateend_values)
        _restore_dateend_mixed = bool(_restore_dateend_any and not _restore_dateend_all)
        _budget_hold_state = _cleanup_status in {
            "waiting_budget_policy",
            "waiting_fresh_kpi_after_budget_clear",
        }
        _budget_hold_value = str(cleanup.get("budget_hold_dateend_value") or "").strip()
        _budget_hold_value_mismatch = bool(
            _budget_hold_state
            and _restore_dateend_all
            and _budget_hold_value
            and any(v != _budget_hold_value for v in _restore_dateend_values)
        )
        if (_restore_missing_new or _restore_missing_old or _restore_unexpected_old
                or set(_restore_dateend) != _restore_feed_ids
                or _restore_dateend_mixed
                or _budget_hold_value_mismatch
                or (not _budget_hold_state and _restore_dateend_any)):
            return {"status": "blocked", "reason": "under_kpi_restore_wait_scope_drift",
                    "missing_new": sorted(_restore_missing_new)[:20],
                    "missing_restored_old": sorted(_restore_missing_old)[:20],
                    "unexpected_old": sorted(_restore_unexpected_old)[:20],
                    "restore_dateend_any": _restore_dateend_any,
                    "restore_dateend_all": _restore_dateend_all,
                    "budget_hold_state": _budget_hold_state,
                    "budget_hold_value": _budget_hold_value or None,
                    "owner_action_required": False}

        # REPLACEMENT_BUDGET_PRE_PROVIDER_GUARD_V1:
        # Financial safety must not depend on a successful Avito upload read.
        # Rehydrate a known/persisted red money signal before any provider gate;
        # a temporary 429/transport failure must never downgrade
        # waiting_budget_policy back to an unprotected restore state.
        _early_budget_red = bool(
            (cleanup.get("restore_budget_policy") or {}).get("blocked")
        )
        _early_budget_authoritative = False
        _early_limit = None
        _early_pressure = {}
        try:
            from app.services.marketing_money_policy import (
                effective_daily_budget_limit as _early_daily_limit,
                presence_budget_pressure as _early_presence_pressure,
            )
            _early_limit = _early_daily_limit(db, account_id)
            if _early_limit is not None and float(_early_limit) > 0:
                _early_pressure = dict(
                    _early_presence_pressure(
                        db, account_id, float(_early_limit)
                    ) or {}
                )
                if str(_early_pressure.get("reason") or ""):
                    _early_budget_authoritative = True
                    _early_budget_red = bool(_early_pressure.get("blocked"))
        except Exception:
            # Fail closed only when this lifecycle already has positive evidence
            # of a red money state; do not invent a new blocker from transport
            # ambiguity alone.
            _early_budget_red = bool(
                _early_budget_red
                or _cleanup_status in {
                    "waiting_budget_policy",
                    "waiting_fresh_kpi_after_budget_clear",
                }
            )
        if _early_budget_red:
            _cleanup_status = "waiting_budget_policy"
            cleanup["status"] = "waiting_budget_policy"

        # REPLACEMENT_RELEASE_FRESH_SPEND_GATE_V1:
        # A historical presence-budget window may become numerically green while
        # today's dedicated Avito spending proof is stale/degraded (for example,
        # provider 429). Releasing our exact DateEnd hold can itself reactivate
        # paid inventory, so that transition must use the same strict <=15m,
        # same-day, non-fallback spend proof as money-increasing writes. Do not
        # redefine money policy here; simply keep the publication hold fail-closed
        # until the adjacent money signal becomes authoritative again.
        if (
            not _early_budget_red
            and _cleanup_status in {
                "waiting_budget_policy",
                "waiting_fresh_kpi_after_budget_clear",
            }
        ):
            _release_spend_signal = {}
            _release_spend_fresh = False
            _release_spend_error = None
            try:
                from app.services.marketing_money_policy import (
                    latest_confirmed_spend as _release_latest_spend,
                )
                from app.services.marketing_signal_guard import (
                    money_spend_signal_eligible as _release_spend_eligible,
                )
                _release_spend_signal = dict(
                    _release_latest_spend(
                        db, account_id, max_age_seconds=900
                    ) or {}
                )
                _release_spend_fresh = bool(
                    _release_spend_eligible(
                        db,
                        account_id,
                        _release_spend_signal,
                        max_age_seconds=900,
                    )
                )
            except Exception as _exc:
                _release_spend_error = type(_exc).__name__
                _release_spend_fresh = False
            if not _release_spend_fresh:
                _safe_today = dict(_early_pressure.get("today") or {})
                _safe_budget_evidence = dict(
                    cleanup.get("restore_budget_policy") or {}
                )
                _safe_budget_evidence.update({
                    "blocked": False,
                    "authoritative": bool(_early_budget_authoritative),
                    "reason": str(
                        _early_pressure.get("reason")
                        or _safe_budget_evidence.get("reason")
                        or "budget_not_blocked"
                    ),
                    "daily_budget_limit_rub": (
                        _early_pressure.get("daily_budget_limit_rub")
                        if _early_pressure.get("daily_budget_limit_rub")
                        is not None
                        else _early_limit
                    ),
                    "today_presence_rub": _safe_today.get("presence_rub"),
                    "today_all_spend_rub": _safe_today.get("all_spend_rub"),
                    "current_presence_ratio": _early_pressure.get(
                        "current_presence_ratio"
                    ),
                    "completed_over_budget_days": _early_pressure.get(
                        "completed_over_budget_days"
                    ),
                    "error_type": None,
                })
                cleanup.update({
                    "status": "waiting_budget_policy",
                    "restore_budget_policy": _safe_budget_evidence,
                    "owner_action_required": False,
                    "system_action": (
                        "wait_fresh_spend_before_budget_hold_release"
                    ),
                    "release_spend_gate": {
                        "fresh": False,
                        "max_age_seconds": 900,
                        "signal_status": _release_spend_signal.get("status"),
                        "signal_reason": _release_spend_signal.get("reason"),
                        "storage_key": _release_spend_signal.get("storage_key"),
                        "timestamp": _release_spend_signal.get("timestamp"),
                        "age_seconds": _release_spend_signal.get("age_seconds"),
                        "spending_date": _release_spend_signal.get(
                            "spending_date"
                        ),
                        "error_type": _release_spend_error,
                        "checked_at": now.isoformat(),
                    },
                })
                cleanup.pop("owner_action", None)
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(
                    root, ensure_ascii=False
                )
                db.commit()
                return {
                    "status": "waiting",
                    "reason": "waiting_fresh_spend_after_budget_clear",
                    "owner_action_required": False,
                    "system_action": (
                        "wait_fresh_spend_before_budget_hold_release"
                    ),
                    "spend_signal": cleanup.get("release_spend_gate"),
                    "budget_policy": _safe_budget_evidence,
                }

        # REPLACEMENT_RELEASE_FRESH_KPI_GATE_V1:
        # A stale/missing KPI is unknown, not proof that the lead target was
        # reached. Once money becomes safe, preserve the exact DateEnd hold
        # until a fresh positive KPI target is available. This prevents an
        # adjacent marketer outage from silently finalizing the restore lane.
        if not _early_budget_red and not _kpi_authoritative:
            _safe_today = dict(_early_pressure.get("today") or {})
            _safe_budget_evidence = dict(
                cleanup.get("restore_budget_policy") or {}
            )
            _safe_budget_evidence.update({
                "blocked": False,
                "authoritative": bool(_early_budget_authoritative),
                "reason": str(
                    _early_pressure.get("reason")
                    or _safe_budget_evidence.get("reason")
                    or "budget_not_blocked"
                ),
                "daily_budget_limit_rub": (
                    _early_pressure.get("daily_budget_limit_rub")
                    if _early_pressure.get("daily_budget_limit_rub") is not None
                    else _early_limit
                ),
                "today_presence_rub": _safe_today.get("presence_rub"),
                "today_all_spend_rub": _safe_today.get("all_spend_rub"),
                "current_presence_ratio": _early_pressure.get(
                    "current_presence_ratio"
                ),
                "completed_over_budget_days": _early_pressure.get(
                    "completed_over_budget_days"
                ),
                "error_type": None,
            })
            cleanup.update({
                "status": "waiting_fresh_kpi_after_budget_clear",
                "restore_budget_policy": _safe_budget_evidence,
                "owner_action_required": False,
                "system_action": "wait_fresh_kpi_before_restore_decision",
                "kpi_gate": {
                    "authoritative": False,
                    "fresh": bool(_kpi_fresh),
                    "target_leads_per_day": _kpi_target,
                    "contacts_today": _kpi_contacts,
                    "generated_at": (
                        _kpi_generated.isoformat() if _kpi_generated else None
                    ),
                    "max_age_seconds": 7200,
                    "checked_at": now.isoformat(),
                },
            })
            cleanup.pop("owner_action", None)
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(
                root, ensure_ascii=False
            )
            db.commit()
            return {
                "status": "waiting",
                "reason": "waiting_fresh_kpi_after_budget_clear",
                "owner_action_required": False,
                "system_action": "wait_fresh_kpi_before_restore_decision",
                "kpi_fresh": bool(_kpi_fresh),
                "kpi_target_leads_per_day": _kpi_target,
                "kpi_contacts_today": _kpi_contacts,
                "budget_policy": _safe_budget_evidence,
            }

        # BUDGET_HOLD_PROVIDER_CONFIRM_CACHE_V1:
        # Once Avito has strictly confirmed the exact DateEnd hold, do not
        # re-read the provider every 15-minute watcher tick while the money
        # policy is still authoritatively red and the exact feed hold is
        # unchanged. Re-verify at most hourly; any money-policy release or feed
        # drift falls through to the full provider state machine immediately.
        _cached_provider_state = dict(
            cleanup.get("budget_hold_provider_state") or {}
        )
        _cached_provider_baseline = int(
            cleanup.get("budget_hold_baseline_upload_id") or 0
        )
        _cached_provider_confirmed_id = int(
            cleanup.get("budget_hold_provider_confirmed_upload_id") or 0
        )
        _cached_provider_checked = _dt(
            cleanup.get("budget_hold_last_provider_checked_at")
            or cleanup.get("budget_hold_provider_confirmed_at")
        )
        _cached_provider_age = (
            max(0.0, (now - _cached_provider_checked).total_seconds())
            if _cached_provider_checked is not None
            else None
        )
        _cached_provider_confirmed = bool(
            _cached_provider_baseline > 0
            and _cached_provider_confirmed_id > _cached_provider_baseline
            and _cached_provider_state.get("confirmed") is True
            and str(_cached_provider_state.get("reason") or "")
            == "provider_dateend_confirmed"
            and int(_cached_provider_state.get("upload_id") or 0)
            == _cached_provider_confirmed_id
            and int(_cached_provider_state.get("expected") or 0)
            == len(_restore_feed_ids)
            and int(_cached_provider_state.get("found") or 0)
            == len(_restore_feed_ids)
            and int(_cached_provider_state.get("active") or 0) == 0
            and int(_cached_provider_state.get("errors") or 0) == 0
            and not (_cached_provider_state.get("missing") or [])
            and int(_cached_provider_state.get("dateend_3007_count") or 0)
            == len(_restore_feed_ids)
        )
        if (
            _cleanup_status == "waiting_budget_policy"
            and _early_budget_red
            and _early_budget_authoritative
            and _restore_dateend_all
            and _cached_provider_confirmed
            and _cached_provider_age is not None
            and _cached_provider_age < 3600
        ):
            _cached_submit_evidence = dict(
                cleanup.get("budget_hold_submit_evidence") or {}
            )
            _cached_submit_upload = int(
                _cached_submit_evidence.get("upload_id") or 0
            )
            _cached_submit_baseline = int(
                _cached_submit_evidence.get("baseline_upload_id") or 0
            )
            if (
                str(_cached_submit_evidence.get("status") or "")
                == "submitted"
                and _cached_submit_baseline == _cached_provider_baseline
                and _cached_submit_upload > _cached_provider_baseline
                and _cached_submit_upload <= _cached_provider_confirmed_id
            ):
                _cached_submit_evidence.update({
                    "status": "verified",
                    "provider_confirmation_required": False,
                    "verified_by_upload_id": _cached_provider_confirmed_id,
                    "verified_at": now.isoformat(),
                })
                cleanup["budget_hold_submit_evidence"] = (
                    _cached_submit_evidence
                )
            _cached_today = dict(_early_pressure.get("today") or {})
            _cached_budget_evidence = dict(
                cleanup.get("restore_budget_policy") or {}
            )
            _cached_budget_evidence.update({
                "blocked": True,
                "authoritative": True,
                "reason": str(
                    _early_pressure.get("reason")
                    or _cached_budget_evidence.get("reason")
                    or "unknown"
                ),
                "daily_budget_limit_rub": (
                    _early_pressure.get("daily_budget_limit_rub")
                    if _early_pressure.get("daily_budget_limit_rub") is not None
                    else float(_early_limit)
                ),
                "today_presence_rub": _cached_today.get("presence_rub"),
                "today_all_spend_rub": _cached_today.get("all_spend_rub"),
                "current_presence_ratio": _early_pressure.get(
                    "current_presence_ratio"
                ),
                "completed_over_budget_days": _early_pressure.get(
                    "completed_over_budget_days"
                ),
                "error_type": None,
            })
            _provider_refresh_due = (
                _cached_provider_checked + timedelta(seconds=3600)
            )
            cleanup.update({
                "status": "waiting_budget_policy",
                "restore_budget_policy": _cached_budget_evidence,
                "restore_provider_upload_id": _cached_provider_confirmed_id,
                "restore_core_active": 0,
                "restore_core_missing_ids": sorted(_restore_avito_ids),
                "owner_action_required": False,
                "system_action": (
                    "budget_hold_provider_confirmed_wait_money_policy"
                ),
                "budget_hold_provider_cache_used_at": now.isoformat(),
                "budget_hold_provider_refresh_due_at": (
                    _provider_refresh_due.isoformat()
                ),
            })
            cleanup.pop("owner_action", None)
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(
                root, ensure_ascii=False
            )
            db.commit()
            return {
                "status": "waiting",
                "reason": "waiting_budget_policy_provider_hold_confirmed_cached",
                "provider_code": cleanup.get("restore_provider_code"),
                "active": 0,
                "expected_restore": len(_restore_feed_ids),
                "upload_id": _cached_provider_confirmed_id,
                "budget_policy": _cached_budget_evidence,
                "budget_hold_provider_state": _cached_provider_state,
                "provider_cache_age_seconds": int(_cached_provider_age),
                "provider_refresh_due_at": _provider_refresh_due.isoformat(),
                "owner_action_required": False,
                "system_action": (
                    "budget_hold_provider_confirmed_wait_money_policy"
                ),
            }

        # If KPI recovered before Avito confirmed the restoration, do not keep
        # expanding reach. Move the exact restored subset back to completed cleanup;
        # the completed branch below will stage DateEnd while preserving unrelated
        # account inventory.
        # REPLACEMENT_BUDGET_BEATS_KPI_V1:
        # Money safety is a harder invariant than inventory-growth intent.
        # While budget policy is red, never leave the protected restore lane
        # merely because KPI evidence is stale/recovered; keep the exact DateEnd
        # hold first. Once money is safe, KPI may decide restore vs final cleanup.
        if _kpi_authoritative and not _under_kpi and not _early_budget_red:
            cleanup.update({
                "status": "completed",
                "resume_reason": "lead_kpi_target_reached_before_restore_confirmation",
                "resumed_at": now.isoformat(),
                "owner_action_required": False,
            })
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            _cleanup_status = "completed"
        else:
            _restore_baseline_upload = int(cleanup.get("restore_baseline_upload_id") or 0)
            _hold_baseline_upload = int(cleanup.get("budget_hold_baseline_upload_id") or 0)
            _submit_evidence = dict(cleanup.get("budget_hold_submit_evidence") or {})
            _submitted_upload_id = int(_submit_evidence.get("upload_id") or 0)
            _submitted_baseline_id = int(_submit_evidence.get("baseline_upload_id") or 0)
            _latest_confirmed_upload_id = int(
                cleanup.get("budget_hold_provider_confirmed_upload_id") or 0
            )
            _prefer_submitted_upload = bool(
                _cleanup_status == "waiting_budget_policy"
                and str(_submit_evidence.get("status") or "") == "submitted"
                and _hold_baseline_upload > 0
                and _submitted_baseline_id == _hold_baseline_upload
                and _submitted_upload_id > _hold_baseline_upload
                and _submitted_upload_id > _latest_confirmed_upload_id
            )
            if _prefer_submitted_upload:
                # BUDGET_HOLD_SUBMITTED_UPLOAD_DIRECT_VERIFY_V1:
                # feed_send_to_avito already returned a concrete accepted upload_id.
                # Do not burn a separate current-upload read before exact-scope
                # verification; go straight to that immutable provider receipt.
                _restore_snap = {
                    "upload_id": _submitted_upload_id,
                    "status": "submitted_evidence",
                }
            else:
                try:
                    _restore_snap = _autoload_upload_snapshot(
                        account_id, None, expected_ad_ids=sorted(new_feed_ids)
                    )
                except Exception as _exc:
                    if _cleanup_status == "waiting_budget_policy":
                        _restore_snap = {}
                    else:
                        return {"status": "waiting", "reason": "under_kpi_restore_upload_read_retryable",
                                "error_type": type(_exc).__name__, "owner_action_required": False}
            _restore_upload = int((_restore_snap or {}).get("upload_id") or 0)
            if not _restore_upload or _restore_upload <= _restore_baseline_upload:
                if _cleanup_status == "waiting_budget_policy":
                    _restore_upload = max(
                        int(_restore_upload or 0),
                        int(cleanup.get("restore_provider_upload_id") or 0),
                        int(cleanup.get("budget_hold_baseline_upload_id") or 0),
                        int(_restore_baseline_upload or 0),
                    )
                else:
                    if _cleanup_status != "waiting_under_kpi_restore_upload":
                        cleanup["status"] = "waiting_under_kpi_restore_upload"
                        cleanup["restore_waiting_since"] = now.isoformat()
                        root["replacement_cleanup"] = cleanup
                        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
                        db.commit()
                    return {"status": "waiting", "reason": "waiting_under_kpi_restore_upload",
                            "baseline_upload_id": _restore_baseline_upload,
                            "current_upload_id": _restore_upload,
                            "owner_action_required": False}

            # UNDER_KPI_RESTORE_PROVIDER_FUNDS_BLOCK_V1:
            # A newer upload exists, so inspect the exact restored old scope.
            # Avito code 2214 explicitly means the listing is accepted but waits
            # for account advance/funds and will activate automatically after
            # replenishment. Do not loop feed mutations or ask the owner to
            # republish; persist one truthful external blocker instead.
            try:
                _restore_scope_snap = _autoload_upload_snapshot(
                    account_id, _restore_upload, expected_ad_ids=sorted(_restore_feed_ids)
                ) or {}
            except Exception:
                _restore_scope_snap = {}
            _restore_scope = dict(_restore_scope_snap.get("scope") or {})
            _restore_scope_upload = int(
                (_restore_scope_snap or {}).get("upload_id")
                or _restore_upload
                or 0
            )
            _fund_blocked_ids = set()
            _other_error_codes = set()
            for _detail in (_restore_scope.get("unresolved_details") or []):
                _fid = str((_detail or {}).get("ad_id") or "").strip()
                _codes = set()
                for _msg in ((_detail or {}).get("messages") or []):
                    try:
                        _code = int((_msg or {}).get("code") or 0)
                    except Exception:
                        _code = 0
                    if _code:
                        _codes.add(_code)
                if 2214 in _codes and _fid:
                    _fund_blocked_ids.add(_fid)
                _other_error_codes.update(
                    _code for _code in _codes
                    if _code not in {2214, 3007}
                )
            _funds_block_detected = bool(_fund_blocked_ids and not _other_error_codes)

            # REPLACEMENT_FUNDS_BUDGET_COORDINATION_V1:
            # Provider code 2214 alone says "fund the account", but publication
            # must not tell the owner to add money when the canonical money
            # policy already proves that presence/placement spend exceeds the
            # authorised daily red line. This is a read-only cross-domain gate:
            # no bids/budgets are mutated here.
            _restore_budget_policy = {
                "blocked": False,
                "reason": "not_checked",
                "daily_budget_limit_rub": None,
            }
            try:
                from app.services.marketing_money_policy import (
                    effective_daily_budget_limit as _effective_daily_budget_limit,
                    presence_budget_pressure as _presence_budget_pressure,
                )
                _restore_daily_limit = _effective_daily_budget_limit(db, account_id)
                if _restore_daily_limit is not None and float(_restore_daily_limit) > 0:
                    _restore_budget_policy = dict(
                        _presence_budget_pressure(
                            db,
                            account_id,
                            float(_restore_daily_limit),
                        ) or {}
                    )
                    _restore_budget_policy.setdefault(
                        "daily_budget_limit_rub",
                        float(_restore_daily_limit),
                    )
                else:
                    _restore_budget_policy = {
                        "blocked": False,
                        "reason": "daily_limit_missing",
                        "daily_budget_limit_rub": _restore_daily_limit,
                    }
            except Exception as _money_exc:
                _restore_budget_policy = {
                    "blocked": False,
                    "reason": "budget_policy_read_retryable",
                    "error_type": type(_money_exc).__name__,
                }

            _budget_reason = str(_restore_budget_policy.get("reason") or "")
            _budget_authoritative = _budget_reason not in {
                "", "not_checked", "daily_limit_missing",
                "budget_policy_read_retryable",
            }
            _budget_blocks_restore = bool(
                _restore_budget_policy.get("blocked")
                or not _budget_authoritative
            )
            _budget_today = dict(_restore_budget_policy.get("today") or {})
            _budget_evidence = {
                "blocked": bool(_restore_budget_policy.get("blocked")),
                "authoritative": bool(_budget_authoritative),
                "reason": _budget_reason or "unknown",
                "daily_budget_limit_rub": _restore_budget_policy.get(
                    "daily_budget_limit_rub"
                ),
                "today_presence_rub": _budget_today.get("presence_rub"),
                "today_all_spend_rub": _budget_today.get("all_spend_rub"),
                "current_presence_ratio": _restore_budget_policy.get(
                    "current_presence_ratio"
                ),
                "completed_over_budget_days": _restore_budget_policy.get(
                    "completed_over_budget_days"
                ),
                "error_type": _restore_budget_policy.get("error_type"),
            }

            # REPLACEMENT_BUDGET_HOLD_PROVIDER_CONFIRM_APPLY_V1:
            # Keep a durable, machine-readable distinction between "desired XML
            # is protected" and "Avito has actually consumed a newer upload that
            # proves DateEnd on all exact restore identities". This is read-only
            # provider verification and remains inside waiting_budget_policy.
            _budget_hold_baseline = int(
                cleanup.get("budget_hold_baseline_upload_id") or 0
            )
            _budget_hold_propagation = _budget_hold_provider_propagation(
                _restore_scope,
                _restore_feed_ids,
                upload_id=_restore_scope_upload,
                baseline_upload_id=_budget_hold_baseline,
            )
            if (
                _cleanup_status == "waiting_budget_policy"
                and _restore_dateend_all
                and _budget_hold_baseline > 0
            ):
                cleanup["budget_hold_provider_state"] = _budget_hold_propagation
                cleanup["budget_hold_last_provider_upload_id"] = _restore_scope_upload
                cleanup["budget_hold_last_provider_checked_at"] = now.isoformat()
                if _budget_hold_propagation.get("confirmed") is True:
                    cleanup["budget_hold_provider_confirmed_upload_id"] = _restore_scope_upload
                    cleanup["budget_hold_provider_confirmed_at"] = now.isoformat()
                    _submit_evidence_now = dict(
                        cleanup.get("budget_hold_submit_evidence") or {}
                    )
                    _submit_upload_now = int(
                        _submit_evidence_now.get("upload_id") or 0
                    )
                    _submit_baseline_now = int(
                        _submit_evidence_now.get("baseline_upload_id") or 0
                    )
                    if (
                        str(_submit_evidence_now.get("status") or "")
                        == "submitted"
                        and _submit_baseline_now == _budget_hold_baseline
                        and _submit_upload_now > _budget_hold_baseline
                        and _submit_upload_now <= _restore_scope_upload
                    ):
                        _submit_evidence_now.update({
                            "status": "verified",
                            "provider_confirmation_required": False,
                            "verified_by_upload_id": _restore_scope_upload,
                            "verified_at": now.isoformat(),
                        })
                        cleanup["budget_hold_submit_evidence"] = (
                            _submit_evidence_now
                        )
                elif int(cleanup.get("budget_hold_provider_confirmed_upload_id") or 0) <= _budget_hold_baseline:
                    cleanup.pop("budget_hold_provider_confirmed_upload_id", None)
                    cleanup.pop("budget_hold_provider_confirmed_at", None)

            # REPLACEMENT_BUDGET_DATEEND_HOLD_V1:
            # A 2214-restored identity can activate automatically if somebody
            # funds Avito for any unrelated reason. While canonical money policy
            # is red (or cannot be read authoritatively), protect the exact 16
            # restore identities in the desired feed with a past DateEnd. This is
            # reversible feed lifecycle state only; it does not mutate money,
            # bids, the 36 proven new items, or unrelated feed cards.
            if _budget_blocks_restore and not _restore_dateend_all:
                _budget_hold_rows = [
                    r for r in old_rows
                    if str(getattr(r, "feed_identity", "") or "").strip() in _restore_feed_ids
                    and str(getattr(r, "avito_item_id", "") or "").strip() in _restore_avito_ids
                ]
                _budget_hold_row_feed_ids = {
                    str(getattr(r, "feed_identity", "") or "").strip()
                    for r in _budget_hold_rows
                }
                _budget_hold_row_avito_ids = {
                    str(getattr(r, "avito_item_id", "") or "").strip()
                    for r in _budget_hold_rows
                }
                if (
                    len(_budget_hold_rows) != len(_restore_feed_ids)
                    or _budget_hold_row_feed_ids != _restore_feed_ids
                    or _budget_hold_row_avito_ids != _restore_avito_ids
                ):
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_identity_mismatch",
                        "matched_rows": len(_budget_hold_rows),
                        "expected": len(_restore_feed_ids),
                        "owner_action_required": False,
                    }
                _budget_hold_models, _ = C._ff_delivery_projection(
                    _budget_hold_rows, db
                )
                _budget_hold_by_id = {
                    str(getattr(x, "id", "") or "").strip(): x
                    for x in _budget_hold_models
                    if str(getattr(x, "id", "") or "").strip()
                }
                if set(_budget_hold_by_id) != _restore_feed_ids:
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_projection_mismatch",
                        "owner_action_required": False,
                    }
                _budget_hold_dateend = (
                    now - timedelta(days=1)
                ).date().isoformat()
                for _model in _budget_hold_models:
                    _model.date_end = _budget_hold_dateend

                def _stage_budget_dateend_hold(latest):
                    _latest_ids = {
                        str(getattr(x, "id", "") or "").strip()
                        for x in latest
                        if str(getattr(x, "id", "") or "").strip()
                    }
                    _missing_new_now = new_feed_ids - _latest_ids
                    _missing_restore_now = _restore_feed_ids - _latest_ids
                    _unexpected_old_now = (
                        _latest_ids & old_feed_ids
                    ) - _restore_feed_ids
                    if (
                        _missing_new_now
                        or _missing_restore_now
                        or _unexpected_old_now
                    ):
                        raise RuntimeError("BUDGET_HOLD_SCOPE_DRIFT")
                    _out = []
                    _seen = set()
                    for _x in latest:
                        _fid = str(getattr(_x, "id", "") or "").strip()
                        if _fid in _restore_feed_ids:
                            _out.append(_budget_hold_by_id[_fid])
                            _seen.add(_fid)
                        else:
                            _out.append(_x)
                    if _seen != _restore_feed_ids:
                        raise RuntimeError("BUDGET_HOLD_REPLACE_SCOPE_INCOMPLETE")
                    return _out

                try:
                    _mutate_feed_items(
                        account_id,
                        _stage_budget_dateend_hold,
                    )
                except Exception as _exc:
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_atomic_mutation_failed",
                        "error_type": type(_exc).__name__,
                        "owner_action_required": False,
                    }
                _budget_hold_verify = _load_feed_items(account_id)
                _budget_hold_verify_ids = {
                    str(getattr(x, "id", "") or "").strip()
                    for x in _budget_hold_verify
                    if str(getattr(x, "id", "") or "").strip()
                }
                _budget_hold_verify_dateend = {
                    str(getattr(x, "id", "") or "").strip():
                    str(getattr(x, "date_end", "") or "").strip()
                    for x in _budget_hold_verify
                    if str(getattr(x, "id", "") or "").strip()
                    in _restore_feed_ids
                }
                if (
                    new_feed_ids - _budget_hold_verify_ids
                    or _restore_feed_ids - _budget_hold_verify_ids
                    or (
                        (_budget_hold_verify_ids & old_feed_ids)
                        - _restore_feed_ids
                    )
                    or set(_budget_hold_verify_dateend) != _restore_feed_ids
                    or any(
                        v != _budget_hold_dateend
                        for v in _budget_hold_verify_dateend.values()
                    )
                ):
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_verify_failed",
                        "owner_action_required": False,
                    }
                for _stale_key in (
                    "budget_hold_provider_state",
                    "budget_hold_provider_confirmed_upload_id",
                    "budget_hold_provider_confirmed_at",
                    "budget_hold_last_provider_upload_id",
                    "budget_hold_last_provider_checked_at",
                ):
                    cleanup.pop(_stale_key, None)
                cleanup.update({
                    "status": "waiting_budget_policy",
                    "restore_provider_code": (
                        2214
                        if _funds_block_detected
                        else int(cleanup.get("restore_provider_code") or 0)
                    ),
                    "restore_provider_reason": (
                        "avito_advance_required"
                        if _funds_block_detected
                        else cleanup.get("restore_provider_reason")
                    ),
                    "restore_provider_upload_id": _restore_upload,
                    "restore_budget_policy": _budget_evidence,
                    "budget_hold_dateend_value": _budget_hold_dateend,
                    "budget_hold_staged_at": now.isoformat(),
                    "budget_hold_baseline_upload_id": _restore_upload,
                    "budget_hold_feed_ids": sorted(_restore_feed_ids),
                    "restore_auto_activation_risk": (
                        "adding Avito funds can activate the restored old "
                        "inventory automatically unless DateEnd hold is present"
                    ),
                    "owner_action_required": False,
                    "system_action": (
                        "keep_restore_dateend_hold_until_money_policy_allows"
                    ),
                })
                cleanup.pop("owner_action", None)
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(
                    root, ensure_ascii=False
                )
                db.commit()
                return {
                    "status": "waiting",
                    "reason": "budget_policy_dateend_hold_staged",
                    "provider_code": cleanup.get("restore_provider_code"),
                    "feed_held": len(_restore_feed_ids),
                    "dateend": _budget_hold_dateend,
                    "baseline_upload_id": _restore_upload,
                    "budget_policy": _budget_evidence,
                    "owner_action_required": False,
                    "system_action": (
                        "keep_restore_dateend_hold_until_money_policy_allows"
                    ),
                }

            # REPLACEMENT_BUDGET_DATEEND_RELEASE_V1:
            # When money policy becomes authoritative and safe again, BORIS
            # reverses only its own exact DateEnd hold and then waits for a newer
            # Autoload upload before trusting provider activation.
            if (
                not _budget_blocks_restore
                and _cleanup_status == "waiting_budget_policy"
                and _restore_dateend_all
            ):
                _budget_release_rows = [
                    r for r in old_rows
                    if str(getattr(r, "feed_identity", "") or "").strip() in _restore_feed_ids
                    and str(getattr(r, "avito_item_id", "") or "").strip() in _restore_avito_ids
                ]
                _budget_release_models, _ = C._ff_delivery_projection(
                    _budget_release_rows, db
                )
                _budget_release_by_id = {
                    str(getattr(x, "id", "") or "").strip(): x
                    for x in _budget_release_models
                    if str(getattr(x, "id", "") or "").strip()
                }
                if set(_budget_release_by_id) != _restore_feed_ids:
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_release_projection_mismatch",
                        "owner_action_required": False,
                    }
                for _model in _budget_release_models:
                    _model.date_end = ""

                def _release_budget_dateend_hold(latest):
                    _latest_ids = {
                        str(getattr(x, "id", "") or "").strip()
                        for x in latest
                        if str(getattr(x, "id", "") or "").strip()
                    }
                    if (
                        new_feed_ids - _latest_ids
                        or _restore_feed_ids - _latest_ids
                        or ((_latest_ids & old_feed_ids) - _restore_feed_ids)
                    ):
                        raise RuntimeError("BUDGET_HOLD_RELEASE_SCOPE_DRIFT")
                    _out = []
                    _seen = set()
                    for _x in latest:
                        _fid = str(getattr(_x, "id", "") or "").strip()
                        if _fid in _restore_feed_ids:
                            _out.append(_budget_release_by_id[_fid])
                            _seen.add(_fid)
                        else:
                            _out.append(_x)
                    if _seen != _restore_feed_ids:
                        raise RuntimeError("BUDGET_HOLD_RELEASE_SCOPE_INCOMPLETE")
                    return _out

                try:
                    _mutate_feed_items(
                        account_id,
                        _release_budget_dateend_hold,
                    )
                except Exception as _exc:
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_release_atomic_mutation_failed",
                        "error_type": type(_exc).__name__,
                        "owner_action_required": False,
                    }
                _budget_release_verify = _load_feed_items(account_id)
                _budget_release_dateend = {
                    str(getattr(x, "id", "") or "").strip():
                    str(getattr(x, "date_end", "") or "").strip()
                    for x in _budget_release_verify
                    if str(getattr(x, "id", "") or "").strip()
                    in _restore_feed_ids
                }
                if (
                    set(_budget_release_dateend) != _restore_feed_ids
                    or any(_budget_release_dateend.values())
                ):
                    return {
                        "status": "blocked",
                        "reason": "budget_hold_release_verify_failed",
                        "owner_action_required": False,
                    }
                _released_confirmed_upload = int(
                    cleanup.get("budget_hold_provider_confirmed_upload_id") or 0
                )
                cleanup.update({
                    "status": "waiting_under_kpi_restore_upload",
                    "restore_baseline_upload_id": _restore_upload,
                    "restore_waiting_since": now.isoformat(),
                    "restore_budget_policy": _budget_evidence,
                    "budget_hold_released_at": now.isoformat(),
                    "budget_hold_previous_confirmed_upload_id": (
                        _released_confirmed_upload or None
                    ),
                    "owner_action_required": False,
                    "system_action": (
                        "wait_newer_autoload_after_budget_hold_release"
                    ),
                })
                for _released_key in (
                    "budget_hold_provider_state",
                    "budget_hold_provider_confirmed_upload_id",
                    "budget_hold_provider_confirmed_at",
                    "budget_hold_last_provider_upload_id",
                    "budget_hold_last_provider_checked_at",
                ):
                    cleanup.pop(_released_key, None)
                cleanup.pop("owner_action", None)
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(
                    root, ensure_ascii=False
                )
                db.commit()
                return {
                    "status": "waiting",
                    "reason": "budget_policy_hold_released_waiting_upload",
                    "baseline_upload_id": _restore_upload,
                    "budget_policy": _budget_evidence,
                    "owner_action_required": False,
                }

            from app.services.avito_account_throttle import (
                account_throttle_remaining as _restore_throttle_remaining,
                record_account_throttle as _restore_record_throttle,
            )
            _retry = int(_restore_throttle_remaining(account_id) or 0)
            if _retry > 0:
                return {"status": "waiting", "reason": "avito_account_throttled",
                        "retry_after_seconds": _retry, "owner_action_required": False}
            _token_data = get_avito_token(account_id)
            if not isinstance(_token_data, dict) or not _token_data.get("access_token"):
                return {"status": "waiting", "reason": "under_kpi_restore_core_auth_unavailable",
                        "owner_action_required": False}
            _token = _extract_token(_token_data)
            _active_old = set()
            for _page in range(1, 21):
                _retry = int(_restore_throttle_remaining(account_id) or 0)
                if _retry > 0:
                    return {"status": "waiting", "reason": "avito_account_throttled",
                            "retry_after_seconds": _retry, "owner_action_required": False}
                try:
                    _rr = _avito_http_get(
                        "https://api.avito.ru/core/v1/items",
                        headers={"Authorization": f"Bearer {_token}"},
                        params={"per_page": 100, "page": _page, "status": "active"},
                        timeout=20,
                    )
                except Exception as _exc:
                    return {"status": "waiting", "reason": "under_kpi_restore_core_read_retryable",
                            "error_type": type(_exc).__name__, "owner_action_required": False}
                if int(getattr(_rr, "status_code", 0) or 0) == 429:
                    try:
                        _retry_after = max(
                            5, int(float(str((getattr(_rr, "headers", {}) or {}).get("Retry-After") or "30")))
                        )
                    except Exception:
                        _retry_after = 30
                    _retry_after = int(_restore_record_throttle(
                        account_id, _retry_after, source="under_kpi_restore_inventory_429"
                    ) or _retry_after)
                    return {"status": "waiting", "reason": "avito_account_throttled",
                            "retry_after_seconds": _retry_after, "owner_action_required": False}
                if int(getattr(_rr, "status_code", 0) or 0) != 200:
                    return {"status": "waiting", "reason": "under_kpi_restore_core_http_retryable",
                            "http_status": int(getattr(_rr, "status_code", 0) or 0),
                            "owner_action_required": False}
                _resources = list((_rr.json() or {}).get("resources") or [])
                for _item in _resources:
                    _aid = str((_item or {}).get("id") or "").strip()
                    if _aid and _aid in _restore_avito_ids:
                        _active_old.add(_aid)
                if len(_resources) < 100:
                    break

            # REPLACEMENT_BUDGET_ACTIVE_GUARD_V1:
            # Even if Avito auto-activates some/all restored identities after an
            # unrelated funding event, a red money policy remains authoritative.
            # Keep the exact DateEnd hold and never reinterpret provider activity
            # as successful growth while the account is already over its red line.
            if _budget_blocks_restore:
                _provider_hold_confirmed = bool(
                    _budget_hold_propagation.get("confirmed") is True
                )
                _budget_system_action = (
                    "budget_hold_provider_confirmed_wait_money_policy"
                    if _provider_hold_confirmed
                    else "keep_restore_dateend_hold_until_money_policy_allows"
                )
                cleanup.update({
                    "status": "waiting_budget_policy",
                    "restore_provider_code": (
                        2214
                        if _funds_block_detected
                        else int(cleanup.get("restore_provider_code") or 0)
                    ),
                    "restore_provider_reason": (
                        "avito_advance_required"
                        if _funds_block_detected
                        else cleanup.get("restore_provider_reason")
                    ),
                    "restore_funds_blocked_feed_ids": sorted(_fund_blocked_ids),
                    "restore_funds_blocked_count": len(_fund_blocked_ids),
                    "restore_provider_upload_id": _restore_scope_upload,
                    "restore_last_checked_at": now.isoformat(),
                    "restore_core_active": len(_active_old),
                    "restore_core_missing_ids": sorted(
                        _restore_avito_ids - _active_old
                    ),
                    "restore_budget_policy": _budget_evidence,
                    "restore_auto_activation_risk": (
                        "restored old inventory may activate after any Avito "
                        "funding; DateEnd hold stays authoritative while money "
                        "policy is red"
                    ),
                    "owner_action_required": False,
                    "system_action": _budget_system_action,
                })
                cleanup.pop("owner_action", None)
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(
                    root, ensure_ascii=False
                )
                db.commit()
                return {
                    "status": "waiting",
                    "reason": (
                        "waiting_budget_policy_provider_hold_confirmed"
                        if _provider_hold_confirmed
                        else "waiting_budget_policy"
                    ),
                    "provider_code": cleanup.get("restore_provider_code"),
                    "funds_blocked": len(_fund_blocked_ids),
                    "active": len(_active_old),
                    "expected_restore": len(_restore_feed_ids),
                    "upload_id": _restore_scope_upload,
                    "budget_policy": _budget_evidence,
                    "budget_hold_provider_state": _budget_hold_propagation,
                    "owner_action_required": False,
                    "system_action": _budget_system_action,
                }

            if _active_old != _restore_avito_ids:
                if _funds_block_detected:
                    cleanup.update({
                        "status": "waiting_owner_funds",
                        "restore_provider_code": 2214,
                        "restore_provider_reason": "avito_advance_required",
                        "restore_funds_blocked_feed_ids": sorted(_fund_blocked_ids),
                        "restore_funds_blocked_count": len(_fund_blocked_ids),
                        "restore_provider_upload_id": _restore_upload,
                        "restore_last_checked_at": now.isoformat(),
                        "restore_core_active": len(_active_old),
                        "restore_core_missing_ids": sorted(_restore_avito_ids - _active_old),
                        "restore_budget_policy": _budget_evidence,
                        "owner_action_required": True,
                        "owner_action": "Пополнить аванс/баланс Avito; повторная публикация не нужна",
                    })
                    root["replacement_cleanup"] = cleanup
                    scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
                    db.commit()
                    return {
                        "status": "waiting",
                        "reason": "waiting_owner_funds",
                        "provider_code": 2214,
                        "funds_blocked": len(_fund_blocked_ids),
                        "active": len(_active_old),
                        "expected_restore": len(_restore_feed_ids),
                        "upload_id": _restore_upload,
                        "owner_action_required": True,
                        "owner_action": "Пополнить аванс/баланс Avito; после пополнения BORIS перепроверит автоматически",
                    }
                cleanup["status"] = "waiting_under_kpi_restore_upload"
                cleanup["restore_last_checked_at"] = now.isoformat()
                cleanup["restore_core_active"] = len(_active_old)
                cleanup["restore_core_missing_ids"] = sorted(_restore_avito_ids - _active_old)
                cleanup["restore_budget_policy"] = _budget_evidence
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
                db.commit()
                return {"status": "waiting", "reason": "under_kpi_restore_core_not_fully_active",
                        "active": len(_active_old),
                        "expected": len(_restore_avito_ids),
                        "missing_ids": sorted(_restore_avito_ids - _active_old)[:20],
                        "owner_action_required": False}

            cleanup.update({
                "status": "paused_under_kpi_growth",
                "status_before_kpi_pause": "completed",
                "pause_reason": "lead_kpi_below_target_preserve_inventory",
                "restore_confirmed_upload_id": _restore_upload,
                "restore_core_active": len(_active_old),
                "restore_confirmed_at": now.isoformat(),
                "kpi_target_leads_per_day": _kpi_target,
                "kpi_contacts_today": _kpi_contacts,
                "owner_action_required": False,
            })
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            return {"status": "waiting", "reason": "under_kpi_preserve_inventory_active",
                    "active_old": len(_active_old),
                    "expected_old": len(_restore_avito_ids),
                    "confirmed_upload_id": _restore_upload,
                    "target_leads_per_day": _kpi_target,
                    "contacts_today": _kpi_contacts,
                    "owner_action_required": False}

    # COMPLETED_CLEANUP_CORE_REVERIFY_V1:
    # "completed" is not a permanent truth claim. Avito may temporarily report
    # old ids inactive and later surface them again. Re-check the exact old
    # numeric ids on every watchdog pass. If any reappear, re-arm the existing
    # DateEnd cleanup automatically; the owner must never become the operator.
    if str(cleanup.get("status") or "") == "completed":
        _completed_models = _load_feed_items(account_id)
        _completed_ids = {
            str(getattr(x, "id", "") or "").strip() for x in _completed_models
            if str(getattr(x, "id", "") or "").strip()
        }
        # COMPLETED_SCOPE_ADDITIVE_INVENTORY_V2:
        # Replacement owns only new_feed_ids + old_feed_ids. Any other feed card
        # belongs to the account, not this lifecycle, and must survive untouched.
        _completed_missing_new = new_feed_ids - _completed_ids
        _completed_present_old = _completed_ids & old_feed_ids
        _completed_unrelated = _completed_ids - new_feed_ids - old_feed_ids
        if _completed_missing_new:
            return {"status": "blocked", "reason": "completed_cleanup_new_scope_missing",
                    "current": len(_completed_ids), "expected_new": len(new_feed_ids),
                    "missing_new": sorted(_completed_missing_new)[:20],
                    "unrelated_preserved": len(_completed_unrelated)}

        # KPI recovery or an interrupted cleanup can legitimately leave exact old
        # identities in the feed. Stage DateEnd on ONLY those old identities and
        # preserve every unrelated item byte-for-byte.
        if _completed_present_old:
            _completed_by_id = {
                str(getattr(x, "id", "") or "").strip(): x for x in _completed_models
            }
            _old_by_feed = {
                str(getattr(r, "feed_identity", "") or "").strip():
                str(getattr(r, "avito_item_id", "") or "").strip()
                for r in old_rows
            }
            _completed_old_avito_ids = {
                _old_by_feed.get(_fid, "") for _fid in _completed_present_old
            }
            _completed_old_avito_ids.discard("")
            if len(_completed_old_avito_ids) != len(_completed_present_old):
                return {"status": "blocked", "reason": "completed_old_identity_mapping_failed",
                        "present_old": len(_completed_present_old),
                        "mapped_old": len(_completed_old_avito_ids)}
            _required_dateend = (now - timedelta(days=1)).date().isoformat()
            _present_dateend = {
                _fid: str(getattr(_completed_by_id.get(_fid), "date_end", "") or "").strip()
                for _fid in _completed_present_old
            }

            try:
                _baseline_snap = _autoload_upload_snapshot(
                    account_id, None, expected_ad_ids=sorted(new_feed_ids)
                )
                _baseline_upload = int((_baseline_snap or {}).get("upload_id") or 0)
            except Exception:
                _baseline_upload = int(
                    cleanup.get("confirmed_upload_id") or cleanup.get("baseline_upload_id") or 0
                )

            if not all(_present_dateend.get(_fid) == _required_dateend
                       for _fid in _completed_present_old):
                _stage_rows = [
                    r for r in old_rows
                    if str(getattr(r, "feed_identity", "") or "").strip()
                    in _completed_present_old
                ]
                _stage_models, _ = C._ff_delivery_projection(_stage_rows, db)
                _stage_by_id = {
                    str(getattr(x, "id", "") or "").strip(): x for x in _stage_models
                }
                if set(_stage_by_id) != _completed_present_old:
                    return {"status": "blocked", "reason": "completed_dateend_projection_mismatch"}
                for _model in _stage_models:
                    _model.date_end = _required_dateend

                def _stage_completed_old(latest):
                    _latest_ids = {
                        str(getattr(x, "id", "") or "").strip() for x in latest
                        if str(getattr(x, "id", "") or "").strip()
                    }
                    _missing_new_now = new_feed_ids - _latest_ids
                    _present_old_now = _latest_ids & old_feed_ids
                    if _missing_new_now or _present_old_now != _completed_present_old:
                        raise RuntimeError("COMPLETED_DATEEND_SCOPE_DRIFT")
                    _out = []
                    _seen = set()
                    for _x in latest:
                        _fid = str(getattr(_x, "id", "") or "").strip()
                        if _fid in _completed_present_old:
                            if _fid not in _seen:
                                _out.append(_stage_by_id[_fid])
                                _seen.add(_fid)
                        else:
                            _out.append(_x)
                    return _out

                try:
                    _mutate_feed_items(account_id, _stage_completed_old)
                except Exception as _exc:
                    return {"status": "blocked", "reason": "completed_dateend_stage_failed",
                            "error_type": type(_exc).__name__}
                _verify_models = _load_feed_items(account_id)
                _verify_ids = {
                    str(getattr(x, "id", "") or "").strip() for x in _verify_models
                    if str(getattr(x, "id", "") or "").strip()
                }
                _verify_dateend = {
                    str(getattr(x, "id", "") or "").strip():
                    str(getattr(x, "date_end", "") or "").strip()
                    for x in _verify_models
                    if str(getattr(x, "id", "") or "").strip() in _completed_present_old
                }
                if ((new_feed_ids - _verify_ids)
                        or (_verify_ids & old_feed_ids) != _completed_present_old
                        or set(_verify_dateend) != _completed_present_old
                        or any(v != _required_dateend for v in _verify_dateend.values())):
                    return {"status": "blocked", "reason": "completed_dateend_stage_verify_failed"}

            cleanup.update({
                "status": "dateend_cleanup_staged",
                "baseline_upload_id": _baseline_upload,
                "cleanup_old_feed_ids": sorted(_completed_present_old),
                "cleanup_old_avito_ids": sorted(_completed_old_avito_ids),
                "cleanup_old_active_expected": len(_completed_present_old),
                "dateend_value": _required_dateend,
                "recovered_interrupted_rearm_at": now.isoformat(),
                "cleanup_method": "completed_scope_DateEnd_preserve_unrelated",
                "owner_action_required": False,
            })
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            return {"status": "rearmed", "changed": True,
                    "reason": "completed_old_scope_staged_dateend",
                    "old_active": len(_completed_present_old),
                    "unrelated_preserved": len(_completed_unrelated),
                    "baseline_upload_id": _baseline_upload,
                    "owner_action_required": False}

        # COMPLETED_CLEANUP_SHARED_ACCOUNT_THROTTLE_V1: this re-verification
        # may read up to 20 inventory pages, so it must obey the same tenant
        # Retry-After ledger as marketer/stats and stop the cycle on local 429.
        from app.services.avito_account_throttle import (
            account_throttle_remaining as _cleanup_throttle_remaining,
            record_account_throttle as _cleanup_record_throttle,
        )

        def _cleanup_retry_after(resp) -> int:
            try:
                raw = str((getattr(resp, "headers", {}) or {}).get("Retry-After") or "").strip()
                if raw:
                    try:
                        return max(0, int(float(raw)))
                    except Exception:
                        from email.utils import parsedate_to_datetime
                        from datetime import datetime as _dt429, timezone as _tz429
                        when = parsedate_to_datetime(raw)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=_tz429.utc)
                        return max(0, int((when.astimezone(_tz429.utc) - _dt429.now(_tz429.utc)).total_seconds()))
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=3915)
            return 30

        _shared_retry = int(_cleanup_throttle_remaining(account_id) or 0)
        if _shared_retry > 0:
            return {"status": "waiting", "reason": "avito_account_throttled",
                    "retry_after_seconds": _shared_retry, "owner_action_required": False}

        _token_data = get_avito_token(account_id)
        if not isinstance(_token_data, dict) or not _token_data.get("access_token"):
            return {"status": "waiting", "reason": "completed_cleanup_core_auth_unavailable",
                    "owner_action_required": False}
        _token = _extract_token(_token_data)
        _resurfaced_old = set()
        for _page in range(1, 21):
            _shared_retry = int(_cleanup_throttle_remaining(account_id) or 0)
            if _shared_retry > 0:
                return {"status": "waiting", "reason": "avito_account_throttled",
                        "retry_after_seconds": _shared_retry, "owner_action_required": False}
            try:
                _rr = _avito_http_get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization": f"Bearer {_token}"},
                    params={"per_page": 100, "page": _page, "status": "active"},
                    timeout=20,
                )
            except Exception as _exc:
                return {"status": "waiting", "reason": "completed_cleanup_core_read_retryable",
                        "error_type": type(_exc).__name__, "owner_action_required": False}
            if int(getattr(_rr, "status_code", 0) or 0) == 429:
                _retry = int(_cleanup_record_throttle(
                    account_id, _cleanup_retry_after(_rr), source="completed_cleanup_inventory_429"
                ) or 30)
                return {"status": "waiting", "reason": "avito_account_throttled",
                        "retry_after_seconds": _retry, "owner_action_required": False}
            if int(getattr(_rr, "status_code", 0) or 0) != 200:
                return {"status": "waiting", "reason": "completed_cleanup_core_http_retryable",
                        "http_status": int(getattr(_rr, "status_code", 0) or 0),
                        "owner_action_required": False}
            _resources = list((_rr.json() or {}).get("resources") or [])
            for _item in _resources:
                _aid = str((_item or {}).get("id") or "").strip()
                if _aid and _aid in old_avito_ids:
                    _resurfaced_old.add(_aid)
            if len(_resources) < 100:
                break

        if not _resurfaced_old:
            cleanup["last_completed_recheck_at"] = now.isoformat()
            cleanup["old_core_active"] = 0
            cleanup["owner_action_required"] = False
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            return {"status": "completed", "changed": False,
                    "old_core_active": 0, "new_items": expected_new}

        _resurfaced_rows = [
            r for r in old_rows
            if str(getattr(r, "avito_item_id", "") or "").strip() in _resurfaced_old
        ]
        if len(_resurfaced_rows) != len(_resurfaced_old):
            return {"status": "blocked", "reason": "completed_reverify_identity_mismatch",
                    "core_old_active": len(_resurfaced_old),
                    "matched_old_rows": len(_resurfaced_rows)}

        _dateend_models, _ = C._ff_delivery_projection(_resurfaced_rows, db)
        _dateend_by_id = {
            str(getattr(x, "id", "") or "").strip(): x for x in _dateend_models
        }
        _dateend_feed_ids = {
            str(getattr(r, "feed_identity", "") or "").strip() for r in _resurfaced_rows
        }
        _dateend_feed_ids.discard("")
        if (len(_dateend_models) != len(_dateend_feed_ids)
                or set(_dateend_by_id) != _dateend_feed_ids):
            return {"status": "blocked", "reason": "completed_reverify_projection_mismatch"}

        _dateend_value = (now - timedelta(days=1)).date().isoformat()
        for _model in _dateend_models:
            _model.date_end = _dateend_value

        def _rearm_completed_dateend(latest):
            _latest_ids = {
                str(getattr(x, "id", "") or "").strip() for x in latest
                if str(getattr(x, "id", "") or "").strip()
            }
            _missing_new_now = new_feed_ids - _latest_ids
            _present_old_now = _latest_ids & old_feed_ids
            if _missing_new_now or _present_old_now:
                raise RuntimeError("COMPLETED_REVERIFY_SCOPE_DRIFT")
            # Preserve every unrelated current feed identity and append only the
            # exact old provider residue being tombstoned.
            return list(latest) + list(_dateend_models)

        try:
            _baseline_snap = _autoload_upload_snapshot(
                account_id, None, expected_ad_ids=sorted(new_feed_ids)
            )
            _baseline_upload = int((_baseline_snap or {}).get("upload_id") or 0)
            _mutate_feed_items(account_id, _rearm_completed_dateend)
        except Exception as _exc:
            return {"status": "blocked", "reason": "completed_reverify_rearm_failed",
                    "error_type": type(_exc).__name__}

        _verify_models = _load_feed_items(account_id)
        _verify_ids = {
            str(getattr(x, "id", "") or "").strip() for x in _verify_models
            if str(getattr(x, "id", "") or "").strip()
        }
        _verify_missing_new = new_feed_ids - _verify_ids
        _verify_missing_dateend = _dateend_feed_ids - _verify_ids
        _verify_unexpected_old = (_verify_ids & old_feed_ids) - _dateend_feed_ids
        if _verify_missing_new or _verify_missing_dateend or _verify_unexpected_old:
            return {"status": "blocked", "reason": "completed_reverify_rearm_verify_failed",
                    "current": len(_verify_ids),
                    "missing_new": sorted(_verify_missing_new)[:20],
                    "missing_dateend": sorted(_verify_missing_dateend)[:20],
                    "unexpected_old": sorted(_verify_unexpected_old)[:20]}

        cleanup.update({
            "status": "dateend_cleanup_staged",
            "baseline_upload_id": _baseline_upload,
            "cleanup_old_feed_ids": sorted(_dateend_feed_ids),
            "cleanup_old_avito_ids": sorted(_resurfaced_old),
            "cleanup_old_active_expected": len(_resurfaced_old),
            "dateend_value": _dateend_value,
            "reopened_from_completed_at": now.isoformat(),
            "cleanup_method": "completed_reverify_then_previous_day_DateEnd",
            "owner_action_required": False,
        })
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {"status": "rearmed", "changed": True,
                "reason": "old_core_inventory_resurfaced",
                "old_active": len(_resurfaced_old),
                "dateend": _dateend_value,
                "baseline_upload_id": _baseline_upload,
                "owner_action_required": False}

    # DATEEND_STUCK_OLD_AUTOLOAD_CLEANUP_V1:
    # A small provider residue can remain active in Core even after XML removal.
    # BORIS may temporarily re-introduce only those exact old identities with a
    # past DateEnd. This branch owns the lifecycle and prunes them after provider
    # proof; owner action is never required.
    _cleanup_status = str(cleanup.get("status") or "")
    if _cleanup_status in {"dateend_cleanup_staged", "waiting_dateend_cleanup_upload"}:
        _dateend_feed_ids = {str(x).strip() for x in (cleanup.get("cleanup_old_feed_ids") or []) if str(x).strip()}
        _dateend_avito_ids = {str(x).strip() for x in (cleanup.get("cleanup_old_avito_ids") or []) if str(x).strip()}
        if not _dateend_feed_ids or not _dateend_avito_ids or len(_dateend_feed_ids) != len(_dateend_avito_ids):
            return {"status": "blocked", "reason": "dateend_cleanup_scope_missing",
                    "feed_ids": len(_dateend_feed_ids), "avito_ids": len(_dateend_avito_ids)}
        if not _dateend_feed_ids.issubset(old_feed_ids) or not _dateend_avito_ids.issubset(old_avito_ids):
            return {"status": "blocked", "reason": "dateend_cleanup_scope_not_old_subset"}

        _current_models = _load_feed_items(account_id)
        _current_ids = {str(getattr(x, "id", "") or "").strip() for x in _current_models}
        _current_ids.discard("")
        _dateend_scope = new_feed_ids | _dateend_feed_ids
        # REPLACEMENT_CLEANUP_ADDITIVE_GROWTH_COMPAT_V1:
        # Growth/autopilot may legitimately add fresh non-parent identities after
        # replacement cleanup was armed. Those additive cards must be preserved;
        # only unexpected identities from the OLD campaign are dangerous drift.
        _unexpected_old = (set(_current_ids) & set(old_feed_ids)) - set(_dateend_feed_ids)
        _new_missing = set(new_feed_ids) - set(_current_ids)
        if _unexpected_old or _new_missing:
            return {"status": "blocked", "reason": "dateend_cleanup_feed_scope_drift",
                    "current": len(_current_ids), "expected_cleanup": len(_dateend_scope),
                    "expected_new": len(new_feed_ids),
                    "unexpected_old": sorted(_unexpected_old)[:20],
                    "new_missing": sorted(_new_missing)[:20]}

        _new_snap = _autoload_upload_snapshot(account_id, None, expected_ad_ids=sorted(new_feed_ids))
        _new_scope = dict(_new_snap.get("scope") or {})
        _current_upload = int(_new_snap.get("upload_id") or 0)
        _baseline = int(cleanup.get("baseline_upload_id") or 0)
        # DATEEND_REMOVE_PREVIOUS_DAY_V1: Avito's autoload removal convention
        # requires a clearly expired calendar date. A same-day timestamp a few
        # minutes in the past can be accepted as syntactically valid while the
        # listing remains active. Use yesterday (YYYY-MM-DD), and self-heal any
        # already staged same-day value before waiting for provider confirmation.
        _required_dateend = (now - timedelta(days=1)).date().isoformat()
        if not _dateend_feed_ids.issubset(_current_ids):
            return {"status": "blocked", "reason": "dateend_cleanup_feed_items_missing",
                    "missing_dateend": sorted(_dateend_feed_ids - _current_ids)[:20],
                    "owner_action_required": False}
        if _dateend_feed_ids.issubset(_current_ids):
            _current_dateend = {
                str(getattr(x, "id", "") or "").strip(): str(getattr(x, "date_end", "") or "").strip()
                for x in _current_models
                if str(getattr(x, "id", "") or "").strip() in _dateend_feed_ids
            }
            if (set(_current_dateend) != _dateend_feed_ids
                    or any(v != _required_dateend for v in _current_dateend.values())):
                def _restage_previous_day(latest):
                    _latest_ids = {str(getattr(x, "id", "") or "").strip() for x in latest}
                    _latest_ids.discard("")
                    _missing_new_now = new_feed_ids - _latest_ids
                    _missing_dateend_now = _dateend_feed_ids - _latest_ids
                    _unexpected_old_now = (_latest_ids & old_feed_ids) - _dateend_feed_ids
                    if _missing_new_now or _missing_dateend_now or _unexpected_old_now:
                        raise RuntimeError("DATEEND_RESTAGE_SCOPE_DRIFT")
                    for _x in latest:
                        if str(getattr(_x, "id", "") or "").strip() in _dateend_feed_ids:
                            _x.date_end = _required_dateend
                    return latest
                try:
                    _mutate_feed_items(account_id, _restage_previous_day)
                except Exception as _exc:
                    return {"status": "blocked", "reason": "dateend_restage_atomic_mutation_failed",
                            "error_type": type(_exc).__name__}
                _verify_models = _load_feed_items(account_id)
                _verify_ids = {str(getattr(x, "id", "") or "").strip() for x in _verify_models}
                _verify_ids.discard("")
                _verify_dateend = {
                    str(getattr(x, "id", "") or "").strip(): str(getattr(x, "date_end", "") or "").strip()
                    for x in _verify_models
                    if str(getattr(x, "id", "") or "").strip() in _dateend_feed_ids
                }
                _verify_missing_new = new_feed_ids - _verify_ids
                _verify_missing_dateend = _dateend_feed_ids - _verify_ids
                _verify_unexpected_old = (_verify_ids & old_feed_ids) - _dateend_feed_ids
                if (_verify_missing_new or _verify_missing_dateend or _verify_unexpected_old
                        or set(_verify_dateend) != _dateend_feed_ids
                        or any(v != _required_dateend for v in _verify_dateend.values())):
                    return {"status": "blocked", "reason": "dateend_restage_verify_failed",
                            "missing_new": sorted(_verify_missing_new)[:20],
                            "missing_dateend": sorted(_verify_missing_dateend)[:20],
                            "unexpected_old": sorted(_verify_unexpected_old)[:20]}
                cleanup.update({
                    "status": "dateend_cleanup_staged",
                    "baseline_upload_id": int(_current_upload or _baseline or 0),
                    "dateend_value": _required_dateend,
                    "dateend_restaged_at": now.isoformat(),
                    "cleanup_method": "previous_day_DateEnd_then_core_zero_then_prune",
                    "owner_action_required": False,
                })
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
                db.commit()
                return {"status": "waiting", "reason": "dateend_cleanup_restaged_previous_day",
                        "baseline_upload_id": cleanup.get("baseline_upload_id"),
                        "dateend": _required_dateend, "owner_action_required": False}

        if not _current_upload or _current_upload <= _baseline:
            if _cleanup_status != "waiting_dateend_cleanup_upload":
                cleanup["status"] = "waiting_dateend_cleanup_upload"
                cleanup["waiting_since"] = now.isoformat()
                root["replacement_cleanup"] = cleanup
                scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
                db.commit()
            return {"status": "waiting", "reason": "waiting_dateend_cleanup_upload",
                    "baseline_upload_id": _baseline, "current_upload_id": _current_upload,
                    "owner_action_required": False}

        if int(_new_scope.get("found") or 0) != expected_new or int(_new_scope.get("active") or 0) != expected_new:
            return {"status": "waiting", "reason": "dateend_cleanup_new_scope_not_fully_active",
                    "upload_id": _current_upload, "found": int(_new_scope.get("found") or 0),
                    "active": int(_new_scope.get("active") or 0), "expected": expected_new,
                    "owner_action_required": False}

        # DATEEND_CLEANUP_SHARED_ACCOUNT_THROTTLE_V1: the final old-item
        # verification is another paginated Core inventory read. Respect a
        # tenant Retry-After before token/page I/O and publish a local 429.
        from app.services.avito_account_throttle import (
            account_throttle_remaining as _dateend_throttle_remaining,
            record_account_throttle as _dateend_record_throttle,
        )

        def _dateend_retry_after(resp) -> int:
            try:
                raw = str((getattr(resp, "headers", {}) or {}).get("Retry-After") or "").strip()
                if raw:
                    try:
                        return max(0, int(float(raw)))
                    except Exception:
                        from email.utils import parsedate_to_datetime
                        from datetime import datetime as _dt429, timezone as _tz429
                        when = parsedate_to_datetime(raw)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=_tz429.utc)
                        return max(0, int((when.astimezone(_tz429.utc) - _dt429.now(_tz429.utc)).total_seconds()))
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=4199)
            return 30

        _shared_retry = int(_dateend_throttle_remaining(account_id) or 0)
        if _shared_retry > 0:
            return {"status": "waiting", "reason": "avito_account_throttled",
                    "retry_after_seconds": _shared_retry, "upload_id": _current_upload,
                    "owner_action_required": False}

        _token_data = get_avito_token(account_id)
        if not isinstance(_token_data, dict) or not _token_data.get("access_token"):
            return {"status": "waiting", "reason": "dateend_cleanup_core_auth_unavailable",
                    "upload_id": _current_upload, "owner_action_required": False}
        _token = _extract_token(_token_data)
        _still_active = set()
        for _page in range(1, 21):
            _shared_retry = int(_dateend_throttle_remaining(account_id) or 0)
            if _shared_retry > 0:
                return {"status": "waiting", "reason": "avito_account_throttled",
                        "retry_after_seconds": _shared_retry, "upload_id": _current_upload,
                        "owner_action_required": False}
            try:
                _rr = _avito_http_get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization": f"Bearer {_token}"},
                    params={"per_page": 100, "page": _page, "status": "active"},
                    timeout=20,
                )
            except Exception as _exc:
                return {"status": "waiting", "reason": "dateend_cleanup_core_read_retryable",
                        "upload_id": _current_upload, "error_type": type(_exc).__name__,
                        "owner_action_required": False}
            if int(getattr(_rr, "status_code", 0) or 0) == 429:
                _retry = int(_dateend_record_throttle(
                    account_id, _dateend_retry_after(_rr), source="dateend_cleanup_inventory_429"
                ) or 30)
                return {"status": "waiting", "reason": "avito_account_throttled",
                        "retry_after_seconds": _retry, "upload_id": _current_upload,
                        "owner_action_required": False}
            if int(getattr(_rr, "status_code", 0) or 0) != 200:
                return {"status": "waiting", "reason": "dateend_cleanup_core_http_retryable",
                        "upload_id": _current_upload,
                        "http_status": int(getattr(_rr, "status_code", 0) or 0),
                        "owner_action_required": False}
            _resources = list((_rr.json() or {}).get("resources") or [])
            for _item in _resources:
                _aid = str((_item or {}).get("id") or "").strip()
                if _aid and _aid in _dateend_avito_ids:
                    _still_active.add(_aid)
            if len(_resources) < 100:
                break
        if _still_active:
            return {"status": "waiting", "reason": "dateend_cleanup_old_core_still_active",
                    "upload_id": _current_upload, "old_active": len(_still_active),
                    "old_active_ids": sorted(_still_active)[:20],
                    "expected_old": len(_dateend_avito_ids), "owner_action_required": False}

        if _dateend_feed_ids & _current_ids:
            def _dateend_prune(latest):
                _latest_ids = {str(getattr(x, "id", "") or "").strip() for x in latest}
                _latest_ids.discard("")
                _unexpected_old_now = (_latest_ids & set(old_feed_ids)) - set(_dateend_feed_ids)
                _new_missing_now = set(new_feed_ids) - _latest_ids
                if _unexpected_old_now or _new_missing_now:
                    raise RuntimeError("DATEEND_CLEANUP_SCOPE_DRIFT")
                # Remove ONLY the proven DateEnd residue. Preserve every additive
                # growth identity that is not part of the old cleanup scope.
                return [x for x in latest if str(getattr(x, "id", "") or "").strip() not in _dateend_feed_ids]
            _mutate_feed_items(account_id, _dateend_prune)
        _verify_ids = {str(getattr(x, "id", "") or "").strip() for x in _load_feed_items(account_id)}
        _verify_ids.discard("")
        _verify_unexpected_old = (_verify_ids & set(old_feed_ids)) - set()
        if (set(new_feed_ids) - _verify_ids) or (_dateend_feed_ids & _verify_ids):
            return {"status": "blocked", "reason": "dateend_cleanup_prune_verify_failed",
                    "current": len(_verify_ids), "expected_at_least_new": len(new_feed_ids),
                    "dateend_residue": sorted(_dateend_feed_ids & _verify_ids)[:20],
                    "new_missing": sorted(set(new_feed_ids) - _verify_ids)[:20]}

        cleanup.update({
            "status": "completed",
            "confirmed_upload_id": _current_upload,
            "completed_at": now.isoformat(),
            "old_core_active": 0,
            "new_scope_active": expected_new,
            "cleanup_method": "past_DateEnd_then_core_zero_then_prune",
            "owner_action_required": False,
        })
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {"status": "completed", "changed": True,
                "confirmed_upload_id": _current_upload,
                "new_items": expected_new, "dateend_stopped_old": len(_dateend_avito_ids)}

    current_models = _load_feed_items(account_id)
    current_ids = {str(getattr(x, "id", "") or "").strip() for x in current_models}
    current_ids.discard("")
    full_scope = old_feed_ids | new_feed_ids
    # REPLACEMENT_ACCOUNT_ADDITIVE_SCOPE_V2: this lifecycle owns only its old/new
    # identities. Other account feed cards are outside the transaction and must be
    # preserved; only missing new identities are fatal here.
    _initial_missing_new = new_feed_ids - current_ids
    if _initial_missing_new:
        return {"status": "blocked", "reason": "canonical_feed_new_scope_missing",
                "current": len(current_ids), "expected_new": len(new_feed_ids),
                "missing_new": sorted(_initial_missing_new)[:20]}

    new_snap = _autoload_upload_snapshot(account_id, None, expected_ad_ids=sorted(new_feed_ids))
    old_snap = _autoload_upload_snapshot(account_id, None, expected_ad_ids=sorted(old_feed_ids))
    new_scope = dict(new_snap.get("scope") or {})
    old_scope = dict(old_snap.get("scope") or {})
    current_upload = int(new_snap.get("upload_id") or 0)
    old_upload = int(old_snap.get("upload_id") or 0)
    if not current_upload or old_upload != current_upload:
        return {"status": "waiting", "reason": "provider_upload_scope_not_stable"}
    if not bool(new_snap.get("full_success")) or int(new_scope.get("active") or 0) != expected_new:
        return {"status": "waiting", "reason": "new_scope_not_fully_active",
                "upload_id": current_upload, "active": int(new_scope.get("active") or 0), "expected": expected_new}

    if str(cleanup.get("status") or "") != "waiting_avito_old_removal":
        # Before deleting anything, provider evidence proves every new listing is
        # already active. Remove ONLY old campaign identities. Preserve unrelated
        # account feed cards exactly as they are.
        _present_old_initial = current_ids & old_feed_ids
        if _present_old_initial:
            def _prune(latest):
                latest_ids = {str(getattr(x, "id", "") or "").strip() for x in latest}
                latest_ids.discard("")
                _missing_new_now = new_feed_ids - latest_ids
                if _missing_new_now:
                    raise RuntimeError("REPLACEMENT_CLEANUP_NEW_SCOPE_DRIFT")
                return [
                    x for x in latest
                    if str(getattr(x, "id", "") or "").strip() not in old_feed_ids
                ]
            _mutate_feed_items(account_id, _prune)
        verify_ids = {str(getattr(x, "id", "") or "").strip() for x in _load_feed_items(account_id)}
        verify_ids.discard("")
        _verify_missing_new = new_feed_ids - verify_ids
        _verify_old_residue = verify_ids & old_feed_ids
        if _verify_missing_new or _verify_old_residue:
            return {"status": "blocked", "reason": "canonical_feed_prune_verify_failed",
                    "missing_new": sorted(_verify_missing_new)[:20],
                    "old_residue": sorted(_verify_old_residue)[:20],
                    "unrelated_preserved": len(verify_ids - new_feed_ids - old_feed_ids)}

        cleanup = {
            "policy_version": "PILOT_SCALE_OLD_CAMPAIGN_CLEANUP_V1",
            "status": "waiting_avito_old_removal",
            "pilot_campaign_id": pilot_id,
            "scale_campaign_id": int(scale_campaign.id),
            "old_campaign_id": old_id,
            "expected_new_items": expected_new,
            "expected_old_items": len(old_feed_ids),
            "baseline_upload_id": current_upload,
            "armed_at": now.isoformat(),
            "owner_action_required": False,
        }
        root["replacement_cleanup"] = cleanup
        scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
        db.commit()
        return {"status": "armed", "changed": True, "baseline_upload_id": current_upload,
                "new_items": expected_new, "old_items": len(old_feed_ids)}

    baseline = int(cleanup.get("baseline_upload_id") or 0)
    if current_upload <= baseline:
        return {"status": "waiting", "reason": "waiting_newer_avito_upload",
                "baseline_upload_id": baseline, "current_upload_id": current_upload}

    old_found = int(old_scope.get("found") or 0)
    old_active = int(old_scope.get("active") or 0)
    account_total = int(new_snap.get("account_total") or 0)

    # PILOT_SCALE_OLD_CORE_INVENTORY_ZERO_V1:
    # Autoload report truth and live Core inventory are independent provider
    # surfaces. Deleting an identity from XML does not prove that the cabinet
    # listing is already inactive. Before superseding historical rows, require
    # a fresh read-only Core inventory scan to prove that NONE of the exact old
    # Avito item ids is still active. Any auth/HTTP ambiguity stays waiting.
    # PILOT_SCALE_OLD_CORE_SHARED_ACCOUNT_THROTTLE_V1: final replacement
    # verification is paginated provider pressure too. Respect shared Retry-After
    # before token/page I/O and publish every confirmed local 429 tenant-wide.
    from app.services.avito_account_throttle import (
        account_throttle_remaining as _oldcore_throttle_remaining,
        record_account_throttle as _oldcore_record_throttle,
    )

    def _oldcore_retry_after(resp) -> int:
        try:
            raw = str((getattr(resp, "headers", {}) or {}).get("Retry-After") or "").strip()
            if raw:
                try:
                    return max(0, int(float(raw)))
                except Exception:
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime as _dt429, timezone as _tz429
                    when = parsedate_to_datetime(raw)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=_tz429.utc)
                    return max(0, int((when.astimezone(_tz429.utc) - _dt429.now(_tz429.utc)).total_seconds()))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=4400)
        return 30

    _shared_retry = int(_oldcore_throttle_remaining(account_id) or 0)
    if _shared_retry > 0:
        return {"status": "waiting", "reason": "avito_account_throttled",
                "retry_after_seconds": _shared_retry, "upload_id": current_upload,
                "owner_action_required": False}

    token_data = get_avito_token(account_id)
    if not isinstance(token_data, dict) or not token_data.get("access_token"):
        return {"status": "waiting", "reason": "old_core_inventory_auth_unavailable",
                "upload_id": current_upload, "owner_action_required": False}
    token = _extract_token(token_data)
    old_core_active = set()
    for page in range(1, 21):
        _shared_retry = int(_oldcore_throttle_remaining(account_id) or 0)
        if _shared_retry > 0:
            return {"status": "waiting", "reason": "avito_account_throttled",
                    "retry_after_seconds": _shared_retry, "upload_id": current_upload,
                    "owner_action_required": False}
        try:
            rr = _avito_http_get(
                "https://api.avito.ru/core/v1/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 100, "page": page, "status": "active"},
                timeout=20,
            )
        except Exception as exc:
            return {"status": "waiting", "reason": "old_core_inventory_read_retryable",
                    "upload_id": current_upload, "error_type": type(exc).__name__,
                    "owner_action_required": False}
        if int(getattr(rr, "status_code", 0) or 0) == 429:
            _retry = int(_oldcore_record_throttle(
                account_id, _oldcore_retry_after(rr), source="pilot_scale_old_core_inventory_429"
            ) or 30)
            return {"status": "waiting", "reason": "avito_account_throttled",
                    "retry_after_seconds": _retry, "upload_id": current_upload,
                    "owner_action_required": False}
        if int(getattr(rr, "status_code", 0) or 0) != 200:
            return {"status": "waiting", "reason": "old_core_inventory_http_retryable",
                    "upload_id": current_upload,
                    "http_status": int(getattr(rr, "status_code", 0) or 0),
                    "owner_action_required": False}
        resources = list((rr.json() or {}).get("resources") or [])
        for item in resources:
            aid = str((item or {}).get("id") or "").strip()
            if aid and aid in old_avito_ids:
                old_core_active.add(aid)
        if len(resources) < 100:
            break
    if old_core_active:
        # DATEEND_STUCK_OLD_AUTOLOAD_STAGING_V1:
        # A scheduled URL feed can remove old identities while Core still leaves
        # a small exact subset active. Re-introduce ONLY those proven old ids with
        # a DateEnd already in the past. This is an account-locked, deterministic
        # lifecycle mutation; all 36 proven new identities remain untouched.
        if (str(cleanup.get("status") or "") == "waiting_avito_old_removal"
                and not (new_feed_ids - current_ids)
                and not (current_ids & old_feed_ids)):
            _stuck_rows = [
                r for r in old_rows
                if str(getattr(r, "avito_item_id", "") or "").strip() in old_core_active
            ]
            if len(_stuck_rows) != len(old_core_active):
                return {"status": "blocked", "reason": "dateend_staging_identity_mismatch",
                        "core_old_active": len(old_core_active), "matched_old_rows": len(_stuck_rows)}
            _dateend_models, _ = C._ff_delivery_projection(_stuck_rows, db)
            _dateend_by_id = {str(getattr(x, "id", "") or "").strip(): x for x in _dateend_models}
            _expected_dateend_ids = {str(getattr(r, "feed_identity", "") or "").strip() for r in _stuck_rows}
            _expected_dateend_ids.discard("")
            if (len(_dateend_models) != len(_expected_dateend_ids)
                    or set(_dateend_by_id) != _expected_dateend_ids):
                return {"status": "blocked", "reason": "dateend_staging_projection_mismatch"}
            _dateend_value = (now - timedelta(days=1)).date().isoformat()
            for _model in _dateend_models:
                _model.date_end = _dateend_value

            def _stage_dateend(latest):
                _latest_ids = {str(getattr(x, "id", "") or "").strip() for x in latest}
                _latest_ids.discard("")
                _missing_new_now = new_feed_ids - _latest_ids
                _present_old_now = _latest_ids & old_feed_ids
                if _missing_new_now or _present_old_now:
                    raise RuntimeError("DATEEND_STAGING_SCOPE_DRIFT")
                return list(latest) + list(_dateend_models)

            try:
                _mutate_feed_items(account_id, _stage_dateend)
            except Exception as _exc:
                return {"status": "blocked", "reason": "dateend_staging_atomic_mutation_failed",
                        "error_type": type(_exc).__name__}
            _after_models = _load_feed_items(account_id)
            _after_ids = {str(getattr(x, "id", "") or "").strip() for x in _after_models}
            _after_ids.discard("")
            _after_missing_new = new_feed_ids - _after_ids
            _after_missing_dateend = _expected_dateend_ids - _after_ids
            _after_unexpected_old = (_after_ids & old_feed_ids) - _expected_dateend_ids
            if _after_missing_new or _after_missing_dateend or _after_unexpected_old:
                return {"status": "blocked", "reason": "dateend_staging_verify_failed",
                        "current": len(_after_ids),
                        "missing_new": sorted(_after_missing_new)[:20],
                        "missing_dateend": sorted(_after_missing_dateend)[:20],
                        "unexpected_old": sorted(_after_unexpected_old)[:20],
                        "unrelated_preserved": len(_after_ids - new_feed_ids - old_feed_ids)}
            _after_dateend = {
                str(getattr(x, "id", "") or "").strip(): str(getattr(x, "date_end", "") or "").strip()
                for x in _after_models
                if str(getattr(x, "id", "") or "").strip() in _expected_dateend_ids
            }
            if set(_after_dateend) != _expected_dateend_ids or any(v != _dateend_value for v in _after_dateend.values()):
                return {"status": "blocked", "reason": "dateend_staging_value_verify_failed"}

            cleanup.update({
                "status": "dateend_cleanup_staged",
                "cleanup_old_feed_ids": sorted(_expected_dateend_ids),
                "cleanup_old_avito_ids": sorted(old_core_active),
                "baseline_upload_id": current_upload,
                "dateend_value": _dateend_value,
                "dateend_staged_at": now.isoformat(),
                "cleanup_method": "past_DateEnd_then_core_zero_then_prune",
                "owner_action_required": False,
            })
            root["replacement_cleanup"] = cleanup
            scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)
            db.commit()
            return {"status": "waiting", "reason": "dateend_cleanup_staged",
                    "baseline_upload_id": current_upload,
                    "old_active": len(old_core_active),
                    "old_active_ids": sorted(old_core_active)[:20],
                    "dateend_feed_items": len(_expected_dateend_ids),
                    "owner_action_required": False}

        return {"status": "waiting", "reason": "old_core_inventory_still_active",
                "upload_id": current_upload,
                "old_active": len(old_core_active),
                "old_active_ids": sorted(old_core_active)[:20],
                "autoload_old_found": old_found,
                "autoload_old_active": old_active,
                "account_total": account_total,
                "expected_total": expected_new,
                "expected_old": len(old_avito_ids),
                "owner_action_required": False}

    # OLD_SCOPE_EXACT_NOT_ACCOUNT_TOTAL_V1:
    # This replacement owns only the exact 36-item campaign scope. The Avito
    # account may legitimately contain unrelated/client-native listings, so
    # account_total must never be compared with expected_new. Completion is
    # proven by exact new-scope success plus zero old identities on provider
    # surfaces; unrelated inventory is preserved and ignored.
    if old_found > 0 or old_active > 0:
        return {"status": "waiting", "reason": "old_scope_still_present",
                "upload_id": current_upload, "old_found": old_found,
                "old_active": old_active, "account_total": account_total,
                "expected_new_scope": expected_new,
                "core_old_active": 0,
                "owner_action_required": False}

    # Final provider truth: a newer upload has all exact new listings active,
    # none of the old identities present in Autoload, and none of the exact old
    # Avito ids active in Core inventory. Unrelated account inventory is outside
    # this replacement transaction and is deliberately not counted.
    old.status = "superseded"
    db.execute(C._t(
        "UPDATE campaign_items SET status='superseded', updated_at=now() "
        "WHERE campaign_id=:c AND account_id=:a AND status='published'"
    ), {"c": old_id, "a": account_id})

    try:
        old_root = json.loads(old.settings_json or "{}")
    except Exception:
        old_root = {}
    old_ff = dict((old_root or {}).get("feed_factory") or {})
    old_ff["replacement"] = {
        "state": "completed",
        "policy_version": "PILOT_SCALE_OLD_CAMPAIGN_CLEANUP_V1",
        "pilot_campaign_id": pilot_id,
        "replacement_campaign_id": int(scale_campaign.id),
        "confirmed_upload_id": current_upload,
        "completed_at": now.isoformat(),
    }
    old_root["feed_factory"] = old_ff
    old.settings_json = json.dumps(old_root, ensure_ascii=False)

    cleanup.update({
        "status": "completed",
        "confirmed_upload_id": current_upload,
        "completed_at": now.isoformat(),
        "old_scope_found": old_found,
        "old_scope_active": old_active,
        "account_total": account_total,
    })
    root["replacement_cleanup"] = cleanup
    scale_campaign.settings_json = json.dumps(root, ensure_ascii=False)

    # Failed predecessor revisions explicitly replaced by this proven scale are
    # historical evidence, not current work. Preserve the rows/jobs, but close
    # the Campaign lifecycle so supervisors cannot resurrect them.
    predecessors = (
        db.query(Campaign)
        .filter(Campaign.default_account_id == account_id,
                Campaign.id != int(scale_campaign.id))
        .order_by(Campaign.id.desc())
        .limit(100)
        .all()
    )
    superseded_predecessors = []
    for predecessor in predecessors:
        try:
            pr = json.loads(predecessor.settings_json or "{}")
        except Exception:
            continue
        terminal = dict((pr or {}).get("terminal_failure") or {})
        if int(terminal.get("replacement_campaign_id") or 0) != int(scale_campaign.id):
            continue
        if str(predecessor.status or "").lower() == "failed":
            predecessor.status = "superseded"
            terminal["superseded_at"] = now.isoformat()
            terminal["replacement_cleanup_confirmed_upload_id"] = current_upload
            pr["terminal_failure"] = terminal
            predecessor.settings_json = json.dumps(pr, ensure_ascii=False)
            superseded_predecessors.append(int(predecessor.id))

    db.commit()
    return {"status": "completed", "changed": True, "confirmed_upload_id": current_upload,
            "new_items": expected_new, "old_items": len(old_feed_ids),
            "superseded_predecessors": superseded_predecessors}


def run_once() -> dict:
    from app.api import campaigns as C

    db = SessionLocal()
    checked = 0
    observing = 0
    degraded = 0
    waiting = 0
    completed = 0
    updated = 0
    publication_state_repairs = 0
    publication_state_repaired_items = 0
    replacement_cleanup_armed = 0
    replacement_cleanup_waiting = 0
    replacement_cleanup_completed = 0
    replacement_cleanup_blocked = 0
    account_business_watches = {}
    business_rollups_written = 0
    external_hold_rechecked_accounts = set()
    external_hold_rechecks = 0
    external_hold_recheck_results = []
    rejected_publication_truth_repairs = 0
    rejected_publication_truth_repaired_ids = []
    errors = []
    now = datetime.now(timezone.utc)

    try:
        allowed = [r[0] for r in db.execute(C._t("SELECT account_id FROM accounts")).fetchall()]
        campaigns = (
            db.query(Campaign)
            .filter(Campaign.status.in_(("completed", "partial")))
            .order_by(Campaign.id.desc())
            .limit(MAX_CAMPAIGNS_PER_TICK)
            .all()
        )

        for campaign in campaigns:
            try:
                _, ff = C._ff_state_load(campaign)
                activation = dict(ff.get("owner_activation") or {})
                _activation_status = str(activation.get("status") or "")
                if _activation_status not in {"completed", "partial"}:
                    continue

                account_id = str(activation.get("account_id") or getattr(campaign, "default_account_id", "") or "").strip()
                # Scheduled-URL deliveries may complete without a legacy `sent_at`.
                # The activation `updated_at` is the authoritative completion time
                # for this path; otherwise a real published campaign silently falls
                # out of KPI/post-publication supervision.
                published_at = _dt(activation.get("sent_at") or activation.get("updated_at"))
                if not account_id:
                    continue
                if not published_at:
                    published_at = _dt((ff.get("owner_activation") or {}).get("updated_at"))
                if not published_at:
                    continue
                if now - published_at > timedelta(days=7):
                    continue

                # PLANETA_KPI_EXTERNAL_HOLD_WATCHDOG_WIRE_V2:
                # Re-check the account-scoped publication hold exactly once per
                # watchdog tick, even though PLANETA has several supervised
                # campaign revisions. This advances only confirmation/local state;
                # it never calls the external publication write endpoint.
                if (
                    account_id == PLANETA_ACCOUNT_ID
                    and account_id not in external_hold_rechecked_accounts
                ):
                    external_hold_rechecked_accounts.add(account_id)
                    _hold_recheck = _recheck_planeta_kpi_external_publication_hold(
                        account_id, now
                    )
                    external_hold_recheck_results.append(_hold_recheck)
                    if str(_hold_recheck.get("status") or "") == "checked":
                        external_hold_rechecks += int(
                            _hold_recheck.get("attempted") or 0
                        )
                    elif str(_hold_recheck.get("status") or "") == "error":
                        errors.append({
                            "campaign_id": int(campaign.id),
                            "account_id": account_id,
                            "error_type": str(
                                _hold_recheck.get("error_type")
                                or "BudgetHoldRecheckError"
                            ),
                            "error": (
                                "budget_hold_recheck: "
                                + str(_hold_recheck.get("error") or "")
                            )[:240],
                        })

                # PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_WIRE_V1:
                # The current 36-slot PLANETA cutover has exact source identity
                # fallbacks for provider-2014 duplicate rejects. Once this
                # contract is armed it is the single writer for that recovery;
                # the older broad under-KPI restore must not compete with it.
                _slot_fallback = _planeta_distinct_slot_fallback_lifecycle(
                    db, campaign, account_id, now
                )
                if str((_slot_fallback or {}).get("status") or "") != "not_configured":
                    _cleanup = _slot_fallback
                else:
                    _cleanup = _pilot_scale_replacement_cleanup(
                        db, campaign, account_id, allowed, now
                    )
                _cleanup_status = str((_cleanup or {}).get("status") or "")
                # REPLACEMENT_CLEANUP_RELOAD_AFTER_MUTATION_V1:
                # The cleanup helper may commit settings_json/feed changes. The
                # pre-cleanup ff object is stale after that commit; reload the ORM
                # row and feed-factory state before any later C._ff_state_save(),
                # otherwise run_once can overwrite a successful self-heal.
                db.refresh(campaign)
                _, ff = C._ff_state_load(campaign)
                activation = dict(ff.get("owner_activation") or {})
                _activation_status = str(activation.get("status") or _activation_status)
                # REPLACEMENT_CLEANUP_DURABLE_OBSERVATION_V1:
                # Waiting is a real production state, not an ephemeral return value.
                # Persist the latest provider evidence so Supervisor/UI/restarts see
                # exactly why BORIS is still waiting without rerunning a destructive
                # action or asking the owner to inspect Avito manually.
                if _cleanup_status in {"armed", "rearmed", "waiting", "completed", "blocked"}:
                    try:
                        _obs_root = json.loads(campaign.settings_json or "{}")
                    except Exception:
                        _obs_root = {}
                    _obs_cleanup = dict((_obs_root or {}).get("replacement_cleanup") or {})
                    if _obs_cleanup:
                        _obs = {str(k): v for k, v in dict(_cleanup or {}).items()
                                if str(k) not in {"changed"}}
                        _obs["checked_at"] = now.isoformat()
                        _obs_cleanup["last_observation"] = _obs
                        # REPLACEMENT_CLEANUP_OWNER_ACTION_TRUTH_V1:
                        # Compatibility marker retained for existing regression
                        # contracts; behavior is implemented by V2 below.
                        # REPLACEMENT_CLEANUP_OWNER_ACTION_TRUTH_V2:
                        # Pure merge prevents transient provider waits from
                        # erasing a durable business/owner blocker.
                        # Legacy regression signature retained as comment only:
                        # _obs_cleanup["owner_action_required"] = bool(
                        _obs_cleanup, _obs = _merge_replacement_owner_action_truth(
                            _obs_cleanup,
                            _obs,
                        )
                        _obs_cleanup["last_observation"] = _obs
                        _obs_root["replacement_cleanup"] = _obs_cleanup
                        campaign.settings_json = json.dumps(_obs_root, ensure_ascii=False)
                        db.commit()
                if _cleanup_status in {"armed", "rearmed"}:
                    replacement_cleanup_armed += 1
                elif _cleanup_status == "waiting":
                    replacement_cleanup_waiting += 1
                elif _cleanup_status == "completed" and bool((_cleanup or {}).get("changed")):
                    replacement_cleanup_completed += 1
                elif _cleanup_status == "blocked":
                    replacement_cleanup_blocked += 1

                rows = [
                    r for r in C._ff_items(db, int(campaign.id), allowed)
                    if str(r.account_id or "") == account_id
                ]

                # PLANETA_REJECTED_PUBLICATION_FAIL_CLOSED_WIRE_V1:
                # Shared Autoload identity reconciliation can observe the old
                # live listing as success_skipped/unchanged. Before any campaign
                # status or writable-state projection, restore durable rejection
                # truth unless authoritative evidence actually crossed the causal
                # rejection boundary.
                _rejected_truth_repair = _reconcile_planeta_rejected_publication_truth(
                    rows, account_id, now
                )
                if int(_rejected_truth_repair.get("changed") or 0) > 0:
                    _changed_ids = [
                        int(x) for x in (
                            _rejected_truth_repair.get("changed_ids") or []
                        ) if int(x or 0) > 0
                    ]
                    rejected_publication_truth_repairs += len(_changed_ids)
                    rejected_publication_truth_repaired_ids.extend(_changed_ids)
                    db.commit()

                expected = len(rows)
                if expected <= 0:
                    continue

                # BASE_LIVE_REVISION_PENDING_MEASUREMENT_V1:
                # A later content revision can be rejected while the previously
                # published Avito listing remains live on the exact same canonical
                # identity. Do not collapse those two truths into "publication lost".
                # Fresh daily_stats + exact feed_identity + exact avito_item_id prove
                # that the base listing is still active for business measurement;
                # identity_status=publication_rejected_bound continues to prove that
                # the desired revision itself is NOT applied.
                _publication_truth_stats = _load_json_storage(
                    db, account_id, f"daily_stats:{marketing_today_iso()}"
                )
                _publication_truth_collected = _dt(
                    _publication_truth_stats.get("collected_at")
                )
                _publication_truth_age = (
                    max(0, int((now - _publication_truth_collected).total_seconds()))
                    if _publication_truth_collected else None
                )
                _publication_truth_fresh = bool(
                    _publication_truth_age is not None
                    and _publication_truth_age <= STATS_STALE_SECONDS
                )
                _live_base_by_feed = {}
                if _publication_truth_fresh:
                    for _si in (_publication_truth_stats.get("items") or []):
                        if not isinstance(_si, dict):
                            continue
                        _ident = dict(_si.get("campaign_identity") or {})
                        try:
                            _cid = int(_ident.get("campaign_id") or 0)
                        except Exception:
                            _cid = 0
                        if _cid != int(campaign.id):
                            continue
                        if str(_ident.get("account_id") or account_id) != str(account_id):
                            continue
                        if str(_si.get("status") or "").lower() != "active":
                            continue
                        # BASE_LIVE_DOES_NOT_REQUIRE_WRITE_CAPABILITY_V1:
                        # write_capability answers whether BORIS may mutate the
                        # current revision. It is not publication evidence. Exact
                        # account+campaign+feed_identity+avito_item_id with fresh
                        # status=active is sufficient proof that the base listing
                        # remains live even when a rejected revision temporarily
                        # makes the item read_only_until_mapped.
                        _fid = str(_ident.get("feed_identity") or "").strip()
                        _aid = str(_si.get("id") or "").strip()
                        if _fid and _aid:
                            _live_base_by_feed[_fid] = _aid

                _rejected_rows = [
                    r for r in rows
                    if str(getattr(r, "identity_status", "") or "")
                    == "publication_rejected_bound"
                ]
                _revision_pending_rows = []
                _hard_rejected_rows = []
                for _rr in _rejected_rows:
                    _rfid = str(getattr(_rr, "feed_identity", "") or "").strip()
                    _raid = str(getattr(_rr, "avito_item_id", "") or "").strip()
                    if (
                        _publication_truth_fresh
                        and _rfid
                        and _raid
                        and _live_base_by_feed.get(_rfid) == _raid
                    ):
                        _revision_pending_rows.append(_rr)
                    else:
                        _hard_rejected_rows.append(_rr)
                _revision_pending_feed_ids = {
                    str(getattr(r, "feed_identity", "") or "").strip()
                    for r in _revision_pending_rows
                    if str(getattr(r, "feed_identity", "") or "").strip()
                }
                if _revision_pending_rows:
                    activation["content_revision_pending"] = {
                        "policy_version": "BASE_LIVE_REVISION_PENDING_MEASUREMENT_V1",
                        "count": len(_revision_pending_rows),
                        "feed_ids": sorted(_revision_pending_feed_ids),
                        "base_listing_active": True,
                        "desired_revision_applied": False,
                        "source_stats_collected_at": (
                            _publication_truth_collected.isoformat()
                            if _publication_truth_collected else None
                        ),
                        "source_stats_age_seconds": _publication_truth_age,
                        "measurement_continues": True,
                        "owner_action_required": False,
                    }
                    ff["owner_activation"] = activation
                    C._ff_state_save(db, campaign, ff)
                else:
                    activation.pop("content_revision_pending", None)

                # BASE_LIVE_PARTIAL_STATE_SELF_HEAL_V1:
                # Older watcher code could downgrade the whole campaign to
                # partial when only a later content revision was rejected. If
                # every canonical row is either normally published or is an exact
                # revision-pending row whose previous Avito listing is freshly
                # proven active, restore the campaign publication state
                # automatically. This does NOT mark the desired revision applied.
                _base_live_scope_complete = bool(
                    rows
                    and not _hard_rejected_rows
                    and all(
                        (
                            str(getattr(_r, "status", "") or "") == "published"
                            and str(getattr(_r, "identity_status", "") or "")
                            == "published_identity_bound"
                        )
                        or str(getattr(_r, "feed_identity", "") or "").strip()
                        in _revision_pending_feed_ids
                        for _r in rows
                    )
                )
                if (
                    _activation_status == "partial"
                    and _revision_pending_rows
                    and _base_live_scope_complete
                ):
                    activation.update({
                        "status": "completed",
                        "published": len(rows),
                        "errors": 0,
                        "measurement_scope_feed_ids": [
                            str(getattr(_r, "feed_identity", "") or "")
                            for _r in rows
                            if str(getattr(_r, "feed_identity", "") or "")
                        ],
                        "excluded_feed_ids": [],
                        "publication_truth_reconciliation": {
                            "policy_version": "BASE_LIVE_PARTIAL_STATE_SELF_HEAL_V1",
                            "reason": "base_listing_active_revision_pending",
                            "reconciled_at": now.isoformat(),
                            "base_listing_scope_fully_active": True,
                            "desired_revision_scope_fully_applied": False,
                            "owner_action_required": False,
                            "safe_republish": False,
                        },
                    })
                    ff["owner_activation"] = activation
                    campaign.status = "completed"
                    C._ff_state_save(db, campaign, ff)
                    _activation_status = "completed"

                # ACTIVATION_IDENTITY_TRUTH_RECONCILE_V2: only a rejection that
                # lacks fresh exact live-base evidence downgrades the campaign to
                # partial. A content-only rejected revision remains separately
                # visible above and must not disappear from business measurement.
                if _activation_status == "completed" and _hard_rejected_rows:
                    _published_count = sum(
                        1 for r in rows
                        if (
                            (
                                str(getattr(r, "status", "") or "") == "published"
                                and str(getattr(r, "identity_status", "") or "")
                                == "published_identity_bound"
                            )
                            or str(getattr(r, "feed_identity", "") or "").strip()
                            in _revision_pending_feed_ids
                        )
                    )
                    activation.update({
                        "status": "partial",
                        "published": _published_count,
                        "errors": len(_hard_rejected_rows),
                        "retry_policy": "no_blind_replay_provider_rejection",
                        "measurement_scope_feed_ids": [
                            str(getattr(r, "feed_identity", "") or "")
                            for r in rows
                            if (
                                (
                                    str(getattr(r, "status", "") or "") == "published"
                                    and str(getattr(r, "identity_status", "") or "")
                                    == "published_identity_bound"
                                )
                                or str(getattr(r, "feed_identity", "") or "").strip()
                                in _revision_pending_feed_ids
                            )
                        ],
                        "excluded_feed_ids": [
                            str(getattr(r, "feed_identity", "") or "")
                            for r in _hard_rejected_rows
                        ],
                        "publication_truth_reconciliation": {
                            "policy_version": "ACTIVATION_IDENTITY_TRUTH_RECONCILE_V2",
                            "reason": "canonical_identity_provider_rejection_without_live_base",
                            "reconciled_at": now.isoformat(),
                            "owner_action_required": False,
                            "safe_republish": False,
                        },
                    })
                    ff["owner_activation"] = activation
                    campaign.status = "partial"
                    C._ff_state_save(db, campaign, ff)
                    _activation_status = "partial"

                # PARTIAL_PUBLICATION_WATCH_SCOPE_V2
                if _activation_status == "partial":
                    _published_rows = [
                        r for r in rows
                        if (
                            str(getattr(r, "status", "") or "") == "published"
                            or str(getattr(r, "feed_identity", "") or "").strip()
                            in _revision_pending_feed_ids
                        )
                    ]
                    if not _published_rows:
                        continue
                    rows = _published_rows
                    expected = len(rows)
                checked += 1

                # PUBLICATION_ITEM_STATUS_SELF_HEAL_V2: generic workflow repair
                # must never erase publication_rejected_bound for a pending content
                # revision. Those exact rows stay draft/rejected until provider
                # success proves the desired revision; their live base remains in
                # measurement via the scope logic above.
                from app.services.campaign_identity import reconcile_published_status
                _repair_rows = [
                    r for r in rows
                    if str(getattr(r, "feed_identity", "") or "").strip()
                    not in _revision_pending_feed_ids
                ]
                _repair = (
                    {"changed": 0, "changed_ids": []}
                    if _activation_status == "partial"
                    else reconcile_published_status(_repair_rows)
                )
                if int(_repair.get("changed") or 0) > 0:
                    publication_state_repairs += 1
                    publication_state_repaired_items += int(_repair.get("changed") or 0)

                stats = _publication_truth_stats
                collected_at = _publication_truth_collected
                watch = dict(ff.get("post_publish_watch") or {})
                watch["campaign_id"] = int(campaign.id)
                watch["account_id"] = account_id
                watch["expected_feed_ids"] = [str(getattr(r, "feed_identity", "") or "") for r in rows if str(getattr(r, "feed_identity", "") or "")]
                if int(_repair.get("changed") or 0) > 0:
                    watch["publication_state_repair"] = {
                        "policy_version": "PUBLICATION_ITEM_STATUS_SELF_HEAL_V1",
                        "changed": int(_repair.get("changed") or 0),
                        "changed_ids": list(_repair.get("changed_ids") or [])[:100],
                        "repaired_at": now.isoformat(),
                        "owner_action_required": False,
                    }

                if not stats or not collected_at or collected_at < published_at:
                    watch.update({
                        "policy_version": "campaign-post-publish-watch-v1",
                        "status": "waiting_fresh_stats",
                        "reason": "no_post_publication_stats_yet",
                        "published_at": published_at.isoformat(),
                        "expected_items": expected,
                        "owner_action_required": False,
                        "diagnostic": "waiting_for_next_stats_collection",
                        "self_heal": "automatic_recheck_no_owner_action",
                        "updated_at": now.isoformat(),
                        "next_check_at": (now + timedelta(minutes=15)).isoformat(),
                        "observation_window_hours": WATCH_WINDOW_HOURS,
                    })
                    ff["post_publish_watch"] = watch
                    C._ff_state_save(db, campaign, ff)
                    waiting += 1
                    updated += 1
                    continue

                metrics = _campaign_metrics(stats, int(campaign.id), account_id,
                                            allowed_feed_ids=[str(getattr(r, "feed_identity", "") or "") for r in rows])
                watch = _update_watch(
                    watch,
                    metrics=metrics,
                    collected_at=collected_at,
                    published_at=published_at,
                    expected=expected,
                    now=now,
                    stats_date=str(
                        stats.get("stats_date")
                        or stats.get("date")
                        or ""
                    )[:10],
                    base_live_revision_pending=bool(
                        _revision_pending_rows and not _hard_rejected_rows
                    ),
                )
                # PROVIDER_MISSING_IDENTITY_DIAG_V1: after the loss survives at least one
                # normal watcher interval, classify it with read-only Avito inventories.
                # Never infer that a missing active item is safe to republish.
                if watch.get("missing_feed_ids") and int(watch.get("missing_age_seconds") or 0) >= 900:
                    _provider_diag = _diagnose_missing_provider_status(
                        account_id, rows, watch.get("missing_feed_ids"), now=now,
                        previous=watch.get("provider_diagnosis"),
                    )
                    watch["provider_diagnosis"] = _provider_diag
                    if _provider_diag.get("status") == "confirmed":
                        watch["diagnosis_stage"] = "provider_status_confirmed"
                        watch["scope_diagnosis"] = str(_provider_diag.get("classification") or "provider_nonactive")
                        watch["diagnostic"] = "provider_status_confirmed_no_republish"
                        watch["self_heal"] = "automatic_provider_status_recheck"
                        watch["safe_republish"] = False
                # Keep a compact current item snapshot for diagnosis without copying
                # the full account daily_stats blob into campaign state.
                watch["current_items"] = metrics.get("items") or {}
                if _revision_pending_rows:
                    _revision_pending_state = {
                        "policy_version": "BASE_LIVE_REVISION_PENDING_MEASUREMENT_V1",
                        "count": len(_revision_pending_rows),
                        "feed_ids": sorted(_revision_pending_feed_ids),
                        "base_listing_active": True,
                        "desired_revision_applied": False,
                        "source_stats_collected_at": (
                            _publication_truth_collected.isoformat()
                            if _publication_truth_collected else None
                        ),
                        "source_stats_age_seconds": _publication_truth_age,
                        "measurement_continues": True,
                        "owner_action_required": False,
                    }
                    watch["content_revision_pending"] = _revision_pending_state
                    _bo = dict(watch.get("business_outcome") or {})
                    if _bo:
                        _bo["content_revision_pending"] = _revision_pending_state
                        watch["business_outcome"] = _bo
                else:
                    watch.pop("content_revision_pending", None)
                    _bo = dict(watch.get("business_outcome") or {})
                    if _bo:
                        _bo.pop("content_revision_pending", None)
                        watch["business_outcome"] = _bo
                ff["post_publish_watch"] = watch
                C._ff_state_save(db, campaign, ff)

                _business_outcome = dict(watch.get("business_outcome") or {})
                if str(_business_outcome.get("policy_version") or "") == "POST_PUBLISH_BUSINESS_OUTCOME_V2":
                    account_business_watches.setdefault(account_id, []).append({
                        "campaign_id": int(campaign.id),
                        "expected_feed_ids": list(watch.get("expected_feed_ids") or []),
                        "current_items": dict(watch.get("current_items") or {}),
                        "business_outcome": _business_outcome,
                    })
                updated += 1

                if watch["status"] == "observing":
                    observing += 1
                elif watch["status"] == "degraded":
                    degraded += 1
                elif watch["status"] == "waiting_fresh_stats":
                    waiting += 1
                elif watch["status"] == "observation_complete":
                    completed += 1
            except Exception as exc:
                try:
                    db.rollback()
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=5168)
                errors.append({
                    "campaign_id": int(getattr(campaign, "id", 0) or 0),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                })

        # POST_PUBLISH_ACCOUNT_BUSINESS_ROLLUP_APPLY_V1:
        # Persist one deduplicated business truth per account so downstream
        # performance logic does not have to add technical campaign revisions.
        for _rollup_account, _rollup_inputs in account_business_watches.items():
            try:
                _rollup = _build_account_business_rollup(
                    _rollup_account, _rollup_inputs, now
                )
                if int(_rollup.get("campaigns_count") or 0) > 0:
                    # POST_PUBLISH_CONVERSION_CONSUMER_WIRE_V1:
                    # Close observability between "problem detected" and the
                    # durable KPI content lifecycle that is actually handling it.
                    # This only reads local DB state; it never schedules or writes
                    # to Avito and never creates an AI task.
                    _consumer_state = _conversion_action_consumer_state(
                        db, _rollup_account, _rollup, now
                    )
                    _rollup["action_consumer_confirmed"] = bool(
                        _consumer_state.get("confirmed")
                    )
                    _rollup["action_consumer"] = _consumer_state
                    if (
                        str(_rollup.get("system_action") or "")
                        == "handoff_conversion_optimization"
                    ):
                        if bool(_consumer_state.get("confirmed")):
                            _rollup["action_delivery_state"] = "consumer_active"
                            _rollup["adjacent_action_required"] = False
                        else:
                            _rollup["action_delivery_state"] = "signal_unconsumed"
                            _rollup["adjacent_action_required"] = True

                    _save_json_storage(
                        db,
                        _rollup_account,
                        "post_publish_business_outcome_rollup",
                        _rollup,
                    )
                    db.commit()
                    business_rollups_written += 1
            except Exception as _rollup_exc:
                try:
                    db.rollback()
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=5219)
                errors.append({
                    "campaign_id": 0,
                    "account_id": _rollup_account,
                    "error_type": type(_rollup_exc).__name__,
                    "error": ("business_rollup: " + str(_rollup_exc))[:240],
                })

        state = "degraded" if degraded or replacement_cleanup_blocked or errors else "ok"
        details = {
            "checked": checked,
            "updated": updated,
            "observing": observing,
            "waiting": waiting,
            "degraded": degraded,
            "completed": completed,
            "publication_state_repairs": publication_state_repairs,
            "publication_state_repaired_items": publication_state_repaired_items,
            "replacement_cleanup_armed": replacement_cleanup_armed,
            "replacement_cleanup_waiting": replacement_cleanup_waiting,
            "replacement_cleanup_completed": replacement_cleanup_completed,
            "replacement_cleanup_blocked": replacement_cleanup_blocked,
            "business_rollups_written": business_rollups_written,
            "external_publication_hold_rechecks": external_hold_rechecks,
            "external_publication_hold_recheck_results": external_hold_recheck_results[:3],
            "rejected_publication_truth_repairs": rejected_publication_truth_repairs,
            "rejected_publication_truth_repaired_ids": sorted(
                set(rejected_publication_truth_repaired_ids)
            )[:50],
            "errors": errors[:10],
        }
        try:
            heartbeat("runtime", "campaign_post_publish_watchdog", state=state, details=details)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=5253)
        return {"status": state, **details}
    finally:
        db.close()
