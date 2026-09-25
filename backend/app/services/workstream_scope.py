from __future__ import annotations

import fnmatch
import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "workstream_scope_registry.json"

# Paths that must always have exactly one canonical writer. This closes the
# gap where a registry could have zero overlaps but still leave shared email
# runtime files unowned and therefore easy to edit from multiple chats.
REQUIRED_SINGLE_WRITERS = {
    "posting_runner.py": "social_reliability_core",
    "boris_background_worker.py": "social_reliability_core",
    "app/services/incident_reconciler.py": "social_reliability_core",
    "email_queue_runner.py": "email_outreach_core",
    "app/services/contact_outreach.py": "email_outreach_core",
    "app/services/email_queue.py": "email_outreach_core",
    "app/services/email_service.py": "email_outreach_core",
    "app/services/client_mailboxes.py": "email_outreach_core",
    "app/services/prospect_campaigns.py": "email_outreach_core",
    "app/services/prospect_reporting.py": "email_outreach_core",
    "app/services/owner_outreach_policy.py": "email_outreach_core",
    "app/services/owner_outreach_volume_safety.py": "email_outreach_core",
    "app/api/prospecting_scale.py": "email_outreach_core",
    "scripts/install_owner_outreach_db_safety_v3.py": "email_outreach_core",
    "scripts/qa_owner_outreach_volume_safety.py": "email_outreach_core",
    "scripts/qa_sales_outreach_safety.py": "email_outreach_core",
    "app/services/contact_prospector.py": "prospecting_discovery_core",
    "app/services/prospect_discovery.py": "prospecting_discovery_core",
    "app/services/prospecting.py": "prospecting_discovery_core",
    "app/services/prospect_replenisher.py": "prospecting_discovery_core",
    "app/api/prospecting.py": "prospecting_discovery_core",
    "app/services/email_tracking.py": "email_tracking_analytics",
    "app/services/owner_outreach_metrics.py": "email_tracking_analytics",
    "scripts/qa_owner_outreach_metrics.py": "email_tracking_analytics",
    "app/services/email_outreach_cost_policy.py": "email_outreach_economics",
    "../frontend/app/email-outreach/page.tsx": "frontend_mobile_ui",
    "app/usage.py": "social_cost_accounting",
    "daily_stats_collector.py": "avito_integration_core",
    "portfolio_bootstrap_runner.py": "avito_marketing_core",
    "app/services/initial_portfolio.py": "avito_marketing_core",
    "app/services/marketing_signal_guard.py": "avito_money_core",
    "app/services/marketing_clock.py": "avito_money_core",
    "app/api/avito.py": "avito_integration_core",
    "app/services/avito_account_throttle.py": "avito_integration_core",
    "app/api/avito_categories.py": "avito_marketing_core",
    "app/models/avito_category.py": "avito_marketing_core",
    "app/services/avito_position_monitor.py": "avito_marketing_core",
    "app/services/avito_premium_compositor.py": "avito_marketing_core",
    "ai_marketer_hourly.sh": "avito_money_core",
    "app/api/cpx_advisor.py": "avito_money_core",
    "cpx_advisor_runner.py": "avito_money_core",
    "cpx_budget_brake.py": "avito_money_core",
    "cpx_budget_resume.py": "avito_money_core",
    "cpx_cap_reconciler.py": "avito_money_core",
    "effect_check.py": "avito_money_core",
    "late_day_spend_controller.py": "avito_money_core",
    "marketer_rollout_runner.py": "avito_money_core",
    "kpi_goal_runner.py": "avito_money_core",
    "app/services/marketing_experiment_journal.py": "avito_money_core",
    "app/services/marketing_money_policy.py": "avito_money_core",
    "qa_marketer_reach_safety.py": "avito_money_core",
    "qa_marketer_rollout.py": "avito_money_core",
    "app/ext_api/dev.py": "workstream_coordination",
    "app/ext_api/repo.py": "workstream_coordination",
    "app/ext_api/a2a.py": "autonomous_dev_execution",
    "app/ext_api/executor.py": "autonomous_dev_execution",
    "app/ext_api/lead.py": "autonomous_dev_execution",
    "app/ext_api/recover.py": "autonomous_dev_execution",
    "app/ext_api/devflow.py": "autonomous_dev_execution",
    "app/ext_api/aiprov.py": "autonomous_dev_execution",
    "app/ext_api/banner.py": "banner_media_core",
    "app/api/media.py": "banner_media_core",
    "app/services/media_semantic_validator.py": "banner_media_core",
    "app/services/media_service.py": "banner_media_core",
    "app/services/media_storage.py": "banner_media_core",
    "app/services/photo_studio.py": "banner_media_core",
    "app/services/image_service.py": "banner_media_core",
    "app/services/banner_design_v4.py": "banner_media_core",
    "app/api/photo_factory.py": "photo_factory_core",
    "app/api/photo_factory_v3.py": "photo_factory_core",
    "app/api/photo_factory_v31.py": "photo_factory_core",
    "app/api/photo_factory_v32.py": "photo_factory_core",
    "app/api/photo_factory_v33.py": "photo_factory_core",
}

