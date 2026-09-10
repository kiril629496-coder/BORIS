from __future__ import annotations

import fcntl
import hashlib
import json
import re
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from app.services import service_marketplace as marketplace
from app.services import forum_quality

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ROOT = BACKEND_ROOT / "data" / "crowd_seo"
PROJECTS_FILE = ROOT / "projects.json"
PROJECTS_LOCK_FILE = ROOT / "projects.lock"
_PROJECTS_THREAD_LOCK = threading.RLock()
_PROJECTS_LOCK_DEPTH = threading.local()
UA = "Mozilla/5.0 (compatible; BORIS-CrowdSEO/1.0; +https://boris-ai.pro)"
TIMEOUT = 12
MAX_PAGES = 5
ANALYSIS_REFRESH_INTERVAL = timedelta(hours=24)

STOPWORDS = {
    "и","в","во","не","что","он","на","я","с","со","как","а","то","все","она","так","его","но","да","ты",
    "к","у","же","вы","за","бы","по","только","ее","мне","было","вот","от","меня","еще","нет","о","из","ему",
    "теперь","когда","даже","ну","вдруг","ли","если","уже","или","ни","быть","был","него","до","вас","нибудь",
    "опять","уж","вам","ведь","там","потом","себя","ничего","ей","может","они","тут","где","есть","надо","ней",
    "для","мы","тебя","их","чем","была","сам","чтоб","без","будто","чего","раз","тоже","себе","под","будет",
    "ж","тогда","кто","этот","того","потому","этого","какой","совсем","ним","здесь","этом","один","почти",
    "мой","тем","чтобы","нее","сейчас","были","куда","зачем","сказать","всех","никогда","сегодня","можно",
    "при","наша","наш","ваш","ваша","это","главная","главный","официальный","сайт","страница","компания",
}

NICHE_RULES = {
    "construction": ["строит","ремонт","дом","коттедж","кровл","бетон","арматур","стройматериал","фасад"],
    "marketing": ["реклам","маркетинг","seo","продвижен","лид","директ","авито"],
    "marketplaces": ["маркетплейс","wildberries","ozon","селлер"],
    "furniture": ["мебел","кухн","шкаф","гардероб"],
    "logistics": ["логист","перевоз","доставк","транспорт"],
    "auto": ["авто","машин","автосервис","запчаст"],
    "realestate": ["недвиж","риелтор","квартир","новострой"],
    "horeca": ["ресторан","кафе","horeca","общепит","пищев","продукты питания"],
    "medicine": ["медицин","клиник","стомат","врач"],
    "manufacturing": ["производств","завод","оборудован","промышлен"],
    "it": ["разработ","программ","crm","api","сайт","интеграц","автоматизац"],
    "tourism": ["туризм","путешеств","отдых","тур","отель","гостиниц"],
    "beauty": ["красот","салон","космет","парикмах"],
    "education": ["обучен","курс","школ","образован"],
    "goods": ["товар","магазин","каталог","купить","цена","опт","розниц","доставк","в наличии"],
    "services": ["услуг","заказать","под ключ","сервис","консультац","обслуживан"],
    "business": ["бизнес","предприним","продаж","коммерц"],
}

NICHE_RU = {
    "construction":"строительство","marketing":"маркетинг и реклама","marketplaces":"маркетплейсы",
    "furniture":"мебель","logistics":"логистика","auto":"авто","realestate":"недвижимость",
    "horeca":"HoReCa и опт","medicine":"медицина","manufacturing":"производство","it":"IT и разработка",
    "tourism":"туризм и отдых","beauty":"красота","education":"образование",
    "goods":"товары","services":"услуги","business":"бизнес",
}

# CROWD_SEO_OFFER_TYPE_V1
# Клиент по-прежнему вводит только сайт. BORIS сам понимает, что продвигаем:
# товары, услуги или смешанное предложение. Это нужно, чтобы тексты для
# товарного бизнеса не звучали как шаблон про "состав услуги".
OFFER_TYPE_RU = {
    "goods": "товары",
    "services": "услуги",
    "mixed": "товары и услуги",
}
GOODS_MARKERS = (
    "товар","купить","каталог","магазин","в наличии","наличи","артикул","модель",
    "цена","руб","₽","доставка","опт","розниц","корзин","заказ товара","продукц",
    "product","catalog","shop","store","price","cart","sku",
)
SERVICE_MARKERS = (
    "услуг","заказать","под ключ","консультац","монтаж","ремонт","установк",
    "обслуживан","разработк","настройк","внедрен","сопровожд","выезд","замер",
    "service","services","consult","installation","repair",
)

def _offer_evidence(pages: list[dict], keywords: list[str]) -> tuple[int,int]:
    corpus = " ".join(keywords)
    for row in pages:
        corpus += " " + " ".join([
            str(row.get("title") or ""),
            str(row.get("h1") or ""),
            str(row.get("description") or ""),
            str(row.get("body") or "")[:6000],
        ])
    low = corpus.lower().replace("ё","е")
    goods = sum(low.count(x) for x in GOODS_MARKERS)
    services = sum(low.count(x) for x in SERVICE_MARKERS)
    return goods, services

def _detect_offer_type(pages: list[dict], keywords: list[str], niche: str | None = None) -> str:
    goods, services = _offer_evidence(pages, keywords)
    if niche == "goods":
        goods += 4
    if niche == "services":
        services += 4
    # IT/marketing/business sites often mention client industries such as
    # shops/catalogs/products in examples. Do not mistake those examples for
    # the site's own goods catalog when service evidence is clearly stronger.
    # A real transactional goods signal (buy/stock/cart/delivery/SKU) keeps the
    # site eligible for mixed classification.
    corpus = " ".join(keywords)
    for row in pages:
        corpus += " " + " ".join([
            str(row.get("title") or ""),
            str(row.get("h1") or ""),
            str(row.get("description") or ""),
            str(row.get("body") or "")[:6000],
        ])
    low = corpus.lower().replace("ё", "е")
    transactional_goods = sum(
        low.count(marker)
        for marker in ("купить", "в наличии", "артикул", "sku", "корзин", "доставка", "заказ товара")
    )
    if (
        niche in {"it", "marketing", "business"}
        and transactional_goods == 0
        and services >= 3
    ):
        # Service/SaaS sites frequently mention shops, catalogs and products in
        # examples/cases. Without actual buy/stock/cart/order signals those
        # mentions must not turn a service business into a goods campaign.
        return "services"
    # Смешанный сайт считаем mixed только когда обе стороны реально заметны.
    if goods > 0 and services > 0 and min(goods, services) >= max(2, int(max(goods, services) * 0.35)):
        return "mixed"
    if goods > services:
        return "goods"
    if services > 0:
        return "services"
    # Для старых/неочевидных корпоративных сайтов безопаснее нейтральный текст.
    return "mixed"

def _legacy_offer_type(project: dict) -> str:
    current=str(project.get("offer_type") or "")
    if current in OFFER_TYPE_RU:
        return current
    niche=str(project.get("niche") or "")
    if niche == "goods":
        return "goods"
    if niche == "services":
        return "services"
    pseudo=[{"title":" ".join(str(x.get("label") or "") for x in (project.get("target_pages") or []))}]
    goods,services=_offer_evidence(pseudo,list(project.get("keywords") or []))
    if goods > services and goods > 0:
        return "goods"
    if services > 0:
        return "services"
    return "mixed"

def _offer_copy_terms(offer_type: str) -> tuple[str,str,str]:
    if offer_type == "goods":
        return (
            "товар",
            "характеристики, наличие, цену, доставку и гарантию",
            "характеристики товара, наличие, условия доставки и гарантию",
        )
    if offer_type == "services":
        return (
            "услугу",
            "состав работ, сроки, условия и сопровождение",
            "что входит в работу, сроки, условия и дальнейшее сопровождение",
        )
    return (
        "предложение",
        "состав предложения, цену, сроки или доставку и дальнейшие условия",
        "что именно входит в предложение, условия, сроки или доставку",
    )

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _load() -> list[dict]:
    ROOT.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        return []
    try:
        data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save(rows: list[dict]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROJECTS_FILE)


@contextmanager
def project_state_lock():
    """Re-entrant in-process + cross-process lock for projects.json mutations.

    API requests and the systemd guardian both mutate the same JSON state. The
    previous atomic rename prevented torn files but not lost updates from two
    read-modify-write cycles racing each other.
    """
    ROOT.mkdir(parents=True, exist_ok=True)
    with _PROJECTS_THREAD_LOCK:
        depth = int(getattr(_PROJECTS_LOCK_DEPTH, "value", 0) or 0)
        if depth > 0:
            _PROJECTS_LOCK_DEPTH.value = depth + 1
            try:
                yield
            finally:
                _PROJECTS_LOCK_DEPTH.value = depth
            return

        with open(PROJECTS_LOCK_FILE, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            _PROJECTS_LOCK_DEPTH.value = 1
            try:
                yield
            finally:
                _PROJECTS_LOCK_DEPTH.value = 0
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _locked_project_mutation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with project_state_lock():
            return fn(*args, **kwargs)
    return wrapped


def _normalise_site(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("site is required")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    p = urlparse(raw)
    if not p.netloc:
        raise ValueError("invalid site")
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))


INACTIVE_CONTENT_STATUSES = {"rejected", "superseded"}
INACTIVE_PROJECT_STATUSES = {"content_rejected", "duplicate_superseded"}


def project_is_active(project: dict | None) -> bool:
    """One live Crowd SEO project per client site.

    Historical duplicates stay in the audit file but never consume capacity,
    create drafts or publish in parallel.
    """
    if not isinstance(project, dict):
        return False
    if str(project.get("content_status") or "") in INACTIVE_CONTENT_STATUSES:
        return False
    if str(project.get("status") or "") in INACTIVE_PROJECT_STATUSES:
        return False
    return True


def _project_content_validation(project: dict) -> dict:
    """Deterministic fail-closed validation for the one-input client flow.

    The client supplies only the site. BORIS may auto-approve only content that
    was derived from that site and still points to the same host. No owner
    click is required when these checks pass.
    """
    errors: list[str] = []
    try:
        site = _normalise_site(project.get("site") or "")
    except Exception:
        site = ""
        errors.append("invalid_site")
    site_host = urlparse(site).netloc.removeprefix("www.").lower() if site else ""

    offer_type = str(project.get("offer_type") or "")
    if offer_type not in OFFER_TYPE_RU:
        errors.append("offer_type_missing")

    targets = list(project.get("target_pages") or [])
    if not targets:
        errors.append("target_pages_missing")
    for row in targets:
        try:
            target = _normalise_site(str(row.get("url") or ""))
        except Exception:
            errors.append("target_url_invalid")
            continue
        target_host = urlparse(target).netloc.removeprefix("www.").lower()
        if site_host and target_host != site_host:
            errors.append("target_url_cross_domain")

    keywords = [str(x).strip() for x in (project.get("keywords") or []) if str(x).strip()]
    if not keywords:
        errors.append("keywords_missing")

    variants = list(project.get("text_variants") or [])
    if not variants:
        errors.append("text_variants_missing")
    for row in variants:
        text = _clean_text(str(row.get("text") or ""))
        if len(text) < 40:
            errors.append("text_variant_too_short")
        if len(text) > 500:
            errors.append("text_variant_too_long")
        low = text.lower()
        if any(marker in low for marker in ("todo", "lorem ipsum", "example.com")) and site_host != "example.com":
            errors.append("placeholder_text")

    if project.get("free_only") is False:
        errors.append("nonfree_project")
    if project.get("generated_without_openai") is False:
        errors.append("unexpected_generation_mode")

    unique_errors = sorted(set(errors))
    return {
        "status": "PASS" if not unique_errors else "FAIL",
        "errors": unique_errors,
        "checked_at": _now(),
        "owner_action_required": False,
        "mode": "automatic_after_site_input",
    }


