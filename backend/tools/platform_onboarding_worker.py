#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import crowd_seo
from app.services import platform_rules
from app.services import service_marketplace as marketplace
from tools import forum_acquisition_guard as guardian

STATUS_FILE = BACKEND_ROOT / "data" / "service_marketplaces" / "platform_onboarding_worker_status.json"
WORKER_LOCK_FILE = BACKEND_ROOT / "run" / "platform_onboarding_worker.lock"
ASSISTANT_SCRIPT = str(BACKEND_ROOT / "tools" / "marketplace_browser_assistant.py")

# Human-gated checkpoints cannot be bypassed. This worker only detects that an
# external one-time checkpoint was legitimately completed and then resumes the
# existing BORIS verification/publication chain automatically.
VERIFY_INTERVAL = timedelta(hours=1)
# Static human gates (CAPTCHA, legal/age declarations, identity/SSO) cannot
# self-clear every hour while nobody is interacting with the site. Rechecking
# them hourly was burning most of the worker budget and starving new/transient
# inventory. Keep fast hourly polling only for checkpoints that commonly clear
# asynchronously (email/SMS), and back off static gates while preserving an
# automatic eventual recheck.
PASSIVE_HUMAN_VERIFY_INTERVAL = timedelta(hours=12)
MANUAL_BROWSER_VERIFY_INTERVAL = timedelta(hours=6)
MAX_VERIFICATIONS_PER_RUN = 12
MAX_TRANSIENT_RULE_AUDITS_PER_RUN = 6
RUN_BUDGET_SECONDS = 240
VERIFY_START_RESERVE_SECONDS = 75
RULE_AUDIT_START_RESERVE_SECONDS = 120

# GUEST_PUBLICATION_MODERATION_SELFHEAL_V1
# Some legitimate guest boards accept a post immediately but publish it only
# after moderation. Submission is already done at that point: retrying the form
# risks duplicates. The onboarding worker therefore performs read-only public
# verification and promotes the local evidence row only after BOTH the exact
# title and the exact target URL are visible on the public listing.
GUEST_PUBLICATIONS_DIR = BACKEND_ROOT / "data" / "service_marketplaces"
GUEST_PUBLICATION_VERIFY_INTERVAL = timedelta(minutes=30)
MAX_GUEST_PUBLICATION_VERIFICATIONS_PER_RUN = 4
EXTERNAL_PUBLICATION_VERIFY_INTERVAL = timedelta(hours=6)
MAX_EXTERNAL_PUBLICATION_VERIFICATIONS_PER_RUN = 6
GUEST_PUBLICATION_VERIFY_URLS = {
    "guest_forma_spb": ("https://www.forma.spb.ru/active/adv/main.shtml",),
}
GUEST_CONFIRMED_POSTS_DIR = GUEST_PUBLICATIONS_DIR / "guest_publish_runs"


