from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "service_marketplaces"
DISCOVERY_FILE = STATE_DIR / "discovered_forums.json"
DISCOVERY_RUN_FILE = STATE_DIR / "discovery_status.json"
TERMINAL_DOMAINS_FILE = STATE_DIR / "terminal_forum_domains.json"
DIRECTORY_SCAN_FILE = STATE_DIR / "forum_directory_scan.json"
FINDAFORUM_SCAN_FILE = STATE_DIR / "findaforum_scan.json"
FINDAFORUM_ENTRIES_FILE = STATE_DIR / "findaforum_entries.json"
TERMINAL_RECHECK_DAYS = 30
# HUMAN_CHECKPOINTS_ARE_NOT_TERMINAL_V1
# CAPTCHA/terms/codes mean "prepare account and wait for a human step", not
# "discard this domain for 30 days". Only genuinely dead/forbidden sites live
# in the terminal registry.
HUMAN_CHECKPOINT_REASONS = {
    "captcha_required",
    "captcha_age_and_terms_required",
    "terms_acceptance_required",
    "registration_agreement_required",
    "age_and_terms_declaration_required",
    "sms_or_verification_code_required",
    "email_verification_required",
    "business_email_required",
    "manual_verification",
}
DIRECTORY_RECHECK_DAYS = 30
FINDAFORUM_CACHE_DAYS = 7
FORUMDIRECTORY_BASE = "https://www.forumdirectory.com/directory/categories/"
FORUMDIRECTORY_CATEGORY_NICHES = {
    "technology.14": "it",
    "automotive.17": "auto",
    "do-it-yourself.2": "construction",
    "financial.19": "business",
    "hobbyist-forums.8": "goods",
    "lifestyle-forums.9": "goods",
    "education.3": "services",
    "creative-arts.1": "services",
    "support-help.13": "services",
    "pet-animal-care.18": "goods",
    "science-nature.11": "services",
    "general-discussion.6": "services",
}