# A neighboring chat is not only assigned to one owner; its operating mode must
# also match that owner. Otherwise a chat can keep the same owner label while
# silently changing from read-only/review into runtime work.
NEIGHBOR_CHAT_OWNER_MODES = {
    "workstream_coordination": "coordination_only",
    "brain_control_plane_surface": "surface_review_only",
    "clients_control_surface": "surface_review_only",
    "development_control_surface": "surface_review_only",
    "autonomous_dev_execution": "execution_only",
    "avito_marketing_core": "avito_content_only",
    "avito_money_core": "avito_money_only",
    "banner_media_core": "client_media_only",
    "email_copy_review": "review_only",
    "prospecting_discovery_core": "discovery_only",
    "email_outreach_core": "runtime_only",
    "email_tracking_analytics": "analytics_only",
    "email_outreach_economics": "economics_only",
    "frontend_mobile_ui": "frontend_only",
    "ocean_social_client": "client_social_only",
    "photo_factory_core": "photo_factory_only",
    "planb_client_operations": "client_operations_only",
    "production_watch_global": "observer_only",
    "social_legacy_autopost_chat": "read_only_alias",
    "social_reliability_core": "social_runtime_only",
    "telephony_core": "telephony_only",
}


class ScopeError(RuntimeError):
    pass


def load_registry(path: Path | None = None) -> dict:
    target = path or REGISTRY_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("policy") != "single_writer_by_workstream":
        raise ScopeError("unsupported_workstream_policy")
    if not isinstance(data.get("workstreams"), dict):
        raise ScopeError("workstreams_missing")
    return data


def normalize_scope_path(value: str) -> str:
    """Normalize BORIS repo spellings to the backend-relative registry form.

    Dev jobs commonly store paths as ``backend/app/...`` while direct tools
    usually operate from ``/root/BORIS/backend`` and the registry stores
    ``app/...``. Without normalization, the same physical file can look
    "unowned" simply because another chat used a different spelling.
    """
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        return ""
    while path.startswith("./"):
        path = path[2:]

    if path.startswith("/root/BORIS/backend/"):
        path = path[len("/root/BORIS/backend/"):]
    elif path == "/root/BORIS/backend":
        path = ""
    elif path.startswith("/root/BORIS/frontend/"):
        path = "../frontend/" + path[len("/root/BORIS/frontend/"):]
    elif path == "/root/BORIS/frontend":
        path = "../frontend"
    elif path.startswith("/root/BORIS/clients/"):
        path = "../clients/" + path[len("/root/BORIS/clients/"):]
    elif path == "/root/BORIS/clients":
        path = "../clients"
    elif path.startswith("backend/"):
        path = path[len("backend/"):]
    elif path == "backend":
        path = ""
    elif path.startswith("frontend/"):
        path = "../frontend/" + path[len("frontend/"):]
    elif path == "frontend":
        path = "../frontend"
    elif path.startswith("clients/"):
        path = "../clients/" + path[len("clients/"):]
    elif path == "clients":
        path = "../clients"

    while "//" in path:
        path = path.replace("//", "/")
    if path not in ("/", ""):
        path = path.rstrip("/")
    return path


