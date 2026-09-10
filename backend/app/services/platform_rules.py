from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.services import service_marketplace as marketplace

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "service_marketplaces" / "rules"
STATE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (compatible; BORIS-RuleAudit/1.0; +https://boris-ai.pro)"
BROWSER_USER_AGENT = "Mozilla/5.0"
MAX_RULE_AGE_DAYS = 7

# Known rule/help URLs where public documentation is discoverable.
RULE_URLS = {
    "stroy_forum": ["https://stroy-forum.ru/forums/uslugi-organizacii-i-ispolnoteley/", "https://stroy-forum.ru/forums/uslugi-svyazannye-so-stroitelstvom/"],
    "ssa": ["https://forum.ssa.ru/"],
    "towerbuild": ["https://forum.towerbuild.ru/categories"],
    "forum_baza_1c": ["https://forum-baza.ru/"],
    "bitrix_dev": [
        "https://dev.1c-bitrix.ru/community/forums/forum14/",
        "https://dev.1c-bitrix.ru/community/forums/rules/",
    ],
    "finforum_services": [
        "https://finforum.pro/categories/doska-objavlenij.88/",
        "https://finforum.pro/help/terms",
    ],
    "homeidea": ["https://homeidea.ru/"],
    "oborot": ["https://oborot.ru/p/community-rules-i31029.html"],
    "mastergrad": [
        "https://mastergrad.com/faq/",
        "https://mastergrad.com/forums/baraholka-chastnye-obyavleniya/",
    ],
    "searchengines_guru": [
        "https://searchengines.guru/ru/forum/840364",
        "https://searchengines.guru/ru/forum/1056890",
    ],
    "disc_affiliate_forum": [
        "https://affiliate.forum/forums/uslugi.28/",
        "https://affiliate.forum/threads/pravila-oformleniya-temy-po-uslugam.29/",
    ],
    "se_guru_services": [
        "https://se.guru/forumdisplay.php?f=21",
        "https://se.guru/rules.php",
    ],
    "tcfs": [
        "https://tcfs.ru/forums/",
    ],
    "kolsar_auto": [
        "https://www.kolsar.info/forum/viewforum.php?f=88",
        "https://www.kolsar.info/forum/app.php/rules",
    ],
    "vashdom_forum": [
        "https://forum.vashdom.ru/forums/uslugi-dlja-vashego-doma.147/",
        "https://forum.vashdom.ru/threads/pravila-razmeschenija-objavlenij.51526/",
    ],
    "disc_forum_amit_ru": [
        "https://forum-amit.ru/index.php?board=6.0",
        "https://forum-amit.ru/index.php?action=register",
    ],
    "sbup_seo_forum": [
        "https://www.sbup.com/seo-forum/rabota_dlya_optimizatora_i_vebmastera/",
        "https://www.sbup.com/seo-forum/rabota_dlya_optimizatora_i_vebmastera/pravila_razdela_rabota_dlya_optimizatora_i_vebmastera/",
        "https://www.sbup.com/seo-forum/o_saite_i_forume/pravila_foruma/",
    ],
    "webmastersun_marketplace": [
        "https://www.webmastersun.com/forums/48-services/",
        "https://www.webmastersun.com/threads/1414-webmaster-sun-marketplace-rules/",
        "https://www.webmastersun.com/threads/296-webmaster-sun-forum-rules/",
    ],
    "digitalpoint_services": [
        "https://forums.digitalpoint.com/forums/services.60/",
        "https://www.digitalpoint.com/help/established",
        "https://forums.digitalpoint.com/threads/allowed-or-not-quick-reference.1112065/",
    ],
    "namepros_promotional": [
        "https://www.namepros.com/forums/promotional.15/",
        "https://www.namepros.com/threads/official-rules-of-namepros.848752/",
        "https://www.namepros.com/threads/marketing-faq.848726/",
    ],
    "freehostforum_webdev": [
        "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace/web-development-offers-and-requests",
        "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace",
    ],
    "htmlforums_programming": [
        "https://htmlforums.net/forums/programming-development.25/",
        "https://htmlforums.net/threads/htmlforums-net-forum-rules.5209/",
        "https://htmlforums.net/help/terms/",
    ],
    "forum_promotion_employment": [
        "https://help.forumpromotion.net/docs/employment-zone",
        "https://help.forumpromotion.net/docs/getting-started/terms",
        "https://forumpromotion.net/forums/seeking-employment.67/",
    ],
    # Dynamic-discovery candidates: audit the public rules, not only the
    # marketplace/category landing page. This prevents repeated false goods
    # candidates when the section exists but commercial self-promotion or
    # mandatory outbound links are restricted.
    "disc_mastergrad_com": [
        "https://mastergrad.com/faq/",
        "https://mastergrad.com/forums/baraholka-chastnye-obyavleniya/",
    ],
    "disc_toysnbricks_com": [
        "https://www.toysnbricks.com/terms-conditions/",
        "https://toysnbricks.com/forum/index.php?/forum/31-lego-classifieds-marketplace-ads-beta/",
    ],
}

POSITIVE = [
    r"представить свои услуги",
    r"услуги.{0,160}исполнител",
    r"предлагать услуги",
    r"предложения об услугах",
    r"продажа и покупка различных услуг",
    r"биржа услуг.{0,100}предложение и поиск услуг",
    r"какие конкретно услуги предлагаете",
    r"объявления об оказании различных услуг",
    r"предложения услуг по аренде спецтехники",
    r"автоуслуги.{0,160}услуги по ремонту",
    r"услуги для .{0,30}дома.{0,180}услуги профессиональных",
    r"строительство домов.{0,120}строительные услуги",
    r"бесплатно разместить.{0,160}услуг",
    r"раздел для любой рекламы",
    r"публиковать объявления.{0,180}услуг",
    r"реклама товаров и услуг",
    r"раздел.{0,100}(?:работа|услуги).{0,100}(?:поиск|размещен|предложен)",
    r"предложени.{0,80}услуг",
    r"услуги.{0,80}(?:поиск|предложение)",
    r"рекламн(?:ый|ая|ое).{0,80}раздел.{0,80}услуг",
    r"рекламн(?:ый|ая|ое).{0,80}раздел.{0,120}(?:работа|объявления)",
    r"услуги профессиональных мастеров и организаций",
    r"услуги для .{0,30}дома.{0,180}услуги профессиональных",
    # English webmaster/community marketplaces.
    r"\bdo you want to offer services\b",
    r"\bif you want to offer your web services\b",
    r"\bservices.{0,100}(?:offer|offers|providers|looking for)\b",
    r"\bmarketplace.{0,180}(?:buy|sell|trade).{0,180}services\b",
    r"\bpromotion\s*&\s*services\b",
    r"\bif you are looking for \(or offering\) services\b",
    r"\bads can be posted for free in the promotional section\b",
    r"\bthe place to buy, sell, and promote.{0,160}(?:products|goods|services)\b",
    r"\bpromotional section may be used to advertise.{0,160}(?:products|goods|services)\b",
    r"\byou can create threads in the buy, sell or trade area\b",
    r"\byou can post links \(links are nofollow\)\b",
]
PAID_ONLY = [
    r"только оплаченных рекламных тем",
    r"рекламн(?:ая|ое|ый|ые).{0,120}платн",
    r"размещение.{0,120}платн",
    r"реклама.{0,120}через администратор",
    r"по вопросам рекламы.{0,120}администратор",
    r"стоимость размещения",
    r"платн.{0,60}услуг.{0,220}меньше\s+100\s+сообщен.{0,260}(?:1000|тысяч).{0,60}руб",
    r"меньше\s+100\s+сообщен.{0,260}(?:одна|1)\s+тем.{0,80}(?:1000|тысяч).{0,60}руб",
    r"\bdesignated for premium and corporate members\b",
    r"\bpremium (?:or|and) corporate members\b",
    r"\bpaid advertising\b",
    r"\badvertis(?:ing|e).{0,80}(?:fee|paid|pricing|rates)\b",
]
PROHIBIT_ADS = [
    r"реклама.{0,100}не допуска",
    r"распространени.{0,100}ссылок.{0,120}реклам.{0,120}не допуска",
    r"ссылок и любой другой рекламы.{0,120}не допуска",
    r"реклама.{0,100}запрещ",
    r"рекламн(?:ые|ая|ый|ое).{0,80}(?:объявлени|сообщени|материал).{0,120}запрещ",
    r"запрещено.{0,100}реклам",
    r"рекламировать.{0,140}(?:товар|услуг)",
    r"рекламировать.{0,220}без специального разрешения",
    r"любые товары и услуги.{0,160}без специального разрешения",
    r"нельзя.{0,100}реклам",
    # English community rules. Keep these specific: generic words like
    # 'advertising' occur on legitimate marketplace pages too.
    r"\bdo not register for the purpose of advertising\b",
    r"\bplease no advertising/self[- ]promotion\b",
    r"\badvertisements/self[- ]promotion.{0,120}(?:will not be|not).{0,80}(?:published|approved|allowed)\b",
    r"\bcommercial listings are not permitted\b",
    r"\bonly supporting vendors can sell on a for-profit level\b",
]
PROHIBIT_LINKS = [
    r"ссылк.{0,100}запрещ",
    r"запрещено.{0,100}ссылк",
    r"контакт.{0,100}запрещ",
    r"телефон.{0,100}запрещ",
    r"ссылк.{0,140}(?:разрешено|разрешены|доступны).{0,100}не сразу",
    r"ссылк.{0,140}(?:только\s+)?\bпосле\b.{0,100}(?:сообщен|регистрац|дн|активност)",
    r"размещение ссылок.{0,140}не сразу",
    r"\bnot allowed to add any links\b",
    r"\boutbound links.{0,260}promote products/services.{0,160}removed\b",
    r"\bself[- ]promotion.{0,180}paid sections\b",
    r"\blinks?.{0,180}primary aim.{0,120}promote products/services.{0,160}removed\b",
]
REPLY_ONLY = [
    r"только в ответ",
    r"только по запрос",
    r"рекомендац.{0,120}допуска.{0,120}личн",
]
FREQUENCY = [
    r"не чаще.{0,40}(?:раз|1).{0,20}(?:сут|день|недел)",
    r"(?:раз|1).{0,20}(?:в сутки|в день|в неделю)",
]

REQUIREMENT_PATTERNS = {
    "city_in_title": [
        r"указывайте.{0,40}город.{0,60}названи.{0,30}тем",
        r"город.{0,60}в названи.{0,30}тем",
    ],
    "unique_text": [
        r"текст.{0,40}должен быть уникал",
        r"не копируйте.{0,80}объявлен",
    ],
    "contact_person_and_phone": [
        r"контактн.{0,30}лиц.{0,40}телефон",
    ],
    "website_or_social_link": [
        r"ссылк.{0,40}(?:на )?(?:сайт|страничк).{0,40}социаль",
        r"ссылк.{0,30}на сайт",
    ],
    "no_duplicate_topics": [
        r"не дублируйте тем",
        r"дублировани.{0,40}тем.{0,40}запрещ",
    ],
    "presentation_not_ad": [
        r"не нужно писать рекламные объявления.{0,120}презентац",
        r"это ваша презентац",
    ],
    "bump_daily_max": [
        r"не чаще одного раза в сутки",
        r"не чаще.{0,20}раз.{0,20}сутк",
    ],
}

def _extract_requirements(text_value: str) -> list[str]:
    low = text_value.lower()
    out = []
    for key, patterns in REQUIREMENT_PATTERNS.items():
        if any(re.search(p, low, re.I | re.S) for p in patterns):
            out.append(key)
    return out

def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)

