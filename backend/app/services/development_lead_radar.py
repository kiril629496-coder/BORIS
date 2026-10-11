"""BORIS Development Lead Radar.

Builds a daily shortlist of 30-50 high-fit leads for custom SaaS/mobile/software
and feeds only verified public business emails into the canonical owner outreach
campaign. No direct SMTP, no CAPTCHA/auth bypass, no applicant-channel auto-send.
"""
from __future__ import annotations

import argparse
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import prospect_campaigns as campaigns
from app.services import prospecting
from app.services.outreach_discovery_policy import company_noise_reason
from app.services.exception_observability import observe_suppressed

OWNER_ID = 2
TARGET_DAILY = 150
MIN_DAILY = 30
HOT_TARGET_SERVICES = {5, 7, 8, 9, 14, 17, 18, 25, 26, 33}
CAMPAIGN_NAME = "Кирилл · разработка SaaS/App"
CAMPAIGN_NICHE = "Коммерческие Avito-ниши"
MIN_PAIN_EVIDENCE = 2
MIN_PAIN_SCORE = 55
SUBJECTS = "Где ПО может ускорить ваш бизнес?||Найдём одну зону роста?||Что в бизнесе можно ускорить?||Посчитаем без обязательств||Есть смысл автоматизировать один процесс?||Где бизнес теряет время?"
DEVELOPMENT_EMAIL_BODY = """Добрый день! Это Кирилл.

Давайте просто поймём, где разработка ПО может повысить скорость и эффективность вашего бизнеса.

Бесплатно определим одну зону роста и посчитаем смету. Это ни к чему вас не обязывает — дальше сами решите, есть ли смысл что-то внедрять.

На сайте — реальные проекты:
https://systems-ai-oko.ru/

Неактуально — ответьте «не интересно», больше писать не будем.

Кирилл
"""


# Короткие A/B-варианты: меняется только тема, смысл и оффер одинаковые.
DEVELOPMENT_EMAIL_VARIANTS = [
    {"label":"A","subject":"Где ПО может ускорить ваш бизнес?","body":DEVELOPMENT_EMAIL_BODY},
    {"label":"B","subject":"Найдём одну зону роста?","body":DEVELOPMENT_EMAIL_BODY},
    {"label":"C","subject":"Что в бизнесе можно ускорить?","body":DEVELOPMENT_EMAIL_BODY},
    {"label":"D","subject":"Посчитаем без обязательств","body":DEVELOPMENT_EMAIL_BODY},
    {"label":"E","subject":"Есть смысл автоматизировать один процесс?","body":DEVELOPMENT_EMAIL_BODY},
    {"label":"F","subject":"Где бизнес теряет время?","body":DEVELOPMENT_EMAIL_BODY},
]

VERTICALS = [
    ("сеть салонов красоты", "beauty", "запись клиентов, загрузка мастеров, CRM, повторные продажи и управление филиалами"),
    ("сеть автосервисов", "auto", "запись, диспетчеризация, статусы ремонта, CRM, уведомления и контроль загрузки"),
    ("ремонт бытовой техники", "service", "приём заявок, распределение мастеров, маршруты, фотоотчёты, оплаты и повторные обращения"),
    ("логистическая компания", "logistics", "заявки, диспетчеризация, водители, статусы, геолокация, документы и кабинет клиента"),
    ("управляющая компания недвижимость", "real_estate", "заявки жильцов, исполнители, платежи, уведомления, документы и контроль SLA"),
    ("оптовая компания дилеры", "wholesale", "B2B-кабинет, персональные цены, остатки, заказы, документы и интеграция с учётом"),
    ("медицинская сеть", "medical", "запись, кабинеты, уведомления, документы, филиалы и управленческая аналитика"),
    ("онлайн школа", "education", "личные кабинеты, подписки, программы, уведомления, оплаты и аналитика"),
    ("франшиза услуг", "franchise", "кабинет франчайзи, стандарты, обучение, заявки, KPI и контроль сети"),
    ("строительная компания", "construction", "лиды, сметы, объекты, исполнители, документы, фото и контроль этапов"),
]

DEVELOPMENT_BUYER_NICHES = (
    'Сети салонов красоты',
    'Сети косметологических клиник',
    'Сети стоматологий',
    'Медицинские сети',
    'Лаборатории / диагностические сети',
    'Сети автосервисов',
    'Шиномонтажные сети',
    'Сервисные компании с выездными мастерами',
    'Ремонт бытовой техники',
    'Клининговые компании',
    'Управляющие компании ЖКХ',
    'Управляющие компании коммерческой недвижимостью',
    'Девелоперы',
    'Строительные компании',
    'Ремонтные компании',
    'Компании по обслуживанию объектов',
    'Логистические компании',
    'Грузоперевозчики',
    'Курьерские компании',
    'Компании со своим автопарком',
    'Аренда спецтехники',
    'Таксопарки / корпоративный транспорт',
    'Оптовые компании',
    'Дистрибьюторы',
    'Производители с дилерской сетью',
    'Франшизы',
    'Федеральные сети',
    'Сети пунктов выдачи / обслуживания',
    'B2B-поставщики',
    'Компании с дилерами',
    'Компании с партнёрским кабинетом',
    'Онлайн-школы — ТОЛЬКО реальные образовательные компании',
    'Учебные центры',
    'Сети детских центров',
    'Частные образовательные сети',
    'HR / рекрутинговые агентства с большим потоком заявок',
    'Сервисные франшизы',
    'Facility management',
    'HoReCa сети',
    'Ресторанные сети',
    'Доставка еды с собственной инфраструктурой',
    'Производства с заказами / дилерами / личным кабинетом',
    'Компании с мобильным приложением, которое явно устарело',
    'Компании с несколькими филиалами и онлайн-записью',
    'Компании с несколькими филиалами + личным кабинетом',
    'Компании с большим числом исполнителей / мастеров',
    'Компании с диспетчеризацией',
    'Компании с маршрутами',
    'Компании с большим документооборотом',
    'Компании с ручной работой через Excel, если это подтверждено их собственным сайтом/материалами',
)

BUYER_SCENARIO_HINTS = ('сеть', 'сетей', 'филиал', 'франшиз', 'дилер', 'партнер', 'партнёр', 'кабинет', 'онлайн-запис', 'онлайн запис', 'заявк', 'приложен', 'курьер', 'водител', 'мастер', 'исполнител', 'диспетчер', 'статус заказ', 'маршрут', 'документ', 'платеж', 'erp', 'crm', 'excel', 'автопарк', 'оптов', 'b2b')

VERTICAL_HINTS = [
    (("салон", "beauty", "маникюр", "парикмах"), "салонный бизнес"),
    (("автосервис", "автомоб", "сто ", "car ", "automotive"), "автосервис / автомобильный бизнес"),
    (("логист", "достав", "курьер", "transport"), "логистика / доставка"),
    (("строит", "ремонт", "монтаж", "construction"), "строительство / сервисные работы"),
    (("клиник", "медицин", "стомат", "health"), "медицина"),
    (("школ", "обуч", "курс", "education"), "образование"),
    (("недвиж", "управляющ", "жк ", "property"), "недвижимость / управление объектами"),
    (("опт", "дилер", "производ", "wholesale"), "опт / производство"),
    (("franchise", "франшиз"), "франшиза / сеть"),
]

PLATFORM_HINTS = (
    "saas", "платформ", "мобильн", "приложен", "crm", "личн", "кабинет",
    "автоматизац", "интеграц", "api", "биллинг", "оплат", "дашборд",
    "несколько филиал", "сеть", "франшиз", "диспетчер", "workflow",
)

NOISE_HINTS = (
    "wordpress", "tilda", "верстк", "дизайнер", "логотип", "баннер",
    "devops engineer", "cloud infrastructure engineer", "senior backend engineer",
    "ищу работу", "резюме",
)

