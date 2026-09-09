from __future__ import annotations

# Authoritative Crowd SEO inventory policy.
# Commercial volume is never defined here: every client project carries the
# exact quantity purchased in its order. These targets describe only the
# reusable BORIS platform pool that should keep growing across all clients.
STRATEGIC_UNIQUE_TARGET = 200
STRATEGIC_PER_FORMAT_TARGET = 100
SUPPORTED_FORMATS = ("goods", "services")


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
    }