def _page_links(html: str, base_url: str) -> list[str]:
    """Preserve real href evidence without following extra links."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for tag in soup.find_all("a", href=True):
        value = urljoin(base_url, str(tag.get("href") or "").strip())
        if not value.startswith(("http://", "https://")) or value in seen:
            continue
        seen.add(value)
        links.append(value)
        if len(links) >= 300:
            break
    return links

def _matches(text_value: str, patterns: list[str]) -> list[str]:
    out = []
    low = text_value.lower()
    for p in patterns:
        m = re.search(p, low, re.I | re.S)
        if m:
            out.append(m.group(0)[:240])
    return out

def classify_rules(text_value: str) -> dict:
    positive = _matches(text_value, POSITIVE)
    paid = _matches(text_value, PAID_ONLY)
    prohibited = _matches(text_value, PROHIBIT_ADS)
    links = _matches(text_value, PROHIBIT_LINKS)
    reply_only = _matches(text_value, REPLY_ONLY)
    frequency = _matches(text_value, FREQUENCY)

    if paid or prohibited or links:
        decision = "blocked"
        reason = "paid_or_prohibited"
    elif reply_only:
        decision = "reply_only"
        reason = "only_replies_to_explicit_demand"
    elif positive:
        decision = "allowed"
        reason = "explicit_service_or_advertising_section"
    else:
        decision = "review"
        reason = "rules_ambiguous"

    return {
        "decision": decision,
        "reason": reason,
        "requirements": _extract_requirements(text_value),
        "evidence": {
            "positive": positive,
            "paid": paid,
            "prohibited": prohibited,
            "links": links,
            "reply_only": reply_only,
            "frequency": frequency,
        },
        "mandatory_contacts_compatible": not bool(links),
        "zero_cost_compatible": not bool(paid),
    }

def inspect_url(url: str) -> dict:
    # Some public forums return an artificial 403/5xx only to obvious bot UAs
    # while the same public page is available to a normal browser. Retry once
    # with a browser UA so rule-audit does not create a false owner blocker.
    # This is plain HTTP retrieval only: no CAPTCHA/challenge solving.
    response = None
    last_error: Exception | None = None
    profile = "boris"
    for profile, ua in (("boris", USER_AGENT), ("browser", BROWSER_USER_AGENT)):
        try:
            candidate = requests.get(
                url,
                headers={"User-Agent": ua},
                timeout=20,
                allow_redirects=True,
            )
            candidate.raise_for_status()
            response = candidate
            break
        except Exception as exc:
            last_error = exc
            response = None
    if response is None:
        assert last_error is not None
        raise last_error

    text_value = _clean_html(response.text)
    result = classify_rules(text_value)
    result.update({
        "url": response.url,
        "http_status": response.status_code,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "text_sha256": hashlib.sha256(text_value.encode("utf-8")).hexdigest(),
        "text_excerpt": text_value[:50000],
        "outbound_links": _page_links(response.text, response.url),
        "transport_profile": profile,
    })
    return result

def inspect_platform(platform_key: str) -> dict:
    platform = marketplace.get_platform(platform_key)
    if not platform:
        raise ValueError("unknown platform")
    urls = RULE_URLS.get(platform_key) or [platform.url]
    inspections = []
    errors = []
    for url in urls:
        try:
            inspections.append(inspect_url(url))
        except Exception as exc:
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    decisions = [x["decision"] for x in inspections]
    requirements = sorted({
        req for item in inspections for req in (item.get("requirements") or [])
    })
    if "blocked" in decisions:
        final = "blocked"
    elif "reply_only" in decisions:
        final = "reply_only"
    elif "allowed" in decisions and "review" not in decisions:
        final = "allowed"
    elif "allowed" in decisions:
        final = "allowed"
    else:
        final = "review"

    # Forum Suppliers has explicit commercial categories for supplier
    # offers, goods sales and service resumes. Its admin-specific prohibition
    # concerns selling supplier databases, not ordinary goods/services offers.
    # Fail closed unless the dedicated sections and narrow admin wording are all
    # present in the fresh audit.
    if platform_key == "partnersearch":
        service_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=22" in str(x.get("url") or "")
        )
        wholesale_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=28" in str(x.get("url") or "")
        )
        service_ok = (
            "услуги и оборудование для бизнеса" in service_excerpts
            and "b2b услуги и другие деловые предложения" in service_excerpts
            and "новая тема" in service_excerpts
        )
        wholesale_ok = (
            "оптовая торговля" in wholesale_excerpts
            and "ищете поставщика" in wholesale_excerpts
            and "оптовые продажи" in wholesale_excerpts
            and "новая тема" in wholesale_excerpts
        )
        if service_ok and wholesale_ok:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_verified_b2b_services_section_only",
                "use_verified_wholesale_goods_section_only",
            })
        else:
            final = "review"

    if platform_key == "supplier_forum":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        supplier_section = (
            "я поставщик. поиск партнеров и дилеров" in excerpts
            and "предложения о партнерстве" in excerpts
        )
        goods_section = (
            "продам" in excerpts
            and "объявления о продаже" in excerpts
        )
        services_section = (
            "резюме. ищу работу" in excerpts
            and "можно предложить свои услуги" in excerpts
        )
        narrow_database_ban = (
            "запрещается рекламировать и размещать объявления о продаже каких-либо баз поставщиков"
            in excerpts
        )
        if supplier_section and goods_section and services_section and narrow_database_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "supplier_databases_prohibited",
                "use_verified_commercial_sections_only",
            })
        else:
            final = "review"

    if platform_key == "partnersearch":
        service_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=22" in str(x.get("url") or "")
        )
        wholesale_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=28" in str(x.get("url") or "")
        )
        service_ok = (
            "услуги и оборудование для бизнеса" in service_excerpts
            and "b2b услуги и другие деловые предложения" in service_excerpts
            and "новая тема" in service_excerpts
        )
        wholesale_ok = (
            "оптовая торговля" in wholesale_excerpts
            and "ищете поставщика" in wholesale_excerpts
            and "оптовые продажи" in wholesale_excerpts
            and "новая тема" in wholesale_excerpts
        )
        if service_ok and wholesale_ok:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_verified_b2b_services_section_only",
                "use_verified_wholesale_goods_section_only",
            })
        else:
            final = "review"

    if platform_key == "supplier_forum":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        supplier_section = (
            "я поставщик. поиск партнеров и дилеров" in excerpts
            and "предложения о партнерстве и диллерстве" in excerpts
        )
        goods_section = (
            "продам" in excerpts
            and "объявления о продаже" in excerpts
        )
        services_section = (
            "резюме. ищу работу" in excerpts
            and "в этом разделе можно предложить свои услуги" in excerpts
        )
        narrow_database_ban = (
            "запрещается рекламировать и размещать объявления о продаже каких-либо баз поставщиков"
            in excerpts
        )
        if supplier_section and goods_section and services_section and narrow_database_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "supplier_databases_prohibited",
                "use_verified_commercial_sections_only",
            })
        else:
            final = "review"

    if platform_key == "supplier_forum":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        supplier_section = (
            "я поставщик. поиск партнеров и дилеров" in excerpts
            and "предложения о партнерстве и диллерстве" in excerpts
        )
        goods_section = (
            "продам" in excerpts
            and "объявления о продаже" in excerpts
        )
        services_section = (
            "резюме. ищу работу" in excerpts
            and "в этом разделе можно предложить свои услуги" in excerpts
        )
        narrow_database_ban = (
            "запрещается рекламировать и размещать объявления о продаже каких-либо баз поставщиков"
            in excerpts
        )
        if supplier_section and goods_section and services_section and narrow_database_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "supplier_databases_prohibited",
                "use_verified_commercial_sections_only",
            })
        else:
            final = "review"

    # WJunction exposes a dedicated Services marketplace. Do not infer
    # permission from generic webmaster discussions: unlock only when the
    # exact commercial section says service providers (including programmers
    # and SEO professionals) may post there.
    if platform_key == "wjunction_services":
        service_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "/forums/services.112" in str(x.get("url") or "")
        )
        dedicated_services = (
            "this forum is for all service providers" in service_excerpts
            and "programmers" in service_excerpts
            and "seo professionals" in service_excerpts
            and "post thread" in service_excerpts
        )
        if dedicated_services:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_services_section_only",
                "english_content_required",
            })
        else:
            final = "review"

    # FinForum's global anti-spam rule applies to ordinary discussions, while
    # the site simultaneously maintains an explicit commercial subsection
    # named "Предлагаю услуги". Unlock only that exact subsection and only when
    # the same official rules also state that section-specific rules govern
    # topic creation and that a project topic must contain a resource link.
    if platform_key == "finforum_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated_services = (
            "предлагаю услуги" in excerpts
            and "в разделе размещаются объявления с предложением услуг" in excerpts
        )
        section_specific = "соблюдай правила раздела" in excerpts
        topic_link_required = (
            "тема должна быть раскрыта" in excerpts
            and "обязательно должна быть ссылка на сам ресурс" in excerpts
        )
        if dedicated_services and section_specific and topic_link_required:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_dedicated_services_section_only",
                "no_spam_outside_commercial_section",
                "topic_must_be_disclosed",
                "site_link_required",
            })
        else:
            final = "review"

    # Specialized automation/no-code communities. Global anti-spam language
    # does not unlock promotion by itself; each adapter requires affirmative
    # evidence from the exact jobs/freelance category that provider posts are
    # accepted there.
    if platform_key == "searchengines_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "программирование - работа и услуги для вебмастеров" in excerpts
            or (
                "работа и услуги для вебмастера" in excerpts
                and "программирование" in excerpts
            )
        )
        explicit_commercial_exception = (
            "публикация коммерческих" in excerpts
            and "объявлений возможна только" in excerpts
            and "работа и" in excerpts
            and "услуги для вебмастера" in excerpts
        )
        automation_access_ban = any(x in excerpts for x in [
            "запрещено автоматизированное использование сайта",
            "запрещен автоматизированный доступ к сайту",
            "не допускается автоматизированный доступ к сайту",
        ])
        if dedicated and explicit_commercial_exception and not automation_access_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_webmaster_programming_section_only",
                "single_relevant_commercial_topic",
                "no_spam_or_mass_campaigns",
                "it_or_automation_services_only",
            })
        else:
            final = "review"

    if platform_key == "zismo_programming_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "программирование" in excerpts
            and (
                "услуги python-разработчика" in excerpts
                or "боты, автоматизация, скрипты" in excerpts
                or "разработка" in excerpts
            )
        )
        announcements_allowed = (
            "каждый пользователь вправе опубликовывать" in excerpts
            or "осуществляет добавление объявлений" in excerpts
        )
        restricted_reggers = (
            "запрещено размещать программы/сервисы для массовой регистрации аккаунтов" in excerpts
            or "массовой регистрации аккаунтов" in excerpts
        )
        automation_access_ban = any(x in excerpts for x in [
            "запрещен автоматизированный доступ",
            "запрещено автоматизированное использование",
            "automated access is prohibited",
        ])
        if dedicated and announcements_allowed and restricted_reggers and not automation_access_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_programming_section_only",
                "no_mass_account_registration_services",
                "lawful_it_automation_services_only",
                "single_relevant_topic",
            })
        else:
            final = "review"

    if platform_key == "n8n_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs - n8n community" in excerpts
            and "for hire" in excerpts
            and ("automation" in excerpts or "n8n projects" in excerpts)
        )
        about = (
            "find open positions at n8n or related jobs" in excerpts
            and "looking for an expert" in excerpts
        )
        if dedicated and about:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_category_only",
                "n8n_related_services_only",
                "english_content_required",
                "no_cross_posting",
            })
        else:
            final = "review"

    if platform_key == "airtable_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs board" in excerpts
            and "post an open role of your own" in excerpts
        )
        provider_examples = (
            "looking for an airtable consultant? maybe i can help" in excerpts
            or "airtable automation expert" in excerpts
            or "automation specialist available" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_board_only",
                "airtable_automation_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    if platform_key == "bubble_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs / freelance" in excerpts
            and "need last-minute help on building an app? post here" in excerpts
        )
        provider_examples = (
            "bubble developer seeking job" in excerpts
            or "seeking for work" in excerpts
            or "ai agent backends for bubble apps" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_freelance_category_only",
                "bubble_or_app_development_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    if platform_key == "weweb_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs & collabs" in excerpts
            and "paid opportunities, freelance gigs" in excerpts
            and "builders looking to join forces" in excerpts
        )
        provider_examples = (
            "available for weweb projects" in excerpts
            or "developer avaliable for work" in excerpts
            or "looking for part-time weweb" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_collabs_category_only",
                "weweb_or_webapp_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    # FAST_IT_MARKETPLACE_RULE_OVERRIDES_V23 must participate in the canonical
    # inspection path. The helper existed but was never wired here, so official
    # marketplace evidence for Forum Promotion remained stuck in REVIEW forever.
    final, requirements = _fast_it_marketplace_rule_override(
        platform_key, inspections, final, requirements
    )
    final, requirements = _curated_goods_vendor_rule_override(
        platform_key, inspections, final, requirements
    )
    final, requirements = _promebelclub_rule_override(
        platform_key, inspections, final, requirements
    )
    if platform_key == "disc_optiboard_com":
        _optiboard_marketplace = " ".join(
            str(x.get("text_excerpt") or "").lower() for x in inspections
            if "/optical-forums/optical-marketplace" in str(x.get("url") or "")
        )
        if (
            "post new threads and items for sale" in _optiboard_marketplace
            and "purchase one of the optiboard subscriptions" in _optiboard_marketplace
        ):
            final = "blocked"
            requirements = sorted(set(requirements) | {"paid_subscription_required_for_marketplace_posting"})
    # CLIOSPORT_ZERO_COST_COMMERCIAL_TRUTH_V29: the public forum root advertises
    # a free account, but the actual Marketplace is visible only to Club Members
    # and the commercial Traders area requires separate trader access. A generic
    # positive phrase must therefore never promote this site into the zero-cost
    # reusable goods reserve until a free commercial posting path is proven.
    if platform_key == "disc_cliosport_net":
        _clio = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _restricted_marketplace = (
            "marketplace" in _clio
            and "visible to cliosport club members only" in _clio
        )
        _restricted_traders = (
            "cliosport traders" in _clio
            and "forum for cliosport traders to post offer and promote their services" in _clio
            and "cliosport club members only" in _clio
        )
        if _restricted_marketplace or _restricted_traders:
            final = "review"
            requirements = sorted(set(requirements) | {
                "commercial_posting_requires_club_or_trader_access_not_zero_cost_verified",
                "do_not_count_in_free_goods_reserve",
            })

    if platform_key == "disc_scubaboard_com":
        _scuba = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _b2b_scope = (
            "here in the marketplace" in _scuba
            and "stores, boats, operators, hotels" in _scuba
            and "can buy, sell, or trade things that other business members may find useful" in _scuba
        )
        _live_goods = (
            "b2b marketplace" in _scuba
            and ("cases of regulator bag closeouts" in _scuba or "business of used breathing air compressor" in _scuba)
        )
        if _b2b_scope and _live_goods:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_b2b_marketplace_only",
                "scuba_or_diving_business_goods_only",
                "english_content_required",
                "single_relevant_commercial_topic",
            })
        else:
            final = "review"

    # OBOROT_GOODS_EXPLICIT_SELL_THREAD_V28: moderator-owned «Продаю» threads
    # explicitly invite product offers. This exception is intentionally scoped
    # to the separate goods key; it must never relax the generic `oborot` rules.
    if platform_key == "oborot_goods":
        _oborot_goods = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _explicit_sell = (
            "можете размещать свои предложения о продаже" in _oborot_goods
            and "создавать отдельные темы для предложений о продаже" in _oborot_goods
            and "они будут удаляться как спам" in _oborot_goods
        )
        _commercial_contact_evidence = (
            "www.altagmbh.com" in _oborot_goods
            or "autoazart.ru" in _oborot_goods
            or "sales@whitestars.by" in _oborot_goods
        )
        if _explicit_sell and _commercial_contact_evidence:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "reply_in_matching_sell_thread_only",
                "separate_advertising_topic_prohibited",
                "goods_only",
                "recheck_thread_before_each_publication",
            })
        else:
            final = "review"

    row = {
        "platform": platform_key,
        "name": platform.name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "decision": final,
        "free_only": True,
        "mandatory_boris_link": True,
        "mandatory_whatsapp": True,
        "mandatory_max": True,
        "requirements": requirements,
        "inspections": inspections,
        "errors": errors,
    }
    path = STATE_DIR / f"{platform_key}.json"
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row

def latest(platform_key: str) -> dict | None:
    path = STATE_DIR / f"{platform_key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def publish_gate(platform_key: str, *, action: str = "proactive") -> dict:
    # A permissive forum rule is not enough: BORIS only uses platforms that
    # also satisfy the hard free-only/business policy.
    policy = marketplace.platform_policy(platform_key)
    if not policy.get("free"):
        return {
            "allowed": False,
            "reason": str(policy.get("reason") or "free_only_policy"),
            "platform": platform_key,
            "checked_at": policy.get("rule_checked_at"),
        }

    row = latest(platform_key)
    if not row:
        return {"allowed": False, "reason": "rules_not_checked", "platform": platform_key}

    try:
        checked = datetime.fromisoformat(str(row["checked_at"]).replace("Z", "+00:00"))
    except Exception:
        return {"allowed": False, "reason": "invalid_rule_snapshot", "platform": platform_key}
    if checked < datetime.now(timezone.utc) - timedelta(days=MAX_RULE_AGE_DAYS):
        return {"allowed": False, "reason": "rules_snapshot_stale", "platform": platform_key, "checked_at": row.get("checked_at")}

    decision = row.get("decision")
    if decision == "allowed":
        return {"allowed": True, "reason": "rules_allow", "platform": platform_key, "checked_at": row.get("checked_at")}
    if decision == "reply_only" and action == "reply":
        return {"allowed": True, "reason": "rules_allow_reply_only", "platform": platform_key, "checked_at": row.get("checked_at")}
    return {"allowed": False, "reason": f"rules_{decision or 'unknown'}", "platform": platform_key, "checked_at": row.get("checked_at")}

def all_status() -> list[dict]:
    out = []
    for p in marketplace.all_platform_objects():
        row = latest(p.key)
        gate = publish_gate(p.key)
        out.append({
            "platform": p.key,
            "name": p.name,
            "rule_status": row.get("decision") if row else "not_checked",
            "checked_at": row.get("checked_at") if row else None,
            "publish_allowed": gate.get("allowed", False),
            "publish_reason": gate.get("reason"),
        })
    return out

RULE_URLS.update({
    "forumrieltorov": ["https://forumrieltorov.ru/viewforum.php?f=26"],
    "promebelclub": [
        "https://promebelclub.ru/forum/forumdisplay.php?f=115",
        "https://promebelclub.ru/forum/announcement.php?a=157&f=13",
        "https://promebelclub.ru/forum/showthread.php?t=15534",
    ],
    "moigruz": ["https://www.moigruz.ru/forum/"],
    "rekforum_logistics": ["https://rekforum.ru/viewforum.php?f=144"],
    "metaprom": ["https://metaprom.ru/page-production-services/"],
    "foodmarkets": [
        "https://foodmarkets.ru/forums",
        "https://non.foodmarkets.ru/blurb/category/1/page1/",
        "https://foodmarkets.ru/info/terms",
    ],
    "kolsar_auto": ["https://kolsar.info/forum/viewforum.php?f=88"],
})

RULE_URLS.update({
    "partnersearch": [
        "https://www.partnersearch.ru/business/viewforum.php?f=22",
        "https://www.partnersearch.ru/business/viewforum.php?f=28",
    ],
    "stroy_russia": ["https://stroy-russia.ru/"],
    "vashdom_forum": ["https://forum.vashdom.ru/"],
    "house_forum": ["https://house-forum.ru/"],
    "kroi_roof": ["https://www.kroi.ru/forum/"],
    "sdelaimebel": ["https://forum.sdelaimebel.ru/"],
    "mp_forum": ["https://mp-forum.ru/"],
    "sellermap": ["https://sellermap.online/community/"],
    "tcfs": ["https://tcfs.ru/forums/"],
    "stom_ru": ["https://stom.ru/"],
    "buh_1c": ["https://buh.ru/forum/group29/"],
    "ati_su": ["https://forums.ati.su/forum/"],
    "perevozka24": ["https://perevozka24.com/forum"],
    "sellerexit": ["https://sellerexit.ru/"],
})

def inspect_url_browser(url: str) -> dict:
    """Read public rule text with a headless browser when normal HTTP is blocked/empty."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ru-RU")
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(800)
        text_value = " ".join(page.locator("body").inner_text(timeout=5000).split())
        final_url = page.url
        status_code = response.status if response else 0
        # Browser fallback must preserve the same href evidence as HTTP.
        # Otherwise Cloudflare/403 sites can expose readable rules and live
        # merchant links in Chromium but remain permanently REVIEW because the
        # evidence disappears before the curated rule gate sees it.
        try:
            raw_links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(a => a.href).filter(Boolean)",
            )
        except Exception:
            # Link extraction is extra evidence. It must never discard readable
            # rule text when a site exposes an unusual DOM/API edge case.
            raw_links = []
        outbound_links = []
        seen_links = set()
        for value in raw_links or []:
            value = str(value or "").strip()
            if not value.startswith(("http://", "https://")) or value in seen_links:
                continue
            seen_links.add(value)
            outbound_links.append(value)
            if len(outbound_links) >= 300:
                break
        browser.close()
    result = classify_rules(text_value)
    result.update({
        "url": final_url,
        "http_status": status_code,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "text_sha256": hashlib.sha256(text_value.encode("utf-8")).hexdigest(),
        "text_excerpt": text_value[:50000],
        "outbound_links": outbound_links,
        "transport": "browser_fallback",
    })
    return result

