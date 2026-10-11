from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.services import crowd_seo
from app.services import service_marketplace as marketplace

ROOT = Path(__file__).resolve().parents[2]
# Immutable backend releases intentionally exclude mutable operational tools and
# runtime state. Checkpoint Center reads/writes the canonical live workspace
# where service-marketplace state, browser profiles and the browser assistant
# already live.
LIVE_BACKEND = Path("/root/BORIS/backend")
DATA = LIVE_BACKEND / "data" / "service_marketplaces"
QUEUE_FILE = DATA / "owner_seo_human_queue_latest.json"
ACCOUNT_SECRETS = DATA / "account_secrets.json"
OPERATOR_PROFILE = DATA / "operator_profile.json"
EVENTS_FILE = DATA / "seo_checkpoint_center_events.json"
BROWSER_ASSISTANT = LIVE_BACKEND / "tools" / "marketplace_browser_assistant.py"
PYTHON = LIVE_BACKEND / "venv" / "bin" / "python"

OWNER_SITES = ("boris-ai.pro", "systems-ai-oko.ru", "avito-specialist.ru")

# Platform-specific factual registration mailbox overrides. Otsovik explicitly
# rejects free-mail providers, so use the existing BORIS-domain system mailbox
# already provisioned for service verification. Mailbox credentials are never
# exposed to the extension.
PLATFORM_EMAIL_OVERRIDES = {
    "pubdisc_otsovik_com": "noreply@boris-ai.pro",
}

# These surfaces are the actual listing/submission form. A human completion
# means the listing itself was submitted; BORIS must verify moderation rather
# than posting the same site a second time.
DIRECT_LISTING_PLATFORMS = {
    "pubdisc_otsiv_ru",
    "pubdisc_orgpage_ru",
    "pubdisc_sitelike_org",
    "pubdisc_launch_ignlab_net",
    "pubdisc_pdfcatalog_ru",
}

