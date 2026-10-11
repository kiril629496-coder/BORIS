"""Deterministic winner lineage for A/B-scaled Campaign items."""
from __future__ import annotations


def apply_scale_lineage(payload: dict, scale_meta: dict | None) -> dict:
    out=dict(payload or {})
    meta=dict(scale_meta or {})
    try:
        source_item_id=int(meta.get("source_item_id") or 0)
    except (TypeError,ValueError):
        source_item_id=0
    if source_item_id<=0:
        return out

    try:
        source_campaign_id=int(meta.get("source_campaign_id") or 0)
    except (TypeError,ValueError):
        source_campaign_id=0
    try:
        requested_count=int(meta.get("requested_count") or 0)
    except (TypeError,ValueError):
        requested_count=0

    out["scale_lineage_contract"]=1
    out["scale_parent_item_id"]=source_item_id
    out["scale_parent_campaign_id"]=source_campaign_id or None
    out["scale_parent_avito_item_id"]=str(meta.get("source_avito_item_id") or "").strip() or None
    out["scale_parent_formula_id"]=str(meta.get("formula_id") or "").strip() or None
    out["scale_parent_hypothesis"]=str(meta.get("hypothesis") or "").strip() or None
    out["scale_parent_creative_angle"]=str(meta.get("creative_angle") or "").strip() or None
    out["scale_requested_count"]=requested_count or None
    out["scale_derivation"]="winner_pattern_new_variant"
    return out