def inspect_platform(platform_key: str) -> dict:
    """Fresh rule inspection: HTTP first, browser fallback, conservative final decision."""
    platform = marketplace.get_platform(platform_key)
    if not platform:
        raise ValueError("unknown platform")
    urls = RULE_URLS.get(platform_key) or [platform.url]
    inspections = []
    errors = []
    for url in urls:
        http_result = None
        try:
            http_result = inspect_url(url)
            http_result["transport"] = "http"
            inspections.append(http_result)
        except Exception as exc:
            errors.append({"url": url, "transport": "http", "error": f"{type(exc).__name__}: {exc}"})
        if http_result is None or http_result.get("decision") == "review":
            try:
                browser_result = inspect_url_browser(url)
                # Avoid duplicate rendered snapshot when it says nothing more.
                if browser_result.get("text_sha256") != (http_result or {}).get("text_sha256"):
                    inspections.append(browser_result)
                elif http_result is None:
                    inspections.append(browser_result)
            except Exception as exc:
                errors.append({"url": url, "transport": "browser", "error": f"{type(exc).__name__}: {exc}"})

    decisions = [x["decision"] for x in inspections]
    requirements = sorted({
        req for item in inspections for req in (item.get("requirements") or [])
    })
    if "blocked" in decisions:
        final = "blocked"
    elif "reply_only" in decisions and "allowed" not in decisions:
        final = "reply_only"
    elif "allowed" in decisions:
        final = "allowed"
    else:
        final = "review"

    if platform_key == "foodmarkets":
        nonfood_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "non.foodmarkets.ru/blurb/category/1" in str(x.get("url") or "")
        )
        terms_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "/info/terms" in str(x.get("url") or "")
        )
        board_ok = (
            "доска объявлений" in nonfood_excerpts
            and "непродовольственные товары" in nonfood_excerpts
            and "предложение" in nonfood_excerpts
            and "поиск дистрибьюторов" in nonfood_excerpts
            and "добавить объявление" in nonfood_excerpts
        )
        outbound_links_ok = (
            "ссылок на материалы" in terms_excerpts
            and "на других информационных ресурсах" in terms_excerpts
        )
        if board_ok and outbound_links_ok:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_verified_nonfood_goods_board_only",
                "truthful_product_information_required",
            })
        else:
            final = "review"

    # Nulled's general rules mention paid advertising, but the same official
    # rules explicitly provide a zero-cost advertising path after Level 3.
    # Override is fail-closed: it activates only when both the dedicated
    # commercial section and the Level 3 free-advertising text were fetched.
    if platform_key == "nulled_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated_section = any(
            x.get("decision") == "allowed"
            and "коммерция и реклама услуг" in str(x.get("text_excerpt") or "").lower()
            for x in inspections
        )
        level3_free = (
            "level 3" in excerpts
            and "бесплатно размещать новые темы в разделах для рекламы" in excerpts
        )
        anti_abuse = "накрут" in excerpts
        if dedicated_section and level3_free and anti_abuse:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "level_3_required",
                "minimum_account_age_15_days",
                "minimum_30_messages",
                "minimum_5_reactions",
                "no_artificial_warmup",
            })
        else:
            final = "review"

    # CyberForum has a dedicated "Предложения фрилансеров" section and its
    # parent freelance rules explicitly allow an executor one personal
    # service/portfolio topic. Access is gated by group 4+ and a membership
    # request, so this is a warming requirement rather than paid placement.
    if platform_key == "cyberforum_freelancers":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated_section = any(
            x.get("decision") == "allowed"
            and "раздел для размещения предложений фрилансеров своих услуг"
                in str(x.get("text_excerpt") or "").lower()
            for x in inspections
        )
        personal_service_topic = (
            "исполнитель имеет право на создание не более одной персональной страницы"
            in excerpts
            and "описанием предоставляемых им услуг" in excerpts
        )
        group_gate = (
            "группы 4" in excerpts
            and "членство в группах" in excerpts
        )
        no_listing_fee = "администрация форума не имеет финансовой выгоды" in excerpts
        if dedicated_section and personal_service_topic and group_gate and no_listing_fee:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "freelance_group_4_plus_required",
                "freelance_group_membership_request_required",
                "one_personal_service_topic",
            })
        else:
            final = "review"

    if platform_key == "partnersearch":
        service_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=22" in str(x.get("url") or "")
        )
        wholesale_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "viewforum.php?f=28" in str(x.get("url") or "")
        )
        service_ok = (
            "услуги и оборудование для бизнеса" in service_excerpts
            and "b2b услуги и другие деловые предложения" in service_excerpts
            and "новая тема" in service_excerpts
        )
        wholesale_ok = (
            "оптовая торговля" in wholesale_excerpts
            and "ищете поставщика" in wholesale_excerpts
            and "оптовые продажи" in wholesale_excerpts
            and "новая тема" in wholesale_excerpts
        )
        if service_ok and wholesale_ok:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_verified_b2b_services_section_only",
                "use_verified_wholesale_goods_section_only",
            })
        else:
            final = "review"

    if platform_key == "supplier_forum":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        supplier_section = (
            "я поставщик. поиск партнеров и дилеров" in excerpts
            and "предложения о партнерстве и диллерстве" in excerpts
        )
        goods_section = (
            "продам" in excerpts
            and "объявления о продаже" in excerpts
        )
        services_section = (
            "резюме. ищу работу" in excerpts
            and "в этом разделе можно предложить свои услуги" in excerpts
        )
        narrow_database_ban = (
            "запрещается рекламировать и размещать объявления о продаже каких-либо баз поставщиков"
            in excerpts
        )
        if supplier_section and goods_section and services_section and narrow_database_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "supplier_databases_prohibited",
                "use_verified_commercial_sections_only",
            })
        else:
            final = "review"

    if platform_key == "wjunction_services":
        service_excerpts = " ".join(
            str(x.get("text_excerpt") or "").lower()
            for x in inspections
            if "/forums/services.112" in str(x.get("url") or "")
        )
        dedicated_services = (
            "this forum is for all service providers" in service_excerpts
            and "programmers" in service_excerpts
            and "seo professionals" in service_excerpts
            and "post thread" in service_excerpts
        )
        if dedicated_services:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_services_section_only",
                "english_content_required",
            })
        else:
            final = "review"

    if platform_key == "finforum_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated_services = (
            "предлагаю услуги" in excerpts
            and "в разделе размещаются объявления с предложением услуг" in excerpts
        )
        section_specific = "соблюдай правила раздела" in excerpts
        topic_link_required = (
            "тема должна быть раскрыта" in excerpts
            and "обязательно должна быть ссылка на сам ресурс" in excerpts
        )
        if dedicated_services and section_specific and topic_link_required:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_dedicated_services_section_only",
                "no_spam_outside_commercial_section",
                "topic_must_be_disclosed",
                "site_link_required",
            })
        else:
            final = "review"

    if platform_key == "searchengines_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "программирование - работа и услуги для вебмастеров" in excerpts
            or ("работа и услуги для вебмастера" in excerpts and "программирование" in excerpts)
        )
        explicit_commercial_exception = (
            "публикация коммерческих" in excerpts
            and "объявлений возможна только" in excerpts
            and "услуги для вебмастера" in excerpts
        )
        automation_access_ban = any(x in excerpts for x in [
            "запрещено автоматизированное использование сайта",
            "запрещен автоматизированный доступ к сайту",
            "не допускается автоматизированный доступ к сайту",
        ])
        if dedicated and explicit_commercial_exception and not automation_access_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_webmaster_programming_section_only",
                "single_relevant_commercial_topic",
                "no_spam_or_mass_campaigns",
                "it_or_automation_services_only",
            })
        else:
            final = "review"

    if platform_key == "zismo_programming_services":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "программирование" in excerpts
            and ("услуги python-разработчика" in excerpts or "боты, автоматизация, скрипты" in excerpts or "разработка" in excerpts)
        )
        announcements_allowed = (
            "каждый пользователь вправе опубликовывать" in excerpts
            or "осуществляет добавление объявлений" in excerpts
        )
        restricted_reggers = "массовой регистрации аккаунтов" in excerpts
        automation_access_ban = any(x in excerpts for x in [
            "запрещен автоматизированный доступ",
            "запрещено автоматизированное использование",
            "automated access is prohibited",
        ])
        if dedicated and announcements_allowed and restricted_reggers and not automation_access_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_programming_section_only",
                "no_mass_account_registration_services",
                "lawful_it_automation_services_only",
                "single_relevant_topic",
            })
        else:
            final = "review"

    if platform_key == "n8n_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs - n8n community" in excerpts
            and "for hire" in excerpts
            and ("automation" in excerpts or "n8n projects" in excerpts)
        )
        about = (
            "find open positions at n8n or related jobs" in excerpts
            and "looking for an expert" in excerpts
        )
        if dedicated and about:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_category_only",
                "n8n_related_services_only",
                "english_content_required",
                "no_cross_posting",
            })
        else:
            final = "review"

    if platform_key == "airtable_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs board" in excerpts
            and "post an open role of your own" in excerpts
        )
        provider_examples = (
            "looking for an airtable consultant? maybe i can help" in excerpts
            or "airtable automation expert" in excerpts
            or "automation specialist available" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_board_only",
                "airtable_automation_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    if platform_key == "bubble_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs / freelance" in excerpts
            and "need last-minute help on building an app? post here" in excerpts
        )
        provider_examples = (
            "bubble developer seeking job" in excerpts
            or "seeking for work" in excerpts
            or "ai agent backends for bubble apps" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_freelance_category_only",
                "bubble_or_app_development_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    if platform_key == "weweb_jobs":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "jobs & collabs" in excerpts
            and "paid opportunities, freelance gigs" in excerpts
            and "builders looking to join forces" in excerpts
        )
        provider_examples = (
            "available for weweb projects" in excerpts
            or "developer avaliable for work" in excerpts
            or "looking for part-time weweb" in excerpts
        )
        if dedicated and provider_examples:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_jobs_collabs_category_only",
                "weweb_or_webapp_related_services_only",
                "english_content_required",
                "single_relevant_provider_topic",
            })
        else:
            final = "review"

    if platform_key == "freehostforum_webdev":
        excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
        dedicated = (
            "web development offers and requests" in excerpts
            and "custom programming" in excerpts
            and "development services" in excerpts
        )
        free_marketplace = (
            "webmaster related advertising is allowed in webmaster marketplace section only" in excerpts
            and "free of charge" in excerpts
        )
        advertising_scope = (
            "do not post links (ads) in posts or threads in non advertising forums" in excerpts
            and "no advertising allowed except paid stickies in other sections" in excerpts
        )
        automation_access_ban = any(x in excerpts for x in [
            "automated access is prohibited",
            "automated use is prohibited",
            "no automated access",
            "bots are prohibited",
        ])
        if dedicated and free_marketplace and advertising_scope and not automation_access_ban:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_web_development_offers_section_only",
                "webmaster_related_services_only",
                "english_content_required",
                "single_relevant_commercial_topic",
                "no_spam_or_mass_posting",
            })
        else:
            final = "review"

    # FAST_IT_MARKETPLACE_RULE_OVERRIDES_V23 must participate in the canonical
    # inspection path. The helper existed but was never wired here, so official
    # marketplace evidence for Forum Promotion remained stuck in REVIEW forever.
    final, requirements = _fast_it_marketplace_rule_override(
        platform_key, inspections, final, requirements
    )
    final, requirements = _curated_goods_vendor_rule_override(
        platform_key, inspections, final, requirements
    )
    final, requirements = _promebelclub_rule_override(
        platform_key, inspections, final, requirements
    )
    if platform_key == "disc_optiboard_com":
        _optiboard_marketplace = " ".join(
            str(x.get("text_excerpt") or "").lower() for x in inspections
            if "/optical-forums/optical-marketplace" in str(x.get("url") or "")
        )
        if (
            "post new threads and items for sale" in _optiboard_marketplace
            and "purchase one of the optiboard subscriptions" in _optiboard_marketplace
        ):
            final = "blocked"
            requirements = sorted(set(requirements) | {"paid_subscription_required_for_marketplace_posting"})
    # CLIOSPORT_ZERO_COST_COMMERCIAL_TRUTH_V29: the public forum root advertises
    # a free account, but the actual Marketplace is visible only to Club Members
    # and the commercial Traders area requires separate trader access. A generic
    # positive phrase must therefore never promote this site into the zero-cost
    # reusable goods reserve until a free commercial posting path is proven.
    if platform_key == "disc_cliosport_net":
        _clio = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _restricted_marketplace = (
            "marketplace" in _clio
            and "visible to cliosport club members only" in _clio
        )
        _restricted_traders = (
            "cliosport traders" in _clio
            and "forum for cliosport traders to post offer and promote their services" in _clio
            and "cliosport club members only" in _clio
        )
        if _restricted_marketplace or _restricted_traders:
            final = "review"
            requirements = sorted(set(requirements) | {
                "commercial_posting_requires_club_or_trader_access_not_zero_cost_verified",
                "do_not_count_in_free_goods_reserve",
            })

    if platform_key == "disc_scubaboard_com":
        _scuba = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _b2b_scope = (
            "here in the marketplace" in _scuba
            and "stores, boats, operators, hotels" in _scuba
            and "can buy, sell, or trade things that other business members may find useful" in _scuba
        )
        _live_goods = (
            "b2b marketplace" in _scuba
            and ("cases of regulator bag closeouts" in _scuba or "business of used breathing air compressor" in _scuba)
        )
        if _b2b_scope and _live_goods:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "use_b2b_marketplace_only",
                "scuba_or_diving_business_goods_only",
                "english_content_required",
                "single_relevant_commercial_topic",
            })
        else:
            final = "review"

    # OBOROT_GOODS_EXPLICIT_SELL_THREAD_V28: moderator-owned «Продаю» threads
    # explicitly invite product offers. This exception is intentionally scoped
    # to the separate goods key; it must never relax the generic `oborot` rules.
    if platform_key == "oborot_goods":
        _oborot_goods = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
        _explicit_sell = (
            "можете размещать свои предложения о продаже" in _oborot_goods
            and "создавать отдельные темы для предложений о продаже" in _oborot_goods
            and "они будут удаляться как спам" in _oborot_goods
        )
        _commercial_contact_evidence = (
            "www.altagmbh.com" in _oborot_goods
            or "autoazart.ru" in _oborot_goods
            or "sales@whitestars.by" in _oborot_goods
        )
        if _explicit_sell and _commercial_contact_evidence:
            final = "allowed"
            requirements = sorted(set(requirements) | {
                "reply_in_matching_sell_thread_only",
                "separate_advertising_topic_prohibited",
                "goods_only",
                "recheck_thread_before_each_publication",
            })
        else:
            final = "review"

    # DYNAMIC_EXACT_COMMERCIAL_SURFACE_V31:
    # A dynamically discovered forum may be promoted from REVIEW only when the
    # candidate points to an exact service-offer OR goods marketplace/classifieds
    # section, the current audit proves the page is reachable and authoring is
    # exposed, and no paid/ad/link prohibition was found. Generic forum roots
    # never use this override.
    if platform_key.startswith("disc_") and final == "review":
        try:
            from app.services import forum_discovery
            candidate = next(
                (x for x in forum_discovery.list_candidates(min_score=8) if x.get("key") == platform_key),
                None,
            )
        except Exception:
            candidate = None
        if candidate:
            surfaces = marketplace._dynamic_verified_publication_surface(candidate)
            candidate_signal = " ".join(
                str(candidate.get(k) or "")
                for k in ("name", "url", "source_surface_label")
            ).lower()
            service_markers = (
                "предлагаю услуги", "предложение услуг", "услуги исполнителей",
                "services offered", "offer services", "services marketplace",
                "for hire", "freelance services",
            )
            goods_markers = (
                "продам", "куплю", "куплю / продам", "куплю/продам",
                "барахолка", "торговая площадка", "объявления о продаже",
                "classif", "classifieds", "marketplace", "buy sell", "buy/sell",
                "buy, sell", "for sale", "buy & sell", "buy and sell",
            )
            inspection_text = " ".join(
                str(x.get("text_excerpt") or "") for x in inspections
            ).lower()
            create_markers = (
                "новая тема", "создать тему", "начать новую тему",
                "new topic", "post new topic", "create thread", "post thread",
                "start new topic",
            )
            negative_evidence = any(
                bool((x.get("evidence") or {}).get("paid"))
                or bool((x.get("evidence") or {}).get("prohibited"))
                or bool((x.get("evidence") or {}).get("links"))
                for x in inspections
            )
            restricted_text = any(
                marker in inspection_text
                for marker in (
                    "private ads only", "club members only", "premium members only",
                    "paid members only", "supporting vendors only",
                    "purchase one of", "subscription required",
                )
            )
            reachable_now = any(
                200 <= int(x.get("http_status") or 0) < 400
                for x in inspections
            )
            service_surface = bool(surfaces) and any(
                "services" in (surface.get("niches") or []) for surface in surfaces
            )
            goods_surface = bool(surfaces) and any(
                "goods" in (surface.get("niches") or []) for surface in surfaces
            )
            signal_text = candidate_signal + " " + inspection_text[:12000]
            explicit_service_offer = any(
                marker in signal_text for marker in service_markers
            )
            explicit_goods_market = any(
                marker in signal_text for marker in goods_markers
            )
            authoring_exposed = bool(candidate.get("create_topic_hint")) or any(
                marker in inspection_text for marker in create_markers
            )
            common_safe = (
                authoring_exposed
                and reachable_now
                and not negative_evidence
                and not restricted_text
            )
            if service_surface and explicit_service_offer and common_safe:
                final = "allowed"
                requirements = sorted(set(requirements) | {
                    "use_exact_discovered_service_surface_only",
                    "recheck_rules_before_each_publication",
                })
            elif goods_surface and explicit_goods_market and common_safe:
                final = "allowed"
                requirements = sorted(set(requirements) | {
                    "use_exact_discovered_goods_surface_only",
                    "recheck_rules_before_each_publication",
                })

    row = {
        "platform": platform_key,
        "name": platform.name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "decision": final,
        "free_only": True,
        "mandatory_boris_link": True,
        "mandatory_whatsapp": True,
        "mandatory_max": True,
        "requirements": requirements,
        "inspections": inspections,
        "errors": errors,
    }
    path = STATE_DIR / f"{platform_key}.json"
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row

