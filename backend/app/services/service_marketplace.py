from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "service_marketplaces"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DRAFTS_FILE = STATE_DIR / "drafts.json"
PUBLICATIONS_FILE = STATE_DIR / "publications.json"
MAILBOXES_FILE = STATE_DIR / "mailboxes.json"
BORIS_SITE = "https://boris-ai.pro"

ActionMode = Literal["catalog", "direct_post", "reply_to_jobs", "paid_post", "reply_only"]

@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    url: str
    mode: ActionMode
    priority: int
    audience: str
    registration: str
    notes: str

@dataclass(frozen=True)
class Offer:
    key: str
    title: str
    setup_from: int
    setup_to: int
    monthly_from: int
    monthly_to: int
    short_pitch: str

PLATFORMS = [
    Platform("fl","FL.ru","https://www.fl.ru/projects/category/avtomatizaciya-biznesa/","reply_to_jobs",1,"заказчики автоматизации, CRM, API, AI","аккаунт исполнителя; могут потребоваться проверки","Отвечать на свежие релевантные проекты; можно оформить услуги."),
    Platform("workspace","Workspace","https://workspace.ru/tenders/crm/","reply_to_jobs",2,"компании и агентские тендеры","профиль/агентство; модерация площадки","Высокие чеки; только релевантные тендеры."),
    Platform("profi","Профи.ру","https://profi.ru/registration/it_freelance/programmer/","reply_to_jobs",3,"малый и средний бизнес","номер телефона и SMS-код","E-mail недостаточно; ручной checkpoint на SMS."),
    Platform("kwork","Kwork","https://kwork.ru/","catalog",4,"малый бизнес и разовые задачи","профиль исполнителя; верификация","Отдельные карточки услуг."),
    Platform("tenchat","TenChat","https://tenchat.ru/","direct_post",5,"предприниматели, B2B, эксперты","профиль пользователя/компании","Экспертные кейсы и офферы."),
    Platform("vk","VK","https://vk.com/","direct_post",6,"предприниматели и сообщества","VK-профиль/сообщество","Публикация в своём сообществе или где правила разрешают рекламу."),
    Platform("tg_biznesschatt","Telegram: БИЗНЕС-ЧАТ | РФ","https://t.me/biznesschatt","direct_post",7,"предприниматели России","Telegram-профиль","Учитывать правила и модерацию."),
    Platform("tg_russianfreelance","Telegram: RussianFreelance","https://t.me/RussianFreelance","paid_post",8,"заказчики и исполнители","Telegram-профиль","Платное размещение; требует согласования тарифа."),
    Platform("tg_freelancegram","Telegram: ФРИЛАНС | ЧАТ","https://t.me/freelance_gram","reply_to_jobs",9,"заказчики и исполнители","Telegram-профиль","Соблюдать актуальные правила."),
    Platform("tg_poiskfreelance","Telegram: ПОИСК ФРИЛАНСЕРОВ | ЧАТ","https://t.me/poiskfreelance","reply_to_jobs",10,"заказчики и исполнители","Telegram-профиль","Проверять закреп/правила."),
    Platform("tg_freelancesam","Telegram: САМОЗАНЯТЫЙ | ФРИЛАНС ЧАТ","https://t.me/freelancesam","reply_to_jobs",11,"фриланс и малый бизнес","Telegram-профиль","Проверять актуальные правила."),
    Platform("tg_predprinimateli_rus","Telegram: Предприниматели России","https://t.me/predprinimateli_rus","reply_only",12,"предприниматели","Telegram-профиль","Только ответ на явный запрос, без проактивной рекламы."),
]

OFFERS = [
    Offer("sales_ai","ИИ-менеджер + CRM",100000,250000,30000,70000,"Принимает обращения, квалифицирует, создаёт сделку и передаёт менеджеру."),
    Offer("sales_automation","Автоматизация отдела продаж",150000,400000,50000,120000,"CRM, заявки, задачи, контроль, отчётность и реактивация."),
    Offer("crm_integrations","CRM + API-интеграции",100000,300000,20000,60000,"Связываем CRM, сайт, телефонию, мессенджеры, 1С и внешние сервисы."),
    Offer("ai_sales_manager","ИИ-РОП: звонки, переписки, KPI",150000,350000,40000,100000,"Автоматический контроль качества отдела продаж."),
    Offer("avito","Автоматизация Avito / объявлений",100000,300000,35000,100000,"Карточки, тексты, фото, фиды, публикации, ответы и KPI."),
    Offer("routine","Автоматизация ручных процессов",80000,300000,20000,60000,"Убираем переносы между Excel, почтой, CRM и сервисами."),
    Offer("crm_1c","CRM + 1С + сайт + телефония",250000,700000,30000,100000,"Единый контур заявок, клиентов, заказов, звонков и учёта."),
    Offer("support_ai","ИИ-поддержка / база знаний",100000,300000,30000,100000,"Ответы на типовые вопросы и передача сложных обращений."),
    Offer("reactivation","Реактивация клиентской базы",80000,200000,30000,70000,"Возвращаем старые лиды и клиентов."),
    Offer("owner_dashboard","Дашборд собственника",100000,300000,20000,70000,"Выручка, лиды, CPL, конверсии, менеджеры и проблемы."),
]

TOPICS = {
    "virtual_department": {
        "title":"BORIS — виртуальный отдел продаж и рекламы под ключ",
        "keywords":["виртуальный отдел продаж","отдел продаж под ключ","автоматизация продаж","автоматизация рекламы","ИИ менеджер продаж","ИИ РОП","CRM автоматизация","обработка заявок","контроль лидов","реактивация клиентов","аналитика продаж","автоматизация Avito","автоматизация Яндекс Директ","BORIS AI"],
        "pain":"заявки, реклама, переписки, звонки, CRM и контроль менеджеров разрознены, а собственник вынужден сам следить за всем вручную",
        "result":"BORIS берёт единым контуром рекламу, входящие обращения, квалификацию лидов, CRM, звонки, переписки, реактивацию и контроль KPI — собственник видит результат, а не становится оператором системы",
    },
    "development": {
        "title":"Разработка и автоматизация бизнес-процессов под ключ",
        "keywords":["автоматизация бизнеса","разработка для бизнеса","автоматизация бизнес процессов","CRM разработка","API интеграция","интеграция сайта","автоматизация заявок","чат бот для бизнеса","личный кабинет","парсер","BI аналитика","автоматизация документов","интеграция телефонии","интеграция маркетплейсов","разработка MVP"],
        "pain":"сотрудники вручную переносят данные, теряют заявки, сводят отчёты и работают сразу в нескольких несвязанных сервисах",
        "result":"разбираем процесс, делаем MVP, связываем нужные системы и автоматизируем конкретный участок бизнеса с понятным результатом, сроком и стоимостью",
    },
    "avito": {
        "title":"Автоматизация Авито для бизнеса",
        "keywords":["автоматизация авито","авито для бизнеса","авито объявления","авито автопубликация","авито фид","авито продвижение","авито аналитика","авито сообщения","авито CRM","авито лиды","авито автоматизация продаж","управление объявлениями авито"],
        "pain":"объявления, ответы, контроль цен и статистики отнимают время, а часть лидов теряется",
        "result":"BORIS помогает собрать публикации, сообщения, аналитику и контроль результата в одном процессе",
    },
    "direct": {
        "title":"Автоматизация Яндекс Директ и рекламы",
        "keywords":["автоматизация яндекс директ","яндекс директ","директолог автоматизация","управление рекламой","контроль рекламы","аналитика директ","лиды яндекс директ","автоматизация ставок","рекламные кампании","контроль CPL","оптимизация рекламных кампаний"],
        "pain":"кампании требуют постоянного контроля, а собственник не видит простой связи реклама → лид → продажа",
        "result":"BORIS связывает рекламные данные с CRM, лидами, продажами и управленческими показателями",
    },
    "sales": {
        "title":"Автоматизация отдела продаж",
        "keywords":["автоматизация продаж","отдел продаж автоматизация","CRM автоматизация","ИИ менеджер продаж","ИИ РОП","контроль менеджеров","реактивация клиентов","обработка лидов","автоматизация заявок","воронка продаж","контроль звонков","анализ переписок","увеличение конверсии продаж"],
        "pain":"заявки приходят из разных мест, менеджеры забывают перезванивать, а руководитель узнаёт о проблемах слишком поздно",
        "result":"BORIS собирает обращения, создаёт сделки и задачи, контролирует переписки и звонки и показывает владельцу результат",
    },
    "integrations": {
        "title":"CRM, 1С и API-интеграции",
        "keywords":["интеграция CRM","CRM 1С","API интеграция","интеграция сайта с CRM","интеграция телефонии CRM","автоматизация 1С","интеграция мессенджеров","автоматизация бизнес процессов","интеграция систем","обмен данными API"],
        "pain":"сотрудники вручную переносят данные между CRM, 1С, сайтом, почтой, таблицами и мессенджерами",
        "result":"BORIS соединяет системы и убирает повторный ручной ввод, дубли и потерянные заявки",
    },
}

def _load(path: Path, default):
    return default if not path.exists() else json.loads(path.read_text(encoding="utf-8"))

def _save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def list_platforms():
    return [asdict(x) for x in sorted(PLATFORMS,key=lambda p:p.priority)]

def list_offers():
    return [asdict(x) for x in OFFERS]

def _draft_id(platform,topic,variant):
    return hashlib.sha256(f"{platform}:{topic}:{variant}".encode()).hexdigest()[:16]

def generate_drafts(platform="all",topic="all",variants=3):
    ps=[p for p in PLATFORMS if platform in ("all",p.key)]
    if topic!="all" and topic not in TOPICS: raise ValueError("unknown topic")
    ts=TOPICS.items() if topic=="all" else [(topic,TOPICS[topic])]
    hooks=[
        "Если часть заявок и контроля держится на ручной работе, это можно убрать программированием.",
        "Автоматизация должна экономить время и не давать заявкам теряться, а не добавлять ещё один кабинет.",
        "Автоматизируем конкретные процессы: от входящей заявки до сделки и отчёта собственнику.",
    ]
    ctas=[
        "Пришлите схему текущего процесса — покажем, что имеет смысл автоматизировать первым.",
        "Опишите, где больше всего ручной работы или теряются заявки — предложим рабочую схему.",
        "Разберём один процесс и оценим внедрение до начала разработки.",
    ]
    out=[]
    for p in ps:
        for topic_key,cfg in ts:
            for i in range(max(1,min(int(variants),20))):
                shift=i%len(cfg["keywords"]); kws=(cfg["keywords"][shift:]+cfg["keywords"][:shift])[:8]
                text=(
                    f"{cfg['title']}\n\n{hooks[i%len(hooks)]}\n\n"
                    f"Проблема: {cfg['pain']}.\n\n"
                    f"Что делаем: {cfg['result']}. Подключаем CRM, API, сообщения, звонки, аналитику, реактивацию и автоматические действия по необходимости.\n\n"
                    "Ориентир по цене: небольшие автоматизации от 50–80 тыс. ₽; комплексные внедрения обычно от 100–300 тыс. ₽ и выше. Точную цену фиксируем после разбора процесса.\n\n"
                    f"{ctas[i%len(ctas)]}\n\nBORIS: {BORIS_SITE}\n\n"
                    "Ключевые направления: "+", ".join(kws)
                )
                out.append({"id":_draft_id(p.key,topic_key,i),"platform":p.key,"platform_name":p.name,"topic":topic_key,
                            "title":cfg["title"],"text":text,"site_url":BORIS_SITE,"status":"needs_owner_approval",
                            "generated_without_openai":True,"variant":i+1})
    return out

def save_drafts(drafts):
    cur={x["id"]:x for x in _load(DRAFTS_FILE,[])}
    for d in drafts:
        old=cur.get(d["id"])
        if old and old.get("status") in {"approved","posted"}:
            d["status"]=old["status"]
            if old.get("publication_url"): d["publication_url"]=old["publication_url"]
        cur[d["id"]]=d
    rows=list(cur.values()); _save(DRAFTS_FILE,rows); return rows

def list_drafts(status=None):
    rows=_load(DRAFTS_FILE,[])
    return rows if not status else [x for x in rows if x.get("status")==status]

def set_draft_status(draft_id,status):
    if status not in {"approved","rejected"}: raise ValueError("invalid status")
    rows=_load(DRAFTS_FILE,[]); found=None
    for r in rows:
        if r["id"]==draft_id: r["status"]=status; found=r; break
    if not found: raise KeyError(draft_id)
    _save(DRAFTS_FILE,rows); return found

def mark_posted(draft_id,url):
    rows=_load(DRAFTS_FILE,[]); d=next((x for x in rows if x["id"]==draft_id),None)
    if not d: raise KeyError(draft_id)
    if d.get("status")!="approved": raise PermissionError("owner approval required")
    if not url.startswith(("https://","http://")): raise ValueError("absolute url required")
    d["status"]="posted"; d["publication_url"]=url; _save(DRAFTS_FILE,rows)
    pubs=_load(PUBLICATIONS_FILE,[])
    item={"draft_id":draft_id,"platform":d["platform"],"topic":d["topic"],"url":url,"site_url":BORIS_SITE,"posted_at":datetime.now(timezone.utc).isoformat()}
    pubs.append(item); _save(PUBLICATIONS_FILE,pubs); return item

def status():
    drafts=_load(DRAFTS_FILE,[]); pubs=_load(PUBLICATIONS_FILE,[]); mbs=_load(MAILBOXES_FILE,[])
    by={}
    for d in drafts: by[d.get("status","unknown")]=by.get(d.get("status","unknown"),0)+1
    return {"drafts":len(drafts),"publications":len(pubs),"mailboxes":len(mbs),"by_status":by,"latest_publications":pubs[-20:]}

REGISTRATIONS_FILE = STATE_DIR / "registrations.json"

def registration_plan(email: str | None = None) -> list[dict]:
    existing = {x.get("platform"): x for x in _load(REGISTRATIONS_FILE, [])}
    if not email:
        mailboxes = _load(MAILBOXES_FILE, [])
        if mailboxes:
            email = str(mailboxes[0].get("address") or "").strip() or None
    out = []
    for p in sorted(PLATFORMS, key=lambda x: x.priority):
        if p.key == "profi":
            checkpoint = "phone_sms_required"
        elif p.key.startswith("tg_") or p.key == "vk":
            checkpoint = "social_account_required"
        else:
            checkpoint = "manual_verification"
        current = existing.get(p.key, {})
        out.append({
            "platform": p.key,
            "name": p.name,
            "url": p.url,
            "mode": p.mode,
            "email": email if checkpoint == "manual_verification" else current.get("email"),
            "status": current.get("status", "not_registered"),
            "checkpoint": current.get("checkpoint", checkpoint),
            "account_url": current.get("account_url"),
            "last_error": current.get("last_error"),
            "created_at": current.get("created_at"),
            "warming_started_at": current.get("warming_started_at"),
            "notes": p.notes,
        })
    return out

def upsert_registration(platform: str, status_value: str, *, email: str | None = None, account_url: str | None = None, checkpoint: str | None = None, last_error: str | None = None) -> dict:
    # Registration state must support auto-discovered forums too, otherwise
    # discovery can find a platform but the state machine cannot progress it.
    try:
        keys = {p.key for p in all_platform_objects()}
    except NameError:
        keys = {p.key for p in PLATFORMS}
    if platform not in keys:
        raise ValueError("unknown platform")
    allowed_status = {"not_registered", "in_progress", "verification_required", "warming", "ready", "blocked"}
    if status_value not in allowed_status:
        raise ValueError("invalid registration status")
    rows = _load(REGISTRATIONS_FILE, [])
    item = next((x for x in rows if x.get("platform") == platform), None)
    if item is None:
        item = {"platform": platform, "created_at": datetime.now(timezone.utc).isoformat()}
        rows.append(item)
    now_iso = datetime.now(timezone.utc).isoformat()
    warming_started_at = item.get("warming_started_at")
    if status_value == "warming" and not warming_started_at:
        warming_started_at = now_iso
    item.update({
        "status": status_value,
        "updated_at": now_iso,
        "email": email if email is not None else item.get("email"),
        "account_url": account_url if account_url is not None else item.get("account_url"),
        "checkpoint": checkpoint if checkpoint is not None else item.get("checkpoint"),
        "last_error": last_error,
        "warming_started_at": warming_started_at,
    })
    _save(REGISTRATIONS_FILE, rows)
    return item

TERMINAL_REGISTRATION_CHECKPOINTS = {
    "free_only_policy",
    # Identity/business-email checks are one-time human onboarding steps, not
    # terminal platform failures. Keeping them terminal makes a valid forum
    # disappear permanently instead of returning to BORIS after completion.
    "registration_disabled_by_site",
    "registration_rejected_by_site",
    "registration_route_unavailable",
}
WARMING_REGISTRATION_CHECKPOINTS = {
    "account_age_required",
    "participation_level_required",
    "reputation_required",
    "post_registration_wait_required",
    "freelance_group_required",
    "membership_group_required",
    "established_member_required",
    "organization_quarantine_required",
}

RECOVERABLE_REGISTRATION_CHECKPOINTS = {
    "registration_form_not_found",
    "registration_form_error",
    "registration_unavailable",
    "external_registration_submit_blocked",
    "account_creation_unverified",
    "login_failed",
    "login_result_unclear",
    "registration_adapter_error",
    "preflight_timeout",
}

def registration_is_terminally_blocked(row: dict | None) -> bool:
    if not row or row.get("status") != "blocked":
        return False
    return str(row.get("checkpoint") or "") in TERMINAL_REGISTRATION_CHECKPOINTS

def registration_is_warming(row: dict | None) -> bool:
    if not row:
        return False
    checkpoint = str(row.get("checkpoint") or "")
    return row.get("status") == "warming" or checkpoint in WARMING_REGISTRATION_CHECKPOINTS


def registration_is_recoverable(row: dict | None) -> bool:
    if not row:
        return False
    checkpoint = str(row.get("checkpoint") or "")
    if checkpoint in RECOVERABLE_REGISTRATION_CHECKPOINTS:
        return True
    return row.get("status") == "blocked" and not registration_is_terminally_blocked(row)

def list_publications() -> list[dict]:
    return _load(PUBLICATIONS_FILE, [])

def _platform_copy_style(platform: Platform) -> str:
    if platform.key in {"fl", "workspace", "profi"}:
        return "response"
    if platform.key == "kwork":
        return "catalog"
    if platform.key in {"tenchat", "vk"}:
        return "expert"
    if platform.key.startswith("tg_"):
        return "chat"
    return "standard"

def _seo_draft_title(platform: Platform, topic_key: str, cfg: dict, seo: dict, variant: int) -> str:
    audience = str(platform.audience or "бизнеса").split(",")[0].strip()
    seo_topics = list(seo.get("topics") or [cfg["title"]])
    niche_topic = seo_topics[variant % len(seo_topics)]
    if topic_key == "virtual_department":
        choices = [
            niche_topic,
            f"Виртуальный отдел продаж и рекламы — {audience}",
            f"Автоматизация заявок, рекламы и продаж — {audience}",
            f"BORIS: отдел продаж и рекламы под ключ — {audience}",
        ]
    elif topic_key == "development":
        choices = [
            f"Автоматизация бизнес-процессов — {audience}",
            f"Разработка и интеграции — {audience}",
            f"MVP автоматизации: от ручной работы к системе — {audience}",
            f"{niche_topic} — разработка и автоматизация под ключ",
        ]
    else:
        choices = [niche_topic, cfg["title"]]
    return choices[(variant + platform.priority) % len(choices)]

