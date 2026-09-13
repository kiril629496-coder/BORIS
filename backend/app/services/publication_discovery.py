from __future__ import annotations

import concurrent.futures
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.services import forum_discovery, forum_quality

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT / "data" / "service_marketplaces" / "publication_discovery.json"

# PUBLICATION_DISCOVERY_V1
# Broader than forum discovery: own listings, service catalogs and author pages.
SEARCH_PATTERNS = [
    'inurl:add {term} услуги',
    'inurl:post {term} услуги',
    'inurl:create {term} компания',
    'inurl:dobavit {term} объявление',
    'inurl:razmestit {term} услуга',
    '"разместить объявление бесплатно" {term}',
    '"подать объявление бесплатно" {term}',
    '"добавить объявление" {term} услуги',
    '"разместить услугу" {term} бесплатно',
    '"каталог услуг" {term} "добавить"',
    '"добавить компанию" {term} каталог',
    '"бизнес каталог" {term} "добавить компанию"',
    '"коммерческие предложения" {term} "добавить"',
    '"опубликовать статью бесплатно" {term}',
    '"разместить статью бесплатно" {term}',
    '"блог компаний" {term} публикация',
    '"публикация без модерации" {term} статья',
    # ZERO_FRICTION_PUBLICATION_DISCOVERY_V1
    # The delivery bottleneck is external onboarding, not raw candidate count.
    # Search explicitly for authoring surfaces that advertise no account/CAPTCHA
    # and a direct/public link path instead of repeatedly filling the queue with
    # otherwise-valid sites that can only stop at a human checkpoint.
    '"без регистрации" "добавить сайт" {term}',
    '"без регистрации" "разместить объявление" {term}',
    '"без капчи" "добавить сайт" {term}',
    '"прямая ссылка" "добавить сайт" {term}',
    '"no registration" "submit website" {term}',
    '"no account" "submit website" {term}',
    '"free listing" "no registration" {term}',
]

TERMS = [
    "IT", "программирование", "разработка", "CRM", "автоматизация бизнеса",
    "SEO", "маркетинг", "реклама", "услуги для бизнеса", "1С", "телефония",
    "аналитика", "SaaS", "digital", "business services",
]

BLOCK_DOMAINS = {
    "avito.ru", "youla.ru",  # handled by dedicated acquisition/product flows
    "youtube.com", "vk.com", "t.me", "facebook.com", "instagram.com",
}

# CURATED_STRONG_OWN_LISTING_SEEDS_V1
# Search engines regularly miss login-gated company creation routes. These
# seeds were verified directly on 2026-09-10 and still pass the same IKS and
# fail-closed platform-rule audit as search-discovered candidates.
CURATED_STRONG_OWN_LISTING_SEEDS = (
    {
        "domain": "yell.ru",
        "url": "https://www.yell.ru/company/create/",
        "title": "Yell · Добавить компанию",
        "snippet": "Каталог компаний; собственный маршрут добавления компании.",
        "kind": "catalog",
    },
    {
        "domain": "orgpage.ru",
        "url": "https://www.orgpage.ru/Cabinet/Create/",
        "title": "Orgpage · Разместить компанию",
        "snippet": "Каталог организаций; форма сообщает, что добавление компании абсолютно бесплатно.",
        "kind": "catalog",
    },
)

# PUBLICATION_DISCOVERY_AUTHORING_SIGNAL_V2
# High IKS alone is not enough. Search results about advertising/blogging were
# polluting the audit queue (Wikipedia, help pages, vendor blogs, login pages).
# Admit only hits that themselves look like an authoring/listing route or have
# an explicit authoring CTA in the result title. The rule engine still performs
# the final fail-closed inspection before any outreach is enabled.
AUTHORING_ROUTE_MARKERS = (
    "add-listing", "add_listing", "submit-listing", "submit_listing",
    "add-company", "add_company", "new-ad", "new_ad", "post-ad", "post_ad",
    "/company/create", "/cabinet/create",
    "/dobavit-obyavlen", "/razmestit-obyavlen", "/podat-obyavlen",
    "/dobavit-kompani", "/dobavit-uslug", "new-topic", "create-thread",
)
AUTHORING_TEXT_MARKERS = (
    "добавить объявление", "подать объявление", "разместить объявление",
    "добавить компанию", "разместить услугу", "добавить услугу",
    "опубликовать статью", "разместить статью", "добавить материал",
    "добавить публикацию", "post an ad", "submit listing", "add listing",
    "add company", "submit article", "publish article",
)
NON_AUTHORING_PATH_MARKERS = (
    "/wiki/", "/article/", "/articles/", "/blog/", "/advice/", "/help/",
    "/news/", "/science/article/", "/support/", "/about/",
)
INFORMATIONAL_TEXT_MARKERS = (
    "где можно разместить", "где разместить объявление", "список сайтов",
    "сайты объявлений", "обзор площадок", "подборка площадок",
    "где бесплатно опубликовать", "где опубликовать статью",
    "лучшие сайты", "топ-", "топ ", "how to ", "best sites",
)
VERTICAL_ONLY_ROUTE_MARKERS = (
    "nedvizhim", "недвижимост", "real-estate", "real_estate",
    "prodazhe-avto", "prodazha-avto", "продажа-авто",
)