RULE_URLS.update({
    "supplier_forum": ["https://forum.tvoipostavshik.ru/"],
    "autopeople": ["https://autopeople.ru/forum/repair/"],
})

RULE_URLS.update({
    "sellersforum": ["https://sellersforum.ru/"],
    "dalionauto_forum": ["https://forum.dalionauto.ru/"],
    "mmgp": ["https://mmgp.com/"],
    "gidtalk": ["https://gidtalk.ru/"],
    "saitsozdanie_forum": ["https://saitsozdanie.ru/forum/"],
    "wmboard_qa": ["https://qa.wmboard.net/"],
})


# Exact commercial section + placement rules for VashDom.
RULE_URLS["vashdom_forum"] = [
    "https://forum.vashdom.ru/forums/uslugi-dlja-vashego-doma.147/",
    "https://forum.vashdom.ru/threads/pravila-razmeschenija-objavlenij.51526/",
]

# Exact official rules for the 1C-Bitrix jobs/services forum. The forum page
# contains commercial-looking topics, but the global rules explicitly forbid
# advertising goods/services without special administration permission.
RULE_URLS["bitrix_dev"] = [
    "https://dev.1c-bitrix.ru/community/forums/forum14/",
    "https://dev.1c-bitrix.ru/community/forums/rules/",
]


