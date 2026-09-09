import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import service_marketplace as svc
from app.services import crowd_seo
from app.services import platform_rules
from app.services import forum_discovery
from app.services import forum_quality
from app.services import crowd_seo_reserve_policy as reserve_policy

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ASSISTANT_SCRIPT = _BACKEND_ROOT / "tools" / "marketplace_browser_assistant.py"

router = APIRouter(prefix="/api/prospecting/service-marketplace", tags=["service-marketplace"])

class GenerateRequest(BaseModel):
    platform: str = "all"
    topic: str = "all"
    variants: int = Field(default=3, ge=1, le=20)

class DecisionRequest(BaseModel):
    decision: str

class PostedRequest(BaseModel):
    url: str

@router.get("/platforms")
def platforms():
    return {"items": svc.list_platforms()}

@router.get("/offers")
def offers():
    return {"items": svc.list_offers()}

@router.get("/drafts")
def drafts(status: str | None = None):
    return {"items": svc.list_drafts(status=status)}

@router.post("/drafts/generate")
def generate(req: GenerateRequest):
    try:
        drafts = svc.generate_drafts(req.platform, req.topic, req.variants)
        rows = svc.save_drafts(drafts)
        return {"generated": len(drafts), "total": len(rows), "items": drafts}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/drafts/{draft_id}/decision")
def decision(draft_id: str, req: DecisionRequest):
    decision = req.decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    try:
        return svc.set_draft_status(draft_id, decision)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found")

@router.post("/drafts/{draft_id}/posted")
def posted(draft_id: str, req: PostedRequest):
    try:
        return svc.mark_posted(draft_id, req.url)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/status")
def status():
    return svc.status()

class RegistrationUpdateRequest(BaseModel):
    status: str
    email: str | None = None
    account_url: str | None = None
    checkpoint: str | None = None
    last_error: str | None = None

@router.get("/registrations")
def registrations(email: str | None = None):
    return {"items": svc.registration_plan(email=email)}


_MULTIPART_SUFFIXES = {
    "com.ua", "net.ua", "org.ua",
    "co.uk", "org.uk",
    "com.au", "com.br", "co.nz",
}