def generate_drafts(platform="all", topic="all", variants=3):
    """Zero-LLM deterministic generator with platform-specific copy styles."""
    ps=[p for p in PLATFORMS if platform in ("all",p.key)]
    if topic!="all" and topic not in TOPICS:
        raise ValueError("unknown topic")
    ts=TOPICS.items() if topic=="all" else [(topic,TOPICS[topic])]
    hooks=[
        "Если часть заявок и контроля держится на ручной работе, это можно убрать программированием.",
        "Автоматизация должна экономить время и не давать заявкам теряться, а не добавлять ещё один кабинет.",
        "Автоматизируем конкретные процессы: от входящей заявки до сделки и отчёта собственнику.",
        "Когда сотрудники копируют данные между сервисами вручную, компания платит за одну и ту же операцию каждый день.",
        "Сначала считаем, где бизнес теряет время и заявки, и автоматизируем именно этот участок.",
        "Не обязательно менять всю инфраструктуру: часто достаточно правильно связать уже используемые сервисы.",
    ]
    ctas=[
        "Опишите текущий процесс — покажем, что имеет смысл автоматизировать первым.",
        "Можно прислать схему работы отдела: источники заявок, CRM и что сотрудники делают вручную.",
        "Разберём один процесс и до разработки зафиксируем результат, объём и ориентир по цене.",
        "Если есть конкретная задача, напишите, какие сервисы уже используются — оценим вариант интеграции.",
        "Для первого шага достаточно описать, где сейчас теряются заявки или тратится больше всего ручного времени.",
        "Можно начать с одной операции, проверить эффект и только потом расширять автоматизацию.",
    ]
    out=[]
    for p in ps:
        style=_platform_copy_style(p)
        seo=_seo_for_platform(p.key,p.audience)
        for topic_key,cfg in ts:
            for i in range(max(1,min(int(variants),20))):
                shift=(i*2)%len(cfg["keywords"])
                combined_keywords=list(dict.fromkeys(cfg["keywords"] + list(seo.get("keywords") or [])))
                kws=(combined_keywords[shift:]+combined_keywords[:shift])[:10]
                post_title=_seo_draft_title(p,topic_key,cfg,seo,i)
                niche_topic=(seo.get("topics") or [cfg["title"]])[i % len(seo.get("topics") or [cfg["title"]])]
                hook=hooks[(i+p.priority)%len(hooks)]
                cta=ctas[(i+p.priority)%len(ctas)]
                core=(
                    f"Проблема: {cfg['pain']}.\n\n"
                    f"Решение: {cfg['result']}. "
                    "При необходимости подключаем CRM, API, сайт, сообщения, звонки, аналитику, маркетплейсы, рекламу и автоматические действия.\n\n"
                    f"Для этой аудитории особенно актуальна тема: {niche_topic}.\n\n"
                )
                price=(
                    "Ориентир: небольшие автоматизации от 50–80 тыс. ₽; "
                    "комплексные внедрения обычно от 100–300 тыс. ₽ и выше. "
                    "Точную стоимость фиксируем после разбора процесса."
                )
                if style=="response":
                    text=(
                        f"{cfg['title']}\n\n"
                        f"{hook}\n\n{core}"
                        "Работаем от бизнес-задачи: сначала описываем текущий процесс, затем интеграции, контроль ошибок и критерий готовности.\n\n"
                        f"{price}\n\n{cta}\n\nBORIS: {BORIS_SITE}\n\n"
                        "Компетенции: "+", ".join(kws)
                    )
                elif style=="catalog":
                    text=(
                        f"{cfg['title']} — внедрение под ключ\n\n"
                        f"{hook}\n\n{core}"
                        "Что входит в базовый разбор:\n"
                        "• схема текущего процесса;\n• точки потери заявок/времени;\n• список интеграций;\n"
                        "• план автоматизации;\n• тестирование и критерии приёмки.\n\n"
                        f"{price}\n\n{cta}\n\nПодробнее: {BORIS_SITE}\n\n"
                        "Ключевые направления: "+", ".join(kws)
                    )
                elif style=="expert":
                    text=(
                        f"{cfg['title']}: что обычно автоматизируем в первую очередь\n\n"
                        f"{hook}\n\n{core}"
                        "Практически полезный критерий простой: после внедрения сотрудник должен перестать повторять операцию руками, "
                        "а руководитель — видеть, сработала автоматизация или нет.\n\n"
                        f"{price}\n\n{cta}\n\nСистема BORIS: {BORIS_SITE}\n\n"
                        "Темы: "+", ".join(kws)
                    )
                elif style=="chat":
                    text=(
                        f"{cfg['title']}\n\n{hook}\n\n"
                        f"{cfg['result']}.\n\n"
                        f"{price}\n\n{cta}\n\nЛендинг: {BORIS_SITE}\n\n"
                        "Направления: "+", ".join(kws[:6])
                    )
                else:
                    text=f"{cfg['title']}\n\n{hook}\n\n{core}{price}\n\n{cta}\n\nBORIS: {BORIS_SITE}"
                out.append({
                    "id":_draft_id(p.key,topic_key,i),
                    "platform":p.key,"platform_name":p.name,"topic":topic_key,
                    "title":post_title,"text":text,"site_url":BORIS_SITE,
                    "status":"needs_owner_approval","generated_without_openai":True,
                    "variant":i+1,"copy_style":style,
                })
    return out

def save_drafts(drafts):
    """Preserve manual approvals; allow ownerless Crowd SEO self-heal pre-publish.

    A posted draft is immutable evidence. A manually approved draft also stays
    immutable. The only approved draft that may be refreshed is client_crowd_seo:
    those materials are deterministically auto-approved from the client's live
    site, and only while no publication URL exists yet.
    """
    cur={x["id"]:x for x in _load(DRAFTS_FILE,[])}
    saved=0
    for d in drafts:
        old=cur.get(d["id"])
        if old and old.get("status")=="posted":
            continue
        if old and old.get("status")=="approved":
            crowd_auto=(
                old.get("topic")=="client_crowd_seo"
                and d.get("topic")=="client_crowd_seo"
                and not old.get("publication_url")
            )
            if not crowd_auto:
                continue
            d={**old,**d,"status":"approved"}
        cur[d["id"]]=d
        saved+=1
    rows=list(cur.values())
    _save(DRAFTS_FILE,rows)
    return rows

# FREE_OUTREACH_POLICY_V1
from app.services.outreach_branding import ensure_contact_block, WHATSAPP_URL, MAX_URL, MAX_PHONE_DISPLAY, CONSULT_PHONE_DISPLAY

# Extra free/community discovery targets. Presence in the registry does not
# mean BORIS may advertise there: FREE_PLATFORM_POLICY is the hard gate.
_extra_platforms = [
    Platform("searchengines_guru","Searchengines.guru","https://searchengines.guru/ru/forum","direct_post",20,
             "вебмастера, маркетологи, предприниматели","обычная регистрация форума",
             "Использовать только профильный раздел «Работа и услуги» и после проверки актуальных правил."),
    Platform("biznet","BizNet бизнес-форум","https://www.biznet.ru/","reply_only",21,
             "предприниматели и малый бизнес","обычная регистрация форума",
             "Только полезные ответы на релевантные запросы; правила рекламы проверять перед каждым новым типом размещения."),
    Platform("mastergrad","Mastergrad","https://mastergrad.com/","reply_only",22,
             "строители, ремонт, инженерные компании","обычная регистрация форума",
             "Контакты/ссылки на форуме ограничены — при обязательной ссылке BORIS канал не используем."),
    Platform("forumhouse","FORUMHOUSE","https://www.forumhouse.ru/","reply_only",23,
             "строительство и загородный бизнес","обычная регистрация форума",
             "Реклама разрешена только в коммерческом разделе; бесплатность не подтверждена — не использовать."),
    Platform("tg_networking_business","Telegram: Предприниматели · объявления и нетворкинг","https://t.me/networking_chat_business","direct_post",24,
             "предприниматели и фрилансеры","Telegram-профиль",
             "Услуги разрешены не более одного раза в день; нетематическая реклама платная."),
    Platform("tg_builders_business","Telegram: Владельцы Строительного Бизнеса","https://t.me/biznes_chat_stroitely","reply_only",25,
             "владельцы строительного бизнеса","Telegram-профиль",
             "Использовать для ответов на релевантные вопросы; перед проактивным постом проверить правила."),
    Platform("tg_builders_moscow_2026","Telegram: СТРОИТЕЛИ Москвы 2026","https://t.me/stroitelimoscow","direct_post",26,
             "заказчики и подрядчики строительного рынка","Telegram-профиль",
             "Описание группы разрешает предлагать услуги; перед отправкой всё равно проверяем закреп."),
    Platform("tg_stroyka_chat","Telegram: Строительство · чат строителей","https://t.me/stroyka_chat","paid_post",27,
             "подрядчики, поставщики, заказчики","Telegram-профиль",
             "Реклама через администратора — исключено бесплатной политикой."),
    Platform("tg_stroiteli_rf","Telegram: Строители РФ","https://t.me/stroiteli_vse_RF","paid_post",28,
             "строители, мастера и подрядчики","Telegram-профиль",
             "Реклама и сотрудничество через администратора — исключено бесплатной политикой."),
]
_existing_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _extra_platforms if p.key not in _existing_keys])

FREE_PLATFORM_POLICY = {
    # free=True means zero-cost use is documented/compatible for the action below.
    "tenchat": {"free": True, "action": "expert_post", "rule_check": True},
    "vk": {"free": True, "action": "own_community_or_free_group", "rule_check": True},
    "tg_biznesschatt": {"free": True, "action": "service_post", "rule_check": True},
    "tg_networking_business": {"free": True, "action": "service_post_max_1_per_day", "rule_check": True},
    "tg_builders_moscow_2026": {"free": True, "action": "service_post", "rule_check": True},
    "searchengines_guru": {"free": True, "action": "services_section", "rule_check": True},

    # Everything below is hard-disabled until a current zero-cost path compatible
    # with mandatory BORIS+WhatsApp+MAX contacts is verified.
    "fl": {"free": False, "reason": "paid_features_or_cost_not_verified"},
    "workspace": {"free": False, "reason": "zero_cost_response_path_not_verified"},
    "profi": {"free": False, "reason": "responses_may_require_payment"},
    "kwork": {"free": False, "reason": "marketplace_cost_model_not_zero_only"},
    "tg_russianfreelance": {"free": False, "reason": "paid_service_placement"},
    "tg_freelancegram": {"free": False, "reason": "mandatory_link_conflicts_or_rules_unclear"},
    "tg_poiskfreelance": {"free": False, "reason": "rules_not_verified_for_zero_cost_links"},
    "tg_freelancesam": {"free": False, "reason": "rules_not_verified_for_zero_cost_links"},
    "tg_predprinimateli_rus": {"free": False, "reason": "proactive_offer_not_allowed"},
    "biznet": {"free": False, "reason": "current_advertising_rules_not_verified"},
    "mastergrad": {"free": False, "reason": "links_and_contacts_restricted"},
    "forumhouse": {"free": False, "reason": "advertising_only_commercial_section"},
    "tg_builders_business": {"free": False, "reason": "proactive_advertising_rules_not_verified"},
    "tg_stroyka_chat": {"free": False, "reason": "paid_advertising"},
    "tg_stroiteli_rf": {"free": False, "reason": "paid_advertising"},
}

def platform_policy(key: str) -> dict:
    return dict(FREE_PLATFORM_POLICY.get(key, {"free": False, "reason": "not_verified"}))

def list_platforms():
    items = []
    for p in sorted(PLATFORMS, key=lambda p: p.priority):
        row = asdict(p)
        row["cost_policy"] = "free_only"
        row["free_policy"] = platform_policy(p.key)
        row["enabled_for_outreach"] = bool(row["free_policy"].get("free"))
        items.append(row)
    return items

_platform_style_generator = generate_drafts

def generate_drafts(platform="all", topic="all", variants=3):
    """Generate only zero-cost eligible drafts and add mandatory BORIS contacts."""
    if platform == "all":
        keys = [p.key for p in PLATFORMS if platform_policy(p.key).get("free")]
    else:
        if not platform_policy(platform).get("free"):
            raise ValueError(f"platform blocked by free-only policy: {platform}")
        keys = [platform]
    out = []
    for key in keys:
        rows = _platform_style_generator(platform=key, topic=topic, variants=variants)
        for row in rows:
            _apply_platform_contact_policy(row, key)
            row["free_only"] = True
            row["rule_check_required"] = bool(platform_policy(key).get("rule_check", True))
            out.append(row)
    return out

def purge_nonfree_pending_drafts() -> dict:
    rows = _load(DRAFTS_FILE, [])
    kept = []
    removed = []
    for row in rows:
        if row.get("status") in {"approved", "posted"}:
            kept.append(row)
            continue
        if not platform_policy(str(row.get("platform") or "")).get("free"):
            removed.append(row.get("id"))
            continue
        kept.append(row)
    _save(DRAFTS_FILE, kept)
    return {"removed": len(removed), "remaining": len(kept), "removed_ids": removed}

def free_outreach_config() -> dict:
    enabled = [x for x in list_platforms() if x.get("enabled_for_outreach")]
    blocked = [x for x in list_platforms() if not x.get("enabled_for_outreach")]
    return {
        "free_only": True,
        "paid_budget_rub": 0,
        "boris_site": BORIS_SITE,
        "whatsapp": WHATSAPP_URL,
        "max": MAX_URL,
        "phone": CONSULT_PHONE_DISPLAY,
        "enabled_platforms": enabled,
        "blocked_platforms": blocked,
    }

# CURATED_FORUM_CANDIDATES_V2
_more_platforms = [
    Platform("stroy_forum","Строй Форум","https://stroy-forum.ru/forums/uslugi-organizacii-i-ispolnoteley/","direct_post",30,
             "строительство, подрядчики, загородные дома","обычная регистрация",
             "Есть отдельные разделы организаций и строительных услуг; публикация только после проверки правил."),
    Platform("ssa","SSA.RU строительный форум","https://forum.ssa.ru/","direct_post",31,
             "строительство, ремонт, материалы","обычная регистрация",
             "На форуме есть отдельный раздел «Реклама»; перед публикацией проверить правила и лимиты."),
    Platform("towerbuild","TowerBuild строительный форум","https://forum.towerbuild.ru/categories","direct_post",32,
             "строители, материалы, техника, услуги","обычная регистрация",
             "Есть раздел «Объявления → Услуги»; правила перепроверяются перед постом."),
    Platform("forum_baza_1c","Форум База 1С","https://forum-baza.ru/","direct_post",33,
             "1С, бухгалтерия, автоматизация","обычная регистрация",
             "Есть 1С-фриланс и бесплатное размещение информации об услугах для 1С-франчайзи."),
    Platform("bitrix_dev","1С-Битрикс · Вакансии и резюме","https://dev.1c-bitrix.ru/community/forums/forum14/","direct_post",34,
             "веб-разработка, CRM, Битрикс24, интеграции","аккаунт 1С-Битрикс",
             "В разделе регулярно размещаются предложения услуг разработчиков; правила проверять перед созданием темы."),
    Platform("homeidea","Идеи Малого Бизнеса","https://homeidea.ru/","direct_post",35,
             "предприниматели, малый бизнес","обычная регистрация",
             "Есть разделы рекламных/маркетинговых и консалтинговых услуг."),
    Platform("oborot","Oborot.ru","https://oborot.ru/forum/","reply_only",36,
             "интернет-магазины, селлеры, e-commerce","обычная регистрация",
             "Правила считают предложением услуг, телефоном и ссылкой рекламу; в бесплатную публикацию не использовать."),
    Platform("forumrieltorov","Планета НЕРС · форум риелторов","https://forumrieltorov.ru/viewforum.php?f=26","reply_only",37,
             "риелторы, агентства недвижимости","обычная регистрация",
             "Есть раздел «Реклама недвижимости и IT-технологии»; правила саморекламы проверить автоматически."),
    Platform("promebelclub","PROMEBELclub","https://promebelclub.ru/forum/forumdisplay.php?f=115","direct_post",38,
             "производители мебели, мебельный бизнес","обычная регистрация",
             "Проверенный раздел «Продаю | Сдаю» разрешает рекламные объявления по мебельным товарам/оборудованию и услугам; обязательны регион и контакты."),
    Platform("moigruz","Мой груз · форум грузоперевозок","https://www.moigruz.ru/forum/","direct_post",39,
             "логистика, грузоперевозки, экспедирование","обычная регистрация",
             "Есть раздел «Товары и услуги — поиск и предложение»; проверить правила ссылок/контактов."),
    Platform("rekforum_logistics","РекФорум · грузоперевозки","https://rekforum.ru/viewforum.php?f=144","direct_post",40,
             "логистика, услуги, перевозки","обычная регистрация",
             "Рекламно-информационный форум; правила публикации проверять перед постом."),
    Platform("metaprom","Metaprom","https://metaprom.ru/page-production-services/","direct_post",41,
             "промышленность, производство, металлообработка, логистика","регистрация компании",
             "Доски объявлений и поставщики; использовать только бесплатный базовый формат после проверки тарифов."),
    Platform("foodmarkets","Foodmarkets.ru","https://foodmarkets.ru/forums","reply_only",42,
             "оптовая торговля продуктами, HoReCa","обычная регистрация",
             "Профильные форумы HoReCa и опта; до правила-аудита только полезные ответы."),
    Platform("kolsar_auto","Колсар · Автоуслуги","https://kolsar.info/forum/viewforum.php?f=88","direct_post",43,
             "автосервис, ремонт автомобилей","обычная регистрация",
             "Есть специализированный раздел автоуслуг; запрещённые виды услуг исключаются."),
]
_keys_now = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _more_platforms if p.key not in _keys_now])

# Searchengines.guru is confirmed paid-only for commercial topics, so it is
# explicitly disabled despite being useful for research.
FREE_PLATFORM_POLICY["searchengines_guru"] = {"free": False, "reason": "commercial_topics_paid_only"}

# New forum candidates are never auto-enabled by assumption. Their status is
# resolved by the fresh platform_rules snapshot before publication.
for _k in [
    "stroy_forum","ssa","towerbuild","forum_baza_1c","bitrix_dev","homeidea",
    "oborot","forumrieltorov","promebelclub","moigruz","rekforum_logistics",
    "metaprom","foodmarkets","kolsar_auto",
]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

# DYNAMIC_RULE_POLICY_V2
_static_platform_policy = platform_policy

