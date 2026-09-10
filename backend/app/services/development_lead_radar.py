"""BORIS Development Lead Radar.

Builds a daily shortlist of 30-50 high-fit leads for custom SaaS/mobile/software
and feeds only verified public business emails into the canonical owner outreach
campaign. No direct SMTP, no CAPTCHA/auth bypass, no applicant-channel auto-send.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import prospect_campaigns as campaigns
from app.services import prospecting

OWNER_ID = 2
TARGET_DAILY = 50
MIN_DAILY = 30
HOT_TARGET_SERVICES = {5, 7, 8, 9, 14, 17, 18, 25, 26, 33}
CAMPAIGN_NAME = "Кирилл · разработка SaaS/App"
CAMPAIGN_NICHE = "Заказная разработка SaaS, мобильных приложений и автоматизации"
SUBJECTS = "По цифровой системе {company}||По автоматизации процессов||По мобильному приложению"
DEVELOPMENT_EMAIL_BODY = """Добрый день!

Посмотрел {company}.

Мы занимаемся заказной разработкой программных продуктов для бизнеса: SaaS-платформ, мобильных приложений iOS/Android, CRM, личных кабинетов, внутренних систем, интеграций и автоматизации процессов.

Если готовые сервисы не закрывают вашу бизнес-логику, можем собрать собственное решение под конкретный процесс: сначала определить ключевые сценарии, затем сделать MVP, протестировать на реальной работе и дальше развивать продукт по этапам.

Чтобы не тратить бюджет на повторную разработку типовых вещей, используем готовый технический фундамент для авторизации, ролей, кабинетов, уведомлений, платежей, аналитики и инфраструктуры, а основное время уходит на уникальные функции вашего проекта.

Могу бесплатно посмотреть задачу и прислать:
— структуру MVP;
— какие функции нужны в первой версии;
— ориентир по срокам;
— вилку стоимости;
— какие готовые сервисы можно использовать вместо собственной разработки, если писать всё с нуля невыгодно.

Примеры направлений: SaaS, мобильные приложения, web-приложения, CRM, кабинеты клиентов/сотрудников/дилеров, AI-автоматизация, интеграции с 1С/API/телефонией.

Подробнее:
https://boris-ai.pro/go/software

Если актуально, ответьте на это письмо несколькими предложениями о задаче — подготовлю первый вариант архитектуры и оценки.

Если предложение не актуально, ответьте «не интересно» — адрес будет исключён из следующих обращений.

Кирилл
"""

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
    if any(x in hay for x in CONTENT_NOISE):
        return False
    positive = VERTICAL_POSITIVE.get(str(vertical_key), ())
    return bool(positive and any(x in hay for x in positive))


def ensure_schema(db):
    db.execute(text("""
    CREATE TABLE IF NOT EXISTS development_lead_shortlist (
      id BIGSERIAL PRIMARY KEY,
      owner_user_id BIGINT NOT NULL,
      service_date DATE NOT NULL,
      kind VARCHAR(32) NOT NULL,
      lead_id BIGINT NULL,
      company_id BIGINT NULL,
      rank INTEGER NOT NULL DEFAULT 0,
      fit_score INTEGER NOT NULL DEFAULT 0,
      brief_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      contact_email TEXT NULL,
      campaign_id BIGINT NULL,
      campaign_member_id BIGINT NULL,
      outreach_status VARCHAR(32) NOT NULL DEFAULT 'not_available',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE(owner_user_id, service_date, kind, lead_id, company_id)
    )
    """))
    db.execute(text("UPDATE development_lead_shortlist SET lead_id=0 WHERE lead_id IS NULL"))
    db.execute(text("UPDATE development_lead_shortlist SET company_id=0 WHERE company_id IS NULL"))
    db.execute(text("ALTER TABLE development_lead_shortlist ALTER COLUMN lead_id SET DEFAULT 0"))
    db.execute(text("ALTER TABLE development_lead_shortlist ALTER COLUMN company_id SET DEFAULT 0"))
    db.execute(text("ALTER TABLE development_lead_shortlist ALTER COLUMN lead_id SET NOT NULL"))
    db.execute(text("ALTER TABLE development_lead_shortlist ALTER COLUMN company_id SET NOT NULL"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_development_lead_shortlist_day ON development_lead_shortlist(owner_user_id,service_date,rank)"))
    db.commit()


def _service_date():
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _service_ids(raw):
    out = set()
    for x in (raw or []):
        try:
            out.add(int(x))
        except Exception:
            pass
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
        row = db.execute(text("SELECT id,status FROM prospect_campaigns WHERE owner_id=:o AND name=:n ORDER BY id DESC LIMIT 1"), {"o": OWNER_ID, "n": CAMPAIGN_NAME}).mappings().first()
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
                     subject_template=:subject,
                     body_template=:body,
                     ab_variants='[]'::jsonb,
                     attachment_path=NULL,
                     daily_limit=20,
                     per_domain_daily_limit=1,
                     min_quality_score=60,
                     updated_at=NOW()
               WHERE id=:c AND owner_id=:o
            """), {"c": cid, "o": OWNER_ID, "niche": CAMPAIGN_NICHE,
                   "subject": SUBJECTS, "body": DEVELOPMENT_EMAIL_BODY})
            db.commit()
        finally:
            db.close()
        if row["status"] != "active":
            campaigns.set_status(OWNER_ID, cid, "active")
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
        regions=["Россия"],
        subject_template=SUBJECTS,
        body_template=DEVELOPMENT_EMAIL_BODY,
        daily_limit=20,
        per_domain_daily_limit=1,
        min_quality_score=60,
        account_id=None,
        mailbox_id=int(mailbox),
    )
    campaigns.set_status(OWNER_ID, cid, "active")
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


