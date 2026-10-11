#!/usr/bin/env python3
"""Read-only production certificate for BORIS Crowd SEO.

The certifier deliberately separates:
1) code/rules/capacity/guardian readiness;
2) real publication readiness.

It never treats CAPTCHA, legal acceptance, account maturity or another external
human checkpoint as a successful publication. It also never performs writes,
registrations, publications, paid AI calls or provider mutations.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import crowd_seo
from app.services import platform_rules
from app.services import service_marketplace as marketplace
from app.services import crowd_seo_reserve_policy as reserve_policy
GUARD_STATUS = ROOT / "data/service_marketplaces/guardian_status.json"
HARD_EXCLUDED = {
    "n8n_jobs": "automated_access_prohibited",
    "weweb_jobs": "automated_access_prohibited",
    "airtable_jobs": "commercial_use_prohibited",
    "bubble_jobs": "automated_access_not_verified",
}
MAX_GUARD_AGE_SEC = max(30 * 60, reserve_policy.GUARD_INTERVAL_MINUTES * 60 * 3)
DAILY_SUBMISSION_TARGET = reserve_policy.DAILY_UNIQUE_SUBMISSION_TARGET


def _iso(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _systemctl(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["systemctl", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return p.returncode, (p.stdout or p.stderr or "").strip()


def _guardian_state() -> dict:
    if not GUARD_STATUS.exists():
        return {"ok": False, "reason": "guardian_status_missing"}
    try:
        row = json.loads(GUARD_STATUS.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"guardian_status_invalid:{type(exc).__name__}"}
    ts = _iso(row.get("at"))
    age = None
    if ts is not None:
        age = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    crowd = row.get("crowd_seo") or {}
    capacity = ((row.get("crowd_capacity") or {}).get("after") or [])
    daily = row.get("daily_execution") or {}
    daily_policy = daily.get("policy") or {}
    cadence_ok = bool(
        daily.get("cadence_ok")
        and int(daily_policy.get("guard_interval_minutes") or 0) == reserve_policy.GUARD_INTERVAL_MINUTES
        and int(daily_policy.get("scheduled_runs_per_day") or 0) == reserve_policy.GUARD_SCHEDULED_RUNS_PER_DAY
        and bool(daily_policy.get("rules_checked_every_guard_run"))
        and bool(daily_policy.get("new_domain_first"))
        and not bool(daily_policy.get("paid_placement_allowed"))
        and not bool(daily_policy.get("captcha_bypass_allowed"))
    )
    ok = bool(
        ts is not None
        and age is not None
        and age <= MAX_GUARD_AGE_SEC
        and cadence_ok
        and not (crowd.get("errors") or [])
        and int((row.get("crowd_capacity") or {}).get("max_discovery_deficit_after_warmup_after") or 0) == 0
    )
    return {
        "ok": ok,
        "at": row.get("at"),
        "age_sec": round(age, 1) if age is not None else None,
        "crowd_errors": crowd.get("errors") or [],
        "capacity_after": capacity,
        "daily_execution": daily,
        "cadence_ok": cadence_ok,
        "reason": None if ok else "guardian_not_fresh_or_cadence_failed_or_has_errors_or_capacity_deficit",
    }


def _timer_state() -> dict:
    rc_enabled, enabled = _systemctl("is-enabled", "boris-forum-acquisition-guard.timer")
    rc_active, active = _systemctl("is-active", "boris-forum-acquisition-guard.timer")
    rc_cat, unit_text = _systemctl("cat", "boris-forum-acquisition-guard.timer")
    expected_calendar = "OnCalendar=*-*-* *:05,15,25,35,45,55:00"
    cadence_configured = rc_cat == 0 and expected_calendar in unit_text
    return {
        "ok": (
            rc_enabled == 0 and enabled == "enabled"
            and rc_active == 0 and active == "active"
            and cadence_configured
        ),
        "enabled": enabled,
        "active": active,
        "cadence_configured": cadence_configured,
        "expected_interval_minutes": reserve_policy.GUARD_INTERVAL_MINUTES,
        "scheduled_runs_per_day": reserve_policy.GUARD_SCHEDULED_RUNS_PER_DAY,
    }


def _project_row(project: dict, capacity: dict) -> dict:
    required = int(capacity.get("required") or 0)
    # CERTIFIER_LIVE_PLACEMENT_SUMMARY_V1: never trust a persisted summary
    # cached on the project row. Verification can downgrade a publication after
    # that cache was written. Recompute from live placements + immutable guest
    # evidence every certificate run so published/verified counts cannot drift.
    placement_summary = crowd_seo._placement_summary(project)
    published_verified = int(placement_summary.get("verified") or 0)
    published_total = int(placement_summary.get("published") or 0)
    remaining_verified = max(0, required - published_verified)
    matches = crowd_seo._usable_matches_for_project(
        project,
        crowd_seo._forum_matches(
            project.get("niche") or "business",
            limit=max(100, max(1, required) * 5),
            keywords=crowd_seo._project_keywords(project),
            offer_type=crowd_seo._legacy_offer_type(project),
        ),
    )

    # CERTIFIER_REACHABLE_TARGET_PLAN_V3
    # Preserve the marketplace relevance/IKS order *inside* each activation
    # tier, but never select a transiently unreachable site while a READY,
    # bootstrap-reachable or warmup-reachable alternative is sitting in reserve.
    # This keeps the concrete selected plan consistent with capacity_snapshot.
    ranked_matches = [
        row for _, row in sorted(
            enumerate(matches),
            key=lambda pair: (
                crowd_seo._match_activation_tier(pair[1]),
                pair[0],
            ),
        )
    ]
    selected = ranked_matches[:required] if required > 0 else []
    reserve = ranked_matches[required:] if required > 0 else list(ranked_matches)

    regs = {x.get("platform"): x for x in marketplace.registration_plan()}
    rule_failures = []
    terminal_matches = []
    hard_excluded_seen = []
    for match in selected:
        key = str(match.get("platform") or "")
        if key in HARD_EXCLUDED:
            hard_excluded_seen.append({"platform": key, "reason": HARD_EXCLUDED[key]})
        policy = marketplace.platform_policy(key)
        gate = platform_rules.publish_gate(key)
        if not policy.get("free") or not gate.get("allowed"):
            rule_failures.append({
                "platform": key,
                "policy": policy,
                "publish_gate": gate,
            })
        reg = regs.get(key) or {}
        if marketplace.registration_is_terminally_blocked(reg):
            terminal_matches.append({
                "platform": key,
                "status": reg.get("status"),
                "checkpoint": reg.get("checkpoint"),
            })

    eligible = int(capacity.get("eligible") or 0)
    ready_total = int(capacity.get("ready") or 0)
    selected_ready = sum(1 for x in selected if x.get("publication_ready"))
    reachable_after_warmup = int(capacity.get("service_reachable_after_warmup") or 0)
    discovery_deficit_after_warmup = int(capacity.get("discovery_deficit_after_warmup") or 0)
    placement_operational_deficit = int(capacity.get("placement_operational_deficit") or 0)

    is_inventory = str(project.get("plan_mode") or "") == "inventory"
    capacity_ok = bool(
        (
            is_inventory
            and discovery_deficit_after_warmup == 0
        )
        or (
            required > 0
            and len(selected) >= required
            and eligible >= required
            and reachable_after_warmup >= required
            and discovery_deficit_after_warmup == 0
        )
    )
    rules_ok = not rule_failures and not hard_excluded_seen and not terminal_matches
    capacity_ok = bool(capacity_ok and placement_operational_deficit == 0)
    infrastructure_ok = capacity_ok and rules_ok
    # CERTIFIER_DELIVERY_TRUTH_V3
    # Infrastructure capacity and actual publication readiness are different facts.
    # An inventory project with real operational demand (required > 0) is NOT
    # delivery-ready while every selected account still waits on onboarding.
    # Only a truly empty inventory pool (required == 0) may be healthy without
    # READY accounts because there is then no current publication obligation.
    delivery_ready = bool(
        infrastructure_ok
        and (
            (is_inventory and required == 0)
            or (required > 0 and selected_ready >= required)
        )
    )

    checkpoints = {}
    for match in selected:
        cp = str(match.get("checkpoint") or "")
        if cp:
            checkpoints[cp] = checkpoints.get(cp, 0) + 1

    return {
        "project": project.get("id"),
        "site": project.get("site"),
        "niche": project.get("niche"),
        "niche_aliases": sorted(crowd_seo._project_niche_aliases(
            project.get("niche") or "business",
            project.get("keywords") or [],
        )),
        "required": required,
        "published_total": published_total,
        "published_verified": published_verified,
        "remaining_verified": remaining_verified,
        "inventory_mode": bool(capacity.get("inventory_mode")),
        "inventory_rule_verified_total": int(capacity.get("inventory_rule_verified_total") or 0),
        "inventory_deferred_unreachable": int(capacity.get("inventory_deferred_unreachable") or 0),
        "activation_deficit_after_bootstrap": int(capacity.get("activation_deficit_after_bootstrap") or 0),
        "activation_deficit_after_warmup": int(capacity.get("activation_deficit_after_warmup") or 0),
        "eligible_total": eligible,
        "selected_slots": len(selected),
        "reserve_slots": len(reserve),
        "ready_selected": selected_ready,
        "ready_total": ready_total,
        "service_reachable_after_warmup": reachable_after_warmup,
        "discovery_deficit_after_warmup": discovery_deficit_after_warmup,
        "placement_covered_live_slots": int(capacity.get("placement_covered_live_slots") or 0),
        "placement_operational_deficit": placement_operational_deficit,
        "matched_slots_total": len(matches),
        "capacity_ok": capacity_ok,
        "rules_ok": rules_ok,
        "infrastructure_ok": infrastructure_ok,
        "delivery_ready": delivery_ready,
        "selected_external_checkpoints": checkpoints,
        "rule_failures": rule_failures,
        "terminal_matches": terminal_matches,
        "hard_excluded_seen": hard_excluded_seen,
        "selected_platforms": [
            f"{x.get('platform')}::{x.get('surface_id') or 'default'}"
            for x in selected
        ],
        "reserve_platforms": [
            f"{x.get('platform')}::{x.get('surface_id') or 'default'}"
            for x in reserve
        ],
    }


def _external_submissions_snapshot() -> dict:
    """Summarize latest external guest/catalog submission state per platform.

    The attempt ledger is append-only.  Older pending rows must not keep a
    platform pending after a later verification/failure record exists.
    """
    latest = {}
    for row in marketplace.list_attempts(limit=1000):
        platform = str(row.get("platform") or "").strip()
        if not platform or platform in latest:
            continue
        action = str(row.get("action") or "")
        status = str(row.get("status") or "")
        if (
            action in {"guest_catalog_submit", "guest_catalog_email_submit", "guest_tool_submit", "verify_publication"}
            or platform.startswith("guest_")
        ):
            latest[platform] = row
    pending = []
    verified = []
    historical_redirected = []
    terminal = []
    for platform, row in latest.items():
        status = str(row.get("status") or "")
        meta = dict(row.get("meta") or {})
        item = {
            "platform": platform,
            "status": status,
            "url": row.get("url"),
            "created_at": row.get("created_at"),
            "classification": meta.get("classification"),
            "project_id": meta.get("project_id"),
            "project": meta.get("project"),
            "target_url": meta.get("target_url"),
            "domain": meta.get("domain"),
            "project_site": meta.get("project_site"),
            "linked_target": meta.get("linked_target"),
            "redirect_target": meta.get("redirect_target"),
            "counts_toward_direct_contract": meta.get("counts_toward_direct_contract"),
            "counts_toward_new_direct_60": meta.get("counts_toward_new_direct_60"),
        }
        if status.startswith("submitted_pending") or status == "submitted":
            pending.append(item)
        elif status == "verified":
            if (
                str(meta.get("classification") or "") == "historical_redirected"
                or meta.get("counts_toward_direct_contract") is False
            ):
                historical_redirected.append(item)
            else:
                verified.append(item)
        elif status in {"blocked", "failed", "rejected", "terminal"}:
            terminal.append(item)
    pending.sort(key=lambda x: str(x.get("platform") or ""))
    verified.sort(key=lambda x: str(x.get("platform") or ""))
    historical_redirected.sort(key=lambda x: str(x.get("platform") or ""))
    terminal.sort(key=lambda x: str(x.get("platform") or ""))
    return {
        "pending_total": len(pending),
        "verified_total": len(verified),
        "historical_redirected_total": len(historical_redirected),
        "terminal_total": len(terminal),
        "pending": pending,
        "verified": verified,
        "historical_redirected": historical_redirected,
        "terminal": terminal,
    }


def _event_domain(row: dict) -> str:
    host = (urlparse(str(row.get("url") or "")).hostname or "").lower()
    return host.removeprefix("www.")


def _recent_submission_domains(
    now: datetime | None = None,
    *,
    cooldown_days: int = reserve_policy.SUBMISSION_DOMAIN_COOLDOWN_DAYS,
) -> set[str]:
    """Domains with an accepted FACT inside the reserve cooldown window."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(days=max(1, int(cooldown_days or 1)))
    domains: set[str] = set()
    for row in marketplace.list_attempts(limit=1000):
        created = _iso(row.get("created_at"))
        if not created or created.astimezone(timezone.utc) < cutoff:
            continue
        action = str(row.get("action") or "")
        status = str(row.get("status") or "")
        meta = row.get("meta") or {}
        accepted = bool(
            (
                status in {"submitted_pending_moderation", "submitted_pending_email"}
                and action in {"guest_catalog_submit", "guest_catalog_email_submit", "guest_tool_submit"}
            )
            or (
                status == "success"
                and action == "publish"
                and meta.get("publication_verified")
            )
        )
        if not accepted:
            continue
        domain = _event_domain({"url": meta.get("domain") or row.get("url")})
        if not domain and meta.get("domain"):
            domain = str(meta.get("domain") or "").lower().removeprefix("www.")
        if domain:
            domains.add(domain)
    for row in marketplace.list_publications():
        posted = _iso(row.get("posted_at"))
        if not posted or posted.astimezone(timezone.utc) < cutoff:
            continue
        domain = _event_domain(row)
        if domain:
            domains.add(domain)
    return domains