FINDAFORUM_SEEDS = {
    "Home/Subcategory/Business-and-Economy/Small-Business/": "business",
    "Home/Subcategory/Business-and-Economy/SEO/": "marketing",
    "Home/Subcategory/Business-and-Economy/Jobs/": "services",
    "Home/Subcategory/Business-and-Economy/Logistics/": "logistics",
    "Home/Subcategory/Business-and-Economy/Marketing/": "marketing",
    "Home/Subcategory/Business-and-Economy/Affiliate-Marketing/": "marketing",
    "Home/Subcategory/Business-and-Economy/B2B/": "business",
    "Home/Subcategory/Business-and-Economy/Making-Money-Online/": "business",
    "Home/Subcategory/Business-and-Economy/Property-&-Real-Estate/": "realestate",
    "Home/Subcategory/Business-and-Economy/Advertising/": "marketing",
    "Home/Subcategory/Business-and-Economy/Work-From-Home/": "services",
    "Home/Subcategory/Computers-and-Internet/Programming-and-Software-Development/": "it",
    "Home/Subcategory/Computers-and-Internet/Web-Hosting/": "it",
    "Home/Subcategory/Computers-and-Internet/Graphics-and-Graphic-Design/": "services",
    "Home/Subcategory/Shopping-and-Ecommerce/Classifieds/": "goods",
    "Home/Subcategory/Shopping-and-Ecommerce/Clothing-and-Fashion/": "goods",
    "Home/Subcategory/Shopping-and-Ecommerce/Electricals-and-Electronic-Goods/": "goods",
    "Home/Subcategory/Shopping-and-Ecommerce/Jewellery-and-Watches/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Cars-and-Vehicles/": "auto",
    "Home/Subcategory/Recreation-and-Hobbies/Pets/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Drones/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Toys-and-Collectibles/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Photography/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Woodworking/": "goods",
    "Home/Subcategory/Recreation-and-Hobbies/Food/": "goods",
    "Home/Subcategory/Family-and-Home/Home/": "goods",
    "Home/Subcategory/Family-and-Home/Gardening/": "goods",
    "Home/Subcategory/Family-and-Home/Cooking/": "goods",
    "Home/Subcategory/Health/Beauty/": "goods",
    "Home/Subcategory/Travel-and-Tourism/Transportation/": "logistics",
}
FINDAFORUM_BASE = "https://www.findaforum.net/"
FINDAFORUM_CACHE_VERSION = 2
FINDAFORUM_CATEGORY_NICHES = {
    "Business-and-Economy": "business",
    "Computers-and-Internet": "it",
    "Shopping-and-Ecommerce": "goods",
    "Recreation-and-Hobbies": "goods",
    "Family-and-Home": "goods",
    "Health": "services",
    "Travel-and-Tourism": "services",
    "Science-and-Education": "services",
    "Sports": "goods",
    "Music": "goods",
    "Art-and-Literature": "goods",
    "Society-and-Culture": "services",
    "News-and-Media": "marketing",
}
FINDAFORUM_EXCLUDED_SUBCATEGORY_MARKERS = (
    "gambling",
    "guns and weapons",
    "sexuality",
    "medicines",
    "bitcoin",
    "cryptocurrenc",
    "forex",
    "stocks and bonds",
)
FINDAFORUM_AUXILIARY_DOMAINS = {
    "findaforum.net", "youtube.com", "facebook.com", "twitter.com", "x.com",
    "moz.com", "godaddy.com", "nichelaboratory.com", "findaniche.net",
    "etfsectordata.com", "google.com",
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121 Safari/537.36"

NICHES = {
    "business": ["предприниматели", "бизнес", "малый бизнес", "поставщики", "B2B", "business", "small business", "entrepreneurs", "vendors"],
    "construction": ["строительство", "строители", "стройматериалы", "подрядчики", "ремонт", "construction", "builders", "contractors", "building materials"],
    "it": ["IT", "программирование", "разработка", "1С", "CRM", "software development", "web development", "programming", "automation", "SaaS", "developers"],
    "marketing": ["маркетинг", "реклама", "SEO", "интернет маркетинг", "digital marketing", "advertising", "webmaster", "SEO services"],
    "goods": [
        "товары",
        "опт",
        "поставщики",
        "производители",
        "электроника",
        "одежда",
        "обувь",
        "косметика",
        "товары для дома",
        "детские товары",
        "зоотовары",
        "инструменты",
        "бытовая техника",
        "сантехника",
        "освещение",
        "сад и дача",
        "упаковка",
        "хозтовары",
        "канцелярские товары",
        "сельхозтовары",
        "стройматериалы",
        "мебель",
        "оборудование",
        "автотовары",
        "запчасти",
        "продукты оптом",
        "товары для бизнеса",
        "промышленная продукция",
        "marketplace",
        "buy sell trade",
        "classifieds",
        "wholesale suppliers",
        "vendors",
        "products for sale",
        "business marketplace",
    ],
    "services": ["услуги", "услуги для бизнеса", "исполнители", "подрядчики", "services", "business services", "freelance", "contractors", "service marketplace", "jobs freelance"],
    "furniture": ["мебель", "мебельщики", "кухни", "производство мебели"],
    "logistics": ["логистика", "грузоперевозки", "перевозчики", "спецтехника"],
    "auto": ["автосервис", "автобизнес", "ремонт автомобилей"],
    "realestate": ["риелторы", "недвижимость", "агентства недвижимости"],
    "marketplaces": ["маркетплейсы", "селлеры", "Ozon", "Wildberries"],
    "horeca": ["HoReCa", "рестораны", "поставщики продуктов", "оптовая торговля продуктами"],
    "medicine": ["медицинский бизнес", "стоматология", "клиники"],
    "accounting": ["бухгалтерия", "1С бухгалтерия", "бухгалтеры"],
    "manufacturing": ["производство", "промышленность", "металлообработка", "оборудование"],
}

INTENTS = [
    'форум {term} "без регистрации" "новая тема"',
    'форум {term} "гостям разрешено создавать темы"',
    'форум {term} "предлагаю услуги"',
    'форум {term} "новая тема" "услуги"',
    'форум {term} "биржа услуг"',
    'форум {term} "услуги"',
    'форум {term} "предложение услуг"',
    'форум {term} "создать тему"',
    'форум {term} "работа и партнёрство"',
    'форум {term} "исполнители"',
    'форум {term} "коммерческие предложения"',
    'форум {term} "объявления"',
    'форум {term} "поиск подрядчика"',
]

# FORUM_DISCOVERY_OFFER_AWARE_INTENTS_V1
# Раньше товары искались теми же запросами, что и услуги ("биржа услуг",
# "предлагаю услуги"), поэтому стратегический резерв для товарных клиентов
# почти не расширялся. Для продаваемого multi-client продукта используем
# отдельные поисковые намерения товаров и услуг.
GOODS_INTENTS = [
    'форум {term} "продам" "новая тема"',
    'форум {term} "товары" "новая тема"',
    'форум {term} "доска объявлений"',
    'форум {term} "куплю продам"',
    'форум {term} "торговая площадка"',
    'форум {term} "предложения поставщиков"',
    'форум {term} "барахолка"',
    'форум {term} "рынок" "куплю" "продам"',
    'форум {term} "объявления"',
    'форум {term} "продажа товаров"',
    'форум {term} "товары и услуги"',
    'форум {term} "поставщики"',
    'форум {term} "производители"',
    'форум {term} "оптовая торговля"',
    'форум {term} "коммерческие предложения"',
    'форум {term} "создать тему"',
    'форум {term} "каталог товаров"',
    'форум {term} "магазины"',
    'форум {term} "партнёры"',
    '"{term}" forum marketplace',
    '"{term}" forum "buy sell trade"',
    '"{term}" forum classifieds',
    '"{term}" forum "for sale"',
    '"{term}" community marketplace',
    '"{term}" forum vendors suppliers',
]
SERVICES_INTENTS = [
    'форум {term} "предлагаю услуги"',
    'форум {term} "услуги программиста"',
    'форум {term} "автоматизация бизнеса"',
    'форум {term} "разработка ботов"',
    'форум {term} "CRM интеграция"',
    'форум {term} "новая тема" "услуги"',
    'форум {term} "биржа услуг"',
    'форум {term} "услуги"',
    'форум {term} "предложение услуг"',
    'форум {term} "исполнители"',
    'форум {term} "подрядчики"',
    'форум {term} "поиск подрядчика"',
    'форум {term} "коммерческие предложения"',
    'форум {term} "объявления"',
    'форум {term} "работа и партнёрство"',
    'форум {term} "создать тему"',
    '"{term}" forum "services offered"',
    '"{term}" forum "services marketplace"',
    '"{term}" forum freelance jobs',
    '"{term}" forum "for hire"',
    '"{term}" community "services"',
    '"{term}" forum contractors',
]

# Active BORIS projects are usually broader than a single SEO service. Generic
# marketing queries kept rediscovering the same ad-prohibited forums, while
# explicit development/automation service sections were missed. Keep these
# high-intent phrases first so a bounded 48-query run spends its scarce budget
# on places where the client can actually offer work/services.
MARKETING_INTENTS = [
    'форум {term} "предлагаю услуги"',
    'форум {term} "автоматизация бизнеса"',
    'форум {term} "услуги программиста"',
    'форум {term} "digital услуги"',
    'форум {term} "без регистрации" "новая тема"',
    'форум {term} "гостям разрешено создавать темы"',
    'форум {term} "CRM интеграция"',
    'форум {term} "внедрение ИИ"',
    'форум {term} "разработка ботов"',
    'форум {term} "работа и услуги"',
    'форум {term} "биржа услуг"',
    'форум {term} "коммерческие предложения"',
    'форум {term} "исполнители"',
    'форум {term} "создать тему" "услуги"',
]

IT_INTENTS = [
    'форум {term} "услуги программиста"',
    'форум {term} "предлагаю услуги"',
    'форум {term} "разработка на заказ"',
    'форум {term} "автоматизация бизнеса"',
    'форум {term} "CRM интеграция"',
    'форум {term} "внедрение ИИ"',
    'форум {term} "разработка ботов"',
    'форум {term} "скрипты на заказ"',
    'форум {term} "работа и услуги"',
    'форум {term} "биржа услуг"',
    'форум {term} "исполнители"',
    'форум {term} "создать тему" "услуги"',
]

NICHE_INTENTS = {
    "goods": GOODS_INTENTS,
    "services": SERVICES_INTENTS,
    "marketing": MARKETING_INTENTS,
    "it": IT_INTENTS,
}

def _intents_for_niche(niche: str) -> list[str]:
    return NICHE_INTENTS.get(niche, INTENTS)

BLOCK_DOMAINS = {
    "vk.com","youtube.com","rutube.ru","wikipedia.org","dzen.ru","t.me","telegram.me",
    "facebook.com","twitter.com","x.com",
    "avito.ru","ozon.ru","wildberries.ru","yandex.ru","google.com","bing.com",
    "forum.exbo.net","exbo.net","forum.digital",
}
BLOCK_TITLE_WORDS = {
    "minecraft","gta","role play","rp форум","игровой форум","steam","warcraft","majestic","nextrp","matrp",
    "популярные бизнес-форумы","популярные бизнес- форумы","популярные бизнес","список форумов","рейтинг форумов","каталог форумов",
    "бизнес-клуб","бизнес клуб","бизнес сообщество","сообщество предпринимателей","подборка форумов",
}
EVENT_HINTS = {
    "вднх","купить билет","билеты","мероприятие","событие для предпринимателей",
    "деловая программа","спикеры форума","программа форума","регистрация на мероприятие",
}
FORUM_HINTS = [
    "/forum","/forums","viewforum","forum.","форум","темы","сообщения","создать тему",
    "новая тема","регистрация","зарегистрироваться","обсуждение",
]
COMMERCIAL_HINTS = [
    "услуги","предложение услуг","объявления","поставщики","компании","бизнес",
    "предприниматели","малый бизнес","исполнители","подрядчики","работа и услуги",
    "поиск и предложение","работа и партнёрство","партнёры","производители","оптовая торговля",
    "маркетинг","реклама","продажи","разработка","автоматизация","интернет-магазины",
    "селлеры","wildberries","ozon","логистика","перевозчики","строительство","стройматериалы",
    "мебельщики","риелторы","недвижимость","автосервис","клиники","стоматология","промышленность",
    "services","service marketplace","marketplace","classifieds","buy sell trade","for sale",
    "jobs","freelance","for hire","contractors","vendors","suppliers","business",
    "development","programming","software","automation","web development","digital marketing",
    "advertising","seo","webmaster",
]

@dataclass
class Candidate:
    key: str
    name: str
    url: str
    domain: str
    niche: str
    query: str
    score: int
    forum_detected: bool
    commercial_context: bool
    register_url: str | None
    create_topic_hint: bool
    evidence: list[str]
    status: str
    discovered_at: str
    last_checked_at: str

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default

def _save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def _normalize_domain(value: str | None) -> str:
    raw=str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw=urlparse(raw).netloc.lower()
    raw=raw.split(":",1)[0].removeprefix("www.")
    return raw.strip(".")

def _terminal_domain_rows(*, active_only: bool = True) -> list[dict]:
    now=datetime.now(timezone.utc)
    out=[]
    for row in _load(TERMINAL_DOMAINS_FILE, []):
        domain=_normalize_domain(row.get("domain"))
        if not domain:
            continue
        if str(row.get("reason") or "") in HUMAN_CHECKPOINT_REASONS:
            # Old state may still contain CAPTCHA/terms/code checkpoints from
            # earlier runs. They are actionable onboarding work, not terminal
            # platform failures, so never suppress the domain from discovery.
            continue
        checked_raw=str(row.get("checked_at") or row.get("blocked_at") or "")
        checked=None
        try:
            checked=datetime.fromisoformat(checked_raw.replace("Z","+00:00")) if checked_raw else None
        except Exception:
            checked=None
        active=bool(checked and checked >= now-timedelta(days=TERMINAL_RECHECK_DAYS))
        if active_only and not active:
            continue
        item=dict(row)
        item["domain"]=domain
        item["active"]=active
        out.append(item)
    return out

def terminal_domain_map(*, active_only: bool = True) -> dict[str,dict]:
    return {row["domain"]:row for row in _terminal_domain_rows(active_only=active_only)}

def unmark_terminal_domain(domain: str, *, reason: str | None = None) -> bool:
    """Return a previously terminal domain to active discovery.

    Used when a platform is not actually dead/forbidden but merely needs
    account maturation or another delayed readiness condition.
    """
    domain = _normalize_domain(domain)
    if not domain:
        return False
    rows = _load(TERMINAL_DOMAINS_FILE, [])
    kept = [x for x in rows if _normalize_domain(x.get("domain")) != domain]
    changed = len(kept) != len(rows)
    if changed:
        _save(TERMINAL_DOMAINS_FILE, kept)

    discovered = _load(DISCOVERY_FILE, [])
    discovery_changed = False
    for item in discovered:
        if _normalize_domain(item.get("domain")) != domain:
            continue
        if item.get("status") == "terminal_policy_blocked":
            item["status"] = "candidate"
        item.pop("terminal_reason", None)
        item.pop("terminal_checked_at", None)
        if reason:
            item["reactivated_reason"] = reason
        discovery_changed = True
    if discovery_changed:
        _save(DISCOVERY_FILE, discovered)
    return changed


def mark_terminal_domain(domain: str, reason: str, *, source: str | None = None, evidence: str | None = None) -> dict:
    domain=_normalize_domain(domain)
    if not domain:
        raise ValueError("domain required")
    reason=str(reason or "terminal_policy_block")
    if reason in HUMAN_CHECKPOINT_REASONS:
        # A human verification step is temporary onboarding state. Re-open any
        # stale terminal record instead of hiding the platform from the reserve.
        unmark_terminal_domain(domain, reason=f"human_checkpoint:{reason}")
        return {
            "domain": domain,
            "reason": reason,
            "source": source,
            "evidence": evidence,
            "terminal": False,
            "human_checkpoint": True,
            "recheck_after_days": 0,
        }
    now=datetime.now(timezone.utc).isoformat()
    rows=_load(TERMINAL_DOMAINS_FILE, [])
    row=next((x for x in rows if _normalize_domain(x.get("domain"))==domain),None)
    if row is None:
        row={"domain":domain,"blocked_at":now}
        rows.append(row)
    row.update({
        "domain":domain,
        "reason":str(reason or "terminal_policy_block"),
        "source":source,
        "evidence":evidence,
        "checked_at":now,
        "recheck_after_days":TERMINAL_RECHECK_DAYS,
    })
    _save(TERMINAL_DOMAINS_FILE,rows)

    discovered=_load(DISCOVERY_FILE,[])
    changed=False
    for item in discovered:
        if _normalize_domain(item.get("domain"))==domain:
            item["status"]="terminal_policy_blocked"
            item["terminal_reason"]=row["reason"]
            item["terminal_checked_at"]=now
            changed=True
    if changed:
        _save(DISCOVERY_FILE,discovered)
    return dict(row)

def _bing_decode(href: str) -> str | None:
    """Decode Bing tracking href parameter u=a1<base64-url>."""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        token = (qs.get("u") or [None])[0]
        if not token:
            return href if href.startswith("http") else None
        token = unquote(token)
        if token.startswith("a1"):
            raw = token[2:]
            raw += "=" * (-len(raw) % 4)
            decoded = base64.urlsafe_b64decode(raw.encode()).decode("utf-8", "ignore")
            return decoded if decoded.startswith("http") else None
    except Exception:
        return None
    return None

def _search_bing(query: str, limit: int = 10) -> list[dict]:
    url = "https://www.bing.com/search?count=20&q=" + quote(query)
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    seen = set()
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a[href]") or li.select_one("a[href]")
        if not a:
            continue
        href = _bing_decode(a.get("href", "")) or a.get("href", "")
        if not href.startswith("http"):
            continue
        href = href.split("#", 1)[0]
        domain = urlparse(href).netloc.lower().removeprefix("www.")
        if domain in seen or domain in BLOCK_DOMAINS:
            continue
        seen.add(domain)
        title = " ".join(a.stripped_strings).strip()
        snippet = " ".join((li.select_one(".b_caption") or li).stripped_strings)
        results.append({"title": title, "url": href, "domain": domain, "snippet": snippet[:1000], "engine": "bing"})
        if len(results) >= limit:
            break
    return results

def _ddg_decode(href: str) -> str:
    try:
        parsed = urlparse(href)
        q = parse_qs(parsed.query)
        target = (q.get("uddg") or [None])[0]
        return unquote(target) if target else href
    except Exception:
        return href

def _search_ddg_lite(query: str, limit: int = 10) -> list[dict]:
    """Second independent discovery source. Lite endpoint works without JS/API keys."""
    url = "https://lite.duckduckgo.com/lite/?q=" + quote(query)
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        title = " ".join(a.stripped_strings).strip()
        href = _ddg_decode(a.get("href", ""))
        if not title or not href.startswith("http"):
            continue
        href = href.split("#", 1)[0]
        domain = urlparse(href).netloc.lower().removeprefix("www.")
        if not domain or domain in seen or domain in BLOCK_DOMAINS:
            continue
        # Ignore the engine's own navigation / generic non-result links.
        if domain.endswith("duckduckgo.com"):
            continue
        seen.add(domain)
        row = a.find_parent("tr")
        snippet = " ".join(row.stripped_strings) if row else title
        results.append({"title": title, "url": href, "domain": domain, "snippet": snippet[:1000], "engine": "duckduckgo_lite"})
        if len(results) >= limit:
            break
    return results

def _search_all(query: str, limit: int = 10, *, use_ddg: bool = True) -> tuple[list[dict], list[str]]:
    merged: dict[str, dict] = {}
    errors: list[str] = []
    engines = [("bing", _search_bing)]
    if use_ddg:
        engines.insert(0, ("duckduckgo_lite", _search_ddg_lite))
    with ThreadPoolExecutor(max_workers=len(engines)) as pool:
        futures = {pool.submit(fn, query, limit): name for name, fn in engines}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                hits = fut.result()
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            for hit in hits:
                d = hit.get("domain")
                if d and d not in merged:
                    merged[d] = hit
                elif d:
                    old = merged[d]
                    if len(hit.get("snippet", "")) > len(old.get("snippet", "")):
                        old["snippet"] = hit.get("snippet", "")
                    old["engine"] = "+".join(sorted(set(str(old.get("engine","")).split("+") + [name])))
    ranked = sorted(merged.values(), key=lambda h: _score_search_hit(h, query), reverse=True)
    return ranked[: max(limit, 1)], errors

def _score_search_hit(hit: dict, query: str) -> int:
    text = f"{hit.get('title','')} {hit.get('url','')} {hit.get('snippet','')}".lower()
    if any(w in text for w in BLOCK_TITLE_WORDS):
        return -100
    score = 0
    score += 5 if "форум" in text else 0
    score += 4 if any(h in text for h in ["/forum","/forums","viewforum","forum."]) else 0
    score += 3 if any(h in text for h in COMMERCIAL_HINTS) else 0
    score += 5 if any(x in text for x in [
        "без регистрации",
        "гостям разрешено создавать темы",
        "гости могут создавать темы",
        "guest posting allowed",
    ]) else 0
    score += 2 if any(x in text for x in ["темы","сообщения","обсуждение"]) else 0
    parts = query.split()
    if len(parts) > 1:
        score += 1 if parts[1].lower() in text else 0
    return score

def _candidate_from_search_hit(hit: dict, niche: str, query: str) -> Candidate | None:
    """Fast discovery stage. Site opening/rules inspection happens later."""
    url = str(hit.get("url") or "")
    domain = str(hit.get("domain") or urlparse(url).netloc.lower().removeprefix("www."))
    if not url.startswith("http") or not domain or domain in BLOCK_DOMAINS:
        return None
    text = f"{hit.get('title','')} {url} {hit.get('snippet','')}".lower()
    if any(w in text for w in BLOCK_TITLE_WORDS):
        return None
    event_like = any(w in text for w in EVENT_HINTS)
    if event_like:
        return None
    forum_hits = [h for h in FORUM_HINTS if h in text]
    commercial_hits = [h for h in COMMERCIAL_HINTS if h in text]
    forum_detected = "форум" in text or any(h in text for h in ["/forum","/forums","viewforum","forum."])
    commercial = bool(commercial_hits)
    score = _score_search_hit(hit, query) + (4 if forum_detected else -5) + (3 if commercial else 0)
    if score < 9 or not forum_detected or not commercial:
        return None
    now = datetime.now(timezone.utc).isoformat()
    return Candidate(
        key="disc_" + re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")[:45],
        name=str(hit.get("title") or domain)[:180],
        url=url,
        domain=domain,
        niche=niche,
        query=query,
        score=score,
        forum_detected=True,
        commercial_context=True,
        register_url=None,
        create_topic_hint=any(x in text for x in [
            "создать тему","новая тема","new topic","post new topic",
            "предлагаю услуги","предложение услуг","без регистрации",
            "гостям разрешено создавать темы","гости могут создавать темы",
        ]),
        evidence=sorted(set(
            (forum_hits + commercial_hits + (
                ["guest_post_hint"] if any(x in text for x in [
                    "без регистрации","гостям разрешено создавать темы",
                    "гости могут создавать темы","guest posting allowed",
                ]) else []
            ))[:12]
        )),
        status="candidate",
        discovered_at=now,
        last_checked_at=now,
    )

def _inspect_candidate(hit: dict, niche: str, query: str) -> Candidate | None:
    url = hit["url"]
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=10, allow_redirects=True)
        if r.status_code in {404, 410}:
            hit["_terminal_reason"] = f"http_{r.status_code}"
            hit["_terminal_evidence"] = r.url
            return None
        if r.status_code >= 500:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script","style","noscript","svg"]):
            tag.decompose()
        text = " ".join(soup.stripped_strings)
        low = text.lower()
        final_url = r.url
        parked_markers = (
            "this domain is successfully pointed at wp engine, but is not configured",
            "the site you were looking for couldn't be found",
            "this domain is parked",
            "buy this domain",
            "domain is for sale",
            "this domain may be for sale",
        )
        if any(marker in low for marker in parked_markers):
            hit["_terminal_reason"] = "parked_or_unconfigured_domain"
            hit["_terminal_evidence"] = text[:500]
            return None
    except Exception:
        text = f"{hit.get('title','')} {hit.get('snippet','')}"
        low = text.lower()
        final_url = url
        soup = None

    evidence = []
    search_context = f"{hit.get('title','')} {hit.get('snippet','')}".lower()
    forum_hits = [h for h in FORUM_HINTS if h in low or h in final_url.lower() or h in search_context]
    commercial_hits = [h for h in COMMERCIAL_HINTS if h in low or h in search_context]
    forum_detected = len(forum_hits) >= 2 or "форум" in low or "форум" in search_context
    # Candidate relevance is broader than advertising permission.
    # The separate rules engine decides whether links/ads are allowed.
    commercial = bool(commercial_hits)
    create_topic = any(x in low for x in ["создать тему","новая тема","new topic","post new topic"])

    register_url = None
    if soup:
        for a in soup.find_all("a", href=True):
            label = " ".join(a.stripped_strings).lower()
            href = a.get("href", "")
            if any(x in label for x in ["регистрация","зарегистрироваться","sign up","register"]):
                from urllib.parse import urljoin
                register_url = urljoin(final_url, href)
                break

    search_score = _score_search_hit(hit, query)
    score = search_score + (5 if forum_detected else -5) + (4 if commercial else 0) + (2 if register_url else 0) + (2 if create_topic else 0)
    evidence.extend(forum_hits[:6])
    evidence.extend(commercial_hits[:6])
    if register_url:
        evidence.append("registration_link")
    if create_topic:
        evidence.append("create_topic")

    if score < 8 or not forum_detected or not commercial:
        return None

    domain = urlparse(final_url).netloc.lower().removeprefix("www.")
    key = "disc_" + re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")[:45]
    now = datetime.now(timezone.utc).isoformat()
    return Candidate(
        key=key,
        name=hit.get("title") or domain,
        url=final_url,
        domain=domain,
        niche=niche,
        query=query,
        score=score,
        forum_detected=forum_detected,
        commercial_context=commercial,
        register_url=register_url,
        create_topic_hint=create_topic,
        evidence=sorted(set(evidence)),
        status="candidate",
        discovered_at=now,
        last_checked_at=now,
    )

