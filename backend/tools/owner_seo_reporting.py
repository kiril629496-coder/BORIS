#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import crowd_seo
from app.services import service_marketplace as marketplace

DATA = ROOT / "data" / "service_marketplaces"
CERT = DATA / "crowd_seo_certificate_latest.json"
STATUS = DATA / "owner_seo_status_latest.json"
HUMAN = DATA / "owner_seo_human_queue_latest.json"
OWNER_MD = DATA / "owner_seo_status_latest.md"
INDEXNOW_STATE = DATA / "indexnow_state.json"

OWNER_DOMAINS = (
    "avito-specialist.ru",
    "boris-ai.pro",
    "systems-ai-oko.ru",
)

LOST_STATUSES = {
    "replacement_required",
    "guarantee_expired",
    "lost",
}
PREPUBLICATION_REPLACEMENT_STATUSES = {
    "platform_replacement_required",
}
PENDING_STATUSES = {
    "publication_pending_verification",
    "published_unverified",
    "verification_temporarily_blocked",
}


def _host(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return (urlparse(raw).hostname or "").lower().removeprefix("www.")


def _http_live(value) -> bool:
    try:
        code = int(value)
    except Exception:
        return False
    return 200 <= code < 400


def _donor(placement: dict) -> str:
    return _host(
        placement.get("final_publication_url")
        or placement.get("publication_url")
        or placement.get("forum_url")
    )


def _publication_record(placement: dict) -> dict:
    return {
        "placement_id": placement.get("id"),
        "donor_domain": _donor(placement),
        "publication_url": placement.get("final_publication_url") or placement.get("publication_url"),
        "target_url": placement.get("target_url"),
        "http_status": placement.get("http_status"),
        "link_present": placement.get("link_present"),
        "indexable": placement.get("indexable"),
        "rel": placement.get("rel"),
        "anchor": placement.get("anchor"),
        "link_mode": placement.get("link_mode"),
        "checked_at": placement.get("checked_at"),
        "first_seen_at": placement.get("published_at") or placement.get("publication_marked_at"),
        "last_verified_at": placement.get("verified_at") or placement.get("checked_at"),
        "iks": placement.get("iks"),
        "iks_tier": placement.get("iks_tier"),
        "platform": placement.get("platform"),
        "surface_id": placement.get("surface_id"),
        "status": placement.get("status"),
    }


def _live(placement: dict) -> bool:
    return bool(
        placement.get("publication_url")
        and placement.get("link_present") is True
        and _http_live(placement.get("http_status"))
    )


def _verified(placement: dict) -> bool:
    return bool(
        _live(placement)
        and placement.get("indexable") is True
        and str(placement.get("status") or "") == "verified"
    )


def _cert_projects() -> dict[str, dict]:
    if not CERT.exists():
        return {}
    try:
        payload = json.loads(CERT.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        _host(row.get("site")): row
        for row in (payload.get("projects") or [])
        if _host(row.get("site"))
    }


def _project_platform_map(projects: dict[str, dict]) -> tuple[dict[str, set[str]], dict[str, dict]]:
    use: dict[str, set[str]] = {}
    quality: dict[str, dict] = {}
    for domain, project in projects.items():
        rows = list(project.get("forum_candidates") or []) + list(project.get("placements") or [])
        for row in rows:
            platform = str(row.get("platform") or "").strip()
            if not platform:
                continue
            use.setdefault(platform, set()).add(domain)
            q = quality.setdefault(platform, {"iks": 0, "iks_tier": None, "name": None, "url": None})
            try:
                iks = int(row.get("iks") or 0)
            except Exception:
                iks = 0
            if iks >= int(q.get("iks") or 0):
                q["iks"] = iks
                q["iks_tier"] = row.get("iks_tier")
                q["name"] = row.get("platform_name") or row.get("name") or q.get("name")
                q["url"] = row.get("forum_url") or row.get("url") or q.get("url")
    return use, quality


def _human_queue(projects: dict[str, dict]) -> dict:
    bootstrap = marketplace.platform_bootstrap_queue()
    raw_items = list(bootstrap.get("items") or bootstrap.get("platforms") or [])
    use, quality = _project_platform_map(projects)
    platform_rows = {str(x.get("key") or ""): x for x in marketplace.list_platforms()}
    items = []
    for row in raw_items:
        platform = str(row.get("platform") or "").strip()
        target_sites = sorted(use.get(platform) or [])
        if not target_sites:
            continue
        q = quality.get(platform) or {}
        p = platform_rows.get(platform) or {}
        account_url = row.get("account_url") or row.get("url")
        checkpoint = str(row.get("checkpoint") or "")
        friction_penalty = {
            "math_antibot_required": 10,
            "captcha_required": 15,
            "captcha_terms_registration_required": 35,
            "terms_acceptance_required": 25,
            "registration_agreement_required": 25,
            "external_account_sso_required": 30,
            "business_email_required": 30,
            "truthful_company_identity_required": 35,
            "age_declaration_required": 40,
            "captcha_age_declaration_required": 45,
            "age_and_terms_declaration_required": 45,
            "captcha_age_and_terms_required": 50,
            "registration_form_not_found": 60,
        }.get(checkpoint, 20)
        try:
            iks_value = int(q.get("iks") or 0)
        except Exception:
            iks_value = 0
        try:
            acquisition_value = int(p.get("acquisition_score") or 0)
        except Exception:
            acquisition_value = 0
        tier = str(q.get("iks_tier") or "unknown").lower()
        quality_bonus = {
            "high": 80,
            "medium": 40,
            "low": -20,
            "unknown": -60,
        }.get(tier, -60)
        human_effort_score = int(
            len(target_sites) * 100
            + min(max(iks_value, 0), 5000) / 10
            + acquisition_value
            + quality_bonus
            - friction_penalty
        )
        items.append({
            "state": "HUMAN_PENDING",
            "platform": platform,
            "name": row.get("name") or q.get("name") or platform,
            "why_needed": "Открывает повторно используемый SEO-донор для активных owner SEO contracts.",
            "potential_backlink_slots": len(target_sites),
            "target_sites": target_sites,
            "iks": q.get("iks"),
            "iks_tier": q.get("iks_tier"),
            "acquisition_score": p.get("acquisition_score"),
            "human_effort_score": human_effort_score,
            "checkpoint": row.get("checkpoint"),
            "action": row.get("action"),
            "action_url": account_url,
            "one_time": bool(row.get("one_time", True)),
            "reusable_across_clients": bool(row.get("reusable_across_clients")),
            "auto_resume_after_verification": bool(row.get("auto_resume_after_verification")),
            "after_completion": row.get("after_completion"),
            "priority": row.get("priority"),
        })
    items.sort(key=lambda x: (
        -(int(x.get("human_effort_score") or 0)),
        -len(x.get("target_sites") or []),
        -(int(x.get("iks") or 0)),
        -(int(x.get("acquisition_score") or 0)),
        int(x.get("priority") or 999999),
        str(x.get("platform") or ""),
    ))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": "HUMAN_PENDING",
        "count": len(items),
        "reusable_across_clients": bool(bootstrap.get("reusable_across_clients")),
        "by_checkpoint": dict(bootstrap.get("by_checkpoint") or {}),
        "top_5_now": [
            {
                key: item.get(key)
                for key in (
                    "platform", "name", "target_sites", "iks", "iks_tier",
                    "human_effort_score", "checkpoint", "action", "action_url",
                    "after_completion",
                )
            }
            for item in items[:5]
        ],
        "items": items,
    }