TYPE_MARKERS = {
    "classified": ("объявлен", "classified", "доска"),
    "catalog": ("каталог", "directory", "компани"),
    "ugc_article": ("стать", "блог", "публикац", "article", "blog"),
    "services": ("услуг", "service", "исполнител"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _kind(title: str, snippet: str, url: str) -> str:
    hay = f"{title} {snippet} {url}".lower()
    best = ("other", 0)
    for kind, markers in TYPE_MARKERS.items():
        score = sum(1 for x in markers if x in hay)
        if score > best[1]:
            best = (kind, score)
    return best[0]


def _authoring_score(url: str, title: str, snippet: str) -> tuple[int, list[str]]:
    try:
        parsed = urlparse(url)
        route = f"{parsed.path}?{parsed.query}".lower()
    except Exception:
        route = str(url or "").lower()
    title_l = str(title or "").lower()
    snippet_l = str(snippet or "").lower()
    evidence: list[str] = []
    score = 0
    # Short verbs such as /add must be a complete URL path segment. A naive
    # substring check turns informational routes like /add-keyboard-layout into
    # false publication surfaces. Longer explicit marketplace slugs remain
    # valid substring markers.
    exact_action_segment = bool(re.search(r"/(?:post|add|submit|publish|write)/?$", route.split("?", 1)[0]))
    if exact_action_segment or any(marker in route for marker in AUTHORING_ROUTE_MARKERS):
        score += 5
        evidence.append("authoring_route")
    if any(marker in title_l for marker in AUTHORING_TEXT_MARKERS):
        score += 4
        evidence.append("authoring_title")
    if any(marker in snippet_l for marker in AUTHORING_TEXT_MARKERS):
        score += 1
        evidence.append("authoring_snippet")
    if any(marker in route for marker in NON_AUTHORING_PATH_MARKERS) and "authoring_route" not in evidence:
        score -= 4
        evidence.append("informational_path")
    informational_text = any(marker in title_l or marker in snippet_l for marker in INFORMATIONAL_TEXT_MARKERS)
    if informational_text and "authoring_route" not in evidence:
        score -= 6
        evidence.append("informational_text")
    route_and_title = f"{route} {title_l}"
    if any(marker in route_and_title for marker in VERTICAL_ONLY_ROUTE_MARKERS):
        score -= 8
        evidence.append("irrelevant_vertical")
    return score, evidence


def _query_pool() -> list[str]:
    return [pattern.format(term=term) for pattern in SEARCH_PATTERNS for term in TERMS]


def discover(max_queries: int = 120, per_query: int = 10, workers: int = 10) -> dict:
    # PUBLICATION_DISCOVERY_ROTATING_BATCH_V3
    # Each scheduled pass continues from the previous search offset. This lets
    # the 10-minute guardian cover the whole search matrix over the day without
    # blocking one run for several minutes or restarting from query #1.
    previous = load()
    pool = _query_pool()
    if not pool:
        return previous
    try:
        offset = int(previous.get("next_query_offset") or 0) % len(pool)
    except Exception:
        offset = 0
    count = max(1, min(int(max_queries or 1), len(pool)))
    queries = [pool[(offset + i) % len(pool)] for i in range(count)]
    next_query_offset = (offset + count) % len(pool)

    raw = {}
    for seed in CURATED_STRONG_OWN_LISTING_SEEDS:
        score, evidence = _authoring_score(
            str(seed.get("url") or ""),
            str(seed.get("title") or ""),
            str(seed.get("snippet") or ""),
        )
        if score < 4:
            continue
        raw[str(seed["domain"])] = {
            **seed,
            "authoring_score": score,
            "authoring_evidence": evidence,
            "queries": ["curated_strong_own_listing_seed"],
            "search_engines": ["curated_direct_verification"],
        }

    # Re-evaluate the previous pool with the current authoring detector so a
    # rule improvement immediately purges stale informational noise while true
    # authoring candidates survive a temporary search-engine outage.
    for old in (previous.get("items") or []):
        domain = _domain(str(old.get("url") or "")) or str(old.get("domain") or "")
        score, evidence = _authoring_score(
            str(old.get("url") or ""),
            str(old.get("title") or ""),
            str(old.get("snippet") or ""),
        )
        if not domain or domain in BLOCK_DOMAINS or score < 4:
            continue
        raw[domain] = {
            **old,
            "domain": domain,
            "authoring_score": score,
            "authoring_evidence": evidence,
            "queries": list(old.get("queries") or []),
            "search_engines": list(old.get("search_engines") or []),
        }

    errors = []
    cycle_domains = set()

    def search_one(q: str):
        try:
            hits, errs = forum_discovery._search_all(
                q,
                limit=per_query,
                use_ddg=False,
                request_timeout_s=5,
            )
            return q, hits, errs, None
        except Exception as exc:
            return q, [], [], f"{type(exc).__name__}: {exc}"

    # Search a few query variants concurrently. Each underlying HTTP request is
    # capped at five seconds, so one blocked search engine cannot monopolize the
    # acquisition guardian.
    search_workers = max(1, min(3, int(workers or 1), len(queries)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=search_workers) as pool_exec:
        search_results = list(pool_exec.map(search_one, queries))

    for q, hits, errs, fatal_error in search_results:
        if fatal_error:
            errors.append({"query": q, "error": fatal_error})
            continue
        errors.extend({"query": q, "error": e} for e in errs)
        for h in hits:
            url = str(h.get("url") or "").strip()
            domain = _domain(url)
            if not domain or domain in BLOCK_DOMAINS:
                continue
            title = str(h.get("title") or "")
            snippet = str(h.get("snippet") or "")
            authoring_score, authoring_evidence = _authoring_score(url, title, snippet)
            # Only direct/explicit authoring evidence enters the expensive
            # IKS + rule-audit pipeline. Generic informational hits are not
            # publication inventory.
            if authoring_score < 4:
                continue
            candidate = {
                "domain": domain,
                "url": url,
                "title": title,
                "snippet": snippet[:800],
                "kind": _kind(title, snippet, url),
                "authoring_score": authoring_score,
                "authoring_evidence": authoring_evidence,
                "queries": [],
                "search_engines": [],
            }
            row = raw.get(domain)
            if row is None or int(candidate["authoring_score"]) > int(row.get("authoring_score") or 0):
                if row:
                    candidate["queries"] = list(row.get("queries") or [])
                    candidate["search_engines"] = list(row.get("search_engines") or [])
                raw[domain] = candidate
                row = candidate
            cycle_domains.add(domain)
            if q not in row["queries"]:
                row["queries"].append(q)
            eng = h.get("engine")
            if eng and eng not in row["search_engines"]:
                row["search_engines"].append(eng)

    # PUBLICATION_DISCOVERY_IKS_CACHE_V1
    # Re-measuring every surviving domain on every rotating search batch can
    # spend more wall-clock time than search itself. Preserve already measured
    # candidate IKS and reuse the shared quality cache; only genuinely new
    # authoring domains go to Yandex Siteinfo.
    quality_cache = forum_quality.by_domain()

    def measure(row):
        existing_iks = row.get("iks")
        existing_tier = row.get("iks_tier")
        if isinstance(existing_iks, int) and existing_tier in {"high", "medium", "low"}:
            return {**row, "iks_source": row.get("iks_source") or "publication_discovery_cache"}
        cached = quality_cache.get(str(row.get("domain") or "")) or {}
        if isinstance(cached.get("iks"), int):
            return {
                **row,
                "iks": cached.get("iks"),
                "iks_tier": cached.get("tier") or forum_quality.tier_for_iks(cached.get("iks")),
                "iks_source": cached.get("source") or "forum_quality_cache",
            }
        q = forum_quality.fetch_iks(row["domain"])
        return {**row, "iks": q.get("iks"), "iks_tier": q.get("tier"), "iks_source": q.get("source")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as iks_pool:
        measured = list(iks_pool.map(measure, raw.values()))

    measured.sort(key=lambda x: (-(x.get("iks") or -1), -int(x.get("authoring_score") or 0), x["domain"]))
    strong = [
        x for x in measured
        if x.get("iks_tier") in {"high", "medium"}
        and int(x.get("authoring_score") or 0) >= 4
    ]
    payload = {
        "updated_at": _now(),
        "queries": len(queries),
        "query_offset": offset,
        "next_query_offset": next_query_offset,
        "query_pool_size": len(_query_pool()),
        "cycle_domains_found": len(cycle_domains),
        "domains_found": len(measured),
        "strong_domains": len(strong),
        "high": sum(1 for x in strong if x.get("iks_tier") == "high"),
        "medium": sum(1 for x in strong if x.get("iks_tier") == "medium"),
        "items": strong,
        "errors": errors[-100:],
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)
    return payload


def load() -> dict:
    if not DATA_FILE.exists():
        return {"updated_at": None, "queries": 0, "domains_found": 0, "strong_domains": 0, "items": []}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": None, "queries": 0, "domains_found": 0, "strong_domains": 0, "items": []}