def query_plan(max_queries: int = 30, priority_niches: list[str] | None = None) -> list[tuple[str,str]]:
    """Build a bounded forum search plan.

    Default behavior stays balanced across all niches. When active client
    projects have an autonomous-capacity deficit, their niches can be passed as
    priorities: roughly two thirds of the query budget is spent on them first,
    then the rest is filled by the normal balanced plan. This keeps discovery
    client-driven without starving the general forum pool.
    """
    limit = max(0, int(max_queries))
    if not limit:
        return []

    plan: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    priorities = [x for x in (priority_niches or []) if x in NICHES]
    priorities = list(dict.fromkeys(priorities))

    if priorities:
        # When exactly one strategic format is still short (for example goods
        # while services already covers its current active-order demand), spend the whole
        # bounded search budget on that deficit instead of wasting the final
        # third on already-covered niches.
        if len(priorities) == 1 and priorities[0] in {"goods", "services"}:
            priority_budget = limit
        else:
            priority_budget = min(limit, max(len(priorities) * 6, (limit * 2) // 3))
        idx = 0
        max_priority_attempts = max(priority_budget * 8, len(priorities) * len(INTENTS) * 8)
        while len(plan) < priority_budget and idx < max_priority_attempts:
            niche = priorities[idx % len(priorities)]
            terms = NICHES[niche]
            visit = idx // len(priorities)
            intents = _intents_for_niche(niche)
            # Rotate term and commercial intent together. This exposes
            # different marketplace/job/classified patterns early in a bounded
            # run instead of spending dozens of queries on one intent after
            # the term vocabulary grows. The seen-set still prevents duplicate
            # queries and the outer loop keeps walking until the budget is full.
            term = terms[visit % len(terms)]
            # Avoid early repetition when term and intent counts share the
            # same cycle length. Advance the intent once per completed
            # term cycle so a bounded run keeps exposing new combinations.
            intent_visit = visit + (visit // max(1, len(terms)))
            template = intents[intent_visit % len(intents)]
            pair = (niche, template.format(term=term))
            if pair not in seen:
                seen.add(pair)
                plan.append(pair)
            idx += 1

    niches = list(NICHES.items())
    idx = 0
    max_attempts = max(limit * 12, len(niches) * len(INTENTS) * 4)
    while len(plan) < limit and idx < max_attempts:
        niche_idx = idx % len(niches)
        niche, terms = niches[niche_idx]
        visit = idx // len(niches)
        term = terms[visit % len(terms)]
        intents = _intents_for_niche(niche)
        # Spread commercial intents across niches even when the balanced budget
        # gives only 1-2 searches per niche. Otherwise every niche repeats the
        # same first templates and the multi-client pool grows too narrowly.
        template = intents[(visit + niche_idx) % len(intents)]
        pair = (niche, template.format(term=term))
        if pair not in seen:
            seen.add(pair)
            plan.append(pair)
        idx += 1

    return plan

# FORUM_DISCOVERY_REAL_PROGRESS_V1
# "Найден снова" != "появился новый рабочий кандидат". Иначе дефицит
# площадок заставляет guardian бесконечно повторять одни и те же запросы.
def _is_material_discovery_progress(old: dict | None, new: dict) -> bool:
    if not old:
        return True
    if not old.get("register_url") and new.get("register_url"):
        return True
    if not bool(old.get("create_topic_hint")) and bool(new.get("create_topic_hint")):
        return True
    if not bool(old.get("forum_detected")) and bool(new.get("forum_detected")):
        return True
    if not bool(old.get("commercial_context")) and bool(new.get("commercial_context")):
        return True
    return False



def _strip_directory_tracking(url: str) -> str:
    """Remove ForumDirectory UTM parameters while preserving a forum's own query."""
    try:
        parsed = urlparse(str(url or ""))
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            clean_key = str(key)
            # Some directory records preserve HTML '&amp;' as a literal query
            # prefix (including URL-encoded amp%3B). Normalize it back to the
            # forum's real parameter, e.g. amp;f=18 -> f=18.
            while clean_key.lower().startswith("amp;"):
                clean_key = clean_key[4:]
            if not clean_key or clean_key.lower().startswith("utm_"):
                continue
            query.append((clean_key, value))
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, parsed.params,
            urlencode(query, doseq=True), "",
        ))
    except Exception:
        return str(url or "")


def _directory_last_page(soup: BeautifulSoup) -> int:
    pages = [1]
    for a in soup.find_all("a", href=True):
        match = re.search(r"[?&]page=(\d+)", str(a.get("href") or ""))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def _forumdirectory_entries(max_entries: int = 1200) -> tuple[list[dict], list[dict]]:
    """Collect external forum listings from public ForumDirectory category pages."""
    limit = max(0, int(max_entries))
    if not limit:
        return [], []
    entries: dict[str, dict] = {}
    errors: list[dict] = []
    for slug, niche in FORUMDIRECTORY_CATEGORY_NICHES.items():
        base = FORUMDIRECTORY_BASE + slug + "/"
        try:
            first = requests.get(
                base,
                headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                timeout=20,
                allow_redirects=True,
            )
            first.raise_for_status()
            first_soup = BeautifulSoup(first.text, "html.parser")
            pages = min(_directory_last_page(first_soup), 15)
        except Exception as exc:
            errors.append({"category": slug, "error": f"{type(exc).__name__}: {exc}"})
            continue

        for page_no in range(1, pages + 1):
            try:
                if page_no == 1:
                    response, soup = first, first_soup
                else:
                    response = requests.get(
                        base + f"?page={page_no}",
                        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                        timeout=20,
                        allow_redirects=True,
                    )
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    if a.get("data-xf-click") != "ldClickCounter":
                        continue
                    target = _strip_directory_tracking(urljoin(response.url, a.get("href", "")))
                    domain = _normalize_domain(target)
                    if (
                        not domain
                        or domain == "forumdirectory.com"
                        or domain in BLOCK_DOMAINS
                        or domain.endswith("forumdirectory.com")
                    ):
                        continue
                    card = a.find_parent("li", class_=re.compile(r"grid-container"))
                    title_el = card.select_one(".grid-item-title") if card else None
                    title = (
                        " ".join(title_el.stripped_strings)
                        if title_el
                        else " ".join(a.stripped_strings)
                    )
                    snippet = " ".join(card.stripped_strings) if card else title
                    current = entries.get(domain)
                    candidate = {
                        "title": title[:180] or domain,
                        "url": target,
                        "domain": domain,
                        "snippet": snippet[:1600],
                        "engine": "forumdirectory",
                        "directory_category": slug,
                        "directory_niche": niche,
                    }
                    if current is None or len(candidate["snippet"]) > len(current.get("snippet", "")):
                        entries[domain] = candidate
                    if len(entries) >= limit:
                        return list(entries.values()), errors
            except Exception as exc:
                errors.append({
                    "category": slug,
                    "page": page_no,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return list(entries.values()), errors


def discover_forum_directory(
    *,
    max_entries: int = 1200,
    max_inspections: int = 350,
    workers: int = 18,
) -> dict:
    """Harvest a broad forum directory, inspect entries, and merge real candidates.

    Directory membership is never treated as permission to advertise. It only
    creates discovery candidates; the normal platform rule-audit remains
    mandatory before a site can enter the reusable delivery reserve.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    terminal_domains = terminal_domain_map()
    existing_rows = _load(DISCOVERY_FILE, [])
    existing = {
        _normalize_domain(x.get("domain")): x
        for x in existing_rows
        if _normalize_domain(x.get("domain"))
    }
    scan_rows = _load(DIRECTORY_SCAN_FILE, [])
    scan = {
        _normalize_domain(x.get("domain")): x
        for x in scan_rows
        if _normalize_domain(x.get("domain"))
    }

    entries, directory_errors = _forumdirectory_entries(max_entries=max_entries)
    due: list[dict] = []
    cached_skips = 0
    for entry in entries:
        domain = _normalize_domain(entry.get("domain"))
        if not domain or domain in terminal_domains or domain in BLOCK_DOMAINS:
            continue
        if domain in existing:
            cached_skips += 1
            continue
        previous = scan.get(domain) or {}
        checked = None
        try:
            checked = datetime.fromisoformat(
                str(previous.get("checked_at") or "").replace("Z", "+00:00")
            )
        except Exception:
            checked = None
        if checked and checked >= now - timedelta(days=DIRECTORY_RECHECK_DAYS):
            cached_skips += 1
            continue
        due.append(entry)

    due = due[: max(0, int(max_inspections))]
    accepted: dict[str, dict] = {}
    rejected = 0
    inspect_errors: list[dict] = []

    def inspect_entry(entry: dict):
        niche = str(entry.get("directory_niche") or "services")
        query = (
            f"forumdirectory {entry.get('directory_category') or ''} "
            "marketplace services classifieds jobs for sale"
        )
        try:
            return entry, _inspect_candidate(entry, niche, query), None
        except Exception as exc:
            return entry, None, f"{type(exc).__name__}: {exc}"

    if due:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 24))) as pool:
            futures = [pool.submit(inspect_entry, entry) for entry in due]
            for fut in as_completed(futures):
                entry, candidate, error = fut.result()
                domain = _normalize_domain(entry.get("domain"))
                if error:
                    inspect_errors.append({"domain": domain, "error": error})
                if candidate is not None:
                    row = asdict(candidate)
                    row["search_engine"] = "forumdirectory"
                    row["directory_category"] = entry.get("directory_category")
                    accepted[domain] = row
                    state = "candidate"
                else:
                    rejected += 1
                    state = "not_commercial_or_not_forum"
                    if domain and entry.get("_terminal_reason"):
                        mark_terminal_domain(
                            domain,
                            str(entry.get("_terminal_reason")),
                            source="forumdirectory",
                            evidence=str(entry.get("_terminal_evidence") or entry.get("url") or "")[:500],
                        )
                        state = "terminal_dead_or_parked"
                scan[domain] = {
                    "domain": domain,
                    "url": entry.get("url"),
                    "category": entry.get("directory_category"),
                    "niche": entry.get("directory_niche"),
                    "status": state,
                    "checked_at": now_iso,
                    "recheck_after_days": DIRECTORY_RECHECK_DAYS,
                }

    merged = dict(existing)
    new_domains: list[str] = []
    improved_domains: list[str] = []
    for domain, row in accepted.items():
        old = merged.get(domain)
        if old is None:
            row["discovered_at"] = row.get("discovered_at") or now_iso
            new_domains.append(domain)
            merged[domain] = row
            continue
        if _is_material_discovery_progress(old, row):
            improved_domains.append(domain)
        row["discovered_at"] = old.get("discovered_at") or row.get("discovered_at") or now_iso
        row["score"] = max(int(old.get("score") or 0), int(row.get("score") or 0))
        if old.get("status") not in {None, "candidate"}:
            row["status"] = old.get("status")
        merged[domain] = row

    rows = sorted(
        merged.values(),
        key=lambda x: (-int(x.get("score") or 0), x.get("domain") or ""),
    )
    _save(DISCOVERY_FILE, rows)
    _save(
        DIRECTORY_SCAN_FILE,
        sorted(scan.values(), key=lambda x: x.get("domain") or ""),
    )
    return {
        "at": now_iso,
        "source": "forumdirectory",
        "directory_entries": len(entries),
        "due_inspections": len(due),
        "accepted": len(accepted),
        "rejected": rejected,
        "new_candidates": len(new_domains),
        "improved_candidates": len(improved_domains),
        "progress_count": len(new_domains) + len(improved_domains),
        "cached_skips": cached_skips,
        "total_candidates": len(rows),
        "directory_errors": directory_errors,
        "inspection_errors": inspect_errors,
    }



def _findaforum_get_soup(url: str, attempts: int = 2) -> tuple[requests.Response, BeautifulSoup]:
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                timeout=(10, 45),
                allow_redirects=True,
            )
            response.raise_for_status()
            return response, BeautifulSoup(response.text, "html.parser")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(0.8 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _findaforum_niche_for_subcategory(label: str, default_niche: str) -> str:
    low = str(label or "").strip().lower()
    mapping = (
        (("advertising", "affiliate", "marketing", "seo", "social media"), "marketing"),
        (("programming", "software", "web hosting", "tech support", "computer science", "hardware"), "it"),
        (("logistics", "transportation"), "logistics"),
        (("property", "real estate"), "realestate"),
        (("jobs", "employment", "human resources", "work from home", "education", "engineering", "graphic design", "tutorials"), "services"),
        (("classified", "clothing", "electrical", "jewellery", "vehicles", "pets", "camping", "collecting", "drones", "food", "photography", "toys", "woodworking", "musical instruments", "gardening", "cooking", "home"), "goods"),
        (("small business", "b2b", "management", "finance", "personal finance", "insurance"), "business"),
    )
    for markers, niche in mapping:
        if any(marker in low for marker in markers):
            return niche
    return str(default_niche or "services")


def _findaforum_seed_pages() -> tuple[list[tuple[str, str]], list[dict]]:
    """Expand curated seeds from FindAForum's own category/subcategory index."""
    seeds = dict(FINDAFORUM_SEEDS)
    errors: list[dict] = []

    def fetch_category(item: tuple[str, str]):
        slug, default_niche = item
        path = f"Home/Category/{slug}/"
        url = urljoin(FINDAFORUM_BASE, path)
        try:
            response, soup = _findaforum_get_soup(url)
            found: dict[str, str] = {path: default_niche}
            prefix = f"/Home/Subcategory/{slug}/"
            for a in soup.find_all("a", href=True):
                href = urljoin(response.url, a.get("href", ""))
                parsed = urlparse(href)
                if _normalize_domain(href) != "findaforum.net":
                    continue
                if not parsed.path.startswith(prefix):
                    continue
                label = " ".join(a.stripped_strings).strip()
                low = label.lower()
                if any(marker in low for marker in FINDAFORUM_EXCLUDED_SUBCATEGORY_MARKERS):
                    continue
                clean_path = parsed.path.lstrip("/")
                if not clean_path.endswith("/"):
                    clean_path += "/"
                found[clean_path] = _findaforum_niche_for_subcategory(label, default_niche)
            return found, None
        except Exception as exc:
            return {}, {"category": slug, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(FINDAFORUM_CATEGORY_NICHES)))) as pool:
        futures = [pool.submit(fetch_category, item) for item in FINDAFORUM_CATEGORY_NICHES.items()]
        for fut in as_completed(futures):
            found, error = fut.result()
            if error:
                errors.append(error)
                continue
            seeds.update(found)
    return list(seeds.items()), errors


def _findaforum_detail_index(max_details: int = 700) -> tuple[list[dict], list[dict]]:
    """Fetch selected FindAForum category pages and collect unique detail cards."""
    limit = max(0, int(max_details))
    if not limit:
        return [], []
    details: dict[str, dict] = {}
    seed_items, seed_errors = _findaforum_seed_pages()
    errors: list[dict] = list(seed_errors)

    def fetch_seed(item: tuple[str, str]):
        path, niche = item
        url = urljoin(FINDAFORUM_BASE, path)
        try:
            response, soup = _findaforum_get_soup(url)
            found: list[dict] = []
            for a in soup.find_all("a", href=True):
                href = urljoin(response.url, a.get("href", ""))
                if "/Forums/" not in href or "/Forums/Random/" in href:
                    continue
                parsed = urlparse(href)
                if _normalize_domain(href) != "findaforum.net":
                    continue
                clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                found.append({
                    "detail_url": clean,
                    "directory_niche": niche,
                    "directory_category": path,
                })
            return found, None
        except Exception as exc:
            return [], {"category": path, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=min(12, max(1, len(seed_items)))) as pool:
        futures = [pool.submit(fetch_seed, item) for item in seed_items]
        for fut in as_completed(futures):
            found, error = fut.result()
            if error:
                errors.append(error)
                continue
            for row in found:
                detail_url = str(row.get("detail_url") or "")
                if not detail_url:
                    continue
                current = details.get(detail_url)
                if current is None:
                    details[detail_url] = row
                else:
                    # Prefer broad sellable formats when the same forum appears
                    # in multiple directory categories.
                    rank = {"goods": 0, "services": 1, "business": 2, "it": 3, "marketing": 4}
                    if rank.get(str(row.get("directory_niche")), 10) < rank.get(str(current.get("directory_niche")), 10):
                        details[detail_url] = row
                if len(details) >= limit:
                    break
    return list(details.values())[:limit], errors


def _findaforum_parse_detail(meta: dict) -> dict | None:
    detail_url = str(meta.get("detail_url") or "")
    if not detail_url:
        return None
    response, soup = _findaforum_get_soup(detail_url)
    title = soup.title.get_text(" ", strip=True) if soup.title else detail_url
    title = re.sub(r"\s*[-–|]\s*FindAForum.*$", "", title, flags=re.I).strip()

    by_domain: dict[str, list[tuple[str, str]]] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(response.url, a.get("href", ""))
        domain = _normalize_domain(href)
        if (
            not domain
            or domain in FINDAFORUM_AUXILIARY_DOMAINS
            or domain in BLOCK_DOMAINS
            or domain.endswith("findaforum.net")
        ):
            continue
        label = " ".join(a.stripped_strings).strip()
        by_domain.setdefault(domain, []).append((label, href))
    if not by_domain:
        return None

    commercial_markers = (
        "marketplace", "classified", "services", "service", "offers", "offer",
        "jobs", "job", "employment", "for sale", "buy & sell", "buy and sell",
        "buy sell", "wanted", "advertising", "free advertising", "contractor",
        "vendors", "suppliers", "products", "sales", "business opportunities",
        "webmaster", "freelance", "hire",
    )
    forum_markers = (
        "/forum", "/forums", "forumdisplay", "viewforum", "community",
        "board=", "/board", "index.php",
    )

    def domain_score(item: tuple[str, list[tuple[str, str]]]) -> tuple[int, int]:
        domain, links = item
        payload = " ".join(f"{label} {href}" for label, href in links).lower()
        commercial = sum(1 for marker in commercial_markers if marker in payload)
        forumish = sum(1 for marker in forum_markers if marker in payload)
        return (commercial * 8 + forumish * 3 + len(links), len(links))

    target_domain, links = max(by_domain.items(), key=domain_score)

    def link_score(row: tuple[str, str]) -> int:
        label, href = row
        payload = f"{label} {href}".lower()
        # Prefer sections where a client can actually promote/sell over generic
        # job boards. FindAForum detail pages often expose both.
        weighted_markers = {
            "free advertising": 50,
            "marketplace": 42,
            "classified": 40,
            "services": 34,
            "offers": 32,
            "for sale": 30,
            "buy & sell": 30,
            "buy and sell": 30,
            "buy sell": 30,
            "advertising": 26,
            "suppliers": 24,
            "vendors": 24,
            "products": 20,
            "webmaster": 18,
            "freelance": 16,
            "hire": 12,
            "jobs": 8,
            "employment": 7,
            "wanted": 7,
        }
        score = sum(weight for marker, weight in weighted_markers.items() if marker in payload)
        score += sum(3 for marker in forum_markers if marker in payload)
        if _normalize_domain(href) == target_domain:
            score += 2
        return score

    best_label, best_url = max(links, key=link_score)
    labels = [" ".join((label, href)).strip() for label, href in links]
    snippet = " ".join([title, *labels])[:4000]
    commercial_signal = any(marker in snippet.lower() for marker in commercial_markers)
    return {
        "title": title[:180] or target_domain,
        "url": _strip_directory_tracking(best_url),
        "domain": target_domain,
        "snippet": snippet,
        "engine": "findaforum",
        "directory_category": meta.get("directory_category"),
        "directory_niche": meta.get("directory_niche") or "services",
        "commercial_surface_hint": commercial_signal,
        "surface_label": best_label[:240],
        "detail_url": detail_url,
    }


def _findaforum_entries(
    max_entries: int = 700,
    *,
    workers: int = 12,
    force_refresh: bool = False,
) -> tuple[list[dict], list[dict], bool]:
    """Build/cache high-signal external forum entries from FindAForum detail pages."""
    limit = max(0, int(max_entries))
    cached = _load(FINDAFORUM_ENTRIES_FILE, {})
    if not force_refresh and isinstance(cached, dict):
        try:
            at = datetime.fromisoformat(str(cached.get("at") or "").replace("Z", "+00:00"))
        except Exception:
            at = None
        items = cached.get("items") if isinstance(cached.get("items"), list) else []
        cache_version_ok = int(cached.get("version") or 0) == FINDAFORUM_CACHE_VERSION
        if (
            cache_version_ok
            and at
            and at >= datetime.now(timezone.utc) - timedelta(days=FINDAFORUM_CACHE_DAYS)
            and items
        ):
            normalized_items = []
            for item in items:
                row = dict(item)
                row["url"] = _strip_directory_tracking(str(row.get("url") or ""))
                normalized_items.append(row)
            return normalized_items[:limit], list(cached.get("errors") or []), True

    details, index_errors = _findaforum_detail_index(max_details=limit)
    entries: dict[str, dict] = {}
    detail_errors: list[dict] = []
    if details:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 16))) as pool:
            futures = {pool.submit(_findaforum_parse_detail, meta): meta for meta in details}
            for fut in as_completed(futures):
                meta = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    detail_errors.append({
                        "detail_url": meta.get("detail_url"),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                if not row:
                    continue
                domain = _normalize_domain(row.get("domain"))
                if not domain:
                    continue
                current = entries.get(domain)
                if current is None:
                    entries[domain] = row
                    continue
                # Keep the card with the stronger explicit commercial surface.
                if bool(row.get("commercial_surface_hint")) and not bool(current.get("commercial_surface_hint")):
                    entries[domain] = row
                elif len(str(row.get("snippet") or "")) > len(str(current.get("snippet") or "")):
                    entries[domain] = row

    errors = [*index_errors, *detail_errors]
    payload = {
        "version": FINDAFORUM_CACHE_VERSION,
        "at": datetime.now(timezone.utc).isoformat(),
        "items": sorted(entries.values(), key=lambda x: x.get("domain") or ""),
        "errors": errors,
    }
    _save(FINDAFORUM_ENTRIES_FILE, payload)
    return payload["items"][:limit], errors, False


def discover_findaforum(
    *,
    max_entries: int = 700,
    max_inspections: int = 250,
    workers: int = 18,
) -> dict:
    """Merge FindAForum candidates; normal BORIS rule-audit remains mandatory."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    terminal_domains = terminal_domain_map()
    existing_rows = _load(DISCOVERY_FILE, [])
    existing = {
        _normalize_domain(x.get("domain")): x
        for x in existing_rows
        if _normalize_domain(x.get("domain"))
    }
    scan_rows = _load(FINDAFORUM_SCAN_FILE, [])
    scan = {
        _normalize_domain(x.get("domain")): x
        for x in scan_rows
        if _normalize_domain(x.get("domain"))
    }
    entries, source_errors, source_cached = _findaforum_entries(max_entries=max_entries)

    due: list[dict] = []
    cached_skips = 0
    for entry in entries:
        domain = _normalize_domain(entry.get("domain"))
        if not domain or domain in terminal_domains or domain in BLOCK_DOMAINS:
            continue
        old = existing.get(domain) or {}
        previous = scan.get(domain) or {}
        checked = None
        try:
            checked = datetime.fromisoformat(str(previous.get("checked_at") or "").replace("Z", "+00:00"))
        except Exception:
            checked = None
        old_url = str(old.get("url") or "")
        new_url = str(entry.get("url") or "")
        url_repaired = bool(old_url and new_url and old_url != new_url)
        already_strong = bool(old.get("register_url")) and bool(old.get("create_topic_hint"))
        if already_strong and not url_repaired:
            cached_skips += 1
            continue
        if (
            checked
            and checked >= now - timedelta(days=DIRECTORY_RECHECK_DAYS)
            and not url_repaired
        ):
            cached_skips += 1
            continue
        due.append(entry)

    # Prefer exact commercial surfaces from the directory before generic cards.
    due.sort(key=lambda x: (not bool(x.get("commercial_surface_hint")), str(x.get("domain") or "")))
    due = due[: max(0, int(max_inspections))]
    accepted: dict[str, dict] = {}
    rejected = 0
    inspect_errors: list[dict] = []

    def inspect_entry(entry: dict):
        niche = str(entry.get("directory_niche") or "services")
        query = (
            f"findaforum {entry.get('directory_category') or ''} "
            f"{entry.get('surface_label') or ''} marketplace services classifieds jobs for sale"
        )
        try:
            return entry, _inspect_candidate(entry, niche, query), None
        except Exception as exc:
            return entry, None, f"{type(exc).__name__}: {exc}"

    if due:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 24))) as pool:
            futures = [pool.submit(inspect_entry, entry) for entry in due]
            for fut in as_completed(futures):
                entry, candidate, error = fut.result()
                domain = _normalize_domain(entry.get("domain"))
                if error:
                    inspect_errors.append({"domain": domain, "error": error})
                if candidate is not None:
                    row = asdict(candidate)
                    row["search_engine"] = "findaforum"
                    row["directory_category"] = entry.get("directory_category")
                    row["source_detail_url"] = entry.get("detail_url")
                    row["source_surface_label"] = entry.get("surface_label")
                    accepted[domain] = row
                    state = "candidate"
                else:
                    rejected += 1
                    state = "not_commercial_or_not_forum"
                    if domain and entry.get("_terminal_reason"):
                        mark_terminal_domain(
                            domain,
                            str(entry.get("_terminal_reason")),
                            source="findaforum",
                            evidence=str(entry.get("_terminal_evidence") or entry.get("url") or "")[:500],
                        )
                        state = "terminal_dead_or_parked"
                scan[domain] = {
                    "domain": domain,
                    "url": entry.get("url"),
                    "category": entry.get("directory_category"),
                    "niche": entry.get("directory_niche"),
                    "status": state,
                    "checked_at": now_iso,
                    "recheck_after_days": DIRECTORY_RECHECK_DAYS,
                }

    merged = dict(existing)
    new_domains: list[str] = []
    improved_domains: list[str] = []
    for domain, row in accepted.items():
        old = merged.get(domain)
        if old is None:
            row["discovered_at"] = row.get("discovered_at") or now_iso
            new_domains.append(domain)
            merged[domain] = row
            continue
        if _is_material_discovery_progress(old, row):
            improved_domains.append(domain)
        row["discovered_at"] = old.get("discovered_at") or row.get("discovered_at") or now_iso
        row["score"] = max(int(old.get("score") or 0), int(row.get("score") or 0))
        if old.get("status") not in {None, "candidate"}:
            row["status"] = old.get("status")
        merged[domain] = row

    rows = sorted(
        merged.values(),
        key=lambda x: (-int(x.get("score") or 0), x.get("domain") or ""),
    )
    _save(DISCOVERY_FILE, rows)
    _save(
        FINDAFORUM_SCAN_FILE,
        sorted(scan.values(), key=lambda x: x.get("domain") or ""),
    )
    return {
        "at": now_iso,
        "source": "findaforum",
        "source_cached": source_cached,
        "source_entries": len(entries),
        "due_inspections": len(due),
        "accepted": len(accepted),
        "rejected": rejected,
        "new_candidates": len(new_domains),
        "improved_candidates": len(improved_domains),
        "progress_count": len(new_domains) + len(improved_domains),
        "cached_skips": cached_skips,
        "total_candidates": len(rows),
        "source_errors": source_errors,
        "inspection_errors": inspect_errors,
    }


def discover(max_queries: int = 24, per_query: int = 8, sleep_s: float = 0.3, priority_niches: list[str] | None = None, directory_max_inspections: int = 0, findaforum_max_inspections: int = 0) -> dict:
    terminal_domains=terminal_domain_map()
    existing = {
        x.get("domain"): x for x in _load(DISCOVERY_FILE, [])
        if x.get("domain")
        and x.get("commercial_context")
        and x.get("domain") not in BLOCK_DOMAINS
        and _normalize_domain(x.get("domain")) not in terminal_domains
    }
    found = {}
    progress_domains: set[str] = set()
    new_domains: set[str] = set()
    improved_domains: set[str] = set()
    errors = []
    plan = query_plan(max_queries=max_queries, priority_niches=priority_niches)
    ddg_enabled = True
    ddg_access_failures = 0
    ddg_disabled_after_query: str | None = None
    for niche, query in plan:
        try:
            hits, search_errors = _search_all(query, limit=per_query, use_ddg=ddg_enabled)
            for err in search_errors:
                errors.append({"query": query, "error": err})
                low_err = err.lower()
                if ddg_enabled and err.startswith("duckduckgo_lite:") and any(marker in low_err for marker in [
                    "403", "429", "connectionerror", "connecttimeout",
                    "connection reset", "connection aborted", "timed out",
                ]):
                    ddg_access_failures += 1
                    if ddg_access_failures >= 2:
                        ddg_enabled = False
                        ddg_disabled_after_query = query
            eligible_hits = [
                h for h in hits
                if h.get("domain") not in BLOCK_DOMAINS
                and _normalize_domain(h.get("domain")) not in terminal_domains
            ]
            for hit in eligible_hits:
                c = _candidate_from_search_hit(hit, niche, query)
                if not c:
                    continue
                old = found.get(c.domain) or existing.get(c.domain)
                row = asdict(c)
                row["search_engine"] = hit.get("engine")
                if _is_material_discovery_progress(old, row):
                    progress_domains.add(c.domain)
                    if c.domain in existing:
                        improved_domains.add(c.domain)
                    else:
                        new_domains.add(c.domain)
                if old:
                    row["discovered_at"] = old.get("discovered_at") or row["discovered_at"]
                    row["score"] = max(int(old.get("score") or 0), row["score"])
                    # Preserve downstream review/registration state.
                    if old.get("status") not in {None,"candidate"}:
                        row["status"] = old["status"]
                found[c.domain] = row
        except Exception as exc:
            errors.append({"query": query, "error": f"{type(exc).__name__}: {exc}"})
        time.sleep(sleep_s)

    merged = dict(existing)
    merged.update(found)
    rows = sorted(merged.values(), key=lambda x: (-int(x.get("score") or 0), x.get("domain") or ""))
    _save(DISCOVERY_FILE, rows)

    directory_result = None
    if int(directory_max_inspections or 0) > 0:
        try:
            directory_result = discover_forum_directory(
                max_entries=1200,
                max_inspections=int(directory_max_inspections),
            )
            rows = _load(DISCOVERY_FILE, [])
        except Exception as exc:
            directory_result = {
                "source": "forumdirectory",
                "progress_count": 0,
                "new_candidates": 0,
                "improved_candidates": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

    findaforum_result = None
    if int(findaforum_max_inspections or 0) > 0:
        try:
            findaforum_result = discover_findaforum(
                max_entries=1500,
                max_inspections=int(findaforum_max_inspections),
            )
            rows = _load(DISCOVERY_FILE, [])
        except Exception as exc:
            findaforum_result = {
                "source": "findaforum",
                "progress_count": 0,
                "new_candidates": 0,
                "improved_candidates": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

    directory_progress = int((directory_result or {}).get("progress_count") or 0)
    directory_new = int((directory_result or {}).get("new_candidates") or 0)
    directory_improved = int((directory_result or {}).get("improved_candidates") or 0)
    findaforum_progress = int((findaforum_result or {}).get("progress_count") or 0)
    findaforum_new = int((findaforum_result or {}).get("new_candidates") or 0)
    findaforum_improved = int((findaforum_result or {}).get("improved_candidates") or 0)
    total_progress = len(progress_domains) + directory_progress + findaforum_progress
    status = {
        "at": datetime.now(timezone.utc).isoformat(),
        "queries": len(plan),
        "priority_niches": list(priority_niches or []),
        # Backwards-compatible field now means real progress, not every
        # rediscovered domain. Guardian uses progress_count to avoid loops.
        "new_or_refreshed": total_progress,
        "progress_count": total_progress,
        "new_candidates": len(new_domains) + directory_new + findaforum_new,
        "improved_candidates": len(improved_domains) + directory_improved + findaforum_improved,
        "rediscovered_without_progress": max(0, len(found) - len(progress_domains)),
        "total_candidates": len(rows),
        "directory": directory_result,
        "findaforum": findaforum_result,
        "errors": errors,
        "search_circuit": {
            "duckduckgo_enabled_at_end": ddg_enabled,
            "duckduckgo_access_failures": ddg_access_failures,
            "duckduckgo_disabled_after_query": ddg_disabled_after_query,
        },
        "top": rows[:25],
    }
    _save(DISCOVERY_RUN_FILE, status)
    return status

def list_candidates(min_score: int = 8) -> list[dict]:
    out = []
    terminal_domains=terminal_domain_map()
    for x in _load(DISCOVERY_FILE, []):
        if int(x.get("score") or 0) < min_score or not x.get("commercial_context"):
            continue
        if x.get("domain") in BLOCK_DOMAINS or _normalize_domain(x.get("domain")) in terminal_domains:
            continue
        text = f"{x.get('name','')} {x.get('url','')} {x.get('query','')}".lower()
        event_like = any(w in text for w in EVENT_HINTS)
        non_forum_page = any(w in text for w in BLOCK_TITLE_WORDS)
        if event_like or non_forum_page:
            continue
        out.append(x)
    return out

def status() -> dict:
    return _load(DISCOVERY_RUN_FILE, {"at": None, "queries": 0, "new_or_refreshed": 0, "progress_count": 0, "new_candidates": 0, "improved_candidates": 0, "total_candidates": len(list_candidates()), "errors": []})