def platform_policy(key: str) -> dict:
    base = dict(FREE_PLATFORM_POLICY.get(key, {"free": False, "reason": "not_verified"}))
    rule_path = STATE_DIR / "rules" / f"{key}.json"
    if rule_path.exists():
        try:
            snap = json.loads(rule_path.read_text(encoding="utf-8"))
            decision = snap.get("decision")
            base["rule_decision"] = decision
            base["rule_checked_at"] = snap.get("checked_at")
            if decision == "blocked":
                base.update({"free": False, "reason": "rules_block_or_paid"})
            elif decision == "review":
                base.update({"free": False, "reason": "rules_ambiguous"})
            elif decision == "reply_only":
                base.update({"free": True, "action": "reply_only", "rule_check": True})
            elif decision == "allowed":
                # Explicit paid-only blocks remain hard blocks even if a generic
                # page elsewhere looked permissive.
                if base.get("reason") not in {"paid_advertising","commercial_topics_paid_only","paid_service_placement"}:
                    base.update({"free": True, "action": base.get("action") or "rules_allowed", "rule_check": True})
        except Exception:
            base.update({"free": False, "reason": "rules_snapshot_invalid"})
    return base

ATTEMPTS_FILE = STATE_DIR / "publication_attempts.json"

def record_attempt(
    *,
    platform: str,
    action: str,
    status_value: str,
    draft_id: str | None = None,
    url: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    rules_decision: str | None = None,
    screenshot: str | None = None,
    meta: dict | None = None,
) -> dict:
    rows = _load(ATTEMPTS_FILE, [])
    item = {
        "id": hashlib.sha256(
            f"{platform}:{action}:{draft_id}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:18],
        "platform": platform,
        "action": action,
        "status": status_value,
        "draft_id": draft_id,
        "url": url,
        "error_code": error_code,
        "error_detail": error_detail,
        "rules_decision": rules_decision,
        "screenshot": screenshot,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rows.append(item)
    _save(ATTEMPTS_FILE, rows[-5000:])
    return item

def list_attempts(limit: int = 200) -> list[dict]:
    rows = _load(ATTEMPTS_FILE, [])
    return list(reversed(rows[-max(1, min(int(limit), 1000)):]))

def _fresh_rule_snapshot(platform: str) -> dict:
    path = STATE_DIR / "rules" / f"{platform}.json"
    if not path.exists():
        return {"allowed": False, "reason": "rules_not_checked"}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(snap.get("checked_at") or "").replace("Z", "+00:00"))
    except Exception:
        return {"allowed": False, "reason": "rules_snapshot_invalid"}
    age = datetime.now(timezone.utc) - checked
    if age.total_seconds() > 7 * 86400:
        return {"allowed": False, "reason": "rules_snapshot_stale", "checked_at": snap.get("checked_at")}
    decision = str(snap.get("decision") or "")
    if decision == "allowed":
        return {"allowed": True, "reason": "rules_allow", "decision": decision, "checked_at": snap.get("checked_at")}
    return {"allowed": False, "reason": f"rules_{decision or 'unknown'}", "decision": decision, "checked_at": snap.get("checked_at")}

def mark_posted(draft_id: str, url: str) -> dict:
    rows = _load(DRAFTS_FILE, [])
    draft = next((x for x in rows if x.get("id") == draft_id), None)
    if not draft:
        raise KeyError(draft_id)
    if draft.get("status") != "approved":
        record_attempt(
            platform=str(draft.get("platform") or "unknown"),
            action="publish",
            status_value="blocked",
            draft_id=draft_id,
            error_code="owner_approval_required",
            error_detail="Публикация запрещена до согласования владельцем.",
        )
        raise PermissionError("owner approval required")
    if not url.startswith(("https://", "http://")):
        record_attempt(
            platform=str(draft.get("platform") or "unknown"),
            action="publish",
            status_value="failed",
            draft_id=draft_id,
            error_code="invalid_publication_url",
            error_detail="Нужна абсолютная ссылка на опубликованный пост.",
        )
        raise ValueError("absolute url required")

    gate = _fresh_rule_snapshot(str(draft.get("platform") or ""))
    if not gate.get("allowed"):
        record_attempt(
            platform=str(draft.get("platform") or "unknown"),
            action="publish",
            status_value="blocked",
            draft_id=draft_id,
            error_code=str(gate.get("reason") or "rules_block"),
            error_detail="Свежая проверка правил площадки не разрешает публикацию.",
            rules_decision=str(gate.get("decision") or ""),
        )
        raise PermissionError(str(gate.get("reason") or "platform rules block publication"))

    draft["status"] = "posted"
    draft["publication_url"] = url
    draft["posted_at"] = datetime.now(timezone.utc).isoformat()
    _save(DRAFTS_FILE, rows)

    pubs = _load(PUBLICATIONS_FILE, [])
    item = {
        "draft_id": draft_id,
        "platform": draft["platform"],
        "topic": draft["topic"],
        "url": url,
        "site_url": BORIS_SITE,
        "posted_at": draft["posted_at"],
        "rules_checked_at": gate.get("checked_at"),
    }
    pubs.append(item)
    _save(PUBLICATIONS_FILE, pubs)

    record_attempt(
        platform=str(draft["platform"]),
        action="publish",
        status_value="success",
        draft_id=draft_id,
        url=url,
        rules_decision=str(gate.get("decision") or "allowed"),
    )
    return item

# CURATED_FORUM_CANDIDATES_V3
_v3_platforms = [
    Platform("partnersearch","PartnerSearch · услуги для бизнеса","https://www.partnersearch.ru/business/viewforum.php?f=22","direct_post",50,
             "предприниматели, производители, дилеры, услуги B2B","обычная регистрация",
             "Есть живые разделы поиска партнёров и услуг для бизнеса; публикация только после свежего rule-audit."),
    Platform("stroy_russia","Строительный форум России","https://stroy-russia.ru/","reply_only",51,
             "строители, материалы, кровля, фасады, подрядчики","обычная регистрация",
             "Большой профильный форум по стройматериалам и технологиям; самореклама только после проверки правил конкретного раздела."),
    Platform("vashdom_forum","ВашДом.RU · строительный форум","https://forum.vashdom.ru/","reply_only",52,
             "стройфирмы, поставщики материалов, частные заказчики","обычная регистрация",
             "Есть разделы по поиску товаров/услуг и представлению компании; бесплатность саморекламы проверять."),
    Platform("house_forum","House-Forum · строительный форум","https://house-forum.ru/","reply_only",53,
             "строительные компании, аренда техники, частное строительство","обычная регистрация",
             "Живые темы про подрядчиков и аренду техники; до аудита правил только ответы на спрос."),
    Platform("kroi_roof","KROI.RU · кровельный форум","https://www.kroi.ru/forum/","reply_only",54,
             "кровельщики, производители, строительные компании","обычная регистрация",
             "Часть коммерческих разделов платная; бесплатный путь разрешается только если rule-audit найдёт подходящий раздел."),
    Platform("sdelaimebel","СделайМебель · форум мебельщиков","https://forum.sdelaimebel.ru/","reply_only",55,
             "мебельщики, производители, дизайнеры, поставщики","обычная регистрация",
             "Есть раздел мебельного бизнеса, рекламы и работы с клиентом; правила саморекламы проверить."),
    Platform("mp_forum","MarketPlace Forum","https://mp-forum.ru/","reply_only",56,
             "селлеры Wildberries, Ozon, Яндекс Маркет","обычная регистрация",
             "Живой форум селлеров; публикации об автоматизации возможны только по правилам раздела."),
    Platform("sellermap","SellerMAP Community","https://sellermap.online/community/","reply_only",57,
             "селлеры и сервисы для маркетплейсов","регистрация сообщества",
             "Есть рекомендованные сервисы и треды; правила добавления сервиса и бесплатность нужно подтвердить."),
    Platform("tcfs","TCFS · форум спецтехники","https://tcfs.ru/forums/","reply_only",58,
             "аренда спецтехники, строительство, коммерческий транспорт","обычная регистрация",
             "Есть раздел предложений услуг по аренде спецтехники; правила ссылок/коммерции проверять."),
    Platform("stom_ru","Stom.ru · форум стоматологов","https://stom.ru/","reply_only",59,
             "стоматологии, клиники, врачи","обычная регистрация",
             "Есть профессиональные разделы и обсуждения рекламы клиник; прямую саморекламу без разрешения не размещать."),
    Platform("buh_1c","БУХ.1С · форум","https://buh.ru/forum/group29/","reply_only",60,
             "бухгалтеры, 1С, автоматизация учёта","аккаунт 1С",
             "Профильный форум по учёту и 1С; использовать для полезных ответов и только разрешённых публикаций."),
    Platform("ati_su","ATI.SU · форум логистики","https://forums.ati.su/forum/","paid_post",61,
             "перевозчики, логисты, владельцы транспорта","аккаунт ATI.SU",
             "Часть коммерческих разделов доступна только платным участникам — исключено режимом 0 ₽."),
    Platform("perevozka24","Перевозка 24 · форум","https://perevozka24.com/forum","paid_post",62,
             "перевозчики и владельцы спецтехники","обычная регистрация",
             "Правила прямо запрещают бесплатную рекламу — исключено режимом 0 ₽."),
    Platform("sellerexit","SellerExit","https://sellerexit.ru/","reply_only",63,
             "независимые селлеры маркетплейсов","регистрация сообщества",
             "Профильный контент о рекламе и автоматизации; формат размещения услуг требует проверки."),
]
_v3_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v3_platforms if p.key not in _v3_keys])

for _k in [
    "partnersearch","stroy_russia","vashdom_forum","house_forum","kroi_roof",
    "sdelaimebel","mp_forum","sellermap","tcfs","stom_ru","buh_1c","sellerexit",
]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})
FREE_PLATFORM_POLICY["ati_su"] = {"free": False, "reason": "paid_participant_required"}
FREE_PLATFORM_POLICY["perevozka24"] = {"free": False, "reason": "free_advertising_prohibited"}

# PLATFORM_CONTENT_PLAN_V4
_DEFAULT_SEO = {
    "topics": [
        "Как автоматизировать обработку заявок в компании",
        "Виртуальный отдел продаж вместо ручного контроля",
        "Как связать рекламу, сообщения, звонки и CRM",
    ],
    "keywords": [
        "автоматизация бизнеса","автоматизация продаж","виртуальный отдел продаж",
        "CRM автоматизация","ИИ менеджер продаж","BORIS AI",
    ],
}
_PLATFORM_SEO = {
    "stroy_forum": {
        "topics": ["Автоматизация продаж в строительной компании","Как не терять заявки на строительство и ремонт","CRM и ИИ для подрядчиков"],
        "keywords": ["автоматизация строительной компании","CRM для строителей","продажи строительных услуг","ИИ менеджер для строительства","заявки на строительство"],
    },
    "towerbuild": {
        "topics": ["Автоматизация заявок на стройматериалы и услуги","Как контролировать продажи подрядчиков","Виртуальный отдел продаж для строительного бизнеса"],
        "keywords": ["автоматизация стройматериалов","CRM подрядчика","продажи стройматериалов","автоматизация заявок","BORIS для строительства"],
    },
    "ssa": {
        "topics": ["Как автоматизировать продажи строительных услуг","Контроль заявок и менеджеров в стройкомпании","Avito + CRM для строительного бизнеса"],
        "keywords": ["автоматизация строительства","Avito для строителей","CRM строительной компании","контроль заявок","виртуальный отдел продаж"],
    },
    "forum_baza_1c": {
        "topics": ["1С и CRM без ручного переноса данных","Автоматизация продаж на базе 1С","Интеграция 1С с сайтом, CRM и мессенджерами"],
        "keywords": ["интеграция 1С CRM","автоматизация 1С","1С API","1С и сайт","1С и продажи"],
    },
    "foodmarkets": {
        "topics": ["Автоматизация продаж в оптовой компании","Как собирать B2B-заявки и не терять клиентов","CRM и реактивация для поставщиков продуктов"],
        "keywords": ["автоматизация оптовых продаж","CRM для поставщиков","HoReCa продажи","реактивация клиентов","B2B автоматизация"],
    },
    "partnersearch": {
        "topics": ["Виртуальный отдел продаж для малого и среднего бизнеса","Автоматизация поиска и обработки B2B-клиентов","Как убрать ручной перенос заявок между сервисами"],
        "keywords": ["автоматизация бизнеса","B2B продажи","виртуальный отдел продаж","автоматизация лидов","CRM интеграция"],
    },
    "tenchat": {
        "topics": ["Что реально можно автоматизировать в отделе продаж","Сколько бизнес теряет на ручной обработке заявок","Как работает виртуальный отдел продаж BORIS"],
        "keywords": ["автоматизация продаж","ИИ для бизнеса","виртуальный отдел продаж","автоматизация маркетинга","BORIS AI"],
    },
    "vk": {
        "topics": ["Кейс: заявка автоматически попадает в CRM","Что можно автоматизировать в рекламе и продажах","BORIS как виртуальный отдел продаж и рекламы"],
        "keywords": ["автоматизация бизнеса","отдел продаж под ключ","ИИ менеджер","автоматизация рекламы","CRM"],
    },
    "tg_biznesschatt": {
        "topics": ["Автоматизация заявок без найма дополнительных менеджеров","Виртуальный отдел продаж для предпринимателя","CRM + сообщения + звонки в одном процессе"],
        "keywords": ["автоматизация бизнеса","продажи предпринимателя","ИИ менеджер","CRM автоматизация","BORIS"],
    },
    "tg_networking_business": {
        "topics": ["Как предпринимателю убрать ручную работу в продажах","Автоматизация лидов и повторных касаний","Виртуальный отдел продаж для малого бизнеса"],
        "keywords": ["автоматизация малого бизнеса","поиск клиентов","реактивация базы","ИИ продажи","CRM"],
    },
    "tg_builders_moscow_2026": {
        "topics": ["Автоматизация заявок для строителей Москвы","Avito + CRM + звонки для подрядчика","Как строительной компании быстрее отвечать на лиды"],
        "keywords": ["строители Москва заявки","автоматизация стройкомпании","Avito строительство","CRM строители","ИИ менеджер"],
    },
}

def _seo_for_platform(key: str, audience: str) -> dict:
    cfg = _PLATFORM_SEO.get(key)
    if cfg:
        return cfg
    low = str(audience or "").lower()
    if any(x in low for x in ["строит", "ремонт", "кров", "спецтех"]):
        return {
            "topics": ["Автоматизация продаж строительных услуг","Как не терять заявки подрядчику","CRM и реклама для строительной компании"],
            "keywords": ["автоматизация строительства","CRM для строителей","заявки подрядчику","автоматизация продаж","BORIS"],
        }
    if any(x in low for x in ["мебел"]):
        return {
            "topics": ["Автоматизация продаж мебельного производства","Как связать заявки, замеры и CRM","Avito и CRM для мебельной компании"],
            "keywords": ["автоматизация мебельного бизнеса","CRM мебель","продажи мебели","Avito мебель","BORIS"],
        }
    if any(x in low for x in ["логист", "перевоз", "транспорт"]):
        return {
            "topics": ["Автоматизация продаж в логистике","CRM для перевозчика и диспетчера","Как не терять заявки на перевозку"],
            "keywords": ["автоматизация логистики","CRM перевозки","заявки грузоперевозки","диспетчеризация","BORIS"],
        }
    if any(x in low for x in ["риел", "недвиж"]):
        return {
            "topics": ["Автоматизация агентства недвижимости","ИИ-менеджер для обработки заявок риелтора","Реактивация старой базы недвижимости"],
            "keywords": ["автоматизация недвижимости","CRM риелтор","ИИ менеджер недвижимости","реактивация базы","BORIS"],
        }
    if any(x in low for x in ["селлер", "marketplace", "маркетплейс", "e-commerce"]):
        return {
            "topics": ["Автоматизация работы селлера","Маркетплейсы + CRM без ручного переноса","Контроль заказов и аналитики селлера"],
            "keywords": ["автоматизация маркетплейсов","CRM селлер","Ozon Wildberries автоматизация","учёт заказов маркетплейсов","BORIS"],
        }
    return _DEFAULT_SEO

def list_platforms():
    items = []
    for p in sorted(PLATFORMS, key=lambda p: p.priority):
        row = asdict(p)
        row["cost_policy"] = "free_only"
        row["free_policy"] = platform_policy(p.key)
        row["enabled_for_outreach"] = bool(row["free_policy"].get("free"))
        seo = _seo_for_platform(p.key, p.audience)
        row["seo_topics"] = seo["topics"]
        row["seo_keywords"] = seo["keywords"]
        items.append(row)
    return items

# FRESH_POLICY_V5
def platform_policy(key: str) -> dict:
    base = dict(FREE_PLATFORM_POLICY.get(key, {"free": False, "reason": "not_verified"}))
    rule_path = STATE_DIR / "rules" / f"{key}.json"
    if not rule_path.exists():
        return base
    try:
        snap = json.loads(rule_path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(snap.get("checked_at") or "").replace("Z", "+00:00"))
        stale = (datetime.now(timezone.utc) - checked).total_seconds() > 7 * 86400
        decision = str(snap.get("decision") or "")
        base["rule_decision"] = decision
        base["rule_checked_at"] = snap.get("checked_at")
        base["rule_requirements"] = list(snap.get("requirements") or [])
        if stale:
            base.update({"free": False, "reason": "rules_snapshot_stale"})
            return base
        if decision == "blocked":
            base.update({"free": False, "reason": "rules_block_or_paid"})
        elif decision == "review":
            base.update({"free": False, "reason": "rules_ambiguous"})
        elif decision == "reply_only":
            base.update({"free": True, "action": "reply_only", "rule_check": True})
        elif decision == "allowed":
            if base.get("reason") not in {"paid_advertising","commercial_topics_paid_only","paid_service_placement","paid_participant_required","free_advertising_prohibited"}:
                base.update({"free": True, "action": base.get("action") or "rules_allowed", "rule_check": True})
    except Exception:
        base.update({"free": False, "reason": "rules_snapshot_invalid"})
    return base

# FREE_ONLY_POLICY_V6
_RULE_AUDIT_CAN_UNLOCK = {
    "rule_audit_required",
    "rules_not_verified_for_zero_cost_links",
    "current_advertising_rules_not_verified",
    "proactive_advertising_rules_not_verified",
    "not_verified",
}
_NEVER_UNLOCK_REASONS = {
    "paid_advertising",
    "commercial_topics_paid_only",
    "paid_service_placement",
    "paid_participant_required",
    "free_advertising_prohibited",
    "responses_may_require_payment",
    "marketplace_cost_model_not_zero_only",
    "zero_cost_response_path_not_verified",
    "paid_features_or_cost_not_verified",
    "automated_access_prohibited",
    "commercial_use_prohibited",
    "automated_access_not_verified",
}

