from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.db.session import SessionLocal
from app.models.storage import Storage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _put(account_id: str, key: str, value: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if row:
            row.value = payload
        else:
            db.add(Storage(account_id=account_id, key=key, value=payload))
        db.commit()
    finally:
        db.close()


def _get(account_id: str, key: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        if not row or not row.value:
            return None
        try:
            value = json.loads(row.value)
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    finally:
        db.close()


def _status(account_id: str, market_id: str, stage: str, **extra: Any) -> None:
    current = _get(account_id, f"market_status:{market_id}") or {}
    current.update({
        "market_audit_id": market_id,
        "stage": stage,
        "updated_at": _now().isoformat(),
        **extra,
    })
    _put(account_id, f"market_status:{market_id}", current)


_STOP = {
    "и","в","на","для","по","из","с","со","под","от","до","или","как","за","у",
    "к","о","об","это","этот","эта","эти","цена","купить","заказать","недорого",
}


def _tokens(text: str) -> List[str]:
    out = []
    for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{2,}", str(text or "").lower()):
        if token in _STOP:
            continue
        out.append(token)
    return out


def _normalize_query(value: str) -> str:
    return " ".join(str(value or "").strip().split())[:180]


def _cache_key(query: str, region: str, category: str = "") -> str:
    raw = json.dumps(
        {"q": query.lower(), "r": region.lower(), "c": category.lower()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_key(query: str, region: str) -> str:
    """Stable cache key used by the Audit Gateway API layer."""
    return _cache_key(_normalize_query(query), _normalize_query(region), "")


def _median(values: List[float]) -> Optional[float]:
    vals = [float(x) for x in values if x is not None]
    return round(float(statistics.median(vals)), 2) if vals else None


def _price_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals = sorted(int(x["price"]) for x in items if isinstance(x.get("price"), (int, float)) and x.get("price"))
    if not vals:
        return {"min": None, "median": None, "max": None, "sample": 0}
    return {
        "min": vals[0],
        "median": int(statistics.median(vals)),
        "max": vals[-1],
        "sample": len(vals),
    }


def _relevance(query: str, title: str) -> str:
    q = set(_tokens(query))
    t = set(_tokens(title))
    if not q or not t:
        return "irrelevant"
    overlap = len(q & t) / max(1, len(q))
    q_text = " ".join(_tokens(query))
    t_text = " ".join(_tokens(title))
    banned = {"вакансия","работа","работник","резюме","станок","оборудование","фурнитура","запчасти"}
    if not (banned & q) and (banned & t):
        return "irrelevant"
    if q_text and q_text in t_text:
        return "direct"
    if overlap >= 0.45 or (len(q) == 1 and bool(q & t)):
        return "direct"
    if overlap > 0:
        return "adjacent"
    return "irrelevant"


def _seller_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html or "", "lxml")
    for marker in ("seller-info/name", "seller-info/label", "seller-info/value"):
        el = soup.find(attrs={"data-marker": marker})
        if el:
            text = " ".join(el.get_text(" ", strip=True).split())
            if 2 <= len(text) <= 160:
                return text
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/brands/" in href or "/user/" in href or "/profile/" in href:
            text = " ".join(a.get_text(" ", strip=True).split())
            if 2 <= len(text) <= 160:
                return text
    return None


def _detail_facts(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "lxml")
    description = ""
    el = soup.find(attrs={"itemprop": "description"}) or soup.find(attrs={"data-marker": "item-description/text"})
    if el:
        description = " ".join(el.get_text(" ", strip=True).split())[:4000]
    images = len(soup.find_all(attrs={"data-marker": "image-preview/item"}))
    return {
        "seller": _seller_from_html(html),
        "description": description,
        "description_length": len(description),
        "images_count": images,
    }


def _page_total(html: str) -> Optional[int]:
    soup = BeautifulSoup(html or "", "lxml")
    for marker in ("page-title/count", "search-results/total-count"):
        el = soup.find(attrs={"data-marker": marker})
        if el:
            nums = re.sub(r"\D+", "", el.get_text(" ", strip=True))
            if nums:
                return int(nums)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    m = re.search(r"(\d[\d\s]{1,12})\s+(?:объявлен|предложен)", title.lower())
    if m:
        return int(re.sub(r"\s+", "", m.group(1)))
    return None


async def _collect_avito(query: str, region: str, max_details: int = 18) -> Dict[str, Any]:
    from app.api import parser as P

    city = P._normalize_city(region or "Москва")
    url = f"https://www.avito.ru/{city}?q={quote_plus(query)}"
    try:
        page_data = await P._fetch_avito_page_headed(url)
        html = str(page_data.get("html") or "")
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "source": "Публичная выдача Avito"}

    if "firewallCaptcha" in html or "geetest_captcha" in html:
        return {
            "status": "unavailable",
            "reason": "captcha",
            "message": "Публичная выдача Avito временно недоступна для анализа.",
            "source": "Публичная выдача Avito",
        }

    raw = P._extract_listing_items(html)
    rows = []
    for item in raw:
        rel = _relevance(query, str(item.get("title") or ""))
        rows.append({**item, "relevance": rel})

    direct = [x for x in rows if x["relevance"] == "direct"]
    adjacent = [x for x in rows if x["relevance"] == "adjacent"]
    page_total = _page_total(html)

    sem = asyncio.Semaphore(3)
    async def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
        detail = {}
        url = str(item.get("url") or "")
        if url:
            async with sem:
                try:
                    pd = await P._fetch_avito_page_headed(url, timeout_ms=20000, wait_ms=2500)
                    dh = str(pd.get("html") or "")
                    if "firewallCaptcha" not in dh and "geetest_captcha" not in dh:
                        detail = _detail_facts(dh)
                except Exception:
                    detail = {}
        return {**item, **detail}

    sampled = await asyncio.gather(*[enrich(x) for x in direct[:max_details]]) if direct else []
    seller_counts = Counter(str(x.get("seller") or "").strip() for x in sampled if str(x.get("seller") or "").strip())
    seller_ad_counts = list(seller_counts.values())
    direct_ratio = len(direct) / max(1, len(rows))
    estimated_relevant = int(round(page_total * direct_ratio)) if page_total is not None and rows else None
    ads_per_seller_median = _median([float(x) for x in seller_ad_counts])
    estimated_competitors = None
    if estimated_relevant is not None and ads_per_seller_median:
        estimated_competitors = max(1, int(math.ceil(estimated_relevant / max(1.0, ads_per_seller_median))))

    title_counter = Counter()
    query_tokens = set(_tokens(query))
    for item in direct:
        for token in _tokens(str(item.get("title") or "")):
            if token not in query_tokens and len(token) >= 4:
                title_counter[token] += 1

    descriptions = [int(x.get("description_length") or 0) for x in sampled if x.get("description_length") is not None]
    images = [int(x.get("images_count") or 0) for x in sampled if x.get("images_count") is not None]

    return {
        "status": "ok",
        "source": "Публичная выдача Avito",
        "source_url": url,
        "checked_at": _now().isoformat(),
        "sample_total": len(rows),
        "sample_direct": len(direct),
        "sample_adjacent": len(adjacent),
        "sample_irrelevant": max(0, len(rows) - len(direct) - len(adjacent)),
        "search_total": page_total,
        "estimated_relevant_offers": estimated_relevant,
        "sampled_unique_sellers": len(seller_counts),
        "estimated_competitors": estimated_competitors,
        "ads_per_seller_median": ads_per_seller_median,
        "price": _price_stats(direct),
        "common_title_words": [{"word": w, "count": c} for w, c in title_counter.most_common(8)],
        "median_description_length": _median([float(x) for x in descriptions]),
        "median_photos": _median([float(x) for x in images]),
        "sample": [
            {
                "title": x.get("title"),
                "price": x.get("price"),
                "seller": x.get("seller"),
                "images_count": x.get("images_count"),
                "photos_count": x.get("images_count"),
                "relevance": x.get("relevance"),
            }
            for x in sampled[:12]
        ],
    }


def _wordstat(query: str) -> Dict[str, Any]:
    try:
        from app.api.avito import wordstat_analyze
        raw = wordstat_analyze(query=query, account_id=None)
        if not isinstance(raw, dict) or raw.get("status") != "ok":
            return {
                "status": "unavailable",
                "source": "Яндекс Вордстат",
                "message": str((raw or {}).get("message") or "Данные спроса временно недоступны")[:240],
            }
        return {
            "status": "ok",
            "source": "Яндекс Вордстат",
            "checked_at": raw.get("checked_at") or _now().isoformat(),
            "total_count": int(raw.get("total_count") or 0),
            "top_requests": list(raw.get("top_requests") or [])[:12],
            "associations": list(raw.get("associations") or [])[:12],
            "commercial_share_percent": float(raw.get("commercial_share_percent") or 0),
            "geography": "Россия",
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "Яндекс Вордстат",
            "message": type(exc).__name__,
        }


def _demand_level(total: Optional[int]) -> Dict[str, Any]:
    if total is None:
        return {"level": "unknown", "label": "Данных пока недостаточно", "score": 10, "confidence": "low"}
    high = int(os.getenv("BORIS_MARKET_DEMAND_HIGH", "3000"))
    medium = int(os.getenv("BORIS_MARKET_DEMAND_MEDIUM", "500"))
    if total >= high:
        return {"level": "high", "label": "Высокий", "score": 20, "confidence": "high"}
    if total >= medium:
        return {"level": "medium", "label": "Средний", "score": 14, "confidence": "high"}
    if total > 0:
        return {"level": "low", "label": "Ограниченный", "score": 8, "confidence": "high"}
    return {"level": "low", "label": "Низкий", "score": 5, "confidence": "medium"}


def _clusters(query: str, wordstat: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []
    for row in wordstat.get("top_requests") or []:
        phrase = _normalize_query(str(row.get("phrase") or ""))
        if not phrase:
            continue
        candidates.append({"phrase": phrase, "count": int(row.get("count") or row.get("shows") or 0)})
    if not candidates:
        candidates = [{"phrase": query, "count": 0}]
    out = []
    seen: List[set[str]] = []
    for item in candidates:
        tok = set(_tokens(item["phrase"]))
        if not tok:
            continue
        duplicate = False
        for prev in seen:
            union = len(tok | prev)
            if union and len(tok & prev) / union >= 0.75:
                duplicate = True
                break
        if duplicate:
            continue
        seen.append(tok)
        out.append(item)
        if len(out) >= 6:
            break
    return out or [{"phrase": query, "count": 0}]


def _recommended_ads(clusters: List[Dict[str, Any]], avito: Dict[str, Any]) -> Dict[str, Any]:
    base = max(2, int(os.getenv("BORIS_MARKET_ADS_MIN_PER_CLUSTER", "4")))
    relevant = avito.get("estimated_relevant_offers")
    if relevant is None:
        relevant = avito.get("sample_direct") or 0
    factor = 1.0
    if relevant >= 120:
        factor = 1.6
    elif relevant >= 60:
        factor = 1.35
    elif relevant >= 20:
        factor = 1.15
    raw = len(clusters) * base * factor
    cap = max(12, int(os.getenv("BORIS_MARKET_ADS_CAP", "60")))
    center = max(6, min(cap, int(math.ceil(raw))))
    low = max(6, int(math.floor(center * 0.85)))
    high = min(cap, max(low, int(math.ceil(center * 1.20))))
    allocation = []
    remaining = center
    for idx, cluster in enumerate(clusters):
        slots_left = len(clusters) - idx
        qty = max(2, int(round(remaining / max(1, slots_left))))
        allocation.append({"cluster": cluster["phrase"], "recommended_ads": qty})
        remaining -= qty
    return {
        "low": low,
        "high": high,
        "center": center,
        "clusters": allocation,
        "formula": {
            "semantic_clusters": len(clusters),
            "base_per_cluster": base,
            "competition_factor": factor,
            "cap": cap,
        },
        "reason": {
            "semantic_clusters": len(clusters),
            "direct_competitors": avito.get("estimated_competitors"),
            "relevant_listings": relevant,
            "demand_count": None,
        },
    }


def _market_score(demand: Dict[str, Any], clusters: List[Dict[str, Any]], avito: Dict[str, Any]) -> Dict[str, Any]:
    gaps = []
    breakdown: Dict[str, Dict[str, Any]] = {}

    demand_score = int(demand.get("score") or 10)
    if demand.get("level") == "unknown":
        gaps.append("Нет точной частотности поискового спроса")
    breakdown["demand_coverage"] = {"score": demand_score, "max": 20, "label": "Покрытие спроса"}

    hypotheses = min(15, max(3, len(clusters) * 3))
    breakdown["hypotheses"] = {"score": hypotheses, "max": 15, "label": "Количество гипотез"}

    common = avito.get("common_title_words") or []
    direct_n = max(1, int(avito.get("sample_direct") or 0))
    saturation = max([float(x.get("count") or 0) / direct_n for x in common], default=0.0)
    differentiation = int(round(20 * (1 - min(0.65, saturation) * 0.7))) if avito.get("status") == "ok" else 10
    if avito.get("status") != "ok":
        gaps.append("Публичная выдача Avito временно недоступна")
    breakdown["differentiation"] = {"score": max(4, min(20, differentiation)), "max": 20, "label": "Конкурентная отстройка"}

    price_sample = int((avito.get("price") or {}).get("sample") or 0)
    price_score = 8 if price_sample >= 5 else 5
    if price_sample < 5:
        gaps.append("Мало цен для уверенного сравнения")
    breakdown["price_positioning"] = {"score": price_score, "max": 10, "label": "Цена / позиционирование"}

    desc_len = avito.get("median_description_length")
    offer_score = 8
    if desc_len is not None:
        offer_score = 13 if desc_len >= 500 else 10 if desc_len >= 200 else 7
    else:
        gaps.append("Недостаточно описаний конкурентов для оценки подачи")
    breakdown["offer_quality"] = {"score": offer_score, "max": 15, "label": "Качество предложения"}

    photos = avito.get("median_photos")
    visual_score = 5
    if photos is not None:
        visual_score = 9 if photos >= 8 else 7 if photos >= 5 else 5
    else:
        gaps.append("Недостаточно данных по визуалам")
    breakdown["visual"] = {"score": visual_score, "max": 10, "label": "Визуальная конкурентность"}

    breakdown["geo"] = {"score": 7, "max": 10, "label": "Географическое покрытие"}

    total = sum(int(x["score"]) for x in breakdown.values())
    confidence = "high" if not gaps and avito.get("status") == "ok" and demand.get("level") != "unknown" else "medium" if avito.get("status") == "ok" or demand.get("level") != "unknown" else "low"
    return {
        "total": max(0, min(100, total)),
        "score_version": "market_v1",
        "confidence": confidence,
        "breakdown": breakdown,
        "data_gaps": gaps,
    }


def run_market_audit(
    query: str,
    region: str,
    account_id: Optional[str] = None,
    progress=None,
    category: str = "",
    own_price_rub: Optional[float] = None,
) -> Dict[str, Any]:
    """Collect and score public market facts. Storage/job lifecycle belongs to viral.py."""
    query = _normalize_query(query)
    region = _normalize_query(region)
    category = _normalize_query(category)
    if len(query) < 2 or len(region) < 2:
        raise ValueError("Ниша и регион обязательны")

    if progress:
        progress("DEMAND_RUNNING")
    wordstat = _wordstat(query)
    if progress:
        progress("DEMAND_READY")

    if progress:
        progress("COMPETITORS_RUNNING")
    avito = asyncio.run(_collect_avito(query, region))
    if progress:
        progress("COMPETITORS_READY")

    total = int(wordstat.get("total_count") or 0) if wordstat.get("status") == "ok" else None
    demand = _demand_level(total)
    clusters = _clusters(query, wordstat)
    recommended = _recommended_ads(clusters, avito)
    score = _market_score(demand, clusters, avito)

    price = dict(avito.get("price") or {})
    own_price_position = None
    if own_price_rub and price.get("median"):
        median = float(price["median"])
        if own_price_rub < median * 0.85:
            own_price_position = "ниже рынка"
        elif own_price_rub > median * 1.15:
            own_price_position = "верхний ценовой диапазон"
        else:
            own_price_position = "в рынке"

    generic_words = [x["word"] for x in (avito.get("common_title_words") or [])[:5]]
    differentiation = {
        "market_patterns": generic_words,
        "what_can_use_now": [
            "Разделить объявления по разным сценариям спроса",
            "Сделать отдельное первое фото для каждого смыслового кластера",
            "Усилить конкретику в заголовках вместо общих обещаний",
        ],
        "ideas_to_validate": [
            "Проверить оффер с конкретным сроком расчёта или ответа",
            "Проверить фиксируемый диапазон цены, если бизнес может его подтвердить",
            "Проверить локальные кейсы и реальные работы по району/городу",
        ],
    }

    competitors = avito.get("estimated_competitors")
    relevant_offers = avito.get("estimated_relevant_offers")
    summary = (
        f"Спрос: {demand.get('label')}. "
        + (f"Оценочно {competitors} прямых конкурентов. " if competitors is not None else "Точное число конкурентов пока не подтверждено. ")
        + f"Для старта BORIS рекомендует протестировать {recommended.get('low')}–{recommended.get('high')} объявлений."
    )
    result = {
        "status": "partial" if wordstat.get("status") != "ok" or avito.get("status") != "ok" else "ok",
        "market_snapshot_id": "ms_" + hashlib.sha256(
            f"{query}|{region}|{_now().isoformat()}".encode("utf-8")
        ).hexdigest()[:20],
        "query": query,
        "region": region,
        "category": category,
        "created_at": _now().isoformat(),
        "summary": summary,
        "demand": {
            **demand,
            "level": demand.get("label"),
            "demand_level": demand.get("label"),
            "level_code": demand.get("level"),
            "exact_monthly_requests": total,
            "total_count": total,
            "source": wordstat.get("source"),
            "top_requests": wordstat.get("top_requests") or [],
            "associations": wordstat.get("associations") or [],
            "commercial_share_percent": wordstat.get("commercial_share_percent"),
        },
        "competitors": {
            "direct_sample": (
                avito.get("sampled_unique_sellers")
                if int(avito.get("sampled_unique_sellers") or 0) > 0
                else avito.get("sample_direct")
            ),
            "estimated_total": competitors,
            "relevant_offers": relevant_offers,
            "confidence": "medium" if avito.get("status") == "ok" else "low",
        },
        "market": {
            "direct_competitors": competitors,
            "relevant_offers": relevant_offers,
            "relevant_listings": relevant_offers,
            "sample_direct": avito.get("sample_direct"),
            "sampled_unique_sellers": avito.get("sampled_unique_sellers"),
            "ads_per_seller_median": avito.get("ads_per_seller_median"),
            "source": avito.get("source"),
            "confidence": "medium" if avito.get("status") == "ok" else "low",
            "price": price,
            "common_title_terms": generic_words,
            "items": avito.get("sample") or [],
        },
        "competition": {
            "estimated_competitors": competitors,
            "estimated_relevant_offers": relevant_offers,
            "sample_direct": avito.get("sample_direct"),
            "sampled_unique_sellers": avito.get("sampled_unique_sellers"),
            "ads_per_seller_median": avito.get("ads_per_seller_median"),
            "source": avito.get("source"),
            "confidence": "medium" if avito.get("status") == "ok" else "low",
        },
        "prices": {
            **price,
            "own_price_rub": own_price_rub,
            "own_position": own_price_position,
        },
        "recommended_ads": {
            **recommended,
            "cluster_count": int(((recommended.get("formula") or {}).get("semantic_clusters") or 0)),
            "reason": {
                **(recommended.get("reason") or {}),
                "demand_count": total,
            },
        },
        "positioning": {
            **differentiation,
            "market_common": generic_words,
            "observed": [
                ("В заголовках часто повторяются: " + ", ".join(generic_words))
                if generic_words else "Рынок пока не дал достаточно повторяющихся формулировок для уверенного вывода."
            ],
            "ideas_to_test": differentiation["ideas_to_validate"],
        },
        "market_score": score,
        "sources": [
            {
                "source": wordstat.get("source") or "Яндекс Вордстат",
                "status": wordstat.get("status"),
                "checked_at": wordstat.get("checked_at"),
            },
            {
                "source": avito.get("source") or "Публичная выдача Avito",
                "status": avito.get("status"),
                "checked_at": avito.get("checked_at"),
            },
        ],
        "partial": wordstat.get("status") != "ok" or avito.get("status") != "ok",
    }
    if progress:
        progress("ANALYSIS_READY")
    return result
