"""Dedicated BORIS background runtime.

Owns the canonical pollers and background_jobs threads outside the HTTP API
process so API replicas can scale without duplicating live actions.
"""
import os
import signal
import threading
import time

os.environ["BORIS_RUNTIME_ROLE"] = "worker"

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from app.db.base import Base
from app.db.session import engine, DATABASE_URL
from app.services.jobs import recover_interrupted_campaign_jobs
from app.services.reliability import heartbeat
from app.telegram_bot import _telegram_poll_loop
from app.api.messenger import _messenger_poll_loop, _reminder_loop
from app.api.mass_editor import isolated_worker_loop
from app.api.cpx_advisor import cpx_reconciliation_loop
from app.main import _background_jobs_loop
from app.services.lead_notifications import dispatch_pending as _dispatch_lead_notifications, queue_operational_notifications as _queue_operational_notifications

_stop = threading.Event()
_SINGLETON_LOCK_KEY = "boris:background-singleton-pollers:v1"
_singleton_lock_engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"application_name": "boris-worker-singleton-leader"},
)


def _signal(signum, frame):
    _stop.set()


def _try_singleton_leader_lock():
    """Acquire one cross-node leadership connection for singleton pollers."""
    conn = _singleton_lock_engine.connect()
    try:
        acquired = bool(conn.execute(text(
            "select pg_try_advisory_lock(hashtextextended(:key, 744616))"
        ), {"key": _SINGLETON_LOCK_KEY}).scalar())
        conn.commit()
        if acquired:
            return conn
    except Exception:
        try: conn.rollback()
        except Exception: pass
    conn.close()
    return None


def _release_singleton_leader_lock(conn) -> None:
    if conn is None:
        return
    try:
        conn.execute(text(
            "select pg_advisory_unlock(hashtextextended(:key, 744616))"
        ), {"key": _SINGLETON_LOCK_KEY})
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
    finally:
        try: conn.close()
        except Exception: pass


def _singleton_lock_alive(conn) -> bool:
    if conn is None:
        return False
    try:
        conn.execute(text("select 1"))
        conn.commit()
        return True
    except Exception:
        try: conn.rollback()
        except Exception: pass
        return False


def _new_ad_bid_watch_loop():
    """Singleton 5-minute first-bid watcher for newly published Feed Factory ads.

    Runs only on the elected background-runtime leader. All money writes still
    pass bootstrap_new_no_promo -> apply_one -> autonomy/money guards.
    """
    from new_ad_bid_watch_runner import main as _watch_once
    while not _stop.is_set():
        state = "ok"
        details = {}
        try:
            rc = int(_watch_once() or 0)
            details["rc"] = rc
            if rc != 0:
                state = "degraded"
        except Exception as exc:
            state = "degraded"
            details["error_type"] = type(exc).__name__
            details["error"] = str(exc)[:240]
            print("NEW_AD_BID_WATCH_LOOP_ERROR:", str(exc)[:240], flush=True)
        try:
            heartbeat("runtime", "new_ad_bid_watchdog", state=state, details=details)
        except Exception:
            pass
        _stop.wait(300)