def _domain(url: str | None) -> str:
    raw = str(url or "").strip().lower()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else "//" + raw)
        host = (parsed.hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    tail = ".".join(parts[-2:])
    if tail in _MULTIPART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail


def _inventory_summary() -> dict:
    platforms = svc.list_platforms()
    registrations = {x.get("platform"): x for x in svc.registration_plan()}
    forums = [x for x in platforms if x.get("channel_type") == "forum"]
    unique_forum_domains = {
        _domain(x.get("url")) for x in forums if _domain(x.get("url"))
    }
    discovery_domains = {
        _domain(x.get("domain") or x.get("url"))
        for x in forum_discovery.list_candidates()
        if _domain(x.get("domain") or x.get("url"))
    }
    terminal_discovery_domains = {
        _domain(domain)
        for domain in forum_discovery.terminal_domain_map().keys()
        if _domain(domain)
    }
    seen_or_checked_domains = (
        unique_forum_domains | discovery_domains | terminal_discovery_domains
    )

    rule_counts = {"allowed": 0, "review": 0, "blocked": 0, "reply_only": 0, "none": 0}
    terminal_registration = 0
    for item in forums:
        decision = str((platform_rules.latest(str(item.get("key") or "")) or {}).get("decision") or "none")
        if decision not in rule_counts:
            rule_counts[decision] = 0
        rule_counts[decision] += 1
        registration = registrations.get(item.get("key"), {})
        if svc.registration_is_terminally_blocked(registration):
            terminal_registration += 1

    bootstrap = svc.platform_bootstrap_queue()
    warmup = svc.platform_warmup_queue()

    coverage_known: dict[str, set[str]] = {}
    coverage_allowed: dict[str, set[str]] = {}
    sellable_format_sites: dict[str, set[str]] = {
        fmt: set() for fmt in reserve_policy.SUPPORTED_FORMATS
    }
    for item in forums:
        key = str(item.get("key") or "")
        surfaces = item.get("publication_surfaces") or []
        niches: set[str] = set()
        for surface in surfaces:
            niches.update(str(x) for x in (surface.get("niches") or []) if x)
        if not niches:
            niches.add(str(item.get("niche") or "unknown"))
        for niche in niches:
            coverage_known.setdefault(niche, set()).add(key)
            if (
                item.get("enabled_for_outreach")
                and reserve_policy.rule_counts_for_reserve(platform_rules.latest(key))
            ):
                coverage_allowed.setdefault(niche, set()).add(key)
                if (
                    niche in sellable_format_sites
                    and not svc.registration_is_terminally_blocked(registrations.get(key) or {})
                ):
                    site = _domain(item.get("url"))
                    if site:
                        sellable_format_sites[niche].add(site)

    coverage_by_niche = {
        niche: {
            "known": len(coverage_known.get(niche, set())),
            "allowed_zero_cost": len(coverage_allowed.get(niche, set())),
        }
        for niche in sorted(set(coverage_known) | set(coverage_allowed))
    }

    active_format_targets = {fmt: 0 for fmt in reserve_policy.SUPPORTED_FORMATS}
    for project in crowd_seo.list_projects():
        if not crowd_seo.project_is_active(project):
            continue
        if str(project.get("plan_mode") or "") == "inventory":
            continue
        required = crowd_seo._project_required_count(project)
        offer_type = crowd_seo._legacy_offer_type(project)
        formats = reserve_policy.SUPPORTED_FORMATS if offer_type == "mixed" else (offer_type,)
        for fmt in formats:
            if fmt in active_format_targets:
                active_format_targets[fmt] = max(active_format_targets[fmt], required)

    sellable_product_formats = {
        fmt: {
            "target": int(active_format_targets.get(fmt) or 0),
            "allowed_unique_sites": len(sellable_format_sites.get(fmt, set())),
            "deficit": max(
                0,
                int(active_format_targets.get(fmt) or 0)
                - len(sellable_format_sites.get(fmt, set())),
            ),
            "sellable": (
                int(active_format_targets.get(fmt) or 0) == 0
                or len(sellable_format_sites.get(fmt, set()))
                >= int(active_format_targets.get(fmt) or 0)
            ),
            "target_source": "active_client_orders",
        }
        for fmt in reserve_policy.SUPPORTED_FORMATS
    }

    project_rows = []
    relevant_platforms: set[str] = set()
    for project in crowd_seo.list_projects():
        if not crowd_seo.project_is_active(project):
            continue
        matches = crowd_seo._forum_matches(
            project.get("niche") or "business",
            100,
            project.get("keywords") or [],
            offer_type=str(project.get("offer_type") or "mixed"),
        )
        relevant_platforms.update(
            str(x.get("platform") or "") for x in matches if x.get("platform")
        )
        project_rows.append({
            "project": project.get("id"),
            "site": project.get("site"),
            **crowd_seo._capacity_for_project(project, matches),
        })

    current_relevant = max(
        (int(x.get("eligible") or 0) for x in project_rows),
        default=0,
    )
    current_after_bootstrap = max(
        (int(x.get("service_reachable_after_bootstrap") or x.get("service_reachable") or 0) for x in project_rows),
        default=0,
    )
    current_after_warmup = max(
        (int(x.get("service_reachable_after_warmup") or x.get("service_reachable") or 0) for x in project_rows),
        default=0,
    )
    current_deficit_after_bootstrap = max(
        (int(x.get("discovery_deficit_after_bootstrap") or 0) for x in project_rows),
        default=0,
    )
    current_deficit_after_warmup = max(
        (
            int(
                x.get("discovery_deficit_after_warmup")
                if x.get("discovery_deficit_after_warmup") is not None
                else x.get("discovery_deficit_after_bootstrap") or 0
            )
            for x in project_rows
        ),
        default=0,
    )
    current_required = max(
        (int(x.get("required") or 0) for x in project_rows),
        default=0,
    )

    quality = forum_quality.load()
    quality_by_domain = {
        str(x.get("domain") or ""): x
        for x in (quality.get("items") or [])
        if x.get("domain")
    }
    allowed_quality_domains: dict[str, dict] = {}
    for item in forums:
        key = str(item.get("key") or "")
        if not item.get("enabled_for_outreach"):
            continue
        if (platform_rules.latest(key) or {}).get("decision") != "allowed":
            continue
        if not svc.platform_policy(key).get("free"):
            continue
        domain = forum_quality.domain_from_url(item.get("url"))
        q = quality_by_domain.get(domain) or {}
        if q.get("tier") not in {"high", "medium"}:
            continue
        current = allowed_quality_domains.get(domain)
        if current is None or int(q.get("iks") or 0) > int(current.get("iks") or 0):
            allowed_quality_domains[domain] = {
                "domain": domain,
                "iks": q.get("iks"),
                "tier": q.get("tier"),
                "platform": key,
                "name": item.get("name"),
            }
    quality_top_allowed = sorted(
        allowed_quality_domains.values(),
        key=lambda x: (-(int(x.get("iks") or 0)), str(x.get("domain") or "")),
    )[:40]

    return {
        "platform_records_total": len(platforms),
        "forum_records_total": len(forums),
        "unique_forum_sites": len(unique_forum_domains),
        "unique_sites_seen_or_checked": len(seen_or_checked_domains),
        "discovery_unique_sites": len(discovery_domains),
        "terminal_discovery_sites": len(terminal_discovery_domains),
        "terminal_only_not_registry": len(terminal_discovery_domains - unique_forum_domains),
        "rules_allowed": int(rule_counts.get("allowed") or 0),
        "rules_reserve_verified": sum(
            1
            for item in forums
            if item.get("enabled_for_outreach")
            and reserve_policy.rule_counts_for_reserve(
                platform_rules.latest(str(item.get("key") or ""))
            )
        ),
        "rules_review": int(rule_counts.get("review") or 0),
        "rules_blocked": int(rule_counts.get("blocked") or 0),
        "rules_reply_only": int(rule_counts.get("reply_only") or 0),
        "publication_ready": sum(1 for x in forums if x.get("publication_ready")),
        "bootstrap_queue": int(bootstrap.get("needed") or 0),
        "warming_accounts": int(warmup.get("warming") or 0),
        "terminal_registration_blocked": terminal_registration,
        "coverage_by_niche": coverage_by_niche,
        "sellable_product_formats": sellable_product_formats,
        "sellable_product_complete": all(
            row.get("sellable") for row in sellable_product_formats.values()
        ),
        "sellable_product_total_deficit": sum(
            int(row.get("deficit") or 0)
            for row in sellable_product_formats.values()
        ),
        "active_crowd_projects": len(project_rows),
        "current_required": current_required,
        "current_relevant_sites": current_relevant,
        "current_relevant_after_bootstrap": current_after_bootstrap,
        "current_relevant_after_warmup": current_after_warmup,
        "current_missing_after_bootstrap": current_deficit_after_bootstrap,
        "current_missing_after_warmup": current_deficit_after_warmup,
        "strict_relevant_platforms": sorted(relevant_platforms),
        "quality_measured_at": quality.get("measured_at"),
        "quality_domains_total": int(quality.get("domains_total") or 0),
        "quality_high_sites": int(quality.get("high") or 0),
        "quality_medium_sites": int(quality.get("medium") or 0),
        "quality_high_medium_sites": int(quality.get("high") or 0) + int(quality.get("medium") or 0),
        "quality_allowed_high_medium_sites": len(allowed_quality_domains),
        "quality_top_allowed": quality_top_allowed,
        "projects": project_rows,
    }


@router.get("/inventory-summary")
def inventory_summary():
    return _inventory_summary()


@router.get("/warmup-queue")
def warmup_queue():
    return svc.platform_warmup_queue()


def _prioritized_bootstrap_queue() -> dict:
    queue = svc.platform_bootstrap_queue()
    try:
        priority = crowd_seo.bootstrap_priority_snapshot()
    except Exception:
        priority = {}

    items = []
    for item in queue.get("items", []):
        extra = priority.get(str(item.get("platform") or ""), {})
        items.append({
            **item,
            "urgent_for_active_projects": bool(extra.get("urgent_for_active_projects")),
            "relevant_projects": int(extra.get("relevant_projects") or 0),
            "potential_slots": int(extra.get("potential_slots") or 0),
            "max_relevance": int(extra.get("max_relevance") or 0),
            "requires_warmup_after_bootstrap": bool(extra.get("requires_warmup_after_bootstrap")),
            "relevant_sites": list(extra.get("relevant_sites") or []),
        })

    checkpoint_effort = {
        "terms_acceptance_required": 0,
        "registration_agreement_required": 0,
        "age_and_terms_declaration_required": 1,
        "captcha_required": 2,
        "captcha_age_and_terms_required": 3,
        "sms_or_verification_code_required": 4,
        "email_verification_required": 4,
    }
    items.sort(
        key=lambda x: (
            not bool(x.get("urgent_for_active_projects")),
            bool(x.get("requires_warmup_after_bootstrap")),
            checkpoint_effort.get(str(x.get("checkpoint") or ""), 5),
            -int(x.get("max_relevance") or 0),
            -int(x.get("potential_slots") or 0),
            int(x.get("priority") or 9999),
            str(x.get("name") or ""),
        )
    )
    queue["items"] = items
    queue["needed"] = len(items)
    queue["needed_for_active_projects"] = sum(
        1 for x in items if x.get("urgent_for_active_projects")
    )
    queue["potential_slots_for_active_projects"] = sum(
        int(x.get("potential_slots") or 0)
        for x in items
        if x.get("urgent_for_active_projects")
    )
    queue["potential_slots_immediate_after_bootstrap"] = sum(
        int(x.get("potential_slots") or 0)
        for x in items
        if x.get("urgent_for_active_projects")
        and not x.get("requires_warmup_after_bootstrap")
    )
    queue["potential_slots_after_warmup"] = sum(
        int(x.get("potential_slots") or 0)
        for x in items
        if x.get("urgent_for_active_projects")
    )
    queue["warming_after_bootstrap"] = sum(
        1 for x in items
        if x.get("urgent_for_active_projects")
        and x.get("requires_warmup_after_bootstrap")
    )
    return queue


@router.get("/bootstrap-queue")
def bootstrap_queue():
    return _prioritized_bootstrap_queue()

@router.post("/bootstrap-queue/{platform}/verify")
def verify_bootstrap(platform: str):
    queue = svc.platform_bootstrap_queue()
    item = next((x for x in queue.get("items", []) if x.get("platform") == platform), None)
    if item is None:
        current = next((x for x in svc.list_platforms() if x.get("key") == platform), None)
        if current and current.get("publication_ready"):
            return {
                "ok": True,
                "platform": platform,
                "ready": True,
                "checkpoint": current.get("registration_checkpoint"),
                "message": "Площадка уже READY.",
                "queue": queue,
            }
        raise HTTPException(status_code=404, detail="Площадка не находится в служебной очереди подготовки.")

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(_ASSISTANT_SCRIPT),
                "verify-registration",
                "--platform",
                platform,
            ],
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
            timeout=70,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Проверка площадки превысила безопасный лимит времени.") from exc

    payload = None
    raw = (proc.stdout or "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None

    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "")[-1200:] or "verify-registration failed").strip()
        raise HTTPException(status_code=502, detail=detail)

    current = next((x for x in svc.list_platforms() if x.get("key") == platform), None) or {}
    next_queue = _prioritized_bootstrap_queue()
    ready = bool(current.get("publication_ready"))
    return {
        "ok": True,
        "platform": platform,
        "ready": ready,
        "checkpoint": current.get("registration_checkpoint"),
        "verification": payload,
        "message": (
            "Площадка READY. BORIS может использовать аккаунт для всех клиентских проектов."
            if ready
            else "Аккаунт ещё не подтверждён как READY. Одноразовая подготовка остаётся в очереди."
        ),
        "queue": next_queue,
    }

@router.post("/registrations/{platform}")
def update_registration(platform: str, req: RegistrationUpdateRequest):
    try:
        return svc.upsert_registration(
            platform,
            req.status,
            email=req.email,
            account_url=req.account_url,
            checkpoint=req.checkpoint,
            last_error=req.last_error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/publications")
def publications():
    return {"items": svc.list_publications()}

@router.get("/attempts")
def attempts(limit: int = 200):
    return {"items": svc.list_attempts(limit=limit)}