SITE_PROFILES = {
    "boris-ai.pro": {
        "business_name": "BORIS AI",
        "website": "https://boris-ai.pro/",
        "description": (
            "BORIS AI — система автоматизации маркетинга и продаж: реклама, "
            "Авито, AI-маркетолог, AI-менеджер продаж, CRM и аналитика."
        ),
        "category_queries": ["software", "business software", "AI software"],
    },
    "systems-ai-oko.ru": {
        "business_name": "SYSTEMS.AI",
        "website": "https://systems-ai-oko.ru/",
        "description": (
            "SYSTEMS.AI — разработка программного обеспечения для бизнеса: "
            "SaaS, CRM, Telegram-боты, AI-автоматизация, приложения и интеграции."
        ),
        "category_queries": [
            "Custom Computer Programming Services",
            "software development",
            "IT services",
        ],
    },
    "avito-specialist.ru": {
        "business_name": "Специалист по Авито, авитолог",
        "website": "https://avito-specialist.ru/",
        "description": (
            "Услуги специалиста по продвижению бизнеса на Авито: аудит, "
            "настройка, ведение объявлений, аналитика и рост продаж."
        ),
        "category_queries": [
            "Marketing Consulting Services",
            "Advertising Agencies",
            "internet marketing",
        ],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def _save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _registration_map() -> dict[str, dict]:
    return {
        str(row.get("platform") or ""): row
        for row in marketplace.registration_plan()
        if row.get("platform")
    }


def _ensure_account_secret(platform: str, email: str) -> dict:
    """Return the same per-platform credentials used by browser assistant.

    Passwords are intentionally returned only to the owner-only checkpoint
    endpoint. They are never copied into reports/events/logs.
    """
    rows = _load(ACCOUNT_SECRETS, [])
    if not isinstance(rows, list):
        rows = []
    row = next((x for x in rows if x.get("platform") == platform), None)
    if row:
        if email and str(row.get("email") or "") != email:
            row["email"] = email
            row["updated_at"] = _now()
            _save(ACCOUNT_SECRETS, rows)
            try:
                os.chmod(ACCOUNT_SECRETS, 0o600)
            except OSError:
                pass
        return row
    base_username = {
        "forum_baza_1c": "BORIS_AI",
        "stroy_forum": "BORIS_AI",
        "foodmarkets": "BORIS_AI",
        "ssa": "BORIS_AI",
        "towerbuild": "BorisAI2026",
        "partnersearch": "BORIS_AI",
    }.get(platform, "BORIS_AI")
    row = {
        "platform": platform,
        "email": email,
        "username": base_username,
        "password": "B!" + secrets.token_urlsafe(20) + "9a",
        "created_at": _now(),
    }
    rows.append(row)
    _save(ACCOUNT_SECRETS, rows)
    try:
        os.chmod(ACCOUNT_SECRETS, 0o600)
    except OSError:
        pass
    return row


def _phone() -> str:
    profile = _load(OPERATOR_PROFILE, {})
    return str((profile or {}).get("phone") or "").strip()


def _queue() -> dict:
    value = _load(QUEUE_FILE, {})
    return value if isinstance(value, dict) else {}


def _direct_listing_states() -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    for row in marketplace.list_attempts(limit=2000):
        if str(row.get("action") or "") not in {
            "guest_catalog_submit",
            "guest_catalog_email_submit",
            "guest_tool_submit",
            "human_listing_submit",
            "verify_publication",
        }:
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        target_url = str(meta.get("target_url") or "").strip().rstrip("/")
        platform = str(row.get("platform") or "").strip()
        if not platform or not target_url:
            continue
        key = (platform, target_url)
        if key in latest:
            continue
        latest[key] = row
    return latest


def _entry_id(platform: str, target_site: str | None) -> str:
    return platform if not target_site else f"{platform}::{target_site}"


def _entry_host(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")


def _build_autofill(
    *,
    platform: str,
    flow_kind: str,
    target_site: str | None,
    registration: dict,
    include_credentials: bool = True,
) -> dict:
    email = str(
        PLATFORM_EMAIL_OVERRIDES.get(platform)
        or registration.get("email")
        or "kirillkozerogov@gmail.com"
    ).strip()
    account = (
        _ensure_account_secret(platform, email)
        if flow_kind == "account_onboarding" and include_credentials
        else {}
    )

    autofill = {
        "email": email,
        "username": (
            str(account.get("username") or "BORIS_AI")
            if flow_kind == "account_onboarding"
            else ""
        ),
        "password": (
            str(account.get("password") or "")
            if flow_kind == "account_onboarding"
            else ""
        ),
        # Phone is used only by registration-like flows in the extension.
        "phone": _phone() if flow_kind == "account_onboarding" else "",
        "business_name": "",
        "website": "",
        "description": "",
        "category_queries": [],
        # Deliberately blank: address/location/age/legal consent are never
        # fabricated or auto-accepted.
        "address": "",
        "city": "",
        "country": "",
        "birth_date": "",
        "contact_name": "",
    }

    if target_site:
        profile = SITE_PROFILES.get(target_site) or {}
        autofill.update(
            {
                "business_name": str(profile.get("business_name") or ""),
                "website": str(profile.get("website") or ""),
                "description": str(profile.get("description") or ""),
                "category_queries": list(profile.get("category_queries") or []),
            }
        )
    return autofill


def build_center_payload(*, include_secrets: bool = False) -> dict:
    queue = _queue()
    registrations = _registration_map()
    direct_states = _direct_listing_states()
    queue_items = list(queue.get("items") or [])
    top_platforms = [
        str(row.get("platform") or "")
        for row in (queue.get("top_5_now") or [])
        if row.get("platform")
    ]

    entries: list[dict] = []
    direct_listing_pending = 0
    for item in queue_items:
        platform = str(item.get("platform") or "").strip()
        if not platform:
            continue
        registration = registrations.get(platform) or {}
        action_url = str(
            item.get("action_url")
            or registration.get("account_url")
            or registration.get("url")
            or ""
        ).strip()
        if not action_url.startswith(("http://", "https://")):
            continue

        targets = [
            str(x)
            for x in (item.get("target_sites") or [])
            if str(x) in OWNER_SITES
        ]
        flow_kind = (
            "direct_listing"
            if platform in DIRECT_LISTING_PLATFORMS
            else "account_onboarding"
        )

        if flow_kind == "direct_listing" and targets:
            expanded_targets: list[str | None] = targets
        else:
            expanded_targets = [None]

        for target_site in expanded_targets:
            if flow_kind == "direct_listing" and target_site:
                target_url = str((SITE_PROFILES.get(target_site) or {}).get("website") or "").rstrip("/")
                state = direct_states.get((platform, target_url)) if target_url else None
                state_status = str((state or {}).get("status") or "")
                if state_status == "verified" or state_status == "submitted" or state_status.startswith("submitted_pending"):
                    direct_listing_pending += 1
                    continue

            autofill = _build_autofill(
                platform=platform,
                flow_kind=flow_kind,
                target_site=target_site,
                registration=registration,
                include_credentials=include_secrets,
            )
            autofill_fields = ["email"]
            if flow_kind == "account_onboarding":
                autofill_fields.extend(["username", "password", "phone"])
            if target_site:
                autofill_fields.extend(["business_name", "website", "description"])
            if not include_secrets:
                autofill = {
                    "email": "",
                    "username": "",
                    "password": "",
                    "phone": "",
                    "business_name": "",
                    "website": "",
                    "description": "",
                    "category_queries": [],
                    "address": "",
                    "city": "",
                    "country": "",
                    "birth_date": "",
                    "contact_name": "",
                }

            entry = {
                "id": _entry_id(platform, target_site),
                "platform": platform,
                "name": item.get("name") or platform,
                "action_url": action_url,
                "host": _entry_host(action_url),
                "checkpoint": item.get("checkpoint"),
                "action": item.get("action"),
                "after_completion": item.get("after_completion"),
                "flow_kind": flow_kind,
                "target_site": target_site,
                "target_sites": targets,
                "iks": item.get("iks"),
                "iks_tier": item.get("iks_tier"),
                "human_effort_score": item.get("human_effort_score"),
                "top_5_platform": platform in top_platforms,
                "autofill_fields": autofill_fields,
                "autofill": autofill,
                "never_automate": [
                    "captcha",
                    "terms_acceptance",
                    "age_declaration",
                    "truthful_identity",
                    "sms_or_email_code",
                ],
            }
            entries.append(entry)

    entries.sort(
        key=lambda x: (
            0 if x.get("top_5_platform") else 1,
            -int(x.get("human_effort_score") or 0),
            str(x.get("platform") or ""),
            str(x.get("target_site") or ""),
        )
    )
    return {
        "status": "ok",
        "generated_at": _now(),
        "extension_protocol": 1,
        "security": {
            "owner_only": True,
            "passwords_in_response": bool(include_secrets),
            "cache": "no-store",
            "captcha_solver": False,
            "legal_consent_automation": False,
        },
        "summary": {
            "unique_platforms": int(queue.get("count") or 0),
            "entries": len(entries),
            "direct_listing_pending": direct_listing_pending,
            "top_5_platforms": top_platforms,
        },
        "entries": entries,
    }


def _events() -> list[dict]:
    value = _load(EVENTS_FILE, [])
    return value if isinstance(value, list) else []


def _record_event(event: dict) -> None:
    rows = _events()
    # Never persist any autofill/credential material.
    clean = {
        "at": _now(),
        "entry_id": event.get("entry_id"),
        "platform": event.get("platform"),
        "target_site": event.get("target_site"),
        "event": event.get("event"),
        "page_url": event.get("page_url"),
        "result": event.get("result"),
    }
    rows.append(clean)
    _save(EVENTS_FILE, rows[-1000:])


def _schedule_guardian(platform: str) -> dict:
    """Resume the canonical Crowd SEO worker without creating a second runner."""
    try:
        proc = subprocess.run(
            ["systemctl", "start", "--no-block", "boris-forum-acquisition-guard.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return {
            "status": "deferred",
            "platform": platform,
            "reason": f"guardian_schedule_error:{type(exc).__name__}",
        }
    if proc.returncode != 0:
        return {
            "status": "deferred",
            "platform": platform,
            "reason": ((proc.stderr or proc.stdout or "guardian_start_failed")[-500:]).strip(),
        }
    return {
        "status": "scheduled",
        "platform": platform,
        "executor": "boris-forum-acquisition-guard.service",
    }


def _refresh_owner_projects() -> None:
    for project in crowd_seo.list_projects():
        domain = str(project.get("domain") or "")
        if domain not in OWNER_SITES or not crowd_seo.project_is_active(project):
            continue
        pid = str(project.get("id") or "")
        if not pid:
            continue
        try:
            crowd_seo.refresh_project_forums(pid)
            crowd_seo.sync_project_drafts(pid)
            crowd_seo.schedule_replacements(pid)
        except Exception:
            continue


def _verify_registration(platform: str) -> dict:
    try:
        proc = subprocess.run(
            [str(PYTHON), str(BROWSER_ASSISTANT), "verify-registration", "--platform", platform],
            cwd=LIVE_BACKEND,
            text=True,
            capture_output=True,
            timeout=80,
            check=False,
            env={**os.environ, "PYTHONPATH": str(LIVE_BACKEND)},
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "verification_error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    parsed = None
    stdout = str(proc.stdout or "").strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
        except Exception:
            parsed = None
    if not isinstance(parsed, dict):
        parsed = {
            "ok": proc.returncode == 0,
            "status": "unknown",
            "error": (proc.stderr or stdout)[-1000:],
        }
    parsed["returncode"] = proc.returncode
    return parsed


def _verify_publication(platform: str, publication_url: str, target_url: str) -> dict:
    if not publication_url.startswith(("http://", "https://")):
        return {"ok": False, "publication_verified": False, "error": "publication_url_missing"}
    try:
        proc = subprocess.run(
            [
                str(PYTHON),
                str(BROWSER_ASSISTANT),
                "verify-publication",
                "--platform",
                platform,
                "--publication-url",
                publication_url,
                "--target-url",
                target_url,
            ],
            cwd=LIVE_BACKEND,
            text=True,
            capture_output=True,
            timeout=80,
            check=False,
            env={**os.environ, "PYTHONPATH": str(LIVE_BACKEND)},
        )
    except Exception as exc:
        return {
            "ok": False,
            "publication_verified": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    stdout = str(proc.stdout or "").strip()
    try:
        parsed = json.loads(stdout) if stdout else {}
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault("returncode", proc.returncode)
    if proc.returncode != 0 and not parsed.get("error"):
        parsed["error"] = (proc.stderr or stdout)[-1000:]
    return parsed


def _same_page(a: str | None, b: str | None) -> bool:
    try:
        pa = urlparse(str(a or ""))
        pb = urlparse(str(b or ""))
        return (
            pa.scheme.lower() == pb.scheme.lower()
            and pa.netloc.lower().removeprefix("www.") == pb.netloc.lower().removeprefix("www.")
            and pa.path.rstrip("/") == pb.path.rstrip("/")
        )
    except Exception:
        return False


def human_completed(
    *,
    entry_id: str,
    platform: str,
    target_site: str | None,
    page_url: str | None,
) -> dict:
    payload = build_center_payload()
    entry = next((x for x in payload["entries"] if x.get("id") == entry_id), None)
    if not entry or str(entry.get("platform") or "") != platform:
        raise KeyError("checkpoint entry not found")

    flow_kind = str(entry.get("flow_kind") or "")
    result: dict

    if flow_kind == "account_onboarding":
        # Human performed the only action BORIS is not allowed to perform.
        # Verify with the same server-side credentials before unlocking posts.
        result = _verify_registration(platform)
        _refresh_owner_projects()
        if str(result.get("status") or "") == "ready":
            result["resume"] = _schedule_guardian(platform)
        else:
            result["resume"] = {
                "status": "not_ready",
                "platform": platform,
            }
    else:
        # The human just submitted the listing itself (CAPTCHA/identity form).
        # Never submit a duplicate and never flip the whole platform globally:
        # completion is tracked per (platform, target_site).
        target_url = str((SITE_PROFILES.get(str(target_site)) or {}).get("website") or "")
        candidate_public_url = str(page_url or "").strip()
        proof = None
        if (
            target_url
            and candidate_public_url
            and not _same_page(candidate_public_url, str(entry.get("action_url") or ""))
        ):
            proof = _verify_publication(platform, candidate_public_url, target_url)

        verified = bool((proof or {}).get("publication_verified"))
        attempt_status = "verified" if verified else "submitted_pending_verification"
        marketplace.record_attempt(
            platform=platform,
            action="human_listing_submit",
            status_value=attempt_status,
            url=candidate_public_url or str(entry.get("action_url") or ""),
            meta={
                "entry_id": entry_id,
                "target_url": target_url,
                "project_site": (
                    f"https://{target_site}/" if target_site else None
                ),
                "human_checkpoint_completed": True,
                "publication_verified": verified,
                "counts_toward_direct_contract": bool(verified),
            },
        )

        if verified:
            publication_url = str((proof or {}).get("final_url") or candidate_public_url)
            marketplace.upsert_verified_external_publication(
                platform=platform,
                url=publication_url,
                target_url=target_url,
                title=(proof or {}).get("title"),
                verified_at=_now(),
                listing_url=publication_url,
                verification={
                    "http_status": (proof or {}).get("http_status"),
                    "final_url": publication_url,
                    "link_present": True,
                    "indexable": True,
                    "rel": (proof or {}).get("rel"),
                    "proof_source": (proof or {}).get("proof_source"),
                    "counts_toward_new_direct_60": True,
                },
                source="human_checkpoint_center",
            )
            marketplace.record_attempt(
                platform=platform,
                action="verify_publication",
                status_value="verified",
                url=publication_url,
                meta={
                    "entry_id": entry_id,
                    "target_url": target_url,
                    "project_site": (
                        f"https://{target_site}/" if target_site else None
                    ),
                    "publication_verified": True,
                    "link_present": True,
                    "indexable": True,
                    "counts_toward_direct_contract": True,
                },
            )
            result = {
                "ok": True,
                "status": "verified",
                "checkpoint": None,
                "publication_url": publication_url,
                "proof": proof,
            }
        else:
            result = {
                "ok": True,
                "status": "submitted_pending_verification",
                "checkpoint": "human_submission_pending_verification",
                "proof": proof,
            }
        _refresh_owner_projects()
        result["resume"] = _schedule_guardian(platform)

    _record_event(
        {
            "entry_id": entry_id,
            "platform": platform,
            "target_site": target_site,
            "event": "human_completed",
            "page_url": page_url,
            "result": {
                "ok": result.get("ok"),
                "status": result.get("status"),
                "checkpoint": result.get("checkpoint"),
            },
        }
    )
    return {
        "status": "ok",
        "entry_id": entry_id,
        "platform": platform,
        "flow_kind": flow_kind,
        "verification": result,
    }