RULE_URLS["nulled_services"] = [
    "https://nulled.cc/forums/kommerciya-i-reklama-uslug.198/",
    "https://nulled.cc/pages/rules/",
    "https://nulled.cc/pages/user-levels/",
]


RULE_URLS["cyberforum_freelancers"] = [
    "https://www.cyberforum.ru/freelancers-offers/",
    "https://www.cyberforum.ru/freelancers-offers/announcement56.html",
    "https://www.cyberforum.ru/misc.php?do=showrules",
]


# SUPPLIER_FORUM_EXACT_COMMERCIAL_RULES_V19
RULE_URLS["supplier_forum"] = [
    "https://forum.tvoipostavshik.ru/forums/partnery-i-dillery/",
    "https://forum.tvoipostavshik.ru/forums/prodam/",
    "https://forum.tvoipostavshik.ru/forums/rezjume-ischu-rabotu.5/",
    "https://forum.tvoipostavshik.ru/posts/31/",
    "https://forum.tvoipostavshik.ru/help/terms",
]


# CURATED_SERVICE_RULES_V20
RULE_URLS["wjunction_services"] = [
    "https://www.wjunction.com/forums/services.112/",
    "https://www.wjunction.com/help/terms/",
]
RULE_URLS["finforum_services"] = [
    "https://finforum.pro/forums/predlagaju-uslugi.90/",
    "https://finforum.pro/help/terms",
]


