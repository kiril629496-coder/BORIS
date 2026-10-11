from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from .query_normalizer import normalize_query
from .wordstat_adapter import collect_wordstat
from .avito_market_adapter import collect_avito_market
from .market_score import build_market_score

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def cache_key(query: str, region: str = "") -> str:
    normalized = normalize_query(query, region)
    return hashlib.sha256(normalized["cache_key"].encode("utf-8")).hexdigest()[:24]

def run_market_audit(query: str, region: str = "", account_id: str | None = None, progress=None) -> dict[str, Any]:
    normalized = normalize_query(query, region)
    if callable(progress):
        progress("DEMAND_RUNNING")
        progress("COMPETITORS_RUNNING")

    wordstat: dict[str, Any] = {"status": "unavailable"}
    avito: dict[str, Any] = {"status": "unavailable", "competing_offers_sample": 0, "relevant_ads_sample": 0}
    collectors = {
        "wordstat": (collect_wordstat, normalized["query"], normalized["region"]),
        "avito": (collect_avito_market, normalized["query"], normalized["region"]),
    }
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="viral-market") as pool:
        futures = {
            pool.submit(fn, query_value, region_value): name
            for name, (fn, query_value, region_value) in collectors.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "unavailable", "error_type": type(exc).__name__}
            if name == "wordstat":
                wordstat = result if isinstance(result, dict) else {"status": "unavailable"}
                if callable(progress):
                    progress("DEMAND_READY")
            else:
                avito = result if isinstance(result, dict) else {"status": "unavailable"}
                if callable(progress):
                    progress("COMPETITORS_READY")

    score = build_market_score(wordstat, avito, normalized["region"])
    if callable(progress):
        progress("ANALYSIS_READY")

    sources = []
    wordstat_ready = wordstat.get("status") == "ok"
    avito_ready = bool(
        avito.get("status") == "ok"
        or int(avito.get("relevant_ads_sample") or 0) > 0
        or avito.get("search_total") is not None
    )
    if wordstat_ready:
        sources.append({"source": "Яндекс Вордстат", "status": "ready", "checked_at": wordstat.get("checked_at")})
    else:
        sources.append({"source": "Яндекс Вордстат", "status": "unavailable"})
    sources.append({"source": "Публичная выдача Avito", "status": "ready" if avito_ready else "unavailable"})

    demand_level = wordstat.get("demand_level") if wordstat.get("status") == "ok" else "Недостаточно данных"
    unique_sellers = avito.get("unique_sellers_sample")
    competitor_count_basis = str(avito.get("competitor_count_basis") or "offers_only")
    competing_offers = int(avito.get("competing_offers_sample") or 0)

    if wordstat_ready and avito_ready and competing_offers:
        summary = "Спрос подтверждён, рынок конкурентный — основной резерв в покрытии поисковых сценариев и отстройке предложения."
    elif wordstat_ready and not avito_ready:
        summary = "Спрос подтверждён. Данные по конкурентной выдаче Avito пока недоступны, поэтому цены и конкуренция не оцениваются."
    elif wordstat_ready:
        summary = "Спрос подтверждён, но в доступной выборке мало конкурирующих предложений."
    elif avito_ready and competing_offers:
        summary = "Рынок виден по Avito, но данные поискового спроса временно недоступны."
    else:
        summary = "Часть рыночных данных временно недоступна — BORIS не будет выдумывать показатели."

    generic = list(avito.get("generic_positioning") or [])
    positioning = {
        "market_common": generic,
        "can_use_now": [],
        "ideas_to_test": [
            "Разделить объявления по разным сценариям спроса",
            "Сделать отдельный первый визуал для каждого сценария",
            "Убрать общие формулировки и усилить конкретику предложения",
        ],
    }

    return {
        "status": "ok" if (wordstat_ready and avito_ready) else "partial",
        "market_snapshot_id": "market_" + normalized["cache_key"].replace("|","_")[:80],
        "query": normalized["query"],
        "region": normalized["region"],
        "normalized_query": normalized["normalized_query"],
        "category_slug": avito.get("category_slug"),
        "created_at": _now(),
        "summary": summary,
        "market_score": score,
        "demand": {
            "level": demand_level,
            "demand_level": demand_level,
            "total_count": wordstat.get("total_count") if wordstat.get("status") == "ok" else None,
            "top_requests": wordstat.get("top_requests") if wordstat.get("status") == "ok" else [],
            "region_scope": wordstat.get("region_scope") or "Россия",
            "requested_region": wordstat.get("requested_region") or normalized["region"],
            "regional_filter_applied": bool(wordstat.get("regional_filter_applied")),
            "confidence": wordstat.get("confidence","low"),
        },
        "competitors": {
            "direct_sample": int(unique_sellers) if unique_sellers is not None else None,
            "count_basis": competitor_count_basis,
            "unique_sellers_sample": int(unique_sellers) if unique_sellers is not None else None,
            "competing_offers_sample": competing_offers,
            "direct_offers_sample": competing_offers,
            "search_total": avito.get("search_total"),
            "estimated_relevant_offers": avito.get("estimated_relevant_offers"),
            "estimated_competitors": avito.get("estimated_competitors"),
            "ads_per_seller_median": avito.get("ads_per_seller_median"),
            "adjacent_sample": int(avito.get("adjacent_sample") or 0),
            "relevant_ads_sample": int(avito.get("relevant_ads_sample") or 0),
            "confidence": avito.get("confidence","low"),
        },
        "prices": avito.get("prices") or {},
        "market": {
            "direct_competitors": int(unique_sellers) if unique_sellers is not None else None,
            "competing_offers": competing_offers,
            "estimated_relevant_offers": avito.get("estimated_relevant_offers"),
            "estimated_competitors": avito.get("estimated_competitors"),
            "relevant_listings": int(avito.get("relevant_ads_sample") or 0),
            "price": avito.get("prices") or {},
            "common_title_terms": avito.get("common_title_terms") or [],
            "median_photos": None,
            "items": avito.get("sample_ads") or [],
            "confidence": avito.get("confidence","low"),
        },
        "positioning": positioning,
        "recommended_ads": score.get("recommended_ads") or {},
        "sample_ads": avito.get("sample_ads") or [],
        "transport": {
            "browser_transport": avito.get("transport"),
            "browser_traffic_mb": float(avito.get("traffic_mb") or 0),
            "proxy_cost_rub": avito.get("proxy_cost_rub"),
            "proxy_cost_known": bool(avito.get("proxy_cost_known", True)),
            "browser_success": bool(avito.get("status") in {"ok","partial"} and (avito.get("transport") or avito.get("relevant_ads_sample") or 0)),
            "wordstat_success": bool(wordstat.get("status") == "ok"),
            "wordstat_cost_rub": float(wordstat.get("cost_rub") or 0),
        },
        "sources": sources,
        "partial": not (wordstat_ready and avito_ready),
    }