def platform_policy(key: str) -> dict:
    base = dict(FREE_PLATFORM_POLICY.get(key, {"free": False, "reason": "not_verified"}))
    original_reason = str(base.get("reason") or "")
    if original_reason in _NEVER_UNLOCK_REASONS:
        base["free"] = False
        return base

    rule_path = STATE_DIR / "rules" / f"{key}.json"
    if not rule_path.exists():
        return base
    try:
        snap = json.loads(rule_path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(snap.get("checked_at") or "").replace("Z", "+00:00"))
        stale = (datetime.now(timezone.utc) - checked).total_seconds() > 7 * 86400
        decision = str(snap.get("decision") or "")
        base["rule_decision"] = decision
        base["rule_checked_at"] = snap.get("checked_at")
        base["rule_requirements"] = list(snap.get("requirements") or [])
        if stale:
            base.update({"free": False, "reason": "rules_snapshot_stale"})
            return base
        if decision == "blocked":
            base.update({"free": False, "reason": "rules_block_or_paid"})
        elif decision == "review":
            base.update({"free": False, "reason": "rules_ambiguous"})
        elif decision == "reply_only":
            if base.get("free") or original_reason in _RULE_AUDIT_CAN_UNLOCK:
                base.update({"free": True, "action": "reply_only", "rule_check": True})
        elif decision == "allowed":
            if base.get("free") or original_reason in _RULE_AUDIT_CAN_UNLOCK:
                base.update({"free": True, "action": base.get("action") or "rules_allowed", "rule_check": True})
    except Exception:
        base.update({"free": False, "reason": "rules_snapshot_invalid"})
    return base

# DYNAMIC_DISCOVERY_V7
_DYNAMIC_SURFACE_BROAD_MARKERS = (
    "marketplace", "classified", "free advertising", "buy & sell", "buy and sell",
    "buy sell", "объявлен", "барахол", "куплю", "продам",
)
_DYNAMIC_SURFACE_SERVICE_MARKERS = (
    "services", "service", "freelance", "for hire", "employment", "jobs",
    "услуги", "исполнител", "подрядчик", "работа",
)
_DYNAMIC_SURFACE_GOODS_MARKERS = (
    "for sale", "products", "goods", "vendors", "suppliers",
    "товары", "продажа", "поставщик",
)

# Dynamic directory discovery often finds a real classifieds section inside a
# very narrow hobby/product community. Such a section is valid capacity only
# for matching client goods, not for every goods client. Keep broad commercial
# boards universal, but constrain narrow directory categories with semantic
# stems that the client's live-site keywords must contain.
_DYNAMIC_GOODS_VERTICAL_TERMS = (
    (
        ("electricals-and-electronic-goods", "computers-and-internet/hardware", "electronics", "home theater", "audio marketplace"),
        ["электрон", "техник", "аудио", "телевиз", "компьют", "гаджет", "процессор", "hardware", "electronics", "audio", "video"],
    ),
    (
        ("clothing-and-fashion", "fashion", "rainwear", "stockings"),
        ["одеж", "обув", "текстил", "мод", "fashion", "clothing", "apparel", "rainwear"],
    ),
    (
        ("pet-animal-care", "/pets/", "chinchilla", "hedgehog", "avian", "bird"),
        ["зоотовар", "питом", "животн", "корм", "птиц", "pet", "animal", "bird"],
    ),
    (
        ("drones", "drone", "uav", "multirotor"),
        ["дрон", "квадрокоп", "бпла", "uav", "drone"],
    ),
    (
        ("toys-and-collectibles", "lego", "collectible", "comic", "toy"),
        ["игруш", "коллекц", "lego", "комикс", "toy", "collectible"],
    ),
    (
        ("photography", "camera", "photo"),
        ["фото", "камер", "объектив", "photography", "camera", "lens"],
    ),
    (
        ("woodworking", "woodwork", "woodworker"),
        ["дерев", "столяр", "деревообработ", "инструмент", "wood", "woodwork"],
    ),
    (
        ("gardening", "garden", "plant", "terraforums"),
        ["сад", "дач", "растен", "семен", "garden", "plant"],
    ),
    (
        ("cooking", "food", "chef"),
        ["продукт", "еда", "кухн", "пищ", "food", "cooking", "kitchen"],
    ),
    (
        ("jewellery-and-watches", "jewelry", "watch"),
        ["ювелир", "украшен", "час", "jewel", "watch"],
    ),
    (
        ("cars-and-vehicles", "automotive", "car parts"),
        ["авто", "машин", "запчаст", "автотовар", "car", "auto", "vehicle"],
    ),
)


def _dynamic_goods_required_terms(candidate: dict, signal: str) -> list[str]:
    source = " ".join([
        str(candidate.get("directory_category") or ""),
        str(candidate.get("query") or ""),
        str(candidate.get("source_surface_label") or ""),
        str(candidate.get("name") or ""),
        signal,
    ]).lower()

    # Explicit broad B2B/classifieds sources are intentionally universal.
    broad_markers = (
        "shopping-and-ecommerce/classifieds",
        "business marketplace",
        "wholesale suppliers",
        "товары для бизнеса",
        "оптовая торговля",
        "предложения поставщиков",
        "доска объявлений товаров",
    )
    if any(x in source for x in broad_markers):
        return []

    for markers, terms in _DYNAMIC_GOODS_VERTICAL_TERMS:
        if any(marker in source for marker in markers):
            return list(terms)

    # Any directory-specific category that was not explicitly mapped above
    # is not proof that the marketplace is relevant to arbitrary goods. Fail
    # closed until its vertical is curated. Only explicit broad classifieds/B2B
    # categories are allowed to remain universal.
    if str(candidate.get("directory_category") or "").strip():
        return ["__goods_vertical_review_required__"]
    return []


def _dynamic_verified_publication_surface(candidate: dict) -> list[dict]:
    """Promote only an exact commercial forum section, never a generic root.

    Rule permission is checked by list_platforms/platform_policy separately.
    This helper only proves that discovery identified a concrete publication
    surface rather than a generic forum homepage.
    """
    if not candidate or not candidate.get("commercial_context"):
        return []
    url = str(candidate.get("url") or "").strip()
    if not url:
        return []

    label = str(candidate.get("source_surface_label") or "").strip()
    name = str(candidate.get("name") or "").strip()
    evidence = " ".join(str(x) for x in (candidate.get("evidence") or []))
    parsed = urlparse(url)
    path_text = f"{parsed.path} {parsed.query}".lower()
    label_text = label.lower()
    name_text = name.lower()
    all_markers = (
        _DYNAMIC_SURFACE_BROAD_MARKERS
        + _DYNAMIC_SURFACE_SERVICE_MARKERS
        + _DYNAMIC_SURFACE_GOODS_MARKERS
    )

    explicit_label = bool(label) and any(x in label_text for x in all_markers)
    commercial_in_url = any(x.replace(" ", "-") in path_text or x in path_text for x in all_markers)
    commercial_in_name = any(x in name_text for x in all_markers)
    section_shape = bool(
        re.search(
            r"(forum\d+|forumdisplay|viewforum|board=|category/|/forums?/[^/?#]+)",
            f"{parsed.path}?{parsed.query}".lower(),
        )
    )
    if not (explicit_label or (section_shape and (commercial_in_url or commercial_in_name))):
        return []

    # Classify the client format from the exact section title/URL only.
    # Generic page evidence may mention ads or classifieds elsewhere in the
    # forum and must not inflate a service section into goods capacity.
    signal = f"{label} {name} {url}".lower()
    discovered_niche = str(candidate.get("niche") or "").lower()
    niches: set[str] = set()

    explicit_service = any(x in signal for x in _DYNAMIC_SURFACE_SERVICE_MARKERS)
    explicit_goods = any(x in signal for x in _DYNAMIC_SURFACE_GOODS_MARKERS)
    explicit_broad = any(x in signal for x in _DYNAMIC_SURFACE_BROAD_MARKERS)
    explicit_both = any(
        x in signal
        for x in (
            "goods and services", "products and services", "товары и услуги",
            "услуги и товары", "buy sell trade services",
        )
    )

    if explicit_both:
        niches.update({"goods", "services"})
    else:
        if explicit_service:
            niches.add("services")
        if explicit_goods:
            niches.add("goods")
        if explicit_broad and not niches:
            if discovered_niche in {"goods", "marketplaces", "furniture", "horeca", "auto"}:
                niches.add("goods")
            elif discovered_niche in {
                "services", "it", "marketing", "business", "construction", "logistics",
                "realestate", "medicine", "accounting",
            }:
                niches.add("services")

    if not niches:
        return []

    required_terms = (
        _dynamic_goods_required_terms(candidate, signal)
        if "goods" in niches else []
    )

    return [{
        "id": "dynamic_verified_commercial_surface",
        "name": label or name or parsed.netloc,
        "url": url,
        "post_url": None,
        "niches": sorted(niches),
        "required_terms": required_terms,
        "language": "ru" if re.search(r"[а-яё]", signal) else "en",
        "evidence": (
            "Exact commercial forum surface discovered from the live/directory "
            "page; delivery still requires a fresh allowed rule audit."
        ),
    }]


def _discovered_platform_objects() -> list[Platform]:
    try:
        from app.services import forum_discovery
        rows = forum_discovery.list_candidates(min_score=8)
    except Exception:
        return []
    static_domains = set()
    for p in PLATFORMS:
        try:
            static_domains.add(urlparse(p.url).netloc.lower().removeprefix("www."))
        except Exception:
            pass
    out: list[Platform] = []
    base_priority = 1000
    for idx, row in enumerate(rows):
        domain = str(row.get("domain") or "").lower().removeprefix("www.")
        duplicate_static = any(
            domain == known
            or domain.endswith("." + known)
            or known.endswith("." + domain)
            for known in static_domains
        )
        if not domain or duplicate_static:
            continue
        # Candidate must show a business/service context; generic game/community
        # forums are never promoted merely because they contain the word forum.
        if not row.get("commercial_context"):
            continue
        if int(row.get("score") or 0) < 10:
            continue
        key = str(row.get("key") or f"disc_{idx}")
        out.append(Platform(
            key=key,
            name=str(row.get("name") or domain)[:160],
            url=str(row.get("url") or ""),
            mode="direct_post" if row.get("create_topic_hint") else "reply_only",
            priority=base_priority + idx,
            audience=f"Автоматически найдено: {row.get('niche') or 'business'}",
            registration="автоматическая разведка регистрации",
            notes=f"Найдено поисковым discovery: {row.get('query') or ''}. Сначала обязательный rule-audit.",
        ))
    return out

def all_platform_objects() -> list[Platform]:
    merged: dict[str, Platform] = {p.key: p for p in PLATFORMS}
    for p in _discovered_platform_objects():
        merged.setdefault(p.key, p)
    return sorted(merged.values(), key=lambda x: x.priority)

def get_platform(key: str) -> Platform | None:
    return next((p for p in all_platform_objects() if p.key == key), None)

_EXPLICIT_FORUM_KEYS = {
    "wmboard_services",
    "webledi_services",
}

def _channel_type(p: Platform) -> str:
    u = str(p.url or "").lower()
    k = str(p.key or "").lower()
    n = str(p.name or "").lower()
    if k in _EXPLICIT_FORUM_KEYS:
        return "forum"
    if k.startswith("tg_") or "t.me/" in u:
        return "telegram"
    if k == "vk" or "vk.com/" in u:
        return "social"
    if k in {"fl","workspace","profi","kwork"}:
        return "service_marketplace"
    if k in {"mmgp","mastergrad"}:
        return "forum"
    if "forum" in u or "форум" in n or any(x in u for x in ["viewforum","/forums","community"]):
        return "forum"
    return "community"

def _niche_label(audience: str) -> str:
    low = str(audience or "").lower()
    # Dynamic discovery stores its canonical niche in the audience marker.
    # Preserve that exact label instead of re-inferring it from a translated
    # description; otherwise discovered goods/forums were silently counted as
    # generic services and reserve coverage became wrong.
    marker = "автоматически найдено:"
    if marker in low:
        discovered = low.split(marker, 1)[1].strip().split()[0] if low.split(marker, 1)[1].strip() else ""
        if discovered in {
            "business", "construction", "it", "marketing", "goods", "services",
            "furniture", "logistics", "auto", "realestate", "marketplaces",
            "horeca", "medicine", "accounting", "manufacturing",
        }:
            return discovered
    groups = [
        ("construction", ["строит","ремонт","кров","спецтех"]),
        ("marketing", ["маркет","seo","реклам","вебмастер"]),
        ("marketplaces", ["селлер","маркетплейс","wildberries","ozon","e-commerce"]),
        ("furniture", ["мебел"]),
        ("logistics", ["логист","перевоз","транспорт"]),
        ("auto", ["автосервис","автобизнес","автозапчаст"]),
        ("realestate", ["риел","недвиж"]),
        ("horeca", ["horeca","продукт","оптов"]),
        ("medicine", ["медицин","стомат","клиник"]),
        ("manufacturing", ["промышлен","производств","металло"]),
        ("it", ["it","разработ","1с","битрикс","crm"]),
        ("business", ["предприним","бизнес","b2b","компан"]),
    ]
    for key, terms in groups:
        if any(t in low for t in terms):
            return key
    return "services"

VERIFIED_PUBLICATION_SURFACES = {
    "forum_baza_1c": [
        {
            "id": "freelance_1c",
            "name": "1С фриланс",
            "url": "https://forum-baza.ru/index.php?board=62.0",
            "post_url": "https://forum-baza.ru/index.php?action=post;board=62.0",
            "niches": ["it"],
            "required_terms": ["1с", "1c"],
            "evidence": "Раздел прямо разрешает предложения услуг разработчиков/программистов 1С.",
        },
        {
            "id": "franchisee_services",
            "name": "Компании 1С ФРАНЧАЙЗИ",
            "url": "https://forum-baza.ru/index.php?board=65.0",
            "post_url": "https://forum-baza.ru/index.php?action=post;board=65.0",
            "niches": ["it", "business"],
            "required_terms": ["1с", "1c"],
            "evidence": "Раздел прямо приглашает бесплатно размещать информацию об услугах и контактах.",
        },
    ],
    "mmgp": [
        {
            "id": "site_development_services",
            "name": "Услуги разработки сайтов",
            "url": "https://mmgp.com/forums/uslugi-razrabotki-sajtov.337/",
            "post_url": "https://mmgp.com/forums/uslugi-razrabotki-sajtov.337/post-thread",
            "niches": ["it", "marketing", "services"],
            "required_terms": ["сайт", "лендинг", "web", "веб", "интернет-магазин"],
            "evidence": "Раздел прямо предназначен для предложений услуг разработки сайтов.",
        },
        {
            "id": "programming_services",
            "name": "Услуги программирования",
            "url": "https://mmgp.com/forums/uslugi-programmirovanija.135/",
            "post_url": "https://mmgp.com/forums/uslugi-programmirovanija.135/post-thread",
            "niches": ["it", "marketing"],
            "required_terms": ["програм", "разработ", "бот", "автомат", "интеграц", "crm", "сервис"],
            "evidence": "Раздел прямо предназначен для услуг программирования; есть свежие темы про AI-сервисы и автоматизацию.",
        },
        {
            "id": "seo_marketing_services",
            "name": "Услуги SEO продвижения и маркетинга",
            "url": "https://mmgp.com/forums/uslugi-seo-prodvizhenija-i-marketinga.195/",
            "post_url": "https://mmgp.com/forums/uslugi-seo-prodvizhenija-i-marketinga.195/post-thread",
            "niches": ["marketing"],
            "required_terms": [],
            "evidence": "Раздел прямо предназначен для предложений SEO и маркетинговых услуг.",
        },
    ],
    "saitsozdanie_forum": [
        {
            "id": "seo_services",
            "name": "SEO услуги",
            "url": "https://saitsozdanie.ru/forum/index.php?board=35.0",
            "post_url": "https://saitsozdanie.ru/forum/index.php?action=post;board=35.0",
            "niches": ["marketing"],
            "required_terms": [],
            "evidence": "Раздел «Где что заказать → SEO услуги» содержит предложения SEO-услуг.",
        },
        {
            "id": "internet_marketing_services",
            "name": "Услуги интернет-маркетинга",
            "url": "https://saitsozdanie.ru/forum/index.php?board=137.0",
            "post_url": "https://saitsozdanie.ru/forum/index.php?action=post;board=137.0",
            "niches": ["marketing"],
            "required_terms": [],
            "evidence": "Раздел прямо предназначен для предложений интернет-маркетинговых услуг.",
        },
        {
            "id": "smm_services",
            "name": "Услуги SMM",
            "url": "https://saitsozdanie.ru/forum/index.php?board=141.0",
            "post_url": "https://saitsozdanie.ru/forum/index.php?action=post;board=141.0",
            "niches": ["marketing"],
            "required_terms": ["smm", "соц", "telegram", "телеграм", "vk", "вк"],
            "evidence": "Раздел прямо предназначен для услуг SMM и продвижения в социальных сетях.",
        },
        {
            "id": "context_ads_services",
            "name": "Услуги по настройке контекстной рекламы",
            "url": "https://saitsozdanie.ru/forum/index.php?board=157.0",
            "post_url": "https://saitsozdanie.ru/forum/index.php?action=post;board=157.0",
            "niches": ["marketing"],
            "required_terms": ["контекст", "директ", "реклам"],
            "evidence": "Отдельный раздел услуг по настройке контекстной рекламы.",
        },
        {
            "id": "it_announcements",
            "name": "Объявления IT",
            "url": "https://saitsozdanie.ru/forum/index.php?board=152.0",
            "post_url": "https://saitsozdanie.ru/forum/index.php?action=post;board=152.0",
            "niches": ["it", "marketing"],
            "required_terms": ["автомат", "бот", "crm", "разработ", "сервис", "сайт"],
            "evidence": "IT-доска содержит предложения разработки, настройки, автоматизации и продвижения.",
        },
    ],
    "forum_seo_net": [
        {
            "id": "seo_services_traffic",
            "name": "SEO-услуги и Трафик",
            "url": "https://forum-seo.net/forums/seo-uslugi-i-trafik.6/",
            "post_url": "https://forum-seo.net/forums/seo-uslugi-i-trafik.6/post-thread",
            "niches": ["marketing"],
            "required_terms": [],
            "evidence": "Коммерческий SEO-раздел содержит предложения SEO и комплексных интернет-маркетинговых услуг.",
        },
        {
            "id": "social_smm_services",
            "name": "Продвижение в соцсетях и SMM",
            "url": "https://forum-seo.net/forums/prodvizheniye-v-sotssetyakh-i-smm.68/",
            "post_url": "https://forum-seo.net/forums/prodvizheniye-v-sotssetyakh-i-smm.68/post-thread",
            "niches": ["marketing"],
            "required_terms": ["smm", "соц", "telegram", "телеграм", "vk", "вк"],
            "evidence": "Раздел прямо описан как услуги продвижения в соцсетях и мессенджерах.",
        },
    ],
    "foodmarkets": [
        {
            "id": "nonfood_goods_board",
            "name": "NonFood · доска непродовольственных товаров",
            "url": "https://non.foodmarkets.ru/blurb/category/1/page1/",
            "post_url": "https://non.foodmarkets.ru/blurb/post_topic",
            "niches": ["goods", "business"],
            "required_terms": [],
            "rule_requirement": "use_verified_nonfood_goods_board_only",
            "evidence": "Универсальная B2B-доска непродовольственных товаров: предложение, спрос, поиск дистрибьюторов; живые объявления по стройматериалам, товарам для дома, химии, электронике и другим категориям. Пользовательское соглашение разрешает ссылки на другие информационные ресурсы.",
        },
    ],
    "partnersearch": [
        {
            "id": "b2b_services",
            "name": "Услуги и оборудование для бизнеса",
            "url": "https://www.partnersearch.ru/business/viewforum.php?f=22",
            "post_url": "https://www.partnersearch.ru/business/posting.php?mode=post&f=22",
            "niches": ["business", "marketing", "it", "services"],
            "required_terms": [],
            "evidence": "B2B-раздел прямо предназначен для деловых предложений и содержит свежие предложения CRM, ИИ-интеграций и автоматизации.",
        },
        {
            "id": "wholesale_goods",
            "name": "Оптовая торговля · товары оптом",
            "url": "https://www.partnersearch.ru/business/viewforum.php?f=28",
            "post_url": "https://www.partnersearch.ru/business/posting.php?mode=post&f=28",
            "niches": ["goods", "business"],
            "required_terms": [],
            "rule_requirement": "use_verified_wholesale_goods_section_only",
            "evidence": "Раздел прямо описан как оптовые продажи, организация сбыта и поиск поставщиков; живые темы предлагают товары оптом. Штатная «Новая тема» и маршрут создания темы проверены 2026-09-08.",
        },
    ],
    "disc_affiliate_forum": [
        {
            "id": "services",
            "name": "Услуги",
            "url": "https://affiliate.forum/forums/uslugi.28/",
            "post_url": "https://affiliate.forum/forums/uslugi.28/post-thread",
            "niches": ["marketing", "business", "it"],
            "required_terms": [],
            "evidence": "Отдельный коммерческий раздел услуг интернет-маркетингового форума.",
        },
    ],
    "disc_forum_amit_ru": [
        {
            "id": "advertising_services",
            "name": "Рекламный раздел · Услуги",
            "url": "https://forum-amit.ru/index.php?board=6.0",
            "post_url": "https://forum-amit.ru/index.php?action=post;board=6.0",
            "niches": ["it", "marketing", "business"],
            "required_terms": ["реклам", "автомат", "разработ", "сайт", "crm", "бот", "интеграц"],
            "evidence": "Раздел прямо помечен как рекламный раздел услуг; в нём есть действующие предложения разработки сайтов и других бизнес-услуг. Маршрут создания темы проверен 2026-09-06 и корректно требует авторизацию.",
        },
    ],
    "regforum_services": [
        {
            "id": "legal_services_offer",
            "name": "Услуги (предложение)",
            "url": "https://regforum.ru/forum/forums/39/",
            "post_url": "https://regforum.ru/forum/forums/39/post-thread",
            "niches": ["business"],
            "required_terms": ["юрид", "бухгалтер", "налог", "регистрац", "лиценз"],
            "evidence": "Раздел предназначен именно для юридических, бухгалтерских и лицензионно-разрешительных услуг.",
        },
    ],
    "disc_forum_investsteel_ru": [
        {
            "id": "industrial_services",
            "name": "Услуги",
            "url": "https://forum.investsteel.ru/category/73/uslugi",
            "post_url": None,
            "niches": ["manufacturing", "construction", "logistics", "services"],
            "required_terms": [
                "металл", "свар", "станк", "производ", "промышлен",
                "монтаж", "проект", "логист", "строител",
            ],
            "evidence": "Раздел прямо приглашает публиковать услуги предприятий и специалистов по металлообработке, монтажу, проектированию, логистике и другим промышленным направлениям; в живой выдаче есть строительство и автоматизация производства.",
        },
    ],
}