# CURATED_SPECIALIZED_AUTOMATION_RULES_V21
RULE_URLS["n8n_jobs"] = [
    "https://community.n8n.io/c/jobs/13",
    "https://community.n8n.io/t/about-the-jobs-category/941",
    "https://community.n8n.io/guidelines",
]
RULE_URLS["airtable_jobs"] = [
    "https://community.airtable.com/jobs-board-16",
    "https://community.airtable.com/jobs-board-16/looking-for-an-airtable-consultant-maybe-i-can-help-41217",
]
RULE_URLS["bubble_jobs"] = [
    "https://forum.bubble.io/c/jobs-freelance/13",
    "https://forum.bubble.io/t/about-the-jobs-freelance-category/1577",
    "https://forum.bubble.io/guidelines",
]
RULE_URLS["weweb_jobs"] = [
    "https://community.weweb.io/c/jobs/22",
    "https://community.weweb.io/t/about-the-jobs-collabs-category/18188",
    "https://community.weweb.io/guidelines",
]


# CURATED_IT_SERVICE_RULES_V23
RULE_URLS["searchengines_services"] = [
    "https://searchengines.guru/ru/forum/webmasters-jobs/programming",
    "https://searchengines.guru/ru/about/rules",
    "https://searchengines.guru/ru/about/terms",
]
RULE_URLS["zismo_programming_services"] = [
    "https://zismo.biz/forum/92-programmirovanie/",
    "https://zismo.biz/topic/214-pravila-foruma/",
    "https://zismo.biz/index.php?app=core&module=global&section=register",
]