def _apply_auto_content_approval(project: dict) -> dict:
    validation = _project_content_validation(project)
    project["content_validation"] = validation
    project["owner_action_required"] = False
    if validation["status"] == "PASS":
        project["content_status"] = "approved"
        project["content_approval_mode"] = "automatic_after_site_input"
        project["content_approved_at"] = project.get("content_approved_at") or _now()
        project["internal_review_required"] = False
    else:
        project["content_status"] = "internal_review_required"
        project["content_approval_mode"] = "automatic_validation_failed"
        project["internal_review_required"] = True
    return validation


def _migrate_legacy_offer_type(project: dict) -> bool:
    """Backfill offer type/content for projects created before goods/services routing.

    Legacy rows are valid audit history, but an active legacy row must not stay
    forever in internal_review_required merely because the new derived field
    did not exist when it was created. Rebuild only deterministic local content;
    no external write or paid AI call is involved.
    """
    current = str(project.get("offer_type") or "")
    if current in OFFER_TYPE_RU:
        return False
    offer_type = _legacy_offer_type(project)
    project["offer_type"] = offer_type
    project["offer_type_label"] = OFFER_TYPE_RU.get(offer_type, offer_type)
    project["text_variants"] = _text_variants(
        str(project.get("site") or ""),
        str(project.get("niche") or "business"),
        list(project.get("keywords") or []),
        list(project.get("target_pages") or []),
        offer_type,
    )
    project["updated_at"] = _now()
    return True


@_locked_project_mutation
def reconcile_projects() -> dict:
    """Self-heal legacy unapproved projects and collapse same-site duplicates.

    This is deliberately local/state-only: no external publication is attempted.
    The newest safe project wins unless an older one already has a publication.
    """
    rows = _load()
    changed = 0
    migrated_offer_type: list[str] = []
    auto_approved: list[str] = []
    superseded: list[dict] = []

    active = [p for p in rows if project_is_active(p)]
    by_site: dict[str, list[dict]] = {}
    for project in active:
        try:
            key = _normalise_site(project.get("site") or "")
        except Exception:
            continue
        by_site.setdefault(key, []).append(project)

    for site, group in by_site.items():
        if len(group) <= 1:
            continue

        def _rank(project: dict):
            has_publication = any(
                bool(x.get("publication_url"))
                for x in (project.get("placements") or [])
            )
            approved = str(project.get("content_status") or "") == "approved"
            return (
                1 if has_publication else 0,
                str(project.get("created_at") or ""),
                1 if approved else 0,
            )

        canonical = max(group, key=_rank)
        for duplicate in group:
            if duplicate is canonical:
                continue
            duplicate["status"] = "duplicate_superseded"
            duplicate["content_status"] = "superseded"
            duplicate["superseded_by"] = canonical.get("id")
            duplicate["superseded_at"] = _now()
            duplicate["owner_action_required"] = False
            superseded.append({
                "project": duplicate.get("id"),
                "superseded_by": canonical.get("id"),
                "site": site,
            })
            changed += 1

    for project in rows:
        if not project_is_active(project):
            continue
        if _migrate_legacy_offer_type(project):
            migrated_offer_type.append(str(project.get("id") or ""))
            changed += 1
        if str(project.get("content_status") or "") != "approved":
            validation = _apply_auto_content_approval(project)
            if validation["status"] == "PASS":
                auto_approved.append(str(project.get("id") or ""))
                changed += 1

    if changed:
        _save(rows)

    # Keep duplicate drafts non-publishable while preserving already posted
    # history. The project retains an explicit superseded reason.
    if superseded:
        superseded_ids = {x["project"] for x in superseded}
        for draft in marketplace.list_drafts():
            if (
                draft.get("crowd_project_id") in superseded_ids
                and draft.get("status") not in {"posted", "rejected"}
            ):
                try:
                    marketplace.set_draft_status(str(draft.get("id")), "rejected")
                except Exception:
                    pass

    return {
        "changed": changed,
        "active_projects": sum(1 for p in rows if project_is_active(p)),
        "migrated_offer_type": migrated_offer_type,
        "auto_approved": auto_approved,
        "superseded": superseded,
        "owner_action_required": False,
    }