def _mapping_recovery_rescue_once(limit: int = 2) -> dict:
    """KPI_MAPPING_RECOVERY_MINUTE_RESCUE_V1.

    Service overdue exact-mapping retries without running the full marketer.
    This lane is deliberately narrow: active marketing service + autonomous
    account + no kill-switch + durable next_retry_at <= now. It calls only the
    read-only/local mapping handler, never the full KPI tick, AI, bids or publish.
    The canonical hourly marketer/deploy lock is shared to prevent overlap.
    """
    import fcntl
    import json
    from datetime import datetime, timezone

    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.services.control_plane_adapters_ext import marketing_service_entitlement
    from app.api.avito import _kpi_exec_recover_canonical_mapping

    lock_path = "/root/BORIS/backend/run/kpi_goal_runner.lock"
    lock_file = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "deferred_lock_busy",
                "attempted": 0,
                "changed_avito": False,
                "ai_calls_made": 0,
            }

        now = datetime.now(timezone.utc)
        db = SessionLocal()
        due = []
        skipped_inactive = 0
        try:
            rows = db.query(Storage).filter(
                Storage.key == "kpi_mapping_recovery_state"
            ).all()
            for row in rows:
                try:
                    state = json.loads(row.value or "{}")
                except Exception:
                    continue
                if str(state.get("status") or "") not in {
                    "waiting_official_mapping_evidence",
                    "provider_deferred",
                }:
                    continue
                retry_raw = str(state.get("next_retry_at") or "").strip()
                if not retry_raw:
                    continue
                try:
                    retry_at = datetime.fromisoformat(
                        retry_raw.replace("Z", "+00:00")
                    )
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if retry_at > now:
                    continue

                account_id = str(row.account_id or "").strip()
                ids = [
                    str(x or "").strip()
                    for x in (state.get("candidate_item_ids") or [])
                    if str(x or "").strip()
                ][:25]
                if not account_id or not ids:
                    continue

                entitlement = marketing_service_entitlement(db, account_id) or {}
                if str(entitlement.get("state") or "") != "active":
                    skipped_inactive += 1
                    continue

                killed = db.execute(text(
                    """SELECT 1 FROM reliability_kill_switches
                       WHERE account_id=:a AND blocked=true
                         AND module IN ('actions','marketing','background')
                       LIMIT 1"""
                ), {"a": account_id}).first()
                if killed:
                    skipped_inactive += 1
                    continue

                ap_row = db.query(Storage).filter(
                    Storage.account_id == account_id,
                    Storage.key == "autopilot_settings",
                ).order_by(Storage.id.desc()).first()
                try:
                    ap_mode = (
                        json.loads(ap_row.value or "{}").get("mode")
                        if ap_row else None
                    )
                except Exception:
                    ap_mode = None
                if ap_mode not in {"goal_auto", "always_auto"}:
                    skipped_inactive += 1
                    continue

                due.append({
                    "account_id": account_id,
                    "retry_at": retry_at,
                    "candidate_item_ids": ids,
                    "previous_status": str(state.get("status") or ""),
                })
        finally:
            db.close()

        due.sort(key=lambda x: (x["retry_at"], x["account_id"]))
        selected = due[:max(1, min(int(limit or 2), 2))]
        results = []
        for item in selected:
            out = _kpi_exec_recover_canonical_mapping(
                item["account_id"],
                {
                    "action": "recover_canonical_mapping",
                    "candidate_items": [
                        {"id": iid} for iid in item["candidate_item_ids"]
                    ],
                },
            ) or {}
            results.append({
                "account_id": item["account_id"],
                "previous_status": item["previous_status"],
                "status": str(out.get("status") or "unknown"),
                "changed_identity": bool(out.get("changed_identity")),
                "changed_avito": bool(out.get("changed_avito")),
                "ai_calls_made": int(out.get("ai_calls_made") or 0),
                "next_retry_at": str(
                    ((out.get("result") or {}).get("next_retry_at"))
                    or out.get("next_retry_at")
                    or ""
                ),
            })

        return {
            "status": "ok",
            "due_total": len(due),
            "attempted": len(results),
            "skipped_inactive": skipped_inactive,
            "results": results,
            "changed_avito": any(x["changed_avito"] for x in results),
            "ai_calls_made": sum(x["ai_calls_made"] for x in results),
        }
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()


def _mapping_recovery_rescue_loop():
    """Singleton one-minute self-heal for overdue exact mapping evidence."""
    while not _stop.is_set():
        state = "ok"
        details = {}
        try:
            details = _mapping_recovery_rescue_once(limit=2)
            if str(details.get("status") or "") not in {
                "ok", "deferred_lock_busy"
            }:
                state = "degraded"
            if details.get("changed_avito") or int(details.get("ai_calls_made") or 0):
                state = "critical"
                details["safety_violation"] = (
                    "mapping recovery rescue must remain non-money/non-AI"
                )
        except Exception as exc:
            state = "degraded"
            details = {
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            }
            print("MAPPING_RECOVERY_RESCUE_ERROR:", str(exc)[:240], flush=True)
        try:
            heartbeat(
                "runtime",
                "mapping_recovery_rescue",
                state=state,
                details=details,
            )
        except Exception:
            pass
        _stop.wait(60)