# OPTIBOARD_ZERO_COST_TRUTH_V1: the forum root advertises a Marketplace, but
# the Marketplace itself explicitly requires a purchased OptiBoard subscription
# to create new sale threads. Audit the exact commercial surface so a generic
# root-page phrase can never promote it into the free Crowd SEO reserve.
RULE_URLS["disc_optiboard_com"] = [
    "https://www.optiboard.com/forums/forum/optical-forums/optical-marketplace",
]
RULE_URLS["disc_scubaboard_com"] = [
    "https://scubaboard.com/community/forums/b2b-marketplace.248/",
    "https://scubaboard.com/community/threads/welcome-to-the-b2b-marketplace.196490/",
]


# PROMEBELCLUB_EXACT_COMMERCIAL_RULES_V34
def _promebelclub_rule_override(platform_key, inspections, final, requirements):
    if platform_key != "promebelclub":
        return final, requirements

    excerpts = " ".join(
        str(x.get("text_excerpt") or "").lower() for x in inspections
    )
    explicit_commercial_surface = (
        "продаю | сдаю" in excerpts
        and "любые объявления рекламного характера" in excerpts
        and "по продаже продукции" in excerpts
        and "оказанию услуг" in excerpts
    )
    explicit_section_rules = (
        "правила создания тем в разделе объявления" in excerpts
        and "обязательно указывать регион и контактную информацию" in excerpts
    )
    external_site_evidence = (
        "наш сайт" in excerpts
        and ("https://" in excerpts or "http://" in excerpts)
    )

    if explicit_commercial_surface and explicit_section_rules and external_site_evidence:
        return "allowed", sorted(set(requirements) | {
            "use_promebelclub_sell_rent_only",
            "furniture_or_related_goods_services_only",
            "region_and_contact_required",
            "external_site_seen_in_commercial_listing",
            "single_relevant_commercial_topic",
        })
    return "review", requirements


# FAST_IT_MARKETPLACE_RULE_OVERRIDES_V23
def _fast_it_marketplace_rule_override(platform_key, inspections, final, requirements):
    excerpts = " ".join(str(x.get("text_excerpt") or "") for x in inspections).lower()
    automation_access_ban = any(x in excerpts for x in [
        "automated access is prohibited",
        "automated use is prohibited",
        "bots are prohibited",
    ])

    if platform_key == "htmlforums_programming":
        dedicated = (
            "looking to offer or pay for programming services? post it here" in excerpts
            and "programming/development" in excerpts
        )
        designated = (
            "no advertising or self-promotion" in excerpts
            and "unless specifically allowed in designated areas" in excerpts
        )
        if dedicated and designated and not automation_access_ban:
            return "allowed", sorted(set(requirements) | {
                "use_programming_development_section_only",
                "web_development_or_programming_services_only",
                "english_content_required",
                "single_relevant_commercial_topic",
                "no_spam_or_mass_posting",
            })
        return "review", requirements

    if platform_key == "forum_promotion_employment":
        seeking = (
            "how to post a seeking employment topic" in excerpts
            and "interested in finding a job on someone else" in excerpts
        )
        scoped_ads = (
            "only advertise where allowed" in excerpts
            and "posting links to your site or affiliate links in off-topic areas is not allowed" in excerpts
        )
        if seeking and scoped_ads and not automation_access_ban:
            return "allowed", sorted(set(requirements) | {
                "use_seeking_employment_only",
                "website_or_forum_work_only",
                "english_content_required",
                "omit_personal_phone_contacts",
                "single_relevant_provider_topic",
            })
        return "review", requirements

    return final, requirements


# PRINT_FORUM_GOODS_RULES_V25
RULE_URLS["print_forum_goods"] = [
    "https://forum.print-forum.ru/forumdisplay.php?f=22",
    "https://forum.print-forum.ru/register.php",
    "https://forum.print-forum.ru/showthread.php?t=911344",
]


# CNC_CLUB_GOODS_RULES_V26
RULE_URLS["cnc_club_goods"] = [
    "https://www.cnc-club.ru/forum/viewforum.php?f=163",
    "https://www.cnc-club.ru/forum/viewtopic.php?t=4",
    "https://www.cnc-club.ru/forum/ucp.php?mode=terms",
]


# OBOROT_GOODS_RULE_SURFACE_V28
# Dedicated moderator-owned sell threads are the only allowed surface for this
# key. Generic Oborot advertising remains governed by the older `oborot` key.
RULE_URLS["oborot_goods"] = [
    "https://oborot.ru/forum/prodayu-tovary-dlya-doma-i-dachi-i27339.html",
    "https://oborot.ru/forum/prodayu-elektronika-i-bytovaya-tehnika-i27461.html",
    "https://oborot.ru/forum/prodayu-avtomobili-avtozapchasti-aksessuary-i27226.html",
]