def _attempt_daily_snapshot(cert_payload: dict, projects: dict[str, dict]) -> dict:
    today = datetime.now(timezone.utc).date()
    attempted = []
    for row in marketplace.list_attempts(limit=2000):
        created = str(row.get("created_at") or "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt.astimezone(timezone.utc).date() != today:
            continue
        if str(row.get("action") or "") in {
            "publish",
            "prepare_post",
            "guest_catalog_submit",
            "guest_catalog_email_submit",
            "guest_tool_submit",
        }:
            attempted.append(row)
    daily = dict(cert_payload.get("daily_delivery") or {})
    live_today_domains = set()
    verified_today_domains = set()
    for project in projects.values():
        for placement in project.get("placements") or []:
            if not _live(placement):
                continue
            stamp = (
                placement.get("published_at")
                or placement.get("publication_marked_at")
                or placement.get("checked_at")
            )
            try:
                dt = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt.astimezone(timezone.utc).date() == today:
                donor = _donor(placement)
                if donor:
                    live_today_domains.add(donor)
                    if _verified(placement):
                        verified_today_domains.add(donor)

        # Canonical external directories (for example Aoyii) bypass the forum
        # placement adapter, so daily delivery must include their verified
        # referring domains too. _confirmed_guest_evidence() already excludes
        # historical redirected backlinks and non-link submission receipts.
        for ext in crowd_seo._confirmed_guest_evidence(project):
            publication_url = str(ext.get("publication_url") or "").strip()
            donor = _host(publication_url)
            stamp = ext.get("verified_at")
            try:
                dt = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if donor and dt.astimezone(timezone.utc).date() == today:
                live_today_domains.add(donor)
                verified_today_domains.add(donor)
    return {
        "attempted_today": len(attempted),
        "submitted_today": int(daily.get("submitted_today") or 0),
        "live_today": len(live_today_domains),
        "verified_today": len(verified_today_domains),
        "submitted_unique_domains_today": int(daily.get("submitted_unique_domains_today") or 0),
        "verified_unique_domains_today": len(verified_today_domains),
        "target_unique_domains": int(daily.get("target_unique_domains") or 0),
    }


def _indexnow_snapshot() -> dict:
    if not INDEXNOW_STATE.exists():
        return {}
    try:
        payload = json.loads(INDEXNOW_STATE.read_text(encoding="utf-8"))
        return dict(payload.get("hosts") or {})
    except Exception:
        return {}


def _technical_seo_status(domain: str) -> dict:
    if domain == "avito-specialist.ru":
        return {
            "status": "NO_WRITE_ACCESS",
            "reason": "Сайт не размещён на текущем BORIS host и writable source/repository не найден.",
        }
    if domain == "systems-ai-oko.ru":
        root = Path("/var/www/systems-ai-oko/current")
        index = root / "index.html"
        robots = root / "robots.txt"
        sitemap = root / "sitemap.xml"
        source = Path("/root/BORIS/software-site-build/index.source.html")
        build_script = Path("/root/BORIS/software-site-build/build.sh")
        deploy_script = Path("/root/BORIS/software-site-deploy/deploy-domain.sh")
        try:
            html = index.read_text(encoding="utf-8")
        except Exception:
            html = ""
        try:
            source_text = source.read_text(encoding="utf-8")
        except Exception:
            source_text = ""
        try:
            deploy_text = deploy_script.read_text(encoding="utf-8")
        except Exception:
            deploy_text = ""
        try:
            current_release = str(root.resolve())
        except Exception:
            current_release = str(root)
        canonical_ok = '<link rel="canonical" href="https://systems-ai-oko.ru/">' in html
        source_semantics_ok = all(
            token in source_text
            for token in (
                "Разработка Telegram-ботов для бизнеса, AI и CRM | SYSTEMS.AI",
                "Разработка CRM на заказ с AI и интеграциями | SYSTEMS.AI",
                "AI-автоматизация бизнеса, AI-агенты и GPT | SYSTEMS.AI",
                "Разработка SaaS и MVP веб-сервиса под ключ | SYSTEMS.AI",
                "Связанные услуги",
            )
        )
        deploy_canonical_guard_ok = (
            "DOMAIN_SEO_CANONICAL_V2" in deploy_text
            and "canonical" in deploy_text
        )
        source_persistence_ok = bool(
            source.exists()
            and build_script.exists()
            and deploy_script.exists()
            and source_semantics_ok
            and deploy_canonical_guard_ok
        )
        ok = bool(
            html
            and canonical_ok
            and robots.exists()
            and sitemap.exists()
            and source_persistence_ok
        )
        return {
            "status": "PASS" if ok else "FAIL",
            "canonical_ok": canonical_ok,
            "robots_ok": robots.exists(),
            "sitemap_ok": sitemap.exists(),
            "source_persistence": "PASS" if source_persistence_ok else "FAIL",
            "source_semantics_ok": source_semantics_ok,
            "deploy_canonical_guard_ok": deploy_canonical_guard_ok,
            "current_release": current_release,
        }
    if domain == "boris-ai.pro":
        index = Path("/var/www/boris-home/index.html")
        source = Path("/root/BORIS/boris-home-source/index.html")
        deploy_script = Path("/root/BORIS/boris-home-source/deploy.sh")
        robots_source = Path("/root/BORIS/frontend/app/robots.ts")
        sitemap_source = Path("/root/BORIS/frontend/app/sitemap.ts")
        try:
            html = index.read_text(encoding="utf-8")
        except Exception:
            html = ""
        try:
            source_bytes = source.read_bytes()
        except Exception:
            source_bytes = b""
        try:
            live_bytes = index.read_bytes()
        except Exception:
            live_bytes = b""
        source_live_match = bool(source_bytes and source_bytes == live_bytes)
        source_persistence_ok = bool(
            source.exists()
            and deploy_script.exists()
            and source_live_match
        )
        canonical_ok = '<link rel="canonical" href="https://boris-ai.pro/">' in html
        metrika_ok = all(
            token in html
            for token in (
                "111297252",
                "boris_lead_form_submit",
                "boris_primary_cta",
                'id="avito-ai-sales"',
            )
        )
        ok = bool(
            html
            and canonical_ok
            and robots_source.exists()
            and sitemap_source.exists()
            and metrika_ok
            and source_persistence_ok
        )
        return {
            "status": "PASS" if ok else "FAIL",
            "canonical_ok": canonical_ok,
            "metrika_ok": metrika_ok,
            "robots_source_ok": robots_source.exists(),
            "sitemap_source_ok": sitemap_source.exists(),
            "source_persistence": "PASS" if source_persistence_ok else "FAIL",
            "source_live_match": source_live_match,
            "source_path": str(source),
        }
    return {"status": "UNKNOWN"}


def build() -> dict:
    cert_payload = {}
    if CERT.exists():
        try:
            cert_payload = json.loads(CERT.read_text(encoding="utf-8"))
        except Exception:
            cert_payload = {}
    cert_projects = _cert_projects()
    indexnow = _indexnow_snapshot()
    known_projects = {
        _host(p.get("site")): p
        for p in crowd_seo.list_projects()
        if _host(p.get("site")) in OWNER_DOMAINS
    }
    all_projects = {
        domain: p for domain, p in known_projects.items()
        if crowd_seo.project_is_active(p)
    }
    human = _human_queue(all_projects)
    external_snapshot = dict(cert_payload.get("external_submissions") or {})
    historical_items = list(external_snapshot.get("historical_redirected") or [])
    rows = {}
    for domain in OWNER_DOMAINS:
        project = all_projects.get(domain)
        cert = cert_projects.get(domain) or {}
        if not project:
            paused = known_projects.get(domain) or {}
            if paused.get("status") == "paused":
                rows[domain] = {
                    "project_id": paused.get("id"), "project_status": "paused",
                    "state": "PAUSED", "target": 0,
                    "contract_target": int(paused.get("target_count") or 60),
                    "live": 0, "verified": 0, "remaining": 0,
                    "technical_seo": {"status": "PAUSED"},
                    "indexnow": {"status": "PAUSED"},
                }
                continue
            rows[domain] = {
                "target": 60,
                "state": "PROJECT_MISSING",
                "live": 0,
                "verified": 0,
                "remaining": 60,
            }
            continue
        placements = list(project.get("placements") or [])
        live_records = [_publication_record(x) for x in placements if _live(x)]
        verified_records = [_publication_record(x) for x in placements if _verified(x)]

        # OWNER_REPORT_CANONICAL_EXTERNAL_V1
        # Canonical external publications (directories/catalogs that bypass the
        # forum placement adapter) are real delivery evidence too. Only current
        # verified rows with a live target link are returned by this helper.
        placement_publication_urls = {
            str(x.get("publication_url") or "").rstrip("/")
            for x in live_records
            if x.get("publication_url")
        }
        external_records = []
        for ext in crowd_seo._confirmed_guest_evidence(project):
            publication_url = str(ext.get("publication_url") or "").strip()
            if not publication_url or publication_url.rstrip("/") in placement_publication_urls:
                continue
            verification = ext.get("verification") if isinstance(ext.get("verification"), dict) else {}
            external_records.append({
                "placement_id": None,
                "donor_domain": _host(publication_url),
                "publication_url": publication_url,
                "target_url": ext.get("target_url"),
                "http_status": verification.get("http_status"),
                "link_present": True,
                "indexable": verification.get("indexable", True),
                "rel": verification.get("rel"),
                "anchor": None,
                "link_mode": "external_directory",
                "checked_at": verification.get("checked_at") or ext.get("verified_at"),
                "first_seen_at": ext.get("verified_at"),
                "last_verified_at": ext.get("verified_at"),
                "iks": ext.get("iks"),
                "iks_tier": ext.get("iks_tier"),
                "platform": ext.get("platform"),
                "surface_id": None,
                "status": "verified",
                "source": ext.get("source") or "canonical_external",
            })
        live_records.extend(external_records)
        verified_records.extend(external_records)
        live_domains = sorted({x["donor_domain"] for x in live_records if x.get("donor_domain")})
        verified_domains = sorted({x["donor_domain"] for x in verified_records if x.get("donor_domain")})
        status_counts = Counter(str(x.get("status") or "unknown") for x in placements)
        target = int(project.get("target_count") or cert.get("required") or 60)
        candidate_pool = int(cert.get("eligible_total") or 0)
        verified_count = len(verified_domains)

        external_pending_for_domain = [
            item for item in list(external_snapshot.get("pending") or [])
            if _host(item.get("target_url")) == domain
        ]
        external_verified_for_domain = [
            item for item in list(external_snapshot.get("verified") or [])
            if _host(item.get("target_url")) == domain
        ]
        submitted_donors = {
            _donor(placement)
            for placement in placements
            if (
                placement.get("publication_url")
                or bool((placement.get("last_publish_result") or {}).get("submission_accepted"))
            )
            and _donor(placement)
        }
        for item in external_pending_for_domain + external_verified_for_domain:
            donor = str(item.get("domain") or "").strip().lower().removeprefix("www.")
            if not donor:
                donor = _host(item.get("url"))
            if donor:
                submitted_donors.add(donor)
        submitted_count = len(submitted_donors)

        historical_for_domain = [
            item for item in historical_items
            if _host(item.get("project_site") or item.get("redirect_target")) == domain
        ]
        quality_buckets = Counter()
        for placement in placements:
            tier = str(placement.get("iks_tier") or "unknown").lower()
            if tier not in {"high", "medium", "low"}:
                tier = "unknown"
            quality_buckets[tier] += 1
        rows[domain] = {
            "project_id": project.get("id"),
            "target": target,
            "plan_mode": project.get("plan_mode"),
            "project_status": project.get("status"),
            "technical_seo": _technical_seo_status(domain),
            "indexnow": (
                {
                    "status": "NO_WRITE_ACCESS",
                    "tracked_urls": 0,
                }
                if domain == "avito-specialist.ru"
                else {
                    "status": (
                        "INDEXNOW_VERIFIED"
                        if any(
                            str((x or {}).get("last_status") or "") == "INDEXNOW_VERIFIED"
                            for x in ((indexnow.get(domain) or {}).get("urls") or {}).values()
                        )
                        else "NOT_VERIFIED"
                    ),
                    "tracked_urls": len(((indexnow.get(domain) or {}).get("urls") or {})),
                    "last_checked_at": (indexnow.get(domain) or {}).get("last_checked_at"),
                }
            ),
            "live": len(live_domains),
            "live_unverified": len(set(live_domains) - set(verified_domains)),
            "verified": verified_count,
            "submitted": submitted_count,
            "remaining": max(0, target - verified_count),
            "remaining_to_20": max(0, 20 - verified_count),
            "remaining_to_40": max(0, 40 - verified_count),
            "remaining_to_60": max(0, 60 - verified_count),
            "pending_moderation": (
                sum(status_counts.get(x, 0) for x in PENDING_STATUSES)
                + len(external_pending_for_domain)
            ),
            "human_pending": int(status_counts.get("external_checkpoint") or 0),
            "ready_to_publish": int(status_counts.get("ready_to_publish") or 0),
            "candidate_pool": candidate_pool,
            "selected_slots": int(cert.get("selected_slots") or len(placements)),
            "eligible_donors": candidate_pool,
            "quality_buckets": {
                "high": int(quality_buckets.get("high") or 0),
                "medium": int(quality_buckets.get("medium") or 0),
                "low": int(quality_buckets.get("low") or 0),
                "unknown": int(quality_buckets.get("unknown") or 0),
            },
            "historical_redirected": len(historical_for_domain),
            "historical_redirected_records": historical_for_domain,
            "lost_replacement": sum(
                1
                for placement in placements
                if str(placement.get("status") or "") in LOST_STATUSES
                and bool(placement.get("publication_url"))
            ),
            "prepublication_replacement": sum(
                status_counts.get(x, 0) for x in PREPUBLICATION_REPLACEMENT_STATUSES
            ),
            "capacity_ok": bool(cert.get("capacity_ok")),
            "rules_ok": bool(cert.get("rules_ok", True)),
            "delivery_ready": bool(cert.get("delivery_ready")),
            "live_referring_domains": live_domains,
            "verified_referring_domains": verified_domains,
            "live_records": live_records,
            "verified_records": verified_records,
            "status_counts": dict(status_counts),
        }

    cross_use: dict[str, list[str]] = {}
    for domain, row in rows.items():
        for donor in row.get("verified_referring_domains") or []:
            cross_use.setdefault(donor, []).append(domain)
    cross_use = {d: sites for d, sites in cross_use.items() if len(sites) > 1}

    payload = {
        "report": "BORIS_OWNER_SEO_STATUS_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "success_definition": "LIVE PUBLIC URL + target link present; VERIFIED additionally requires indexable=true and status=verified.",
        "domains": rows,
        "totals": {
            "target": sum(int(x.get("target") or 0) for x in rows.values()),
            "live": sum(int(x.get("live") or 0) for x in rows.values()),
            "verified": sum(int(x.get("verified") or 0) for x in rows.values()),
            "remaining": sum(int(x.get("remaining") or 0) for x in rows.values()),
            "human_pending": sum(int(x.get("human_pending") or 0) for x in rows.values()),
            "ready_to_publish": sum(int(x.get("ready_to_publish") or 0) for x in rows.values()),
            "submitted": sum(int(x.get("submitted") or 0) for x in rows.values()),
            "historical_redirected": sum(int(x.get("historical_redirected") or 0) for x in rows.values()),
            "lost_replacement": sum(int(x.get("lost_replacement") or 0) for x in rows.values()),
        },
        "cross_domain_verified_donor_reuse": cross_use,
        "daily_delivery": _attempt_daily_snapshot(cert_payload, all_projects),
        "acquisition_lanes": cert_payload.get("acquisition_lanes") or {},
        "human_checkpoint_queue": {
            "unique_platforms": human.get("count"),
            "placement_slots": sum(int(x.get("human_pending") or 0) for x in rows.values()),
            "by_checkpoint": human.get("by_checkpoint"),
            "top_5_now": human.get("top_5_now") or [],
            "file": str(HUMAN),
        },
        "external_moderation": {
            "pending_total": int(external_snapshot.get("pending_total") or 0),
            "verified_total": int(external_snapshot.get("verified_total") or 0),
            "historical_redirected_total": int(external_snapshot.get("historical_redirected_total") or 0),
            "historical_redirected": list(external_snapshot.get("historical_redirected") or []),
        },
        "paid_backlinks_enabled": False,
        "acceptance": {
            "phase_a": all(int(x.get("verified") or 0) >= 20 for x in rows.values()),
            "phase_b": all(int(x.get("verified") or 0) >= 40 for x in rows.values()),
            "phase_c_final": all(int(x.get("verified") or 0) >= 60 for x in rows.values()),
        },
    }

    ready_total = int(payload["totals"].get("ready_to_publish") or 0)
    human_total = int(payload["totals"].get("human_pending") or 0)
    moderation_total = sum(int(x.get("pending_moderation") or 0) for x in rows.values())
    remaining_total = int(payload["totals"].get("remaining") or 0)
    if remaining_total <= 0:
        delivery_state = "DELIVERY_COMPLETE"
    elif ready_total > 0:
        delivery_state = "DELIVERY_ACTIVE"
    elif human_total > 0:
        delivery_state = "DELIVERY_BLOCKED_BY_HUMAN_CHECKPOINTS"
    elif moderation_total > 0:
        delivery_state = "DELIVERY_WAITING_MODERATION"
    else:
        delivery_state = "DELIVERY_BLOCKED_BY_CAPACITY"
    payload["delivery_health"] = {
        "state": delivery_state,
        "ready_to_publish": ready_total,
        "pending_moderation": moderation_total,
        "human_pending_placement_slots": human_total,
        "human_pending_unique_platforms": int(human.get("count") or 0),
        "remaining_verified_domains": remaining_total,
        "autonomous_safe_work_available": bool(ready_total > 0),
        "next_safe_action": (
            "publish_ready"
            if ready_total > 0
            else "verify_moderation_and_wait_for_human_checkpoint_completion"
            if moderation_total > 0
            else "human_checkpoint_completion"
            if human_total > 0
            else "expand_quality_donor_pool"
        ),
    }
    return payload, human


def _render_md(payload: dict) -> str:
    labels = {
        "systems-ai-oko.ru": "SYSTEMS.AI",
        "boris-ai.pro": "BORIS",
        "avito-specialist.ru": "AVITO SPECIALIST",
    }
    lines = ["# SEO OWNER STATUS", ""]
    for domain in ("systems-ai-oko.ru", "boris-ai.pro", "avito-specialist.ru"):
        row = payload["domains"].get(domain) or {}
        if row.get("state") == "PAUSED":
            lines += [f"## {labels.get(domain, domain)}", "- Приостановлен владельцем; исключён из активного плана.", ""]
            continue
        lines += [
            f"## {labels.get(domain, domain)}",
            f"- Technical SEO: {(row.get('technical_seo') or {}).get('status', 'UNKNOWN')}",
            f"- IndexNow: {(row.get('indexnow') or {}).get('status', 'UNKNOWN')}",
            f"- target: {row.get('target', 0)}",
            f"- eligible donors: {row.get('eligible_donors', 0)}",
            f"- live: {row.get('live', 0)}",
            f"- verified: {row.get('verified', 0)}/{row.get('target', 0)}",
            f"- remaining: {row.get('remaining', 0)}",
            f"- pending moderation: {row.get('pending_moderation', 0)}",
            f"- HUMAN_PENDING: {row.get('human_pending', 0)}",
            f"- ready to publish: {row.get('ready_to_publish', 0)}",
            f"- lost/replacement: {row.get('lost_replacement', 0)}",
            "",
        ]
    lines += [
        f"Human checkpoint platforms: {(payload.get('human_checkpoint_queue') or {}).get('unique_platforms', 0)}",
        f"Human checkpoint placement slots: {(payload.get('human_checkpoint_queue') or {}).get('placement_slots', 0)}",
        f"Autonomous ready: {payload['totals'].get('ready_to_publish', 0)}",
        f"Pending moderation: {(payload.get('external_moderation') or {}).get('pending_total', 0)}",
        f"Verified external submissions: {(payload.get('external_moderation') or {}).get('verified_total', 0)}",
        f"Verified total: {payload['totals'].get('verified', 0)}/{payload['totals'].get('target', 0)}",
        "",
        "PASS запрещён, пока каждый домен не достиг 60 verified referring domains.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    payload, human = build()
    DATA.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    HUMAN.write_text(json.dumps(human, ensure_ascii=False, indent=2), encoding="utf-8")
    OWNER_MD.write_text(_render_md(payload), encoding="utf-8")
    print(json.dumps({
        "status_file": str(STATUS),
        "human_queue_file": str(HUMAN),
        "owner_md": str(OWNER_MD),
        "totals": payload["totals"],
        "acceptance": payload["acceptance"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