def _business_day_context(now: datetime | None = None) -> dict:
    """Return one canonical Crowd SEO business-day window.

    Ledger timestamps remain UTC. Only day membership is evaluated in the
    configured IANA timezone so the daily SLO rolls over at local midnight.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(reserve_policy.BUSINESS_TIMEZONE)
    local = current.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return {
        "timezone": reserve_policy.BUSINESS_TIMEZONE,
        "local_date": local.date(),
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": end_local.astimezone(timezone.utc),
    }


def _same_business_day(value: str | None, context: dict) -> bool:
    parsed = _iso(value)
    if parsed is None:
        return False
    return bool(context["start_utc"] <= parsed.astimezone(timezone.utc) < context["end_utc"])


def _daily_delivery_snapshot(now: datetime | None = None) -> dict:
    """Count current business-day submission FACTs; verification stays separate."""
    context = _business_day_context(now)
    day = context["local_date"]
    submitted = []
    verified = []

    for row in marketplace.list_attempts(limit=1000):
        if not _same_business_day(row.get("created_at"), context):
            continue
        action = str(row.get("action") or "")
        status = str(row.get("status") or "")
        meta = row.get("meta") or {}
        domain = _event_domain(row)
        pending_fact = bool(
            status in {"submitted_pending_moderation", "submitted_pending_email"}
            and action in {"guest_catalog_submit", "guest_catalog_email_submit", "guest_tool_submit"}
        )
        verified_publish_fact = bool(
            status == "success"
            and action == "publish"
            and meta.get("publication_verified")
        )
        if domain and (pending_fact or verified_publish_fact):
            submitted.append({
                "id": row.get("id"),
                "platform": row.get("platform"),
                "domain": domain,
                "status": status,
                "created_at": row.get("created_at"),
            })
        if domain and status == "verified" and action == "verify_publication":
            verified.append({
                "id": row.get("id"),
                "platform": row.get("platform"),
                "domain": domain,
                "verified_at": row.get("created_at"),
            })

    for row in marketplace.list_publications():
        domain = _event_domain(row)
        if domain and _same_business_day(row.get("posted_at"), context):
            submitted.append({
                "id": row.get("draft_id"),
                "platform": row.get("platform"),
                "domain": domain,
                "status": "published",
                "created_at": row.get("posted_at"),
            })
        if domain and bool(row.get("verified")) and _same_business_day(row.get("verified_at"), context):
            verified.append({
                "id": row.get("draft_id"),
                "platform": row.get("platform"),
                "domain": domain,
                "verified_at": row.get("verified_at"),
            })

    submitted_domains = sorted({x["domain"] for x in submitted})
    verified_domains = sorted({x["domain"] for x in verified})
    return {
        "business_timezone": context["timezone"],
        "business_date": day.isoformat(),
        "bucket_start_utc": context["start_utc"].isoformat(),
        "bucket_end_utc": context["end_utc"].isoformat(),
        "target_unique_domains": DAILY_SUBMISSION_TARGET,
        "submitted_today": len(submitted),
        "submitted_unique_domains_today": len(submitted_domains),
        "submitted_unique_domains": submitted_domains,
        "remaining_unique_domains": max(0, DAILY_SUBMISSION_TARGET - len(submitted_domains)),
        "verified_today": len(verified),
        "verified_unique_domains_today": len(verified_domains),
        "verified_unique_domains": verified_domains,
    }


def _acquisition_lane_snapshot(
    external_submissions: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Expose fresh autonomous delivery capacity separately from the human queue."""
    rows = marketplace.list_platforms()
    autonomous = [
        x for x in rows
        if x.get("delivery_lane") == "autonomous_candidate"
    ]
    human = [
        x for x in rows
        if x.get("delivery_lane") == "human_assisted"
        or (
            not x.get("autonomous_ready")
            and (
                x.get("registration_checkpoint")
                or x.get("registration_terminal_blocked")
                or x.get("registration_warming")
            )
        )
    ]
    allowed_autonomous = [
        x for x in autonomous
        if bool((x.get("free_policy") or {}).get("free"))
        and str((x.get("free_policy") or {}).get("rule_decision") or "") == "allowed"
    ]
    autonomous_ready_raw = [x for x in autonomous if x.get("autonomous_ready")]
    cooldown_domains = _recent_submission_domains(now)
    autonomous_ready = [
        x for x in autonomous_ready_raw
        if _event_domain({"url": x.get("url") or x.get("forum_url")}) not in cooldown_domains
    ]
    cooldown_blocked_ready = [
        x for x in autonomous_ready_raw
        if _event_domain({"url": x.get("url") or x.get("forum_url")}) in cooldown_domains
    ]
    human_assisted_ready = [x for x in human if x.get("publication_ready")]
    warming = [x for x in human if x.get("registration_warming")]
    human_blocked = [
        x for x in human
        if not x.get("registration_warming")
        and (
            x.get("registration_checkpoint")
            or x.get("registration_terminal_blocked")
        )
    ]
    pending_moderation = int(
        (external_submissions or {}).get("pending_total") or 0
    )
    queue = sorted(
        human,
        key=lambda x: (
            -int(x.get("acquisition_score") or 0),
            str(x.get("key") or ""),
        ),
    )[:20]
    return {
        "target_autonomous_candidates": reserve_policy.AUTONOMOUS_CANDIDATE_TARGET,
        "target_allowed_autonomous": reserve_policy.ALLOWED_AUTONOMOUS_TARGET,
        "target_ready_or_prefill_ready": reserve_policy.AUTONOMOUS_READY_TARGET,
        "autonomous_candidates": len(autonomous),
        "allowed_autonomous": len(allowed_autonomous),
        "autonomous_ready_raw": len(autonomous_ready_raw),
        "autonomous_ready": len(autonomous_ready),
        "cooldown_blocked_ready": len(cooldown_blocked_ready),
        "submission_domain_cooldown_days": reserve_policy.SUBMISSION_DOMAIN_COOLDOWN_DAYS,
        "human_assisted_candidates": len(human),
        "human_assisted_ready": len(human_assisted_ready),
        "human_blocked": len(human_blocked),
        "warming": len(warming),
        "pending_moderation": pending_moderation,
        "human_queue": [
            {
                "platform": x.get("key"),
                "acquisition_score": x.get("acquisition_score"),
                "checkpoint": x.get("registration_checkpoint"),
                "warming": bool(x.get("registration_warming")),
            }
            for x in queue
        ],
    }