def _html_needs_browser_render(html: str) -> bool:
    """Detect SPA shells where requests sees only a loader/meta shell."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script","style","noscript","svg"]):
        tag.decompose()
    text = _clean_text(soup.get_text(" ", strip=True))
    low = text.lower()
    loader_hints = (
        "загрузка",
        "loading",
        "enable javascript",
        "javascript required",
    )
    return len(text) < 650 or (len(text) < 1400 and any(x in low for x in loader_hints))

def _rendered_fetch(url: str) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            final_url = page.url
            html = page.content()
            if response and response.status >= 400:
                raise ValueError(f"rendered site returned HTTP {response.status}")
            return final_url, html
        finally:
            browser.close()

def _fetch(url: str) -> tuple[str, str]:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language":"ru-RU,ru;q=0.9"}, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "html" not in ctype and "<html" not in r.text[:1000].lower():
        raise ValueError("site did not return HTML")
    if _html_needs_browser_render(r.text):
        try:
            rendered_url, rendered_html = _rendered_fetch(r.url)
            static_len = len(_page_info(r.url, r.text).get("body") or "")
            rendered_len = len(_page_info(rendered_url, rendered_html).get("body") or "")
            if rendered_len >= max(800, static_len * 2):
                return rendered_url, rendered_html
        except Exception:
            pass
    return r.url, r.text

def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()

def _page_info(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript","svg"]):
        tag.decompose()
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    h1 = _clean_text(soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = _clean_text(meta.get("content","") if meta else "")
    body = _clean_text(soup.get_text(" ", strip=True))[:18000]
    return {"url":url,"title":title,"h1":h1,"description":desc,"body":body}

NONCOMMERCIAL_PATH_SEGMENTS = {
    "privacy","politika-konfidencialnosti","policy","oferta","offer","terms","legal",
    "support","help","contact","contacts","about","login","register","auth","account",
    "cart","checkout","search","blog","news","verify","onboarding","memory","training",
    "dashboard","admin","journal","robots.txt","sitemap.xml",
}

def _is_noncommercial_target_url(url: str) -> bool:
    p = urlparse(str(url or ""))
    path = (p.path or "/").lower().strip("/")
    if not path:
        return False
    segments = {x for x in re.split(r"[/_.-]+", path) if x}
    if segments & NONCOMMERCIAL_PATH_SEGMENTS:
        return True
    return path.startswith(("api/","static/","assets/","_next/"))

def _internal_links(base_url: str, html: str) -> list[str]:
    host = urlparse(base_url).netloc.lower()
    soup = BeautifulSoup(html, "lxml")
    scored: list[tuple[int,str]] = []
    seen = set()
    positive = ["uslug","service","product","catalog","price","tarif","solution","naprav","category","shop","kurs","remont"]
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a.get("href","")).split("#",1)[0]
        p = urlparse(href)
        if p.netloc.lower() != host or p.scheme not in {"http","https"}:
            continue
        clean = urlunparse((p.scheme,p.netloc,p.path or "/", "", "", ""))
        if clean in seen or clean.rstrip("/") == base_url.rstrip("/"):
            continue
        if _is_noncommercial_target_url(clean):
            continue
        seen.add(clean)
        low = (p.path + " " + _clean_text(a.get_text(" ",strip=True))).lower()
        score = sum(3 for x in positive if x in low)
        if p.path.count("/") <= 3:
            score += 1
        scored.append((score,clean))
    return [u for _,u in sorted(scored,key=lambda x:(-x[0],len(x[1])))[:20]]

def _tokens(pages: list[dict]) -> list[str]:
    weighted = []
    for p in pages:
        weighted.extend(([p.get("title","")] * 4) + ([p.get("h1","")] * 5) + ([p.get("description","")] * 3) + [p.get("body","")[:5000]])
    text = " ".join(weighted).lower().replace("ё","е")
    words = re.findall(r"[а-яa-z0-9][а-яa-z0-9-]{2,}", text, re.I)
    return [w for w in words if w not in STOPWORDS and not w.isdigit() and len(w) <= 40]

def _brand_tokens(site: str, pages: list[dict]) -> set[str]:
    p = urlparse(site)
    domain_root = p.netloc.lower().removeprefix("www.").split(".")[0]
    out = {x for x in re.split(r"[^a-zа-я0-9]+", domain_root.replace("ё","е")) if len(x) >= 3}
    title = (pages[0].get("title","") if pages else "").replace("ё","е")
    prefix = re.split(r"[—|:-]", title, maxsplit=1)[0].strip()
    prefix_words = re.findall(r"[а-яa-z0-9][а-яa-z0-9-]{2,}", prefix.lower(), re.I)
    if 0 < len(prefix_words) <= 3:
        out.update(prefix_words)
    return out

HIGH_SIGNAL_CAPABILITIES = [
    (("автоматизац", "автопилот"), "автоматизация"),
    (("crm",), "CRM"),
    (("api",), "API"),
    (("интеграц",), "интеграции"),
    (("создание сайт", "разработк сайт", "веб-разработ"), "создание сайтов"),
    (("разработ", "программ"), "разработка"),
    (("бот", "чат-бот", "чатбот"), "боты"),
    (("лидогенерац",), "лидогенерация"),
    (("seo",), "SEO"),
    (("smm",), "SMM"),
    (("реклам",), "реклама"),
    (("маркетинг",), "маркетинг"),
    (("аналитик",), "аналитика"),
    (("продаж",), "продажи"),
]

def _high_signal_keywords(pages: list[dict]) -> list[str]:
    corpus = " ".join(
        " ".join([
            str(p.get("title") or ""),
            str(p.get("h1") or ""),
            str(p.get("description") or ""),
            str(p.get("body") or ""),
        ])
        for p in pages
    ).lower().replace("ё", "е")
    out = []
    for stems, label in HIGH_SIGNAL_CAPABILITIES:
        if any(stem in corpus for stem in stems):
            out.append(label)
    return out

def _keywords(pages: list[dict], site: str, limit: int = 10) -> list[str]:
    tokens = _tokens(pages)
    banned = _brand_tokens(site, pages)
    uni = Counter(tokens)
    big = Counter(" ".join(pair) for pair in zip(tokens,tokens[1:]) if pair[0] != pair[1])
    candidates = [(w,c) for w,c in uni.items() if c >= 2]
    candidates += [(w,c*1.7) for w,c in big.items() if c >= 2]
    out=_high_signal_keywords(pages)[:limit]
    if len(out) >= limit:
        return out[:limit]
    for w,_ in sorted(candidates,key=lambda x:(-x[1],-len(x[0]))):
        parts=set(w.split())
        if parts & banned:
            continue
        # Russian/other inflected brand forms must not leak into SEO keys
        # (e.g. "Борис" -> "Бориса"/"Борисе"). Exact-token filtering is not enough.
        if any(
            part.startswith(brand) or brand.startswith(part)
            for part in parts
            for brand in banned
            if len(part) >= 4 and len(brand) >= 4
        ):
            continue
        if any(w in old or old in w for old in out):
            continue
        out.append(w)
        if len(out)>=limit:
            break
    return out

def _detect_niche(pages: list[dict], keywords: list[str]) -> str:
    corpus = (" ".join(keywords) + " " + " ".join((p.get("title","")+" "+p.get("h1","")) for p in pages)).lower()
    scores = {k:sum(1 for stem in stems if stem in corpus) for k,stems in NICHE_RULES.items()}

    # Preserve a concrete industry whenever one is detected. Generic
    # goods/services are fallbacks for stores and service businesses that do
    # not belong to a narrower vertical in our taxonomy.
    broad = {"goods", "services", "business"}
    specific = {k:v for k,v in scores.items() if k not in broad}
    if specific:
        best_specific = max(specific, key=specific.get)
        if specific[best_specific] > 0:
            return best_specific

    for fallback in ("goods", "services", "business"):
        if scores.get(fallback, 0) > 0:
            return fallback
    return "business"

def _target_pages(pages: list[dict]) -> list[dict]:
    out=[]
    for p in pages:
        url=str(p.get("url") or "")
        if not url or _is_noncommercial_target_url(url):
            continue
        label = p.get("h1") or p.get("title") or urlparse(url).path.strip("/") or "главная"
        out.append({"url":url,"label":str(label)[:140]})
        if len(out)>=MAX_PAGES:
            break
    if not out and pages:
        first=pages[0]
        url=str(first.get("url") or "")
        if url:
            label=first.get("h1") or first.get("title") or "главная"
            out.append({"url":url,"label":str(label)[:140]})
    return out

def _anchor_plan(targets: list[dict], keywords: list[str]) -> list[dict]:
    if not targets:
        return []
    plan=[]
    for i,t in enumerate(targets):
        kw = keywords[i % len(keywords)] if keywords else t["label"]
        mode = "безанкорная" if i % 4 != 3 else "частичный анкор"
        anchor = t["url"] if mode == "безанкорная" else kw
        plan.append({"url":t["url"],"keyword":kw,"mode":mode,"anchor":anchor})
    return plan

def _text_variants(site: str, niche: str, keywords: list[str], targets: list[dict], offer_type: str = "mixed") -> list[dict]:
    brand = urlparse(site).netloc.removeprefix("www.")
    k1 = keywords[0] if keywords else NICHE_RU.get(niche,"предложение")
    k2 = keywords[1] if len(keywords)>1 else "выбор компании"
    t1 = targets[0]["url"] if targets else site
    noun, compare_short, compare_long = _offer_copy_terms(offer_type)
    templates = [
        f"Если ищете {noun} по теме «{k1}», можно посмотреть {brand}. На сайте есть подробное описание и варианты: {t1}. Перед выбором лучше сравнить {compare_short}.",
        f"По теме «{k2}» пригодился сайт {brand}: {t1}. Там основные условия собраны в одном месте, без необходимости уточнять всё по частям. Ссылку оставляю как дополнительный вариант для сравнения.",
        f"Для тех, кто сейчас выбирает по тематике «{NICHE_RU.get(niche,'бизнес')}»: у {brand} есть отдельная страница {t1}. Я бы сравнивал не только цену, но и {compare_long}.",
    ]
    return [{"variant":i+1,"text":_clean_text(x)[:500]} for i,x in enumerate(templates)]

NICHE_COMPATIBILITY = {
    "marketing": {"marketing", "business", "services"},
    "it": {"it", "business", "services"},
    "construction": {"construction", "business", "services"},
    "furniture": {"furniture", "business", "services"},
    "logistics": {"logistics", "business", "services"},
    "auto": {"auto", "business", "services"},
    "realestate": {"realestate", "business", "services"},
    "horeca": {"horeca", "business", "services"},
    "medicine": {"medicine", "business", "services"},
    "manufacturing": {"manufacturing", "business", "services"},
    "marketplaces": {"marketplaces", "marketing", "business", "services"},
    "tourism": {"tourism", "business", "services"},
    "beauty": {"beauty", "business", "services"},
    "education": {"education", "business", "services"},
    "goods": {"goods", "marketplaces", "business"},
    "services": {"services", "business"},
    "business": {"business", "services"},
}

PLACEMENT_FAILED_STATUSES = {
    "replacement_required",
    "platform_replacement_required",
    "guarantee_expired",
}

def _project_required_count(project: dict) -> int:
    """Commercial volume comes only from the concrete client order.

    Internal inventory/marketing projects have no artificial package target.
    """
    if str(project.get("plan_mode") or "") == "inventory":
        return 0
    target_raw=project.get("target_count")
    if target_raw is None:
        raise ValueError("crowd_seo_contract_target_required")
    bonus_raw=project.get("bonus_count")
    target=max(1,int(target_raw))
    bonus=max(0,int(0 if bonus_raw is None else bonus_raw))
    return target+bonus

def _project_plan_count(project: dict, candidates: list[dict] | None = None) -> int:
    """How many eligible slots this project actively tries to publish.

    Client contracts follow their explicit order volume. Internal inventory
    projects may use every currently eligible relevant surface.
    """
    pool = candidates if candidates is not None else list(project.get("forum_candidates") or [])
    if str(project.get("plan_mode") or "") == "inventory" or project.get("publish_all_eligible"):
        return len(pool)
    return _project_required_count(project)

def _project_keywords(project: dict) -> list[str]:
    """Merge live-site semantics with an optional campaign keyword bank."""
    out = []
    profile = project.get("campaign_profile") or {}
    values = list(profile.get("keyword_bank") or []) + list(project.get("keywords") or [])
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out

def _placement_is_active(placement: dict) -> bool:
    return str(placement.get("status") or "") not in PLACEMENT_FAILED_STATUSES

def _parse_iso_dt(value: str | None) -> datetime | None:
    raw=str(value or "").strip()
    if not raw:
        return None
    try:
        dt=datetime.fromisoformat(raw.replace("Z","+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _guarantee_deadline(project: dict, published_at: str | None) -> str | None:
    start=_parse_iso_dt(published_at)
    if not start:
        return None
    days=max(0,int(project.get("guarantee_days",30) or 0))
    return (start+timedelta(days=days)).isoformat()

def _stamp_guarantee(project: dict, placement: dict) -> None:
    if not placement.get("published_at"):
        return
    if not placement.get("guarantee_until"):
        placement["guarantee_until"]=_guarantee_deadline(project,placement.get("published_at"))

def _guarantee_expired(project: dict, placement: dict, *, now: datetime | None = None) -> bool:
    if not placement.get("publication_url"):
        return False
    _stamp_guarantee(project,placement)
    deadline=_parse_iso_dt(placement.get("guarantee_until"))
    if not deadline:
        return False
    return (now or datetime.now(timezone.utc)) > deadline

def _placement_summary(project: dict) -> dict:
    placements=list(project.get("placements") or [])
    active=[x for x in placements if _placement_is_active(x)]
    history=[x for x in placements if not _placement_is_active(x)]
    guarantee_expired=[
        x for x in placements
        if x.get("status")=="guarantee_expired" and x.get("publication_url")
    ]
    planned=_project_plan_count(project)
    published=[x for x in active if x.get("publication_url")]
    verified=[x for x in active if x.get("status")=="verified"]
    contract_covered=len(active)+len(guarantee_expired)
    return {
        "planned":planned,
        "active":len(active),
        "history":len(history),
        "published":len(published),
        "verified":len(verified),
        "guarantee_expired":len(guarantee_expired),
        "contract_covered":contract_covered,
        "active_deficit":max(0,planned-contract_covered),
    }

def _retarget_unpublished_placements(project: dict) -> int:
    """Self-heal target pages after a fresh site analysis.

    Published placements are immutable evidence and are never rewritten.
    Only active, not-yet-published slots are retargeted to the current
    commercial pages/keywords derived from the client's live site.
    """
    targets=list(project.get("target_pages") or [])
    keywords=[str(x) for x in (project.get("keywords") or []) if str(x).strip()]
    if not targets:
        return 0
    if not keywords:
        keywords=[str(project.get("niche_label") or "услуги")]
    brand=str(project.get("domain") or urlparse(str(project.get("site") or "")).netloc.removeprefix("www."))
    candidates=[
        x for x in (project.get("placements") or [])
        if _placement_is_active(x) and not x.get("publication_url")
    ]
    changed=0
    for i,pl in enumerate(candidates):
        target=targets[i % len(targets)]
        keyword=keywords[i % len(keywords)]
        mod=i % 10
        if mod <= 5:
            mode,anchor="безанкорная",target["url"]
        elif mod <= 8:
            mode,anchor="частичный анкор",keyword
        else:
            mode,anchor="брендовая",brand
        before=(pl.get("target_url"),pl.get("keyword"),pl.get("link_mode"),pl.get("anchor"),pl.get("text_variant"))
        after=(target["url"],keyword,mode,anchor,(i % 3)+1)
        if before != after:
            pl["target_url"],pl["keyword"],pl["link_mode"],pl["anchor"],pl["text_variant"]=after
            pl["retargeted_at"]=_now()
            changed+=1
    return changed

def _decorate_project(project: dict) -> dict:
    out=dict(project)
    offer_type=_legacy_offer_type(project)
    out["offer_type"]=offer_type
    out["offer_type_label"]=OFFER_TYPE_RU.get(offer_type,offer_type)
    out["placement_summary"]=_placement_summary(project)
    out["active"]=project_is_active(project)
    out["owner_action_required"]=False
    return out

def _slot_key(row: dict) -> str:
    return f"{row.get('platform')}::{row.get('surface_id') or 'default'}"

def _project_niche_aliases(project_niche: str, keywords: list[str]) -> set[str]:
    """Return secondary niches proven by the client's live-site semantics.

    A SaaS can legitimately be both IT and marketing. Keeping only the single
    highest-scoring niche hid already-audited SEO/advertising marketplaces from
    BORIS even when those capabilities were present on the live site.
    """
    aliases = {str(project_niche or "business")}
    corpus = " ".join(str(x) for x in keywords).lower().replace("ё", "е")
    if any(x in corpus for x in ["seo", "реклам", "маркетинг", "smm", "лидогенерац"]):
        aliases.add("marketing")
    if any(x in corpus for x in ["автомат", "crm", "api", "интеграц", "разработ", "создание сайтов", "бот"]):
        aliases.add("it")
    return aliases

def _niche_compatible(project_niche: str, platform_niche: str, keywords: list[str] | None = None) -> bool:
    aliases = _project_niche_aliases(project_niche, list(keywords or []))
    allowed = set()
    for alias in aliases:
        allowed.update(NICHE_COMPATIBILITY.get(alias, {alias, "business", "services"}))
    return platform_niche in allowed

def _surface_matches_project(
    surface: dict,
    niche: str,
    keywords: list[str],
    offer_type: str = "mixed",
) -> bool:
    niches = {str(x) for x in (surface.get("niches") or []) if x}
    if niches:
        format_niches = niches & {"goods", "services"}
        if (
            offer_type in {"goods", "services"}
            and format_niches
            and offer_type not in format_niches
        ):
            return False
        project_niches = _project_niche_aliases(niche, keywords)
        generic_ok = (
            "business" in niches
            or (offer_type == "goods" and "goods" in niches)
            or (offer_type == "services" and "services" in niches)
            or (
                offer_type == "mixed"
                and bool(niches & {"goods", "services", "business"})
            )
        )
        if not (project_niches & niches) and not generic_ok:
            return False
    required = [str(x).lower() for x in (surface.get("required_terms") or []) if str(x).strip()]
    if not required:
        return True
    corpus = " ".join(str(x) for x in keywords).lower().replace("ё","е")
    return any(term.replace("ё","е") in corpus for term in required)

MULTI_SURFACE_PROJECT_PLATFORMS = {
    # These two 1C surfaces serve materially different marketplace intents and
    # were separately verified before multi-surface routing was introduced.
    "forum_baza_1c",
}

def _surface_project_score(
    surface: dict,
    niche: str,
    keywords: list[str],
    offer_type: str = "mixed",
) -> tuple[int, int, int, int, str]:
    corpus = " ".join(str(x) for x in keywords).lower().replace("ё", "е")
    required = [
        str(x).lower().replace("ё", "е")
        for x in (surface.get("required_terms") or [])
        if str(x).strip()
    ]
    required_hits = sum(1 for term in required if term in corpus)
    surface_niches = {str(x) for x in (surface.get("niches") or []) if x}
    project_niches = _project_niche_aliases(niche, keywords)

    # A multi-purpose forum may have separate Goods and Services sections.
    # The offer format must win over a generic "business" match, otherwise a
    # product client can be routed into a services board (or vice versa).
    format_match = 0
    if offer_type == "goods" and "goods" in surface_niches:
        format_match = 3
    elif offer_type == "services" and "services" in surface_niches:
        format_match = 3
    elif offer_type == "mixed" and surface_niches & {"goods", "services"}:
        format_match = 2

    niche_match = 2 if project_niches & surface_niches else 0
    generic_business = 1 if "business" in surface_niches else 0

    # Required-term specificity remains useful inside the same format.
    # Stable name fallback keeps selection deterministic across guardian runs.
    return (
        format_match,
        niche_match,
        required_hits,
        1 if required else generic_business,
        str(surface.get("name") or ""),
    )

def _forum_matches(
    niche: str,
    limit: int = 40,
    keywords: list[str] | None = None,
    offer_type: str = "mixed",
) -> list[dict]:
    regs = {x["platform"]:x for x in marketplace.registration_plan()}
    project_keywords = list(keywords or [])
    rows=[]
    for p in marketplace.list_platforms():
        if p.get("channel_type") != "forum":
            continue
        if not p.get("enabled_for_outreach"):
            continue
        pniche = p.get("niche") or "services"
        reg = regs.get(p["key"],{})
        account_status = reg.get("status","not_registered")
        # Only a terminal block removes a client slot. Technical/adaptor blocks
        # stay in the pool so BORIS can diagnose and retry them automatically.
        if marketplace.registration_is_terminally_blocked(reg):
            continue

        explicit_surfaces = list(p.get("publication_surfaces") or [])
        if explicit_surfaces:
            surfaces = [
                s for s in explicit_surfaces
                if _surface_matches_project(s, niche, project_keywords, offer_type)
            ]
            if len(surfaces) > 1 and p["key"] not in MULTI_SURFACE_PROJECT_PLATFORMS:
                surfaces = [
                    max(
                        surfaces,
                        key=lambda s: _surface_project_score(
                            s,
                            niche,
                            project_keywords,
                            offer_type,
                        ),
                    )
                ]
        else:
            if not _niche_compatible(niche, pniche, project_keywords):
                continue
            surfaces = [{
                "id":"default",
                "name":p["name"],
                "url":p["url"],
                "post_url":None,
                "niches":[pniche],
                "required_terms":[],
            }]

        for surface in surfaces:
            relevance = 3 if pniche == niche else 2 if pniche in {"business","services"} else 1
            quality = forum_quality.quality_for_url(surface.get("url") or p.get("url"))
            rows.append({
                "platform":p["key"],
                "surface_id":surface.get("id") or "default",
                "surface_name":surface.get("name") or p["name"],
                "surface_language":str(surface.get("language") or "ru").lower(),
                "name":f"{p['name']} · {surface.get('name')}" if surface.get("id") not in {None,"default"} else p["name"],
                "url":surface.get("url") or p["url"],
                "post_url":surface.get("post_url"),
                "niche":pniche,
                "relevance":relevance,
                "account_status":account_status,
                "account_recoverable":marketplace.registration_is_recoverable(reg),
                "account_warming":marketplace.registration_is_warming(reg),
                "maturity_required":bool(
                    str(p.get("key") or "") in getattr(marketplace, "PLATFORM_MATURITY_REQUIREMENTS", {})
                ),
                "checkpoint":reg.get("checkpoint"),
                "publication_ready":bool(p.get("publication_ready")),
                "rule_status":(
                    (p.get("free_policy") or {}).get("rule_decision")
                    or (p.get("free_policy") or {}).get("reason")
                    or "rule_audit_required"
                ),
                "surface_evidence":surface.get("evidence"),
                "iks":quality.get("iks"),
                "iks_tier":quality.get("iks_tier"),
                "iks_measured_at":quality.get("iks_measured_at"),
            })
    # Prefer immediately usable/recoverable slots over long-warmup assets.
    # A mature forum remains valuable reserve capacity, but it must not occupy
    # a client's critical-path slot when an equally valid no-warmup surface exists.
    def _activation_rank(row: dict) -> int:
        if row.get("publication_ready"):
            return 0
        checkpoint = str(row.get("checkpoint") or "")
        if checkpoint in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS:
            return 1
        if row.get("account_status") == "ready":
            return 1
        if row.get("account_status") == "not_registered":
            return 2
        if row.get("account_recoverable"):
            return 4
        if row.get("account_status") == "in_progress":
            return 3
        return 3

    rows.sort(key=lambda x:(
        -x["publication_ready"],
        bool(x.get("maturity_required")),
        _activation_rank(x),
        -x["relevance"],
        -{"high":3,"medium":2,"low":1,"unknown":0}.get(str(x.get("iks_tier") or "unknown"),0),
        -int(x.get("iks") or 0),
        x["name"],
    ))
    return rows[:limit]

def bootstrap_priority_snapshot(projects: list[dict] | None = None) -> dict[str, dict]:
    """Prioritize one-time platform onboarding by live Crowd SEO demand.

    The service onboarding queue is global, but a worker should first prepare
    platforms that can unlock placements for currently waiting client projects.
    Counts use the same strict forum/surface matching as the real placement plan,
    so this must not inflate capacity by counting irrelevant forum sections.
    """
    queue_platforms = {
        str(x.get("platform") or "")
        for x in marketplace.platform_bootstrap_queue().get("items", [])
        if str(x.get("platform") or "")
    }
    if not queue_platforms:
        return {}

    active_projects = projects if projects is not None else list_projects()
    stats: dict[str, dict] = {}

    for project in active_projects:
        if not project_is_active(project):
            continue
        # Internal inventory/marketing projects grow the reusable reserve, but
        # they are not paying client demand and must never be labeled urgent.
        # The global bootstrap queue still processes them as strategic reserve.
        if str(project.get("plan_mode") or "") == "inventory":
            continue
        project_id = str(project.get("id") or "")
        if not project_id:
            continue
        matches = _forum_matches(
            project.get("niche") or "business",
            limit=max(100, _project_required_count(project) * 5),
            keywords=_project_keywords(project),
            offer_type=_legacy_offer_type(project),
        )
        # BOOTSTRAP_PLAN_SCOPE_V2:
        # Client projects are capped by the explicit order volume. Internal
        # inventory/marketing projects have no artificial package target and
        # therefore prioritize every relevant reusable platform.
        if str(project.get("plan_mode") or "") != "inventory":
            matches = matches[:_project_required_count(project)]
        per_platform_surfaces: dict[str, set[str]] = {}
        per_platform_relevance: dict[str, int] = {}
        per_platform_requires_warmup: dict[str, bool] = {}
        for match in matches:
            platform = str(match.get("platform") or "")
            checkpoint = str(match.get("checkpoint") or "")
            if (
                platform not in queue_platforms
                or match.get("publication_ready")
                or checkpoint not in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
            ):
                continue
            per_platform_surfaces.setdefault(platform, set()).add(
                str(match.get("surface_id") or "default")
            )
            per_platform_relevance[platform] = max(
                per_platform_relevance.get(platform, 0),
                int(match.get("relevance") or 0),
            )
            per_platform_requires_warmup[platform] = bool(
                per_platform_requires_warmup.get(platform)
                or match.get("maturity_required")
                or match.get("account_warming")
            )

        for platform, surfaces in per_platform_surfaces.items():
            row = stats.setdefault(
                platform,
                {
                    "platform": platform,
                    "relevant_projects": 0,
                    "relevant_sites": set(),
                    "potential_slots": 0,
                    "max_relevance": 0,
                    "requires_warmup_after_bootstrap": False,
                },
            )
            row["relevant_projects"] += 1
            if project.get("site"):
                row["relevant_sites"].add(str(project.get("site")))
            # One prepared account is reusable. Capacity gain is the maximum
            # number of strict surfaces this platform can add to one live project,
            # not the sum across duplicate/similar client projects.
            row["potential_slots"] = max(row["potential_slots"], len(surfaces))
            row["max_relevance"] = max(
                row["max_relevance"],
                per_platform_relevance.get(platform, 0),
            )
            row["requires_warmup_after_bootstrap"] = bool(
                row.get("requires_warmup_after_bootstrap")
                or per_platform_requires_warmup.get(platform)
            )

    return {
        platform: {
            **row,
            "relevant_sites": sorted(row["relevant_sites"]),
            "urgent_for_active_projects": True,
        }
        for platform, row in stats.items()
    }


def analyse_site(site: str) -> dict:
    site = _normalise_site(site)
    final_url, home_html = _fetch(site)
    home = _page_info(final_url, home_html)
    pages=[home]
    for u in _internal_links(final_url, home_html):
        if len(pages)>=MAX_PAGES:
            break
        try:
            fu,html = _fetch(u)
            info = _page_info(fu,html)
            if len(info.get("body","")) >= 200:
                pages.append(info)
        except Exception:
            continue
    kws = _keywords(pages, final_url)
    niche = _detect_niche(pages,kws)
    offer_type = _detect_offer_type(pages,kws,niche)
    targets = _target_pages(pages)
    anchors = _anchor_plan(targets,kws)
    return {
        "site":final_url,"domain":urlparse(final_url).netloc.removeprefix("www."),
        "niche":niche,"niche_label":NICHE_RU.get(niche,niche),
        "offer_type":offer_type,"offer_type_label":OFFER_TYPE_RU.get(offer_type,offer_type),
        "pages":targets,
        "keywords":kws[:10],"anchor_plan":anchors,
        "text_variants":_text_variants(final_url,niche,kws,targets,offer_type),
        "forums":_forum_matches(niche, keywords=kws, offer_type=offer_type),"analysed_at":_now(),
    }

def refresh_project_analysis(project_id: str, *, force: bool = False) -> dict:
    """Re-read the client site so Crowd SEO follows the current live offer.

    The network/browser work happens outside the state lock. A 24h guard keeps
    this cheap while allowing SPA content and newly-added services to self-heal.
    """
    current = get_project(project_id)
    stamp = current.get("analysis_refreshed_at") or current.get("analysed_at") or current.get("created_at")
    due = force
    if not due and stamp:
        try:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            due = datetime.now(timezone.utc) - dt.astimezone(timezone.utc) >= ANALYSIS_REFRESH_INTERVAL
        except Exception:
            due = True
    elif not due:
        due = True
    if not due:
        return {"changed": 0, "due": False, "project": project_id}

    analysis = analyse_site(str(current.get("site") or ""))
    with project_state_lock():
        rows = _load()
        project = next((x for x in rows if x.get("id") == project_id), None)
        if not project:
            raise KeyError(project_id)
        before = {
            "site": project.get("site"),
            "niche": project.get("niche"),
            "offer_type": project.get("offer_type"),
            "keywords": list(project.get("keywords") or []),
            "target_pages": list(project.get("target_pages") or []),
        }
        project.update({
            "site": analysis["site"],
            "domain": analysis["domain"],
            "niche": analysis["niche"],
            "niche_label": analysis["niche_label"],
            "offer_type": analysis["offer_type"],
            "offer_type_label": analysis["offer_type_label"],
            "keywords": analysis["keywords"],
            "target_pages": analysis["pages"],
            "anchor_plan": analysis["anchor_plan"],
            "text_variants": analysis["text_variants"],
            "forum_candidates": analysis["forums"],
            "analysed_at": analysis["analysed_at"],
            "analysis_refreshed_at": _now(),
            "updated_at": _now(),
        })
        after = {
            "site": project.get("site"),
            "niche": project.get("niche"),
            "offer_type": project.get("offer_type"),
            "keywords": list(project.get("keywords") or []),
            "target_pages": list(project.get("target_pages") or []),
        }
        changed = int(before != after)
        retargeted = _retarget_unpublished_placements(project)
        _save(rows)
    return {
        "changed": changed,
        "retargeted_unpublished": retargeted,
        "due": True,
        "project": project_id,
        "niche": analysis["niche"],
        "offer_type": analysis["offer_type"],
        "keywords": analysis["keywords"],
    }

@_locked_project_mutation
def create_project(site: str, target_count: int, bonus_count: int = 0) -> dict:
    requested_site = _normalise_site(site)
    requested_target = max(1, int(target_count))
    requested_bonus = max(0, int(bonus_count))

    def _find_existing(rows: list[dict], canonical_site: str) -> dict | None:
        matches = [
            p for p in rows
            if project_is_active(p)
            and _normalise_site(p.get("site") or "") == canonical_site
        ]
        return max(matches, key=lambda p: str(p.get("created_at") or "")) if matches else None

    def _reuse(current: dict, rows: list[dict]) -> dict:
        contract_changed = (
            str(current.get("plan_mode") or "") != "contract"
            or int(current.get("target_count") or 0) != requested_target
            or int(current.get("bonus_count") or 0) != requested_bonus
        )
        if contract_changed:
            current["plan_mode"] = "contract"
            current["contract_count_source"] = "explicit_order"
            current["target_count"] = requested_target
            current["bonus_count"] = requested_bonus
            current["updated_at"] = _now()
            _save(rows)
            prepare_placement_plan(str(current.get("id")))
            rows = _load()
            current = next(x for x in rows if x.get("id") == current.get("id"))
        if str(current.get("content_status") or "") != "approved":
            _apply_auto_content_approval(current)
            current["updated_at"] = _now()
            _save(rows)
            if current.get("content_status") == "approved":
                sync_project_drafts(str(current.get("id")))
        refreshed = get_project(str(current.get("id")))
        refreshed["reused_existing"] = True
        refreshed["contract_updated"] = contract_changed
        refreshed["owner_action_required"] = False
        return refreshed

    # First quick idempotency gate. The lock prevents API retries and the
    # systemd guardian from racing on the same state snapshot.
    with project_state_lock():
        rows = _load()
        current = _find_existing(rows, requested_site)
        if current:
            return _reuse(current, rows)

    # Site analysis can take seconds and does not mutate state, so do it without
    # holding the global project lock.
    analysis = analyse_site(requested_site)
    analysed_site = _normalise_site(analysis["site"])

    # Double-check after analysis. Two concurrent create requests can both pass
    # the first gate; only one is allowed to append the live project.
    with project_state_lock():
        rows = _load()
        current = _find_existing(rows, analysed_site)
        if current:
            return _reuse(current, rows)

        key = hashlib.sha1((analysis["site"]+"|"+_now()).encode()).hexdigest()[:12]
        project = {
            "id":"crowd_"+key,"site":analysis["site"],"domain":analysis["domain"],"status":"analysis_ready",
            "client_input":{"site":analysis["site"],"only_required_field":"site"},
            "niche":analysis["niche"],"niche_label":analysis["niche_label"],
            "offer_type":analysis["offer_type"],"offer_type_label":analysis["offer_type_label"],
            "plan_mode":"contract","contract_count_source":"explicit_order",
            "target_count":requested_target,"bonus_count":requested_bonus,
            "target_pages":analysis["pages"],"keywords":analysis["keywords"],"anchor_plan":analysis["anchor_plan"],
            "text_variants":analysis["text_variants"],"forum_candidates":analysis["forums"],
            "placements":[],"created_at":_now(),"updated_at":_now(),
            "guarantee_days":30,"free_only":True,
            "generated_without_openai":True,
            "delivery_rule":"клиент даёт только сайт; BORIS сам определяет всё остальное",
            "owner_action_required":False,
        }
        _apply_auto_content_approval(project)
        rows.append(project)
        _save(rows)
        prepared = prepare_placement_plan(project["id"])
        if prepared.get("content_status") == "approved":
            sync_project_drafts(project["id"])
            prepared = get_project(project["id"])
        prepared["reused_existing"] = False
        return prepared

def list_projects() -> list[dict]:
    return [
        _decorate_project(x)
        for x in sorted(_load(), key=lambda x:x.get("created_at",""), reverse=True)
    ]

def get_project(project_id: str) -> dict:
    row=next((x for x in _load() if x.get("id")==project_id),None)
    if not row:
        raise KeyError(project_id)
    return _decorate_project(row)

def _capacity_for_project(project: dict, matches: list[dict] | None = None) -> dict:
    required=_project_required_count(project)
    if matches is None:
        matches=_forum_matches(
            project.get("niche") or "business",
            limit=max(100,required*5),
            keywords=_project_keywords(project),
            offer_type=_legacy_offer_type(project),
        )
    rule_verified=[
        x for x in matches
        if (
            ("rule_status" not in x)
            or str(x.get("rule_status") or "").lower()=="allowed"
        )
    ]
    rule_pending=[
        x for x in matches
        if x not in rule_verified
    ]
    ready=[
        x for x in rule_verified
        if x.get("publication_ready")
    ]
    autonomous=[
        x for x in rule_verified
        if x.get("publication_ready")
        or (
            x.get("account_status")=="in_progress"
            and x.get("checkpoint")=="form_prepared"
        )
    ]
    bootstrap=[
        x for x in rule_verified
        if not x.get("publication_ready")
        and str(x.get("checkpoint") or "") in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
    ]
    bootstrap_platforms={
        str(x.get("platform") or "")
        for x in bootstrap
        if str(x.get("platform") or "")
    }
    bootstrap_immediate=[
        x for x in bootstrap
        if not x.get("maturity_required")
    ]
    bootstrap_immediate_platforms={
        str(x.get("platform") or "")
        for x in bootstrap_immediate
        if str(x.get("platform") or "")
    }
    warming=[
        x for x in rule_verified
        if not x.get("publication_ready")
        and (
            x.get("account_warming")
            or x.get("maturity_required")
            or str(x.get("checkpoint") or "") in marketplace.WARMING_REGISTRATION_CHECKPOINTS
        )
    ]
    warming_platforms={
        str(x.get("platform") or "")
        for x in warming
        if str(x.get("platform") or "")
    }
    reachable_after_bootstrap=[
        x for x in rule_verified
        if x.get("publication_ready")
        or (
            x.get("account_status")=="in_progress"
            and x.get("checkpoint")=="form_prepared"
        )
        or (
            str(x.get("checkpoint") or "") in marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
            and not x.get("maturity_required")
        )
    ]
    reachable_after_warmup=[
        x for x in rule_verified
        if x in reachable_after_bootstrap
        or x.get("account_warming")
        or x.get("maturity_required")
        or str(x.get("checkpoint") or "") in marketplace.WARMING_REGISTRATION_CHECKPOINTS
    ]
    return {
        "required":required,
        "eligible":len(matches),
        "rule_verified_relevant":len(rule_verified),
        "rule_pending_relevant":len(rule_pending),
        "autonomous":len(autonomous),
        "ready":len(ready),
        "bootstrap_relevant":len(bootstrap_platforms),
        "bootstrap_immediate_relevant":len(bootstrap_immediate_platforms),
        "bootstrap_surface_slots":len(bootstrap),
        "warming_relevant":len(warming_platforms),
        "service_reachable":len(reachable_after_bootstrap),
        "service_reachable_after_bootstrap":len(reachable_after_bootstrap),
        "service_reachable_after_warmup":len(reachable_after_warmup),
        "autonomous_deficit":max(0,required-len(autonomous)),
        "ready_deficit":max(0,required-len(ready)),
        "discovery_deficit_after_bootstrap":max(0,required-len(reachable_after_bootstrap)),
        "discovery_deficit_after_warmup":max(0,required-len(reachable_after_warmup)),
    }

def _operational_status(project: dict, matches: list[dict] | None = None) -> str:
    if not project_is_active(project):
        return str(project.get("status") or "content_rejected")
    capacity=_capacity_for_project(project,matches)
    if capacity["autonomous_deficit"]>0:
        return "expanding_publication_pool"
    if project.get("content_status")=="approved":
        return "approved_for_placement"
    return "placement_plan_ready"

def capacity_snapshot() -> list[dict]:
    """Live delivery capacity for non-rejected Crowd SEO projects.

    `autonomous` counts only paths BORIS can complete without an owner:
    already-ready accounts or a registration form that has already passed the
    fail-closed preflight and is stored as `form_prepared`. Unknown
    registrations, login failures, account-creation ambiguity, CAPTCHA,
    terms/identity and terminal site blocks are deliberately excluded.
    """
    out=[]
    for project in list_projects():
        if not project_is_active(project):
            continue
        cap=_capacity_for_project(project)
        out.append({
            "project":project.get("id"),"site":project.get("site"),"niche":project.get("niche") or "business",
            **cap,
        })
    return out

@_locked_project_mutation
def prepare_placement_plan(project_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    forums=list(project.get("forum_candidates") or [])
    wanted=_project_plan_count(project, forums)
    anchors=list(project.get("anchor_plan") or [])
    texts=list(project.get("text_variants") or [])
    placements=[]
    brand = project.get("domain") or urlparse(project["site"]).netloc.removeprefix("www.")
    profile = project.get("campaign_profile") or {}
    profile_url = str(profile.get("primary_url") or "").strip()
    targets = (
        [{"url":profile_url,"label":str(profile.get("brand") or project.get("niche_label","сайт"))}]
        if profile_url
        else (project.get("target_pages") or [{"url":project["site"],"label":project.get("niche_label","сайт")}])
    )
    keywords = _project_keywords(project) or [project.get("niche_label","услуги")]
    for i,forum in enumerate(forums[:wanted]):
        target = targets[i % len(targets)]
        keyword = keywords[i % len(keywords)]
        # Safe diversified profile: mostly naked URLs, some partial anchors,
        # occasional brand anchors. Client does not configure this manually.
        mod = i % 10
        if mod <= 5:
            mode, anchor = "безанкорная", target["url"]
        elif mod <= 8:
            mode, anchor = "частичный анкор", keyword
        else:
            mode, anchor = "брендовая", brand
        a={"url":target["url"],"keyword":keyword,"mode":mode,"anchor":anchor}
        t=texts[i % len(texts)] if texts else {"variant":1,"text":project["site"]}
        variant = i + 1 if (project.get("campaign_profile") or {}).get("posts_ru") else int(t.get("variant") or 1)
        if forum.get("publication_ready"):
            state="ready_for_content_approval"
        elif forum.get("account_status") in {"verification_required","in_progress"}:
            state="external_checkpoint"
        else:
            state="waiting_platform_ready"
        placements.append({
            "id":f"{project_id}_{i+1:03d}","platform":forum["platform"],"platform_name":forum["name"],
            "surface_id":forum.get("surface_id") or "default",
            "surface_name":forum.get("surface_name") or forum["name"],
            "surface_language":forum.get("surface_language") or "ru",
            "forum_url":forum["url"],
            "publication_surface_post_url":forum.get("post_url"),
            "target_url":a["url"],"keyword":a["keyword"],"link_mode":a["mode"],
            "anchor":a["anchor"],"text_variant":variant,"text":t["text"],"status":state,
            "publication_url":None,"checked_at":None,"link_present":None,"indexable":None,"rel":None,
            "http_status":None,"final_publication_url":None,
            "relevance":forum.get("relevance"),"rule_status":forum.get("rule_status"),
            "surface_evidence":forum.get("surface_evidence"),
            "iks":forum.get("iks"),
            "iks_tier":forum.get("iks_tier"),
            "iks_measured_at":forum.get("iks_measured_at"),
            "error":None,
        })
    project["placements"]=placements
    project["status"]=_operational_status(project)
    project["updated_at"]=_now()
    _save(rows)
    return project

@_locked_project_mutation
def mark_publication(project_id: str, placement_id: str, publication_url: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project: raise KeyError(project_id)
    pl=next((x for x in project.get("placements",[]) if x.get("id")==placement_id),None)
    if not pl: raise KeyError(placement_id)
    pl["publication_url"]=publication_url.strip()
    pl["published_at"]=pl.get("published_at") or _now()
    _stamp_guarantee(project,pl)
    pl["status"]="published_unverified"
    pl["updated_at"]=_now()
    project["updated_at"]=_now()
    _save(rows)
    return project

@_locked_project_mutation
def verify_placement(project_id: str, placement_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project: raise KeyError(project_id)
    pl=next((x for x in project.get("placements",[]) if x.get("id")==placement_id),None)
    if not pl: raise KeyError(placement_id)
    pub=pl.get("publication_url")
    if not pub:
        raise ValueError("publication_url missing")
    try:
        r=requests.get(pub,headers={"User-Agent":UA},timeout=TIMEOUT,allow_redirects=True)
        html=r.text
        soup=BeautifulSoup(html,"lxml")
        target=pl.get("target_url","")
        link=None
        for a in soup.find_all("a",href=True):
            raw_href = a.get("href","")
            absolute_href = urljoin(r.url, raw_href)
            anchor_text = " ".join(a.stripped_strings).strip()
            target_norm = target.rstrip("/")
            if (
                raw_href.rstrip("/") == target_norm
                or absolute_href.rstrip("/") == target_norm
                or anchor_text.rstrip("/") == target_norm
            ):
                link=a
                break
        meta=soup.find("meta",attrs={"name":re.compile("^robots$",re.I)})
        robots=(meta.get("content","") if meta else "").lower()
        pl["checked_at"]=_now()
        pl["http_status"]=int(r.status_code)
        pl["final_publication_url"]=str(r.url)
        pl["link_present"]=bool(link)
        pl["indexable"]=r.status_code==200 and "noindex" not in robots
        pl["rel"]=" ".join(link.get("rel",[])) if link else None
        pl["link_rel_type"]=(
            "nofollow"
            if link and "nofollow" in {str(x).lower() for x in (link.get("rel") or [])}
            else ("dofollow" if link else None)
        )
        pl["status"]="verified" if pl["link_present"] and pl["indexable"] else "replacement_required"
        pl["error"]=None if pl["status"]=="verified" else "link_missing_or_not_indexable"
    except Exception as exc:
        pl["checked_at"]=_now()
        pl["http_status"]=None
        pl["final_publication_url"]=None
        pl["status"]="replacement_required"
        pl["error"]=f"{type(exc).__name__}: {exc}"
    project["updated_at"]=_now()
    _save(rows)
    return pl

def report(project_id: str) -> dict:
    p=get_project(project_id)
    placements=p.get("placements") or []
    active=[x for x in placements if _placement_is_active(x)]
    published_rows=[x for x in active if x.get("publication_url")]
    verified_rows=[x for x in active if x.get("status")=="verified"]
    indexable_rows=[x for x in verified_rows if x.get("indexable") is True]
    dofollow_rows=[x for x in verified_rows if x.get("link_rel_type")=="dofollow"]
    nofollow_rows=[x for x in verified_rows if x.get("link_rel_type")=="nofollow"]
    replacement_history_rows=[x for x in placements if x.get("status") in {"replacement_required","platform_replacement_required"}]
    replacement_rows=[x for x in replacement_history_rows if not x.get("replacement_placement_id")]
    completed_replacements=[x for x in replacement_history_rows if x.get("replacement_placement_id")]
    guarantee_expired_rows=[x for x in placements if x.get("status")=="guarantee_expired"]
    iks_known=[int(x.get("iks")) for x in active if isinstance(x.get("iks"), int)]
    iks_tiers=Counter(str(x.get("iks_tier") or "unknown") for x in active)
    iks_measured=max(
        [str(x.get("iks_measured_at") or "") for x in active if x.get("iks_measured_at")],
        default=None,
    )
    return {
        "project_id":p["id"],"site":p["site"],"niche":p["niche_label"],
        "offer_type":p.get("offer_type"),"offer_type_label":p.get("offer_type_label"),
        "target_count":p.get("target_count"),"bonus_count":p.get("bonus_count"),
        "published":len(published_rows),
        "verified":len(verified_rows),
        "indexable":len(indexable_rows),
        "dofollow":len(dofollow_rows),
        "nofollow":len(nofollow_rows),
        "replacement_required":len(replacement_rows),
        "replacement_history":len(replacement_history_rows),
        "replacements_completed_or_scheduled":len(completed_replacements),
        "quality":{
            "iks_known":len(iks_known),
            "iks_unknown":max(0,len(active)-len(iks_known)),
            "iks_average":round(sum(iks_known)/len(iks_known),1) if iks_known else None,
            "iks_max":max(iks_known) if iks_known else None,
            "high":int(iks_tiers.get("high",0)),
            "medium":int(iks_tiers.get("medium",0)),
            "low":int(iks_tiers.get("low",0)),
            "unknown":int(iks_tiers.get("unknown",0)),
            "measured_at":iks_measured,
            "source":"Яндекс Вебмастер · информация о сайте",
        },
        "direct_urls":[x.get("publication_url") for x in published_rows if x.get("publication_url")],
        "placements":placements,
        "client_required_input":["сайт"],
        "client_required_fields_count":1,
        "guarantee_days":p.get("guarantee_days",30),
        "seo_policy":{
            "position_guarantee":False,
            "traffic_guarantee":False,
            "delivery_guarantee":"согласованный объём проверенных размещений; пропавшие ссылки заменяются в гарантийный период",
            "fake_metrics":False,
        },
    }

def _render_link(target_url: str, anchor: str, mode: str) -> str:
    if mode == "безанкорная":
        return target_url
    # Most forum engines accept BBCode. The publication verifier records the
    # actual resulting href/rel; if a platform strips markup BORIS can replace it.
    return f"[url={target_url}]{anchor}[/url]"

def _surface_content_angle(surface_name: str, project: dict | None = None) -> tuple[str, str]:
    """Neutral client-safe angle for goods, services and mixed sites.

    CROWD_SEO_MULTI_CLIENT_SURFACE_COPY_V1
    The previous implementation talked about BORIS/automation even for a future
    furniture/product client.  Surface wording must describe the client's niche,
    not BORIS' own offer.
    """
    low = str(surface_name or "").lower().replace("ё", "е")
    project=project or {}
    niche=str(project.get("niche_label") or "предложение")
    if any(x in low for x in ["smm", "соцсет", "social"]):
        return (
            f"{niche}: обсуждение и рекомендации",
            "В профильном разделе полезнее давать конкретную страницу по теме и объяснять, что на ней можно сравнить, без рекламных обещаний и лишнего давления.",
        )
    if any(x in low for x in ["контекст", "директ", "реклам", "маркетинг"]):
        return (
            f"{niche}: вариант для сравнения",
            "Если человек уже выбирает варианты по этой теме, полезно сразу дать страницу с условиями и фактами, чтобы он мог сравнить предложение самостоятельно.",
        )
    if any(x in low for x in ["seo", "линк", "крауд"]):
        return (
            f"{niche}: полезная ссылка по теме",
            "В тематическом обсуждении ссылка должна дополнять ответ: вести на релевантную страницу и давать человеку больше конкретики по его вопросу.",
        )
    if any(x in low for x in ["програм", "разработ", "it", "бот"]):
        return (
            f"{niche}: профильное предложение",
            "Для профильной темы лучше сразу показать конкретную страницу, условия и ограничения, чтобы читатель мог оценить соответствие своей задаче.",
        )
    return (
        f"{niche}: как сравнить варианты",
        "В тематическом обсуждении полезно дать конкретную страницу по вопросу и кратко объяснить, что на ней можно проверить перед выбором.",
    )

def _render_placement_text(project: dict, pl: dict, variant: int) -> tuple[str,str]:
    offer_type=_legacy_offer_type(project)
    language=str(pl.get("surface_language") or "ru").lower()
    profile=project.get("campaign_profile") or {}

    # CAMPAIGN_PROFILE_COPY_V1
    # An explicitly approved live-test/project profile may carry richer,
    # transparent provider copy. It still uses one destination URL per post and
    # never impersonates an independent customer/reviewer.
    profile_posts=list(profile.get("posts_en" if language=="en" else "posts_ru") or [])
    if profile_posts:
        # CAMPAIGN_PROFILE_KEYWORD_ROUTING_V2
        # Keep one search intent per placement. Prefer a post whose explicit
        # match_keywords intersect the placement keyword/anchor/surface text;
        # only fall back to the historical variant rotation when no cluster
        # matches. This prevents a CRM placement from receiving Avito/SEO copy.
        routing_haystack=" ".join(str(pl.get(k) or "") for k in (
            "keyword","anchor","surface_name","platform_name","title"
        )).lower()
        post=None
        best_score=0
        best_candidates=[]
        explicit_cluster=str(pl.get("keyword_cluster") or "").strip().lower()
        for candidate in profile_posts:
            candidate_cluster=str(candidate.get("cluster") or "").strip().lower()
            terms=[str(x).strip().lower() for x in (candidate.get("match_keywords") or []) if str(x).strip()]
            # Explicit product cluster is authoritative. Text matching remains
            # only as a compatibility fallback for legacy placements.
            score=(100 if explicit_cluster and candidate_cluster == explicit_cluster else 0)
            score+=sum(1 for term in terms if term in routing_haystack)
            if score > best_score:
                best_score=score
                best_candidates=[candidate]
            elif score > 0 and score == best_score:
                best_candidates.append(candidate)
        if best_candidates:
            post=best_candidates[(max(1,int(variant))-1) % len(best_candidates)]
        if post is None:
            post=profile_posts[(max(1,int(variant))-1) % len(profile_posts)]
        brand=str(profile.get("brand") or project.get("domain") or "BORIS")
        url=str(pl.get("target_url") or project.get("site") or "")
        title=str(post.get("title") or brand).replace("{brand}",brand).replace("{url}",url)
        text=str(post.get("text") or "").replace("{brand}",brand).replace("{url}",url)
        exact_keyword=str(pl.get("keyword") or "").strip()
        if exact_keyword and exact_keyword.lower() not in text.lower():
            label="Keyword phrase" if language=="en" else "Ключевая фраза"
            text=f"{text}\n\n{label}: {exact_keyword}."
        return _clean_text(title)[:180], _clean_text(text)[:1800]

    # INTERNATIONAL_SURFACE_COPY_V1
    # Never publish Russian copy or Cyrillic anchors into a verified English
    # marketplace. Use transparent provider wording rather than pretending to
    # be an independent recommendation. Naked URLs also avoid mixed-language
    # anchors and work across forum markup engines.
    if language == "en":
        link=str(pl["target_url"])
        brand = project.get("domain") or urlparse(project["site"]).netloc.removeprefix("www.")
        if offer_type == "goods":
            heading = "Product offer and specifications"
            templates = [
                (
                    f"{heading} — {brand}",
                    f"Product details are available here: {link}. The page describes the offer, specifications and contact options. If you are comparing suppliers, review the product scope, delivery terms and commercial conditions before contacting the team."
                ),
                (
                    f"Product information — {brand}",
                    f"Here is the product page: {link}. It can be used to review the specifications, offer details and contact information before making a supplier comparison."
                ),
                (
                    f"Supplier offer — {brand}",
                    f"We are sharing the relevant product page for this marketplace section: {link}. Please check the specifications, delivery conditions and commercial terms to decide whether the offer fits your requirements."
                ),
            ]
        else:
            heading = "Service offer and project details"
            templates = [
                (
                    f"{heading} — {brand}",
                    f"We provide the service described here: {link}. The page covers the scope, implementation details and contact options. If you are comparing providers, review the deliverables, integrations, timelines and commercial terms before contacting the team."
                ),
                (
                    f"Business service and implementation details — {brand}",
                    f"Here is our service page: {link}. It explains what the team provides and how the implementation is structured. It may be useful when comparing providers, scope, integrations and operating terms."
                ),
                (
                    f"Service provider — {brand}",
                    f"We are sharing the relevant service page for this marketplace section: {link}. Please review the scope, implementation details and contact information to decide whether it fits your project."
                ),
            ]
        title,text = templates[(max(1,variant)-1) % len(templates)]
        return title[:180], _clean_text(text)[:500]

    link = _render_link(pl["target_url"], pl["anchor"], pl["link_mode"])
    noun, compare_short, compare_long=_offer_copy_terms(offer_type)
    kw = pl.get("keyword") or project.get("niche_label") or noun
    niche = project.get("niche_label") or OFFER_TYPE_RU.get(offer_type,"предложение")
    surface_name = pl.get("surface_name") or pl.get("platform_name") or ""
    angle_title, angle_intro = _surface_content_angle(surface_name, project)
    page = next((x for x in project.get("target_pages",[]) if x.get("url")==pl.get("target_url")), None) or {}
    label = (page.get("label") or kw)[:100]
    templates = [
        (
            f"{angle_title}: {label}",
            f"{angle_intro} Если сейчас сравниваете варианты по теме «{kw}», полезно заранее проверить {compare_short}. "
            f"Нашёл подробную страницу по теме: {link}. Лучше сравнивать не только цену, но и {compare_long}."
        ),
        (
            f"{angle_title}: {kw}",
            f"{angle_intro} По теме «{kw}» пригодилась отдельная страница с описанием: {link}. "
            f"Информация может быть полезна тем, кто выбирает в нише «{niche}» и хочет до обращения понять {compare_short}."
        ),
        (
            f"{angle_title} — как выбрать по теме «{kw}»",
            f"{angle_intro} При выборе по теме «{kw}» я бы сначала проверил {compare_short}. "
            f"Как один из вариантов для сравнения можно посмотреть {link}. На странице есть описание предложения без необходимости собирать информацию по частям."
        ),
    ]
    title,text = templates[(max(1,variant)-1) % len(templates)]
    return title[:180], _clean_text(text)[:500]

@_locked_project_mutation
def sync_project_drafts(project_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    approved = project.get("content_status") == "approved"
    new_drafts=[]
    for pl in project.get("placements",[]):
        if pl.get("status") not in {
            "ready_for_content_approval",
            "ready_to_publish",
            "external_checkpoint",
            "waiting_platform_ready",
            "published_unverified",
            "verified",
        }:
            continue
        draft_id = pl.get("marketplace_draft_id") or f"crowdseo_{project_id}_{pl['id'].split('_')[-1]}"
        title,text = _render_placement_text(project,pl,int(pl.get("text_variant") or 1))
        draft={
            "id":draft_id,
            "platform":pl["platform"],
            "platform_name":pl["platform_name"],
            "topic":"client_crowd_seo",
            "title":title,
            "text":text,
            "site_url":project["site"],
            "target_url":pl["target_url"],
            "status":"approved" if approved else "needs_owner_approval",
            "generated_without_openai":True,
            "variant":pl.get("text_variant",1),
            "crowd_project_id":project_id,
            "crowd_placement_id":pl["id"],
            "publication_surface_id":pl.get("surface_id") or "default",
            "publication_surface_name":pl.get("surface_name") or pl.get("platform_name"),
            "publication_surface_language":pl.get("surface_language") or "ru",
            "publication_surface_url":pl.get("forum_url"),
            "publication_surface_post_url":pl.get("publication_surface_post_url"),
            "link_mode":pl["link_mode"],
            "anchor":pl["anchor"],
            "keyword":pl["keyword"],
        }
        new_drafts.append(draft)
        pl["marketplace_draft_id"]=draft_id
        if approved and pl.get("status")=="ready_for_content_approval":
            pl["status"]="ready_to_publish"

    # CROWD_DRAFT_LIFECYCLE_V1:
    # A live plan is a snapshot, not an ever-growing union of every historical
    # replacement. Old unpublished drafts must become non-publishable as soon
    # as their placement leaves the active plan. Posted drafts are immutable
    # evidence and are deliberately preserved.
    active_draft_ids={str(x.get("id") or "") for x in new_drafts}
    stale_rejected=0
    for old in marketplace.list_drafts():
        if (
            old.get("crowd_project_id")==project_id
            and str(old.get("id") or "") not in active_draft_ids
            and old.get("status") not in {"posted","rejected"}
        ):
            marketplace.set_draft_status(str(old.get("id")), "rejected")
            stale_rejected+=1

    marketplace.save_drafts(new_drafts)
    project["updated_at"]=_now()
    _save(rows)
    return {
        "project_id":project_id,
        "drafts":len(new_drafts),
        "approved":approved,
        "stale_rejected":stale_rejected,
    }

@_locked_project_mutation
def decide_project(project_id: str, decision: str) -> dict:
    if decision not in {"approved","rejected"}:
        raise ValueError("invalid decision")
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    project["content_status"]=decision
    project["updated_at"]=_now()
    if decision=="rejected":
        project["status"]="content_rejected"
        _save(rows)
        return project
    project["status"]=_operational_status(project)
    _save(rows)
    sync_project_drafts(project_id)
    return get_project(project_id)

@_locked_project_mutation
def sync_publications(project_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    drafts={x.get("id"):x for x in marketplace.list_drafts()}
    changed=0
    for pl in project.get("placements",[]):
        d=drafts.get(pl.get("marketplace_draft_id"))
        if not d:
            continue
        if d.get("status")=="posted" and d.get("publication_url") and pl.get("publication_url")!=d.get("publication_url"):
            pl["publication_url"]=d["publication_url"]
            pl["published_at"]=pl.get("published_at") or d.get("posted_at") or _now()
            _stamp_guarantee(project,pl)
            pl["status"]="published_unverified"
            pl["updated_at"]=_now()
            changed+=1
    if changed:
        project["updated_at"]=_now(); _save(rows)
    return {"project_id":project_id,"changed":changed}

@_locked_project_mutation
def schedule_replacements(project_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    placements=list(project.get("placements") or [])
    now=datetime.now(timezone.utc)
    guarantee_expired=0
    for old in project.get("placements",[]):
        if old.get("status")!="replacement_required" or not old.get("publication_url"):
            continue
        _stamp_guarantee(project,old)
        if _guarantee_expired(project,old,now=now):
            old["status"]="guarantee_expired"
            old["error"]="guarantee_period_expired"
            old["guarantee_expired_at"]=now.isoformat()
            guarantee_expired+=1
    # A slot blocks reuse only while it is active or after it was actually
    # published. Failed pre-publication history must not poison the pool
    # forever if that surface later becomes eligible again.
    used={
        _slot_key(x)
        for x in placements
        if _placement_is_active(x) or bool(x.get("publication_url"))
    }
    # Replacement candidates are recalculated from the current live eligibility
    # set so a stale/blocked forum kept in the project's historical pool can
    # never be selected as the next self-heal target.
    candidates=[
        x for x in _forum_matches(
            project.get("niche") or "business",
            limit=80,
            keywords=_project_keywords(project),
            offer_type=_legacy_offer_type(project),
        )
        if _slot_key(x) not in used
    ]
    created=0
    for old in list(project.get("placements",[])):
        if old.get("status") not in {"replacement_required","platform_replacement_required"} or old.get("replacement_placement_id"):
            continue
        if not candidates:
            break
        forum=candidates.pop(0)
        rid=f"{project_id}_r{len(project['placements'])+1:03d}"
        state="ready_for_content_approval" if forum.get("publication_ready") else ("external_checkpoint" if forum.get("account_status") in {"verification_required","in_progress"} else "waiting_platform_ready")
        repl={
            "id":rid,"platform":forum["platform"],"platform_name":forum["name"],
            "surface_id":forum.get("surface_id") or "default",
            "surface_name":forum.get("surface_name") or forum["name"],
            "surface_language":forum.get("surface_language") or "ru",
            "forum_url":forum["url"],
            "publication_surface_post_url":forum.get("post_url"),
            "target_url":old["target_url"],"keyword":old["keyword"],"link_mode":old["link_mode"],"anchor":old["anchor"],
            "text_variant":old.get("text_variant",1),"text":old.get("text",""),"status":state,
            "publication_url":None,"checked_at":None,"link_present":None,"indexable":None,"rel":None,
            "http_status":None,"final_publication_url":None,
            "relevance":forum.get("relevance"),"rule_status":forum.get("rule_status"),
            "surface_evidence":forum.get("surface_evidence"),
            "iks":forum.get("iks"),
            "iks_tier":forum.get("iks_tier"),
            "iks_measured_at":forum.get("iks_measured_at"),
            "error":None,
            "replacement_for":old["id"],
        }
        project["placements"].append(repl)
        old["replacement_placement_id"]=rid
        used.add(_slot_key(forum))
        created+=1
    # If the original project started with fewer than the contractual number
    # of live slots, newly discovered eligible surfaces must fill that deficit
    # even when there is no old failed placement to replace.
    topups=0
    wanted=_project_plan_count(project, project.get("forum_candidates") or [])
    active_count=(
        sum(1 for x in project.get("placements",[]) if _placement_is_active(x))
        + sum(
            1 for x in project.get("placements",[])
            if x.get("status")=="guarantee_expired" and x.get("publication_url")
        )
    )
    profile=project.get("campaign_profile") or {}
    profile_url=str(profile.get("primary_url") or "").strip()
    targets=(
        [{"url":profile_url,"label":str(profile.get("brand") or project.get("niche_label","сайт"))}]
        if profile_url
        else (project.get("target_pages") or [{"url":project["site"],"label":project.get("niche_label","сайт")}])
    )
    keywords=_project_keywords(project) or [project.get("niche_label","услуги")]
    texts=project.get("text_variants") or [{"variant":1,"text":project["site"]}]
    brand=project.get("domain") or urlparse(project["site"]).netloc.removeprefix("www.")
    while active_count < wanted and candidates:
        forum=candidates.pop(0)
        i=active_count
        target=targets[i % len(targets)]
        keyword=keywords[i % len(keywords)]
        mod=i % 10
        if mod <= 5:
            mode,anchor="безанкорная",target["url"]
        elif mod <= 8:
            mode,anchor="частичный анкор",keyword
        else:
            mode,anchor="брендовая",brand
        t=texts[i % len(texts)]
        rid=f"{project_id}_a{len(project['placements'])+1:03d}"
        state="ready_for_content_approval" if forum.get("publication_ready") else (
            "external_checkpoint"
            if forum.get("account_status") in {"verification_required","in_progress"}
            else "waiting_platform_ready"
        )
        project["placements"].append({
            "id":rid,
            "platform":forum["platform"],
            "platform_name":forum["name"],
            "surface_id":forum.get("surface_id") or "default",
            "surface_name":forum.get("surface_name") or forum["name"],
            "surface_language":forum.get("surface_language") or "ru",
            "forum_url":forum["url"],
            "publication_surface_post_url":forum.get("post_url"),
            "target_url":target["url"],
            "keyword":keyword,
            "link_mode":mode,
            "anchor":anchor,
            "text_variant":t.get("variant",1),
            "text":t.get("text",""),
            "status":state,
            "publication_url":None,
            "checked_at":None,
            "link_present":None,
            "indexable":None,
            "rel":None,
            "http_status":None,
            "final_publication_url":None,
            "relevance":forum.get("relevance"),
            "rule_status":forum.get("rule_status"),
            "surface_evidence":forum.get("surface_evidence"),
            "iks":forum.get("iks"),
            "iks_tier":forum.get("iks_tier"),
            "iks_measured_at":forum.get("iks_measured_at"),
            "error":None,
            "replacement_for":None,
        })
        used.add(_slot_key(forum))
        active_count+=1
        created+=1
        topups+=1
    if created or guarantee_expired:
        project["updated_at"]=_now(); _save(rows)
        if project.get("content_status")=="approved" and created:
            sync_project_drafts(project_id)
    return {
        "project_id":project_id,
        "created":created,
        "topups":topups,
        "guarantee_expired":guarantee_expired,
        "placement_summary":_placement_summary(project),
    }

@_locked_project_mutation
def refresh_project_forums(project_id: str) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project:
        raise KeyError(project_id)
    fresh=_forum_matches(
        project.get("niche") or "business",
        limit=80,
        keywords=_project_keywords(project),
        offer_type=_legacy_offer_type(project),
    )
    # Candidate pool is a live snapshot, not an audit log. Historical
    # platforms stay in placements/replacement chains only. Keeping stale
    # candidates here made the apparent plan grow forever.
    # _forum_matches already applies the canonical priority including
    # readiness, warmup cost, relevance and IKS. Preserve that exact order so
    # the UI, onboarding worker and placement plan cannot disagree.
    project["forum_candidates"]=list(fresh)
    current={_slot_key(x):x for x in fresh}
    # PREPUBLICATION_PRIORITY_ALIGNMENT_V1:
    # Before anything is published, the active delivery plan must follow the
    # same top-N live priority set used by onboarding. A still-eligible but slow
    # reserve forum must not occupy a contractual slot if a faster eligible
    # forum has moved ahead of it. Published/verified history is never rotated.
    planned_slots={
        _slot_key(x)
        for x in fresh[:_project_plan_count(project, fresh)]
    }
    changed=0
    for pl in project.get("placements",[]):
        if pl.get("status") in {"verified","replacement_required","published_unverified","publication_pending_verification"}:
            continue
        if pl.get("status") == "platform_replacement_required" and pl.get("replacement_placement_id"):
            # This old slot already has an active replacement and remains only
            # as delivery history; never resurrect it in parallel.
            continue
        slot=_slot_key(pl)
        f=current.get(slot)
        if not f:
            # The forum dropped out of the current eligible set (for example
            # registration became blocked or fresh rules no longer allow it).
            # Before publication this is not a client failure: rotate the slot.
            if not pl.get("publication_url"):
                pl["status"]="platform_replacement_required"
                pl["error"]="platform_no_longer_eligible"
                changed+=1
            continue
        if (
            not pl.get("publication_url")
            and _placement_is_active(pl)
            and slot not in planned_slots
        ):
            pl["status"]="platform_replacement_required"
            pl["error"]="outside_current_priority_plan"
            changed+=1
            continue
        pl["surface_id"]=f.get("surface_id") or "default"
        pl["surface_name"]=f.get("surface_name") or f.get("name")
        pl["surface_language"]=f.get("surface_language") or "ru"
        pl["forum_url"]=f.get("url") or pl.get("forum_url")
        pl["publication_surface_post_url"]=f.get("post_url")
        # Keep quality metrics live for existing client projects. The weekly
        # IKS refresh updates the shared quality registry; this sync propagates
        # the fresh value into the report without rebuilding or losing URLs.
        pl["iks"]=f.get("iks")
        pl["iks_tier"]=f.get("iks_tier")
        pl["iks_measured_at"]=f.get("iks_measured_at")
        if f.get("publication_ready"):
            new_status="ready_to_publish" if project.get("content_status")=="approved" else "ready_for_content_approval"
        elif f.get("account_status") in {"verification_required","in_progress"}:
            new_status="external_checkpoint"
        else:
            new_status="waiting_platform_ready"
        if pl.get("status")!=new_status:
            pl["status"]=new_status
            pl["error"]=None
            changed+=1
    next_status=_operational_status(project,fresh)
    status_changed=project.get("status")!=next_status
    if status_changed:
        project["status"]=next_status
    if changed or fresh or status_changed:
        project["updated_at"]=_now(); _save(rows)
    if project.get("content_status")=="approved":
        sync_project_drafts(project_id)
    return {"project_id":project_id,"changed":changed,"forum_candidates":len(project.get("forum_candidates",[]))}

@_locked_project_mutation
def record_publish_attempt(project_id: str, placement_id: str, result: dict) -> dict:
    rows=_load(); project=next((x for x in rows if x.get("id")==project_id),None)
    if not project: raise KeyError(project_id)
    pl=next((x for x in project.get("placements",[]) if x.get("id")==placement_id),None)
    if not pl: raise KeyError(placement_id)
    pl["last_publish_attempt_at"]=_now()
    pl["last_publish_result"]={
        "publish_clicked":bool(result.get("publish_clicked")),
        "publication_verified":bool(result.get("publication_verified")),
        "checkpoint":result.get("checkpoint"),
        "url":result.get("url"),
        "filled":bool(result.get("filled")),
        "next_action":result.get("next_action"),
    }
    if result.get("publication_verified"):
        pl["status"]="published_unverified"
    elif result.get("publish_clicked"):
        pl["status"]="publication_pending_verification"
        pl["error"]="publication_url_verification_required"
    elif result.get("checkpoint") == "foreign_thread_reply_forbidden":
        pl["status"]="platform_replacement_required"
        pl["error"]="foreign_thread_reply_forbidden"
    elif result.get("checkpoint"):
        pl["status"]="external_checkpoint"
        pl["error"]=str(result.get("checkpoint"))
    elif not result.get("filled"):
        pl["status"]="adapter_required"
        pl["error"]="post_form_not_found"
    else:
        pl["status"]="ready_to_publish"
    project["updated_at"]=_now(); _save(rows)
    sync_publications(project_id)
    return pl