# CURATED_VENDOR_SURFACE_EVIDENCE_V36
# These are exact commercial sections. They remain fail-closed: the override
# returns ALLOWED only when fresh fetched evidence proves every required
# condition. A single generic marketplace/advertising phrase is insufficient.
RULE_URLS["disc_forum_arcadecontrols_com"] = [
    "https://forum.arcadecontrols.com/index.php/board,56.0.html",
    "https://forum.arcadecontrols.com/index.php/topic,141620.0.html",
    "https://forum.arcadecontrols.com/index.php?action=register",
    "https://arcadecontrols.com/arcade_message_rules.html",
]
RULE_URLS["disc_teaforum_org"] = [
    "https://www.teaforum.org/viewforum.php?f=30",
    "https://www.teaforum.org/viewtopic.php?f=2&t=7",
    "https://www.teaforum.org/viewtopic.php?f=30&t=48",
    "https://www.teaforum.org/viewtopic.php?f=30&t=2899",
]
RULE_URLS["disc_teachat_com"] = [
    "https://www.teachat.com/viewforum.php?f=60",
    "https://www.teachat.com/viewtopic.php?t=24587",
    "https://www.teachat.com/viewtopic.php?p=304350",
]
RULE_URLS["disc_forums_deeperblue_com"] = [
    "https://forums.deeperblue.com/help/rules/",
    "https://forums.deeperblue.com/threads/advertising-guidelines.28107/",
    "https://forums.deeperblue.com/forums/goods-for-sale.49/",
    "https://forums.deeperblue.com/threads/mako-spearguns-sale-on-titanium-and-stainless-kona-knives-15.128285/",
]
RULE_URLS["disc_doityourselfchristmas_com"] = [
    "https://www.doityourselfchristmas.com/forum/index.php?forums/vendors-arena.61/",
    "https://www.doityourselfchristmas.com/forum/index.php?threads/vendors-arena-read-1st.4022/",
    "https://www.doityourselfchristmas.com/forum/index.php?help/terms/",
    "https://www.doityourselfchristmas.com/forum/index.php?threads/pixel-dcssr-from-diyledexpress-com.26109/",
    "https://www.doityourselfchristmas.com/forum/index.php?threads/50-100-count-pixel-strings-in-stock.57236/",
]
RULE_URLS["disc_mcarterbrown_com"] = [
    "https://mcarterbrown.com/forum/buy-sell-trade/dealers-forum",
    "https://mcarterbrown.com/register",
    "https://mcarterbrown.com/help",
    "https://mcarterbrown.com/forum/buy-sell-trade/dealers-forum/859025-even-more-freaks-icd-thundercat-desert-fox-etc",
]
RULE_URLS["disc_penturners_org"] = [
    "https://www.penturners.org/threads/marketplace-changes.144918/",
    "https://www.penturners.org/forums/vendor-forums.208/",
]
RULE_URLS["diyaudio_goods"] = [
    "https://helpdesk.diyaudio.com/article/26/where-can-i-sell-my-stuff",
    "https://www.diyaudio.com/community/forums/vendors-bazaar.44/",
]


def _curated_goods_vendor_rule_override(platform_key, inspections, final, requirements):
    body = " ".join(str(x.get("text_excerpt") or "").lower() for x in inspections)
    links = {
        str(link).lower()
        for item in inspections
        for link in (item.get("outbound_links") or [])
    }
    paid_markers = (
        "paid vendor membership required",
        "subscription required to post",
        "premium membership required to post",
        "vendor fee required",
        "supporting membership required to post",
        "subscription required to advertise",
    )
    paid_gate = any(token in body for token in paid_markers)

    def allow(*reqs):
        return "allowed", sorted(set(requirements) | {"curated_vendor_surface_verified", *reqs})

    if platform_key == "disc_forum_arcadecontrols_com":
        proven = (
            "retail vendors" in body
            and "relating to the sales of products from retail and semi-retail hobby vendors" in body
            and "this board is for commercial/retail vendors" in body
            and "if you've sold more than 3 of whatever it is you sell" in body
            and any(host in body for host in ("gamemolding.com", "groovygamegear.com", "focusattack.com"))
            and not paid_gate
        )
        return allow(
            "use_retail_vendors_board_only",
            "arcade_or_gaming_hardware_goods_only",
            "english_content_required",
            "single_relevant_vendor_topic",
        ) if proven else ("review", requirements)

    if platform_key == "disc_teaforum_org":
        proven = (
            "tea/teaware vendors" in body
            and "vendor news and self-promotion" in body
            and "create your own vendor or artisan topic" in body
            and "request to join the vendors or artisans groups" in body
            and "membership will remain pending until approved by an admin" in body
            and "provide links to your web site" in body
            and "only one thread per vendor" in body
            and "do not promote your company elsewhere on the forum via links or testimonials" in body
            and "bulk rooibos tea for sale" in body
            and "fairestcapeteacompany.com" in body
            and not paid_gate
        )
        return allow(
            "use_tea_teaware_vendors_board_only",
            "tea_or_teaware_goods_only",
            "vendor_group_admin_approval_required",
            "one_vendor_topic_only",
            "website_links_allowed_in_vendor_topic",
            "no_promotion_outside_vendor_board",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "disc_teachat_com":
        proven = (
            "tea vendor forum guidelines" in body
            and "vendors may advertise their teas in the tea vendor section of the forum only" in body
            and "new members with less than 10 posts and less than 30 days membership shall not post links" in body
            and "vendors may not place links or e-mail addresses on this forum leading to their businesses" in body
            and ("if you're a vendor reading this please reach out" in body or "if you’re a vendor reading this please reach out" in body)
            and not paid_gate
        )
        return allow(
            "use_tea_vendors_section_only",
            "tea_or_teaware_goods_only",
            "minimum_account_age_30_days",
            "minimum_messages_10",
            "website_links_only_after_maturity",
            "natural_participation_required",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "disc_mcarterbrown_com":
        proven = (
            "dealers forum" in body
            and "paintball dealer? having a special? let us know here" in body
            and "you will need to register before you can post" in body
            and "simply click the register link above to get started" in body
            and any("docsmachine.com" in link for link in links)
            and not paid_gate
        )
        return allow(
            "use_dealers_forum_only",
            "paintball_goods_only",
            "external_business_links_allowed_in_dealers_forum",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "disc_doityourselfchristmas_com":
        proven = (
            "vendors arena" in body
            and "a place for companies to show us their products and promote special sales" in body
            and "the purpose of this forum is for registered companies to promote products" in body
            and "animated holiday displays" in body
            and "supporting membership" in body
            and any(any(host in link for host in ("diyledexpress.com", "3kings.llc")) for link in links)
            and not paid_gate
        )
        return allow(
            "use_vendors_arena_only",
            "animated_holiday_display_goods_only",
            "registered_company_identity_required",
            "external_product_links_allowed_in_vendor_arena",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "disc_forums_deeperblue_com":
        proven = (
            "goods for sale" in body
            and "this forum is for commercial advertisements" in body
            and "the marketplace" in body
            and "do you have an item ready to be sold? post in here" in body
            and "register for a free account" in body
            and "absolutely free" in body
            and "self-promotion or commercial posts require prior permission" in body
            and any("makospearguns.com" in link for link in links)
        )
        return allow(
            "use_goods_for_sale_marketplace_only",
            "diving_freediving_or_spearfishing_goods_only",
            "commercial_permission_required_before_first_post",
            "external_product_link_allowed_in_goods_for_sale",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "disc_penturners_org":
        proven = (
            "all use of the marketplace will be free" in body
            and "personally owned or business sales are allowed" in body
            and "there will be no cost for a vendor forum" in body
            and "must be an iap member for at least one year" in body
            and "vendor forums" in body
            and "specials, tips, and general information from our vendors" in body
        )
        return allow(
            "use_iap_marketplace_vendor_forum_only",
            "minimum_account_age_365_days",
            "vendor_forum_approval_required",
            "penturning_goods_only",
            "english_content_required",
        ) if proven else ("review", requirements)

    if platform_key == "diyaudio_goods":
        proven = (
            "two free forums for advertising your wares" in body
            and "vendors bazaar" in body
            and "for commercial advertisements" in body
            and "currently free to post in the vendors bazaar" in body
        )
        return allow(
            "use_vendors_bazaar_only",
            "one_commercial_thread_per_vendor",
            "no_thread_bumping",
            "audio_goods_only",
            "english_content_required",
        ) if proven else ("review", requirements)

    return final, requirements