def main() -> int:
    projects = [p for p in crowd_seo.list_projects() if crowd_seo.project_is_active(p)]
    capacities = {x.get("project"): x for x in crowd_seo.capacity_snapshot()}
    rows = [
        _project_row(p, capacities.get(p.get("id")) or {})
        for p in projects
    ]
    guardian = _guardian_state()
    timer = _timer_state()
    bootstrap = marketplace.platform_bootstrap_queue()
    external_submissions = _external_submissions_snapshot()
    daily_delivery = _daily_delivery_snapshot()
    acquisition_lanes = _acquisition_lane_snapshot(external_submissions)

    infrastructure_ok = bool(
        rows
        and all(x.get("infrastructure_ok") for x in rows)
        and guardian.get("ok")
        and timer.get("ok")
    )
    delivery_ready = bool(
        infrastructure_ok
        and all(x.get("delivery_ready") for x in rows)
    )

    if delivery_ready:
        state = "PASS"
    elif infrastructure_ok:
        state = "EXTERNAL_ONBOARDING_PENDING"
    else:
        state = "FAIL"

    result = {
        "certificate": "BORIS_CROWD_SEO_PRODUCTION_CERTIFIER_V1",
        "at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "infrastructure_ok": infrastructure_ok,
        "delivery_ready": delivery_ready,
        "projects": rows,
        "guardian": guardian,
        "guardian_timer": timer,
        "external_submissions": external_submissions,
        "daily_delivery": daily_delivery,
        "acquisition_lanes": acquisition_lanes,
        "bootstrap": {
            "needed": bootstrap.get("needed"),
            "human_action_required": bootstrap.get("human_action_required"),
            "owner_action_required": bootstrap.get("owner_action_required"),
            "reusable_across_clients": bootstrap.get("reusable_across_clients"),
            "by_checkpoint": bootstrap.get("by_checkpoint"),
        },
        "policy": {
            "read_only": True,
            "paid_ai_calls": 0,
            "provider_mutations": 0,
            "registrations_performed": 0,
            "publications_performed": 0,
            "hard_excluded_platforms": HARD_EXCLUDED,
            "fail_closed_on_external_onboarding": True,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"BORIS_CROWD_SEO_PRODUCTION_CERTIFIER_V1={state}")
    return 0 if delivery_ready else (2 if infrastructure_ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
