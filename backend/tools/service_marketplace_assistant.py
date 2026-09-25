#!/usr/bin/env python3
"""BORIS marketplace/service distribution assistant.

Zero-LLM content generation, owner approval gate, publication URL ledger.
No CAPTCHA/SMS/2FA bypass. No mailbox passwords stored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data" / "service_marketplaces"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"
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
    Platform("fl","FL.ru","https://www.fl.ru/projects/category/avtomatizaciya-biznesa/","reply_to_jobs",1,
             "заказчики автоматизации, CRM, API, AI","аккаунт исполнителя; могут потребоваться проверки",
             "Основной канал: отвечать на свежие релевантные проекты; также можно оформить услуги."),
    Platform("workspace","Workspace","https://workspace.ru/tenders/crm/","reply_to_jobs",2,
             "компании и агентские тендеры","профиль/агентство; модерация площадки",
             "Высокие чеки. Отклик только на релевантные тендеры."),
    Platform("profi","Профи.ру","https://profi.ru/registration/it_freelance/programmer/","reply_to_jobs",3,
             "малый и средний бизнес","номер телефона и SMS-код",
             "E-mail недостаточно; ручной checkpoint на SMS."),
    Platform("kwork","Kwork","https://kwork.ru/","catalog",4,
             "малый бизнес и разовые задачи","профиль исполнителя; верификация по правилам",
             "Отдельные карточки услуг; не демпинговать сложные проекты."),
    Platform("tenchat","TenChat","https://tenchat.ru/","direct_post",5,
             "предприниматели, B2B, эксперты","профиль пользователя/компании",
             "Экспертные кейсы и офферы, не одинаковый рекламный спам."),
    Platform("vk","VK","https://vk.com/","direct_post",6,
             "предприниматели, сообщества и локальный бизнес","VK-профиль/сообщество",
             "Публикация только в собственном сообществе или там, где правила разрешают рекламу."),
    Platform("tg_biznesschatt","Telegram: БИЗНЕС-ЧАТ | РФ","https://t.me/biznesschatt","direct_post",7,
             "предприниматели России","Telegram-профиль",
             "Услуги по правилам чата; учитывать модерацию."),
    Platform("tg_russianfreelance","Telegram: RussianFreelance","https://t.me/RussianFreelance","paid_post",8,
             "заказчики и исполнители","Telegram-профиль",
             "Размещение услуг платное; не отправлять без согласования тарифа."),
    Platform("tg_freelancegram","Telegram: ФРИЛАНС | ЧАТ","https://t.me/freelance_gram","reply_to_jobs",9,
             "заказчики и исполнители","Telegram-профиль",
             "Соблюдать актуальные правила чата; ссылки могут быть ограничены."),
    Platform("tg_poiskfreelance","Telegram: ПОИСК ФРИЛАНСЕРОВ | ЧАТ","https://t.me/poiskfreelance","reply_to_jobs",10,
             "заказчики и исполнители","Telegram-профиль",
             "Перед публикацией проверять закреп/правила."),
    Platform("tg_freelancesam","Telegram: САМОЗАНЯТЫЙ | ФРИЛАНС ЧАТ","https://t.me/freelancesam","reply_to_jobs",11,
             "фриланс и малый бизнес","Telegram-профиль",
             "Перед публикацией читать актуальные правила."),
    Platform("tg_predprinimateli_rus","Telegram: Предприниматели России","https://t.me/predprinimateli_rus","reply_only",12,
             "предприниматели","Telegram-профиль",
             "Не предлагать услугу без запроса. Только отвечать на явный спрос."),
]

OFFERS = [
    Offer("sales_ai","ИИ-менеджер + CRM",100000,250000,30000,70000,
          "Принимает обращения, квалифицирует, создаёт сделку и передаёт менеджеру."),
    Offer("sales_automation","Автоматизация отдела продаж",150000,400000,50000,120000,
          "CRM, каналы заявок, задачи, контроль менеджеров, отчётность и реактивация."),
    Offer("crm_integrations","CRM + API-интеграции",100000,300000,20000,60000,
          "Связываем CRM, сайт, телефонию, мессенджеры, 1С и внешние сервисы."),
    Offer("ai_sales_manager","ИИ-РОП: звонки, переписки, KPI",150000,350000,40000,100000,
          "Автоматический контроль качества отдела продаж и ежедневные рекомендации."),
    Offer("avito","Автоматизация Avito / объявлений",100000,300000,35000,100000,
          "Карточки, тексты, фото, фиды, публикации, ответы и контроль KPI."),
    Offer("routine","Автоматизация ручных процессов",80000,300000,20000,60000,
          "Убираем переносы между Excel, почтой, CRM и другими сервисами."),
    Offer("crm_1c","CRM + 1С + сайт + телефония",250000,700000,30000,100000,
          "Единый контур заявок, клиентов, заказов, звонков и учёта."),
    Offer("support_ai","ИИ-поддержка / база знаний",100000,300000,30000,100000,
          "Отвечает на типовые вопросы и передаёт сложные обращения сотрудникам."),
    Offer("reactivation","Реактивация клиентской базы",80000,200000,30000,70000,
          "Возвращаем старые лиды и клиентов автоматическими персональными касаниями."),
    Offer("owner_dashboard","Дашборд собственника",100000,300000,20000,70000,
          "Выручка, лиды, CPL, конверсии, менеджеры и проблемы на одном экране."),
]

TOPICS = {
    "avito": {
        "title": "Автоматизация Авито для бизнеса",
        "keywords": ["автоматизация авито","авито для бизнеса","авито объявления","авито автопубликация",
                     "авито фид","авито продвижение","авито аналитика","авито сообщения","авито CRM","авито лиды",
                     "авито автоматизация продаж","управление объявлениями авито"],
        "pain": "объявления, ответы, контроль цен и статистики отнимают время, а часть лидов теряется",
        "result": "BORIS помогает собрать публикации, сообщения, аналитику и контроль результата в одном процессе",
    },
    "direct": {
        "title": "Автоматизация Яндекс Директ и рекламы",
        "keywords": ["автоматизация яндекс директ","яндекс директ","директолог автоматизация","управление рекламой",
                     "контроль рекламы","аналитика директ","лиды яндекс директ","автоматизация ставок",
                     "рекламные кампании","контроль CPL","оптимизация рекламных кампаний"],
        "pain": "кампании требуют постоянного контроля, а собственник не видит простой связи реклама → лид → продажа",
        "result": "BORIS связывает рекламные данные с CRM, лидами, продажами и управленческими показателями",
    },
    "sales": {
        "title": "Автоматизация отдела продаж",
        "keywords": ["автоматизация продаж","отдел продаж автоматизация","CRM автоматизация","ИИ менеджер продаж",
                     "ИИ РОП","контроль менеджеров","реактивация клиентов","обработка лидов","автоматизация заявок",
                     "воронка продаж","контроль звонков","анализ переписок","увеличение конверсии продаж"],
        "pain": "заявки приходят из разных мест, менеджеры забывают перезванивать, а руководитель узнаёт о проблемах слишком поздно",
        "result": "BORIS собирает обращения, создаёт сделки и задачи, контролирует переписки и звонки и показывает владельцу результат",
    },
    "integrations": {
        "title": "CRM, 1С и API-интеграции",
        "keywords": ["интеграция CRM","CRM 1С","API интеграция","интеграция сайта с CRM",
                     "интеграция телефонии CRM","автоматизация 1С","интеграция мессенджеров",
                     "автоматизация бизнес процессов","интеграция систем","обмен данными API"],
        "pain": "сотрудники вручную переносят данные между CRM, 1С, сайтом, почтой, таблицами и мессенджерами",
        "result": "BORIS соединяет системы и убирает повторный ручной ввод, дубли и потерянные заявки",
    },
}

def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def _save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def registration_plan(email: str) -> list[dict]:
    tasks=[]
    for p in sorted(PLATFORMS,key=lambda x:x.priority):
        checkpoint="manual_verification"
        if p.key.startswith("tg_") or p.key=="vk":
            checkpoint="social_account_required"
        if p.key=="profi":
            checkpoint="phone_sms_required"
        tasks.append({"platform":p.key,"name":p.name,"url":p.url,
                      "email":email if checkpoint=="manual_verification" else None,
                      "mode":p.mode,"next_action":"create_or_complete_profile",
                      "checkpoint":checkpoint,"notes":p.notes})
    return tasks

def posting_plan() -> list[dict]:
    return [{"platform":p.key,"name":p.name,"mode":p.mode,
             "proactive_post_allowed_by_registry":p.mode!="reply_only",
             "requires_current_rule_check":True,"notes":p.notes}
            for p in sorted(PLATFORMS,key=lambda x:x.priority)]

def _draft_id(platform:str,topic:str,variant:int)->str:
    return hashlib.sha256(f"{platform}:{topic}:{variant}".encode()).hexdigest()[:16]

def generate_drafts(platform:str="all",topic:str="all",variants:int=3)->list[dict]:
    platforms=[p for p in PLATFORMS if platform in ("all",p.key)]
    if topic!="all" and topic not in TOPICS:
        raise SystemExit(f"unknown topic: {topic}")
    topics=TOPICS.items() if topic=="all" else [(topic,TOPICS[topic])]
    hooks=[
        "Если в компании часть заявок и контроля до сих пор держится на ручной работе, это можно убрать программированием.",
        "Автоматизация должна экономить время и не давать заявкам теряться, а не добавлять ещё один кабинет.",
        "Мы автоматизируем конкретные процессы бизнеса: от входящей заявки до сделки и отчёта собственнику.",
    ]
    ctas=[
        "Можно прислать схему текущего процесса — покажем, что имеет смысл автоматизировать в первую очередь.",
        "Опишите, где сейчас больше всего ручной работы или теряются заявки — предложим рабочую схему автоматизации.",
        "Разберём один процесс и оценим внедрение до начала разработки.",
    ]
    drafts=[]
    for p in platforms:
        for topic_key,cfg in topics:
            for i in range(max(1,min(int(variants),20))):
                shift=i%len(cfg["keywords"])
                selected=(cfg["keywords"][shift:]+cfg["keywords"][:shift])[:7]
                body=(
                    f"{cfg['title']}\n\n"
                    f"{hooks[i%len(hooks)]}\n\n"
                    f"Типичная проблема: {cfg['pain']}.\n\n"
                    f"Что делаем: {cfg['result']}. "
                    "Настраиваем только нужные бизнесу модули: CRM, API-интеграции, сообщения, звонки, "
                    "аналитику, реактивацию и автоматические действия.\n\n"
                    "Стоимость зависит от процесса: небольшие автоматизации обычно начинаются от 50–80 тыс. ₽, "
                    "комплексные внедрения — от 100–300 тыс. ₽ и выше. Сначала фиксируем объём и результат.\n\n"
                    f"{ctas[i%len(ctas)]}\n\n"
                    f"BORIS: {BORIS_SITE}\n\n"
                    "Ключевые направления: "+", ".join(selected)
                )
                drafts.append({"id":_draft_id(p.key,topic_key,i),"platform":p.key,"platform_name":p.name,
                               "topic":topic_key,"title":cfg["title"],"text":body,"site_url":BORIS_SITE,
                               "status":"needs_owner_approval","generated_without_openai":True,"variant":i+1})
    return drafts

def persist_drafts(drafts:list[dict])->dict:
    current={x["id"]:x for x in _load_json(DRAFTS_FILE,[])}
    for d in drafts:
        old=current.get(d["id"])
        if old and old.get("status") in {"approved","posted"}:
            d["status"]=old["status"]
            if old.get("publication_url"):
                d["publication_url"]=old["publication_url"]
        current[d["id"]]=d
    rows=list(current.values())
    _save_json(DRAFTS_FILE,rows)
    return {"saved":len(drafts),"total":len(rows),"path":str(DRAFTS_FILE)}

def approve_draft(draft_id:str)->dict:
    rows=_load_json(DRAFTS_FILE,[])
    found=None
    for row in rows:
        if row["id"]==draft_id:
            row["status"]="approved"; found=row; break
    if not found:
        raise SystemExit(f"draft not found: {draft_id}")
    _save_json(DRAFTS_FILE,rows)
    return found

def reject_draft(draft_id:str)->dict:
    rows=_load_json(DRAFTS_FILE,[])
    found=None
    for row in rows:
        if row["id"]==draft_id:
            row["status"]="rejected"; found=row; break
    if not found:
        raise SystemExit(f"draft not found: {draft_id}")
    _save_json(DRAFTS_FILE,rows)
    return found

def mark_posted(draft_id:str,url:str)->dict:
    rows=_load_json(DRAFTS_FILE,[])
    draft=next((x for x in rows if x["id"]==draft_id),None)
    if not draft:
        raise SystemExit(f"draft not found: {draft_id}")
    if draft.get("status")!="approved":
        raise SystemExit("publication blocked: owner approval required")
    if not url.startswith(("https://","http://")):
        raise SystemExit("publication url must be absolute")
    draft["status"]="posted"; draft["publication_url"]=url
    _save_json(DRAFTS_FILE,rows)
    pubs=_load_json(PUBLICATIONS_FILE,[])
    item={"draft_id":draft_id,"platform":draft["platform"],"topic":draft["topic"],"url":url,
          "site_url":BORIS_SITE,"posted_at":datetime.now(timezone.utc).isoformat()}
    pubs.append(item); _save_json(PUBLICATIONS_FILE,pubs)
    return item

def register_mailbox(address:str,provider:str)->dict:
    provider=provider.lower().strip()
    if provider not in {"gmail","yandex","other"}:
        raise SystemExit("provider must be gmail, yandex or other")
    rows=_load_json(MAILBOXES_FILE,[])
    item={"address":address.strip().lower(),"provider":provider,"password_stored":False,
          "purpose":"service_marketplace_registration","created_at":datetime.now(timezone.utc).isoformat()}
    rows=[x for x in rows if x.get("address")!=item["address"]]+[item]
    _save_json(MAILBOXES_FILE,rows)
    return item

def publication_status()->dict:
    drafts=_load_json(DRAFTS_FILE,[])
    pubs=_load_json(PUBLICATIONS_FILE,[])
    mailboxes=_load_json(MAILBOXES_FILE,[])
    counts={}
    for d in drafts:
        s=d.get("status","unknown"); counts[s]=counts.get(s,0)+1
    return {"drafts":len(drafts),"publications":len(pubs),"mailboxes":len(mailboxes),
            "by_status":counts,"latest_publications":pubs[-20:],
            "drafts_file":str(DRAFTS_FILE),"publications_file":str(PUBLICATIONS_FILE),
            "mailboxes_file":str(MAILBOXES_FILE)}

def main()->int:
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="cmd",required=True)
    sub.add_parser("platforms")
    sub.add_parser("offers")
    reg=sub.add_parser("registration-plan"); reg.add_argument("--email",required=True)
    sub.add_parser("posting-plan")
    gen=sub.add_parser("generate"); gen.add_argument("--platform",default="all"); gen.add_argument("--topic",default="all"); gen.add_argument("--variants",type=int,default=3); gen.add_argument("--save",action="store_true")
    ap=sub.add_parser("approve"); ap.add_argument("draft_id")
    rj=sub.add_parser("reject"); rj.add_argument("draft_id")
    mp=sub.add_parser("mark-posted"); mp.add_argument("draft_id"); mp.add_argument("url")
    mb=sub.add_parser("add-mailbox"); mb.add_argument("address"); mb.add_argument("--provider",required=True)
    sub.add_parser("status")
    args=parser.parse_args()
    if args.cmd=="platforms": out=[asdict(x) for x in sorted(PLATFORMS,key=lambda p:p.priority)]
    elif args.cmd=="offers": out=[asdict(x) for x in OFFERS]
    elif args.cmd=="registration-plan": out=registration_plan(args.email)
    elif args.cmd=="posting-plan": out=posting_plan()
    elif args.cmd=="generate":
        drafts=generate_drafts(args.platform,args.topic,args.variants)
        out={"drafts":drafts,"persist":persist_drafts(drafts) if args.save else None}
    elif args.cmd=="approve": out=approve_draft(args.draft_id)
    elif args.cmd=="reject": out=reject_draft(args.draft_id)
    elif args.cmd=="mark-posted": out=mark_posted(args.draft_id,args.url)
    elif args.cmd=="add-mailbox": out=register_mailbox(args.address,args.provider)
    elif args.cmd=="status": out=publication_status()
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