def _matches(pattern: str, path: str) -> bool:
    pattern = normalize_scope_path(pattern)
    path = normalize_scope_path(path)
    if not pattern or not path:
        return False
    if pattern.endswith("/**"):
        return path == pattern[:-3].rstrip("/") or path.startswith(pattern[:-2])
    return fnmatch.fnmatch(path, pattern)


def writable_owners(path: str, registry: dict | None = None) -> list[str]:
    data = registry or load_registry()
    owners = []
    for scope, spec in data["workstreams"].items():
        for pattern in spec.get("writable_paths") or []:
            if _matches(pattern, path):
                owners.append(scope)
                break
    return owners


def _pattern_stem(pattern: str) -> str:
    """Return the literal prefix before the first glob metacharacter."""
    value = normalize_scope_path(pattern)
    cut = len(value)
    for token in ("*", "?", "["):
        pos = value.find(token)
        if pos >= 0:
            cut = min(cut, pos)
    return value[:cut].rstrip("/")


def scope_path_overlaps_pattern(path: str, pattern: str) -> bool:
    """Whether a concrete/file-tree claim can touch a registry writer pattern.

    Dev jobs often claim broad parent directories while the canonical registry
    intentionally assigns individual files/subtrees to separate workstreams.
    Exact fnmatch alone misses that dangerous parent claim, so ancestor
    containment is treated as overlap too.
    """
    normalized = normalize_scope_path(path)
    normalized_pattern = normalize_scope_path(pattern)
    if not normalized or not normalized_pattern:
        return False
    if _matches(normalized_pattern, normalized):
        return True
    stem = _pattern_stem(normalized_pattern)
    if not stem:
        return False
    return stem == normalized or stem.startswith(normalized.rstrip("/") + "/")


def classify_workstream_paths(paths: list[str], registry: dict | None = None) -> dict:
    """Classify a dev/chat file scope against canonical single-writer domains.

    ambiguous means one task can write into more than one canonical workstream
    and therefore must be decomposed or narrowed before execution. Unowned
    logical locks are reported without inventing a writer.
    """
    data = registry or load_registry()
    normalized_paths = []
    seen = set()
    for raw in paths or []:
        path = normalize_scope_path(raw)
        if path and path not in seen:
            normalized_paths.append(path)
            seen.add(path)

    matches = []
    owners: set[str] = set()
    for path in normalized_paths:
        per_path = []
        for scope, spec in data["workstreams"].items():
            for pattern in spec.get("writable_paths") or []:
                if scope_path_overlaps_pattern(path, str(pattern)):
                    per_path.append({"scope": scope, "pattern": str(pattern)})
                    owners.add(scope)
        matches.append({
            "path": path,
            "owners": sorted({x["scope"] for x in per_path}),
            "matches": per_path,
        })

    unowned = [x["path"] for x in matches if not x["owners"]]
    return {
        "paths": normalized_paths,
        "owners": sorted(owners),
        "ambiguous": len(owners) > 1,
        "unowned_paths": unowned,
        "matches": matches,
    }


def is_filesystem_scope_path(path: str) -> bool:
    """Distinguish file/tree claims from logical locks such as proxy/topic names."""
    value = normalize_scope_path(path)
    if not value or ":" in value:
        return False
    if "/" in value:
        return True
    suffixes = (
        ".py", ".sh", ".sql", ".json", ".yaml", ".yml", ".ts", ".tsx",
        ".js", ".jsx", ".kt", ".java", ".swift", ".go", ".rs", ".md",
    )
    return value in {"app", "tests", "run", "scripts", "frontend", "backend"} or value.endswith(suffixes)


