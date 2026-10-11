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

# High-signal software/AI directory routes are a separate fixed pool. These
# queries do not multiply by every Russian service term and therefore rotate
# efficiently while the reusable autonomous reserve is below target.
QUERY_POOL_VERSION = 2
FIXED_SEARCH_QUERIES = [
    '"submit ai tool" free directory',
    '"submit your ai tool" directory',
    '"add your ai tool" directory',
    '"list your ai tool" free',
    '"submit ai agent" directory',
    '"submit your agent" ai directory',
    '"submit saas" free directory',
    '"submit your saas" directory',
    '"add saas" directory free',
    '"submit software" directory free',
    '"add software" directory free',
    '"submit startup" free directory',
    '"add startup" directory free',
    '"submit tool" "no registration"',
    '"submit website" "no registration" directory',
    '"vendor submission" software directory',
    '"vendor submission" ai tools',
    '"free tool submission" ai directory',
    '"submit product" saas directory',
    '"add product" software directory',
    '"submit app" software directory free',
    '"submit resource" developer tools directory',
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
    # AUTONOMOUS_FIRST_CURATED_SEEDS_V1
    # Current public authoring pages. These are candidates, not publication
    # permission: the independent rule/form audit still fails closed.
    {
        "domain": "otsiv.ru",
        "url": "https://otsiv.ru/org/create/",
        "title": "Добавить компанию в каталог бесплатно",
        "snippet": "Добавить компанию можно без регистрации; адрес сайта поддерживается.",
        "kind": "catalog",
    },
    {
        "domain": "otsovik.com",
        "url": "https://otsovik.com/job/add_company.php",
        "title": "Каталог компаний: добавить компанию бесплатно",
        "snippet": "Можно без авторизации анонимно; активная ссылка на сайт поддерживается.",
        "kind": "catalog",
    },
    {
        "domain": "pdfcatalog.ru",
        "url": "https://pdfcatalog.ru/index/addsite",
        "title": "Добавить сайт в белый каталог",
        "snippet": "Бесплатно и без регистрации; только прямые ссылки; ручная модерация.",
        "kind": "catalog",
    },
    {
        "domain": "dumki.by",
        "url": "https://dumki.by/company/create",
        "title": "Добавить компанию в каталог бесплатно",
        "snippet": "Добавление без регистрации, но форма требует юридическое лицо и страну регистрации.",
        "kind": "catalog",
    },
    {
        "domain": "saitovkatalog.ucoz.ru",
        "url": "https://saitovkatalog.ucoz.ru/index/dobavit_sajt_v_katalog/0-8",
        "title": "Добавить сайт в каталог без регистрации",
        "snippet": "Бесплатно, без регистрации и без обратной ссылки; модерируемый каталог.",
        "kind": "catalog",
    },
    # Candidate-only seeds from a browser-verified 2026-09-28 no-login /
    # no-payment directory list. They are NOT trusted as permission: every one
    # still passes the live same-domain rule/form audit before outreach.
    {
        "domain": "aitoolsdirectory.com",
        "url": "https://aitoolsdirectory.com/submit-tool",
        "title": "AI Tools Directory · Submit Tool",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "futuretools.io",
        "url": "https://www.futuretools.io/submit-a-tool",
        "title": "FutureTools · Submit a Tool",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "gptbot.io",
        "url": "https://gptbot.io/submit-ai-tool",
        "title": "GPTBot · Submit AI Tool",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "thenextai.com",
        "url": "https://www.thenextai.com/submit-ai-tool/",
        "title": "The Next AI · Submit AI Tool",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "aisuperhub.io",
        "url": "https://www.aisuperhub.io/ai-tools/submit/free",
        "title": "AI SuperHub · Free AI Tool Submission",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "dropyourai.com",
        "url": "https://www.dropyourai.com/submit-tool",
        "title": "DropYourAI · Submit Tool",
        "snippet": "AI tool directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "launchingnext.com",
        "url": "https://www.launchingnext.com/submit/",
        "title": "Launching Next · Submit Startup",
        "snippet": "Startup directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "startupstash.com",
        "url": "https://startupstash.com/add-listing/",
        "title": "Startup Stash · Add Listing",
        "snippet": "Startup directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "startupcollections.com",
        "url": "https://startupcollections.com/submit-product/",
        "title": "Startup Collections · Submit Product",
        "snippet": "Startup directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "launchpedia.co",
        "url": "https://launchpedia.co/submit/",
        "title": "Launchpedia · Submit",
        "snippet": "Startup directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    {
        "domain": "joinly.xyz",
        "url": "https://joinly.xyz/submit-startup",
        "title": "Joinly · Submit Startup",
        "snippet": "Startup directory; candidate reports free submission with no login.",
        "kind": "vendor_directory",
    },
    # SEO_FINAL_CLOSURE_CURATED_EXPANSION_V1
    # Current direct authoring routes verified from live 2026 sources. These
    # remain candidate-only until the BORIS rule/form audit approves them.
    {
        "domain": "launch.ignlab.net",
        "url": "https://launch.ignlab.net/submit.html",
        "title": "Ignlab Launch · Submit your SaaS or AI tool",
        "snippet": "Free submission; no account required; human review before the listing goes live.",
        "kind": "vendor_directory",
    },
    {
        "domain": "submitstartup.io",
        "url": "https://submitstartup.io/submit",
        "title": "Submit Startup · Submit your startup",
        "snippet": "Free startup/SaaS/AI submission form with review and a public directory listing.",
        "kind": "vendor_directory",
    },
    {
        "domain": "cataloxy.ru",
        "url": "https://www.cataloxy.ru/firms_add.htm",
        "title": "Cataloxy · Добавить компанию",
        "snippet": "Бесплатная регистрация компании; requires account verification, anti-bot check and terms acceptance.",
        "kind": "catalog",
    },
    {
        "domain": "brownbook.net",
        "url": "https://www.brownbook.net/add-business",
        "title": "Brownbook · Add a business",
        "snippet": "Global business directory; candidate-only direct add-business route from a browser-verified free directory list.",
        "kind": "catalog",
    },
    {
        "domain": "twelve.tools",
        "url": "https://twelve.tools/submit-your-tool",
        "title": "Twelve Tools · Submit your tool",
        "snippet": "Tool directory; candidate-only direct submission route; free route may require a badge.",
        "kind": "vendor_directory",
    },
    {
        "domain": "thesaasdir.com",
        "url": "https://thesaasdir.com/submit/",
        "title": "TheSaaSDir · Submit",
        "snippet": "SaaS directory; candidate-only direct submission route; free route may require a badge.",
        "kind": "vendor_directory",
    },
    {
        "domain": "toolpilot.ai",
        "url": "https://www.toolpilot.ai/pages/submit-your-ai-tool",
        "title": "ToolPilot · Submit your AI tool",
        "snippet": "AI tool directory; candidate-only direct submission route; free route may require a badge.",
        "kind": "vendor_directory",
    },
    {
        "domain": "sitelike.org",
        "url": "https://www.sitelike.org/add-site/",
        "title": "SiteLike · Add site",
        "snippet": "Website directory; candidate-only direct add-site route.",
        "kind": "catalog",
    },
    {
        "domain": "submitaitools.org",
        "url": "https://submitaitools.org/submit-your-ai-tool/",
        "title": "Submit AI Tools · Submit your AI tool",
        "snippet": "AI directory; candidate-only direct submission route; free conditions require rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "onepagelove.com",
        "url": "https://onepagelove.com/submit",
        "title": "One Page Love · Submit",
        "snippet": "Website directory; candidate-only direct submission route.",
        "kind": "vendor_directory",
    },
    {
        "domain": "somuch.com",
        "url": "https://www.somuch.com/submit-links/",
        "title": "SoMuch · Submit links",
        "snippet": "General directory; candidate-only direct submission route.",
        "kind": "catalog",
    },
    {
        "domain": "library.phygital.plus",
        "url": "https://library.phygital.plus/tool-submission",
        "title": "Phygital+ Library · Tool submission",
        "snippet": "AI/software directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "whattheai.tech",
        "url": "https://whattheai.tech/submit",
        "title": "WhatTheAI · Submit",
        "snippet": "AI directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "ai-hunter.io",
        "url": "https://ai-hunter.io/submit-a-tool/",
        "title": "AI Hunter · Submit a tool",
        "snippet": "AI directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "aivalley.ai",
        "url": "https://aivalley.ai/submit-tool/",
        "title": "AI Valley · Submit tool",
        "snippet": "AI directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "toolscout.ai",
        "url": "https://toolscout.ai/submit",
        "title": "ToolScout · Submit",
        "snippet": "AI/software directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "launched.io",
        "url": "https://launched.io/newsubmission",
        "title": "Launched · New submission",
        "snippet": "Startup directory submission route; candidate-only until live rule audit.",
        "kind": "vendor_directory",
    },
    {
        "domain": "aisotools.com",
        "url": "https://aisotools.com/submit",
        "title": "AISO Tools · Submit Your AI Tool",
        "snippet": "Free AI tool listing with permanent backlink on the free tier; no CAPTCHA or account required.",
        "kind": "vendor_directory",
    },
    {
        "domain": "autoventure.live",
        "url": "https://autoventure.live/submit.html",
        "title": "AutoVenture · Submit your AI tool",
        "snippet": "Free AI tool submission form with website URL and human review; no CAPTCHA or account required.",
        "kind": "vendor_directory",
    },
    {
        "domain": "aisuperhub.io",
        "url": "https://www.aisuperhub.io/ai-tools/submit/free",
        "title": "AI SuperHub · Free AI Tool Submission",
        "snippet": "Free AI/software directory submission with website URL and manual review; no CAPTCHA or legal-consent checkbox.",
        "kind": "vendor_directory",
    },
    {
        "domain": "cybo.com",
        "url": "https://www.cybo.com/add-business",
        "title": "Cybo · Add a Business",
        "snippet": "Global business directory. Official help states account creation is free and a Website URL in the listing creates a backlink. Human account/terms checkpoint required.",
        "kind": "catalog",
        "niches": ["business", "services", "marketing", "it"],
        "language": "en",
    },
    {
        "domain": "worldbizdir.online",
        "url": "https://www.worldbizdir.online/add-listing",
        "title": "WorldBizDir · Add Your Business",
        "snippet": "Global directory with free listing, no credit card, 195 countries and public Visit Website links; human factual business details and terms still required.",
        "kind": "catalog",
        "niches": ["business", "services", "marketing", "it"],
        "language": "en",
    },
    {
        "domain": "iplocation.net",
        "url": "https://www.iplocation.net/web-agencies/add",
        "title": "IPLocation · Submit a Web Agency",
        "snippet": "Editorially reviewed agency directory with Website URL and Web Development, Mobile App Development and AI Development service categories.",
        "kind": "services",
        "niches": ["it"],
        "language": "en",
    },
    {
        "domain": "goodfirms.co",
        "url": "https://www.goodfirms.co/get-listed",
        "title": "GoodFirms · Get Listed",
        "snippet": "Official free company-profile application for B2B service companies and software vendors; human research verification required.",
        "kind": "services",
        "niches": ["it", "marketing"],
        "language": "en",
    },
    {
        "domain": "clutch.co",
        "url": "https://clutch.co/get-listed",
        "title": "Clutch · Get Listed",
        "snippet": "Official free company profile flow with website URL, service focus, location and contact information; account and factual company details required.",
        "kind": "services",
        "niches": ["it", "marketing"],
        "language": "en",
    },
    {
        "domain": "sortlist.com",
        "url": "https://www.sortlist.com/join",
        "title": "Sortlist · Register as a provider",
        "snippet": "Free agency/provider profile; freelancers and single-employee firms are allowed, with marketing, online advertising, software development and AI categories.",
        "kind": "services",
        "niches": ["it", "marketing"],
        "language": "en",
    },
    {
        "domain": "techbehemoths.com",
        "url": "https://techbehemoths.com/get-listed",
        "title": "TechBehemoths · Get Listed",
        "snippet": "Company directory registration route documented by TechBehemoths; company identity/profile details required.",
        "kind": "services",
        "niches": ["it", "marketing"],
        "language": "en",
    },
    {
        "domain": "hotfrog.com",
        "url": "https://www.hotfrog.com/add-business",
        "title": "Hotfrog · Add Business",
        "snippet": "Business directory with free profiles and website links; requires account and factual business information.",
        "kind": "catalog",
        "niches": ["business", "services", "marketing", "it"],
        "language": "en",
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
    "submit-tool", "submit_tool", "submit-a-tool", "submit-ai-tool",
    "submit-your-tool", "submit-your-ai-tool", "tool-submission", "newsubmission",
    "submit-links", "/submit.html", "/submit/", "/submit", "add-site", "add-business",
    "get-listed", "/join", "web-agencies/add", "add-your-company", "add-listing", "free-listing",
    "add-tool", "add_tool", "submit-ai", "submit_ai", "submit-product",
    "submit-saas", "submit_saas", "submit-startup", "vendor-submission", "/submit/free",
    "add-company", "add_company", "firms_add", "new-ad", "new_ad", "post-ad", "post_ad",
    "/company/create", "/cabinet/create", "/org/create", "/company/add", "add_company.php", "/index/addsite", "dobavit_sajt",
    "/dobavit-obyavlen", "/razmestit-obyavlen", "/podat-obyavlen",
    "/dobavit-kompani", "/dobavit-uslug", "new-topic", "create-thread",
)
AUTHORING_TEXT_MARKERS = (
    "добавить объявление", "подать объявление", "разместить объявление",
    "добавить компанию", "разместить услугу", "добавить услугу",
    "опубликовать статью", "разместить статью", "добавить материал",
    "добавить публикацию", "post an ad", "submit listing", "add listing",
    "add company", "submit article", "publish article",
    "submit ai tool", "add ai tool", "submit your tool", "add your tool",
    "submit saas", "add saas", "submit startup", "vendor submission",
)
NON_AUTHORING_PATH_MARKERS = (
    "/wiki/", "/article/", "/articles/", "/blog/", "/advice/", "/help/",
    "/news/", "/science/article/", "/support/", "/about/", "/land/",
    "/guide/", "/guides/", "/how-to/", "/howto/",
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
    "company_profile": ("профиль компании", "карточк", "company profile"),
    "vendor_directory": (
        "vendor", "agency directory", "software directory", "saas directory",
        "startup directory", "ai tool", "tool directory", "submit tool",
        "submit ai", "submit startup",
    ),
    "community_qa": ("вопрос", "ответ", "community", "q&a"),
}
AUTONOMY_POSITIVE_MARKERS = (
    "без регистрации", "без авторизации", "регистрация не требуется", "no registration",
    "no account", "no login", "without creating an account", "guest submission",
)
AUTONOMY_NEGATIVE_MARKERS = (
    "captcha", "капч", "защитный код", "sms", "телефон обязател",
    "sso", "войти", "авториз", "identity", "паспорт",
    "юридическое лицо", "регистрационный номер", "унп",
)
LINK_ALLOWED_MARKERS = (
    "адрес сайта", "ссылка на сайт", "website", "your url", "site url",
    "без обратной ссылки", "прямая ссылка", "прямые ссылки", "dofollow",
)


def _policy_scores(row: dict) -> dict:
    """Rank delivery feasibility before IKS; final rules/form checks stay strict."""
    hay = " ".join(
        str(row.get(k) or "") for k in ("url", "title", "snippet")
    ).lower()
    autonomy_evidence = [m for m in AUTONOMY_POSITIVE_MARKERS if m in hay]
    friction_evidence = [m for m in AUTONOMY_NEGATIVE_MARKERS if m in hay]
    # Do not let the negative stem "авториз" cancel the explicit positive
    # phrase "без авторизации". The latter is stronger, direct evidence that
    # the authoring route itself does not require a login.
    if "без авторизации" in hay:
        friction_evidence = [m for m in friction_evidence if m != "авториз"]
    link_evidence = [m for m in LINK_ALLOWED_MARKERS if m in hay]
    authoring = max(0, int(row.get("authoring_score") or 0))
    autonomy_score = min(10, 2 + 4 * bool(autonomy_evidence) - 2 * len(friction_evidence))
    friction_score = min(10, 2 * len(friction_evidence))
    publication_surface_score = min(10, authoring)
    link_policy_score = 8 if link_evidence else 3
    relevance_score = 8 if str(row.get("kind") or "") in {
        "catalog", "services", "company_profile", "vendor_directory", "ugc_article", "community_qa"
    } else 4
    tier = str(row.get("iks_tier") or "")
    quality_score = {"high": 10, "medium": 7, "low": 4}.get(tier, 2)
    seo_score = quality_score
    explicit_autonomous = bool(autonomy_evidence) and not friction_evidence
    lane = "autonomous_candidate" if explicit_autonomous else "human_assisted"
    total = (
        autonomy_score * 5
        + link_policy_score * 3
        + publication_surface_score * 2
        + relevance_score * 2
        + quality_score
        + seo_score
        - friction_score * 4
    )
    return {
        **row,
        "quality_score": quality_score,
        "seo_score": seo_score,
        "relevance_score": relevance_score,
        "publication_surface_score": publication_surface_score,
        "link_policy_score": link_policy_score,
        "autonomy_score": autonomy_score,
        "friction_score": friction_score,
        "autonomy_evidence": autonomy_evidence,
        "friction_evidence": friction_evidence,
        "link_policy_evidence": link_evidence,
        "delivery_lane": lane,
        "acquisition_score": total,
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
    # SEO_FINAL_CLOSURE_AUTHORING_ROUTE_V1
    # Generic action words are valid only when they are the actual root
    # authoring route (/add, /submit, ...). A trailing segment inside an
    # informational path such as /wiki/ADD must never count as an authoring
    # surface.
    route_path = route.split("?", 1)[0]
    exact_action_segment = bool(
        re.fullmatch(r"/(?:[^/]+/)*(?:post|add|submit|publish|write)/?", route_path)
    )
    informational_path = any(marker in route for marker in NON_AUTHORING_PATH_MARKERS)
    if not informational_path and (
        exact_action_segment or any(marker in route for marker in AUTHORING_ROUTE_MARKERS)
    ):
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
    queries = list(FIXED_SEARCH_QUERIES)
    queries.extend(
        pattern.format(term=term)
        for pattern in SEARCH_PATTERNS
        for term in TERMS
    )
    # Preserve deterministic rotation while preventing accidental duplicate
    # fixed/templated queries from wasting a bounded guardian pass.
    return list(dict.fromkeys(queries))


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
        if int(previous.get("query_pool_version") or 0) != QUERY_POOL_VERSION:
            offset = 0
        else:
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
        curated_verified = "curated_direct_verification" in set(old.get("search_engines") or [])
        if (
            not domain
            or domain in BLOCK_DOMAINS
            or score < 4
            or ("authoring_route" not in evidence and not curated_verified)
        ):
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
                # Bing can return a clean empty page while healthy alternatives
                # still have authoring results. Search both bounded engines so a
                # zero-result Bing response cannot stall autonomous reserve growth.
                use_ddg=True,
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
            # Search-result prose is not enough. The URL itself must
            # expose a concrete authoring/listing route before it can enter the
            # donor pool. This removes tutorials, Wikipedia pages, docs and
            # articles that merely contain phrases such as "добавить услугу".
            if authoring_score < 4 or "authoring_route" not in authoring_evidence:
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

    # AUTONOMOUS_FIRST_ACQUISITION_V1
    # Delivery feasibility and an allowed outbound link outrank IKS. Low/unknown
    # IKS is retained as quality metadata instead of excluding an autonomous
    # surface before its rule/form audit.
    measured = [_policy_scores(x) for x in measured]
    measured.sort(key=lambda x: (
        -int(x.get("acquisition_score") or 0),
        -int(x.get("autonomy_score") or 0),
        -int(x.get("link_policy_score") or 0),
        -(x.get("iks") or -1),
        x["domain"],
    ))
    admitted = [
        x for x in measured
        if int(x.get("authoring_score") or 0) >= 4
        and (
            (
                x.get("delivery_lane") == "autonomous_candidate"
                and str(x.get("kind") or "") in {
                    "catalog", "services", "company_profile",
                    "vendor_directory", "ugc_article", "classified",
                }
            )
            or (
                x.get("iks_tier") in {"high", "medium"}
                and str(x.get("kind") or "") in {
                    "catalog", "services", "company_profile",
                    "vendor_directory", "ugc_article", "classified",
                }
            )
            or (
                int(x.get("authoring_score") or 0) >= 8
                and str(x.get("kind") or "") in {
                    "catalog", "services", "company_profile",
                    "vendor_directory", "ugc_article", "classified",
                }
            )
            or (
                # Curated direct authoring routes may remain as LOW diversity
                # candidates when Yandex IKS is positive. This does not relax
                # search-discovered admission and does not authorize posting;
                # the live rule/form audit remains mandatory.
                "curated_direct_verification" in set(x.get("search_engines") or [])
                and int(x.get("iks") or 0) > 0
                and str(x.get("kind") or "") in {
                    "catalog", "services", "company_profile",
                    "vendor_directory", "ugc_article", "classified",
                }
            )
        )
    ]
    payload = {
        "updated_at": _now(),
        "policy": "autonomous_first",
        "query_pool_version": QUERY_POOL_VERSION,
        "queries": len(queries),
        "query_offset": offset,
        "next_query_offset": next_query_offset,
        "query_pool_size": len(_query_pool()),
        "cycle_domains_found": len(cycle_domains),
        "domains_found": len(measured),
        "strong_domains": len(admitted),
        "autonomous_candidates": sum(1 for x in admitted if x.get("delivery_lane") == "autonomous_candidate"),
        "human_assisted_candidates": sum(1 for x in admitted if x.get("delivery_lane") == "human_assisted"),
        "high": sum(1 for x in admitted if x.get("iks_tier") == "high"),
        "medium": sum(1 for x in admitted if x.get("iks_tier") == "medium"),
        "items": admitted,
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
