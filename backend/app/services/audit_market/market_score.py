from __future__ import annotations

import math
from typing import Any

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def recommended_ads(demand_total: int, competitor_sample: int, top_requests: list[dict], region: str) -> dict[str, Any]:
    request_count = len([x for x in top_requests if int(x.get("count") or 0) > 0])
    clusters = 5 if request_count >= 8 else 4 if request_count >= 4 else 3
    base = clusters * 4
    competition_factor = 1.0 + min(1.0, competitor_sample / 20.0) * 0.5
    demand_factor = 1.0 + min(1.0, max(0, demand_total) / 10000.0) * 0.5
    low = int(round(_clamp(base * competition_factor * demand_factor, 6, 50)))
    high = int(round(_clamp(low * 1.25, low + 2, 60)))
    logic = {
        "semantic_clusters": clusters,
        "competition_sample": competitor_sample,
        "direct_competitors": competitor_sample,
        "relevant_listings": competitor_sample,
        "demand_total": demand_total,
        "demand_count": demand_total,
        "region_groups": 1 if region else 1,
    }
    grounded = bool(demand_total > 0 or competitor_sample > 0)
    return {
        "low": low,
        "high": high,
        "clusters": clusters,
        "grounded": grounded,
        "confidence": "medium" if grounded else "low",
        "logic": logic,
        "reason": logic,
    }

def build_market_score(wordstat: dict, avito: dict, region: str) -> dict[str, Any]:
    demand_total = int(wordstat.get("total_count") or 0) if wordstat.get("status") == "ok" else 0
    competing_offers = int(
        avito.get("estimated_relevant_offers")
        if avito.get("estimated_relevant_offers") is not None
        else avito.get("competing_offers_sample") or 0
    ) if avito.get("status") in {"ok","partial"} else 0
    unique_sellers = int(avito.get("unique_sellers_sample") or 0) if avito.get("unique_sellers_sample") is not None else 0
    top_requests = list(wordstat.get("top_requests") or []) if wordstat.get("status") == "ok" else []
    ads = recommended_ads(demand_total, competing_offers, top_requests, region)
    demand_grounded = bool(wordstat.get("status") == "ok" and demand_total > 0)
    competition_grounded = bool(
        avito.get("status") == "ok"
        or int(avito.get("relevant_ads_sample") or 0) > 0
        or avito.get("search_total") is not None
    )
    ads["demand_grounded"] = demand_grounded
    ads["competition_grounded"] = competition_grounded
    ads["basis"] = (
        "demand_and_competition" if demand_grounded and competition_grounded
        else "demand_only" if demand_grounded
        else "competition_only" if competition_grounded
        else "insufficient"
    )

    demand_points = int(round(_clamp(math.log10(max(1, demand_total)) / 5 * 20, 4 if demand_total else 8, 20))) if demand_total else 8
    hypotheses_points = int(round(_clamp(ads["clusters"] / 8 * 15, 4, 15)))
    differentiation_points = 9 if avito.get("generic_positioning") else 13
    price_points = 8 if (avito.get("prices") or {}).get("median") else 5
    offer_points = 10 if competing_offers >= 3 else 8
    visual_points = 7 if any(int(x.get("photos_count") or 0) >= 5 for x in (avito.get("sample_ads") or [])) else 5
    geo_points = 10 if region else 6

    breakdown = {
        "demand_coverage": {"label": "Покрытие спроса", "score": demand_points, "max": 20},
        "hypotheses": {"label": "Количество гипотез", "score": hypotheses_points, "max": 15},
        "differentiation": {"label": "Конкурентная отстройка", "score": differentiation_points, "max": 20},
        "price_positioning": {"label": "Цена / позиционирование", "score": price_points, "max": 10},
        "offer_quality": {"label": "Качество предложения", "score": offer_points, "max": 15},
        "visual_competitiveness": {"label": "Визуальная конкурентность", "score": visual_points, "max": 10},
        "geo_coverage": {"label": "Географическое покрытие", "score": geo_points, "max": 10},
    }
    total = int(sum(x["score"] for x in breakdown.values()))
    confidence = "high" if wordstat.get("status") == "ok" and competing_offers >= 8 else ("medium" if wordstat.get("status") == "ok" or competing_offers >= 3 else "low")
    label = "Сильное соответствие рынку" if total >= 80 else "Хорошая база" if total >= 65 else "Есть точки роста" if total >= 45 else "Предложение стоит усилить"
    if confidence == "low" or not competition_grounded:
        label = "Предварительная оценка рынка"
    return {
        "total": total,
        "label": label,
        "score_version": "market_v1",
        "confidence": confidence,
        "breakdown": breakdown,
        "recommended_ads": ads,
    }