VERTICAL_POSITIVE = {
    "beauty": ("салон красоты", "beauty", "nail", "маникюр", "парикмах", "косметолог", "barber", "spa"),
    "auto": ("автосервис", "авто сервис", "сто ", "шиномонтаж", "техцентр", "car service", "auto service"),
    "service": ("ремонт бытовой техники", "сервисный центр", "холодильник", "стиральн", "посудомо", "телевизор", "appliance repair"),
    "logistics": ("логист", "грузоперевоз", "транспортная компания", "доставка груз", "курьер", "freight", "logistics"),
    "real_estate": ("управляющая компания", "управление недвиж", "жилищ", "property management", "ук "),
    "wholesale": ("оптов", "дилер", "производител", "дистрибьют", "wholesale"),
    "medical": ("клиник", "медицин", "стоматолог", "диагност", "medical", "clinic"),
    "education": ("онлайн школ", "образователь", "обучение", "academy", "school", "курс"),
    "franchise": ("франшиз", "franchise", "сеть "),
    "construction": ("строитель", "подряд", "монтаж", "ремонт квартир", "construction"),
}

CONTENT_NOISE = (
    "как открыть", "инструкция", "пошагов", "своими руками", "статья", "блог",
    "советы", "идеи", "фильм", "кинопоиск", "энциклоп", "википед", "рейтинг",
    "справочник", "каталог компаний", "ваканс", "резюме",
)

def _company_fit_relevant(company: dict, vertical_key: str) -> bool:
    hay = " ".join(str(company.get(k) or "") for k in ("name", "title", "snippet", "domain", "website", "url")).lower().replace("ё", "е")
    if company_noise_reason(company) or _is_dev_vendor(company):
        return False
    if any(x in hay for x in CONTENT_NOISE):
        return False
    positive = VERTICAL_POSITIVE.get(str(vertical_key), ())
    return bool(positive and any(x in hay for x in positive) and any(x in hay for x in BUYER_SCENARIO_HINTS))


def ensure_schema(db):
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text("UPDATE development_lead_shortlist SET lead_id=0 WHERE lead_id IS NULL"))
    db.execute(text("UPDATE development_lead_shortlist SET company_id=0 WHERE company_id IS NULL"))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def _service_date():
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _service_ids(raw):
    out = set()
    for x in (raw or []):
        try:
            out.add(int(x))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=278)
    return out


def _vertical(text_value: str) -> str:
    low = (text_value or "").lower().replace("ё", "е")
    for keys, label in VERTICAL_HINTS:
        if any(k in low for k in keys):
            return label
    return "бизнес-сервисы / цифровые процессы"


def _compact(value: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit].rstrip()


PAIN_EVIDENCE_RULES = (
    ("manual_process", 42, ("excel", "эксель", "вручную", "ручная обработка", "ручной учет", "ручной учёт"),
     "На сайте есть прямое указание на Excel или ручной этап процесса."),
    ("multi_location", 28, ("наши филиалы", "сеть филиалов", "сеть салонов", "сеть клиник", "сеть автосервисов", "открыли более"),
     "На сайте подтверждается собственная сеть, филиалы или работа в нескольких точках."),
    ("online_request", 22, ("онлайн-запись", "записаться", "оставить заявку", "оформить заказ", "заказать онлайн", "рассчитать стоимость"),
     "На сайте есть онлайн-запись, заявка или заказ."),
    ("client_cabinet", 28, ("личный кабинет", "кабинет клиента", "кабинет партнера", "кабинет партнёра", "войти в кабинет"),
     "На сайте есть личный кабинет или отдельный вход для клиента/партнёра."),
    ("partner_network", 28, ("дилер", "франшиз", "партнерская сеть", "партнёрская сеть", "стать партнером", "стать партнёром", "оптов"),
     "На сайте описана дилерская, партнёрская, франчайзинговая или оптовая модель."),
    ("mobile_app", 24, ("мобильное приложение", "app store", "google play", "rustore", "приложение для клиентов"),
     "На сайте есть мобильное приложение или ссылка на магазин приложений."),
    ("logistics_flow", 24, ("маршрут", "диспетчер", "доставка", "отслеживание", "статус заказа", "водител"),
     "На сайте описаны доставка, маршруты, статусы или диспетчеризация."),
    ("documents_payments", 18, ("документ", "счет", "счёт", "оплат", "акт", "накладн", "договор"),
     "На сайте явно присутствует контур документов или оплат."),
    ("staff_operations", 20, ("мастер", "исполнитель", "сотрудник", "специалист выезжает", "бригада"),
     "На сайте описана работа сотрудников или исполнителей с клиентскими задачами."),
)

EVIDENCE_LINK_HINTS = (
    "service", "services", "uslugi", "услуг", "client", "клиент", "partner", "партнер", "партнёр",
    "franchise", "франш", "dealer", "дилер", "delivery", "достав", "booking", "запис", "cabinet", "кабинет",
    "app", "прилож", "branches", "филиал", "network", "сеть", "contact", "контакт", "about", "о компании", "sitemap",
)


def _visible_site_text(body: str) -> str:
    soup = BeautifulSoup(str(body or ""), "lxml")
    for node in soup(["script", "style", "noscript", "svg", "template"]):
        node.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _evidence_excerpt(text_value: str, needle: str, limit: int = 180) -> str:
    low = text_value.lower().replace("ё", "е")
    idx = low.find(needle.lower().replace("ё", "е"))
    if idx < 0:
        return ""
    left = max(0, idx - 65)
    right = min(len(text_value), idx + len(needle) + 95)
    return _compact(text_value[left:right], limit)


def _extract_site_evidence(page_url: str, body: str) -> list[dict]:
    text_value = _visible_site_text(body)
    low = text_value.lower().replace("ё", "е")
    evidence = []
    for kind, strength, needles, fact in PAIN_EVIDENCE_RULES:
        quote = ''
        for needle in needles:
            normalized = needle.replace('ё', 'е')
            if normalized == 'акт':
                pattern = r'\bакт(?:ы|ов|ами|ах|ом|а)?\b'
            elif normalized == 'счет':
                pattern = r'\bсчет(?:а|ов|ами|ах|ом|у)?\b'
            else:
                pattern = r'\b' + re.escape(normalized)
            for match in re.finditer(pattern, low):
                context = low[max(0, match.start()-65):match.end()+95]
                if kind in {'online_request', 'staff_operations'} and any(x in context for x in ('собеседован', 'ваканси', 'соискател')):
                    continue
                if kind == 'staff_operations' and not any(x in context for x in ('клиент', 'заявк', 'заказ', 'выезд', 'объект', 'запис', 'услуг', 'визит', 'процедур')):
                    continue
                if kind == 'multi_location' and normalized == 'открыли более' and not re.search(r'\d+.{0,25}(?:студи|филиал|клиник|точ|салон|цент|магазин)', context):
                    continue
                quote = _compact(text_value[max(0, match.start()-65):match.end()+95], 180)
                break
            if quote:
                break
        if not quote:
            continue
        evidence.append({
            "kind": kind,
            "fact": fact,
            "quote": quote,
            "source_url": page_url,
            "strength": strength,
            "source_type": "company_site",
        })
    return evidence


def _evidence_links(base_url: str, body: str, limit: int = 3) -> list[str]:
    soup = BeautifulSoup(str(body or ""), "lxml")
    scored = []
    seen = set()
    for a in soup.find_all("a", href=True):
        raw = str(a.get("href") or "").strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, raw)
        if url in seen or not prospecting.same_domain(base_url, url):
            continue
        seen.add(url)
        hay = (url + " " + a.get_text(" ", strip=True)).lower().replace("ё", "е")
        score = sum(1 for hint in EVIDENCE_LINK_HINTS if hint.replace("ё", "е") in hay)
        if score:
            scored.append((score, len(url), url))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:max(0, int(limit))]]


