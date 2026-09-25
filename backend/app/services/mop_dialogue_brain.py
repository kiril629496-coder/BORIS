"""Per-account MOP dialogue brain and knowledge-gap notifications.

Privacy contract:
- account brain may use sanitized question templates only inside its own account_id;
- niche brain stores only aggregated semantic stages from >=2 independent accounts;
- no phone/email/URL/raw client transcript is copied into niche/global brain.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

ACCOUNT_TABLE = "mop_account_dialogue_brain"
NICHE_TABLE = "mop_niche_dialogue_brain"
GAP_TABLE = "mop_knowledge_gaps"

TAG_RULES = [
    ("need", "Потребность/задача", (r"что.*нуж", r"какая.*задач", r"что.*хотит", r"что.*ищет", r"какой.*результат")),
    ("product", "Тип продукта/услуги", (r"какой.*тип", r"что.*именно", r"какая.*модель", r"какой.*вариант", r"новострой|вторич")),
    ("dimensions", "Размер/площадь", (r"размер", r"площад", r"габарит", r"сколько.*комнат", r"комнатност")),
    ("quantity", "Количество", (r"сколько.*штук", r"количеств", r"сколько.*нуж")),
    ("location", "География/адрес", (r"район", r"метро", r"город", r"адрес", r"куда.*достав", r"где.*наход")),
    ("timing", "Срок/дата", (r"когда", r"к какому.*срок", r"на какой.*срок", r"срок")),
    ("budget", "Бюджет/сумма", (r"бюджет", r"какая.*сумм", r"сколько.*план", r"стоимост.*покуп")),
    ("payment", "Оплата/финансирование", (r"ипотек", r"наличн", r"способ.*оплат", r"первоначальн.*взнос", r"рассроч")),
    ("income", "Доход/квалификация", (r"доход", r"трудоустро", r"официальн", r"самозанят")),
    ("credit_load", "Действующие обязательства", (r"действующ.*кредит", r"кредитн.*карт", r"кредитов")),
    ("delivery", "Доставка/логистика", (r"достав", r"самовывоз", r"манипулятор", r"логист")),
    ("condition", "Состояние/комплектация", (r"новая.*б/у|б/у.*нов", r"комплектац", r"состояни")),
    ("material", "Материал/характеристики", (r"материал", r"цвет", r"толщин", r"марка", r"бренд")),
    ("availability", "Наличие", (r"в налич", r"есть.*сейчас", r"под заказ")),
    ("contact", "Контакт", (r"номер.*телефон", r"телефон", r"контакт")),
    ("next_step", "Следующий шаг", (r"созвон", r"встреч", r"замер", r"когда.*удоб", r"отправить.*расчет", r"связаться")),
]

def ensure_tables(db) -> None:
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def niche_key(name: str, niche: str, description: str = "") -> str:
    s = " ".join((str(name or ""), str(niche or ""), str(description or ""))).lower().replace("ё","е")
    rules = [
        ("bytovki", ("бытов", "блок-контейнер")),
        ("ipoteka_nedvizhimost", ("ипотек", "риелтор", "недвижим", "брокер")),
        ("plitka", ("тротуар", "брусчат", "плитк")),
        ("mebel", ("мебел", "кухн", "гардероб", "шкаф")),
        ("steklo", ("стекл", "перегород")),
        ("shtukaturka", ("штукатур",)),
        ("beton_stroitelstvo", ("бетон", "фундамент", "монолит", "жби")),
        ("almaznoe", ("алмазн",)),
        ("evakuatory", ("эвакуатор",)),
        ("avto", ("авто с выкуп", "автомобил", "camry", "машин")),
        ("dveri", ("двер",)),
        ("angary", ("ангар", "ферм", "металлоконструк")),
        ("nerudnye", ("песок", "щебен", "грунт")),
        ("water_gear", ("водн", "гидрокостюм", "вейкборд", "аквашуз", "спасательн", "экипировк")),
        ("electric_bikes", ("электровелосип", "электросамокат", "венбокс", "электротранспорт")),
        ("building_materials", ("строительн", "стройматериал", "материалы")),
        ("marketing", ("маркетинг", "продвижение на авито")),
    ]
    for key, needles in rules:
        if any(n in s for n in needles):
            return key
    return ""


def _sanitize_question(value: str) -> str:
    s = " ".join(str(value or "").replace("\n"," ").split()).strip()
    if not s or "?" not in s:
        return ""
    # Redact first: dots in URLs/emails must not split the final question.
    s = re.sub(r"https?://\S+|www\.\S+", "[ссылка]", s, flags=re.I)
    s = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "[email]", s, flags=re.I)
    s = re.sub(r"(?:\+?7|8)[\s()\-]*\d{3}[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", "[телефон]", s)
    s = re.sub(r"\b\d[\d\s.,]{3,}\s*(?:₽|руб(?:лей|ля|\.?)?|тыс(?:яч[аи]?)?|млн|миллион\w*)", "[сумма]", s, flags=re.I)
    parts = re.findall(r"[^.!?]{2,220}\?", s)
    q = (parts[-1] if parts else s).strip()
    q = re.sub(r"^(?:ru|en)\s+", "", q, flags=re.I).strip()
    low_q = q.lower().replace("ё","е").strip(" ?!.,")
    if low_q in {"почему", "зачем", "что", "как", "чем могу помочь", "актуально"}:
        return ""
    if len(re.findall(r"[а-яёa-z0-9]+", q, re.I)) < 2:
        return ""
    return q[:220]


def _norm_template(q: str) -> str:
    low = q.lower().replace("ё","е")
    low = re.sub(r"\[[^\]]+\]", " ", low)
    low = re.sub(r"\b\d+\b", " # ", low)
    low = re.sub(r"\b(?:подскажите|пожалуйста|уточните|скажите|правильно понимаю|понял|поняла)\b", " ", low)
    low = " ".join(re.findall(r"[а-яa-z0-9#]+", low, re.I))
    return low[:180]


def classify_question(q: str) -> tuple[str,str]:
    low = q.lower().replace("ё","е")
    for key,label,patterns in TAG_RULES:
        if any(re.search(p, low, re.I) for p in patterns):
            return key,label
    return "custom","Специфический вопрос ниши"


def _payload(db, table: str, key_col: str, key: str) -> dict[str,Any]:
    row=db.execute(text(f"SELECT payload FROM {table} WHERE {key_col}=:k"),{"k":key}).first()
    if not row or not row[0]:
        return {}
    return row[0] if isinstance(row[0],dict) else json.loads(row[0])


def account_dialogue_payload(account_id: str) -> dict[str,Any]:
    db=SessionLocal()
    try:
        ensure_tables(db)
        return _payload(db,ACCOUNT_TABLE,"account_id",str(account_id or ""))
    except Exception:
        try: db.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=158)
        return {}
    finally:
        db.close()


def dialogue_context(account_id: str, max_chars: int=5000) -> str:
    account_id=str(account_id or "").strip()
    db=SessionLocal()
    try:
        ensure_tables(db)
        acc=_payload(db,ACCOUNT_TABLE,"account_id",account_id)
        row=db.execute(text("SELECT coalesce(name,''),coalesce(company_niche,''),coalesce(company_description,'') FROM accounts WHERE account_id=:a"),{"a":account_id}).first()
        nk=niche_key(*(row or ("","","")))
        niche=_payload(db,NICHE_TABLE,"niche_key",nk) if nk else {}
    except Exception:
        try: db.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=175)
        return ""
    finally:
        db.close()

    parts=[
        "ДИАЛОГОВЫЙ МОЗГ ТЕКУЩЕГО АККАУНТА.",
        "Это подсказка по порядку разговора, выученная из его собственной истории. Не повторяй уже отвеченные вопросы и не копируй механически старые фразы.",
    ]
    if acc:
        s=acc.get("summary") or {}
        parts.append(f"История: {int(s.get('messages') or 0)} сообщений, {int(s.get('chats') or 0)} диалогов.")
        seq=acc.get("recommended_sequence") or []
        if seq:
            parts.append("Обычная последовательность этапов этого аккаунта:")
            for i,x in enumerate(seq[:10],1):
                parts.append(f"{i}. {x.get('label')}")
        tmpls=acc.get("question_templates") or []
        if tmpls:
            parts.append("Безопасные примеры вопросов из собственной истории аккаунта:")
            for x in tmpls[:10]:
                parts.append(f"- {x.get('question')} (встречалось: {int(x.get('support') or 0)})")
    if niche and int(niche.get("accounts") or 0)>=2:
        parts.append(f"Нишевой резерв: агрегат по {int(niche.get('accounts') or 0)} независимым аккаунтам этой ниши.")
        for x in (niche.get("recommended_sequence") or [])[:8]:
            parts.append(f"- {x.get('label')}")
    parts.append("Локальные подтверждённые факты, цены и правила текущего account_id всегда важнее этой статистики.")
    return "\n".join(parts)[:max(1200,int(max_chars))]


NICHE_DEFAULT_QUESTIONS = {
    "bytovki": [
        "Нужна аренда или покупка бытовки?",
        "Какой размер бытовки нужен?",
        "К какому сроку нужна бытовка?",
        "Подскажите адрес объекта или доставки?",
    ],
    "ipoteka_nedvizhimost": [
        "Что именно хотите решить: ипотеку, рефинансирование, кредит под залог или подбор недвижимости?",
    ],
    "plitka": [
        "Что именно нужно: плитка, брусчатка или другой материал?",
        "Какая примерно площадь в квадратных метрах?",
        "В каком городе или районе объект?",
        "Нужен только материал или также укладка?",
        "К какому сроку это нужно?",
    ],
    "mebel": [
        "Какой тип мебели нужен?",
        "В каком городе нужен проект?",
        "Есть примерные размеры помещения или зоны?",
        "Есть ориентир по стилю и цвету?",
        "Какой примерно бюджет рассматриваете?",
        "Когда планируете реализовать проект?",
    ],
    "steklo": [
        "Что именно нужно изготовить или установить?",
        "Есть примерные размеры?",
        "В каком городе находится объект?",
        "К какому сроку нужен результат?",
        "Нужен замер на объекте?",
    ],
    "shtukaturka": [
        "Что нужно штукатурить: стены, потолок или весь объект?",
        "Какая примерно площадь?",
        "В каком городе или районе объект?",
        "К какому сроку нужно начать работы?",
        "Нужен выезд на замер?",
    ],
    "beton_stroitelstvo": [
        "Какие именно работы нужны?",
        "Есть примерный объём или размеры?",
        "Где находится объект?",
        "К какому сроку нужны работы?",
        "Нужен выезд специалиста на объект?",
    ],
    "almaznoe": [
        "Что нужно сделать: отверстие, резку или другой вид работ?",
        "Какой диаметр или размер нужен примерно?",
        "Из какого материала конструкция?",
        "Где находится объект?",
        "Когда нужно выполнить работу?",
    ],
    "evakuatory": [
        "Где сейчас находится автомобиль?",
        "Куда нужно доставить автомобиль?",
        "Что за автомобиль и на ходу ли он?",
        "Когда нужен эвакуатор?",
    ],
    "avto": [
        "Какой автомобиль или вариант рассматриваете?",
        "Какой примерно бюджет рассматриваете?",
        "В каком городе вы находитесь?",
        "Когда планируете принять решение?",
    ],
    "dveri": [
        "Какие двери нужны: входные или межкомнатные?",
        "Сколько дверей нужно?",
        "Есть размеры проёмов?",
        "В каком городе или районе объект?",
        "Нужен монтаж?",
    ],
    "angary": [
        "Для какой задачи нужен ангар или конструкция?",
        "Какие примерные размеры нужны?",
        "В каком регионе будет объект?",
        "К какому сроку нужен результат?",
    ],
    "nerudnye": [
        "Какой материал нужен?",
        "Какой объём нужен примерно?",
        "Куда нужна доставка?",
        "К какому сроку нужен материал?",
    ],
    "water_gear": [
        "Какая именно экипировка вам нужна?",
        "Для какого вида спорта или задачи выбираете экипировку?",
        "Какой размер нужен?",
        "В каком городе вы находитесь?",
        "К какому сроку нужна экипировка?",
    ],
    "electric_bikes": [
        "Для каких поездок выбираете электровелосипед?",
        "Какой примерно запас хода вам нужен?",
        "Какой бюджет рассматриваете?",
        "В каком городе нужна доставка или самовывоз?",
        "Когда планируете покупку?",
    ],
    "building_materials": [
        "Какой именно строительный материал нужен?",
        "Какое количество или объём нужен примерно?",
        "Куда нужна доставка?",
        "К какому сроку нужен материал?",
    ],
    "marketing": [
        "Какая у вас ниша и что продаёте?",
        "Какой результат хотите получить от продвижения?",
        "Сколько обращений сейчас приходит примерно?",
        "Какие каналы уже используете?",
    ],
}


GENERIC_TAG_QUESTIONS = {
    "need": "Подскажите, пожалуйста, что именно хотите решить?",
    "product": "Какой вариант или тип услуги вам нужен?",
    "dimensions": "Какие размеры или параметры вам нужны?",
    "quantity": "Какое количество вам нужно?",
    "location": "В каком городе или районе нужен результат?",
    "timing": "К какому сроку это нужно?",
    "budget": "Какой примерно бюджет рассматриваете?",
    "payment": "Какой вариант оплаты или финансирования рассматриваете?",
    "income": "Есть подтверждаемый доход или нужна программа с альтернативным подтверждением?",
    "credit_load": "Есть сейчас действующие кредиты?",
    "delivery": "Куда нужна доставка или выезд?",
    "condition": "Какое состояние или комплектация нужны?",
    "material": "Есть требования к материалу или характеристикам?",
    "availability": "Нужно из наличия или подойдёт под заказ?",
    "contact": "Если удобно, оставьте номер телефона для связи.",
    "next_step": "Какой следующий шаг вам удобнее: продолжить здесь или коротко созвониться?",
}


def next_learned_question(account_id: str, history_text: str) -> str:
    """Account-private learned question, then privacy-safe niche fallback."""
    account_id=str(account_id or "").strip()
    payload=account_dialogue_payload(account_id)
    templates=payload.get("question_templates") or []
    ours=" ".join(re.findall(r"Мы:\s*(.+)",str(history_text or ""),re.I))
    ours_norm=_norm_template(ours)
    asked_tags=set()
    for m in re.findall(r"Мы:\s*([^\n]+)",str(history_text or ""),re.I):
        if "?" in m:
            asked_tags.add(classify_question(m)[0])

    # 1) Account-private real/configured templates.
    seq=[str(x.get("tag") or "") for x in (payload.get("recommended_sequence") or []) if str(x.get("tag") or "")]
    ordered=[]
    for tag in seq:
        ordered.extend([x for x in templates if x.get("tag")==tag])
    ordered.extend(templates)
    seen=set()
    for x in ordered:
        q=str(x.get("question") or "").strip()
        tag=str(x.get("tag") or "")
        key=_norm_template(q)
        if not q or key in seen:
            continue
        seen.add(key)
        if tag and tag!="custom" and tag in asked_tags:
            continue
        if key and key in ours_norm:
            continue
        return q

    # 2) Niche semantic aggregate (>=2 independent accounts). Never copy another
    # account's literal text; render only a generic safe question for the tag.
    db=SessionLocal()
    try:
        ensure_tables(db)
        row=db.execute(text(
            "SELECT coalesce(name,''),coalesce(company_niche,''),coalesce(company_description,'') "
            "FROM accounts WHERE account_id=:a"
        ),{"a":account_id}).first()
        nk=niche_key(*(row or ("","","")))
        niche=_payload(db,NICHE_TABLE,"niche_key",nk) if nk else {}
    except Exception:
        try: db.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=383)
        niche={}
    finally:
        db.close()
    if int(niche.get("accounts") or 0)>=2:
        for x in niche.get("recommended_sequence") or []:
            tag=str(x.get("tag") or "")
            if not tag or tag in asked_tags:
                continue
            q=GENERIC_TAG_QUESTIONS.get(tag,"")
            if q:
                return q

    # 3) Safe niche starter. Used only when account history/config and niche
    # aggregate did not yield a next question.
    for q in NICHE_DEFAULT_QUESTIONS.get(nk, []):
        tag,_ = classify_question(q)
        key=_norm_template(q)
        if tag in asked_tags:
            continue
        if key and key in ours_norm:
            continue
        return q
    return ""


def record_knowledge_gap(account_id: str, chat_id: str, question: str, reason: str) -> dict[str,Any]:
    """Create/refresh one account-scoped knowledge gap and visible notification."""
    account_id=str(account_id or "").strip()
    if not account_id:
        return {}
    q=" ".join(str(question or "").split()).strip()[:800]
    reason=str(reason or "unknown_fact").strip()[:120]
    fingerprint=hashlib.sha256((account_id+"|"+reason+"|"+_norm_template(q)).encode("utf-8")).hexdigest()
    db=SessionLocal()
    try:
        ensure_tables(db)
        row=db.execute(text(f"SELECT id,status,occurrences,last_notified_at FROM {GAP_TABLE} WHERE gap_key=:g"),{"g":fingerprint}).first()
        if row:
            gap_id,status,occ,last_notified=row
            db.execute(text(f"""
                UPDATE {GAP_TABLE}
                SET occurrences=occurrences+1,last_seen_at=now(),avito_chat_id=coalesce(:c,avito_chat_id)
                WHERE gap_key=:g
            """),{"g":fingerprint,"c":str(chat_id or "") or None})
        else:
            gap_id=db.execute(text(f"""
                INSERT INTO {GAP_TABLE}(gap_key,account_id,avito_chat_id,reason,question)
                VALUES(:g,:a,:c,:r,:q) RETURNING id
            """),{"g":fingerprint,"a":account_id,"c":str(chat_id or "") or None,"r":reason,"q":q}).scalar_one()
            last_notified=None
        db.commit()

        # Notify immediately on first occurrence, then at most once per 24h while open.
        now=datetime.now(timezone.utc)
        should_notify=(last_notified is None) or ((now-last_notified).total_seconds()>=86400 if getattr(last_notified,"tzinfo",None) else True)
        if should_notify:
            from app.services.notification_store import append_notification
            text_msg=(
                "🧠 МОПу не хватает знания. "
                f"Вопрос клиента: «{q[:260]}». "
                "Добавьте/подтвердите ответ в знаниях компании — после этого BORIS будет использовать его в следующих диалогах."
            )
            append_notification(db,account_id,{
                "ts":now.replace(microsecond=0).isoformat(),
                "kind":"mop_knowledge_gap",
                "text":text_msg,
                "related_results":[{"type":"mop_knowledge_gap","gap_id":int(gap_id),"chat_id":str(chat_id or ""), "reason":reason}],
                "read":False,
            },idempotency_key=f"mop-gap:{fingerprint}:{now.date().isoformat()}",commit=False)
            db.execute(text(f"UPDATE {GAP_TABLE} SET last_notified_at=now() WHERE id=:i"),{"i":gap_id})
            db.commit()
        return {"gap_id":int(gap_id),"gap_key":fingerprint,"notified":bool(should_notify)}
    except Exception:
        try: db.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=458)
        return {}
    finally:
        db.close()