def scope_claims_overlap(left: str, right: str) -> bool:
    """Return True when two filesystem claims can touch the same file/tree."""
    a = normalize_scope_path(left)
    b = normalize_scope_path(right)
    if not is_filesystem_scope_path(a) or not is_filesystem_scope_path(b):
        return False
    if a == b:
        return True
    if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    a_stem = _pattern_stem(a)
    b_stem = _pattern_stem(b)
    if not a_stem or not b_stem:
        return False
    return (
        a_stem == b_stem
        or a_stem.startswith(b_stem.rstrip("/") + "/")
        or b_stem.startswith(a_stem.rstrip("/") + "/")
    )


def unowned_scope_conflicts(
    jobs: list[dict],
    registry: dict | None = None,
) -> dict:
    """Find pairwise overlaps only among jobs not owned by any canonical writer.

    This does not guess ownership. It only proves that two still-unowned tasks
    can mutate the same filesystem tree, which is enough to fail closed until
    their scopes are narrowed or a canonical owner is declared.
    """
    data = registry or load_registry()
    candidates = []
    for raw in jobs or []:
        job = dict(raw or {})
        paths = [
            normalize_scope_path(x)
            for x in (job.get("paths") or [])
            if is_filesystem_scope_path(str(x or ""))
        ]
        classification = classify_workstream_paths(paths, data)
        if classification["owners"] or not paths:
            continue
        candidates.append({
            "id": job.get("id"),
            "title": job.get("title"),
            "status": job.get("status"),
            "paths": paths,
        })

    pairs = []
    conflicted: dict[object, set] = {}
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            hits = []
            for a in left["paths"]:
                for b in right["paths"]:
                    if scope_claims_overlap(a, b):
                        hits.append({"left": a, "right": b})
            if not hits:
                continue
            pairs.append({
                "left_id": left["id"],
                "right_id": right["id"],
                "overlaps": hits,
            })
            conflicted.setdefault(left["id"], set()).add(right["id"])
            conflicted.setdefault(right["id"], set()).add(left["id"])

    return {
        "candidate_count": len(candidates),
        "pair_count": len(pairs),
        "pairs": pairs,
        "conflicted_jobs": {
            key: sorted(value)
            for key, value in conflicted.items()
        },
    }


def owned_scope_conflicts(
    jobs: list[dict],
    registry: dict | None = None,
) -> dict:
    """Find overlapping jobs that resolve to the same canonical writer.

    Canonical ownership prevents cross-workstream writes, but two waiting jobs
    inside the same owner can still claim the exact same files. That is safe
    only while the scheduler serializes them. The coordination guard uses this
    pure report to keep exactly one runnable writer per overlapping component,
    so neighboring chats do not leave duplicate active work on the same path.
    Unowned tail paths are allowed here as long as the job resolves to exactly
    one canonical owner; this function never grants ownership to those paths.
    """
    data = registry or load_registry()
    candidates = []
    for raw in jobs or []:
        job = dict(raw or {})
        paths = [
            normalize_scope_path(x)
            for x in (job.get("paths") or [])
            if is_filesystem_scope_path(str(x or ""))
        ]
        if not paths:
            continue
        classification = classify_workstream_paths(paths, data)
        owners = classification.get("owners") or []
        if classification.get("ambiguous") or len(owners) != 1:
            continue
        candidates.append({
            "id": job.get("id"),
            "title": job.get("title"),
            "status": job.get("status"),
            "paths": paths,
            "owner": owners[0],
            "unowned_paths": classification.get("unowned_paths") or [],
        })

    pairs = []
    conflicted: dict[object, set] = {}
    owners_by_job = {
        item["id"]: item["owner"]
        for item in candidates
    }
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            if left["owner"] != right["owner"]:
                continue
            hits = []
            for a in left["paths"]:
                for b in right["paths"]:
                    if scope_claims_overlap(a, b):
                        hits.append({"left": a, "right": b})
            if not hits:
                continue
            pairs.append({
                "owner": left["owner"],
                "left_id": left["id"],
                "right_id": right["id"],
                "overlaps": hits,
            })
            conflicted.setdefault(left["id"], set()).add(right["id"])
            conflicted.setdefault(right["id"], set()).add(left["id"])

    return {
        "candidate_count": len(candidates),
        "pair_count": len(pairs),
        "pairs": pairs,
        "owners_by_job": owners_by_job,
        "conflicted_jobs": {
            key: sorted(value)
            for key, value in conflicted.items()
        },
    }


