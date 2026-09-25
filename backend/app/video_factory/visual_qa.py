# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import os
from PIL import Image
from app.services.exception_observability import observe_suppressed

MODEL = "gpt-4o-mini"
OPERATION = "BORIS Video Factory visual QA"


def _data_uri(path):
    from app.services.ai_privacy import sanitize_external_reference_image_bytes
    with open(path, "rb") as fh:
        safe_bytes, mime = sanitize_external_reference_image_bytes(
            fh.read(), max_side=384, blur_radius=3.5
        )
    return "data:" + mime + ";base64," + base64.b64encode(safe_bytes).decode()


def audit(data, assets, projects_root=None):
    """Strict one-intent visual QA using the shared paid Vision guard/cache.

    Repeating the exact same project/images is a durable cache hit, not a new
    OpenAI purchase. Missing account_id fails closed because paid work must be
    attributable to one BORIS account.
    """
    if not assets:
        return {"pass": False, "score": 0, "items": [], "reason": "no_visuals"}
    account_id = str(data.get("account_id") or "").strip()
    if not account_id:
        return {"pass": False, "score": 0, "items": [], "reason": "account_required_for_paid_vision"}
    if not os.environ.get("OPENAI_API_KEY"):
        return {"pass": False, "score": 0, "items": [], "reason": "vision_not_configured"}

    plan = data.get("plan") or {}
    prompt = (plan.get("prompt") or "").strip()
    scenes = plan.get("scenes") or []
    content = [{"type": "text", "text": (
        "You are a strict visual QA editor for a premium commercial short-form advertisement. "
        "The client description below is the ONLY source of truth. Inspect EACH image. Reject an image if it depicts the wrong product/service, invents important product characteristics, contains malformed anatomy/geometry, visible gibberish/text/watermarks, looks like a cheap abstract placeholder, or is too ambiguous to sell the offer. Do NOT require a picture to literally depict non-visual claims such as warranty, price, city, phone number, duration or CTA; for such scenes accept a strong relevant visual of the core product/service as long as it does not contradict the claim. "
        "Return strict JSON array, one object per image in order: "
        "{\"index\":1,\"match\":true,\"confidence\":0.0,\"quality\":0.0,\"subject\":\"visible subject\",\"issues\":[\"short issue\"],\"reason\":\"short\"}. "
        "quality means commercial advertising usefulness, 0..1. Be conservative. "
        f"CLIENT DESCRIPTION: {prompt[:5000]}"
    )}]
    nonvisual = ("гарант", "цена", "₽", "руб", "телефон", "позвон", "напиш", "город",
                 "санкт-петербург", "москва", "год", "месяц", "срок")
    for idx, asset in enumerate(assets, 1):
        scene_no = int(asset.get("scene_index") or idx)
        scene = next((s for s in scenes if int(s.get("index") or 0) == scene_no), {})
        caption = (scene.get("caption") or "")[:500]
        low = caption.lower()
        meaning = (
            "Supporting visual of the core advertised product/service. The caption contains a non-visual claim; DO NOT expect the image to literally show that claim."
            if any(k in low for k in nonvisual) else caption
        )
        content.append({"type": "text", "text": f"IMAGE {idx}; SCENE {scene_no}; SCENE MEANING: {meaning}"})
        content.append({"type": "image_url", "image_url": {"url": _data_uri(asset["path"]), "detail": "low"}})

    from app.services.media_semantic_validator import _vision_chat_json
    items, meta = _vision_chat_json(
        account_id, OPERATION, content, max_tokens=1200, timeout=120, return_meta=True
    )
    if not isinstance(items, list) or len(items) != len(assets):
        raise RuntimeError("visual_qa_bad_shape")

    normalized = []
    for asset, verdict in zip(assets, items):
        match = bool(verdict.get("match"))
        conf = float(verdict.get("confidence") or 0)
        quality = float(verdict.get("quality") or 0)
        ok = match and conf >= 0.78 and quality >= 0.72
        normalized.append({
            "scene_index": int(asset.get("scene_index") or 0),
            "asset": asset.get("name"),
            "pass": ok,
            "match": match,
            "confidence": round(conf, 3),
            "quality": round(quality, 3),
            "subject": str(verdict.get("subject") or "")[:220],
            "issues": [str(x)[:160] for x in (verdict.get("issues") or [])][:5],
            "reason": str(verdict.get("reason") or "")[:280],
        })
    score = round(sum((x["quality"] * 100) for x in normalized) / max(1, len(normalized)))
    passed = all(x["pass"] for x in normalized) and score >= 76

    # Project-local cost ledger is secondary. Append only for a fresh paid call;
    # cache hits are zero new spend and canonical api_usage remains authoritative.
    if projects_root and not bool((meta or {}).get("cache_hit")) and (meta or {}).get("cost_rub") is not None:
        try:
            from app.usage import USD_TO_RUB
            from .costs import append as cost_append
            cost_rub = float(meta.get("cost_rub") or 0)
            cost_append(
                projects_root, data["id"], "openai", MODEL, "visual_qa", 1, "request",
                cost_rub / float(USD_TO_RUB or 90),
                {"cost_rub": cost_rub, "request_id": meta.get("request_id"), "images": len(assets)},
            )
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=101)

    return {
        "pass": passed,
        "score": score,
        "model": MODEL,
        "items": normalized,
        "request_id": (meta or {}).get("request_id") or (meta or {}).get("provider_request_id"),
        "cache_hit": bool((meta or {}).get("cache_hit")),
    }
