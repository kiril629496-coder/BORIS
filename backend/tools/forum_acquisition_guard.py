#!/usr/bin/env python3
from __future__ import annotations

import json
import fcntl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import platform_rules
from app.services import forum_quality
from app.services import service_marketplace as marketplace
from app.services import forum_discovery
from app.services import publication_discovery
from app.services import crowd_seo
from app.services import crowd_seo_reserve_policy as reserve_policy

# A guardian runs every 10 minutes. Keep each pass bounded so it releases the
# shared mutation lock before the onboarding worker's next window. More work is
# achieved by many short passes, not one multi-minute batch that starves onboarding.
MAX_AUDITS_PER_RUN = 6
MAX_REGISTRATION_PREFLIGHTS_PER_RUN = 2
MAX_REGISTRATION_SUBMITS_PER_RUN = 1
MAX_REGISTRATION_VERIFICATIONS_PER_RUN = 2
STALE_AFTER = timedelta(days=6)
# A transport failure is not a valid six-day rule verdict. Retry it on the
# same short service cadence used for other infrastructure checkpoints, while
# keeping successful/semantic audits on the normal six-day freshness window.
TRANSIENT_AUDIT_RETRY_INTERVAL = timedelta(hours=2)
TRANSIENT_AUDIT_ERROR_MARKERS = (
    "temporary failure in name resolution",
    "name or service not known",
    "name resolutionerror",
    "nameresolutionerror",
    "err_name_not_resolved",
    "connecttimeout",
    "readtimeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "network is unreachable",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)
STATUS_FILE = BACKEND_ROOT / "data" / "service_marketplaces" / "guardian_status.json"
# FORUM_ACQUISITION_SINGLETON_LOCK_V1
# Timer/manual/self-heal launches may overlap. Only one guardian may mutate
# registration/publication state at a time, otherwise duplicate registrations
# or duplicate publishes become possible.
GUARD_LOCK_FILE = BACKEND_ROOT / "run" / "forum_acquisition_guard.lock"

def _acquire_run_lock(path: Path = GUARD_LOCK_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh

def _daily_execution_snapshot(now: datetime, *, mark_run: bool = True) -> dict:
    """Persist/measure execution cadence so daily rules cannot silently stop.

    Historical misses remain visible, but a repaired timer can recover health
    after the next normal interval. This avoids keeping production red for the
    rest of the UTC day after the root cause of an earlier scheduling collision
    has already been fixed.
    """
    policy = reserve_policy.daily_execution_policy()
    interval = int(policy["guard_interval_minutes"])
    today = now.date().isoformat()
    row = {}
    if DAILY_EXECUTION_FILE.exists():
        try:
            row = json.loads(DAILY_EXECUTION_FILE.read_text(encoding="utf-8"))
        except Exception:
            row = {}
    if str(row.get("date") or "") != today:
        row = {
            "date": today,
            "first_run_at": now.isoformat(),
            "runs": 0,
        }
    first = _parse_iso(row.get("first_run_at")) or now
    previous_last = _parse_iso(row.get("last_run_at"))
    if mark_run:
        row["runs"] = int(row.get("runs") or 0) + 1
        row["last_run_at"] = now.isoformat()
    elapsed_minutes = max(0, int((now - first).total_seconds() // 60))
    expected_since_first = min(
        int(policy["scheduled_runs_per_day"]),
        elapsed_minutes // max(1, interval) + 1,
    )
    historical_missed_runs = max(
        0,
        expected_since_first - int(row.get("runs") or 0),
    )
    last_gap_minutes = None
    if previous_last is not None:
        last_gap_minutes = max(0.0, (now - previous_last).total_seconds() / 60.0)
    # One overlapping/slow cycle may legitimately consume the next tick. A
    # healthy cadence must recover on the following normal run instead of
    # remaining failed for the whole day. Freshness is independently enforced
    # by the production certifier.
    max_recent_gap_minutes = interval * 2 + 2
    recent_cadence_ok = bool(
        last_gap_minutes is None
        or last_gap_minutes <= max_recent_gap_minutes
    )
    row.update({
        "policy": policy,
        "expected_runs_since_first": expected_since_first,
        "scheduled_runs_per_day": int(policy["scheduled_runs_per_day"]),
        "historical_missed_runs": historical_missed_runs,
        "last_gap_minutes": round(last_gap_minutes, 2) if last_gap_minutes is not None else None,
        "max_recent_gap_minutes": max_recent_gap_minutes,
        "cadence_ok": recent_cadence_ok,
    })
    if mark_run:
        DAILY_EXECUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DAILY_EXECUTION_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DAILY_EXECUTION_FILE)
    return row


DISCOVERY_INTERVAL = timedelta(hours=24)
DISCOVERY_NO_PROGRESS_BACKOFF = timedelta(hours=1)
# Keep the 10-minute guardian lightweight. Real client/placement deficits may
# force discovery at most twice per hour; strategic reserve growth is bounded
# to a few passes per day. This prevents long search sweeps from holding the
# shared acquisition lock across the onboarding-worker window.
DISCOVERY_URGENT_FORCE_INTERVAL = timedelta(minutes=30)
# When capacity exists only behind CAPTCHA/terms/identity, keep looking for
# lower-friction guest/direct-registration alternatives without hammering
# search engines on every 10-minute Guardian tick.
DISCOVERY_AUTONOMOUS_FORCE_INTERVAL = timedelta(hours=2)
DISCOVERY_STRATEGIC_FORCE_INTERVAL = timedelta(hours=6)
# PUBLICATION_DISCOVERY_GUARD_SCHEDULE_V2
# Broad non-forum discovery is heavier than one guard pass, so keep it on the
# existing singleton owner but refresh it several times per day while a real
# publication deficit exists. No second worker/timer is introduced.
PUBLICATION_DISCOVERY_DEFICIT_INTERVAL = timedelta(minutes=30)
PUBLICATION_DISCOVERY_IDLE_INTERVAL = timedelta(hours=12)
PUBLICATION_DISCOVERY_CLOCK_SKEW = timedelta(minutes=15)
# PUBLICATION_DISCOVERY_BOUNDED_EXECUTION_V1: this search is useful but must
# never hold the shared acquisition singleton long enough to suppress publish
# or registration windows. A timed-out pass leaves its state untouched and is
# automatically retried on a later guardian tick.
PUBLICATION_DISCOVERY_WALL_TIMEOUT_S = 70
PUBLICATION_DISCOVERY_DEFICIT_QUERIES = 6
PUBLICATION_DISCOVERY_IDLE_QUERIES = 3
CROWD_VERIFY_INTERVAL = timedelta(hours=24)
REGISTRATION_RETRY_INTERVAL = timedelta(hours=24)
REGISTRATION_TRANSIENT_RETRY_INTERVAL = timedelta(hours=2)
# Broken/missing authoring forms are not transient network blips. Retrying them
# every hour wastes the bounded acquisition budget and can look abusive. Keep
# the short two-hour retry only for actual timeouts; technical form failures
# return to the normal daily recheck cadence.
REGISTRATION_TECHNICAL_RETRY_INTERVAL = timedelta(hours=24)
BOOTSTRAP_VERIFY_INTERVAL = timedelta(hours=6)
MAX_CROWD_PUBLISHES_PER_RUN = reserve_policy.MAX_SAFE_PUBLISH_ATTEMPTS_PER_RUN
MAX_CROWD_VERIFICATIONS_PER_RUN = 20
DAILY_EXECUTION_FILE = BACKEND_ROOT / "data" / "service_marketplaces" / "crowd_guard_daily_execution.json"

# GOODS_SERVICES_RESERVE_DISCOVERY_V1:
# Crowd SEO is sold as one reusable product to clients with goods and services.
# Do not wait for the first such paying project before building the platform
# reserve: every normal discovery cycle keeps these two broad formats in the
# search priority in addition to any niches that are currently short of slots.
STRATEGIC_RESERVE_NICHES = reserve_policy.SUPPORTED_FORMATS
# Authoritative targets live in crowd_seo_reserve_policy so a parallel edit of
# this guardian cannot silently reduce the long-term product target.
STRATEGIC_RESERVE_TARGET = reserve_policy.STRATEGIC_PER_FORMAT_TARGET
STRATEGIC_RESERVE_TOTAL_TARGET = reserve_policy.STRATEGIC_UNIQUE_TARGET

# Commercial readiness follows only explicit active client orders.
# Strategic discovery is independent and keeps expanding the reusable pool.
_MULTIPART_SITE_SUFFIXES = {
    "com.ua", "net.ua", "org.ua",
    "co.uk", "org.uk",
    "com.au", "com.br", "co.nz",
}


def _site_domain(url: str | None) -> str:
    host = urlparse(str(url or "")).hostname or ""
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    tail = ".".join(parts[-2:])
    if tail in _MULTIPART_SITE_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail


def _reconcile_terminal_registry_conflicts() -> list[dict]:
    """Self-heal stale discovery terminal marks contradicted by live registry.

    CAPTCHA/terms/other onboarding checkpoints are handled by BOOTSTRAP, not by
    the discovery terminal cache. If an enabled forum has a fresh explicit
    zero-cost allowed rule decision and its current registration state is not
    terminal, a historical terminal mark must not keep hiding the domain.
    """
    terminal = forum_discovery.terminal_domain_map()
    if not terminal:
        return []
    registrations = {x.get("platform"): x for x in marketplace.registration_plan()}
    reactivated: list[dict] = []
    for item in marketplace.list_platforms():
        if item.get("channel_type") != "forum" or not item.get("enabled_for_outreach"):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        rule = platform_rules.latest(key) or {}
        if str(rule.get("decision") or "") != "allowed":
            continue
        reg = registrations.get(key) or {}
        if marketplace.registration_is_terminally_blocked(reg):
            continue
        domain = (urlparse(str(item.get("url") or "")).hostname or "").lower().removeprefix("www.")
        if not domain:
            continue
        candidates = [d for d in terminal if domain == d or domain.endswith("." + d) or d.endswith("." + domain)]
        for blocked_domain in candidates:
            old = terminal.get(blocked_domain) or {}
            if forum_discovery.unmark_terminal_domain(
                blocked_domain,
                reason=f"live_registry_allowed:{key}",
            ):
                reactivated.append({
                    "domain": blocked_domain,
                    "platform": key,
                    "previous_reason": old.get("reason"),
                })
                terminal.pop(blocked_domain, None)
    return reactivated



def _rule_counts_for_reserve(rule: dict | None) -> bool:
    """Compatibility wrapper around the authoritative reserve policy."""
    return reserve_policy.rule_counts_for_reserve(rule)

def _active_order_targets_by_format() -> dict[str, int]:
    """Largest explicit active client order that each broad format must serve."""
    targets = {fmt: 0 for fmt in reserve_policy.SUPPORTED_FORMATS}
    for project in crowd_seo.list_projects():
        if not crowd_seo.project_is_active(project):
            continue
        if str(project.get("plan_mode") or "") == "inventory":
            continue
        required = crowd_seo._project_required_count(project)
        offer_type = crowd_seo._legacy_offer_type(project)
        formats = reserve_policy.SUPPORTED_FORMATS if offer_type == "mixed" else (offer_type,)
        for fmt in formats:
            if fmt in targets:
                targets[fmt] = max(targets[fmt], required)
    return targets


def _strategic_reserve_snapshot() -> dict:
    """Global reusable inventory plus readiness for current explicit orders.

    Strategic goals grow the reusable pool. Commercial readiness is computed
    from active client project quantities and never from a global package size.
    """
    strategic_niches = reserve_policy.SUPPORTED_FORMATS
    strategic_format_target = reserve_policy.STRATEGIC_PER_FORMAT_TARGET
    strategic_unique_target = reserve_policy.STRATEGIC_UNIQUE_TARGET
    active_targets = _active_order_targets_by_format()

    coverage: dict[str, set[str]] = {niche: set() for niche in strategic_niches}
    all_sellable_sites: set[str] = set()
    registrations = {x.get("platform"): x for x in marketplace.registration_plan()}
    for item in marketplace.list_platforms():
        if item.get("channel_type") != "forum" or not item.get("enabled_for_outreach"):
            continue
        key = str(item.get("key") or "")
        if not _rule_counts_for_reserve(platform_rules.latest(key)):
            continue
        if marketplace.registration_is_terminally_blocked(registrations.get(key) or {}):
            continue
        site = _site_domain(item.get("url"))
        if not site:
            continue

        niches: set[str] = set()
        for surface in item.get("publication_surfaces") or []:
            niches.update(
                str(x).strip().lower()
                for x in (surface.get("niches") or [])
                if str(x).strip()
            )
        if not niches:
            continue

        strategic_match = False
        for niche in strategic_niches:
            if niche in niches:
                coverage[niche].add(site)
                strategic_match = True
        # The 200-site strategic target is specifically the reusable
        # goods/services pool. Allowed forums for unrelated niches must not
        # inflate the unique-site progress counter.
        if strategic_match:
            all_sellable_sites.add(site)

    rows = {
        niche: {
            "target": strategic_format_target,
            "allowed_unique_sites": len(coverage[niche]),
            "deficit": max(0, strategic_format_target - len(coverage[niche])),
            "sites": sorted(coverage[niche]),
        }
        for niche in strategic_niches
    }
    unique_total_deficit = max(0, strategic_unique_target - len(all_sellable_sites))
    format_max_deficit = max((row["deficit"] for row in rows.values()), default=0)

    sellable_rows = {}
    for niche in strategic_niches:
        target = int(active_targets.get(niche) or 0)
        allowed = len(coverage[niche])
        sellable_rows[niche] = {
            "target": target,
            "allowed_unique_sites": allowed,
            "deficit": max(0, target - allowed),
            "ready_for_active_orders": target == 0 or allowed >= target,
            "target_source": "active_client_orders",
        }

    sellable_total_target = sum(int(x) for x in active_targets.values())
    sellable_total_deficit = sum(int(row["deficit"]) for row in sellable_rows.values())
    sellable_max_deficit = max((int(row["deficit"]) for row in sellable_rows.values()), default=0)

    return {
        "policy": reserve_policy.snapshot(),
        "target_total": strategic_unique_target,
        "allowed_unique_sites_total": len(all_sellable_sites),
        "allowed_format_site_slots_total": sum(int(row["allowed_unique_sites"]) for row in rows.values()),
        "total_deficit": unique_total_deficit,
        "target_per_format": strategic_format_target,
        "formats": rows,
        "max_deficit": max(unique_total_deficit, format_max_deficit),
        "complete": unique_total_deficit == 0 and all(row["deficit"] == 0 for row in rows.values()),
        "active_order_targets_by_format": active_targets,
        "sellable_target_total": sellable_total_target,
        "sellable_target_per_format": max(active_targets.values(), default=0),
        "sellable_formats": sellable_rows,
        "sellable_total_deficit": sellable_total_deficit,
        "sellable_max_deficit": sellable_max_deficit,
        "sellable_complete": all(
            row["ready_for_active_orders"] for row in sellable_rows.values()
        ),
    }


HARD_BLOCK_REASONS = {
    "paid_advertising",
    "commercial_topics_paid_only",
    "paid_service_placement",
    "paid_participant_required",
    "free_advertising_prohibited",
    "responses_may_require_payment",
    "marketplace_cost_model_not_zero_only",
    "zero_cost_response_path_not_verified",
    "paid_features_or_cost_not_verified",
    "terms_prohibit_advertising_or_spam",
    "advertising_is_paid_product",
    "registration_disabled",
}

def _audit_has_transient_transport_error(row: dict) -> bool:
    # Once other official evidence has produced a definitive semantic verdict,
    # a challenged auxiliary URL is not a reason to churn the audit every 2h.
    if str(row.get("decision") or "") in {"allowed", "blocked", "reply_only"}:
        return False
    errors = row.get("errors") or []
    payload = json.dumps(errors, ensure_ascii=False).lower()
    if any(marker in payload for marker in TRANSIENT_AUDIT_ERROR_MARKERS):
        return True

    # A public forum can temporarily present a Cloudflare/browser security
    # challenge as HTTP 403. That is infrastructure state, not a six-day rule
    # verdict. Retry it on the short cadence, but do not treat arbitrary 403s
    # as transient unless the captured page itself identifies the challenge.
    inspections = row.get("inspections") or []
    inspection_payload = json.dumps(inspections, ensure_ascii=False).lower()
    challenge_markers = (
        "cloudflare",
        "checking your browser",
        "security check",
        "выполнение проверки безопасности",
        "проверяет, что вы не бот",
    )
    return any(marker in inspection_payload for marker in challenge_markers)


def _audit_terminal_dead_reason(row: dict) -> str | None:
    """Return a terminal reason only for a confirmed dead/parked endpoint."""
    inspections = row.get("inspections") or []
    text_payload = " ".join(
        str(item.get("text_excerpt") or "").lower()
        for item in inspections
    )
    parked_markers = (
        "this domain is successfully pointed at wp engine, but is not configured",
        "the site you were looking for couldn't be found",
        "this domain is parked",
        "buy this domain",
        "domain is for sale",
        "this domain may be for sale",
    )
    if any(marker in text_payload for marker in parked_markers):
        return "parked_or_unconfigured_domain"

    statuses = [
        int(item.get("http_status") or 0)
        for item in inspections
        if int(item.get("http_status") or 0) > 0
    ]
    if statuses and all(status in {404, 410} for status in statuses):
        return "http_410" if 410 in statuses else "http_404"

    # Browser fallback can itself fail on a dead endpoint. Only accept an HTTP
    # error string as terminal when there was no successful inspection.
    if not statuses:
        errors_payload = json.dumps(row.get("errors") or [], ensure_ascii=False).lower()
        if "410 client error" in errors_payload:
            return "http_410"
        if "404 client error" in errors_payload:
            return "http_404"
    return None


def needs_audit(key: str) -> bool:
    base = marketplace.FREE_PLATFORM_POLICY.get(key, {})
    if base.get("reason") in HARD_BLOCK_REASONS:
        return False
    row = platform_rules.latest(key)
    if not row:
        return True
    try:
        checked = datetime.fromisoformat(str(row.get("checked_at") or "").replace("Z", "+00:00"))
    except Exception:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - checked
    if _audit_has_transient_transport_error(row):
        return age >= TRANSIENT_AUDIT_RETRY_INTERVAL
    return age >= STALE_AFTER

def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def _crowd_link_due(placement: dict, now: datetime) -> bool:
    if not placement.get("publication_url"):
        return False
    if placement.get("status") == "published_unverified":
        return True
    checked = _parse_iso(placement.get("checked_at"))
    if placement.get("status") == "verification_temporarily_blocked":
        # 401/403/429/5xx from the verifier is a transient observation, not a
        # terminal publication state. Recheck automatically instead of leaving
        # a real published URL stuck forever until the owner notices it.
        return checked is None or now - checked >= TRANSIENT_AUDIT_RETRY_INTERVAL
    if placement.get("status") != "verified":
        return False
    return checked is None or now - checked >= CROWD_VERIFY_INTERVAL

def _crowd_in_guarantee(project: dict, placement: dict, now: datetime) -> bool:
    try:
        days = max(0, int(project.get("guarantee_days", 30)))
    except Exception:
        days = 30
    started = _parse_iso(placement.get("published_at")) or _parse_iso(project.get("created_at"))
    if started is None:
        return True
    return now - started <= timedelta(days=days)

def _active_project_bootstrap_snapshot(bootstrap_state: dict | None = None) -> dict:
    """Separate active-project onboarding from the global reusable reserve.

    Uses the same capacity contract as Crowd SEO. Inventory-mode projects may
    expose more reusable platforms than they currently need, so this status
    selects only the minimum highest-value bootstrap set needed to cover the
    live operational requirement instead of reporting the whole reserve as
    urgent work.
    """
    state = bootstrap_state if bootstrap_state is not None else marketplace.platform_bootstrap_queue()
    try:
        priority = crowd_seo.bootstrap_priority_snapshot()
    except Exception:
        priority = {}

    capacity_rows = [
        row for row in crowd_seo.capacity_snapshot()
        if int(row.get("required") or 0) > 0
    ]
    required = max((int(row.get("required") or 0) for row in capacity_rows), default=0)

    items = list(state.get("items") or [])
    candidates = [
        x for x in items
        if str(x.get("platform") or "") in priority
    ]
    candidates.sort(
        key=lambda x: (
            -int((priority.get(str(x.get("platform") or "")) or {}).get("max_relevance") or 0),
            bool((priority.get(str(x.get("platform") or "")) or {}).get("requires_warmup_after_bootstrap")),
            -int((priority.get(str(x.get("platform") or "")) or {}).get("potential_slots") or 0),
            int(x.get("priority") or 9999),
            str(x.get("name") or ""),
        )
    )

    urgent_items = []
    potential_slots = 0
    for item in candidates:
        if required and potential_slots >= required:
            break
        platform = str(item.get("platform") or "")
        urgent_items.append(item)
        potential_slots += int((priority.get(platform) or {}).get("potential_slots") or 0)

    # If there is no active operational requirement, nothing in the reusable
    # reserve should be presented as urgent client work.
    if required <= 0:
        urgent_items = []
        potential_slots = 0

    warmup_after = sum(
        1
        for x in urgent_items
        if bool((priority.get(str(x.get("platform") or "")) or {}).get("requires_warmup_after_bootstrap"))
    )
    return {
        "needed_now": len(urgent_items),
        "potential_slots": potential_slots,
        "required_slots": required,
        "contract_covered_after_bootstrap": potential_slots >= required if required else True,
        "warming_after_bootstrap": warmup_after,
        "reserve_items": max(0, len(items) - len(urgent_items)),
        "owner_action_required": False,
        "executor_role": "platform_onboarding_worker",
        "platforms": [str(x.get("platform") or "") for x in urgent_items],
    }


def _registration_retry_due(row: dict | None, now: datetime) -> bool:
    if not row:
        return True
    status = str(row.get("status") or "not_registered")
    if status == "not_registered":
        return True
    if status in {"ready", "verification_required", "warming"}:
        return False
    if str(row.get("checkpoint") or "") == "preflight_timeout":
        updated = _parse_iso(row.get("updated_at"))
        return updated is None or now - updated >= REGISTRATION_TRANSIENT_RETRY_INTERVAL
    if str(row.get("checkpoint") or "") in {
        "account_creation_unverified",
        "login_failed",
        "login_result_unclear",
        "login_verification_required",
        "post_submit_review",
    }:
        return False
    if marketplace.registration_is_terminally_blocked(row):
        return False
    if not marketplace.registration_is_recoverable(row):
        return False
    updated = _parse_iso(row.get("updated_at"))
    checkpoint = str(row.get("checkpoint") or "")
    if checkpoint in {
        "registration_form_not_found",
        "registration_form_error",
        "registration_adapter_error",
        "registration_unavailable",
    }:
        return updated is None or now - updated >= REGISTRATION_TECHNICAL_RETRY_INTERVAL
    return updated is None or now - updated >= REGISTRATION_RETRY_INTERVAL

def _preflight_allows_auto_submit(payload: dict | None, platform: str | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("submitted") or payload.get("checkpoint"):
        return False
    filled = payload.get("filled") or {}
    if filled.get("consent") or filled.get("profession") or filled.get("email_notifications_consent"):
        return False
    if platform == "wmboard_services":
        return bool(
            filled.get("email")
            and filled.get("registration_mode") == "email_only"
            and not filled.get("username")
            and not filled.get("password")
        )
    if not all(filled.get(k) for k in ("username", "email", "password")):
        return False
    return True

def _registration_submit_needs_immediate_verify(payload: dict | None) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("submitted")
        and payload.get("checkpoint") in {None, "registered"}
    )


def _registration_verification_due(row: dict | None, now: datetime) -> bool:
    if not row or marketplace.registration_is_terminally_blocked(row):
        return False
    checkpoint = str(row.get("checkpoint") or "")
    bootstrap_checkpoint = checkpoint in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
    warming_checkpoint = marketplace.registration_is_warming(row)
    if checkpoint not in {
        "account_creation_unverified",
        "login_failed",
        "login_result_unclear",
        "login_verification_required",
        "post_submit_review",
    } and not bootstrap_checkpoint and not warming_checkpoint:
        return False
    updated = _parse_iso(row.get("updated_at"))
    if warming_checkpoint:
        interval = REGISTRATION_RETRY_INTERVAL
    else:
        interval = BOOTSTRAP_VERIFY_INTERVAL if bootstrap_checkpoint else REGISTRATION_RETRY_INTERVAL
    return updated is None or now - updated >= interval

def _development_draft_target_safe(project: dict, placement: dict, draft_id: str) -> bool:
    """Fail closed if a software-development post can publish with a stale root URL."""
    project_site = str(project.get("site") or "")
    if "/software-dev" not in project_site:
        return True
    target = str(placement.get("target_url") or "")
    if "/software-dev" not in target:
        return False
    draft = next(
        (x for x in marketplace.list_drafts() if str(x.get("id") or "") == str(draft_id or "")),
        None,
    )
    if not draft or str(draft.get("status") or "") != "approved":
        return False
    if str(draft.get("target_url") or "") != target:
        return False
    return target in str(draft.get("text") or "")


def run_crowd_seo_cycle(assistant_script: str) -> dict:
    """Drive approved Crowd SEO projects without owner-as-operator work."""
    now = datetime.now(timezone.utc)
    publish_budget = MAX_CROWD_PUBLISHES_PER_RUN
    verify_budget = MAX_CROWD_VERIFICATIONS_PER_RUN
    attempted_domains_this_run: set[str] = set()
    out = {
        "projects": 0,
        "analysis_refreshed": 0,
        "refreshed": 0,
        "drafts_synced": 0,
        "publication_sync_changes": 0,
        "publish_attempts": 0,
        "published_verified": 0,
        "link_checks": 0,
        "replacement_required": 0,
        "replacements_created": 0,
        "errors": [],
    }

    for base_project in crowd_seo.list_projects():
        project_id = str(base_project.get("id") or "")
        if not project_id:
            continue
        if not crowd_seo.project_is_active(base_project):
            continue
        out["projects"] += 1

        try:
            if base_project.get("site"):
                analysis_refresh = crowd_seo.refresh_project_analysis(project_id)
                out["analysis_refreshed"] += int(analysis_refresh.get("changed") or 0)

            refreshed = crowd_seo.refresh_project_forums(project_id)
            out["refreshed"] += int(refreshed.get("changed") or 0)

            draft_sync = crowd_seo.sync_project_drafts(project_id)
            out["drafts_synced"] += int(draft_sync.get("drafts") or 0)

            pub_sync = crowd_seo.sync_publications(project_id)
            out["publication_sync_changes"] += int(pub_sync.get("changed") or 0)

            project = crowd_seo.get_project(project_id)

            # Verify new links immediately and re-check verified links once per day
            # during the 30-day guarantee window.
            for placement in list(project.get("placements") or []):
                if verify_budget <= 0:
                    break
                if not _crowd_link_due(placement, now):
                    continue
                if not _crowd_in_guarantee(project, placement, now):
                    continue
                checked = crowd_seo.verify_placement(project_id, placement["id"])
                verify_budget -= 1
                out["link_checks"] += 1
                if checked.get("status") == "replacement_required":
                    out["replacement_required"] += 1

            replacement = crowd_seo.schedule_replacements(project_id)
            out["replacements_created"] += int(replacement.get("created") or 0)

            # A replacement can become publishable immediately if a READY forum
            # is available. Refresh/sync once more before consuming publish budget.
            crowd_seo.refresh_project_forums(project_id)
            crowd_seo.sync_project_drafts(project_id)
            project = crowd_seo.get_project(project_id)

            if project.get("content_status") != "approved" or publish_budget <= 0:
                continue

            ready_placements = [
                placement
                for placement in list(project.get("placements") or [])
                if placement.get("status") == "ready_to_publish"
            ]
            used_domains = {
                _site_domain(placement.get("forum_url") or placement.get("publication_url"))
                for placement in list(project.get("placements") or [])
                if placement.get("publication_url")
            }
            used_domains.discard("")
            fresh_domain_placements = [
                placement
                for placement in ready_placements
                if _site_domain(placement.get("forum_url")) not in used_domains
            ]
            publish_queue = fresh_domain_placements or ready_placements

            for placement in publish_queue:
                if publish_budget <= 0:
                    break
                placement_domain = _site_domain(placement.get("forum_url"))
                if placement_domain and placement_domain in attempted_domains_this_run:
                    continue
                draft_id = str(placement.get("marketplace_draft_id") or "")
                if not draft_id:
                    crowd_seo.sync_project_drafts(project_id)
                    project = crowd_seo.get_project(project_id)
                    placement = next(
                        (x for x in project.get("placements", []) if x.get("id") == placement.get("id")),
                        placement,
                    )
                    draft_id = str(placement.get("marketplace_draft_id") or "")
                if not draft_id:
                    out["errors"].append({"project": project_id, "placement": placement.get("id"), "error": "draft_missing"})
                    continue

                # DEVELOPMENT_DRAFT_TARGET_GATE_V1
                # Guard the last irreversible step too: even if project state or
                # an old draft drifts later, software-development outreach must
                # never publish a generic BORIS-root link. Re-sync once, then
                # fail closed and continue with other work if the invariant is
                # still not satisfied.
                if not _development_draft_target_safe(project, placement, draft_id):
                    crowd_seo.sync_project_drafts(project_id)
                    project = crowd_seo.get_project(project_id)
                    placement = next(
                        (x for x in project.get("placements", []) if x.get("id") == placement.get("id")),
                        placement,
                    )
                    draft_id = str(placement.get("marketplace_draft_id") or draft_id)
                if not _development_draft_target_safe(project, placement, draft_id):
                    marketplace.record_attempt(
                        platform=str(placement.get("platform") or "unknown"),
                        action="publish",
                        status_value="blocked",
                        draft_id=draft_id,
                        error_code="development_draft_target_mismatch",
                        error_detail="Software-development draft does not contain its exact approved /software-dev target URL.",
                    )
                    out["errors"].append({
                        "project": project_id,
                        "placement": placement.get("id"),
                        "error": "development_draft_target_mismatch",
                    })
                    continue

                if placement_domain:
                    attempted_domains_this_run.add(placement_domain)
                proc = subprocess.run(
                    [
                        sys.executable,
                        assistant_script,
                        "prepare-post",
                        "--platform",
                        str(placement.get("platform") or ""),
                        "--draft-id",
                        draft_id,
                        "--publish",
                    ],
                    cwd="/root/BORIS/backend",
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                publish_budget -= 1
                out["publish_attempts"] += 1

                payload = None
                try:
                    payload = json.loads((proc.stdout or "").strip()) if (proc.stdout or "").strip() else None
                except Exception:
                    payload = None

                if not isinstance(payload, dict):
                    detail = ((proc.stderr or proc.stdout or "")[-1200:] or "publisher process returned no JSON")
                    marketplace.record_attempt(
                        platform=str(placement.get("platform") or "unknown"),
                        action="publish",
                        status_value="failed",
                        draft_id=draft_id,
                        error_code="publisher_process_failed",
                        error_detail=detail,
                    )
                    payload = {
                        "filled": False,
                        "publish_clicked": False,
                        "publication_verified": False,
                        "checkpoint": "publisher_process_failed",
                        "url": None,
                        "next_action": "retry_after_diagnosis",
                    }

                crowd_seo.record_publish_attempt(project_id, placement["id"], payload)
                if payload.get("publication_verified"):
                    crowd_seo.sync_publications(project_id)
                    latest = crowd_seo.get_project(project_id)
                    latest_pl = next(
                        (x for x in latest.get("placements", []) if x.get("id") == placement.get("id")),
                        None,
                    )
                    if latest_pl and latest_pl.get("publication_url") and verify_budget > 0:
                        checked = crowd_seo.verify_placement(project_id, placement["id"])
                        verify_budget -= 1
                        out["link_checks"] += 1
                        if checked.get("status") == "verified":
                            out["published_verified"] += 1
                        elif checked.get("status") == "replacement_required":
                            out["replacement_required"] += 1

            replacement = crowd_seo.schedule_replacements(project_id)
            out["replacements_created"] += int(replacement.get("created") or 0)
        except subprocess.TimeoutExpired:
            out["errors"].append({"project": project_id, "error": "publisher_timeout"})
        except Exception as exc:
            out["errors"].append({"project": project_id, "error": f"{type(exc).__name__}: {exc}"})

    return out

def _crowd_capacity_snapshot() -> list[dict]:
    return crowd_seo.capacity_snapshot()


def _discovery_schedule(
    ds: dict,
    capacity_deficit: int,
    *,
    urgent: bool = False,
    autonomous: bool = False,
    now: datetime | None = None,
) -> dict:
    now=now or datetime.now(timezone.utc)
    last=None
    try:
        last=datetime.fromisoformat(str(ds.get("at") or "").replace("Z","+00:00"))
    except Exception:
        last=None

    normal_due=bool(last is None or now-last >= DISCOVERY_INTERVAL)
    if urgent:
        force_interval = DISCOVERY_URGENT_FORCE_INTERVAL
    elif autonomous:
        force_interval = DISCOVERY_AUTONOMOUS_FORCE_INTERVAL
    else:
        force_interval = DISCOVERY_STRATEGIC_FORCE_INTERVAL
    forced_due=bool(
        capacity_deficit > 0
        and (last is None or now-last >= force_interval)
    )
    backoff_active=False
    backoff_until=None

    # DISCOVERY_REAL_PROGRESS_BACKOFF_V2: even when the previous forced search
    # found candidates, do not immediately repeat the whole search on every
    # 10-minute guardian tick. With zero progress keep the stronger one-hour
    # backoff; with progress use the urgent/strategic force interval above.
    previous_progress = int(ds.get("progress_count", ds.get("new_or_refreshed") or 0) or 0)
    if capacity_deficit > 0 and last is not None and previous_progress == 0:
        until=last+max(DISCOVERY_NO_PROGRESS_BACKOFF, force_interval)
        if now < until:
            forced_due=False
            backoff_active=True
            backoff_until=until.isoformat()

    return {
        "due": bool(normal_due or forced_due),
        "normal_due": normal_due,
        "forced_due": forced_due,
        "urgent": bool(urgent),
        "autonomous": bool(autonomous),
        "force_interval_minutes": int(force_interval.total_seconds() // 60),
        "backoff_active": backoff_active,
        "backoff_until": backoff_until,
        "previous_new_or_refreshed": int(ds.get("new_or_refreshed") or 0),
        "previous_progress_count": previous_progress,
    }


def _publication_discovery_urgency(capacity_rows: list[dict], base_deficit: int) -> int:
    """Keep direct-authoring discovery active while autonomous inventory is short.

    PUBLICATION_DISCOVERY_AUTONOMOUS_DEFICIT_V1:
    A project can have enough bootstrap candidates while every one of them is
    gated by CAPTCHA/terms/identity. In that case forum discovery is satisfied,
    but fast publication discovery must keep looking for guest/direct-authoring
    surfaces that can actually become READY without a human checkpoint.
    """
    autonomous = max(
        (int((row or {}).get("autonomous_deficit") or 0) for row in (capacity_rows or [])),
        default=0,
    )
    return max(int(base_deficit or 0), autonomous)


def _publication_discovery_schedule(
    state: dict,
    capacity_deficit: int,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    last = _parse_iso((state or {}).get("updated_at"))
    interval = (
        PUBLICATION_DISCOVERY_DEFICIT_INTERVAL
        if int(capacity_deficit or 0) > 0
        else PUBLICATION_DISCOVERY_IDLE_INTERVAL
    )
    clock_skew = bool(last and last > now + PUBLICATION_DISCOVERY_CLOCK_SKEW)
    due = bool(last is None or clock_skew or now - last >= interval)
    return {
        "due": due,
        "last_updated_at": last.isoformat() if last else None,
        "interval_hours": int(interval.total_seconds() // 3600),
        "interval_minutes": int(interval.total_seconds() // 60),
        "capacity_deficit": int(capacity_deficit or 0),
        "clock_skew_detected": clock_skew,
    }


def _run_publication_discovery_bounded(capacity_deficit: int) -> dict:
    """Run publication discovery in a hard-bounded child process."""
    max_queries = (
        PUBLICATION_DISCOVERY_DEFICIT_QUERIES
        if int(capacity_deficit or 0) > 0
        else PUBLICATION_DISCOVERY_IDLE_QUERIES
    )
    code = (
        "import json,sys; "
        "from app.services import publication_discovery as p; "
        "r=p.discover(max_queries=int(sys.argv[1]), per_query=6, workers=6); "
        "print(json.dumps(r, ensure_ascii=False))"
    )
    proc = subprocess.run(
        [str(BACKEND_ROOT / "venv" / "bin" / "python"), "-c", code, str(max_queries)],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=PUBLICATION_DISCOVERY_WALL_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"publication_discovery_exit_{proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '')[-500:]}"
        )
    lines=[line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("publication_discovery_empty_output")
    payload=json.loads(lines[-1])
    if not isinstance(payload,dict):
        raise RuntimeError("publication_discovery_invalid_payload")
    payload["bounded_execution"]={
        "max_queries":max_queries,
        "wall_timeout_seconds":PUBLICATION_DISCOVERY_WALL_TIMEOUT_S,
    }
    return payload


PUBLICATION_DEVELOPMENT_ROUTES_V1 = (
    ("Разработка веб-приложений и сервисов под заказ", "https://boris-ai.pro/software-dev/services/web-app-development"),
    ("Разработка SaaS-платформ под ключ", "https://boris-ai.pro/software-dev/services/saas-development"),
    ("Разработка CRM под бизнес-процессы компании", "https://boris-ai.pro/software-dev/services/crm-development"),
    ("Разработка AI-автоматизации для бизнеса", "https://boris-ai.pro/software-dev/services/ai-automation"),
    ("Разработка API-интеграций между бизнес-системами", "https://boris-ai.pro/software-dev/services/api-integration"),
    ("Интеграция 1С с CRM, сайтом и внешними сервисами", "https://boris-ai.pro/software-dev/services/1c-integration"),
    ("Разработка мобильных приложений под ключ", "https://boris-ai.pro/software-dev/services/mobile-app-development"),
    ("Разработка Telegram-ботов для бизнеса", "https://boris-ai.pro/software-dev/services/telegram-bots"),
    ("Разработка и интеграция IP-телефонии", "https://boris-ai.pro/software-dev/services/ip-telephony"),
)
PUBLICATION_DEVELOPMENT_KEYWORDS_V1 = (
    "разработка ПО под заказ", "разработка программного обеспечения",
    "разработка для бизнеса", "автоматизация бизнес процессов",
    "разработка веб приложения", "разработка SaaS",
    "разработка CRM", "API интеграция", "интеграция 1С",
    "разработка мобильного приложения", "разработка Telegram бота",
    "AI автоматизация бизнеса", "MVP разработка",
)

def _publication_development_drafts(platform_keys: set[str]) -> list[dict]:
    """Deterministic, pre-validated development-only drafts for pubdisc sites.

    PUBLICATION_DISCOVERY_DEVELOPMENT_ONLY_V1: publication-discovery inventory
    belongs to the custom software campaign. It must never inherit the generic
    BORIS/Avito/Direct acquisition copy. The owner has explicitly authorized
    autonomous software-development outreach; every actual submit still passes
    fresh platform rules, account/CAPTCHA/terms and publication verification.
    """
    out=[]
    routes=PUBLICATION_DEVELOPMENT_ROUTES_V1
    keywords=list(PUBLICATION_DEVELOPMENT_KEYWORDS_V1)
    for key in sorted(set(platform_keys or set())):
        p=marketplace.get_platform(key)
        if p is None or not marketplace.platform_policy(key).get("free"):
            continue
        base=sum(ord(ch) for ch in str(key)) % len(routes)
        for i in range(2):
            title,target=routes[(base+i) % len(routes)]
            kw=keywords[i:] + keywords[:i]
            body=(
                f"{title}\n\n"
                "Разрабатываем программное обеспечение под конкретные бизнес-задачи: от MVP и архитектуры "
                "до production, интеграций и дальнейшего развития. Работаем с веб-сервисами, SaaS, CRM, API, "
                "1С, мобильными приложениями, Telegram-ботами и AI-автоматизацией.\n\n"
                "Сначала фиксируем задачу, ожидаемый результат и границы работ, затем проектируем и реализуем решение. "
                "Можно начать с одного процесса или MVP и развивать систему после проверки результата.\n\n"
                f"Подробнее по направлению: {target}\n\n"
                "Ключевые фразы: " + "; ".join(kw[:13])
            )
            draft={
                "id": marketplace._draft_id(key, "development", i),
                "platform": key,
                "platform_name": p.name,
                "topic": "development",
                "title": title,
                "text": body,
                "site_url": target,
                "target_url": target,
                "publication_surface_post_url": p.url,
                "status": "approved",
                "approval_source": "owner_authorized_autonomous_software_development_outreach",
                "generated_without_openai": True,
                "variant": i+1,
                "copy_style": "publication_discovery_development_only_v1",
                "discovered_automatically": True,
                "free_only": True,
                "rule_check_required": True,
            }
            gate=marketplace.validate_publication_content(draft)
            if not gate.get("ok"):
                raise RuntimeError(f"publication development draft failed content gate: {key}: {gate}")
            out.append(draft)
    return out

def _publication_draft_needs_repair(draft: dict, publication_keys: set[str]) -> bool:
    if str(draft.get("platform") or "") not in publication_keys:
        return False
    if str(draft.get("status") or "") == "posted":
        return False
    topic=str(draft.get("topic") or "")
    # Client Crowd SEO has its own deterministic project renderer/lifecycle.
    # This repair is only for the generic acquisition drafts that previously
    # leaked product copy into Publication Discovery.
    if topic not in {"virtual_department", "development"}:
        return False
    if topic != "development":
        return True
    return not bool(marketplace.validate_publication_content(draft).get("ok"))


def _discovery_priority_niches(capacity_rows: list[dict]) -> list[str]:
    active_deficit_niches = {
        str(x.get("niche") or "business")
        for x in (capacity_rows or [])
        if int(
            x.get(
                "discovery_deficit_after_warmup",
                x.get("discovery_deficit_after_bootstrap", x.get("autonomous_deficit") or 0),
            )
            or 0
        ) > 0
    }
    # A dead/removed publication creates a real delivery deficit even when the
    # reusable platform inventory itself is still large enough. Capacity rows
    # carry that live placement deficit so discovery prioritizes the same niche
    # without a second, potentially inconsistent project-state read.
    active_deficit_niches.update({
        str(x.get("niche") or "business")
        for x in (capacity_rows or [])
        if int(x.get("placement_operational_deficit") or 0) > 0
    })
    # Capacity can be numerically full while every usable slot is behind a
    # human CAPTCHA/terms/identity checkpoint. Keep the active project niche at
    # the front of discovery until BORIS has autonomous/READY alternatives.
    active_deficit_niches.update({
        str(x.get("niche") or "business")
        for x in (capacity_rows or [])
        if int(x.get("autonomous_deficit") or 0) > 0
    })

    # Strategic discovery never stops at the capacity required by any single current client order.
    # Include every format that is still below its long-term target; order the
    # most deficient format first so the bounded query budget still closes the
    # weakest side quickly.
    reserve = _strategic_reserve_snapshot()
    reserve_rows = reserve.get("formats") or {}
    ordered_reserve_niches = [
        str(niche)
        for niche, row in sorted(
            reserve_rows.items(),
            key=lambda item: (-int((item[1] or {}).get("deficit") or 0), str(item[0])),
        )
        if int((row or {}).get("deficit") or 0) > 0
    ]
    # Active delivery/autonomous deficits come first. Strategic reserve niches
    # fill only the remaining bounded query budget.
    ordered = sorted(active_deficit_niches)
    for niche in ordered_reserve_niches:
        if niche not in ordered:
            ordered.append(niche)
    return ordered


def main() -> int:
    run_lock = _acquire_run_lock()
    if run_lock is None:
        print(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped_already_running",
            "reason": "forum_acquisition_guard_singleton_lock",
        }, ensure_ascii=False))
        return 0
    discovery_result = None
    publication_discovery_result = None
    terminal_reactivated = _reconcile_terminal_registry_conflicts()
    project_reconcile = crowd_seo.reconcile_projects()
    capacity_before = _crowd_capacity_snapshot()
    autonomous_deficit = max((int(x.get("autonomous_deficit") or 0) for x in capacity_before), default=0)
    discovery_deficit = max((
        int(x.get("discovery_deficit_after_bootstrap", x.get("autonomous_deficit") or 0) or 0)
        for x in capacity_before
    ), default=0)
    strategic_reserve_before = _strategic_reserve_snapshot()
    sellable_incomplete = not bool(strategic_reserve_before.get("sellable_complete"))
    strategic_reserve_deficit = int(
        strategic_reserve_before.get("sellable_max_deficit") or 0
        if sellable_incomplete
        else strategic_reserve_before.get("max_deficit") or 0
    )
    placement_deficit = max((
        int(x.get("placement_operational_deficit") or 0)
        for x in capacity_before
    ), default=0)
    urgent_discovery_deficit = max(discovery_deficit, placement_deficit)
    autonomous_discovery_deficit = max(0, autonomous_deficit)
    combined_discovery_deficit = max(
        urgent_discovery_deficit,
        autonomous_discovery_deficit,
        strategic_reserve_deficit,
    )
    ds = forum_discovery.status()
    discovery_schedule = _discovery_schedule(
        ds,
        combined_discovery_deficit,
        urgent=urgent_discovery_deficit > 0,
        autonomous=urgent_discovery_deficit <= 0 and autonomous_discovery_deficit > 0,
    )
    discovery_due = bool(discovery_schedule.get("due"))
    if discovery_due:
        try:
            priority_niches = _discovery_priority_niches(capacity_before)
            total_reserve_deficit = int(
                strategic_reserve_before.get("sellable_total_deficit") or 0
                if sellable_incomplete
                else strategic_reserve_before.get("total_deficit") or 0
            )
            if urgent_discovery_deficit > 0:
                # Current client/placement gap: search promptly but keep the
                # guardian bounded so the next onboarding-worker window stays free.
                discovery_query_budget, discovery_per_query = 48, 8
                directory_inspection_budget = 120
                findaforum_inspection_budget = 120
            elif autonomous_discovery_deficit > 0:
                # Capacity exists, but only behind human checkpoints. Spend a
                # modest bounded search budget on lower-friction IT/service
                # alternatives so verified delivery can improve without owner work.
                discovery_query_budget, discovery_per_query = 24, 8
                directory_inspection_budget = 60
                findaforum_inspection_budget = 80
            elif total_reserve_deficit > 100:
                # Strategic reserve growth is useful, but never more important
                # than the live automation cadence.
                discovery_query_budget, discovery_per_query = 32, 8
                directory_inspection_budget = 80
                findaforum_inspection_budget = 100
            elif total_reserve_deficit > 0:
                discovery_query_budget, discovery_per_query = 24, 8
                directory_inspection_budget = 60
                findaforum_inspection_budget = 80
            else:
                discovery_query_budget, discovery_per_query = 30, 6
                directory_inspection_budget = 0
                findaforum_inspection_budget = 0
            discovery_result = forum_discovery.discover(
                max_queries=discovery_query_budget,
                per_query=discovery_per_query,
                sleep_s=0.15,
                priority_niches=priority_niches,
                directory_max_inspections=directory_inspection_budget,
                findaforum_max_inspections=findaforum_inspection_budget,
            )
        except Exception as exc:
            discovery_result = {"error": f"{type(exc).__name__}: {exc}"}
            marketplace.record_attempt(
                platform="forum_discovery",
                action="discover",
                status_value="failed",
                error_code="discovery_exception",
                error_detail=discovery_result["error"],
            )

    publication_discovery_urgency = _publication_discovery_urgency(
        capacity_before, urgent_discovery_deficit
    )
    publication_discovery_state = publication_discovery.load()
    publication_discovery_schedule = _publication_discovery_schedule(
        publication_discovery_state,
        publication_discovery_urgency,
    )
    if publication_discovery_schedule["due"]:
        try:
            publication_discovery_result = _run_publication_discovery_bounded(
                publication_discovery_urgency
            )
        except subprocess.TimeoutExpired:
            publication_discovery_result = {
                "error": "publication_discovery_timeout",
                "timed_out": True,
                "wall_timeout_seconds": PUBLICATION_DISCOVERY_WALL_TIMEOUT_S,
            }
            marketplace.record_attempt(
                platform="publication_discovery",
                action="discover",
                status_value="failed",
                error_code="publication_discovery_timeout",
                error_detail=(
                    f"Publication discovery exceeded {PUBLICATION_DISCOVERY_WALL_TIMEOUT_S}s; "
                    "guardian continued and will retry automatically."
                ),
            )
        except Exception as exc:
            publication_discovery_result = {"error": f"{type(exc).__name__}: {exc}"}
            marketplace.record_attempt(
                platform="publication_discovery",
                action="discover",
                status_value="failed",
                error_code="publication_discovery_exception",
                error_detail=publication_discovery_result["error"],
            )

    dynamic_candidate_rows = {
        str(x.get("key") or ""): x
        for x in forum_discovery.list_candidates(min_score=8)
        if str(x.get("key") or "")
    }
    dynamic_keys = set(dynamic_candidate_rows)
    # Publication Discovery is a separate strong-domain pool (catalogs,
    # classifieds, author pages). Include it in the same bounded audit queue;
    # otherwise high/medium IKS non-forum candidates are discovered but never
    # progress beyond stale REVIEW snapshots.
    publication_candidate_rows = {
        str(p.key): p
        for p in marketplace._publication_discovered_platform_objects()
    }
    publication_keys = set(publication_candidate_rows)

    def _audit_priority(key: str):
        row = platform_rules.latest(key)
        base = marketplace.FREE_PLATFORM_POLICY.get(key, {})
        platform = marketplace.get_platform(key)
        url = getattr(platform, "url", "") if platform else ""
        quality_tier, iks = forum_quality.priority_for_url(url)
        # A verified/exact commercial surface is much closer to becoming usable
        # Crowd SEO inventory than a generic forum root. Audit those first,
        # while preserving the same fail-closed rule gate.
        has_exact_surface = bool(
            marketplace.VERIFIED_PUBLICATION_SURFACES.get(key)
            or marketplace._dynamic_verified_publication_surface(
                dynamic_candidate_rows.get(key) or {}
            )
        )
        surface_group = 0 if has_exact_surface else 1
        # High/medium IKS surfaces are strategically more valuable, but only
        # after the same fail-closed rule audit. New candidates still go first
        # inside their quality tier so discovery does not starve.
        freshness_group = 0
        if (key in dynamic_keys or key in publication_keys) and not row:
            freshness_group = 0
        elif base.get("reason") == "rule_audit_required" and not row:
            freshness_group = 1
        elif not row:
            freshness_group = 2
        else:
            freshness_group = 3
        return (surface_group, -quality_tier, -iks, freshness_group, str((row or {}).get("checked_at") or ""), key)

    candidates = sorted(
        [p.key for p in marketplace.all_platform_objects() if needs_audit(p.key)],
        key=_audit_priority,
    )
    # Do not wait for the broad forum discovery backoff to inspect newly found
    # strong non-forum publication domains. Their own rule audit is cheap and
    # is the prerequisite for any safe catalog/listing attempt.
    for key in sorted(publication_keys):
        row = platform_rules.latest(key)
        needs_exact_authoring_recheck = bool(
            row
            and row.get("decision") == "allowed"
            and "exact_same_domain_authoring_surface_required" not in (row.get("requirements") or [])
        )
        if key not in candidates and (needs_audit(key) or needs_exact_authoring_recheck):
            candidates.append(key)
    candidates.sort(key=_audit_priority)
    audited = []
    failed = []
    total_reserve_deficit = int(strategic_reserve_before.get("total_deficit") or 0)
    if total_reserve_deficit > 100:
        audit_budget = 60
    elif total_reserve_deficit > 0:
        audit_budget = 40
    elif combined_discovery_deficit > 0:
        audit_budget = 24
    else:
        audit_budget = MAX_AUDITS_PER_RUN
    # Large reserve deficits increase how many cycles are needed, not the
    # duration of a single lock-holding cycle. This prevents Guardian from
    # starving the dedicated onboarding worker.
    audit_budget = min(audit_budget, MAX_AUDITS_PER_RUN)
    for key in candidates[:audit_budget]:
        try:
            row = platform_rules.inspect_platform(key)
            audited.append({"platform": key, "decision": row.get("decision"), "errors": row.get("errors", [])})
            if key in dynamic_keys:
                platform = marketplace.get_platform(key)
                domain = urlparse(str(getattr(platform, "url", "") or "")).netloc.lower().removeprefix("www.") if platform else ""
                terminal_reason = _audit_terminal_dead_reason(row)
                if domain and terminal_reason:
                    forum_discovery.mark_terminal_domain(
                        domain,
                        terminal_reason,
                        source=key,
                        evidence="fresh platform_rules audit confirmed dead/parked endpoint",
                    )
                elif domain and row.get("decision") == "blocked":
                    forum_discovery.mark_terminal_domain(
                        domain,
                        "rules_blocked",
                        source=key,
                        evidence="fresh platform_rules decision=blocked",
                    )
        except Exception as exc:
            failed.append({"platform": key, "error": f"{type(exc).__name__}: {exc}"})
            marketplace.record_attempt(
                platform=key,
                action="rules_audit",
                status_value="failed",
                error_code="rules_audit_exception",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
        time.sleep(0.35)

    # Registration preflight is discovery-only: fill/inspect public forms but
    # never submit an external account. This removes manual diagnosis while
    # respecting CAPTCHA, verification and identity requirements.
    registration_preflights = []
    reg_state = {x["platform"]: x for x in marketplace.registration_plan()}
    free_forums = [
        p for p in marketplace.list_platforms()
        if p.get("enabled_for_outreach")
        and (
            p.get("channel_type") == "forum"
            or bool(p.get("publication_surfaces"))
        )
    ]
    now = datetime.now(timezone.utc)
    preflight_candidates = [
        p for p in free_forums
        if _registration_retry_due(reg_state.get(p["key"]), now)
    ]
    assistant_script = "/root/BORIS/backend/tools/marketplace_browser_assistant.py"
    for p in preflight_candidates[:MAX_REGISTRATION_PREFLIGHTS_PER_RUN]:
        key = p["key"]
        try:
            proc = subprocess.run(
                [sys.executable, assistant_script, "register", "--platform", key],
                cwd="/root/BORIS/backend",
                capture_output=True,
                text=True,
                timeout=65,
                check=False,
            )
            payload = None
            try:
                payload = json.loads((proc.stdout or "").strip()) if proc.stdout.strip() else None
            except Exception:
                payload = None
            registration_preflights.append({
                "platform": key,
                "returncode": proc.returncode,
                "checkpoint": (payload or {}).get("checkpoint"),
                "submitted": bool((payload or {}).get("submitted")),
            })
            if proc.returncode != 0:
                marketplace.record_attempt(
                    platform=key,
                    action="registration_preflight",
                    status_value="failed",
                    error_code="registration_preflight_failed",
                    error_detail=((proc.stderr or proc.stdout or "")[-1000:] or "registration preflight failed"),
                )
        except subprocess.TimeoutExpired:
            registration_preflights.append({"platform": key, "returncode": 124, "checkpoint": "preflight_timeout", "submitted": False})
            marketplace.record_attempt(
                platform=key,
                action="registration_preflight",
                status_value="failed",
                error_code="registration_preflight_timeout",
                error_detail="Предварительная проверка регистрации превысила безопасный лимит времени.",
            )
        time.sleep(0.2)

    # Safe account creation: only forms that preflight as plain
    # username+email+password with no CAPTCHA, terms, identity or consent fields.
    # Re-run preflight immediately before submit so a changed page fails closed.
    registration_submissions = []
    reg_state = {x["platform"]: x for x in marketplace.registration_plan()}
    submit_candidates = [
        p for p in free_forums
        if (reg_state.get(p["key"], {}).get("status") == "in_progress"
            and reg_state.get(p["key"], {}).get("checkpoint") == "form_prepared")
    ]
    for p in submit_candidates[:MAX_REGISTRATION_SUBMITS_PER_RUN]:
        key = p["key"]
        probe_payload = None
        submit_payload = None
        try:
            probe = subprocess.run(
                [sys.executable, assistant_script, "register", "--platform", key],
                cwd="/root/BORIS/backend",
                capture_output=True,
                text=True,
                timeout=65,
                check=False,
            )
            try:
                probe_payload = json.loads((probe.stdout or "").strip()) if (probe.stdout or "").strip() else None
            except Exception:
                probe_payload = None
            if probe.returncode != 0 or not _preflight_allows_auto_submit(probe_payload, platform=key):
                registration_submissions.append({
                    "platform": key,
                    "attempted": False,
                    "reason": (probe_payload or {}).get("checkpoint") or "preflight_not_safe_for_auto_submit",
                })
                continue

            submitted = subprocess.run(
                [sys.executable, assistant_script, "register", "--platform", key, "--submit"],
                cwd="/root/BORIS/backend",
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            try:
                submit_payload = json.loads((submitted.stdout or "").strip()) if (submitted.stdout or "").strip() else None
            except Exception:
                submit_payload = None
            registration_submissions.append({
                "platform": key,
                "attempted": True,
                "returncode": submitted.returncode,
                "submitted": bool((submit_payload or {}).get("submitted")),
                "checkpoint": (submit_payload or {}).get("checkpoint"),
            })
            if submitted.returncode != 0:
                marketplace.record_attempt(
                    platform=key,
                    action="registration_submit",
                    status_value="failed",
                    error_code="registration_submit_failed",
                    error_detail=((submitted.stderr or submitted.stdout or "")[-1000:] or "registration submit failed"),
                )
            elif _registration_submit_needs_immediate_verify(submit_payload):
                verify = subprocess.run(
                    [sys.executable, assistant_script, "verify-registration", "--platform", key],
                    cwd="/root/BORIS/backend",
                    capture_output=True,
                    text=True,
                    timeout=65,
                    check=False,
                )
                verify_payload = None
                try:
                    verify_payload = json.loads((verify.stdout or "").strip()) if (verify.stdout or "").strip() else None
                except Exception:
                    verify_payload = None
                registration_submissions[-1]["verification"] = {
                    "returncode": verify.returncode,
                    "status": (verify_payload or {}).get("status"),
                    "checkpoint": (verify_payload or {}).get("checkpoint"),
                    "ok": bool((verify_payload or {}).get("ok")),
                }
        except subprocess.TimeoutExpired:
            registration_submissions.append({
                "platform": key,
                "attempted": True,
                "returncode": 124,
                "checkpoint": "registration_submit_timeout",
            })
            marketplace.record_attempt(
                platform=key,
                action="registration_submit",
                status_value="failed",
                error_code="registration_submit_timeout",
                error_detail="Создание аккаунта превысило безопасный лимит времени.",
            )

    # Existing accounts with an unclear result are verified instead of creating
    # another account. This avoids duplicate registrations and anti-spam loops.
    registration_verifications = []
    reg_state = {x["platform"]: x for x in marketplace.registration_plan()}
    verification_candidates = [
        p for p in free_forums
        if _registration_verification_due(reg_state.get(p["key"]), datetime.now(timezone.utc))
    ]
    verification_candidates.sort(
        key=lambda p: _parse_iso((reg_state.get(p["key"]) or {}).get("updated_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    for p in verification_candidates[:MAX_REGISTRATION_VERIFICATIONS_PER_RUN]:
        key = p["key"]
        try:
            proc = subprocess.run(
                [sys.executable, assistant_script, "verify-registration", "--platform", key],
                cwd="/root/BORIS/backend",
                capture_output=True,
                text=True,
                timeout=65,
                check=False,
            )
            payload = None
            try:
                payload = json.loads((proc.stdout or "").strip()) if (proc.stdout or "").strip() else None
            except Exception:
                payload = None
            registration_verifications.append({
                "platform": key,
                "returncode": proc.returncode,
                "status": (payload or {}).get("status"),
                "checkpoint": (payload or {}).get("checkpoint"),
                "ok": bool((payload or {}).get("ok")),
            })
        except subprocess.TimeoutExpired:
            registration_verifications.append({
                "platform": key,
                "returncode": 124,
                "status": "in_progress",
                "checkpoint": "verification_timeout",
                "ok": False,
            })

    purge = marketplace.purge_nonfree_pending_drafts()
    drafts = []
    for core_topic in ("virtual_department", "development"):
        generated = marketplace.generate_drafts(platform="all", topic=core_topic, variants=2)
        # Publication Discovery is permanently bound to custom software
        # development. Never let generic BORIS/product copy enter this queue.
        drafts.extend(d for d in generated if str(d.get("platform") or "") not in publication_keys)
    drafts.extend(_publication_development_drafts(publication_keys))

    publication_drafts_repaired = 0
    for old in list(marketplace.list_drafts()):
        if _publication_draft_needs_repair(old, publication_keys):
            marketplace.set_draft_status(old["id"], "rejected")
            publication_drafts_repaired += 1

    # Remove stale pending core drafts from platforms that are no longer in the
    # current free/relevant campaign (duplicate discovery domains, events,
    # blocked registrations, deliberately excluded narrow communities, etc.).
    desired_ids = {d["id"] for d in drafts}
    stale_core = 0
    for old in list(marketplace.list_drafts()):
        if (
            old.get("topic") in {"virtual_department", "development"}
            and old.get("status") == "needs_owner_approval"
            and old.get("id") not in desired_ids
        ):
            marketplace.set_draft_status(old["id"], "rejected")
            stale_core += 1

    rows = marketplace.save_drafts(drafts)

    # Client Crowd SEO is serviced by the same guardian. The client supplies
    # only the site; BORIS validates generated content, deduplicates retries and
    # moves safe projects forward without an owner approval click.
    crowd_cycle = run_crowd_seo_cycle(assistant_script)
    capacity_after = _crowd_capacity_snapshot()
    bootstrap_state = marketplace.platform_bootstrap_queue()
    active_project_bootstrap = _active_project_bootstrap_snapshot(bootstrap_state)
    warmup_state = marketplace.platform_warmup_queue()
    longterm_discovery_deficit = max((
        int(x.get("discovery_deficit_after_warmup", x.get("discovery_deficit_after_bootstrap") or 0) or 0)
        for x in capacity_after
    ), default=0)
    strategic_reserve_after = _strategic_reserve_snapshot()

    daily_execution = _daily_execution_snapshot(datetime.now(timezone.utc), mark_run=True)
    summary = {
        "at": datetime.now(timezone.utc).isoformat(),
        "daily_execution": daily_execution,
        "audited": audited,
        "failed": failed,
        "registration_preflights": registration_preflights,
        "registration_submissions": registration_submissions,
        "registration_verifications": registration_verifications,
        "purged_nonfree_pending": purge.get("removed", 0),
        "purged_stale_core_pending": stale_core,
        "publication_drafts_repaired": publication_drafts_repaired,
        "generated_or_refreshed": len(drafts),
        "drafts_total": len(rows),
        "free_platforms": [p["key"] for p in marketplace.list_platforms() if p.get("enabled_for_outreach")],
        "remaining_audits": max(0, len(candidates) - audit_budget),
        "discovery": discovery_result or forum_discovery.status(),
        "publication_discovery": publication_discovery_result or publication_discovery.load(),
        "publication_discovery_schedule": publication_discovery_schedule,
        "auto_discovered_candidates": len(forum_discovery.list_candidates()),
        "terminal_reactivated": terminal_reactivated,
        "project_reconcile": project_reconcile,
        "platform_bootstrap": bootstrap_state,
        "active_project_bootstrap": active_project_bootstrap,
        "platform_warmup": warmup_state,
        "crowd_capacity": {
            "before": capacity_before,
            "placement_deficit_before": placement_deficit,
            "combined_discovery_deficit_before": combined_discovery_deficit,
            "after": capacity_after,
            "discovery_forced_by_deficit": bool(discovery_schedule.get("forced_due") and discovery_result is not None),
            "discovery_schedule": discovery_schedule,
            "max_autonomous_deficit_before": autonomous_deficit,
            "max_discovery_deficit_after_bootstrap_before": discovery_deficit,
            "max_discovery_deficit_after_warmup_after": longterm_discovery_deficit,
            "strategic_reserve_before": strategic_reserve_before,
            "strategic_reserve_after": strategic_reserve_after,
            "strategic_reserve_deficit_before": strategic_reserve_deficit,
            "combined_discovery_deficit_before": combined_discovery_deficit,
        },
        "crowd_seo": crowd_cycle,
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    # Individual forum failures are recorded in the attempt ledger and retried on the next cycle;
    # they must not disable the whole guardian.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