def registry_overlaps(registry: dict | None = None) -> list[dict]:
    data = registry or load_registry()
    samples: set[str] = set()
    # Exact/glob stems are sufficient for registry-level duplicate ownership.
    for spec in data["workstreams"].values():
        for pattern in spec.get("writable_paths") or []:
            samples.add(pattern)
    overlaps = []
    patterns = []
    for scope, spec in data["workstreams"].items():
        for pattern in spec.get("writable_paths") or []:
            patterns.append((scope, pattern))
    for i, (a_scope, a) in enumerate(patterns):
        for b_scope, b in patterns[i + 1:]:
            if a_scope == b_scope:
                continue
            # Reject exact duplicates and clear prefix/glob containment.
            a0 = a.replace("**", "").rstrip("*").rstrip("/")
            b0 = b.replace("**", "").rstrip("*").rstrip("/")
            if a == b or (a0 and b0 and (a0.startswith(b0) or b0.startswith(a0))):
                overlaps.append({
                    "scope_a": a_scope, "pattern_a": a,
                    "scope_b": b_scope, "pattern_b": b,
                })
    return overlaps


def assert_scope_paths(scope: str, paths: list[str], registry: dict | None = None) -> dict:
    data = registry or load_registry()
    if scope not in data["workstreams"]:
        raise ScopeError(f"unknown_scope:{scope}")
    foreign = []
    unowned = []
    for path in paths:
        owners = writable_owners(path, data)
        if not owners:
            unowned.append(path)
        elif scope not in owners:
            foreign.append({"path": path, "owners": owners})
    if foreign:
        raise ScopeError("foreign_scope_paths:" + json.dumps(foreign, ensure_ascii=False))
    return {"ok": True, "scope": scope, "paths": paths, "unowned": unowned}


def required_single_writer_contract(registry: dict | None = None) -> dict[str, str]:
    """Return the canonical writer contract from the registry.

    Python constants are only a backwards-compatible fallback for an older
    registry. The immutable registry is the single source of truth so parallel
    coordination work cannot make code/tests and production ownership drift.
    """
    data = registry or load_registry()
    coordination = (data.get("workstreams") or {}).get("workstream_coordination") or {}
    raw = coordination.get("required_single_writers")
    if isinstance(raw, dict) and raw:
        return {
            normalize_scope_path(str(path)): str(owner).strip()
            for path, owner in raw.items()
            if normalize_scope_path(str(path)) and str(owner).strip()
        }
    return dict(REQUIRED_SINGLE_WRITERS)


def neighbor_chat_owner_mode_contract(registry: dict | None = None) -> dict[str, str]:
    data = registry or load_registry()
    coordination = (data.get("workstreams") or {}).get("workstream_coordination") or {}
    raw = coordination.get("neighbor_chat_owner_modes")
    if isinstance(raw, dict) and raw:
        return {
            str(owner).strip(): str(mode).strip()
            for owner, mode in raw.items()
            if str(owner).strip() and str(mode).strip()
        }
    return dict(NEIGHBOR_CHAT_OWNER_MODES)


