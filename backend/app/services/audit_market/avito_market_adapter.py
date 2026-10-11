from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections import Counter
from typing import Any
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from .query_normalizer import relevance, query_tokens

_DIRECT_BLOCKED_UNTIL = 0.0
_ROUTE_COOLDOWN_SEC = max(60, int(os.getenv("BORIS_AUDIT_AVITO_ROUTE_COOLDOWN_SEC", "900") or 900))


def _page_total(html: str) -> int | None:
    import re
    soup = BeautifulSoup(html or "", "lxml")
    for marker in ("page-title/count", "search-results/total-count"):
        el = soup.find(attrs={"data-marker": marker})
        if el:
            digits = re.sub(r"\D+", "", el.get_text(" ", strip=True))
            if digits:
                return int(digits)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    match = re.search(r"(\d[\d\s]{1,12})\s+(?:объявлен|предложен)", title.lower())
    if match:
        return int(re.sub(r"\s+", "", match.group(1)))
    return None

async def _audit_fetch_avito_html(url: str, timeout_ms: int = 25000, wait_ms: int = 3500) -> dict[str, Any]:
    global _DIRECT_BLOCKED_UNTIL
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    stealth = Stealth()
    last_error: Exception | None = None

    async def fetch_once(proxy: dict[str, Any] | None, nav_timeout_ms: int | None = None, settle_ms: int | None = None) -> dict[str, Any]:
        browser = None
        page = None
        traffic = {"bytes": 0, "requests": 0}
        try:
            async with async_playwright() as p:
                kwargs: dict[str, Any] = {"headless": True, "args": ["--no-sandbox"]}
                if proxy:
                    kwargs["proxy"] = proxy
                else:
                    kwargs["args"] = ["--no-sandbox", "--no-proxy-server"]
                browser = await p.chromium.launch(**kwargs)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()

                async def block_heavy(route):
                    try:
                        if route.request.resource_type in ("image", "stylesheet", "font", "media"):
                            await route.abort()
                        else:
                            await route.continue_()
                    except (PlaywrightError, PlaywrightTimeoutError, RuntimeError):
                        return

                def count_response(response):
                    try:
                        raw = response.headers.get("content-length") or "0"
                        traffic["bytes"] += max(0, int(raw))
                        traffic["requests"] += 1
                    except (AttributeError, TypeError, ValueError):
                        return

                await page.route("**/*", block_heavy)
                page.on("response", count_response)
                response = await page.goto(url, timeout=int(nav_timeout_ms or timeout_ms), wait_until="domcontentloaded")
                await page.wait_for_timeout(int(settle_ms if settle_ms is not None else wait_ms))
                html = await page.content()
                final_url = page.url
                page_title = (await page.title()).strip().lower()
                blocked_ip = (
                    "доступ ограничен" in page_title
                    or "проблема с ip" in page_title
                    or "access denied" in page_title
                    or ("ip address" in page_title and "blocked" in page_title)
                    or "доступ ограничен: проблема с ip" in html.lower()
                )
                captcha = "firewallCaptcha" in html or "geetest_captcha" in html
                if blocked_ip:
                    raise RuntimeError("avito_ip_blocked")
                if captcha:
                    raise RuntimeError("avito_captcha")

                mb = traffic["bytes"] / 1024 / 1024
                proxy_cost_known = True
                proxy_rub: float | None = 0.0
                if proxy:
                    try:
                        rub_per_gb = float(os.getenv("BORIS_AUDIT_PROXY_RUB_PER_GB", "0") or 0)
                    except (TypeError, ValueError):
                        rub_per_gb = 0.0
                    if rub_per_gb > 0:
                        proxy_rub = round(mb / 1024 * rub_per_gb, 4)
                    else:
                        proxy_rub = None
                        proxy_cost_known = False
                return {
                    "html": html,
                    "final_url": final_url,
                    "http_status": response.status if response else None,
                    "transport": "proxy" if proxy else "direct",
                    "traffic_mb": round(mb, 4),
                    "proxy_cost_rub": proxy_rub,
                    "proxy_cost_known": proxy_cost_known,
                }
        finally:
            if page is not None:
                try:
                    await page.unroute_all(behavior="ignoreErrors")
                except (PlaywrightError, PlaywrightTimeoutError):
                    page = None
            if browser is not None:
                try:
                    await browser.close()
                except (PlaywrightError, PlaywrightTimeoutError):
                    browser = None

    if time.time() >= _DIRECT_BLOCKED_UNTIL:
        try:
            return await fetch_once(None, nav_timeout_ms=min(timeout_ms, 8000), settle_ms=min(wait_ms, 1800))
        except Exception as exc:
            last_error = exc
            if "avito_captcha" in str(exc):
                raise
            _DIRECT_BLOCKED_UNTIL = time.time() + _ROUTE_COOLDOWN_SEC

    try:
        from proxy_pool import (
            PROXY_HOST,
            PROXY_PASS,
            PROXY_USER,
            find_clean_port,
            get_avito_proxy_health,
        )
        health = get_avito_proxy_health()
        checked_at = float(health.get("checked_at") or 0)
        if (
            health.get("state") in {"avito_blocked", "auth_failed", "transport_down", "transport_degraded"}
            and time.time() - checked_at < _ROUTE_COOLDOWN_SEC
        ):
            raise RuntimeError("avito_route_unavailable_cached")

        port = await find_clean_port(max_tries=3, cache_key="audit-market", force_refresh=False)
        if not port:
            raise RuntimeError("avito_no_clean_route")
        proxy = {
            "server": f"http://{PROXY_HOST}:{int(port)}",
            "username": PROXY_USER,
            "password": PROXY_PASS,
        }
        return await fetch_once(proxy)
    except Exception as exc:
        last_error = exc

    raise RuntimeError("audit_avito_browser_unavailable:" + str(last_error)[:180])