def list_platforms():
    items = []
    try:
        from app.services import forum_discovery
        dynamic_candidates = {
            str(x.get("key") or ""): x
            for x in forum_discovery.list_candidates(min_score=8)
            if str(x.get("key") or "")
        }
    except Exception:
        dynamic_candidates = {}
    dynamic_keys = set(dynamic_candidates)
    existing_regs = {x.get("platform"): x for x in _load(REGISTRATIONS_FILE, [])}
    for p in all_platform_objects():
        row = asdict(p)
        row["cost_policy"] = "free_only"
        row["free_policy"] = platform_policy(p.key)
        row["enabled_for_outreach"] = bool(row["free_policy"].get("free"))
        row["discovered_automatically"] = p.key in dynamic_keys
        row["channel_type"] = _channel_type(p)
        row["niche"] = _niche_label(p.audience)
        reg = existing_regs.get(p.key, {})
        row["registration_status"] = reg.get("status", "not_registered")
        row["registration_checkpoint"] = reg.get("checkpoint")
        row["registration_terminal_blocked"] = registration_is_terminally_blocked(reg)
        row["registration_warming"] = registration_is_warming(reg)
        row["registration_recoverable"] = registration_is_recoverable(reg)
        row["publication_ready"] = bool(
            row["enabled_for_outreach"]
            and (row["channel_type"] != "forum" or row["registration_status"] == "ready")
        )
        rule_requirements = set(row["free_policy"].get("rule_requirements") or [])
        row["publication_surfaces"] = [
            dict(x)
            for x in VERIFIED_PUBLICATION_SURFACES.get(p.key, [])
            if not x.get("rule_requirement")
            or str(x.get("rule_requirement")) in rule_requirements
        ]
        if (
            not row["publication_surfaces"]
            and row["discovered_automatically"]
            and row["free_policy"].get("rule_decision") == "allowed"
        ):
            row["publication_surfaces"] = _dynamic_verified_publication_surface(
                dynamic_candidates.get(p.key) or {}
            )
        seo = _seo_for_platform(p.key, p.audience)
        row["seo_topics"] = seo["topics"]
        row["seo_keywords"] = seo["keywords"]
        items.append(row)
    return items

def registration_plan(email: str | None = None) -> list[dict]:
    existing = {x.get("platform"): x for x in _load(REGISTRATIONS_FILE, [])}
    if not email:
        mailboxes = _load(MAILBOXES_FILE, [])
        if mailboxes:
            email = str(mailboxes[0].get("address") or "").strip() or None
    out = []
    for p in all_platform_objects():
        if p.key == "profi":
            checkpoint = "phone_sms_required"
        elif p.key.startswith("tg_") or p.key == "vk":
            checkpoint = "social_account_required"
        else:
            checkpoint = "manual_verification"
        current = existing.get(p.key, {})
        out.append({
            "platform": p.key,
            "name": p.name,
            "url": p.url,
            "mode": p.mode,
            "email": email if checkpoint == "manual_verification" else current.get("email"),
            "status": current.get("status", "not_registered"),
            "checkpoint": current.get("checkpoint", checkpoint),
            "account_url": current.get("account_url"),
            "last_error": current.get("last_error"),
            "created_at": current.get("created_at"),
            "warming_started_at": current.get("warming_started_at"),
            "updated_at": current.get("updated_at"),
            "terminal_blocked": registration_is_terminally_blocked(current),
            "recoverable": registration_is_recoverable(current),
            "notes": p.notes,
            "discovered_automatically": p.key.startswith("disc_"),
        })
    return out

HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS = {
    "manual_verification": "Один раз пройти обычную регистрацию/вход на площадке",
    "captcha_required": "Пройти антибот-проверку площадки один раз",
    "captcha_age_and_terms_required": "Один раз пройти CAPTCHA, указать дату рождения и принять условия регистрации площадки",
    "registration_agreement_required": "Один раз принять правила регистрации площадки",
    "terms_acceptance_required": "Один раз принять условия регистрации площадки",
    "age_and_terms_declaration_required": "Один раз подтвердить требуемые площадкой условия регистрации",
    "email_verification_required": "Подтвердить регистрационное письмо площадки",
    "sms_or_verification_code_required": "Один раз подтвердить SMS или код площадки",
    "phone_sms_required": "Один раз подтвердить телефон/SMS на площадке",
    "business_email_required": "Указать корпоративную почту для регистрации",
    "real_identity_required": "Пройти требуемую площадкой проверку личности",
    "business_email_and_real_identity_required": "Указать корпоративную почту и пройти проверку личности",
    "external_account_sso_required": "Один раз создать/авторизовать внешний аккаунт или SSO площадки",
}

def platform_bootstrap_queue() -> dict:
    """One-time human platform setup, never client-owner operational work.

    Only rules-allowed forum platforms are included. Dead/disabled/rejected
    registration routes are deliberately excluded. Completing one item makes
    the account reusable across all future Crowd SEO client projects.
    """
    platforms = {
        x.get("key"): x
        for x in list_platforms()
        if x.get("channel_type") == "forum"
    }
    registrations = {x.get("platform"): x for x in registration_plan()}
    items = []

    for key, platform in platforms.items():
        if not platform.get("enabled_for_outreach") or platform.get("publication_ready"):
            continue
        reg = registrations.get(key) or {}
        checkpoint = str(reg.get("checkpoint") or "")
        action = HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS.get(checkpoint)
        if not action:
            continue
        maturity_requirements = dict(
            PLATFORM_MATURITY_REQUIREMENTS.get(key, {})
        )
        surfaces = VERIFIED_PUBLICATION_SURFACES.get(key, [])
        niches = {
            str(n).strip().lower()
            for surface in surfaces
            for n in (surface.get("niches") or [])
            if str(n).strip()
        }
        supported_client_formats = [
            fmt for fmt in ("goods", "services") if fmt in niches
        ]
        starts_warmup = bool(maturity_requirements)
        items.append({
            "platform": key,
            "name": platform.get("name"),
            "url": platform.get("url"),
            "account_url": reg.get("account_url") or platform.get("url"),
            "checkpoint": checkpoint,
            "status": reg.get("status"),
            "last_error": reg.get("last_error"),
            "action": action,
            "human_action_required": True,
            "owner_action_required": False,
            "executor_role": "platform_onboarding_worker",
            "one_time": True,
            "reusable_across_clients": True,
            "supported_client_formats": supported_client_formats,
            "maturity_requirements": maturity_requirements,
            "starts_warmup_after_bootstrap": starts_warmup,
            "after_completion": (
                "BORIS автоматически проверит аккаунт, переведёт его в WARMING "
                "и будет перепроверять готовность раз в сутки; после выполнения "
                "требований площадка станет READY."
                if starts_warmup
                else "BORIS автоматически проверит аккаунт и переведёт площадку в READY."
            ),
            "priority": int(platform.get("priority") or 9999),
        })

    items.sort(key=lambda x: (x["priority"], x["name"] or ""))
    by_checkpoint = {}
    for item in items:
        cp = item["checkpoint"]
        by_checkpoint[cp] = int(by_checkpoint.get(cp) or 0) + 1

    return {
        "needed": len(items),
        "owner_action_required": False,
        "human_action_required": bool(items),
        "reusable_across_clients": True,
        "executor_role": "platform_onboarding_worker",
        "by_checkpoint": by_checkpoint,
        "items": items,
    }


# CURATED_LIVE_SEEDS_V8
_v8 = [
    Platform("supplier_forum","Форум Поставщиков","https://forum.tvoipostavshik.ru/","direct_post",64,
             "поставщики, оптовые компании, производители, B2B","обычная регистрация",
             "Есть разделы поиска партнёров, поставщиков и помощи бизнесу/услуг; публикация только после проверки правил."),
    Platform("autopeople","AutoPeople · форум автосервисов","https://autopeople.ru/forum/repair/","reply_only",65,
             "автосервисы, ремонт автомобилей, автобизнес","обычная регистрация",
             "Живой профильный форум; прямую рекламу включать только после rule-audit конкретного раздела."),
]
_v8_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v8 if p.key not in _v8_keys])
FREE_PLATFORM_POLICY.setdefault("supplier_forum", {"free": False, "reason": "rule_audit_required"})
FREE_PLATFORM_POLICY.setdefault("autopeople", {"free": False, "reason": "rule_audit_required"})

# VERIFIED_ZERO_COST_BLOCKS_V9
FREE_PLATFORM_POLICY["supplier_forum"] = {"free": False, "reason": "terms_prohibit_advertising_or_spam"}
FREE_PLATFORM_POLICY["autopeople"] = {"free": False, "reason": "advertising_is_paid_product"}
_NEVER_UNLOCK_REASONS.update({"terms_prohibit_advertising_or_spam","advertising_is_paid_product"})

# DYNAMIC_DRAFT_GENERATOR_V10
def _platform_content_language(platform_key: str) -> str:
    languages = {
        str(surface.get("language") or "").strip().lower()
        for surface in VERIFIED_PUBLICATION_SURFACES.get(platform_key, [])
        if str(surface.get("language") or "").strip()
    }
    return "en" if languages == {"en"} else "ru"


def _ensure_english_contact_block(text: str) -> str:
    raw = str(text or "").rstrip()
    # Remove a legacy Russian contact label if an old draft is being refreshed.
    lines = [
        line for line in raw.splitlines()
        if not line.strip().startswith("Консультация:")
    ]
    value = "\n".join(lines).rstrip()
    missing = []
    if BORIS_SITE not in value:
        missing.append(f"BORIS: {BORIS_SITE}")
    if WHATSAPP_URL not in value:
        missing.append(f"WhatsApp: {WHATSAPP_URL}")
    if MAX_URL not in value:
        missing.append(f"MAX: {MAX_URL}")
    if CONSULT_PHONE_DISPLAY not in value:
        missing.append(f"Consultation: {CONSULT_PHONE_DISPLAY}")
    return value + (("\n\n" + "\n".join(missing)) if missing else "")


def _generate_english_platform_drafts(p: Platform, topic="all", variants=3) -> list[dict]:
    if topic != "all" and topic not in TOPICS:
        raise ValueError("unknown topic")
    topic_keys = list(TOPICS) if topic == "all" else [topic]
    labels = {
        "virtual_department": "AI sales and marketing operations",
        "development": "Business automation and software development",
        "avito": "Marketplace and classifieds automation",
        "direct": "Paid advertising automation and analytics",
        "sales": "Sales process automation",
        "integrations": "CRM, API and system integrations",
    }
    angles = [
        "We help businesses reduce manual work around leads, CRM, messages, calls, reporting and integrations.",
        "The goal is to remove repetitive operational work and connect lead handling, CRM, communications and analytics into one controlled workflow.",
        "We design and implement practical automation around sales, customer communication, integrations, reporting and AI-assisted workflows.",
    ]
    ctas = [
        "Share the current process and we can identify the first automation step worth implementing.",
        "Describe where manual work or lost leads are concentrated and we can outline a practical implementation scope.",
        "We can review one process first and define the expected result, scope and commercial terms before development.",
    ]
    out = []
    for topic_key in topic_keys:
        title = labels.get(topic_key, "Business automation services")
        for i in range(max(1, min(int(variants), 20))):
            text = (
                f"{title}\n\n"
                f"{angles[i % len(angles)]}\n\n"
                "Typical work includes CRM and API integrations, lead routing, messaging and call workflows, dashboards, "
                "reactivation, business process automation and custom software where needed.\n\n"
                "Pricing and delivery time depend on the scope and are confirmed after reviewing the current process.\n\n"
                f"{ctas[i % len(ctas)]}"
            )
            out.append({
                "id": _draft_id(p.key, topic_key, i),
                "platform": p.key,
                "platform_name": p.name,
                "topic": topic_key,
                "title": f"{title} — BORIS" if i == 0 else f"{title} — implementation services",
                "text": text,
                "site_url": BORIS_SITE,
                "status": "needs_owner_approval",
                "generated_without_openai": True,
                "variant": i + 1,
                "copy_style": "standard_en",
                "content_language": "en",
                "discovered_automatically": True,
            })
    return out


def _generate_dynamic_platform_drafts(p: Platform, topic="all", variants=3) -> list[dict]:
    if _platform_content_language(p.key) == "en":
        return _generate_english_platform_drafts(p, topic=topic, variants=variants)
    if topic != "all" and topic not in TOPICS:
        raise ValueError("unknown topic")
    ts = TOPICS.items() if topic == "all" else [(topic, TOPICS[topic])]
    hooks = [
        "Если часть заявок и контроля держится на ручной работе, это можно убрать программированием.",
        "Автоматизация должна экономить время и не давать заявкам теряться, а не добавлять ещё один кабинет.",
        "Автоматизируем конкретные процессы: от входящей заявки до сделки и отчёта собственнику.",
        "Сначала считаем, где бизнес теряет время и заявки, и автоматизируем именно этот участок.",
    ]
    ctas = [
        "Опишите текущий процесс — покажем, что имеет смысл автоматизировать первым.",
        "Можно прислать схему работы отдела: источники заявок, CRM и что сотрудники делают вручную.",
        "Разберём один процесс и до разработки зафиксируем результат, объём и ориентир по цене.",
    ]
    out = []
    seo = _seo_for_platform(p.key, p.audience)
    for topic_key, cfg in ts:
        for i in range(max(1, min(int(variants), 20))):
            kws = list(dict.fromkeys(cfg["keywords"] + list(seo.get("keywords", []))))[:10]
            post_title = _seo_draft_title(p, topic_key, cfg, seo, i)
            niche_topic = (seo.get("topics") or [cfg["title"]])[i % len(seo.get("topics") or [cfg["title"]])]
            hook = hooks[(i + p.priority) % len(hooks)]
            cta = ctas[(i + p.priority) % len(ctas)]
            text = (
                f"{cfg['title']}\n\n"
                f"{hook}\n\n"
                f"Проблема: {cfg['pain']}.\n\n"
                f"Решение: {cfg['result']}. "
                "При необходимости связываем CRM, сайт, Avito, Яндекс Директ, сообщения, звонки, аналитику, маркетплейсы и реактивацию.\n\n"
                f"Для аудитории этой площадки отдельная тема: {niche_topic}.\n\n"
                "Ориентир: небольшие автоматизации от 50–80 тыс. ₽; комплексные внедрения обычно от 100–300 тыс. ₽ и выше. "
                "Точную стоимость фиксируем после разбора процесса.\n\n"
                f"{cta}\n\n"
                f"Тема для этой площадки: {seo.get('topics',[cfg['title']])[i % len(seo.get('topics',[cfg['title']]))]}.\n\n"
                "Ключевые направления: " + ", ".join(kws)
            )
            out.append({
                "id": _draft_id(p.key, topic_key, i),
                "platform": p.key,
                "platform_name": p.name,
                "topic": topic_key,
                "title": post_title,
                "text": text,
                "site_url": BORIS_SITE,
                "status": "needs_owner_approval",
                "generated_without_openai": True,
                "variant": i + 1,
                "copy_style": "standard",
                "discovered_automatically": True,
            })
    return out

_STATIC_KEYS = {p.key for p in PLATFORMS}