def _campaign_post_publish_watch_loop():
    """DB-only 15-minute monitor for freshly completed Feed Factory campaigns.

    It consumes cached daily_stats only, so it does not add Avito/API pressure or
    paid AI calls. Publication-day traffic is baselined after the confirmed upload;
    later checks measure exact campaign-scoped active items, views and contacts.
    """
    from app.services.campaign_post_publish_watch import run_once as _watch_once
    while not _stop.is_set():
        try:
            _watch_once()
        except Exception as exc:
            try:
                heartbeat("runtime", "campaign_post_publish_watchdog",
                          state="degraded",
                          details={"error_type": type(exc).__name__,
                                   "error": str(exc)[:240]})
            except Exception:
                pass
            print("CAMPAIGN_POST_PUBLISH_WATCH_ERROR:", str(exc)[:240], flush=True)
        _stop.wait(900)


def _incident_reconcile_loop():
    """Close only transient adapter-read incidents proven healthy by readback."""
    from app.services.incident_reconciler import run_once as _reconcile_once
    while not _stop.is_set():
        state = "ok"; details = {}
        try:
            details = _reconcile_once(limit=20)
            if str(details.get("status") or "") != "ok": state = "degraded"
        except Exception as exc:
            state = "degraded"; details = {"error_type": type(exc).__name__, "error": str(exc)[:240]}
            print("INCIDENT_RECONCILE_LOOP_ERROR:", str(exc)[:240], flush=True)
        try: heartbeat("runtime", "control_incident_reconciler", state=state, details=details)
        except Exception: pass
        _stop.wait(60)


def _social_owner_policy_source_guard(root=None):
    """Heal and verify Social authority through external root-of-trust."""
    import json, os, subprocess
    _root = root or os.path.dirname(os.path.abspath(__file__))
    try:
        _p = subprocess.run(
            ["/usr/local/sbin/boris-social-owner-preflight.py", _root, "--runtime"],
            capture_output=True, text=True, timeout=15,
        )
        _data = json.loads((_p.stdout or "{}").strip().splitlines()[-1])
        _s = _data.get("source") or {}
        return {
            "ok": bool(_data.get("ok") and _s.get("ok")),
            "self_healed": bool(_s.get("self_healed")),
            "canonical_self_healed": bool(_s.get("canonical_self_healed")),
            "target_self_healed": bool(_s.get("target_self_healed")),
            "sha256": _s.get("sha256"),
            "reason": _s.get("reason"),
        }
    except Exception as _exc:
        return {"ok":False,"self_healed":False,"reason":"%s:%s"%(type(_exc).__name__,str(_exc)[:160])}


def _social_owner_policy_consumer_guard(root=None):
    """Self-heal mutable Social consumers through external root-of-trust."""
    import json, os, subprocess
    _root = root or os.path.dirname(os.path.abspath(__file__))
    try:
        _p = subprocess.run(
            ["/usr/local/sbin/boris-social-owner-preflight.py", _root, "--runtime"],
            capture_output=True, text=True, timeout=15,
        )
        _data = json.loads((_p.stdout or "{}").strip().splitlines()[-1])
        _c = _data.get("consumers") or {}
        return {
            "ok": bool(_data.get("ok") and _c.get("ok")),
            "self_healed": bool(_c.get("self_healed")),
            "healed": _c.get("healed") or [],
            "drift": _c.get("drift") or [],
        }
    except Exception as _exc:
        return {"ok":False,"self_healed":False,"healed":[],"drift":[{"path":"external_preflight","reason":"%s:%s"%(type(_exc).__name__,str(_exc)[:160])}]}