def _pain_hypothesis(evidence: list[dict], vertical_label: str) -> tuple[str, str]:
    kinds = {str(x.get("kind") or "") for x in evidence}
    if "manual_process" in kinds:
        return (
            "часть процесса может зависеть от ручной обработки и терять скорость при росте объёма",
            "автоматизация ручного участка с единым статусом, ответственными и аналитикой",
        )
    if {"multi_location", "online_request"} <= kinds:
        return (
            "при нескольких точках и онлайн-заявках может быть сложно держать единое распределение, загрузку и контроль ответа",
            "единый контур заявок, филиалов/исполнителей, статусов и управленческой аналитики",
        )
    if "partner_network" in kinds and ("client_cabinet" in kinds or "documents_payments" in kinds):
        return (
            "партнёрский контур может требовать единого управления заказами, ценами, документами и статусами",
            "B2B-кабинет партнёра/дилера с заказами, документами и интеграциями",
        )
    if "logistics_flow" in kinds and "online_request" in kinds:
        return (
            "между заявкой, диспетчеризацией, исполнением и информированием клиента может возникать разрыв",
            "система заявка → диспетчеризация → статус → документы → кабинет клиента",
        )
    if "client_cabinet" in kinds and "online_request" in kinds:
        return (
            "заявка и дальнейшая работа клиента могут жить в разных контурах и требовать ручной синхронизации",
            "единый клиентский кабинет/CRM-контур от заявки до результата и повторного обращения",
        )
    if "multi_location" in kinds:
        return (
            "масштаб сети может усложнять единый контроль заявок, сотрудников, статусов и показателей по точкам",
            "единая система для сети с ролями, филиалами, KPI, заявками и аналитикой",
        )
    return (
        f"в сегменте «{vertical_label}» несколько цифровых точек процесса могут требовать ручной синхронизации между собой",
        "сначала разобрать текущий процесс и автоматизировать только подтверждённый узкий участок",
    )


def _score_pain_evidence(evidence: list[dict]) -> int:
    by_kind = {}
    for item in evidence:
        kind = str(item.get("kind") or "")
        by_kind[kind] = max(int(item.get("strength") or 0), int(by_kind.get(kind) or 0))
    strengths = sorted(by_kind.values(), reverse=True)[:3]
    score = sum(strengths)
    if len(by_kind) >= 3:
        score += 8
    if "manual_process" in by_kind:
        score += 8
    return min(100, score)