def _sync_confirmed_guest_publications() -> dict:
    """Merge independently-confirmed guest posts into the canonical ledger."""
    scanned = 0
    created = []
    existing = []
    errors = []
    # confirmed_posts_*.json is immutable historical evidence, not the live
    # source of truth. Once a URL has entered the canonical ledger, only the
    # live verifier may change its current verified/link state. Re-importing an
    # old sidecar must never resurrect a backlink that was later stripped.
    canonical_urls = {
        str(item.get("url") or "").strip().rstrip("/")
        for item in marketplace.list_publications()
        if str(item.get("url") or "").strip()
    }
    for path in sorted(GUEST_CONFIRMED_POSTS_DIR.glob("confirmed_posts_*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"[:500]})
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or str(row.get("status") or "") != "posted_verified":
                continue
            scanned += 1
            publication_url = str(row.get("publication_url") or row.get("listing_url") or "").strip()
            canonical = publication_url.rstrip("/")
            if canonical and canonical in canonical_urls:
                existing.append(str(row.get("platform") or "guest_external"))
                continue
            try:
                saved = marketplace.upsert_verified_external_publication(
                    platform=str(row.get("platform") or "guest_external"),
                    url=publication_url,
                    target_url=str(row.get("target_url") or ""),
                    title=str(row.get("title") or "") or None,
                    posted_at=row.get("submitted_at") or row.get("verified_at"),
                    verified_at=row.get("verified_at") or row.get("confirmed_at"),
                    listing_url=row.get("listing_url"),
                    verification=row.get("verification"),
                    source="confirmed_guest_post",
                )
                bucket = created if saved.get("created") else existing
                bucket.append(str(row.get("platform") or "guest_external"))
                if canonical:
                    canonical_urls.add(canonical)
            except Exception as exc:
                errors.append({
                    "file": path.name,
                    "platform": row.get("platform"),
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
    return {
        "scanned": scanned,
        "created": created,
        "existing": existing,
        "errors": errors,
    }


def _external_publication_due(row: dict | None, now: datetime) -> bool:
    if not row:
        return False
    source = str(row.get("source") or "")
    draft_id = str(row.get("draft_id") or "")
    if source not in {"confirmed_guest_post", "guest_moderation_self_heal"} and not draft_id.startswith("external_"):
        return False
    checked = _parse_iso(row.get("verification_checked_at"))
    return checked is None or now - checked >= EXTERNAL_PUBLICATION_VERIFY_INTERVAL


def _verify_external_publication(row: dict, *, timeout: int = 20) -> dict:
    url = str(row.get("url") or "").strip()
    target = str(row.get("site_url") or "").strip().rstrip("/")
    title = " ".join(str(row.get("title") or "").split())
    if not url or not target:
        return {"ok": False, "verified": False, "error": "external_verification_preflight_failed"}
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BORIS-PublicationVerifier/1.0)"},
            timeout=timeout,
            allow_redirects=True,
        )
        if response.apparent_encoding:
            response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text or "", "lxml")
        page_text = " ".join(soup.stripped_strings)
        target_text_present = target in page_text.rstrip("/")
        exact_href = any(
            urljoin(str(response.url), str(anchor.get("href") or "").strip()).rstrip("/") == target
            for anchor in soup.find_all("a", href=True)
        )
        title_present = bool(title and title in page_text)
        # CROWD_EXTERNAL_CLICKABLE_LINK_PROOF_V1: visible URL text is not an
        # SEO backlink. Credit only a real clickable href to the exact target.
        verified = bool(response.status_code == 200 and exact_href)
        return {
            "ok": True,
            "verified": verified,
            "http_status": int(response.status_code),
            "final_url": str(response.url),
            "link_present": bool(exact_href),
            "target_text_present": bool(target_text_present),
            "title_present": title_present,
            "error": None if verified else "target_link_missing",
        }
    except Exception as exc:
        # Transport failures are not proof that a previously verified link was
        # removed. Preserve the current state and retry on the next cycle.
        return {
            "ok": False,
            "verified": None,
            "link_present": None,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


def _recheck_external_publications(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    due = [row for row in marketplace.list_publications() if _external_publication_due(row, now)]
    attempts=[]; removed=[]; healthy=[]; transient=[]
    for row in due[:MAX_EXTERNAL_PUBLICATION_VERIFICATIONS_PER_RUN]:
        result=_verify_external_publication(row)
        url=str(row.get("url") or "")
        if result.get("verified") is not None:
            marketplace.update_external_publication_verification(
                url,
                verified=bool(result.get("verified")),
                checked_at=now.isoformat(),
                link_present=result.get("link_present"),
                error=result.get("error"),
            )
            (healthy if result.get("verified") else removed).append(str(row.get("platform") or ""))
        else:
            transient.append(str(row.get("platform") or ""))
        attempts.append({
            "platform": row.get("platform"),
            "url": url,
            "verified": result.get("verified"),
            "link_present": result.get("link_present"),
            "error": result.get("error"),
        })
    return {
        "due": len(due),
        "attempted": len(attempts),
        "remaining_due": max(0, len(due)-len(attempts)),
        "healthy": healthy,
        "removed_or_stripped": removed,
        "transient": transient,
        "attempts": attempts,
    }


GUEST_SUBMISSION_CONFIRMATION_MARKERS = (
    "объявление отправлено",
    "заявка принята",
    "объявление поступило на модерацию",
    "добавлено в нашу базу",
    "будет опубликовано",
    "отправлено на модерацию",
    "успешно добавлено",
    "материал отправлен",
    "спасибо, ваше объявление",
)


def _guest_submission_evidence_valid(row: dict | None) -> bool:
    """Reject a same-form POST that has no explicit submission evidence.

    GUEST_SUBMIT_EVIDENCE_TRUTH_V1:
    Generic words such as ``опубликованное ранее`` inside the input form are
    not proof that the POST was accepted. A moderation/publication row remains
    submitted only when it has a public URL, a different confirmation route,
    or an explicit acceptance/moderation message.
    """
    if not isinstance(row, dict) or not row.get("submitted"):
        return True
    if row.get("verified") or str(row.get("public_url") or "").strip():
        return True
    source = str(row.get("source_form") or "").strip().rstrip("/")
    final = str(row.get("final_url") or "").strip().rstrip("/")
    if not source or not final:
        return True
    if source != final:
        return True
    excerpt = " ".join(str(row.get("response_excerpt") or "").lower().split())
    return any(marker in excerpt for marker in GUEST_SUBMISSION_CONFIRMATION_MARKERS)


def _guest_publication_due(row: dict | None, now: datetime) -> bool:
    if not row or not row.get("submitted") or row.get("verified"):
        return False
    checked = _parse_iso(row.get("last_checked_at"))
    return checked is None or now - checked >= GUEST_PUBLICATION_VERIFY_INTERVAL


def _verify_guest_publication(row: dict, *, timeout: int = 20) -> dict:
    platform = str(row.get("platform") or "").strip()
    title = " ".join(str(row.get("title") or "").split())
    target = str(row.get("target_url") or "").strip().rstrip("/")
    # Reuse the exact public probes captured at submit time before falling back
    # to a platform-specific static map. A successful submit must never become
    # permanently unverifiable merely because the site was discovered outside
    # the original hard-coded guest-board list.
    saved_probe_urls = tuple(
        str(probe.get("url") or "").strip()
        for probe in (row.get("probes") or [])
        if isinstance(probe, dict) and str(probe.get("url") or "").strip()
    )
    urls = tuple(row.get("verification_urls") or ()) or saved_probe_urls or GUEST_PUBLICATION_VERIFY_URLS.get(platform, ())
    if not platform or not title or not target or not urls:
        return {
            "ok": False,
            "verified": False,
            "platform": platform,
            "error": "guest_verification_preflight_failed",
        }

    probes = []
    for source_url in urls:
        try:
            response = requests.get(
                str(source_url),
                headers={"User-Agent": "Mozilla/5.0 (compatible; BORIS-PublicationVerifier/1.0)"},
                timeout=timeout,
                allow_redirects=True,
            )
            if response.apparent_encoding:
                response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text or "", "lxml")
            page_text = " ".join(soup.stripped_strings)
            title_present = title in page_text
            target_present = target in page_text
            robots = " ".join(
                str(meta.get("content") or "").lower()
                for meta in soup.find_all("meta")
                if str(meta.get("name") or "").strip().lower() == "robots"
            )
            indexable = bool(response.status_code == 200 and "noindex" not in robots)
            exact_href = False
            for anchor in soup.find_all("a", href=True):
                raw = str(anchor.get("href") or "").strip()
                absolute = urljoin(str(response.url), raw).rstrip("/")
                if raw.rstrip("/") == target or absolute == target:
                    exact_href = True
                    break
            # CROWD_GUEST_CLICKABLE_LINK_TRUTH_V1: plain text that merely spells
            # the target URL is not a backlink. Credit only an exact clickable
            # href on an indexable public page so guest and canonical verification
            # cannot oscillate between verified and target_link_missing.
            verified = bool(response.status_code == 200 and title_present and exact_href and indexable)
            probe = {
                "url": str(source_url),
                "final_url": str(response.url),
                "http_status": int(response.status_code),
                "title_present": title_present,
                "target_present": target_present,
                "exact_href": exact_href,
                "indexable": indexable,
                "verified": verified,
            }
            probes.append(probe)
            if verified:
                return {
                    "ok": True,
                    "verified": True,
                    "platform": platform,
                    "public_url": str(response.url),
                    "probe": probe,
                    "error": None,
                }
        except Exception as exc:
            probes.append({
                "url": str(source_url),
                "verified": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
    return {
        "ok": True,
        "verified": False,
        "platform": platform,
        "probes": probes,
        "error": None,
    }


def _verify_pending_guest_publications(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    due = []
    demoted_unconfirmed = []
    touched_files: dict[Path, list] = {}
    files = sorted(GUEST_PUBLICATIONS_DIR.glob("guest_publications_*.json"))
    for path in files:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            if row.get("submitted") and not _guest_submission_evidence_valid(row):
                updated = dict(row)
                updated["submitted"] = False
                updated["verified"] = False
                updated["checkpoint"] = "unconfirmed_submit_response"
                updated["submission_evidence_invalid"] = True
                updated["last_checked_at"] = now.isoformat()
                updated["last_verification"] = {
                    "ok": False,
                    "verified": False,
                    "error": "unconfirmed_submit_response",
                }
                rows[idx] = updated
                touched_files[path] = rows
                platform = str(updated.get("platform") or "guest_external")
                demoted_unconfirmed.append(platform)
                marketplace.record_attempt(
                    platform=platform,
                    action="submission_evidence_reconcile",
                    status_value="failed",
                    url=str(updated.get("final_url") or updated.get("source_form") or "") or None,
                    error_code="unconfirmed_submit_response",
                    error_detail=(
                        "POST returned to the same authoring form without an explicit "
                        "acceptance/moderation confirmation or public URL."
                    ),
                    meta={"guest_submit_evidence_truth": True},
                )
                continue
            if _guest_publication_due(row, now):
                due.append((path, rows, idx, row))

    attempts = []
    verified_platforms = []
    for path, rows, idx, row in due[:MAX_GUEST_PUBLICATION_VERIFICATIONS_PER_RUN]:
        result = _verify_guest_publication(row)
        updated = dict(row)
        updated["last_checked_at"] = now.isoformat()
        updated["verification_checks"] = int(updated.get("verification_checks") or 0) + 1
        updated["last_verification"] = result
        if result.get("verified"):
            updated["verified"] = True
            updated["verified_at"] = now.isoformat()
            updated["public_url"] = result.get("public_url")
            verified_platforms.append(str(updated.get("platform") or ""))
            marketplace.upsert_verified_external_publication(
                platform=str(updated.get("platform") or "guest_external"),
                url=str(updated.get("public_url") or ""),
                target_url=str(updated.get("target_url") or ""),
                title=str(updated.get("title") or "") or None,
                posted_at=updated.get("at"),
                verified_at=updated.get("verified_at"),
                listing_url=(result.get("probe") or {}).get("url"),
                verification=result,
                source="guest_moderation_self_heal",
            )
            marketplace.record_attempt(
                platform=str(updated.get("platform") or "unknown"),
                action="verify_publication",
                status_value="verified",
                url=str(updated.get("public_url") or ""),
                error_code=None,
                error_detail=None,
                meta={
                    "guest_moderation_self_heal": True,
                    "target_url": updated.get("target_url"),
                    "title": updated.get("title"),
                },
            )
        rows[idx] = updated
        touched_files[path] = rows
        attempts.append({
            "platform": updated.get("platform"),
            "verified": bool(result.get("verified")),
            "public_url": result.get("public_url"),
            "error": result.get("error"),
        })

    for path, rows in touched_files.items():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    return {
        "due": len(due),
        "attempted": len(attempts),
        "demoted_unconfirmed": demoted_unconfirmed,
        "remaining_due": max(0, len(due) - len(attempts)),
        "verified": verified_platforms,
        "attempts": attempts,
        "resubmissions": 0,
    }


def _budget_allows_start(deadline_monotonic: float | None, reserve_seconds: int) -> bool:
    if deadline_monotonic is None:
        return True
    return monotonic() <= deadline_monotonic - max(0, int(reserve_seconds))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


# CROWD_SEO_TRANSIENT_RULE_AUDIT_SELFHEAL_V1
# The onboarding worker also retries infrastructure-only rule-audit failures
# so the slower full guardian cadence cannot strand a usable forum.
def _transient_rule_audit_due(platform: str, now: datetime) -> bool:
    row = platform_rules.latest(platform)
    if not row or not guardian._audit_has_transient_transport_error(row):
        return False
    checked = _parse_iso(row.get("checked_at"))
    if checked is None:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return now - checked >= guardian.TRANSIENT_AUDIT_RETRY_INTERVAL


def _retry_transient_rule_audits(now: datetime, *, deadline_monotonic: float | None = None) -> dict:
    candidates = []
    for platform in marketplace.list_platforms():
        key = str(platform.get("key") or "")
        if not key or not _transient_rule_audit_due(key, now):
            continue
        previous = platform_rules.latest(key) or {}
        checked = _parse_iso(previous.get("checked_at"))
        candidates.append((checked or datetime.min.replace(tzinfo=timezone.utc), key))

    candidates.sort(key=lambda item: item[0])
    attempts = []
    progressed = []
    budget_exhausted = False
    for _, key in candidates[:MAX_TRANSIENT_RULE_AUDITS_PER_RUN]:
        if not _budget_allows_start(deadline_monotonic, RULE_AUDIT_START_RESERVE_SECONDS):
            budget_exhausted = True
            break
        try:
            row = platform_rules.inspect_platform(key)
            still_transient = guardian._audit_has_transient_transport_error(row)
            attempts.append({
                "platform": key,
                "decision": row.get("decision"),
                "transient_error": still_transient,
                "errors": len(row.get("errors") or []),
            })
            if not still_transient:
                progressed.append(key)
        except Exception as exc:
            attempts.append({
                "platform": key,
                "decision": None,
                "transient_error": True,
                "errors": 1,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "due": len(candidates),
        "attempted": len(attempts),
        "remaining_due": max(0, len(candidates) - len(attempts)),
        "budget_exhausted": budget_exhausted,
        "progressed": progressed,
        "attempts": attempts,
    }


def _acquire_lock(path: Path = WORKER_LOCK_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def _verification_interval_for_checkpoint(checkpoint: str | None) -> timedelta:
    checkpoint = str(checkpoint or "")
    if checkpoint in {
        "email_verification_required",
        "sms_or_verification_code_required",
        "phone_sms_required",
    }:
        return VERIFY_INTERVAL
    if checkpoint == "manual_verification":
        return MANUAL_BROWSER_VERIFY_INTERVAL
    if checkpoint in {
        "captcha_required",
        "captcha_age_and_terms_required",
        "captcha_age_declaration_required",
        "registration_agreement_required",
        "terms_acceptance_required",
        "age_and_terms_declaration_required",
        "age_declaration_required",
        "business_email_required",
        "real_identity_required",
        "business_email_and_real_identity_required",
        "external_account_sso_required",
        "truthful_company_identity_required",
    }:
        return PASSIVE_HUMAN_VERIFY_INTERVAL
    return VERIFY_INTERVAL


def _verification_due(row: dict | None, now: datetime) -> bool:
    if not row:
        return False
    if marketplace.registration_is_terminally_blocked(row):
        return False
    if str(row.get("status") or "") != "verification_required":
        return False
    checkpoint = str(row.get("checkpoint") or "")
    if checkpoint not in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS:
        return False
    updated = _parse_iso(row.get("updated_at"))
    interval = _verification_interval_for_checkpoint(checkpoint)
    return updated is None or now - updated >= interval


def _priority_key(item: dict, priority: dict[str, dict], reg_state: dict[str, dict]):
    key = str(item.get("platform") or "")
    p = priority.get(key) or {}
    urgent = bool(p.get("urgent_for_active_projects"))
    relevant_projects = int(p.get("relevant_projects") or 0)
    potential_slots = int(p.get("potential_slots") or 0)
    iks_tier_rank = int(p.get("max_iks_tier_rank") or 0)
    iks = int(p.get("max_iks") or 0)
    updated = _parse_iso((reg_state.get(key) or {}).get("updated_at"))
    oldest = updated.timestamp() if updated else 0.0
    return (
        0 if urgent else 1,
        -iks_tier_rank,
        -iks,
        -relevant_projects,
        -potential_slots,
        oldest,
        int(item.get("priority") or 999999),
        key,
    )


def _record_verification_transport_failure(platform: str, error: str) -> None:
    error = marketplace.redact_sensitive_text(error) or "verification_failed"
    current = next(
        (x for x in marketplace.registration_plan() if x.get("platform") == platform),
        {},
    )
    checkpoint = str(current.get("checkpoint") or "verification_required")
    status = str(current.get("status") or "verification_required")
    if status not in {"verification_required", "warming"}:
        status = "verification_required"
    marketplace.upsert_registration(
        platform,
        status,
        email=current.get("email"),
        account_url=current.get("account_url"),
        checkpoint=checkpoint,
        last_error=f"Временная ошибка автоматической проверки: {error}",
    )
    marketplace.record_attempt(
        platform=platform,
        action="onboarding_verify",
        status_value="failed",
        error_code="verification_transport_error",
        error_detail=error,
    )


def _scope_candidates_to_active_projects(candidates: list[dict], priority: dict[str, dict]) -> list[dict]:
    if not priority:
        return list(candidates)
    return [
        item for item in candidates
        if str(item.get("platform") or "") in priority
    ]


def _has_active_contract_demand(projects: list[dict] | None = None) -> bool:
    """Fence reusable onboarding only for a real active client contract."""
    rows = crowd_seo.list_projects() if projects is None else projects
    for project in rows:
        if not crowd_seo.project_is_active(project):
            continue
        if str(project.get("plan_mode") or "") == "inventory":
            continue
        if crowd_seo._project_required_count(project) > 0:
            return True
    return False


def _effective_bootstrap_priority(
    priority: dict[str, dict],
    projects: list[dict] | None = None,
) -> dict[str, dict]:
    if not _has_active_contract_demand(projects):
        return {}
    return {
        key: row for key, row in priority.items()
        if bool(row.get("urgent_for_active_projects"))
    }


def _verify(platform: str) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, ASSISTANT_SCRIPT, "verify-registration", "--platform", platform],
            cwd="/root/BORIS/backend",
            capture_output=True,
            text=True,
            timeout=70,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _record_verification_transport_failure(platform, "verification_timeout")
        current = next(
            (x for x in marketplace.registration_plan() if x.get("platform") == platform),
            {},
        )
        return {
            "platform": platform,
            "returncode": 124,
            "ok": False,
            "status": "verification_required",
            "checkpoint": current.get("checkpoint") or "verification_required",
            "submitted": False,
            "error": "verification_timeout",
        }
    payload = None
    try:
        payload = json.loads((proc.stdout or "").strip()) if (proc.stdout or "").strip() else None
    except Exception:
        payload = None

    raw_error = None if proc.returncode == 0 else ((proc.stderr or proc.stdout or "")[-1000:] or "verification_failed")
    error = marketplace.redact_sensitive_text(raw_error) if raw_error is not None else None
    if proc.returncode != 0:
        # A browser/network failure must not keep the same oldest timestamp and
        # monopolise the top of the worker queue on every run. Preserve the
        # human checkpoint, record the transient failure and move it to the end
        # of the due queue until the next hourly pass.
        _record_verification_transport_failure(platform, error or "verification_failed")

    return {
        "platform": platform,
        "returncode": proc.returncode,
        "ok": bool((payload or {}).get("ok")),
        "status": (payload or {}).get("status"),
        "checkpoint": (payload or {}).get("checkpoint"),
        "submitted": bool((payload or {}).get("submitted")),
        "error": error,
    }


def _write_status(snapshot: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_FILE)


def _skipped_snapshot(status: str) -> dict:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "owner_action_required": False,
        "captcha_bypass": False,
        "legal_consent_automatic": False,
        "policy": {
            "role": "platform_onboarding_worker",
            "scope": "lock_observability_only_no_provider_mutation",
            "paid_ai_calls": 0,
        },
    }


def main() -> int:
    worker_lock = _acquire_lock()
    if worker_lock is None:
        snapshot = _skipped_snapshot("skipped_worker_already_running")
        _write_status(snapshot)
        print(json.dumps(snapshot, ensure_ascii=False))
        return 0

    # Share the Guardian mutation lock. Never verify accounts while the full
    # Guardian is registering or publishing, otherwise duplicate actions are possible.
    guard_lock = guardian._acquire_run_lock()
    if guard_lock is None:
        snapshot = _skipped_snapshot("skipped_guardian_busy")
        _write_status(snapshot)
        print(json.dumps(snapshot, ensure_ascii=False))
        return 0

    run_started_monotonic = monotonic()
    run_deadline_monotonic = run_started_monotonic + RUN_BUDGET_SECONDS
    now = datetime.now(timezone.utc)
    # Read-only moderation recovery runs under the same Guardian mutation lock.
    # It never re-submits a guest form; it only verifies already-submitted rows.
    confirmed_guest_publications = _sync_confirmed_guest_publications()
    external_publication_recheck = _recheck_external_publications(now)
    guest_publications = _verify_pending_guest_publications(now)
    # P1 before P2: external checkpoint verification is the direct delivery
    # path. Transient rule audits consume only whatever run budget remains
    # after the due onboarding batch has been checked.
    queue = marketplace.platform_bootstrap_queue()
    priority = crowd_seo.bootstrap_priority_snapshot()
    # Inventory relevance is a soft sorting signal. Only real client-contract
    # urgency may hard-scope the reusable queue and exclude reserve platforms.
    scope_priority = _effective_bootstrap_priority(priority)
    reg_state = {x.get("platform"): x for x in marketplace.registration_plan()}

    candidates = [
        item for item in (queue.get("items") or [])
        if _verification_due(reg_state.get(item.get("platform")), now)
    ]
    # Only a real active client contract may fence reusable onboarding.
    # Internal inventory projects must not starve unrelated goods/services
    # BOOTSTRAP while the reusable reserve is still growing.
    candidates = _scope_candidates_to_active_projects(candidates, scope_priority)
    candidates.sort(key=lambda item: _priority_key(item, priority, reg_state))

    attempts = []
    progressed = []
    budget_exhausted = False
    for item in candidates[:MAX_VERIFICATIONS_PER_RUN]:
        if not _budget_allows_start(run_deadline_monotonic, VERIFY_START_RESERVE_SECONDS):
            budget_exhausted = True
            break
        key = str(item.get("platform") or "")
        try:
            result = _verify(key)
        except subprocess.TimeoutExpired:
            _record_verification_transport_failure(key, "verification_timeout")
            result = {
                "platform": key,
                "returncode": 124,
                "ok": False,
                "status": "verification_required",
                "checkpoint": (reg_state.get(key) or {}).get("checkpoint") or "verification_required",
                "submitted": False,
                "error": "verification_timeout",
            }
        attempts.append(result)
        if result.get("status") in {"ready", "warming"}:
            progressed.append(key)

    transient_rule_audits = _retry_transient_rule_audits(
        now, deadline_monotonic=run_deadline_monotonic
    )
    budget_exhausted = bool(
        budget_exhausted or transient_rule_audits.get("budget_exhausted")
    )

    crowd_cycle = None
    if progressed or transient_rule_audits.get("progressed"):
        # A cleared checkpoint or recovered rule audit should immediately flow
        # into the existing client Crowd SEO chain; no owner click and no wait
        # for the 6-hour full Guardian.
        crowd_cycle = guardian.run_crowd_seo_cycle(ASSISTANT_SCRIPT)

    snapshot = {
        "at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "queue_needed": queue.get("needed"),
        "due": len(candidates),
        "attempted": len(attempts),
        "remaining_due": max(0, len(candidates) - len(attempts)),
        "budget_exhausted": budget_exhausted,
        "run_elapsed_seconds": round(monotonic() - run_started_monotonic, 3),
        "progressed": progressed,
        "attempts": attempts,
        "transient_rule_audits": transient_rule_audits,
        "confirmed_guest_publications": confirmed_guest_publications,
        "external_publication_recheck": external_publication_recheck,
        "guest_publications": guest_publications,
        "crowd_cycle": crowd_cycle,
        "owner_action_required": False,
        "captcha_bypass": False,
        "legal_consent_automatic": False,
        "policy": {
            "role": "platform_onboarding_worker",
            "scope": "detect_completed_external_checkpoint_retry_transient_rule_audit_then_resume",
            "verify_interval_hours": int(VERIFY_INTERVAL.total_seconds() // 3600),
            "manual_browser_verify_interval_hours": int(MANUAL_BROWSER_VERIFY_INTERVAL.total_seconds() // 3600),
            "passive_human_verify_interval_hours": int(PASSIVE_HUMAN_VERIFY_INTERVAL.total_seconds() // 3600),
            "transient_rule_retry_hours": int(guardian.TRANSIENT_AUDIT_RETRY_INTERVAL.total_seconds() // 3600),
            "max_transient_rule_audits_per_run": MAX_TRANSIENT_RULE_AUDITS_PER_RUN,
            "max_verifications_per_run": MAX_VERIFICATIONS_PER_RUN,
            "run_budget_seconds": RUN_BUDGET_SECONDS,
            "verify_start_reserve_seconds": VERIFY_START_RESERVE_SECONDS,
            "rule_audit_start_reserve_seconds": RULE_AUDIT_START_RESERVE_SECONDS,
            "paid_ai_calls": 0,
        },
    }
    _write_status(snapshot)
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