def generate_drafts(platform="all", topic="all", variants=3):
    """Final generator: static + auto-discovered free platforms, no paid channels."""
    if platform == "all":
        # The broad BORIS acquisition campaign intentionally skips narrowly
        # specialized 1C-only communities. They remain available for explicit,
        # relevant integration offers, but must not dominate mass promotion.
        platforms = [
            p for p in all_platform_objects()
            if platform_policy(p.key).get("free") and p.key != "forum_baza_1c"
        ]
    else:
        p = get_platform(platform)
        if not p:
            raise ValueError("unknown platform")
        if not platform_policy(platform).get("free"):
            raise ValueError(f"platform blocked by free-only policy: {platform}")
        platforms = [p]

    out = []
    for p in platforms:
        if p.key in _STATIC_KEYS:
            rows = _platform_style_generator(platform=p.key, topic=topic, variants=variants)
        else:
            rows = _generate_dynamic_platform_drafts(p, topic=topic, variants=variants)
        for row in rows:
            _apply_platform_contact_policy(row, p.key)
            row["free_only"] = True
            row["rule_check_required"] = bool(platform_policy(p.key).get("rule_check", True))
            out.append(row)
    return out

# REGISTRATION_AVAILABILITY_V11
FREE_PLATFORM_POLICY["ssa"] = {"free": False, "reason": "registration_disabled"}
_NEVER_UNLOCK_REASONS.add("registration_disabled")

# CURATED_LIVE_SEEDS_V12
_v12 = [
    Platform("sellersforum","SellersForum · форум селлеров","https://sellersforum.ru/","reply_only",66,
             "селлеры Wildberries, Ozon, Яндекс Маркет, e-commerce","обычная регистрация",
             "Форум для продавцов маркетплейсов; есть работа/партнёрство и сервисы. Публикация только после свежего rule-audit."),
    Platform("dalionauto_forum","DalionAuto · форум автобизнеса","https://forum.dalionauto.ru/","reply_only",67,
             "автосервисы, магазины автозапчастей, автобизнес","обычная регистрация",
             "Профильный форум автоматизации автосервисов и магазинов; правила услуг/ссылок проверять до публикации."),
    Platform("mmgp","MMGP · интернет-бизнес и маркетинг","https://mmgp.com/","reply_only",68,
             "интернет-бизнес, маркетологи, SEO, реклама","обычная регистрация",
             "Есть интернет-маркетинг, SEO, контекстная реклама и услуги; формат коммерческой темы проверять отдельно."),
    Platform("gidtalk","GidTalk · SEO-форум","https://gidtalk.ru/","reply_only",69,
             "вебмастера, SEO, интернет-маркетинг, разработчики","обычная регистрация",
             "Есть биржа работ и услуг; правила коммерческих предложений и ссылок проверять."),
    Platform("saitsozdanie_forum","Saitsozdanie · SEO-форум","https://saitsozdanie.ru/forum/","reply_only",70,
             "SEO, SMM, интернет-маркетинг, веб-разработка","обычная регистрация",
             "Есть разделы SEO/SMM/интернет-маркетинга и услуг; публикация после проверки правил."),
    Platform("wmboard_qa","QA.WMBoard · SEO форум","https://qa.wmboard.net/","reply_only",71,
             "вебмастера, SEO, разработчики, заказчики услуг","обычная регистрация",
             "Есть биржа услуг и поиск исполнителя; бесплатность/ограничения ссылок проверять."),
]
_v12_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v12 if p.key not in _v12_keys])
for _k in ["sellersforum","dalionauto_forum","mmgp","gidtalk","saitsozdanie_forum","wmboard_qa"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

# CURATED_LIVE_SEEDS_V13
_v13 = [
    Platform("business_forum_my1","Профессиональный Бизнес форум","https://business-forum.my1.ru/forum/","direct_post",72,
             "предприниматели, малый бизнес, торговля, услуги","обычная регистрация",
             "Живой бизнес-форум с разделами по действующему бизнесу и услугам; публикация только после свежего rule-audit."),
    Platform("skripters","Skripters · форум вебмастеров","https://top.skripters.biz/","reply_only",73,
             "вебмастера, SEO, разработчики, интернет-бизнес","обычная регистрация",
             "Есть раздел Работа / Услуги, SEO и другие услуги; платность конкретного формата проверять до публикации."),
]
_v13_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v13 if p.key not in _v13_keys])
for _k in ["business_forum_my1","skripters"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

# CURATED_LIVE_SEEDS_V14
_v14 = [
    Platform("forum_seo_net","Forum-SEO · работа и услуги","https://www.forum-seo.net/","direct_post",74,
             "SEO, вебмастера, интернет-маркетинг, разработчики","обычная регистрация",
             "Есть раздел предложений работы и собственных услуг вебмастеров/оптимизаторов. Публикация только после свежего rule-audit."),
    Platform("sbup_seo_forum","SBUP · SEO форум","https://www.sbup.com/seo-forum/index.php","direct_post",75,
             "SEO, вебмастера, программирование, интернет-бизнес","обычная регистрация",
             "Есть раздел Работа и услуги и подраздел услуг по программированию. Публикация только после проверки правил."),
    Platform("baurum_forum","Baurum · строительный форум","https://www.baurum.ru/forum/","direct_post",76,
             "строители, проектировщики, подрядчики, строительный бизнес","обычная регистрация",
             "Есть Партнерство, Подрядчики и Совместный бизнес. Активность ниже новых форумов; правила и актуальность проверять."),
]
_v14_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v14 if p.key not in _v14_keys])
for _k in ["forum_seo_net","sbup_seo_forum","baurum_forum"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

# CURATED_LIVE_SEEDS_V15
_v15 = [
    Platform("regforum_services","Regforum · Объявления: Услуги","https://regforum.ru/forum/forums/7/","direct_post",77,
             "предприниматели, юристы, бухгалтеры, сервисы для бизнеса","обычная регистрация",
             "Есть отдельный раздел Услуги (предложение). Учитывать лимиты и актуальные правила."),
    Platform("webledi_services","Webledi Club · Предлагаю услуги","https://webledi.club/section/predlagaju-uslugi.59/","direct_post",78,
             "вебмастера, владельцы сайтов, интернет-бизнес","обычная регистрация",
             "Есть раздел предложений услуг, включая создание/продвижение сайтов."),
    Platform("finforum_services","FinForum · Предлагаю услуги","https://finforum.pro/categories/doska-objavlenij.88/","direct_post",79,
             "онлайн-бизнес, финансы, предприниматели, интернет-услуги","обычная регистрация",
             "Доска объявлений содержит отдельный раздел Предлагаю услуги."),
    Platform("forum_db_partners","Forum-DB · поиск партнёров","https://www.forum-db.ru/viewforum.php?f=66","direct_post",80,
             "поставщики, производители, предприниматели, B2B","обычная регистрация",
             "Есть предложения о сотрудничестве, поиск агентов/дилеров и совместный бизнес."),
    Platform("se_guru_services","SE.Guru · Биржа услуг","https://se.guru/forumdisplay.php?f=21","direct_post",81,
             "интернет-маркетинг, SEO, вебмастера, разработчики","обычная регистрация",
             "Есть Биржа услуг: предложение и поиск услуг."),
]
_v15_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v15 if p.key not in _v15_keys])
for _k in ["regforum_services","webledi_services","finforum_services","forum_db_partners","se_guru_services"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

# CURATED_LIVE_SEEDS_V16
_v16 = [
    Platform("wmboard_services","WMBoard · Биржа услуг","https://qa.wmboard.net/birzha-uslug-fc1240","direct_post",82,
             "SEO, вебмастера, разработчики, интернет-маркетинг","обычная регистрация",
             "Есть Биржа услуг: поиск исполнителей и предложения услуг. Публикация только после свежего rule-audit."),
    Platform("sashakustov_build_services","Строительный форум · доска услуг","https://sashakustov.ru/forum/doska-stroitelnyh-obyavlenij-i-uslug/","direct_post",83,
             "строительные компании, подрядчики, поставщики стройматериалов","обычная регистрация",
             "Доска строительных объявлений и услуг компаний. Публикация только после свежего rule-audit."),
    Platform("searchengines_guru_services","Searchengines.Guru · Работа и услуги","https://searchengines.guru/ru/forum/cat/marketing","direct_post",84,
             "SEO, вебмастера, интернет-маркетологи, разработчики","обычная регистрация",
             "Форум вебмастеров и маркетологов с активными предложениями услуг. Конкретный раздел и правила проверять перед публикацией."),
    Platform("mp_forum_marketplaces","MP Forum · продавцы маркетплейсов","https://mp-forum.ru/","direct_post",85,
             "селлеры Wildberries, Ozon, Яндекс Маркет, e-commerce","обычная регистрация",
             "Форум продавцов маркетплейсов, есть создание тем и профессиональные обсуждения. Рекламный формат проверять rule-audit."),
    Platform("itnull_services","ITNULL · рекламный раздел","https://itnull.me/","direct_post",86,
             "IT, SEO, разработчики, интернет-маркетинг","обычная регистрация",
             "Есть рекламные и сервисные темы. Бесплатность и допустимость ссылок обязательно подтверждать rule-audit."),
]
_v16_keys={p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v16 if p.key not in _v16_keys])
for _k in ["wmboard_services","sashakustov_build_services","searchengines_guru_services","mp_forum_marketplaces","itnull_services"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})


# CURATED_LIVE_SEEDS_V17
# Nulled has a real zero-cost advertising path, but only after the account
# reaches Level 3. It is therefore a reusable warming asset, not a terminal block.
_v17 = [
    Platform(
        "nulled_services",
        "Nulled · Коммерция и реклама услуг",
        "https://nulled.cc/forums/kommerciya-i-reklama-uslug.198/",
        "direct_post",
        87,
        "интернет-бизнес, разработчики, маркетинг, сервисы, товары и услуги",
        "регистрация + антибот-проверка; бесплатная рекламная тема после Level 3",
        "Бесплатный рекламный раздел разрешён правилами. Level 3: минимум 15 дней, 30 сообщений и 5 реакций; искусственную накрутку не использовать.",
    ),
]
_v17_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v17 if p.key not in _v17_keys])

FREE_PLATFORM_POLICY["nulled_services"] = {
    "free": True,
    "action": "advertising_services_section_after_level3",
    "rule_check": True,
}

VERIFIED_PUBLICATION_SURFACES["nulled_services"] = [
    {
        "id": "commercial_services",
        "name": "Коммерция и реклама услуг",
        "url": "https://nulled.cc/forums/kommerciya-i-reklama-uslug.198/",
        "post_url": "https://nulled.cc/forums/kommerciya-i-reklama-uslug.198/post-thread",
        "niches": ["it", "marketing", "business", "services", "goods"],
        "required_terms": [],
        "evidence": "Официальный деловой раздел: товары, услуги и работу предлагают здесь. Бесплатные новые рекламные темы доступны после Level 3.",
    },
]

# CURATED_LIVE_SEEDS_V18
# CY-PR has a dedicated Работа / Услуги tree with active service offers.
# Keep it fail-closed until the normal rule audit confirms the zero-cost path.
_v18 = [
    Platform(
        "cy_pr_services",
        "CY-PR · Работа / Услуги",
        "https://www.cy-pr.com/forum/group15/",
        "direct_post",
        89,
        "SEO, интернет-маркетинг, разработчики, дизайнеры, владельцы сайтов",
        "обычная регистрация",
        "Есть отдельные разделы для предложений исполнителей, продвижения сайтов, создания/дизайна сайтов и услуг в соцсетях. Публикация только после свежего rule-audit.",
    ),
]
_v18_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v18 if p.key not in _v18_keys])
FREE_PLATFORM_POLICY.setdefault("cy_pr_services", {"free": False, "reason": "rule_audit_required"})


# CURATED_LIVE_SEEDS_V19
# Additional active webmaster communities with explicit service / promotion
# marketplaces. They stay fail-closed until the normal BORIS rule audit proves
# a zero-cost path compatible with the client's required link.
_v19 = [
    Platform(
        "talkingcity_services",
        "TalkingCity · Marketplace Services",
        "https://www.talkingcity.com/forum/the-marketplace/marketplace-buy-sell-or-barter",
        "direct_post",
        90,
        "webmasters, developers, designers, SEO, internet marketing",
        "обычная регистрация",
        "Marketplace explicitly includes a Services area for offering or finding webmaster services. Use only after fresh rule-audit.",
    ),
    Platform(
        "webmastersun_marketplace",
        "WebmasterSun · Marketplace",
        "https://www.webmastersun.com/forums/48-services/",
        "direct_post",
        91,
        "webmasters, SEO, developers, internet marketing",
        "обычная регистрация",
        "Forum has a Webmaster Marketplace for listing services and related digital offers. Use only after fresh rule-audit.",
    ),
    Platform(
        "admin_junkies_promotion",
        "Admin-Junkies · Promotion & Services",
        "https://admin-junkies.com/",
        "direct_post",
        92,
        "webmasters, community owners, developers, digital services",
        "обычная регистрация",
        "Community has explicit Promotion & Services / marketplace areas. Use only after fresh rule-audit.",
    ),
]
_v19_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v19 if p.key not in _v19_keys])
for _k in ["talkingcity_services", "webmastersun_marketplace", "admin_junkies_promotion"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["talkingcity_services"] = [
    {
        "id": "services",
        "name": "Services",
        "url": "https://www.talkingcity.com/forum/the-marketplace/marketplace-buy-sell-or-barter/services",
        "post_url": None,
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": [],
        "language": "en",
        "evidence": "Official TalkingCity Marketplace subsection named Services: 'Do you want to offer services or looking for, this is the forum to use'; live topics include web development and SEO.",
    },
]


# CURATED_INTERNATIONAL_SERVICES_V21
# Two large, active marketplaces with explicit service/promotion sections.
# They remain fail-closed until BORIS verifies the current rules itself.
_v21 = [
    Platform(
        "digitalpoint_services",
        "DigitalPoint · Services Marketplace",
        "https://forums.digitalpoint.com/forums/services.60/",
        "direct_post",
        93,
        "webmasters, developers, designers, SEO, digital marketing, online business",
        "free registration; marketplace access after Established Member requirements",
        "Official Services marketplace explicitly accepts offered services. Free marketplace access requires natural account establishment; do not ask for likes.",
    ),
    Platform(
        "namepros_promotional",
        "NamePros · Promotional",
        "https://www.namepros.com/forums/promotional.15/",
        "direct_post",
        94,
        "domain investors, webmasters, online business, marketing and digital services",
        "обычная регистрация",
        "Official rules state that ads can be posted for free in Promotional; this section permits promotion of products, goods and services.",
    ),
]
_v21_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v21 if p.key not in _v21_keys])
for _k in ["digitalpoint_services", "namepros_promotional"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["digitalpoint_services"] = [
    {
        "id": "services",
        "name": "Services",
        "url": "https://forums.digitalpoint.com/forums/services.60/",
        "post_url": "https://forums.digitalpoint.com/forums/services.60/create-thread",
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": [],
        "language": "en",
        "evidence": "Official DigitalPoint Services marketplace: looking for or offering services, including programming, design and other web services.",
    },
]

VERIFIED_PUBLICATION_SURFACES["namepros_promotional"] = [
    {
        "id": "promotional",
        "name": "Promotional",
        "url": "https://www.namepros.com/forums/promotional.15/",
        "post_url": "https://www.namepros.com/forums/promotional.15/post-thread",
        "niches": ["it", "marketing", "business", "services", "goods"],
        "required_terms": [],
        "language": "en",
        "evidence": "Official NamePros Promotional marketplace permits free ads for products, goods and services and is the designated place for business promotion.",
    },
]


# CURATED_FREEHOSTFORUM_WEBDEV_V22
# FreeHostForum explicitly allows webmaster-related advertising free of charge
# inside Webmaster Marketplace. Use only the Web Development offers/requests
# surface and keep it fail-closed until the live rule audit confirms the policy.
_v22 = [
    Platform(
        "freehostforum_webdev",
        "FreeHostForum · Web Development",
        "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace/web-development-offers-and-requests",
        "direct_post",
        95,
        "webmasters, developers, website owners, hosting and online business",
        "free registration; one-time date-of-birth and terms declaration",
        "Official Webmaster Marketplace allows webmaster-related advertising free of charge; this surface is specifically for custom programming and web-development services.",
    ),
]
_v22_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v22 if p.key not in _v22_keys])
FREE_PLATFORM_POLICY.setdefault(
    "freehostforum_webdev",
    {"free": False, "reason": "rule_audit_required"},
)

VERIFIED_PUBLICATION_SURFACES["freehostforum_webdev"] = [
    {
        "id": "web_development_offers",
        "name": "Web Development Offers And Requests",
        "url": "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace/web-development-offers-and-requests",
        "post_url": None,
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": ["автомат", "crm", "api", "разработ", "бот", "создание сайтов", "интеграц"],
        "language": "en",
        "evidence": "Official section description: offers and requests for websites, templates, custom programming, graphics, or development services. Board rules allow webmaster-related advertising in Webmaster Marketplace free of charge.",
    },
]