def _social_posting_loop():

    """Singleton social scheduler: materialize due slots and finish delivery.

    Generation is attempted at most once per 5-minute bucket. The posting
    runner itself persists a slot generation_key before paid AI, so retries
    are idempotent. Due/partial deliveries are checked every minute and only
    missing platforms are retried.
    """
    source_guard = _social_owner_policy_source_guard()
    consumer_guard = _social_owner_policy_consumer_guard()
    while not (source_guard.get("ok") and consumer_guard.get("ok")) and not _stop.is_set():
        try:
            heartbeat(
                "runtime",
                "social_posting_scheduler",
                state="degraded",
                details={
                    "owner_policy_source": source_guard,
                    "owner_policy_consumers": consumer_guard,
                    "generation_blocked": "OWNER_SOCIAL_POLICY_SOURCE_UNAVAILABLE",
                    "due_blocked": "OWNER_SOCIAL_POLICY_SOURCE_UNAVAILABLE",
                    "owner_action_required": False,
                },
            )
        except Exception:
            pass
        _stop.wait(60)
        source_guard = _social_owner_policy_source_guard()
        consumer_guard = _social_owner_policy_consumer_guard()
    if _stop.is_set():
        return

    import posting_runner as _social_runner
    from app.services.social_owner_policy_v3 import (
        ensure_owner_project_policy,
        install_posting_runner_guard,
        public_copy_runtime_boundary_health,
        stable_social_vk_external_unavailable_health,
        stable_social_vk_pending_cleanup_once,
    )

    autopost_all = _social_runner.autopost_all
    pregenerate_upcoming_all = _social_runner.pregenerate_upcoming_all
    autopost_projects = _social_runner.autopost_projects
    due_posts_all = _social_runner.due_posts_all
    social_scheduler_contract_health = _social_runner.social_scheduler_contract_health
    social_owner_pre_slot_self_heal = _social_runner.social_owner_pre_slot_self_heal
    social_owner_pre_slot_readiness_health = _social_runner.social_owner_pre_slot_readiness_health
    social_active_paid_pre_slot_self_heal = _social_runner.social_active_paid_pre_slot_self_heal
    social_active_paid_pre_slot_readiness_health = _social_runner.social_active_paid_pre_slot_readiness_health
    social_transport_readiness_cached_health = _social_runner.social_transport_readiness_cached_health
    ocean_daily_delivery_health = _social_runner.ocean_daily_delivery_health
    dushi_daily_delivery_health = _social_runner.dushi_daily_delivery_health
    persist_social_delivery_sla_breaches = _social_runner.persist_social_delivery_sla_breaches
    social_active_paid_daily_delivery_health = _social_runner.social_active_paid_daily_delivery_health
    ocean_vk_link_cleanup_once = _social_runner.ocean_vk_link_cleanup_once
    ensure_ocean_media_reuse = _social_runner.ensure_ocean_media_reuse
    install_posting_runner_guard(_social_runner)
    last_generation_bucket = None
    next_ocean_cleanup_at = 0.0
    last_ocean_cleanup = None
    while not _stop.is_set():
        state = "ok"
        details = {}
        try:
            source_guard = _social_owner_policy_source_guard()
            consumer_guard = _social_owner_policy_consumer_guard()
            project_policy = ensure_owner_project_policy(_social_runner, account_id="u2")
            pre_slot_self_heal = social_owner_pre_slot_self_heal("u2")
            pre_slot_readiness = social_owner_pre_slot_readiness_health("u2")
            active_paid_pre_slot_self_heal = social_active_paid_pre_slot_self_heal()
            active_paid_pre_slot_readiness = social_active_paid_pre_slot_readiness_health()
            transport_readiness = social_transport_readiness_cached_health()
            media_floor = ensure_ocean_media_reuse("u2")
            ocean_daily_delivery = ocean_daily_delivery_health("u2")
            dushi_daily_delivery = dushi_daily_delivery_health("u2")
            active_paid_daily_delivery = social_active_paid_daily_delivery_health()
            details["owner_policy_source"] = source_guard
            details["owner_policy_consumers"] = consumer_guard
            details["owner_project_policy"] = project_policy
            details["pre_slot_self_heal"] = pre_slot_self_heal
            details["pre_slot_readiness"] = pre_slot_readiness
            details["active_paid_pre_slot_self_heal"] = active_paid_pre_slot_self_heal
            details["active_paid_pre_slot_readiness"] = active_paid_pre_slot_readiness
            details["transport_readiness"] = transport_readiness
            details["ocean_media_safety_floor"] = media_floor
            details["ocean_daily_delivery"] = ocean_daily_delivery
            details["dushi_daily_delivery"] = dushi_daily_delivery
            details["active_paid_daily_delivery"] = active_paid_daily_delivery
            paid_sla_journal={}
            for _paid_health in (active_paid_daily_delivery.get("projects") or []):
                if not isinstance(_paid_health,dict):
                    continue
                _paid_key=(
                    str(_paid_health.get("account_id") or "")
                    + ":"
                    + str(_paid_health.get("project_id") or "")
                )
                paid_sla_journal[_paid_key]=persist_social_delivery_sla_breaches(
                    _paid_health
                )
            details["social_delivery_sla_journal"] = {
                "ocean": persist_social_delivery_sla_breaches(ocean_daily_delivery),
                "dushi": persist_social_delivery_sla_breaches(dushi_daily_delivery),
                "active_paid": paid_sla_journal,
            }
            guard_install = install_posting_runner_guard(_social_runner)
            details["public_copy_guard_install"] = guard_install
            boundary = public_copy_runtime_boundary_health()
            details["public_copy_boundary"] = boundary
            boundary_ok = bool(
                source_guard.get("ok")
                and consumer_guard.get("ok")
                and project_policy.get("ok")
                and media_floor.get("ok")
                and media_floor.get("image_source") == "reuse"
                and boundary.get("ok")
            )
            if not boundary_ok:
                state = "degraded"
                details["generation_blocked"] = "PUBLIC_COPY_BOUNDARY_REGRESSION"
                details["due_blocked"] = "PUBLIC_COPY_BOUNDARY_REGRESSION"
            else:
                generation_bucket = int(time.time() // 300)
                if generation_bucket != last_generation_bucket:
                    # Prepare moderate content two hours before its slot so provider
                    # latency/cooldown is absorbed before the moderation deadline.
                    pregen = pregenerate_upcoming_all(lead_hours=2)
                    details["upcoming_pregeneration"] = pregen
                    if not pregen.get("ok"):
                        state = "degraded"
                    owner_projects = [
                        dict(x) for x in (pre_slot_readiness.get("projects") or [])
                        if isinstance(x, dict)
                    ]
                    transport_projects = {
                        (
                            str(x.get("account_id") or "u2"),
                            str(x.get("project_id") or ""),
                        ): dict(x)
                        for x in (transport_readiness.get("projects") or [])
                        if isinstance(x, dict)
                    }
                    for item in owner_projects:
                        pid = str(item.get("project_id") or "")
                        effective = list(item.get("blocked_reasons") or [])
                        titem = transport_projects.get(("u2",pid))
                        if not transport_readiness.get("fresh"):
                            effective.append("transport_readiness_stale_or_missing")
                        elif not titem or not bool(titem.get("ready")):
                            effective.append("transport_permission_not_ready")
                        item["effective_blocked_reasons"] = list(dict.fromkeys(effective))
                        item["transport_ready"] = bool(
                            transport_readiness.get("fresh")
                            and titem and titem.get("ready")
                        )
                    owner_ready = [
                        x for x in owner_projects
                        if not x.get("effective_blocked_reasons")
                        and x.get("expected_auto_generation")
                    ]
                    owner_blocked = [
                        x for x in owner_projects if x.get("effective_blocked_reasons")
                    ]

                    paid_projects=[
                        dict(x) for x in (active_paid_pre_slot_readiness.get("projects") or [])
                        if isinstance(x,dict) and x.get("monitored")
                    ]
                    paid_blocked=[
                        x for x in paid_projects if x.get("blocked_reasons")
                    ]
                    paid_blocked_accounts={
                        str(x.get("account_id") or "")
                        for x in paid_blocked if str(x.get("account_id") or "")
                    }
                    paid_ready_in_blocked_accounts=[
                        x for x in paid_projects
                        if str(x.get("account_id") or "") in paid_blocked_accounts
                        and not x.get("blocked_reasons")
                    ]

                    if not owner_blocked and not paid_blocked:
                        autopost_all(respect_time=True)
                        details["generation_checked"] = True
                    else:
                        # Isolation is account+project scoped. A broken paid
                        # client/project cannot pause healthy clients or the
                        # healthy sibling project in the same account.
                        skip_accounts=set(paid_blocked_accounts)
                        if owner_blocked:
                            skip_accounts.add("u2")
                        autopost_all(
                            respect_time=True,
                            skip_accounts=skip_accounts or None,
                        )
                        targeted_owner=[]
                        if owner_blocked:
                            for item in owner_ready:
                                pid=str(item.get("project_id") or "")
                                if not pid:
                                    continue
                                autopost_projects(
                                    "u2",owner=True,respect_time=True,only_id=pid
                                )
                                targeted_owner.append(pid)

                        targeted_paid=[]
                        for item in paid_ready_in_blocked_accounts:
                            acc=str(item.get("account_id") or "")
                            pid=str(item.get("project_id") or "")
                            if not acc or not pid:
                                continue
                            autopost_projects(
                                acc,owner=False,respect_time=True,only_id=pid
                            )
                            targeted_paid.append({"account_id":acc,"project_id":pid})

                        details["generation_checked"] = True
                        details["generation_isolation"] = {
                            "owner_blocked_projects": [
                                {
                                    "account_id":"u2",
                                    "project_id":str(x.get("project_id") or ""),
                                    "reasons":list(x.get("effective_blocked_reasons") or []),
                                }
                                for x in owner_blocked
                            ],
                            "paid_blocked_projects": [
                                {
                                    "account_id":str(x.get("account_id") or ""),
                                    "project_id":str(x.get("project_id") or ""),
                                    "reasons":list(x.get("blocked_reasons") or []),
                                }
                                for x in paid_blocked
                            ],
                            "targeted_ready_owner_projects":targeted_owner,
                            "targeted_ready_paid_projects":targeted_paid,
                            "other_accounts_continued":True,
                        }
                        details["generation_blocked"] = "PRE_SLOT_READINESS_PARTIAL"
                    last_generation_bucket = generation_bucket
                due_posts_all(dry=False)
                details["due_checked"] = True
                # A healthy scheduler must not become "degraded" merely because
                # one paid client is waiting for external onboarding data. Keep
                # runtime health about the scheduler itself; the account-scoped
                # dependency is surfaced by active_paid_pre_slot_readiness and
                # the durable SOCIAL_SUBSCRIPTION_PROJECT_MISSING incident.
                owner_runtime_blockers = [
                    x for x in (active_paid_pre_slot_readiness.get("blocked") or [])
                    if str((x or {}).get("dependency_state") or "") not in {"waiting_owner", "waiting_external"}
                ]
                if not pre_slot_readiness.get("ok") or owner_runtime_blockers:
                    state = "degraded"
                if not active_paid_pre_slot_readiness.get("ok") and not owner_runtime_blockers:
                    details["client_dependencies_waiting"] = True
                    details["client_dependencies_count"] = int(active_paid_pre_slot_readiness.get("blocked_count") or 0)

            # Daily plan/fact is an outcome health signal, not a kill-switch:
            # even when a slot is overdue the canonical worker must keep trying
            # to generate/deliver it. We mark the runtime degraded only after
            # the slot grace period has expired.
            daily_delivery_checks = {
                "ocean": ocean_daily_delivery,
                "dushi": dushi_daily_delivery,
            }
            for _paid_health in (active_paid_daily_delivery.get("projects") or []):
                if not isinstance(_paid_health,dict):
                    continue
                _paid_key=(
                    "paid:"
                    + str(_paid_health.get("account_id") or "")
                    + ":"
                    + str(_paid_health.get("project_id") or "")
                )
                daily_delivery_checks[_paid_key]=_paid_health
            daily_states = {
                key: str((value or {}).get("state") or "ok")
                for key, value in daily_delivery_checks.items()
            }
            if "critical" in daily_states.values():
                state = "critical"
                details["daily_delivery_owner_action_required"] = any(
                    bool((value or {}).get("owner_action_required"))
                    for value in daily_delivery_checks.values()
                )
            elif any(value != "ok" for value in daily_states.values()):
                state = "degraded"
                details["daily_delivery_self_heal_active"] = True
            details["daily_delivery_states"] = daily_states
            if any(
                int((value or {}).get("sla_breach_count") or 0) > 0
                for value in daily_delivery_checks.values()
            ):
                details["daily_delivery_sla_breach_recorded"] = True
            if not transport_readiness.get("ok"):
                state = "degraded"
                details["transport_self_heal_active"] = not bool(
                    transport_readiness.get("owner_action_required")
                )

            now_ts = time.time()
            if now_ts >= next_ocean_cleanup_at:
                # First reconcile any explicitly pending VK edits across all
                # Social projects. Only when that queue is empty do we run the
                # older Ocean-specific legacy link cleanup.
                cleanup = stable_social_vk_pending_cleanup_once(_social_runner)
                if str((cleanup or {}).get("status") or "") == "idle":
                    cleanup = ocean_vk_link_cleanup_once()
                last_ocean_cleanup = cleanup
                cleanup_status = str((cleanup or {}).get("status") or "")
                # Drain a known owner-approved cleanup backlog without turning the
                # owner into an operator. Successful edits advance slowly (5 min)
                # to the next item; platform CAPTCHA/rate protection keeps a long
                # 6-hour backoff; idle/clean/error states stay on the hourly pace.
                if cleanup_status == "captcha":
                    retry_seconds=(
                        (cleanup or {}).get("next_retry_in_seconds")
                        or (cleanup or {}).get("retry_after_seconds")
                        or 21600
                    )
                    next_ocean_cleanup_at = now_ts + max(300, int(retry_seconds))
                elif cleanup_status == "backoff":
                    retry_seconds=(
                        (cleanup or {}).get("next_retry_in_seconds")
                        or (cleanup or {}).get("retry_after_seconds")
                        or 3600
                    )
                    next_ocean_cleanup_at = now_ts + max(60, int(retry_seconds))
                elif cleanup_status in {"fixed_pending", "reposted_uneditable", "skipped_uneditable"}:
                    # Keep draining the rest of the queue. Successful verified
                    # reposts are paced like ordinary edits; permanent blocked
                    # records are surfaced separately by unavailable-health.
                    next_ocean_cleanup_at = now_ts + 300
                else:
                    next_ocean_cleanup_at = now_ts + 3600
            cleanup_view = dict(last_ocean_cleanup or {})
            if cleanup_view:
                cleanup_view["next_retry_in_seconds"] = max(0, int(next_ocean_cleanup_at - now_ts))
                cleanup_view.setdefault("owner_action_required", False)
                # Canonical generic key; keep the old alias for dashboard
                # compatibility while consumers migrate.
                details["social_vk_cleanup"] = cleanup_view
                details["ocean_vk_cleanup"] = cleanup_view
                cleanup_status_now = str(cleanup_view.get("status") or "")
                if cleanup_status_now in {"error", "verify_failed"}:
                    state = "degraded"
                if bool(cleanup_view.get("owner_action_required")):
                    state = "degraded"

            unavailable = stable_social_vk_external_unavailable_health(_social_runner)
            details["social_vk_unavailable"] = unavailable
            details["owner_action_required"] = bool(
                ocean_daily_delivery.get("owner_action_required")
                or dushi_daily_delivery.get("owner_action_required")
                or unavailable.get("owner_action_required")
                or pre_slot_readiness.get("owner_action_required")
                or active_paid_pre_slot_readiness.get("owner_action_required")
                or active_paid_daily_delivery.get("owner_action_required")
                or transport_readiness.get("owner_action_required")
            )
            if not unavailable.get("ok") and state != "critical":
                state = "degraded"

            contract = social_scheduler_contract_health()
            details["contract_ok"] = bool(contract.get("ok"))
            details["blocked_count"] = int(contract.get("blocked_count") or 0)
            if contract.get("blocked_projects"):
                details["blocked_projects"] = contract.get("blocked_projects")
            if not contract.get("ok"):
                state = "degraded"
        except Exception as exc:
            state = "degraded"
            details["error"] = str(exc)[:300]
            print("SOCIAL_POSTING_LOOP_ERROR:", str(exc)[:300], flush=True)
        try:
            heartbeat("runtime", "social_posting_scheduler", state=state, details=details)
        except Exception:
            pass
        _stop.wait(60)


def _lead_notification_loop():
    """Single-owner near-real-time delivery + 30s operational reminder scan."""
    next_scan=0.0
    while not _stop.is_set():
        state="ok"; details={}
        try:
            now=time.time()
            if now>=next_scan:
                details["scan"]=_queue_operational_notifications() or {}
                next_scan=now+30
            details["delivery"]=_dispatch_lead_notifications(limit=30) or {}
            if int((details.get("delivery") or {}).get("failed") or 0)>0:
                state="degraded"
        except Exception as exc:
            state="degraded"; details={"error_type":type(exc).__name__,"error":str(exc)[:240]}
            print("LEAD_NOTIFICATION_LOOP_ERROR:",str(exc)[:240],flush=True)
        try:
            heartbeat("runtime","lead_notifications",state=state,details=details)
        except Exception:
            pass
        _stop.wait(2)


def main():
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    Base.metadata.create_all(bind=engine)
    recovered = recover_interrupted_campaign_jobs()
    print(f"WORKER_STARTUP: recovered={recovered}", flush=True)

    threads = []
    singleton_threads: list[threading.Thread] = []
    singleton_lock = _try_singleton_leader_lock()

    def start_singleton_pollers() -> None:
        nonlocal singleton_threads
        if singleton_threads:
            return
        for name, target in (
            ("telegram_poll", _telegram_poll_loop),
            ("messenger_poll", _messenger_poll_loop),
            ("reminder_poll", _reminder_loop),
            ("cpx_reconcile", lambda: cpx_reconciliation_loop(_stop, 30)),
            ("new_ad_bid_watch", _new_ad_bid_watch_loop),
            ("mapping_recovery_rescue", _mapping_recovery_rescue_loop),
            # POST_PUBLISH_WATCH_SINGLE_OWNER_V2: the durable background_jobs
            # singleton is the only executor. The old direct 15-minute loop used
            # to run the same DB reconciliation a second time and could race the
            # canonical job. Keep the function as emergency fallback, do not start it.
            ("incident_reconcile", _incident_reconcile_loop),
            ("social_posting", _social_posting_loop),
            ("lead_notifications", _lead_notification_loop),
        ):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start(); threads.append(t); singleton_threads.append(t)
        print("WORKER_LEADER: singleton pollers active", flush=True)

    if singleton_lock is not None:
        start_singleton_pollers()
    else:
        print("WORKER_STANDBY: singleton pollers owned by another worker node", flush=True)

    # Isolated browser dispatch is safe on every node because each account is
    # protected by its own distributed advisory lock in mass_editor.py.
    isolated_thread = threading.Thread(target=isolated_worker_loop, daemon=True, name="avito_isolated_worker")
    isolated_thread.start(); threads.append(isolated_thread)

    try:
        worker_count = max(1, min(int(os.environ.get("BORIS_JOB_WORKERS", "8")), 16))
    except Exception:
        worker_count = 8
    for slot in range(worker_count):
        t = threading.Thread(target=_background_jobs_loop, args=(slot,), daemon=True,
                             name=f"background_jobs_{slot}")
        t.start(); threads.append(t)

    print(f"WORKER_STARTUP: singleton_leader={bool(singleton_lock)} background_jobs={worker_count} isolated_executor=distributed", flush=True)
    while not _stop.wait(5):
        # Hot-standby leadership. A second worker node runs DB jobs immediately
        # but does not duplicate Telegram/Messenger/reminder pollers. If the
        # leader process dies, PostgreSQL releases its session lock and standby
        # takes over on the next heartbeat.
        if singleton_lock is None:
            singleton_lock = _try_singleton_leader_lock()
            if singleton_lock is not None:
                start_singleton_pollers()
        elif not _singleton_lock_alive(singleton_lock):
            # Losing the DB session also loses the advisory lock. Exit instead
            # of allowing singleton pollers to run without proven leadership;
            # systemd will restart this node and it will re-elect safely.
            print("WORKER_LEADER_LOST: database leadership connection lost", flush=True)
            raise SystemExit(3)

        dead = [t.name for t in threads if not t.is_alive()]
        heartbeat("runtime", "background_runtime",
                  state="degraded" if dead else "ok",
                  details={"threads": len(threads), "dead": dead, "job_workers": worker_count,
                           "singleton_leader": bool(singleton_lock), "singleton_pollers": len(singleton_threads)})
        if dead:
            print(f"WORKER_RUNTIME: dead_threads={dead}", flush=True)
            raise SystemExit(2)
    # The canonical job payloads/leases live in PostgreSQL and are recovered on
    # the next startup. Some imported pollers own helper threads that may outlive
    # this main loop even after SIGTERM; letting them hold the interpreter makes
    # systemd hit TimeoutStopSec and creates a brief old/new worker overlap.
    # Once dispatch is stopped here, terminate the worker process deterministically.
    print("WORKER_STOP: graceful checkpoint reached; exiting runtime", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