async def _collect_async(query: str, region: str, limit: int) -> dict[str, Any]:
    from app.api import parser as P

    city = str(region or "Москва").strip() or "Москва"
    url = f"https://www.avito.ru/{P._normalize_city(city)}?q={quote_plus(query)}"
    html = ""
    items = []
    last_error = None
    transport_meta: dict[str, Any] = {}

    try:
        page = await _audit_fetch_avito_html(url)
        transport_meta = {k: page.get(k) for k in ("transport", "traffic_mb", "proxy_cost_rub", "proxy_cost_known")}
        html = str(page.get("html") or "")
        if "firewallCaptcha" in html or "geetest_captcha" in html:
            last_error = "source_unavailable"
            items = []
        else:
            items = P._extract_listing_items(html)
    except Exception as exc:
        last_error = type(exc).__name__

    classified = []
    seen = set()
    for row in items:
        title = str(row.get("title") or "").strip()
        key = str(row.get("url") or "") or title.lower()
        if not title or not key or key in seen:
            continue
        seen.add(key)
        cls = relevance(title, query)
        classified.append({
            "title": title,
            "price": row.get("price"),
            "url": row.get("url"),
            "relevance": cls,
        })
        if len(classified) >= max(10, limit * 3):
            break

    direct_all = [x for x in classified if x["relevance"] == "direct"]
    direct = direct_all[:limit]
    adjacent = [x for x in classified if x["relevance"] == "adjacent"][:limit]
    prices = sorted(int(x["price"]) for x in direct if isinstance(x.get("price"), (int, float)) and x["price"] > 0)
    category_slugs = []
    for row in direct:
        parts = [x for x in urlparse(str(row.get("url") or "")).path.split("/") if x]
        if len(parts) >= 3:
            slug = parts[1].strip()
            if slug and slug not in category_slugs:
                category_slugs.append(slug)
    category_slug = category_slugs[0] if len(category_slugs) == 1 else None
    page_total = _page_total(html)
    direct_ratio = (len(direct_all) / max(1, len(classified))) if classified else 0.0
    estimated_relevant_offers = (
        max(len(direct_all), int(round(page_total * direct_ratio)))
        if page_total is not None and direct_ratio > 0 else None
    )

    detail_rows = direct[: min(10, len(direct))]
    sem = asyncio.Semaphore(3)
    async def enrich(row):
        description = ""
        photos_count = 0
        seller_name = ""
        if row.get("url"):
            async with sem:
                try:
                    raw_page = await _audit_fetch_avito_html(row["url"], timeout_ms=18000, wait_ms=1800)
                    raw_html = str(raw_page.get("html") or "")
                    if "firewallCaptcha" not in raw_html and "geetest_captcha" not in raw_html:
                        soup = BeautifulSoup(raw_html, "lxml")
                        desc = soup.find(attrs={"itemprop": "description"})
                        description = " ".join(desc.get_text(" ", strip=True).split())[:2500] if desc else ""
                        photos_count = len(soup.find_all(attrs={"data-marker": "image-preview/item"}))
                        if photos_count == 0:
                            photos_count = len({
                                str(img.get("src") or img.get("content") or img.get("data-src") or "").strip()
                                for img in soup.find_all(attrs={"itemprop": "image"})
                                if str(img.get("src") or img.get("content") or img.get("data-src") or "").strip()
                            })
                        for marker in ("seller-info/name", "seller-info/label", "seller-info/value"):
                            el = soup.find(attrs={"data-marker": marker})
                            text = " ".join(el.get_text(" ", strip=True).split()) if el else ""
                            if 1 < len(text) <= 160:
                                seller_name = text
                                break
                except (RuntimeError, ValueError, TypeError, AttributeError):
                    raw_page = {}
        return {
            **row,
            "description": description,
            "photos_count": int(photos_count or 0),
            "seller_name": seller_name or None,
            "_transport": (raw_page.get("transport") if row.get("url") and 'raw_page' in locals() and isinstance(raw_page, dict) else None),
            "_traffic_mb": float(raw_page.get("traffic_mb") or 0) if row.get("url") and 'raw_page' in locals() and isinstance(raw_page, dict) else 0.0,
            "_proxy_cost_rub": raw_page.get("proxy_cost_rub") if row.get("url") and 'raw_page' in locals() and isinstance(raw_page, dict) else 0.0,
            "_proxy_cost_known": bool(raw_page.get("proxy_cost_known", True)) if row.get("url") and 'raw_page' in locals() and isinstance(raw_page, dict) else True,
        }
    enriched = await asyncio.gather(*[enrich(x) for x in detail_rows]) if detail_rows else []
    detail_traffic_mb = sum(float(x.get("_traffic_mb") or 0) for x in enriched)
    detail_proxy_cost_known = all(bool(x.get("_proxy_cost_known", True)) for x in enriched)
    detail_proxy_cost = 0.0
    for x in enriched:
        value = x.get("_proxy_cost_rub")
        if value is not None:
            detail_proxy_cost += float(value or 0)
    if transport_meta:
        transport_meta["traffic_mb"] = round(float(transport_meta.get("traffic_mb") or 0) + detail_traffic_mb, 4)
        if transport_meta.get("proxy_cost_rub") is None or not detail_proxy_cost_known:
            transport_meta["proxy_cost_rub"] = None
            transport_meta["proxy_cost_known"] = False
        else:
            transport_meta["proxy_cost_rub"] = round(float(transport_meta.get("proxy_cost_rub") or 0) + detail_proxy_cost, 4)
            transport_meta["proxy_cost_known"] = bool(transport_meta.get("proxy_cost_known", True) and detail_proxy_cost_known)

    words = Counter()
    for row in direct:
        for tok in query_tokens(row.get("title") or ""):
            words[tok] += 1
    common_title_terms = [w for w, _ in words.most_common(12)]

    seller_names = sorted({
        " ".join(str(x.get("seller_name") or "").split()).strip()
        for x in enriched
        if " ".join(str(x.get("seller_name") or "").split()).strip()
    })
    seller_counts = Counter(
        " ".join(str(x.get("seller_name") or "").split()).strip()
        for x in enriched
        if " ".join(str(x.get("seller_name") or "").split()).strip()
    )
    ads_per_seller_median = (
        float(statistics.median(list(seller_counts.values())))
        if seller_counts else None
    )
    estimated_competitors = (
        max(len(seller_names), int(round(estimated_relevant_offers / max(1.0, ads_per_seller_median))))
        if estimated_relevant_offers is not None and ads_per_seller_median else None
    )

    descriptions = " ".join(str(x.get("description") or "").lower() for x in enriched)
    generic = []
    for marker in ("качество","гарантия","низкие цены","быстро","недорого","профессионально","индивидуальный подход"):
        if marker in descriptions:
            generic.append(marker)

    return {
        "status": "ok" if direct else "partial",
        "source": "Публичная выдача Avito",
        "query": query,
        "region": city,
        "category_slug": category_slug,
        "direct_competitors_sample": len(seller_names) if seller_names else None,
        "unique_sellers_sample": len(seller_names) if seller_names else None,
        "competing_offers_sample": len(direct),
        "search_total": page_total,
        "estimated_relevant_offers": estimated_relevant_offers,
        "estimated_competitors": estimated_competitors,
        "ads_per_seller_median": ads_per_seller_median,
        "competitor_count_basis": "unique_sellers" if seller_names else "offers_only",
        "adjacent_sample": len(adjacent),
        "relevant_ads_sample": len(direct),
        "prices": {
            "min": prices[0] if prices else None,
            "median": int(statistics.median(prices)) if prices else None,
            "max": prices[-1] if prices else None,
        },
        "common_title_terms": common_title_terms,
        "generic_positioning": generic[:8],
        "sample_ads": enriched[:8],
        "source_url": url,
        "transport": transport_meta.get("transport"),
        "traffic_mb": transport_meta.get("traffic_mb"),
        "proxy_cost_rub": transport_meta.get("proxy_cost_rub"),
        "confidence": "high" if len(direct) >= 8 else ("medium" if len(direct) >= 3 else "low"),
        "message": None if direct else (last_error or "Мало релевантных объявлений"),
    }

def collect_avito_market(query: str, region: str = "", limit: int = 10) -> dict[str, Any]:
    return asyncio.run(_collect_async(query, region, max(3, min(int(limit or 10), 20))))
