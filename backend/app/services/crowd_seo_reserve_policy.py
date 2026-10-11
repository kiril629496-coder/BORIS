from __future__ import annotations

# Authoritative Crowd SEO inventory policy.
# Commercial volume is never defined here: every client project carries the
# exact quantity purchased in its order. These targets describe only the
# reusable BORIS platform pool that should keep growing across all clients.
STRATEGIC_UNIQUE_TARGET = 200
STRATEGIC_PER_FORMAT_TARGET = 100
SUPPORTED_FORMATS = ("goods", "services")

# Daily autonomous-delivery SLO and the reusable reserve needed to avoid
# starting each business day from an empty queue.
DAILY_UNIQUE_SUBMISSION_TARGET = 20
AUTONOMOUS_CANDIDATE_TARGET = 100
ALLOWED_AUTONOMOUS_TARGET = 50
AUTONOMOUS_READY_TARGET = 40
# A domain accepted recently is not counted as next-day autonomous reserve.
# This prevents one-time directory submissions from inflating READY across days.
SUBMISSION_DOMAIN_COOLDOWN_DAYS = 30


def rule_counts_for_reserve(rule: dict | None) -> bool:
    """Return True only when a rule has enough evidence for reusable inventory.

    Curated platforms may use an explicit allowed rule decision directly.
    Dynamically promoted exact surfaces are held to a stricter standard: at
    least one fetched inspection must itself verify the surface as allowed or
    reply-only. This prevents aggregate heuristics from inflating inventory.
    """
    rule = rule or {}
    if str(rule.get("decision") or "") != "allowed":
        return False
    requirements = {str(x) for x in (rule.get("requirements") or [])}
    if "curated_vendor_surface_verified" in requirements:
        return True
    dynamic_override = bool(requirements & {
        "use_exact_discovered_goods_surface_only",
        "use_exact_discovered_service_surface_only",
    })
    if not dynamic_override:
        return True
    return any(
        str(x.get("decision") or "") in {"allowed", "reply_only"}
        for x in (rule.get("inspections") or [])
        if isinstance(x, dict)
    )


def snapshot() -> dict:
    return {
        "commercial_target_source": "project.target_count + project.bonus_count",
        "strategic_unique_target": STRATEGIC_UNIQUE_TARGET,
        "strategic_per_format_target": STRATEGIC_PER_FORMAT_TARGET,
        "supported_formats": list(SUPPORTED_FORMATS),
        "daily_unique_submission_target": DAILY_UNIQUE_SUBMISSION_TARGET,
        "autonomous_candidate_target": AUTONOMOUS_CANDIDATE_TARGET,
        "allowed_autonomous_target": ALLOWED_AUTONOMOUS_TARGET,
        "autonomous_ready_target": AUTONOMOUS_READY_TARGET,
        "submission_domain_cooldown_days": SUBMISSION_DOMAIN_COOLDOWN_DAYS,
    }


# CROWD_DAILY_EXECUTION_POLICY_V1
# One authoritative cadence for the Crowd SEO guardian. The systemd timer,
# runtime guardian report and read-only production certifier all consume/check
# this value so a future rule change cannot silently remain "only in code".
GUARD_INTERVAL_MINUTES = 10
GUARD_SCHEDULED_RUNS_PER_DAY = (24 * 60) // GUARD_INTERVAL_MINUTES
# One IANA timezone is the source of truth for Crowd SEO business-day SLOs.
# Event timestamps remain UTC; only daily bucketing/cadence reporting converts
# into this zone so midnight follows the owner's operating day.
BUSINESS_TIMEZONE = "Europe/Moscow"

# The publication budget is intentionally bounded per pass. The guardian runs
# repeatedly and moves to a different eligible domain; CAPTCHA/SMS/legal
# checkpoints are recorded and skipped rather than retried aggressively.
MAX_SAFE_PUBLISH_ATTEMPTS_PER_RUN = 2


def daily_execution_policy() -> dict:
    return {
        "business_timezone": BUSINESS_TIMEZONE,
        "guard_interval_minutes": GUARD_INTERVAL_MINUTES,
        "scheduled_runs_per_day": GUARD_SCHEDULED_RUNS_PER_DAY,
        "max_safe_publish_attempts_per_run": MAX_SAFE_PUBLISH_ATTEMPTS_PER_RUN,
        "rules_checked_every_guard_run": True,
        "new_domain_first": True,
        "paid_placement_allowed": False,
        "captcha_bypass_allowed": False,
    }
