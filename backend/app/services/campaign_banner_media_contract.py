"""Local integrity checks shared by Campaign banner and gallery mutations.

These helpers never generate, publish, repair, or silently upgrade a QA receipt.
The existing Campaign revision transaction owns writes; Premium owns paid work.
"""
import hashlib
import re
from pathlib import Path


V7_COMPOSITION_FAMILIES = ("left", "right", "split", "editorial", "diagonal", "callout")
V7_LAYOUT_METADATA = {
    "left": {"text_position": "left", "object_position": "right", "background_family": "negative_space_scene"},
    "right": {"text_position": "right", "object_position": "left", "background_family": "negative_space_scene"},
    "split": {"text_position": "split_typography_zone", "object_position": "split_visual_zone", "background_family": "dual_zone"},
    "editorial": {"text_position": "integrated_editorial", "object_position": "hero_dominant", "background_family": "editorial_full_bleed"},
    "diagonal": {"text_position": "diagonal_counterbalance", "object_position": "diagonal_flow", "background_family": "depth_gradient"},
    "callout": {"text_position": "callout_overlay", "object_position": "hero_primary", "background_family": "hero_context"},
}


def premium_creative_choices(account_id, campaign_id, item_id, intent_key, existing_families=None):
    """Stable history-aware V7 choice without imposing an artificial equal quota."""
    hypotheses = ("product_hero", "pain_solution", "result_analytics", "dashboard_control", "people_product")
    seed = f"{account_id}|{int(campaign_id)}|{int(item_id)}|{intent_key}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    hypothesis = hypotheses[int.from_bytes(digest[:8], "big") % len(hypotheses)]
    pick = int.from_bytes(digest[8:16], "big")
    base_family = V7_COMPOSITION_FAMILIES[pick % len(V7_COMPOSITION_FAMILIES)]
    existing = [str(x or "").strip().lower() for x in (existing_families or [])
                if str(x or "").strip().lower() in V7_COMPOSITION_FAMILIES]
    if not existing:
        return hypothesis, base_family
    counts = {family: existing.count(family) for family in V7_COMPOSITION_FAMILIES}
    unseen = tuple(family for family in V7_COMPOSITION_FAMILIES if counts[family] == 0)
    if unseen:
        return hypothesis, unseen[pick % len(unseen)]
    total = len(existing)
    minimum = min(counts.values())
    projected_share = (counts[base_family] + 1) / float(total + 1)
    if projected_share > 0.35 or counts[base_family] >= minimum + 4:
        underused = tuple(family for family in V7_COMPOSITION_FAMILIES
                          if counts[family] <= minimum + 1)
        return hypothesis, underused[pick % len(underused)]
    return hypothesis, base_family


def premium_v7_cta(composition_family):
    family = str(composition_family or "").strip().lower()
    variants = {
        "left": ("message_discuss", "Напишите в Avito — обсудим задачу"),
        "right": ("message_select", "Напишите в Avito — подберём вариант"),
        "split": ("message_compare", "Напишите в Avito — сравним варианты"),
        "editorial": ("message_details", "Напишите в Avito — уточним детали"),
        "diagonal": ("message_start", "Напишите в Avito — начнём с вашей задачи"),
        "callout": ("message_offer", "Напишите в Avito — разберём предложение"),
    }
    return variants.get(family, variants["left"])


def premium_v7_metadata(composition_family, preview):
    """Persist intended V7 layout plus the actual rendered perceptual signature."""
    family = str(composition_family or "").strip().lower()
    if family not in V7_LAYOUT_METADATA:
        family = "left"
    preview = preview if isinstance(preview, dict) else {}
    technical = preview.get("technical_qa") or {}
    phash = str(technical.get("perceptual_hash") or "").strip().lower()
    signature = phash or hashlib.sha256(
        ("|".join([
            family,
            str(preview.get("composition_profile") or ""),
            str(preview.get("icon_profile") or ""),
            str(preview.get("creative_hypothesis") or ""),
            str(preview.get("prompt_sha256") or ""),
        ])).encode("utf-8")
    ).hexdigest()
    layout = dict(V7_LAYOUT_METADATA[family])
    cta_type, _cta_text = premium_v7_cta(family)
    return {
        "composition_family": family,
        "visual_signature": signature,
        "text_position": layout["text_position"],
        "object_position": layout["object_position"],
        "cta_type": cta_type,
        "background_family": layout["background_family"],
        "main_composition_class": family,
        "composition_profile": str(preview.get("composition_profile") or ""),
        "icon_profile": str(preview.get("icon_profile") or ""),
        "contract_version": "banner_v7_history_v1",
    }