def single_writer_scope_health(registry: dict | None = None) -> dict:
    data = registry or load_registry()
    overlaps = registry_overlaps(data)
    required = required_single_writer_contract(data)
    wrong = []
    for path, expected in required.items():
        owners = writable_owners(path, data)
        if owners != [expected]:
            wrong.append({"path": path, "expected": expected, "owners": owners})
    return {
        "ok": not overlaps and not wrong,
        "policy": data.get("policy"),
        "overlaps": overlaps,
        "wrong_or_missing_single_writers": wrong,
        "required_count": len(required),
    }


def required_single_writer_violations(registry: dict | None = None) -> list[dict]:
    data = registry or load_registry()
    required = required_single_writer_contract(data)
    violations = []
    for path, expected_owner in required.items():
        owners = writable_owners(path, data)
        if owners != [expected_owner]:
            violations.append({
                "path": path,
                "expected_owner": expected_owner,
                "actual_owners": owners,
            })
    return violations


def neighbor_chat_map_health(registry: dict | None = None) -> dict:
    """Validate one canonical owner for each explicitly neighboring chat."""
    data = registry or load_registry()
    workstreams = data.get("workstreams") or {}
    coordination = workstreams.get("workstream_coordination") or {}
    rows = coordination.get("neighbor_chat_map") or []
    owner_modes = neighbor_chat_owner_mode_contract(data)
    violations = []
    seen: dict[str, str] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            violations.append({"index": index, "reason": "row_not_object"})
            continue
        chat = str(raw.get("chat") or "").strip()
        owner = str(raw.get("owner") or "").strip()
        mode = str(raw.get("mode") or "").strip()
        if not chat:
            violations.append({"index": index, "reason": "chat_missing"})
            continue
        if not owner:
            violations.append({"chat": chat, "reason": "owner_missing"})
            continue
        if owner not in workstreams:
            violations.append({
                "chat": chat,
                "owner": owner,
                "reason": "owner_unknown",
            })
        if not mode:
            violations.append({
                "chat": chat,
                "owner": owner,
                "reason": "mode_missing",
            })
        expected_mode = owner_modes.get(owner)
        if expected_mode and mode and mode != expected_mode:
            violations.append({
                "chat": chat,
                "owner": owner,
                "mode": mode,
                "expected_mode": expected_mode,
                "reason": "owner_mode_mismatch",
            })
        if chat in seen:
            violations.append({
                "chat": chat,
                "owner": owner,
                "previous_owner": seen[chat],
                "reason": "chat_mapped_more_than_once",
            })
        else:
            seen[chat] = owner
    return {
        "ok": not violations,
        "count": len(rows),
        "violations": violations,
        "mapping": dict(seen),
    }


def registry_health(registry: dict | None = None) -> dict:
    data = registry or load_registry()
    overlaps = registry_overlaps(data)
    writer_violations = required_single_writer_violations(data)
    chat_health = neighbor_chat_map_health(data)
    return {
        "ok": not overlaps and not writer_violations and chat_health["ok"],
        "policy": data.get("policy"),
        "version": data.get("version"),
        "overlaps": overlaps,
        "required_single_writer_violations": writer_violations,
        "neighbor_chat_map": chat_health,
    }


def social_reliability_scope_health() -> dict:
    data = load_registry()
    health = registry_health(data)
    core = data["workstreams"].get("social_reliability_core") or {}
    required = {
        "posting_runner.py",
        "boris_background_worker.py",
        "app/services/incident_reconciler.py",
    }
    owned = set(core.get("writable_paths") or [])
    missing = sorted(required - owned)
    return {
        "ok": health["ok"] and not missing,
        "policy": data.get("policy"),
        "overlaps": health["overlaps"],
        "required_single_writer_violations": health["required_single_writer_violations"],
        "missing_core_paths": missing,
        "scope": "social_reliability_core",
    }
