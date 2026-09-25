from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "workstream_scope_registry.json"

EXPECTED_CHAT_BOUNDARIES = {
    "Автопостинг BORIS": ("social_reliability_core", "social_runtime_only"),
    "Обновление автопостинга BORIS": ("social_legacy_autopost_chat", "read_only_alias"),
    "Проверка VK Океан": ("social_editorial_client_config", "client_editorial_only"),
    "Публикации без видео": ("social_editorial_client_config", "client_editorial_only"),
    "Проверка автопостинга бытовок": ("planb_client_operations", "client_operations_only"),
    "BORIS Production Watch / Production Buildout": ("production_watch_global", "observer_only"),
}


def social_workstream_boundary_health(path: Path | None = None) -> dict:
    target = path or REGISTRY
    violations: list[dict] = []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "violations": [{"reason": "registry_unreadable", "error": type(exc).__name__}],
            "registry": str(target),
        }

    workstreams = data.get("workstreams") or {}
    coordination = workstreams.get("workstream_coordination") or {}
    rows = coordination.get("neighbor_chat_map") or []
    by_chat: dict[str, list[dict]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        chat = str(raw.get("chat") or "").strip()
        if chat:
            by_chat.setdefault(chat, []).append(raw)

    for chat, (owner, mode) in EXPECTED_CHAT_BOUNDARIES.items():
        matches = by_chat.get(chat) or []
        if len(matches) != 1:
            violations.append({
                "chat": chat,
                "reason": "chat_mapping_count",
                "expected": 1,
                "actual": len(matches),
            })
            continue
        row = matches[0]
        actual_owner = str(row.get("owner") or "").strip()
        actual_mode = str(row.get("mode") or "").strip()
        if actual_owner != owner:
            violations.append({
                "chat": chat,
                "reason": "owner_mismatch",
                "expected": owner,
                "actual": actual_owner,
            })
        if actual_mode != mode:
            violations.append({
                "chat": chat,
                "reason": "mode_mismatch",
                "expected": mode,
                "actual": actual_mode,
            })

    legacy = workstreams.get("social_legacy_autopost_chat") or {}
    if legacy.get("status") != "read_only_alias":
        violations.append({
            "reason": "legacy_status_not_read_only",
            "actual": legacy.get("status"),
        })
    if list(legacy.get("writable_paths") or []):
        violations.append({
            "reason": "legacy_has_writable_paths",
            "actual": legacy.get("writable_paths"),
        })

    social = workstreams.get("social_reliability_core") or {}
    social_writable = list(social.get("writable_paths") or [])
    for forbidden in (
        "client-data/u2/social-editorial/**",
        "/usr/local/share/boris-editorial-client-v3.json",
        "/usr/local/sbin/boris-editorial-client-guard.py",
    ):
        if forbidden in social_writable:
            violations.append({
                "reason": "social_runtime_claims_editorial_path",
                "path": forbidden,
            })

    editorial = workstreams.get("social_editorial_client_config") or {}
    editorial_writable = list(editorial.get("writable_paths") or [])
    for forbidden in (
        "posting_runner.py",
        "boris_background_worker.py",
        "app/api/posting.py",
        "app/services/social_*",
    ):
        if forbidden in editorial_writable:
            violations.append({
                "reason": "editorial_claims_social_runtime_path",
                "path": forbidden,
            })

    return {
        "ok": not violations,
        "version": data.get("version"),
        "registry": str(target),
        "violations": violations,
        "canonical_runtime_chat": "Автопостинг BORIS",
        "legacy_chat": "Обновление автопостинга BORIS",
    }