def _scan_company_evidence(company: dict, vertical_label: str, max_pages: int = 4) -> dict:
    website = str(company.get("website") or "").strip()
    if company_noise_reason(company) or _is_dev_vendor(company):
        return {"analysis_status":"rejected_noise", "reason":"non_buyer", "evidence":[], "pain_score":0}
    if not website:
        return {"analysis_status": "scan_failed", "reason": "website_missing", "evidence": [], "pain_score": 0}
    session = requests.Session()
    session.headers.update({"User-Agent": prospecting.USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    queue = [website]
    visited = set()
    evidence = []
    errors = []
    while queue and len(visited) < max(1, min(int(max_pages), 5)):
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            prospecting.assert_public_host(url)
            final_url, body, _size = prospecting.fetch(session, url)
            if not prospecting.same_domain(website, final_url) or company_noise_reason({'website': final_url}):
                errors.append('non_corporate_redirect')
                continue
            evidence.extend(_extract_site_evidence(final_url, body))
            if len(visited) == 1:
                queue.extend(x for x in _evidence_links(final_url, body, limit=8) if x not in visited)
        except Exception as exc:
            errors.append(type(exc).__name__)
    uniq = {}
    for item in evidence:
        key = str(item.get("kind") or "")
        current = uniq.get(key)
        if current is None or int(item.get("strength") or 0) > int(current.get("strength") or 0):
            uniq[key] = item
    evidence = sorted(uniq.values(), key=lambda x: (-int(x.get("strength") or 0), str(x.get("kind") or "")))
    score = _score_pain_evidence(evidence)
    site_count = sum(1 for x in evidence if x.get("source_type") == "company_site")
    ready = len(evidence) >= MIN_PAIN_EVIDENCE and site_count >= MIN_PAIN_EVIDENCE and score >= MIN_PAIN_SCORE
    hypothesis, offer_angle = _pain_hypothesis(evidence, vertical_label)
    return {
        "analysis_status": "pass" if ready else "insufficient_evidence",
        "reason": "" if ready else (";".join(errors[-2:]) if errors and not evidence else "not_enough_confirmed_site_signals"),
        "evidence": evidence[:6],
        "evidence_count": len(evidence),
        "site_evidence_count": site_count,
        "pain_score": score,
        "pain_confidence": "high" if score >= 75 and len(evidence) >= 3 else ("medium" if ready else "low"),
        "pain_hypothesis": hypothesis,
        "offer_angle": offer_angle,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "pages_analyzed": len(visited),
    }


def _brief_outreach_ready(brief: dict | None) -> bool:
    if not isinstance(brief, dict):
        return False
    return bool(
        brief.get("outreach_gate") == "pass"
        and int(brief.get("evidence_count") or 0) >= MIN_PAIN_EVIDENCE
        and int(brief.get("site_evidence_count") or 0) >= MIN_PAIN_EVIDENCE
        and int(brief.get("pain_score") or 0) >= MIN_PAIN_SCORE
    )


def _check_range(row, ids):
    lo = int(row.get("budget_min_rub") or 0)
    hi = int(row.get("budget_max_rub") or 0)
    if {5, 9} <= ids or (5 in ids and ids.intersection({7, 8, 9})):
        base_lo, base_hi = 350000, 900000
    elif ids.intersection({7, 8, 9}):
        base_lo, base_hi = 250000, 700000
    elif ids.intersection({5, 14, 33}):
        base_lo, base_hi = 250000, 700000
    elif ids.intersection({26, 17, 18, 25}):
        base_lo, base_hi = 150000, 500000
    else:
        base_lo, base_hi = 120000, 400000
    if hi >= 150000:
        base_hi = max(base_hi, min(2000000, hi))
    if lo >= 100000:
        base_lo = max(base_lo, min(lo, base_hi))
    return base_lo, base_hi


def _hot_fit(row):
    ids = _service_ids(row.get("service_ids"))
    if not ids.intersection(HOT_TARGET_SERVICES):
        return -999
    blob = " ".join([str(row.get("title") or ""), str(row.get("body") or ""), str(row.get("company_name") or "")]).lower().replace("ё", "е")
    core_custom = {5, 7, 8, 9, 14, 26, 33}
    secondary = {17, 18, 25}
    strong_terms = (
        "saas", "платформ", "web app", "веб-прилож", "мобильн", "приложен",
        "crm", "срм", "личный кабинет", "личного кабинета", " лк ", "маркетплейс",
        "автоматизац", "информационн", "система", "портал", "диспетчер",
        "booking", "кабинет клиента", "кабинет дилер", "франшиз", "филиал",
    )
    small_only_terms = (
        "простой telegram-бот", "простого telegram-бот", "telegram bot", "телеграм-бот",
        "доработка сайта на wp", "wordpress", "tilda", "создание инфраструктуры",
        "настройка сервера", "devops", "верстка", "верстк",
    )
    strong_count = sum(1 for x in strong_terms if x in blob)
    business_process = any(x in blob for x in (
        "продаж", "заявк", "клиент", "заказ", "мастер", "сотрудник", "филиал",
        "дилер", "водител", "логист", "запис", "оплат", "документ", "склад",
        "учет", "учёт", "workflow", "business", "service", "cleaning", "automotive",
    ))
    explicit_mobile = bool(ids.intersection({7, 8, 9}))
    explicit_business_core = bool(ids.intersection({14, 33}))
    automation_fit = 26 in ids and business_process
    saas_fit = 5 in ids and (strong_count >= 1 or len(ids.intersection(HOT_TARGET_SERVICES)) >= 2)
    secondary_fit = len(ids.intersection(secondary)) >= 2 and strong_count >= 2
    if not (explicit_mobile or explicit_business_core or automation_fit or saas_fit or secondary_fit):
        return -999
    if any(x in blob for x in small_only_terms) and strong_count < 2 and not explicit_mobile and not explicit_business_core:
        return -999
    # Generic staffing ads are useful signals in the broad radar but not in this
    # high-ticket shortlist unless the product/business system is explicit.
    if re.search(r"\b(fullstack|full-stack|backend|frontend)\b", blob) and strong_count < 1 and not explicit_mobile:
        return -999
    score = int(row.get("score") or 0)
    score += min(18, sum(4 for x in PLATFORM_HINTS if x in blob))
    if len(ids.intersection(HOT_TARGET_SERVICES)) >= 2:
        score += 8
    if row.get("company_name") or row.get("company_domain"):
        score += 8
    hi = int(row.get("budget_max_rub") or 0)
    if hi >= 1000000:
        score += 14
    elif hi >= 500000:
        score += 10
    elif hi >= 250000:
        score += 6
    elif hi and hi < 100000:
        score -= 12
    if str(row.get("signal_family") or "") == "ready_brief":
        score += 10
    if str(row.get("signal_family") or "") in {"problem", "business_gap"}:
        score += 8
    if str(row.get("signal_family") or "") == "hiring":
        score -= 5
    source = str(row.get("source_key") or "")
    if source == "eu_ted":
        score -= 24
    elif source in {"apple_app_store", "github_issues", "hacker_news_show"}:
        score -= 10
    elif source == "freelancer":
        score -= 6
    elif source in {"freelance", "new_companies", "franchises", "news", "boris_company_sites", "boris_cold_b2b"}:
        score += 8
    cyr = len(re.findall(r"[а-яА-ЯёЁ]", blob))
    lat = len(re.findall(r"[a-zA-Z]", blob))
    if cyr >= 20:
        score += 6
    elif lat > cyr * 4 and source in {"eu_ted", "freelancer"}:
        score -= 6
    if any(x in blob for x in NOISE_HINTS) and not any(x in blob for x in ("saas", "приложен", "платформ", "crm", "автоматизац")):
        score -= 18
    return max(0, min(100, score))


def _offering_names(db, ids):
    if not ids:
        return []
    rows = db.execute(text("SELECT offering_code,name FROM lead_radar_offerings WHERE owner_user_id=:o AND enabled=true"), {"o": OWNER_ID}).mappings().all()
    mapping = {int(r["offering_code"]): str(r["name"]) for r in rows}
    return [mapping[x] for x in sorted(ids) if x in mapping][:4]


def _hot_brief(db, row, fit_score):
    ids = _service_ids(row.get("service_ids"))
    services = _offering_names(db, ids)
    vertical = _vertical(" ".join([str(row.get("title") or ""), str(row.get("body") or "")]))
    title = _compact(row.get("title") or "", 180)
    company = _compact(row.get("company_name") or "", 100) or f"Заказчик: {title[:70]}"
    signal = str(row.get("signal_family") or "")
    if signal == "ready_brief":
        problem = f"Прямой запрос на разработку: {title}"
    elif signal == "hiring":
        problem = f"Компания усиливает IT-команду; это сигнал активного внутреннего проекта: {title}"
    elif signal in {"problem", "business_gap"}:
        problem = f"Открытый сигнал проблемы/разрыва в процессе: {title}"
    elif signal in {"money", "launch"}:
        problem = f"Компания растёт или запускает продукт: {title}"
    else:
        problem = title
    why = "Задача затрагивает несколько модулей/интеграций и бизнес-логику; готовый коробочный сервис с высокой вероятностью закроет только часть процесса."
    if len(ids.intersection(HOT_TARGET_SERVICES)) <= 1:
        why = "Есть прямой запрос на разработку или автоматизацию под конкретный процесс; собственное решение позволяет не подгонять процесс под ограничения готового сервиса."
    offer = "MVP: " + ", ".join(services or ["SaaS / мобильное приложение / автоматизация"]) + ". Сначала фиксируем 5–10 ключевых сценариев, затем даём срок и этапную смету."
    lo, hi = _check_range(row, ids)
    first = (
        f"Добрый день! Увидел вашу задачу «{title[:110]}». "
        f"Мы делаем SaaS, мобильные приложения и автоматизацию под процессы бизнеса. "
        f"По этой задаче я бы начал с короткого MVP и зафиксировал интеграции/роли до разработки. "
        f"Могу прислать структуру MVP, срок и вилку стоимости."
    )
    return {
        "company": company,
        "what_company_does": f"Сегмент: {vertical}. " + ("Компания определена по открытому источнику." if row.get("company_name") else "Название компании в источнике не раскрыто."),
        "problem": problem,
        "why_custom_saas_app": why,
        "what_we_offer": offer,
        "estimated_check_rub": {"min": lo, "max": hi},
        "decision_maker": row.get("decision_maker_hint") or "Собственник / руководитель проекта / директор по продукту",
        "contact": row.get("contact_hint") or row.get("url") or f"Источник: {row.get('source_key')}",
        "personal_first_message": first,
        "source": row.get("source_key"),
        "source_url": row.get("url"),
        "lead_score": int(row.get("score") or 0),
        "fit_score": fit_score,
        "service_ids": sorted(ids),
    }


def _ensure_campaign():
    db = SessionLocal()
    try:
        campaigns.ensure_schema(db)
        row = db.execute(text("SELECT id,status,desired_status,status_reason FROM prospect_campaigns WHERE owner_id=:o AND name=:n ORDER BY id DESC LIMIT 1"), {"o": OWNER_ID, "n": CAMPAIGN_NAME}).mappings().first()
    finally:
        db.close()
    if row:
        cid = int(row["id"])
        db = SessionLocal()
        try:
            # Development outreach is intentionally isolated from BORIS product
            # outreach. Moving account_id away from __owner_outreach__ also
            # prevents the BORIS exact-body DB lock from rewriting this copy.
            db.execute(text("""
              UPDATE prospect_campaigns
                 SET account_id=NULL,
                     niche=:niche,
                     regions='[]'::jsonb,
                     subject_template=:subject,
                     body_template=:body,
                     ab_variants=CAST(:ab AS JSONB),
                     attachment_path=NULL,
                     daily_limit=15,
                     per_domain_daily_limit=1,
                     min_quality_score=60,
                     updated_at=NOW()
               WHERE id=:c AND owner_id=:o
            """), {"c": cid, "o": OWNER_ID, "niche": CAMPAIGN_NICHE,
                   "subject": SUBJECTS, "body": DEVELOPMENT_EMAIL_BODY,
                   "ab": json.dumps(DEVELOPMENT_EMAIL_VARIANTS, ensure_ascii=False)})
            db.commit()
        finally:
            db.close()
        # Never undo an explicit/safety pause. The radar may prepare evidence while
        # paused, but only a deliberate desired_status='active' is allowed to send.
        if row["status"] != "active" and str(row.get("desired_status") or "") == "active" and not row.get("status_reason"):
            campaigns.set_status(OWNER_ID, cid, "active", source="development_radar")
        return cid
    db = SessionLocal()
    try:
        mailbox = db.execute(text("SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND account_id='__owner_outreach__' AND status='active' ORDER BY id DESC LIMIT 1"), {"o": OWNER_ID}).scalar()
    finally:
        db.close()
    if not mailbox:
        raise RuntimeError("OWNER_OUTREACH_MAILBOX_NOT_READY")
    cid = campaigns.create_campaign(
        OWNER_ID,
        name=CAMPAIGN_NAME,
        niche=CAMPAIGN_NICHE,
        regions=[],
        subject_template=SUBJECTS,
        body_template=DEVELOPMENT_EMAIL_BODY,
        ab_variants=DEVELOPMENT_EMAIL_VARIANTS,
        daily_limit=15,
        per_domain_daily_limit=1,
        min_quality_score=60,
        account_id=None,
        mailbox_id=int(mailbox),
    )
    campaigns.set_status(OWNER_ID, cid, "active", source="development_radar")
    return cid


def _vertical_key_from_search_query(value: str) -> str:
    low = str(value or "").lower().replace("ё", "е")
    for niche, key, _pain in VERTICALS:
        if niche.lower().replace("ё", "е") in low:
            return key
    return ""


def _verified_email(db, company_id):
    row = db.execute(text("""
      SELECT pct.id contact_id,pct.normalized_value email,pct.quality_score
      FROM prospect_contacts pct
      WHERE pct.company_id=:c AND pct.kind='email' AND pct.selected_for_outreach=true
        AND COALESCE(pct.quality_score,0)>=60
        AND NOT EXISTS(SELECT 1 FROM prospect_suppression s WHERE s.kind='email' AND s.normalized_value=pct.normalized_value)
      ORDER BY pct.quality_score DESC,pct.id LIMIT 1
    """), {"c": int(company_id)}).mappings().first()
    return dict(row) if row else None


def _already_owner_contacted(db, email):
    """Dedupe inside the development funnel only.

    Unsent ready members must be reanalyzed after hardening; canonical member
    uniqueness and the send-time family guard prevent duplicate initial mail.
    BORIS-product outreach is a separate offer/funnel. It must never rewrite or
    suppress development solely because another BORIS campaign used that address.
    """
    return bool(db.execute(text("""
      SELECT 1
      FROM prospect_campaign_members m
      JOIN prospect_campaigns c ON c.id=m.campaign_id
      WHERE c.owner_id=:o
        AND (c.name=:name OR lower(coalesce(c.niche,'')) LIKE '%заказн%разработ%')
        AND lower(m.email)=lower(:e)
        AND (m.sent_at IS NOT NULL OR m.status IN ('queued','sent'))
      LIMIT 1
    """), {"o": OWNER_ID, "e": email, "name": CAMPAIGN_NAME}).first())


DEV_POOL_POSITIVE = (
    "development-fit", "сеть салон", "сеть студ", "федеральная сеть", "франшиз",
    "логист", "аренда спецтехники", "автопарк", "диспетчер", "сеть автосервис",
    "b2b", "дистрибьют", "личный кабинет", "кабинет дилер", "управляющая компания",
    "медицинская сеть", "сеть клиник", "стоматолог", "онлайн школ", "образователь",
    "оптовая компания", "оптом", "маршрут", "сеть пунктов", "200+ точек", "500+ партнер",
)

DEV_POOL_NEGATIVE = (
    "сыпуч", "песок", "щеб", "грунт", "асфальтовая крош", "неруд", "бетон",
    "тротуарная плит", "кровельные работы", "строительство забор", "фильм",
    "статья", "справочник", "каталог компаний", "rutube", "pulscen", "nashaspravka",
)

# Companies that sell software development themselves are partners/competitors,
# not cold-email buyers for the development campaign. Keep this gate fail-closed
# for automated discovery; explicitly curated buyer companies are evaluated by
# their curated development-fit reason instead.
DEV_VENDOR_HINTS = (
    "заказная разработка", "разработка по на заказ", "разработка программного обеспечения на заказ",
    "разработка сайтов", "разработка мобильных приложений", "разработка веб-приложений",
    "веб-разработка", "web development", "software development", "custom software",
    "it-компания", "ит-компания", "it агентство", "ит-агентство", "digital-агентство",
    "digital агентство", "digital-интегратор", "студия разработки", "product studio",
    "digital product studio", "разработка цифровых продуктов", "разрабатываем сайты",
    "разрабатываем мобильные", "разработка под ключ", "обсудить проект",
)

def _is_dev_vendor(company: dict) -> bool:
    source = str(company.get("source") or "").strip().lower()
    hay = " ".join(str(company.get(k) or "") for k in (
        "name", "title", "snippet", "domain", "website", "url", "search_query"
    )).lower().replace("ё", "е")
    # Old contaminated discovery rows were created by using our own offer as
    # the search niche. They must never re-enter campaign 22.
    if CAMPAIGN_NICHE.lower().replace("ё", "е") in hay:
        return True
    return any(x in hay for x in DEV_VENDOR_HINTS)


def _existing_company_fit_score(company: dict) -> tuple[int, str, str]:
    """High-confidence reusable pool when public search engines are degraded."""
    source = str(company.get("source") or "").lower()
    hay = " ".join(str(company.get(k) or "") for k in ("name", "domain", "search_query", "website")).lower().replace("ё", "е")
    if company_noise_reason(company) or _is_dev_vendor(company):
        return 0, "", ""
    if any(x in hay for x in DEV_POOL_NEGATIVE):
        return 0, "", ""
    if source == "development_curated":
        base = 88
    else:
        hits = [x for x in DEV_POOL_POSITIVE if x in hay]
        if not hits:
            return 0, "", ""
        base = 72 + min(16, len(hits) * 4)
    # Require at least a real company domain and website. Aggregators/content
    # pages must never become email-ready just because an address was scraped.
    domain = str(company.get("domain") or "").strip().lower()
    website = str(company.get("website") or "").strip()
    if not domain or not website:
        return 0, "", ""
    if source != "development_curated" and any(x in domain for x in ("rutube.ru", "kinopoisk.ru", "pulscen.ru", "nashaspravka.ru")):
        return 0, "", ""
    vertical = _vertical(hay)
    if "спецтех" in hay or "автопарк" in hay or "диспетчер" in hay:
        pain = "заявки, диспетчеризация, загрузка техники/исполнителей, статусы, документы и кабинет клиента"
    elif "логист" in hay or "маршрут" in hay:
        pain = "заявки, маршруты, статусы, документы, исполнители и кабинет клиента"
    elif "b2b" in hay or "дистрибьют" in hay or "оптом" in hay or "оптов" in hay:
        pain = "B2B-заказы, персональные цены, остатки, документы, кабинет партнёра и интеграции"
    elif "салон" in hay or "студи" in hay or "beauty" in hay or "барберш" in hay:
        pain = "запись клиентов, филиалы, мастера, CRM, повторные продажи и клиентское приложение"
    elif "управляющая" in hay or "недвиж" in hay:
        pain = "заявки, объекты, исполнители, документы, платежи и личный кабинет клиента"
    elif "клиник" in hay or "медицин" in hay or "стомат" in hay:
        pain = "запись, филиалы, кабинеты, уведомления, документы и управленческая аналитика"
    else:
        pain = "единый процесс заявок, сотрудников, клиентов, уведомлений, аналитики и интеграций"
    return min(100, base), vertical, pain


def _enrich_curated_missing_contacts(max_companies=8):
    """Bounded no-AI enrichment for pre-qualified development buyers.

    First reuses the canonical crawler with a deeper page budget. If the site
    still has no selected business email, inspect a small set of same-host
    sitemap/contact pages. No search engine, login, CAPTCHA, or paid API.
    """
    import requests

    db = SessionLocal()
    try:
        rows = [dict(r) for r in db.execute(text("""
          SELECT c.id,c.name,c.website,c.domain
          FROM prospect_companies c
          WHERE c.owner_id=:o AND c.source='development_curated'
            AND COALESCE(c.website,'')<>''
            AND NOT EXISTS(
              SELECT 1 FROM prospect_contacts p
              WHERE p.company_id=c.id AND p.kind='email'
                AND p.selected_for_outreach=true AND COALESCE(p.quality_score,0)>=60
            )
            AND NOT EXISTS(
              SELECT 1 FROM prospect_runs pr
              WHERE pr.owner_id=:o AND pr.company_id=c.id
                AND pr.started_at>=NOW()-INTERVAL '24 hours'
            )
          ORDER BY c.id
          LIMIT :lim
        """), {"o": OWNER_ID, "lim": max(1, min(int(max_companies), 20))}).mappings().all()]
    finally:
        db.close()

    stats={"companies":0,"canonical_found":0,"sitemap_found":0,"emails_saved":0,"failed":0}
    for company in rows:
        cid=int(company["id"]); stats["companies"]+=1
        # Canonical deeper crawl first.
        try:
            db=SessionLocal()
            try:
                result=prospecting.run_company(db,OWNER_ID,cid,max_pages=12)
                prospecting.rank_company_contacts(db,cid); db.commit()
                if result.get("emails"):
                    stats["canonical_found"]+=1
            finally:
                db.close()
        except Exception:
            stats["failed"]+=1

        db=SessionLocal()
        try:
            verified=bool(db.execute(text("""SELECT 1 FROM prospect_contacts
              WHERE company_id=:c AND kind='email' AND selected_for_outreach=true
                AND COALESCE(quality_score,0)>=60 LIMIT 1"""),{"c":cid}).first())
        finally:
            db.close()
        if verified:
            continue

        base=str(company.get("website") or "").strip()
        parsed=urlparse(base)
        if parsed.scheme not in {"http","https"} or not parsed.hostname:
            continue
        root=f"{parsed.scheme}://{parsed.netloc}/"
        try:
            prospecting.assert_public_host(root)
        except Exception:
            continue
        session=requests.Session()
        session.headers.update({"User-Agent":prospecting.USER_AGENT,"Accept":"text/html,application/xhtml+xml,application/xml,text/xml,text/plain"})
        sitemap_urls=[urljoin(root,"sitemap.xml")]
        try:
            prospecting.assert_public_host(urljoin(root,"robots.txt"))
            rr=session.get(urljoin(root,"robots.txt"),timeout=prospecting.TIMEOUT,allow_redirects=True)
            if rr.status_code==200 and len(rr.content)<=500_000:
                for line in rr.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        u=line.split(":",1)[1].strip()
                        if u: sitemap_urls.append(u)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=708)

        locs=[]
        seen_maps=set()
        for sm in sitemap_urls[:4]:
            if sm in seen_maps: continue
            seen_maps.add(sm)
            try:
                prospecting.assert_public_host(sm)
                sr=session.get(sm,timeout=prospecting.TIMEOUT,allow_redirects=True)
                if sr.status_code!=200 or len(sr.content)>2_000_000: continue
                these=re.findall(r"<loc>\s*(.*?)\s*</loc>",sr.text,re.I|re.S)
                child=[x.strip() for x in these if x.strip().lower().endswith(".xml")]
                locs.extend(x.strip() for x in these if not x.strip().lower().endswith(".xml"))
                for ch in child[:4]:
                    try:
                        prospecting.assert_public_host(ch)
                        cr=session.get(ch,timeout=prospecting.TIMEOUT,allow_redirects=True)
                        if cr.status_code==200 and len(cr.content)<=2_000_000:
                            locs.extend(x.strip() for x in re.findall(r"<loc>\s*(.*?)\s*</loc>",cr.text,re.I|re.S))
                    except Exception:
                        continue
            except Exception:
                continue

        hints=("contact","kontakt","kontakty","контакт","rekviz","requis","franch","франш","partner","about","company","corporate","b2b")
        candidates=[]
        for path in (
            "/contacts/","/contact/","/kontakty/","/about/contacts/","/company/contacts/",
            "/requisites/","/rekvizity/","/franchise/","/partners/","/about/",
        ):
            candidates.append(urljoin(root,path.lstrip('/')))
        # Prefer short, contact-like sitemap paths; never scan the whole sitemap.
        sitemap_candidates=[u for u in locs if any(h in u.lower() for h in hints)]
        sitemap_candidates=sorted(set(sitemap_candidates),key=lambda u:(len(urlparse(u).path),len(u)))[:10]
        candidates.extend(sitemap_candidates)
        seen=set(); saved_before=0
        db=SessionLocal()
        try:
            for u in candidates[:16]:
                if u in seen: continue
                seen.add(u)
                try:
                    if not prospecting.same_domain(root,u):
                        continue
                    final_url,body,_size=prospecting.fetch(session,u)
                    emails,_phones=prospecting.extract_contacts(final_url,body)
                except Exception:
                    continue
                for email in emails:
                    if prospecting.save_contact(db,cid,'email',email,final_url,confidence=0.97):
                        saved_before+=1
            prospecting.rank_company_contacts(db,cid); db.commit()
            verified=bool(db.execute(text("""SELECT 1 FROM prospect_contacts
              WHERE company_id=:c AND kind='email' AND selected_for_outreach=true
                AND COALESCE(quality_score,0)>=60 LIMIT 1"""),{"c":cid}).first())
        finally:
            db.close()
        if saved_before:
            stats["emails_saved"]+=saved_before
        if verified:
            stats["sitemap_found"]+=1
    return stats


def _existing_verified_company_fit(max_companies=30):
    """Reuse only already-verified public corporate contacts with strong dev fit."""
    db = SessionLocal()
    try:
        rows = [dict(r) for r in db.execute(text("""
          SELECT DISTINCT ON (pc.id)
                 pc.id,pc.name,pc.city,pc.website,pc.domain,pc.source,pc.search_query,pc.discovery_score,
                 pct.id contact_id,pct.normalized_value email,pct.quality_score
          FROM prospect_companies pc
          JOIN prospect_contacts pct ON pct.company_id=pc.id
           AND pct.kind='email' AND pct.selected_for_outreach=true
           AND COALESCE(pct.quality_score,0)>=60
          WHERE pc.owner_id=:o
            AND NOT EXISTS(
              SELECT 1 FROM prospect_suppression ss
              WHERE ss.kind='email' AND ss.normalized_value=pct.normalized_value
            )
          ORDER BY pc.id,pct.quality_score DESC,pct.id
          LIMIT 1200
        """), {"o": OWNER_ID}).mappings().all()]
        found=[]
        for company in rows:
            score, vertical, pain = _existing_company_fit_score(company)
            if score < 72:
                continue
            email = str(company.get("email") or "").strip().lower()
            if not email or _already_owner_contacted(db,email):
                continue
            found.append({
                "company": company,
                "vertical": vertical,
                "pain": pain,
                "fit_score": min(100, score + min(8, max(0, int(company.get("quality_score") or 0)-60)//5)),
                "email": {"contact_id": int(company["contact_id"]), "email": email, "quality_score": int(company.get("quality_score") or 0)},
            })
        uniq={}
        for item in found:
            key=(str(item["company"].get("domain") or "") or item["email"]["email"]).lower()
            prev=uniq.get(key)
            if prev is None or item["fit_score"]>prev["fit_score"]:
                uniq[key]=item
        return sorted(uniq.values(),key=lambda x:(-x["fit_score"],-int(x["email"].get("quality_score") or 0),int(x["company"]["id"])))[:max_companies]
    finally:
        db.close()


def _company_brief(company, vertical_label, process_pain, fit_score, email, analysis=None):
    name = _compact(company.get("name") or company.get("domain") or "Компания", 100)
    analysis = dict(analysis or {})
    evidence = list(analysis.get("evidence") or [])
    ready = (
        analysis.get("analysis_status") == "pass"
        and len(evidence) >= MIN_PAIN_EVIDENCE
        and int(analysis.get("site_evidence_count") or 0) >= MIN_PAIN_EVIDENCE
        and int(analysis.get("pain_score") or 0) >= MIN_PAIN_SCORE
    )
    facts = [str(x.get("fact") or "").strip().rstrip(".") for x in evidence[:2] if str(x.get("fact") or "").strip()]
    hypothesis = str(analysis.get("pain_hypothesis") or "").strip()
    offer_angle = str(analysis.get("offer_angle") or "").strip()
    if ready and len(facts) >= 2:
        first = (
            f"Добрый день! На сайте {name} вижу два конкретных сигнала: {facts[0].lower()}; {facts[1].lower()}. "
            f"Из этого есть гипотеза: {hypothesis}. Не утверждаю, что проблема у вас уже есть — хочу проверить, актуальна ли она. "
            f"Если да, могу бесплатно набросать первый вариант решения: {offer_angle}."
        )
    else:
        first = ""
    return {
        "company": name,
        "what_company_does": f"Компания в сегменте «{vertical_label}». Факты ниже взяты с публичного сайта компании.",
        "problem": (f"Проверяемая гипотеза: {hypothesis}." if hypothesis else "Гипотеза не сформирована: недостаточно подтверждённых фактов."),
        "why_custom_saas_app": (f"Угол предложения: {offer_angle}." if offer_angle else "Не выбран: сначала нужен доказательный анализ процесса."),
        "what_we_offer": (f"Разобрать подтверждённый процесс и предложить MVP: {offer_angle}." if ready else "Письмо не отправлять до появления минимум двух подтверждённых сигналов."),
        "estimated_check_rub": {"min": 300000, "max": 1200000},
        "decision_maker": "Собственник / генеральный директор / операционный директор / руководитель цифровизации",
        "contact": email,
        "personal_first_message": first,
        "source": "company_fit",
        "source_url": company.get("website"),
        "fit_score": fit_score,
        "vertical_prior": process_pain,
        "evidence": evidence,
        "evidence_count": int(analysis.get("evidence_count") or len(evidence)),
        "site_evidence_count": int(analysis.get("site_evidence_count") or 0),
        "pain_score": int(analysis.get("pain_score") or 0),
        "pain_confidence": str(analysis.get("pain_confidence") or "low"),
        "pain_hypothesis": hypothesis,
        "offer_angle": offer_angle,
        "analysis_status": str(analysis.get("analysis_status") or "insufficient_evidence"),
        "analysis_reason": str(analysis.get("reason") or ""),
        "pages_analyzed": int(analysis.get("pages_analyzed") or 0),
        "analyzed_at": analysis.get("analyzed_at"),
        "outreach_gate": "pass" if ready else "blocked",
    }


def _discover_company_fit(max_companies=16):
    today = _service_date()
    # Rotate niches so the same four web searches are not repeated every day.
    offset = (today.toordinal() * 4 + datetime.now(timezone.utc).hour) % len(DEVELOPMENT_BUYER_NICHES)
    chosen = []
    for i in range(4):
        niche = DEVELOPMENT_BUYER_NICHES[(offset + i) % len(DEVELOPMENT_BUYER_NICHES)]
        low = niche.lower()
        label = next((k for k, hints in VERTICAL_POSITIVE.items() if any(h in low for h in hints)), 'franchise')
        pain = next((p for _, k, p in VERTICALS if k == label), 'заявки, исполнители, документы и статусы')
        chosen.append((niche, label, pain))
    found = []
    for niche, label, pain in chosen:
        try:
            res = campaigns.discover_niche(OWNER_ID, niche, ["Россия"], target=6, max_queries=3, query_offset=today.toordinal(), discovery_family="development", buyer_vertical=label)
            ids = list(dict.fromkeys((res.get("inserted") or []) + (res.get("existing") or [])))[:8]
            if ids:
                campaigns.parse_batch(OWNER_ID, ids, max_pages=4, max_companies=8)
            db = SessionLocal()
            try:
                for cid in ids:
                    c = db.execute(text("SELECT id,name,city,website,domain,search_query,discovery_score FROM prospect_companies WHERE id=:c AND owner_id=:o"), {"c": cid, "o": OWNER_ID}).mappings().first()
                    if not c:
                        continue
                    if not _company_fit_relevant(dict(c), label):
                        continue
                    email = _verified_email(db, cid)
                    if not email or _already_owner_contacted(db, email["email"]):
                        continue
                    fit = 78
                    name_blob = (str(c.get("name") or "") + " " + str(c.get("search_query") or "")).lower()
                    if any(x in name_blob for x in ("сеть", "франш", "group", "холдинг")):
                        fit += 8
                    found.append({"company": dict(c), "vertical": label, "pain": pain, "fit_score": min(100, fit), "email": email})
            finally:
                db.close()
        except Exception as exc:
            observe_suppressed(__name__, exc, line=1054)
            continue
    # unique by email/domain
    uniq = {}
    for item in found:
        key = (item["company"].get("domain") or item["email"]["email"]).lower()
        if key not in uniq or item["fit_score"] > uniq[key]["fit_score"]:
            uniq[key] = item
    return sorted(uniq.values(), key=lambda x: (-x["fit_score"], int(x["company"]["id"])))[:max_companies]


def _upsert_shortlist(db, day, items, campaign_id):
    ids = []
    for rank, item in enumerate(items, 1):
        brief = item["brief"]
        row = db.execute(text("""
          INSERT INTO development_lead_shortlist
            (owner_user_id,service_date,kind,lead_id,company_id,rank,fit_score,brief_json,contact_email,campaign_id,outreach_status)
          VALUES
            (:o,:d,:k,:lead,:company,:rank,:fit,CAST(:brief AS JSONB),:email,:campaign,:outreach)
          ON CONFLICT(owner_user_id,service_date,kind,lead_id,company_id)
          DO UPDATE SET rank=EXCLUDED.rank,fit_score=EXCLUDED.fit_score,brief_json=EXCLUDED.brief_json,
                        contact_email=EXCLUDED.contact_email,campaign_id=EXCLUDED.campaign_id,
                        outreach_status=CASE WHEN development_lead_shortlist.outreach_status IN ('queued','sent') THEN development_lead_shortlist.outreach_status ELSE EXCLUDED.outreach_status END,
                        updated_at=NOW()
          RETURNING id
        """), {
            "o": OWNER_ID, "d": day, "k": item["kind"], "lead": int(item.get("lead_id") or 0), "company": int(item.get("company_id") or 0),
            "rank": rank, "fit": item["fit_score"], "brief": json.dumps(brief, ensure_ascii=False),
            "email": item.get("email"), "campaign": campaign_id,
            "outreach": ("ready" if item.get("email") and _brief_outreach_ready(brief) else ("analysis_required" if item.get("email") else "platform_or_crm")),
        }).scalar_one()
        ids.append(int(row))
    db.commit()
    return ids


def _feed_campaign(db, day, campaign_id):
    rows = db.execute(text("""
      SELECT s.id shortlist_id,s.company_id,s.contact_email,s.fit_score,s.brief_json
      FROM development_lead_shortlist s
      WHERE s.owner_user_id=:o AND s.service_date=:d AND s.contact_email IS NOT NULL
        AND s.outreach_status NOT IN ('queued','sent','suppressed')
      ORDER BY s.fit_score DESC,s.rank
    """), {"o": OWNER_ID, "d": day}).mappings().all()
    added = 0
    for r in rows:
        if not r.get("company_id"):
            continue
        if not _brief_outreach_ready(r.get("brief_json")):
            db.execute(text("UPDATE development_lead_shortlist SET outreach_status='analysis_required',updated_at=NOW() WHERE id=:i"), {"i": r["shortlist_id"]})
            db.execute(text("""UPDATE prospect_campaign_members
              SET status='skipped',skip_reason='pain_evidence_required',updated_at=NOW()
              WHERE campaign_id=:ca AND company_id=:co AND status='ready'"""),
              {"ca": campaign_id, "co": int(r["company_id"])})
            continue
        company = db.execute(text("""
          SELECT id,name,domain,website,search_query,source,discovery_score
          FROM prospect_companies WHERE id=:c AND owner_id=:o
        """), {"c": int(r["company_id"]), "o": OWNER_ID}).mappings().first()
        vertical_key = _vertical_key_from_search_query(str((company or {}).get("search_query") or ""))
        verified_pool_score, _vp_vertical, _vp_pain = _existing_company_fit_score(dict(company or {}))
        live_fit = bool(company and vertical_key and _company_fit_relevant(dict(company), vertical_key))
        reserve_fit = bool(company and verified_pool_score >= 72)
        if not (live_fit or reserve_fit):
            db.execute(text("UPDATE development_lead_shortlist SET outreach_status='company_fit_rejected',updated_at=NOW() WHERE id=:i"), {"i": r["shortlist_id"]})
            continue
        email = str(r["contact_email"]).strip().lower()
        if _already_owner_contacted(db, email):
            db.execute(text("UPDATE development_lead_shortlist SET outreach_status='duplicate',updated_at=NOW() WHERE id=:i"), {"i": r["shortlist_id"]})
            continue
        contact = _verified_email(db, r["company_id"])
        if not contact or contact["email"].lower() != email:
            db.execute(text("UPDATE development_lead_shortlist SET outreach_status='contact_not_verified',updated_at=NOW() WHERE id=:i"), {"i": r["shortlist_id"]})
            continue
        member = db.execute(text("""
          INSERT INTO prospect_campaign_members(campaign_id,company_id,contact_id,email,email_domain,quality_score,status)
          VALUES(:ca,:co,:ct,:e,:dom,:q,'ready')
          ON CONFLICT DO NOTHING
          RETURNING id
        """), {
            "ca": campaign_id, "co": r["company_id"], "ct": contact["contact_id"], "e": email,
            "dom": email.split("@", 1)[1], "q": min(100, int(r["fit_score"])),
        }).scalar()
        if member:
            added += 1
            member_id=int(member); member_status='ready'
        else:
            existing=db.execute(text("""SELECT id,status,skip_reason,email,sent_at FROM prospect_campaign_members
              WHERE campaign_id=:ca AND contact_id=:ct ORDER BY id LIMIT 1"""),
              {"ca":campaign_id,"ct":contact["contact_id"]}).mappings().first()
            # A company can legitimately get a new verified mailbox after the
            # previous address bounced. The campaign has a unique(company_id)
            # guard, so ON CONFLICT above cannot insert a replacement row.
            # Rebind only terminal/unsendable rows to a DIFFERENT verified email;
            # never rewrite a live queued/sent member.
            if not existing:
                existing=db.execute(text("""SELECT id,status,skip_reason,email,sent_at FROM prospect_campaign_members
                  WHERE campaign_id=:ca AND company_id=:co ORDER BY id LIMIT 1"""),
                  {"ca":campaign_id,"co":int(r["company_id"])}).mappings().first()
                if existing:
                    old_status=str(existing.get("status") or "")
                    old_email=str(existing.get("email") or "").strip().lower()
                    if old_status in {"suppressed","failed","skipped"} and old_email != email:
                        rebound=db.execute(text("""UPDATE prospect_campaign_members
                          SET contact_id=:ct,email=:e,email_domain=:dom,quality_score=:q,
                              status='ready',skip_reason=NULL,email_queue_id=NULL,queued_at=NULL,
                              sent_at=NULL,reply_status=NULL,replied_at=NULL,updated_at=NOW()
                          WHERE id=:m AND status IN ('suppressed','failed','skipped')"""),
                          {"ct":contact["contact_id"],"e":email,"dom":email.split("@",1)[1],
                           "q":min(100,int(r["fit_score"])),"m":int(existing["id"])}).rowcount
                        if rebound:
                            existing=db.execute(text("""SELECT id,status,skip_reason,email,sent_at
                              FROM prospect_campaign_members WHERE id=:m"""),
                              {"m":int(existing["id"])}).mappings().first()
            member_id=int(existing["id"]) if existing else None
            member_status=str(existing["status"] or "") if existing else ""
            if existing and member_status == 'skipped' and str(existing.get('skip_reason') or '') == 'pain_evidence_required':
                db.execute(text("UPDATE prospect_campaign_members SET status='ready',skip_reason=NULL,updated_at=NOW() WHERE id=:m"), {"m": member_id})
                member_status='ready'
        if member_id:
            shortlist_status = 'sent' if member_status=='sent' else ('queued' if member_status=='queued' else ('ready' if member_status=='ready' else member_status or 'linked'))
            db.execute(text("UPDATE development_lead_shortlist SET campaign_member_id=:m,outreach_status=:st,updated_at=NOW() WHERE id=:i"), {"m": member_id, "st": shortlist_status, "i": r["shortlist_id"]})
    campaigns.sync_campaign(db, campaign_id)
    db.commit()
    return added


def run(*, discover=True, target=TARGET_DAILY):
    day = _service_date()
    db = SessionLocal()
    try:
        ensure_schema(db)
        raw = [dict(r) for r in db.execute(text("""
          SELECT * FROM lead_radar_leads
          WHERE owner_user_id=:o
            AND published_at>=NOW()-INTERVAL '36 hours'
            AND status IN ('qualified','draft_ready','ready_to_contact','contacted')
            AND score>=45
          ORDER BY score DESC,published_at DESC
          LIMIT 1200
        """), {"o": OWNER_ID}).mappings().all()]
        hot = []
        seen = set()
        per_source = {}
        for row in raw:
            fit = _hot_fit(row)
            if fit < 62:
                continue
            source = str(row.get("source_key") or "")
            cap = 5 if source == "eu_ted" else 10
            if int(per_source.get(source, 0)) >= cap:
                continue
            key = (str(row.get("company_domain") or "").lower(), _compact(row.get("title") or "", 100).lower())
            if key in seen:
                continue
            seen.add(key)
            per_source[source] = int(per_source.get(source, 0)) + 1
            hot.append({"kind": "demand", "lead_id": int(row["id"]), "company_id": None, "fit_score": fit, "brief": _hot_brief(db, row, fit), "email": None})
        hot.sort(key=lambda x: (-x["fit_score"], -x["lead_id"]))
    finally:
        db.close()

    company_items = []
    company_candidates = []
    # Deep-enrich a bounded number of already curated buyers before selecting
    # the verified reserve. Full morning runs do more; emergency refills stay light.
    enrichment=_enrich_curated_missing_contacts(max_companies=(8 if discover else 3))
    if discover:
        company_candidates.extend(_discover_company_fit(max_companies=60))
    # Always keep a verified reserve so search-engine anti-bot pages do not stop
    # tomorrow's email work. This path performs no paid API calls.
    company_candidates.extend(_existing_verified_company_fit(max_companies=120))
    company_seen=set()
    analysis_budget = 50 if discover else 12
    analyzed = 0
    for x in sorted(company_candidates,key=lambda z:-int(z.get("fit_score") or 0)):
        domain=str(x["company"].get("domain") or "").strip().lower()
        key=domain or str(x["email"]["email"]).lower()
        if key in company_seen:
            continue
        company_seen.add(key)
        if analyzed >= analysis_budget:
            break
        analyzed += 1
        analysis = _scan_company_evidence(x["company"], x["vertical"], max_pages=4)
        brief = _company_brief(x["company"], x["vertical"], x["pain"], x["fit_score"], x["email"]["email"], analysis=analysis)
        company_items.append({
            "kind": "company_fit", "lead_id": None, "company_id": int(x["company"]["id"]),
            "fit_score": x["fit_score"], "brief": brief, "email": x["email"]["email"],
        })
    company_items.sort(key=lambda x: (0 if _brief_outreach_ready(x.get("brief")) else 1, -int(x.get("brief",{}).get("pain_score") or 0), -int(x.get("fit_score") or 0)))

    wanted = max(MIN_DAILY, min(int(target), TARGET_DAILY))
    company_take = min(100, len(company_items), wanted)
    selected = list(company_items[:company_take])
    selected.extend(hot[:max(0, wanted - len(selected))])
    if len(selected) < wanted:
        already={(x.get("kind"),x.get("lead_id"),x.get("company_id")) for x in selected}
        for item in company_items[company_take:] + hot[max(0, wanted-company_take):]:
            key=(item.get("kind"),item.get("lead_id"),item.get("company_id"))
            if key in already:
                continue
            selected.append(item); already.add(key)
            if len(selected) >= wanted:
                break
    selected.sort(key=lambda x: (-x["fit_score"], 0 if x["kind"] == "company_fit" else 1, -(x.get("lead_id") or 0)))
    campaign_id = _ensure_campaign()

    db = SessionLocal()
    try:
        ensure_schema(db)
        _upsert_shortlist(db, day, selected, campaign_id)
        added = _feed_campaign(db, day, campaign_id)
        counts = dict(db.execute(text("""
          SELECT count(*) total,
                 count(*) FILTER(WHERE contact_email IS NOT NULL) with_email,
                 count(*) FILTER(WHERE outreach_status='ready') email_ready,
                 count(*) FILTER(WHERE outreach_status='queued') email_queued,
                 count(*) FILTER(WHERE outreach_status='sent') email_sent,
                 count(*) FILTER(WHERE brief_json->>'outreach_gate'='pass') evidence_ready,
                 count(*) FILTER(WHERE contact_email IS NOT NULL AND COALESCE(brief_json->>'outreach_gate','blocked')<>'pass') analysis_required
          FROM development_lead_shortlist WHERE owner_user_id=:o AND service_date=:d
        """), {"o": OWNER_ID, "d": day}).mappings().one())
        top = [dict(r) for r in db.execute(text("""
          SELECT rank,kind,fit_score,brief_json,contact_email,outreach_status
          FROM development_lead_shortlist
          WHERE owner_user_id=:o AND service_date=:d
          ORDER BY rank LIMIT 5
        """), {"o": OWNER_ID, "d": day}).mappings().all()]
    finally:
        db.close()

    return {
        "status": "PASS" if counts["total"] >= MIN_DAILY else "PARTIAL",
        "service_date": str(day),
        "campaign_id": campaign_id,
        "selected": len(selected),
        "campaign_members_added": added,
        "enrichment": enrichment,
        "counts": counts,
        "top5": top,
        "note": "Email delivery remains governed by canonical owner cap/pacing; shortlist target is independent.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-discovery", action="store_true")
    ap.add_argument("--target", type=int, default=TARGET_DAILY)
    args = ap.parse_args()
    print(json.dumps(run(discover=not args.no_discovery, target=args.target), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