def _existing_company_fit_score(company: dict) -> tuple[int, str, str]:
    """High-confidence reusable pool when public search engines are degraded."""
    source = str(company.get("source") or "").lower()
    hay = " ".join(str(company.get(k) or "") for k in ("name", "domain", "search_query", "website")).lower().replace("ё", "е")
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


def _company_brief(company, vertical_label, process_pain, fit_score, email):
    name = _compact(company.get("name") or company.get("domain") or "Компания", 100)
    first = (
        f"Добрый день! Посмотрел {name}. По открытым данным вы работаете в сегменте «{vertical_label}». "
        f"Для такого бизнеса часто узкое место — {process_pain}. "
        f"Если часть процесса сейчас распределена между CRM, таблицами и мессенджерами, можем собрать единый SaaS/личный кабинет или приложение. "
        f"Могу прислать структуру MVP и вилку по срокам/стоимости."
    )
    return {
        "company": name,
        "what_company_does": f"Компания в сегменте «{vertical_label}»; источник — официальный публичный сайт.",
        "problem": f"Гипотеза по типу бизнеса: {process_pain}. Требует подтверждения на первом контакте.",
        "why_custom_saas_app": "Для сети/сервисного бизнеса ценность появляется, когда запись/заявки, сотрудники, клиентский кабинет, уведомления, аналитика и интеграции работают как единый процесс.",
        "what_we_offer": "Сначала короткий аудит процесса → прототип → MVP на готовом SaaS/mobile-скелете → интеграции и запуск.",
        "estimated_check_rub": {"min": 300000, "max": 1200000},
        "decision_maker": "Собственник / генеральный директор / операционный директор / руководитель цифровизации",
        "contact": email,
        "personal_first_message": first,
        "source": "company_fit",
        "source_url": company.get("website"),
        "fit_score": fit_score,
    }


def _discover_company_fit(max_companies=16):
    today = _service_date()
    # Rotate niches so the same four web searches are not repeated every day.
    offset = today.toordinal() % len(VERTICALS)
    chosen = [VERTICALS[(offset + i) % len(VERTICALS)] for i in range(4)]
    found = []
    for niche, label, pain in chosen:
        try:
            res = campaigns.discover_niche(OWNER_ID, niche, ["Россия"], target=6, max_queries=3, query_offset=today.toordinal())
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
        except Exception:
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
            "outreach": "ready" if item.get("email") else "platform_or_crm",
        }).scalar_one()
        ids.append(int(row))
    db.commit()
    return ids


def _feed_campaign(db, day, campaign_id):
    rows = db.execute(text("""
      SELECT s.id shortlist_id,s.company_id,s.contact_email,s.fit_score
      FROM development_lead_shortlist s
      WHERE s.owner_user_id=:o AND s.service_date=:d AND s.contact_email IS NOT NULL
        AND s.outreach_status NOT IN ('queued','sent','suppressed')
      ORDER BY s.fit_score DESC,s.rank
    """), {"o": OWNER_ID, "d": day}).mappings().all()
    added = 0
    for r in rows:
        if not r.get("company_id"):
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
            existing=db.execute(text("""SELECT id,status FROM prospect_campaign_members
              WHERE campaign_id=:ca AND contact_id=:ct ORDER BY id LIMIT 1"""),
              {"ca":campaign_id,"ct":contact["contact_id"]}).mappings().first()
            member_id=int(existing["id"]) if existing else None
            member_status=str(existing["status"] or "") if existing else ""
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
    if discover:
        company_candidates.extend(_discover_company_fit(max_companies=18))
    # Always keep a verified reserve so search-engine anti-bot pages do not stop
    # tomorrow's email work. This path performs no paid API calls.
    company_candidates.extend(_existing_verified_company_fit(max_companies=40))
    company_seen=set()
    for x in sorted(company_candidates,key=lambda z:-int(z.get("fit_score") or 0)):
        domain=str(x["company"].get("domain") or "").strip().lower()
        key=domain or str(x["email"]["email"]).lower()
        if key in company_seen:
            continue
        company_seen.add(key)
        brief = _company_brief(x["company"], x["vertical"], x["pain"], x["fit_score"], x["email"]["email"])
        company_items.append({
            "kind": "company_fit", "lead_id": None, "company_id": int(x["company"]["id"]),
            "fit_score": x["fit_score"], "brief": brief, "email": x["email"]["email"],
        })

    wanted = max(MIN_DAILY, min(int(target), TARGET_DAILY))
    company_take = min(15, len(company_items), wanted)
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
                 count(*) FILTER(WHERE outreach_status='sent') email_sent
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
