#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
MAX_VERIFICATIONS_PER_RUN = 12
MAX_TRANSIENT_RULE_AUDITS_PER_RUN = 6


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


# CROWD_SEO_TRANSIENT_RULE_AUDIT_SELFHEAL_V1
# The 2-hour onboarding worker also retries infrastructure-only rule-audit
# failures so the 6-hour full guardian cadence cannot strand a usable forum.
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


def _retry_transient_rule_audits(now: datetime) -> dict:
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
    for _, key in candidates[:MAX_TRANSIENT_RULE_AUDITS_PER_RUN]:
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
    return updated is None or now - updated >= VERIFY_INTERVAL


def _priority_key(item: dict, priority: dict[str, dict], reg_state: dict[str, dict]):
    key = str(item.get("platform") or "")
    p = priority.get(key) or {}
    urgent = bool(p.get("urgent_for_active_projects"))
    relevant_projects = int(p.get("relevant_projects") or 0)
    potential_slots = int(p.get("potential_slots") or 0)
    updated = _parse_iso((reg_state.get(key) or {}).get("updated_at"))
    oldest = updated.timestamp() if updated else 0.0
    return (
        0 if urgent else 1,
        -relevant_projects,
        -potential_slots,
        oldest,
        int(item.get("priority") or 999999),
        key,
    )


def _record_verification_transport_failure(platform: str, error: str) -> None:
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

    error = None if proc.returncode == 0 else ((proc.stderr or proc.stdout or "")[-1000:] or "verification_failed")
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


def main() -> int:
    worker_lock = _acquire_lock()
    if worker_lock is None:
        print(json.dumps({"status": "skipped_worker_already_running"}, ensure_ascii=False))
        return 0

    # Share the Guardian mutation lock. Never verify accounts while the full
    # Guardian is registering or publishing, otherwise duplicate actions are possible.
    guard_lock = guardian._acquire_run_lock()
    if guard_lock is None:
        print(json.dumps({"status": "skipped_guardian_busy"}, ensure_ascii=False))
        return 0

    now = datetime.now(timezone.utc)
    transient_rule_audits = _retry_transient_rule_audits(now)
    queue = marketplace.platform_bootstrap_queue()
    priority = crowd_seo.bootstrap_priority_snapshot()
    reg_state = {x.get("platform"): x for x in marketplace.registration_plan()}

    candidates = [
        item for item in (queue.get("items") or [])
        if _verification_due(reg_state.get(item.get("platform")), now)
    ]
    # ACTIVE_CLIENT_BOOTSTRAP_FENCE_V1
    # While a live Crowd SEO project still has human-gated onboarding, spend
    # this worker cycle only on platforms that unlock its actual target slots.
    # Strategic reserve onboarding resumes automatically when no active-project
    # bootstrap demand remains.
    candidates = _scope_candidates_to_active_projects(candidates, priority)
    candidates.sort(key=lambda item: _priority_key(item, priority, reg_state))

    attempts = []
    progressed = []
    for item in candidates[:MAX_VERIFICATIONS_PER_RUN]:
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
        "progressed": progressed,
        "attempts": attempts,
        "transient_rule_audits": transient_rule_audits,
        "crowd_cycle": crowd_cycle,
        "owner_action_required": False,
        "captcha_bypass": False,
        "legal_consent_automatic": False,
        "policy": {
            "role": "platform_onboarding_worker",
            "scope": "detect_completed_external_checkpoint_retry_transient_rule_audit_then_resume",
            "verify_interval_hours": int(VERIFY_INTERVAL.total_seconds() // 3600),
            "transient_rule_retry_hours": int(guardian.TRANSIENT_AUDIT_RETRY_INTERVAL.total_seconds() // 3600),
            "max_transient_rule_audits_per_run": MAX_TRANSIENT_RULE_AUDITS_PER_RUN,
            "max_verifications_per_run": MAX_VERIFICATIONS_PER_RUN,
            "paid_ai_calls": 0,
        },
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_FILE)
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