# CURATED_FAST_IT_MARKETPLACES_V23
_v23 = [
    Platform("htmlforums_programming", "HTMLForums · Programming/Development",
             "https://htmlforums.net/forums/programming-development.25/", "direct_post", 96,
             "web developers, programmers, website owners and online businesses",
             "free account; one-time direct non-VPN/non-proxy registration",
             "Designated Marketplace section explicitly allows programming-service offers."),
    Platform("forum_promotion_employment", "Forum Promotion · Seeking Employment",
             "https://forumpromotion.net/forums/seeking-employment.67/", "direct_post", 97,
             "website owners, forum owners, webmasters and community administrators",
             "free Basic account; one-time terms acceptance",
             "Official Employment Zone allows Seeking Employment topics for website/forum work."),
]
_v23_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v23 if p.key not in _v23_keys])
for _k in ["htmlforums_programming", "forum_promotion_employment"]:
    FREE_PLATFORM_POLICY.setdefault(_k, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["htmlforums_programming"] = [{
    "id": "programming_development",
    "name": "Programming/Development",
    "url": "https://htmlforums.net/forums/programming-development.25/",
    "post_url": "https://htmlforums.net/forums/programming-development.25/post-thread",
    "niches": ["it", "marketing", "business", "services"],
    "required_terms": ["автомат", "crm", "api", "разработ", "бот", "создание сайтов", "интеграц"],
    "language": "en",
    "evidence": "Official Marketplace subsection explicitly allows programming-service offers; forum rules allow advertising/self-promotion in designated areas.",
}]
VERIFIED_PUBLICATION_SURFACES["forum_promotion_employment"] = [{
    "id": "seeking_employment",
    "name": "Seeking Employment",
    "url": "https://forumpromotion.net/forums/seeking-employment.67/",
    "post_url": "https://forumpromotion.net/forums/seeking-employment.67/post-thread",
    "niches": ["it", "business", "services"],
    "required_terms": ["разработ", "создание сайтов", "api", "crm", "автомат", "интеграц"],
    "language": "en",
    "evidence": "Official Employment Zone guide allows a Seeking Employment topic for relevant website/forum work.",
}]

# CROWD_SEO_REUSABLE_WARMUP_15D_V1
# WARMING is a reusable platform asset: waiting 15+ days is normal, not a
# client failure. Requirements remain platform-truth and are never faked.
PLATFORM_MATURITY_REQUIREMENTS = {
    "nulled_services": {
        "level": "Level 3",
        "minimum_account_age_days": 15,
        "minimum_messages": 30,
        "minimum_reactions": 5,
        "free_new_ad_topics": True,
        "natural_participation_required": True,
        "notes": "Не накручивать сообщения/реакции: правила форума прямо запрещают накрутку параметров аккаунта.",
    },
    "digitalpoint_services": {
        "required_status": "Established Member",
        "minimum_account_age_hours": 48,
        "minimum_likes_from_established_members": 3,
        "natural_participation_required": True,
        "free_new_service_topics": True,
        "links_nofollow": True,
        "notes": "Бесплатный marketplace открывается через 48 часов после регистрации и 3 лайка от разных Established Members. Просить или накручивать лайки запрещено.",
    },
}


def _parse_registration_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def platform_warmup_queue() -> dict:
    """Reusable accounts that are valid but not yet mature enough to publish.

    Warming is a normal queue state, not an error. We expose the earliest
    age-based readiness date while still keeping non-time requirements (for
    example natural messages/reactions) explicit and fail-closed.
    """
    platforms = {x.get("key"): x for x in list_platforms()}
    registrations = {x.get("platform"): x for x in registration_plan()}
    now = datetime.now(timezone.utc)
    items = []
    for key, reg in registrations.items():
        if not registration_is_warming(reg):
            continue
        platform = platforms.get(key) or {}
        requirements = dict(PLATFORM_MATURITY_REQUIREMENTS.get(key, {}))
        started = (
            _parse_registration_time(reg.get("warming_started_at"))
            or _parse_registration_time(reg.get("created_at"))
            or _parse_registration_time(reg.get("updated_at"))
        )
        min_age_hours = max(
            0,
            int(requirements.get("minimum_account_age_hours") or 0),
            int(requirements.get("minimum_account_age_days") or 0) * 24,
        )
        earliest_age_ready_at = (
            started + timedelta(hours=min_age_hours)
            if started and min_age_hours else None
        )
        age_elapsed_hours = (
            max(0, int((now - started).total_seconds() // 3600))
            if started else None
        )
        age_elapsed_days = (
            age_elapsed_hours // 24 if age_elapsed_hours is not None else None
        )
        age_remaining_hours = (
            max(0, int((earliest_age_ready_at - now).total_seconds() // 3600) + (
                1 if earliest_age_ready_at > now
                and int((earliest_age_ready_at - now).total_seconds()) % 3600 else 0
            ))
            if earliest_age_ready_at else 0
        )
        age_remaining_days = (
            (age_remaining_hours + 23) // 24 if age_remaining_hours else 0
        )
        surfaces = VERIFIED_PUBLICATION_SURFACES.get(key, [])
        niches = {
            str(n).strip().lower()
            for surface in surfaces
            for n in (surface.get("niches") or [])
            if str(n).strip()
        }
        supported_client_formats = [
            fmt for fmt in ("goods", "services") if fmt in niches
        ]
        items.append({
            "platform": key,
            "name": platform.get("name") or key,
            "url": platform.get("url"),
            "status": reg.get("status"),
            "checkpoint": reg.get("checkpoint"),
            "updated_at": reg.get("updated_at"),
            "warming_started_at": started.isoformat() if started else None,
            "earliest_age_ready_at": (
                earliest_age_ready_at.isoformat() if earliest_age_ready_at else None
            ),
            "age_elapsed_hours": age_elapsed_hours,
            "age_elapsed_days": age_elapsed_days,
            "age_remaining_hours": age_remaining_hours,
            "age_remaining_days": age_remaining_days,
            "account_url": reg.get("account_url"),
            "requirements": requirements,
            "supported_client_formats": supported_client_formats,
            "owner_action_required": False,
            "executor_role": "platform_warmup_worker",
            "reusable_across_clients": True,
            "next_action": (
                "BORIS автоматически перепроверяет право публикации каждые сутки и "
                "не накручивает сообщения/реакции ради уровня. Возраст аккаунта идёт "
                "сам, а требования реального участия должны выполниться естественно; "
                "как только площадка даст право публикации, аккаунт автоматически станет READY."
            ),
        })
    items.sort(key=lambda x: (x.get("name") or ""))
    return {
        "warming": len(items),
        "owner_action_required": False,
        "reusable_across_clients": True,
        "items": items,
    }


# CURATED_LIVE_SEEDS_V18
# CyberForum has an explicit "Предложения фрилансеров" section. The parent
# freelance rules allow one personal service/portfolio topic, but executor
# access requires forum group 4+ and a membership request. Treat this as a
# reusable warming asset after one-time registration/CAPTCHA.
_v18 = [
    Platform(
        "cyberforum_freelancers",
        "CyberForum · Предложения фрилансеров",
        "https://www.cyberforum.ru/freelancers-offers/",
        "direct_post",
        88,
        "разработчики, IT, автоматизация, веб-разработка, бизнес-услуги",
        "регистрация + CAPTCHA; затем прогрев до группы 4+ и заявка в группу фриланса",
        "Раздел прямо предназначен для предложений услуг. Исполнитель может иметь одну персональную тему с описанием услуг и портфолио. Срок получения группы заранее не выдумываем.",
    ),
]
_v18_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v18 if p.key not in _v18_keys])

FREE_PLATFORM_POLICY["cyberforum_freelancers"] = {
    "free": True,
    "action": "freelancer_personal_service_topic_after_group_access",
    "rule_check": True,
}

VERIFIED_PUBLICATION_SURFACES["cyberforum_freelancers"] = [
    {
        "id": "freelancer_offers",
        "name": "Предложения фрилансеров",
        "url": "https://www.cyberforum.ru/freelancers-offers/",
        "post_url": "https://www.cyberforum.ru/newthread.php?do=newthread&f=258",
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": [],
        "evidence": "Официальный раздел прямо описан как раздел для размещения предложений фрилансерами своих услуг; правила Фриланса разрешают исполнителю одну персональную тему услуг/портфолио.",
    },
]

PLATFORM_MATURITY_REQUIREMENTS["cyberforum_freelancers"] = {
    "required_group": "4+",
    "group_membership_request_required": True,
    "natural_participation_required": True,
    "notes": "Доступ исполнителя требует группы 4+ и заявки через «Членство в группах». Точные лимиты скрыты до регистрации, поэтому срок не выдумывается.",
}


# SUPPLIER_FORUM_VERIFIED_GOODS_SERVICES_V19
# Exact commercial sections were verified live. The admin rule bans selling
# supplier databases, not ordinary goods/services listings. External sites and
# contact emails are present in live supplier/sales threads.
FREE_PLATFORM_POLICY["supplier_forum"] = {
    "free": True,
    "action": "verified_goods_services_sections",
    "rule_check": True,
}

VERIFIED_PUBLICATION_SURFACES["supplier_forum"] = [
    {
        "id": "supplier_partners",
        "name": "Я поставщик · поиск партнёров и дилеров",
        "url": "https://forum.tvoipostavshik.ru/forums/partnery-i-dillery/",
        "post_url": "https://forum.tvoipostavshik.ru/forums/partnery-i-dillery/create-thread",
        "niches": ["goods", "business"],
        "required_terms": [],
        "evidence": "Раздел прямо предназначен для предложений поставщиков, производителей и поиска дилеров; в живых темах используются внешние ссылки и email.",
    },
    {
        "id": "goods_for_sale",
        "name": "Продам · объявления о продаже",
        "url": "https://forum.tvoipostavshik.ru/forums/prodam/",
        "post_url": "https://forum.tvoipostavshik.ru/forums/prodam/create-thread",
        "niches": ["goods", "business"],
        "required_terms": [],
        "evidence": "Раздел прямо называется «Продам — Объявления о продаже»; в живых темах размещены внешние сайты и товарные ссылки.",
    },
    {
        "id": "service_resume",
        "name": "Резюме · предлагаю услуги",
        "url": "https://forum.tvoipostavshik.ru/forums/rezjume-ischu-rabotu.5/",
        "post_url": "https://forum.tvoipostavshik.ru/forums/rezjume-ischu-rabotu.5/create-thread",
        "niches": ["services", "it", "marketing", "business"],
        "required_terms": [],
        "evidence": "Описание раздела: «В этом разделе можно предложить свои услуги»; есть темы по наполнению сайтов, копирайтингу и дизайну.",
    },
]


# CURATED_INTERNATIONAL_SERVICES_V20
# Expand Crowd SEO beyond the exhausted Russian-language forum tail, but only
# through designated service marketplaces with a verified zero-cost path.
_v20 = [
    Platform(
        "wjunction_services",
        "WJunction · Services",
        "https://www.wjunction.com/forums/services.112/",
        "direct_post",
        89,
        "international webmasters, developers, programmers, SEO and web-service buyers",
        "free registration; real identity/age/terms and anti-bot verification are one-time external checkpoints",
        "Official Webmaster Marketplace rules explicitly allow website-related services including coders, SEO and site management.",
    ),
]
_v20_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v20 if p.key not in _v20_keys])
FREE_PLATFORM_POLICY.setdefault(
    "wjunction_services",
    {"free": False, "reason": "rule_audit_required"},
)

VERIFIED_PUBLICATION_SURFACES["wjunction_services"] = [
    {
        "id": "services",
        "name": "Services",
        "url": "https://www.wjunction.com/forums/services.112/",
        "post_url": "https://www.wjunction.com/forums/services.112/post-thread",
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": [],
        "language": "en",
        "evidence": "Official WJunction Webmaster Marketplace Services forum for programmers, SEO professionals and website-related service providers.",
    },
]

# FINFORUM_EXACT_SERVICE_SECTION_V20
# The global anti-spam rule is not treated as permission to advertise anywhere.
# Only the dedicated commercial subsection is eligible: the forum explicitly
# describes it as the place where service offers are posted, while the global
# rules also require section-specific rules to be followed.
VERIFIED_PUBLICATION_SURFACES["finforum_services"] = [
    {
        "id": "services_offer",
        "name": "Предлагаю услуги",
        "url": "https://finforum.pro/forums/predlagaju-uslugi.90/",
        "post_url": "https://finforum.pro/forums/predlagaju-uslugi.90/post-thread",
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": [],
        "language": "ru",
        "evidence": "Официальный раздел прямо описан как место, где размещаются объявления с предложением услуг; использовать только этот раздел, не обычные обсуждения форума.",
    },
]


# CURATED_SPECIALIZED_AUTOMATION_COMMUNITIES_V21
# These are not generic advertising forums. Each surface is limited to the
# exact jobs/freelance category that already carries provider/job-seeker posts.
_v21 = [
    Platform(
        "n8n_jobs",
        "n8n Community · Jobs",
        "https://community.n8n.io/c/jobs/13",
        "direct_post",
        91,
        "international automation buyers, n8n users, AI/automation teams",
        "free community account; registration itself accepts Terms, so one-time human legal checkpoint",
        "Jobs category explicitly covers related jobs/complex-workflow help and carries current For Hire automation posts.",
    ),
    Platform(
        "airtable_jobs",
        "Airtable Community · Jobs Board",
        "https://community.airtable.com/jobs-board-16",
        "direct_post",
        90,
        "business automation, Airtable, CRM/workflow buyers and consultants",
        "community account through Airtable SSO/account setup; one-time external account checkpoint",
        "Jobs Board carries both hiring posts and current consultant/automation-specialist availability posts.",
    ),
    Platform(
        "bubble_jobs",
        "Bubble Forum · Jobs / Freelance",
        "https://forum.bubble.io/c/jobs-freelance/13",
        "direct_post",
        89,
        "web/mobile app buyers, founders, Bubble and AI-app teams",
        "Bubble account required; one-time external account/terms checkpoint",
        "Dedicated Jobs/Freelance category contains current freelancer and AI-systems provider posts.",
    ),
    Platform(
        "weweb_jobs",
        "WeWeb Community · Jobs & collabs",
        "https://community.weweb.io/c/jobs/22",
        "direct_post",
        88,
        "web app, portal, Supabase/Xano, no-code and automation buyers",
        "free community account; registration accepts Terms, so one-time human legal checkpoint",
        "Official Jobs & collabs category explicitly allows paid opportunities, freelance gigs and builders looking to join forces.",
    ),
]
_v21_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v21 if p.key not in _v21_keys])
for _key in ("n8n_jobs", "airtable_jobs", "bubble_jobs", "weweb_jobs"):
    FREE_PLATFORM_POLICY.setdefault(_key, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["n8n_jobs"] = [
    {
        "id": "jobs",
        "name": "Jobs",
        "url": "https://community.n8n.io/c/jobs/13",
        "post_url": "https://community.n8n.io/new-topic?category=jobs/13",
        "niches": ["it", "marketing", "business", "services"],
        "required_terms": ["автомат", "crm", "api", "бот", "интеграц", "разработ"],
        "language": "en",
        "evidence": "Jobs category is used for n8n-related jobs and current For Hire automation specialists.",
    },
]
VERIFIED_PUBLICATION_SURFACES["airtable_jobs"] = [
    {
        "id": "jobs_board",
        "name": "Jobs Board",
        "url": "https://community.airtable.com/jobs-board-16",
        "post_url": "https://community.airtable.com/topic/new",
        "niches": ["it", "business", "services"],
        "required_terms": ["автомат", "crm", "api", "интеграц", "разработ"],
        "language": "en",
        "evidence": "Jobs Board currently contains consultant and automation specialist availability posts as well as open roles.",
    },
]
VERIFIED_PUBLICATION_SURFACES["bubble_jobs"] = [
    {
        "id": "jobs_freelance",
        "name": "Jobs / Freelance",
        "url": "https://forum.bubble.io/c/jobs-freelance/13",
        "post_url": "https://forum.bubble.io/new-topic?category=jobs-freelance/13",
        "niches": ["it", "services"],
        "required_terms": ["создание сайтов", "разработ", "api", "бот", "автомат"],
        "language": "en",
        "evidence": "Dedicated Jobs/Freelance category has current provider posts including AI systems and app development.",
    },
]
VERIFIED_PUBLICATION_SURFACES["skripters"] = [
    {
        "id": "programmer_services",
        "name": "Услуги программистов",
        "url": "https://top.skripters.biz/forums/19/",
        "post_url": "https://top.skripters.biz/forums/19/post-thread",
        "niches": ["it", "services"],
        "required_terms": [],
        "language": "ru",
        "evidence": "Официальный раздел «Работа / Услуги → Услуги программистов» прямо предназначен для написания скриптов и других работ с кодом; живые темы включают Telegram-боты, WebApp, PHP и web-разработку.",
    },
]

VERIFIED_PUBLICATION_SURFACES["weweb_jobs"] = [
    {
        "id": "jobs_collabs",
        "name": "Jobs & collabs",
        "url": "https://community.weweb.io/c/jobs/22",
        "post_url": "https://community.weweb.io/new-topic?category=jobs/22",
        "niches": ["it", "services"],
        "required_terms": ["создание сайтов", "разработ", "api", "автомат", "интеграц"],
        "language": "en",
        "evidence": "Official category says paid opportunities, freelance gigs and collaborative projects; current provider posts are present.",
    },
]


# AUTOMATION_TERMS_FAIL_CLOSED_V22
# These communities may contain legitimate job/provider posts, but their current
# Terms conflict with autonomous Crowd SEO. Keep them out of capacity even if
# an older rule snapshot says the category itself allows service posts.
FREE_PLATFORM_POLICY["n8n_jobs"] = {"free": False, "reason": "automated_access_prohibited"}
FREE_PLATFORM_POLICY["weweb_jobs"] = {"free": False, "reason": "automated_access_prohibited"}
FREE_PLATFORM_POLICY["airtable_jobs"] = {"free": False, "reason": "commercial_use_prohibited"}
# Bubble Jobs/Freelance is a valid category, but autonomous access permission
# was not proven. Fail closed until an explicit current rule audit proves it.
FREE_PLATFORM_POLICY["bubble_jobs"] = {"free": False, "reason": "automated_access_not_verified"}


# CURATED_IT_SERVICE_MARKETPLACES_V23
_v23 = [
    Platform(
        "searchengines_services",
        "Searchengines.guru · Программирование",
        "https://searchengines.guru/ru/forum/webmasters-jobs/programming",
        "direct_post",
        92,
        "вебмастера, разработчики, интернет-маркетинг, программирование и автоматизация",
        "обычная регистрация + подтверждение email/условий; одноразовые внешние проверки не автоматизируются",
        "Официальные правила форума прямо разрешают коммерческие объявления только в Бирже оптимизатора и Работа и услуги для вебмастера; используется только подраздел Программирование.",
    ),
    Platform(
        "zismo_programming_services",
        "ZiSMO · Программирование",
        "https://zismo.biz/forum/92-programmirovanie/",
        "direct_post",
        91,
        "разработчики, вебмастера, автоматизация, боты, скрипты и digital-услуги",
        "обычная регистрация; юридическое согласие/антибот-проверки только вручную один раз",
        "В разделе есть предложения Python-разработки, ботов и автоматизации. Не размещать услуги массовой регистрации аккаунтов и иные запрещённые правилами услуги.",
    ),
]
_v23_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v23 if p.key not in _v23_keys])
for _key in ("searchengines_services", "zismo_programming_services"):
    FREE_PLATFORM_POLICY.setdefault(_key, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["searchengines_services"] = [
    {
        "id": "programming_services",
        "name": "Работа и услуги · Программирование",
        "url": "https://searchengines.guru/ru/forum/webmasters-jobs/programming",
        "post_url": "https://searchengines.guru/ru/forum/webmasters-jobs/programming?do=add",
        "niches": ["it", "marketing", "services"],
        "required_terms": ["автомат", "crm", "api", "интеграц", "разработ", "бот", "создание сайтов"],
        "language": "ru",
        "evidence": "Правила форума разрешают коммерческие объявления в Работа и услуги для вебмастера; этот подраздел предназначен для программирования.",
    },
]
VERIFIED_PUBLICATION_SURFACES["zismo_programming_services"] = [
    {
        "id": "programming",
        "name": "Программирование",
        "url": "https://zismo.biz/forum/92-programmirovanie/",
        "post_url": "https://zismo.biz/index.php?app=forums&module=post&section=post&do=new_post&f=92",
        "niches": ["it", "marketing", "services"],
        "required_terms": ["автомат", "crm", "api", "интеграц", "разработ", "бот", "скрипт", "создание сайтов"],
        "language": "ru",
        "evidence": "Живой раздел содержит предложения Python-разработки, ботов, автоматизации и скриптов; контент BORIS ограничивается законными IT/digital-услугами.",
    },
]


# VERIFIED_DISCOVERED_PSPX_POST_ROUTE_V25
# PSPx is a vBulletin 3.8 forum. The new-thread route is stable and returns
# the forum permission/login screen for guests instead of a 404, which is the
# correct fail-closed pre-login behaviour for an authoring endpoint.
VERIFIED_PUBLICATION_SURFACES["disc_pspx_ru"] = [
    {
        "id": "default",
        "name": "Куплю / Продам / Обменяю / Услуги - PSPx форум",
        "url": "https://www.pspx.ru/forum/forumdisplay.php?f=74",
        "post_url": "https://www.pspx.ru/forum/newthread.php?do=newthread&f=74",
        "niches": ["goods", "it", "marketing", "business", "services"],
        "required_terms": [],
        "language": "ru",
        "evidence": "Live section f=74 explicitly contains Продам goods and commercial services; use only the matching sub-section after READY account and fresh rule gate.",
    },
]


# PLATFORM_CONTACT_POLICY_V24
SITE_ONLY_CONTACT_PLATFORMS = {"forum_promotion_employment"}

def _apply_platform_contact_policy(row: dict, platform_key: str) -> dict:
    if platform_key in SITE_ONLY_CONTACT_PLATFORMS:
        raw = str(row.get("text") or "")
        blocked_prefixes = ("WhatsApp:", "MAX:", "Consultation:", "Консультация:")
        lines = [
            line for line in raw.splitlines()
            if not line.strip().startswith(blocked_prefixes)
            and WHATSAPP_URL not in line
            and MAX_URL not in line
            and CONSULT_PHONE_DISPLAY not in line
        ]
        value = "\n".join(lines).rstrip()
        if BORIS_SITE not in value:
            value += ("\n\n" if value else "") + f"BORIS: {BORIS_SITE}"
        row["text"] = value
        row["content_language"] = "en"
        row["contact_whatsapp"] = None
        row["contact_max"] = None
        row["contact_phone"] = None
        return row

    if _platform_content_language(platform_key) == "en":
        row["text"] = _ensure_english_contact_block(row.get("text") or "")
        row["content_language"] = "en"
    else:
        row["text"] = ensure_contact_block(row.get("text") or "", include_site=True)
        row["content_language"] = row.get("content_language") or "ru"
    row["contact_whatsapp"] = WHATSAPP_URL
    row["contact_max"] = MAX_URL
    row["contact_phone"] = CONSULT_PHONE_DISPLAY
    return row


# CURATED_GOODS_MARKETPLACE_V25
_v25 = [
    Platform(
        "print_forum_goods",
        "Принт-Форум · Торговая площадка",
        "https://forum.print-forum.ru/forumdisplay.php?f=22",
        "direct_post",
        89,
        "полиграфия, промышленное оборудование, материалы, упаковка, рекламное производство",
        "бесплатная регистрация + подтверждение email/антибот; затем бесплатная заявка в группу «Полиграфист»",
        "Официальный раздел продажи оборудования. Одно объявление — одна единица товара; списки оборудования требуют Premium, поэтому BORIS публикует только одну позицию. UP и кросспостинг запрещены.",
    ),
]
_v25_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v25 if p.key not in _v25_keys])