def media_signature(pairs):
    """Detect a gallery writer even when it failed to advance the card version."""
    return tuple((int(link.media_id), str(link.role or ""), int(link.position or 0),
                  str(asset.storage_key or ""), str(asset.sha256 or ""))
                 for link, asset in pairs)


def asset_signature(assets):
    """Exact media selected before provider latency, including newly linked files."""
    return tuple((int(asset.id), str(asset.account_id or ""),
                  str(asset.storage_key or ""), str(asset.sha256 or ""),
                  str(asset.media_type or ""), str(asset.source_provider or ""),
                  str(asset.source_ref or "")) for asset in assets)


def premium_identity(source_ref):
    match = re.fullmatch(r"premium:([A-Za-z0-9_-]+):v([1-9][0-9]*)", str(source_ref or ""))
    return (match.group(1), int(match.group(2))) if match else (None, None)


def linked_file_current(asset, image_root="/root/BORIS/backend/images"):
    """A registered media hash must still describe the exact local bytes."""
    root = Path(image_root).resolve()
    key = str(getattr(asset, "storage_key", "") or "")
    expected = str(getattr(asset, "sha256", "") or "")
    if not key or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        return False
    try:
        path = (root / key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual = digest.hexdigest()
        return actual == expected.lower()
    except (OSError, ValueError):
        return False


def premium_link_proof(payload, pairs, *, account_id, hero_media_id,
                       read_preview, expected_intent=None,
                       image_root="/root/BORIS/backend/images", public_url=None,
                       content_fingerprint=None, campaign_id=None, item_id=None):
    """Read-only final proof: correct tenant, linked hero, bytes, QA and costs."""
    payload = payload if isinstance(payload, dict) else {}
    qa = payload.get("banner_qa") or {}
    if (str(payload.get("banner_provider") or "") != "openai_full_ai_premium"
            or qa.get("passed") is not True or not pairs):
        return {"ready": False, "code": "PREMIUM_BANNER_PROOF_MISSING"}
    if expected_intent is not None and str(payload.get("banner_regeneration_key") or "") != str(expected_intent):
        return {"ready": False, "code": "BANNER_INTENT_MISMATCH"}
    link, asset = pairs[0]
    expected_url = "/images/" + str(asset.storage_key or "").lstrip("/")
    banner_url = str(payload.get("banner_url") or "")
    # asset_public may expose either the relative URL or the site's absolute URL.
    if (int(link.position or 0) != 0 or str(link.role or "") != "hero"
            or int(hero_media_id or 0) != int(asset.id)
            or str(asset.account_id or "") != str(account_id)
            or str(asset.source_provider or "") != "openai_full_ai_premium"
            or banner_url not in {expected_url, str(public_url or expected_url)}
            or not payload.get("images") or str(payload["images"][0]) != banner_url
            or any(str(a.account_id or "") != str(account_id) for _, a in pairs)
            or len({int(a.id) for _, a in pairs}) != len(pairs)):
        return {"ready": False, "code": "PREMIUM_BANNER_LINK_DRIFT"}
    banner_id, version = premium_identity(asset.source_ref)
    if not banner_id or not linked_file_current(asset, image_root):
        return {"ready": False, "code": "PREMIUM_BANNER_ARTIFACT_DRIFT"}
    current = read_preview(str(account_id), banner_id, version)
    if (qa.get("banner_id") != banner_id or type(qa.get("version")) is not int
            or qa.get("version") != version or not qa.get("qa_version")
            or qa.get("qa_version") != current.get("qa_version")):
        return {"ready": False, "code": "PREMIUM_BANNER_QA_IDENTITY_MISMATCH"}
    provenance = current.get("provenance") or {}
    if ((campaign_id is not None and str(provenance.get("campaign_id")) != str(campaign_id))
            or (item_id is not None and str(provenance.get("campaign_item_id") or provenance.get("item_id")) != str(item_id))):
        return {"ready": False, "code": "PREMIUM_BANNER_ITEM_SCOPE_MISMATCH"}
    if content_fingerprint is not None and (
            qa.get("content_fingerprint") != content_fingerprint
            or (current.get("provenance") or {}).get("content_fingerprint") != content_fingerprint):
        return {"ready": False, "code": "PREMIUM_BANNER_CONTENT_CHANGED"}
    expected_hash = str(qa.get("artifact_sha256") or "")
    technical_hash = str((qa.get("technical_qa") or {}).get("artifact_sha256") or "")
    ready = bool(
        current.get("status") == "ok"
        and current.get("receipt_current") is True
        and current.get("artifact_current") is True
        and current.get("qa_terminal") is True
        and current.get("qa_passed") is True
        and current.get("rejected") is not True
        and current.get("cost_reconciled") is True
        and current.get("cost_sla_pass") is True
        and str(current.get("artifact_sha256") or "") == str(asset.sha256 or "")
        and expected_hash == str(asset.sha256 or "")
        and technical_hash == expected_hash
        and str(current.get("url") or "") in {expected_url, str(public_url or expected_url)}
    )
    return {"ready": ready, "code": "PASS" if ready else str(current.get("code") or "QA_RECEIPT_UPGRADE_REQUIRED"),
            "banner_id": banner_id, "version": version, "preview": current}


def preview_error(item_id, code, preview=None, **extra):
    """Keep paid-outcome identity intact through the adapter/worker boundary."""
    preview = preview if isinstance(preview, dict) else {}
    result = {"id": int(item_id), "code": str(code),
              "retry_action": str(preview.get("retry_action") or "blocked"),
              "retry_allowed": preview.get("retry_allowed") is True,
              "image_outcome": str(preview.get("image_outcome") or "unknown"),
              "qa_terminal": preview.get("qa_terminal") is True,
              "qa_rejection_reason": preview.get("qa_rejection_reason"),
              "banner_id": preview.get("banner_id"), "version": preview.get("version"),
              "artifact_sha256": preview.get("artifact_sha256"),
              "provider_request_id": preview.get("provider_request_id"),
              "ambiguous_provider_outcome": preview.get("ambiguous_provider_outcome") is True,
              "cost_reconciled": preview.get("cost_reconciled") is True,
              "cost_sla_pass": preview.get("cost_sla_pass") is True,
              "total_cost_rub": preview.get("total_cost_rub"),
              "preview_status": preview.get("preview_status"),
              "issues": (preview.get("vision_qa") or {}).get("issues") or []}
    result.update(extra)
    return result


def request_intents(body, item_ids):
    """A partial/foreign identity map must never silently fall back to a new key."""
    from fastapi import HTTPException
    wanted = {str(int(i)) for i in item_ids}
    image = dict(getattr(body, "item_idempotency_keys", None) or {})
    budget = dict(getattr(body, "item_budget_intent_keys", None) or {})
    if not image and not budget:
        return {}, {}
    image = {str(k): str(v).strip() for k, v in image.items()}
    budget = {str(k): str(v).strip() for k, v in budget.items()}
    if (set(image) != wanted or set(budget) != wanted
            or len(set(image.values())) != len(image)
            or len(set(budget.values())) != len(budget)
            or any(not value or len(value) > 200 or any(ord(c) < 32 for c in value)
                   for value in [*image.values(), *budget.values()])):
        raise HTTPException(422, detail={"code": "BANNER_ITEM_INTENT_SCOPE_INVALID",
                            "message": "Ключи операции должны точно соответствовать выбранным объявлениям."})
    return image, budget
