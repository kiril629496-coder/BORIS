from __future__ import annotations

import os
import time
from typing import Any

import httpx

_SEARCH_ROOT = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
_REGIONS_CACHE: dict[str, Any] = {"loaded_at": 0.0, "map": {}}


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def _credentials() -> tuple[str, str]:
    key = (
        os.getenv("YANDEX_CLOUD_API_KEY")
        or os.getenv("YANDEX_AI_API_KEY")
        or os.getenv("YANDEX_API_KEY")
        or ""
    ).strip()
    folder = (
        os.getenv("YANDEX_CLOUD_FOLDER_ID")
        or os.getenv("YANDEX_GPT_FOLDER_ID")
        or ""
    ).strip()
    return key, folder


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Api-Key {key}",
        "Content-Type": "application/json",
    }


def _flatten_regions(rows: list[dict[str, Any]], out: dict[str, str]) -> None:
    for row in rows or []:
        rid = str(row.get("id") or "").strip()
        label = str(row.get("label") or row.get("name") or "").strip()
        if rid and label:
            out[_norm(label)] = rid
        _flatten_regions(list(row.get("children") or []), out)


def _region_map(key: str, folder: str) -> dict[str, str]:
    now = time.time()
    cached = _REGIONS_CACHE.get("map") or {}
    if cached and now - float(_REGIONS_CACHE.get("loaded_at") or 0) < 86400:
        return dict(cached)

    resp = httpx.post(
        f"{_SEARCH_ROOT}/getRegionsTree",
        headers=_headers(key),
        json={"folderId": folder},
        timeout=20,
    )
    if resp.status_code != 200:
        return {}
    data = resp.json()
    mapping: dict[str, str] = {}
    _flatten_regions(list(data.get("regions") or []), mapping)
    _REGIONS_CACHE["map"] = mapping
    _REGIONS_CACHE["loaded_at"] = now
    return mapping


def _resolve_region_id(region: str, key: str, folder: str) -> tuple[str | None, bool]:
    requested = _norm(region)
    if not requested:
        return None, False

    # Hot path for common cities; full tree below handles everything else.
    common = {
        "москва": "213",
        "санкт-петербург": "2",
        "санкт петербург": "2",
        "казань": "43",
        "уфа": "172",
        "екатеринбург": "54",
        "новосибирск": "65",
        "нижний новгород": "47",
        "краснодар": "35",
        "самара": "51",
        "омск": "66",
        "челябинск": "56",
        "ростов-на-дону": "39",
        "ростов на дону": "39",
        "сочи": "239",
    }
    if requested in common:
        return common[requested], True

    try:
        mapping = _region_map(key, folder)
    except Exception:
        mapping = {}
    if requested in mapping:
        return mapping[requested], True

    # Conservative fuzzy fallback: only exact token containment against one unique region.
    matches = [rid for label, rid in mapping.items() if requested and (requested in label or label in requested)]
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0], True
    return None, False


def collect_wordstat(query: str, region: str = "") -> dict[str, Any]:
    """Yandex Search API v2 Wordstat adapter.

    Uses real regional Wordstat data when the region can be resolved. Never
    inserts the region name into the phrase to simulate geo filtering.
    """
    key, folder = _credentials()
    if not key or not folder:
        return {
            "status": "unavailable",
            "source": "Яндекс Вордстат",
            "message": "Wordstat API не настроен",
            "region_scope": "Россия",
            "requested_region": str(region or "").strip() or None,
            "regional_filter_applied": False,
            "confidence": "low",
            "cost_rub": 0.0,
        }

    region_id, regional = _resolve_region_id(region, key, folder)
    body: dict[str, Any] = {
        "phrase": str(query or "").strip(),
        "numPhrases": 20,
        "devices": ["DEVICE_ALL"],
        "folderId": folder,
    }
    if region_id:
        body["regions"] = [str(region_id)]

    try:
        resp = httpx.post(
            f"{_SEARCH_ROOT}/topRequests",
            headers=_headers(key),
            json=body,
            timeout=25,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "Яндекс Вордстат",
            "message": f"Wordstat временно недоступен: {type(exc).__name__}",
            "region_scope": str(region or "").strip() if regional else "Россия",
            "requested_region": str(region or "").strip() or None,
            "regional_filter_applied": regional,
            "confidence": "low",
            "cost_rub": 0.0,
        }

    if resp.status_code != 200:
        message = ""
        try:
            payload = resp.json()
            message = str(payload.get("message") or payload.get("error") or "")[:220]
        except Exception:
            message = resp.text[:220]
        return {
            "status": "unavailable",
            "source": "Яндекс Вордстат",
            "message": f"Wordstat API ошибка {resp.status_code}: {message}",
            "region_scope": str(region or "").strip() if regional else "Россия",
            "requested_region": str(region or "").strip() or None,
            "regional_filter_applied": regional,
            "confidence": "low",
            "cost_rub": 0.0,
        }

    data = resp.json()
    total = int(data.get("totalCount") or 0)
    raw_results = list(data.get("results") or [])
    associations = list(data.get("associations") or [])

    top = []
    for row in raw_results[:20]:
        phrase_value = str(row.get("phrase") or "").strip()
        if not phrase_value:
            continue
        top.append({
            "phrase": phrase_value,
            "count": int(row.get("count") or 0),
        })

    commercial_markers = (
        "купить", "цена", "заказать", "стоимость", "недорого",
        "доставка", "оптом", "прайс", "заказ"
    )
    commercial = [
        row for row in top
        if any(marker in str(row.get("phrase") or "").lower() for marker in commercial_markers)
    ]
    commercial_share = round(len(commercial) / len(top) * 100, 1) if top else 0.0

    if total >= 5000:
        level = "Высокий"
    elif total >= 1000:
        level = "Средний"
    elif total > 0:
        level = "Низкий"
    else:
        level = "Недостаточно данных"

    return {
        "status": "ok",
        "source": "Яндекс Вордстат",
        "query": str(query or "").strip(),
        "total_count": total,
        "demand_level": level,
        "top_requests": top,
        "associations": [
            {"phrase": str(x.get("phrase") or "").strip(), "count": int(x.get("count") or 0)}
            for x in associations[:20] if str(x.get("phrase") or "").strip()
        ],
        "commercial_requests": commercial,
        "commercial_share_percent": commercial_share,
        "region_scope": str(region or "").strip() if regional else "Россия",
        "requested_region": str(region or "").strip() or None,
        "region_id": region_id,
        "regional_filter_applied": regional,
        "confidence": "high" if total > 0 else "low",
        "cost_rub": float(os.getenv("BORIS_WORDSTAT_COST_RUB", "0.02") or 0.02),
    }
