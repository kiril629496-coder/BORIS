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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import crowd_seo
from app.services import platform_rules
from app.services import service_marketplace as marketplace
GUARD_STATUS = ROOT / "data/service_marketplaces/guardian_status.json"
HARD_EXCLUDED = {
    "n8n_jobs": "automated_access_prohibited",
    "weweb_jobs": "automated_access_prohibited",
    "airtable_jobs": "commercial_use_prohibited",
    "bubble_jobs": "automated_access_not_verified",
}
MAX_GUARD_AGE_SEC = 8 * 3600


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
    ok = bool(
        ts is not None
        and age is not None
        and age <= MAX_GUARD_AGE_SEC
        and not (crowd.get("errors") or [])
        and int((row.get("crowd_capacity") or {}).get("max_discovery_deficit_after_warmup_after") or 0) == 0
    )
    return {
        "ok": ok,
        "at": row.get("at"),
        "age_sec": round(age, 1) if age is not None else None,
        "crowd_errors": crowd.get("errors") or [],
        "capacity_after": capacity,
        "reason": None if ok else "guardian_not_fresh_or_has_errors_or_capacity_deficit",
    }


def _timer_state() -> dict:
    rc_enabled, enabled = _systemctl("is-enabled", "boris-forum-acquisition-guard.timer")
    rc_active, active = _systemctl("is-active", "boris-forum-acquisition-guard.timer")
    return {
        "ok": rc_enabled == 0 and enabled == "enabled" and rc_active == 0 and active == "active",
        "enabled": enabled,
        "active": active,
    }


def _project_row(project: dict, capacity: dict) -> dict:
    required = int(capacity.get("required") or 0)
    matches = crowd_seo._forum_matches(
        project.get("niche") or "business",
        limit=max(100, max(1, required) * 5),
        keywords=project.get("keywords") or [],
        offer_type=crowd_seo._legacy_offer_type(project),
    )

    # CERTIFIER_TARGET_PLAN_V2
    # The client buys a target count, not every reserve surface BORIS knows.
    # Validate/report the first strict target slots as the critical path and
    # expose extra eligible surfaces separately as reserve. This keeps a bad
    # reserve forum from falsely failing an otherwise complete explicit-order plan.
    selected = matches[:required] if required > 0 else []
    reserve = matches[required:] if required > 0 else list(matches)

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
    infrastructure_ok = capacity_ok and rules_ok
    # Inventory projects are reusable pool growth, not a paid delivery contract.
    # With no explicit client quantity they must neither create a fake package
    # deficit nor claim that external publication delivery is owed.
    delivery_ready = infrastructure_ok and (is_inventory or selected_ready >= required)

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
        "eligible_total": eligible,
        "selected_slots": len(selected),
        "reserve_slots": len(reserve),
        "ready_selected": selected_ready,
        "ready_total": ready_total,
        "service_reachable_after_warmup": reachable_after_warmup,
        "discovery_deficit_after_warmup": discovery_deficit_after_warmup,
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