FREE_PLATFORM_POLICY["print_forum_goods"] = {
    "free": True,
    "action": "single_goods_listing_after_poligrafist_group",
    "rule_check": True,
}

VERIFIED_PUBLICATION_SURFACES["print_forum_goods"] = [
    {
        "id": "printing_equipment_sale",
        "name": "Продажа полиграфического оборудования",
        "url": "https://forum.print-forum.ru/forumdisplay.php?f=22",
        "post_url": "https://forum.print-forum.ru/newthread.php?do=newthread&f=22",
        "niches": ["goods", "manufacturing"],
        "required_terms": [
            "оборудован", "станок", "машин", "полиграф", "печать", "принтер",
            "упаков", "типограф", "материал", "запчаст",
        ],
        "language": "ru",
        "evidence": "Раздел прямо разрешает продажу одной единицы оборудования после бесплатного статуса «Полиграфист». В действующих объявлениях присутствуют внешние сайты продавцов.",
    },
]

PLATFORM_MATURITY_REQUIREMENTS["print_forum_goods"] = {
    "required_group": "Полиграфист",
    "profile_completion_required": True,
    "group_application_required": True,
    "paid_account_required": False,
    "single_item_only": True,
    "natural_participation_required": False,
    "notes": "После регистрации заполнить профиль и один раз подать бесплатную заявку в группу «Полиграфист». Premium нужен только для объявлений со списком оборудования.",
}


# CURATED_GOODS_MARKETPLACE_V26
_v26 = [
    Platform(
        "cnc_club_goods",
        "CNC-Club · Продам оборудование и комплектующие",
        "https://www.cnc-club.ru/forum/viewforum.php?f=163",
        "direct_post",
        90,
        "станки ЧПУ, промышленное оборудование, комплектующие, материалы и инструмент",
        "обычная phpBB-регистрация + одноразовое принятие условий; новый коммерческий аккаунт может требовать естественного участия до выхода из карантина",
        "Раздел прямо разрешает предложения физических лиц и коммерческих организаций. Обязательны характеристики, цена, оплата/доставка; ссылка на сайт разрешена. Один продавец — одна тема, искусственный UP запрещён.",
    ),
]
_v26_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v26 if p.key not in _v26_keys])

FREE_PLATFORM_POLICY["cnc_club_goods"] = {
    "free": True,
    "action": "single_seller_goods_topic",
    "rule_check": True,
}

VERIFIED_PUBLICATION_SURFACES["cnc_club_goods"] = [
    {
        "id": "sell_goods",
        "name": "Продам",
        "url": "https://www.cnc-club.ru/forum/viewforum.php?f=163",
        "post_url": "https://www.cnc-club.ru/forum/posting.php?mode=post&f=163",
        "niches": ["goods", "manufacturing"],
        "required_terms": [
            "станок", "чпу", "оборудован", "комплект", "материал",
            "инструмент", "привод", "двигател", "электроник", "запчаст",
        ],
        "language": "ru",
        "evidence": "Официальные правила раздела разрешают продажи коммерческим организациям и прямую ссылку на сайт при наличии характеристик, цены, оплаты и доставки.",
    },
]

PLATFORM_MATURITY_REQUIREMENTS["cnc_club_goods"] = {
    "organization_quarantine_possible": True,
    "natural_participation_required": True,
    "single_seller_topic_only": True,
    "artificial_up_prohibited": True,
    "notes": "Новые сообщения организаций могут переноситься в карантин до естественного участия. Не накручивать активность; READY подтверждать отдельно.",
}


# DISCOVERED_GOODS_MARKETPLACE_SCUBABOARD_V27
# Verified from the live B2B Marketplace and its pinned operator guidance:
# businesses may buy/sell/trade items useful to other business members. Keep
# this narrow to scuba/diving goods; it is reusable only for matching clients.
VERIFIED_PUBLICATION_SURFACES["disc_scubaboard_com"] = [
    {
        "id": "b2b_marketplace_goods",
        "name": "Scuba Industry Pros · B2B Marketplace",
        "url": "https://scubaboard.com/community/forums/b2b-marketplace.248/",
        "post_url": "https://scubaboard.com/community/forums/b2b-marketplace.248/post-thread",
        "niches": ["goods", "business"],
        "required_terms": [
            "scuba", "diving", "dive", "compressor", "regulator", "equipment",
            "gear", "boat", "charter", "light", "tank", "breathing air",
        ],
        "language": "en",
        "evidence": "Pinned B2B Marketplace guidance explicitly permits stores, boats, operators and hotels to buy, sell or trade items useful to other business members; live marketplace topics contain commercial product offers and external business links.",
    },
]


# DISCOVERED_GOODS_MARKETPLACE_OBOROT_V28
# Oborot's general forum rules reject generic advertising, but moderator-owned
# "Продаю" catalogue threads are an explicit commercial exception. Keep this
# as a separate goods-only surface so the exception cannot leak to services or
# ordinary discussion threads.
_v28 = [
    Platform(
        "oborot_goods",
        "Oborot.ru · Продаю товары",
        "https://oborot.ru/forum/prodayu-tovary-dlya-doma-i-dachi-i27339.html",
        "reply_only",
        92,
        "товары для дома и дачи, электроника, бытовая техника, автотовары, детские товары и другие товарные категории",
        "обычная регистрация; размещение только ответом в соответствующей закреплённой теме «Продаю»",
        "Модераторские темы прямо разрешают предложения о продаже; отдельные рекламные темы запрещены. В существующих разрешённых сообщениях есть сайты и контактные данные продавцов.",
    ),
]
_v28_keys = {p.key for p in PLATFORMS}
PLATFORMS.extend([p for p in _v28 if p.key not in _v28_keys])
FREE_PLATFORM_POLICY["oborot_goods"] = {"free": True, "action": "reply_in_matching_sell_thread_only", "rule_check": True}
VERIFIED_PUBLICATION_SURFACES["oborot_goods"] = [
    {
        "id": "sell_goods_catalogue_threads",
        "name": "Тематические темы «Продаю»",
        "url": "https://oborot.ru/forum/prodayu-tovary-dlya-doma-i-dachi-i27339.html",
        "post_url": "https://oborot.ru/forum/prodayu-tovary-dlya-doma-i-dachi-i27339.html",
        "niches": ["goods", "ecommerce"],
        "required_terms": ["товар", "продаж", "магазин", "поставщик", "электроник", "техник", "авто", "дом", "дач", "детск"],
        "language": "ru",
        "evidence": "Модераторские темы «Продаю» прямо разрешают товарные предложения и запрещают создавать отдельные рекламные темы; опубликованные предложения содержат сайты и контакты продавцов.",
    },
]
PLATFORM_MATURITY_REQUIREMENTS["oborot_goods"] = {
    "reply_only": True,
    "matching_sell_thread_required": True,
    "new_advertising_topic_prohibited": True,
    "natural_participation_required": False,
    "notes": "Публиковать только в существующей тематической теме «Продаю». Не создавать отдельную рекламную тему; перед READY перепроверять доступность ответа и актуальные правила.",
}


# CURATED_VENDOR_SURFACES_V36
# Exact reusable commercial surfaces. Existing discovered domains keep their
# original dynamic key so account/bootstrap state is not duplicated.
for _key in (
    "disc_forum_arcadecontrols_com",
    "disc_teaforum_org",
    "disc_teachat_com",
    "disc_forums_deeperblue_com",
    "disc_doityourselfchristmas_com",
    "disc_mcarterbrown_com",
    "disc_penturners_org",
):
    FREE_PLATFORM_POLICY.setdefault(_key, {"free": False, "reason": "rule_audit_required"})

VERIFIED_PUBLICATION_SURFACES["disc_forum_arcadecontrols_com"] = [{
    "id": "retail_vendors",
    "name": "Retail Vendors",
    "url": "https://forum.arcadecontrols.com/index.php/board,56.0.html",
    "post_url": "https://forum.arcadecontrols.com/index.php?action=post;board=56.0",
    "niches": ["goods", "it", "manufacturing"],
    "required_terms": ["arcade", "joystick", "controller", "pcb", "cabinet", "t-molding", "button", "monitor", "gaming", "electronics", "parts"],
    "language": "en",
    "evidence": "Dedicated Retail Vendors board; enabled only after the strict vendor-rule audit proves the commercial exception.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_forum_arcadecontrols_com"] = {
    "designated_vendor_board_only": True,
    "natural_participation_required": False,
    "paid_account_required": False,
    "notes": "Использовать только Retail Vendors; реклама в обычных разделах не разрешается.",
}

VERIFIED_PUBLICATION_SURFACES["disc_teaforum_org"] = [{
    "id": "tea_teaware_vendors",
    "name": "Tea/Teaware Vendors",
    "url": "https://www.teaforum.org/viewforum.php?f=30",
    "post_url": "https://www.teaforum.org/posting.php?mode=post&f=30",
    "niches": ["goods"],
    "required_terms": ["tea", "teaware", "teapot", "teacup", "matcha", "oolong", "puerh", "herbal tea"],
    "language": "en",
    "evidence": "Vendor section with one company topic and website links after vendor-group approval; strict audit required.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_teaforum_org"] = {
    "vendor_group_admin_approval_required": True,
    "natural_participation_required": False,
    "marketplace_only": True,
    "notes": "После регистрации требуется одобрение vendor-группы; одна тема на продавца.",
}

VERIFIED_PUBLICATION_SURFACES["disc_teachat_com"] = [{
    "id": "tea_vendors",
    "name": "Tea Vendors",
    "url": "https://www.teachat.com/viewforum.php?f=60",
    "post_url": "https://www.teachat.com/posting.php?mode=post&f=60",
    "niches": ["goods"],
    "required_terms": ["tea", "teaware", "teapot", "teacup", "matcha", "oolong", "puerh", "herbal tea"],
    "language": "en",
    "evidence": "Vendor advertising is limited to Tea Vendors; links unlock only after natural account maturity.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_teachat_com"] = {
    "minimum_account_age_days": 30,
    "minimum_messages": 10,
    "natural_participation_required": True,
    "links_unlocked_after_maturity": True,
    "marketplace_only": True,
    "notes": "30 дней и 10 сообщений должны набраться естественно; не накручивать.",
}

VERIFIED_PUBLICATION_SURFACES["disc_forums_deeperblue_com"] = [{
    "id": "goods_for_sale_marketplace",
    "name": "The Marketplace · Goods For Sale",
    "url": "https://forums.deeperblue.com/forums/goods-for-sale.49/",
    "post_url": "https://forums.deeperblue.com/forums/goods-for-sale.49/post-thread",
    "niches": ["goods"],
    "required_terms": ["dive", "diving", "scuba", "freediving", "spearfishing", "speargun", "fins", "mask", "snorkel", "wetsuit", "gear"],
    "language": "en",
    "evidence": "Commercial Goods For Sale exception; prior commercial permission is required before first post.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_forums_deeperblue_com"] = {
    "commercial_permission_required": True,
    "one_time_human_permission_request": True,
    "marketplace_only": True,
    "natural_participation_required": False,
    "notes": "До READY один раз получить разрешение на commercial/self-promo; вне Marketplace не публиковать.",
}

VERIFIED_PUBLICATION_SURFACES["disc_doityourselfchristmas_com"] = [{
    "id": "vendors_arena",
    "name": "Vendors Arena",
    "url": "https://www.doityourselfchristmas.com/forum/index.php?forums/vendors-arena.61/",
    "post_url": "https://www.doityourselfchristmas.com/forum/index.php?forums/vendors-arena.61/post-thread",
    "niches": ["goods"],
    "required_terms": ["christmas", "holiday", "lighting", "pixel", "controller", "led", "display", "rgb", "power"],
    "language": "en",
    "evidence": "Registered-company vendor section for animated holiday display products; strict audit required.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_doityourselfchristmas_com"] = {
    "registered_company_identity_required": True,
    "marketplace_only": True,
    "natural_participation_required": False,
    "notes": "Проверить право компании на Vendors Arena; supporting membership не считать обязательным без явного требования.",
}

VERIFIED_PUBLICATION_SURFACES["disc_mcarterbrown_com"] = [{
    "id": "dealers_forum",
    "name": "Dealers Forum",
    "url": "https://mcarterbrown.com/forum/buy-sell-trade/dealers-forum",
    "post_url": "https://mcarterbrown.com/forum/buy-sell-trade/dealers-forum",
    "niches": ["goods"],
    "required_terms": ["paintball", "marker", "barrel", "airsmith", "autococker", "pump", "mask", "hopper", "tank", "parts", "gear"],
    "language": "en",
    "evidence": "Dealer-only commercial section with external business links; strict audit required.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_mcarterbrown_com"] = {
    "marketplace_only": True,
    "natural_participation_required": False,
    "notes": "Использовать только Dealers Forum, не частные Buy/Sell/Trade разделы.",
}

VERIFIED_PUBLICATION_SURFACES["disc_penturners_org"] = [{
    "id": "vendor_forums",
    "name": "IAP Vendor Forums",
    "url": "https://www.penturners.org/forums/vendor-forums.208/",
    "post_url": "https://www.penturners.org/forums/vendor-forums.208/",
    "niches": ["goods"],
    "required_terms": ["pen", "penturning", "blank", "wood", "resin", "tool", "kit", "lathe", "turning"],
    "language": "en",
    "evidence": "Free business selling/vendor forum with one-year natural account maturity requirement.",
}]
PLATFORM_MATURITY_REQUIREMENTS["disc_penturners_org"] = {
    "minimum_account_age_days": 365,
    "vendor_forum_approval_required": True,
    "natural_participation_required": True,
    "marketplace_only": True,
    "notes": "Возраст аккаунта 1 год должен набраться естественно; затем требуется одобрение Vendor Forum.",
}

# PROMEBELCLUB_EXACT_COMMERCIAL_SURFACE_V34
# The old generic "Мебельный бизнес" URL was not a publication surface and
# kept the platform in REVIEW. Use only the explicit classifieds subsection.
FREE_PLATFORM_POLICY["promebelclub"] = {
    "free": True,
    "action": "verified_furniture_classifieds",
    "rule_check": True,
}
VERIFIED_PUBLICATION_SURFACES["promebelclub"] = [{
    "id": "sell_rent_classifieds",
    "name": "Продаю | Сдаю",
    "url": "https://promebelclub.ru/forum/forumdisplay.php?f=115",
    "post_url": "https://promebelclub.ru/forum/newthread.php?do=newthread&f=115",
    "niches": ["goods", "services", "furniture", "manufacturing"],
    "required_terms": [
        "мебел", "фурнитур", "дерев", "столяр", "дсп", "мдф", "кухн",
        "шкаф", "станок", "оборудован", "производ",
    ],
    "language": "ru",
    "evidence": (
        "Раздел прямо разрешает рекламные объявления по продаже продукции "
        "(оборудования), оказанию услуг и аренде; правило раздела требует "
        "регион и контактную информацию. В опубликованных коммерческих темах "
        "используются внешние сайты продавцов."
    ),
}]

# diyAudio was not present in the current dynamic registry, so add one curated
# platform key. It still stays disabled until fresh rule audit proves the free
# Vendor's Bazaar path.
if not any(p.key == "diyaudio_goods" for p in PLATFORMS):
    PLATFORMS.append(Platform(
        "diyaudio_goods",
        "diyAudio · Vendor's Bazaar",
        "https://www.diyaudio.com/community/forums/vendors-bazaar.44/",
        "direct_post",
        94,
        "аудио-компоненты, платы, усилители, ЦАП, динамики, электроника и DIY-аудио товары",
        "обычная регистрация форума",
        "Commercial posts are allowed only in Vendor's Bazaar after fresh rule verification.",
    ))
FREE_PLATFORM_POLICY["diyaudio_goods"] = {"free": False, "reason": "rule_audit_required"}
VERIFIED_PUBLICATION_SURFACES["diyaudio_goods"] = [{
    "id": "vendors_bazaar",
    "name": "Vendor's Bazaar",
    "url": "https://www.diyaudio.com/community/forums/vendors-bazaar.44/",
    "post_url": "https://www.diyaudio.com/community/forums/vendors-bazaar.44/post-thread",
    "niches": ["goods"],
    "required_terms": ["audio", "amplifier", "dac", "speaker", "pcb", "electronics", "tube", "transformer", "parts"],
    "language": "en",
    "evidence": "Helpdesk must freshly prove that commercial Vendor's Bazaar posting is free.",
}]
PLATFORM_MATURITY_REQUIREMENTS["diyaudio_goods"] = {
    "marketplace_only": True,
    "single_vendor_thread_only": True,
    "no_bumping": True,
    "natural_participation_required": False,
    "notes": "Только Vendor's Bazaar, одна коммерческая тема на продавца, без bump.",
}
