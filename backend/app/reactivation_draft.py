# -*- coding: utf-8 -*-
"""Генерация персонального черновика реактивации.
Модель ПРОДОЛЖАЕТ конкретный разговор, а не пишет шаблон «актуально ли ещё».
Всё, что она сочинила сверх переписки, ловится детерминированными проверками."""
import logging
import re

from sqlalchemy import text

from app.reactivation_ai import build_context

log = logging.getLogger(__name__)

OPERATION = "reactivation_message_generation"
MAX_CHARS = 400

# Слова, которых в тексте быть не должно, если их нет в самой переписке.
BANNED_RE = [
    (r"скидк|акци|распродаж", "выдуманная скидка или акция"),
    (r"\bтолько\s+(сегодня|сейчас)\b|только\s+до\s+(?!\d{1,2}[:.]\d)\d|успей|успевайте|"
     r"последний\s+день|ограниченн\w*\s+(предложен|партия|количеств|врем)", "выдуманный срок"),
    (r"бесплатн", "выдуманное бесплатно"),
    (r"гаранти", "обещание гарантии"),
    (r"в\s+наличии|есть\s+на\s+складе", "утверждение о наличии"),
]

GOAL_HINT = {
    "clarify_relevance": "мягко уточнить, актуален ли ещё вопрос, и предложить продолжить",
    "continue_estimate": "предложить довести расчёт до конца, назвав недостающие данные",
    "request_phone": "предложить продолжить по телефону и попросить номер",
    "send_missing_information": "предложить прислать недостающую информацию",
    "remind_offer": "напомнить о ранее обсуждавшемся предложении",
    "contact_at_requested_time": "напомнить о себе в оговорённый срок",
    "offer_alternative": "предложить альтернативу тому, что обсуждали",
}

SYSTEM = """Ты менеджер по продажам. Пишешь ОДНО короткое сообщение покупателю на Avito,
который перестал отвечать. Задача — продолжить КОНКРЕТНЫЙ разговор, а не прислать шаблон.

ЖЁСТКИЕ ПРАВИЛА:
1. Отвечай ТОЛЬКО валидным JSON без markdown и пояснений.
2. Опирайся исключительно на переписку. НЕЛЬЗЯ придумывать: цены, скидки, акции, сроки,
   наличие, гарантии, бесплатные услуги, характеристики товара. Если факта нет в переписке —
   его нет вообще.
3. Можно и нужно ссылаться на то, что уже обсуждали: названную цену, размеры, сроки, город,
   договорённости. Каждый такой факт указывай в facts_used с id сообщения, откуда он взят.
4. Не дави, не торопи, не выдумывай личных обстоятельств покупателя.
5. Не здоровайся так, будто пишешь впервые, если переписка уже была.
6. Не более 400 символов, обычная человеческая речь, без эмодзи и КАПСА.
7. Если для достижения цели не хватает данных — так и напиши в missing и сформулируй
   сообщение как вопрос, а не как утверждение.

ФОРМАТ ОТВЕТА:
{"message": "текст сообщения",
 "facts_used": [{"fact": "что именно взято из переписки", "message_id": 123}],
 "missing": ["чего не хватает, чтобы ответить полнее"],
 "must_not_invent": ["что в этом диалоге запрещено придумывать"],
 "confidence": 0.0-1.0}"""


def _safe_contextual_fallback(db, account_id, avito_chat_id, item_title=None, reason=None):
    """Безопасный fallback без AI: только факт объявления + уже доказанная причина остановки."""
    title = (item_title or "").strip()
    if not title:
        row = db.execute(text("""
            SELECT item_title FROM messenger_messages
             WHERE account_id=:a AND avito_chat_id=:c
               AND btrim(coalesce(item_title,'')) <> ''
             ORDER BY avito_created_at DESC,id DESC LIMIT 1
        """), {"a": account_id, "c": avito_chat_id}).fetchone()
        title = str(row[0] or "").strip() if row else ""
    title = re.sub(r"\s+", " ", title)[:90]
    subject = (" по «%s»" % title) if title else ""
    templates = {
        "price_requested": "Подскажите, вопрос%s ещё актуален? Если да, продолжим по расчёту и уточним недостающие детали." % subject,
        "estimate_sent": "Подскажите, удалось посмотреть расчёт%s? Если вопрос ещё актуален, продолжим здесь." % subject,
        "asked_to_contact_later": "Возвращаюсь к вашему вопросу%s, как договаривались. Актуально продолжить?" % subject,
        "no_phone": "Подскажите, вопрос%s ещё актуален? Если да, продолжим здесь и согласуем удобный следующий шаг." % subject,
        "no_reply": "Подскажите, вопрос%s ещё актуален? Если да, продолжим с того места, где остановились." % subject,
    }
    return templates.get(reason) or ("Подскажите, вопрос%s ещё актуален? Если да, продолжим с того места, где остановились." % subject)


def _digits(s):
    return set(re.findall(r"\d[\d\s]{2,}\d|\d{3,}", re.sub(r"[.,]", "", s or "")))


def is_duplicate_outgoing(db, account_id, avito_chat_id, body):
    """Exact normalized duplicate of any previous real outgoing in this chat."""
    normalized = re.sub(r"\s+", " ", str(body or "").strip().lower())
    if not normalized:
        return False
    row = db.execute(text("""
        SELECT 1
          FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND lower(coalesce(direction,'')) IN ('out','outgoing')
           AND coalesce(msg_type,'') <> 'system'
           AND regexp_replace(lower(btrim(coalesce(text,''))), '\\s+', ' ', 'g')=:t
         LIMIT 1
    """), {"a": account_id, "c": avito_chat_id, "t": normalized}).fetchone()
    return bool(row)


def verify(draft_text, dialog_text):
    """Детерминированная проверка того, что модель ничего не досочинила."""
    problems = []
    t = (draft_text or "").strip()
    if not t:
        return ["пустой текст"]
    if len(t) > MAX_CHARS:
        problems.append("длиннее %d символов (%d)" % (MAX_CHARS, len(t)))
    low, dlow = t.lower(), (dialog_text or "").lower()
    for rx, label in BANNED_RE:
        if re.search(rx, low) and not re.search(rx, dlow):
            problems.append(label)
    # Business-policy claims are just as dangerous as invented prices. In auto
    # reactivation they need explicit evidence in the dialog, not inference.
    pickup_no = r"(?:самовывоз\w*.{0,24}(?:нет|нельзя|не\s+предусмотр)|(?:нет|нельзя|не\s+предусмотр).{0,24}самовывоз)"
    if re.search(pickup_no, low) and not re.search(pickup_no, dlow):
        problems.append("неподтверждённое условие самовывоза")
    if re.search(r"\bзависит\b", low) and not re.search(r"\bзависит\b", dlow):
        problems.append("неподтверждённое условие зависимости")
    # Сравниваем ТОЛЬКО цифры: в переписке между разрядами попадается неразрывный
    # пробел, и «85 000» из черновика не совпадало с «85\u00a0000» из диалога.
    dial_nums = {re.sub(r"\D", "", d) for d in _digits(dlow)}
    for num in _digits(low):
        if re.sub(r"\D", "", num) not in dial_nums:
            problems.append("число «%s» отсутствует в переписке" % num.strip())
    if re.search(r"[А-ЯЁ]{5,}", t):
        problems.append("КАПС в тексте")
    if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", t):
        problems.append("эмодзи в тексте")
    return problems


def generate(db, account_id, avito_chat_id, item_title=None, reason=None, goal=None,
             summary=None, model=None):
    ctx, ids = build_context(db, account_id, avito_chat_id, item_title)
    if not ids:
        return {"ok": False, "error": "в диалоге нет сообщений"}

    # Canonical path: реактивация использует ТОТ ЖЕ генератор, память и обучение,
    # что и обычный МОП. Никакого второго AI-контура и старого proxy здесь нет.
    rows = db.execute(text("""
        SELECT lower(coalesce(direction,'')), coalesce(text,'')
          FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND coalesce(msg_type,'') <> 'system'
           AND btrim(coalesce(text,'')) <> ''
         ORDER BY avito_created_at ASC,id ASC LIMIT 30
    """), {"a": account_id, "c": avito_chat_id}).fetchall()
    training_messages = [
        {"role": "client" if str(direction or "").startswith("in") else "mop", "text": txt}
        for direction, txt in rows if str(txt or "").strip()
    ]
    goal_text = GOAL_HINT.get(goal or "clarify_relevance", "вернуть клиента в диалог и продолжить разговор по существу")
    try:
        from app.api.messenger import generate_ai_draft_reply
        mop = generate_ai_draft_reply(
            account_id,
            {"id": "training:reactivation:" + str(avito_chat_id),
             "_training_messages": training_messages,
             "_training_goal": goal_text},
            style_hint="reactivation",
            return_meta=True,
        )
        body_text = str((mop or {}).get("text") or "").strip() if isinstance(mop, dict) else str(mop or "").strip()
        if body_text and not body_text.startswith("[Ошибка генерации черновика:"):
            problems = verify(body_text, ctx)
            if is_duplicate_outgoing(db, account_id, avito_chat_id, body_text):
                problems.append("точный дубль предыдущего исходящего")
            if not problems:
                return {"ok": True, "draft": {
                    "message": body_text, "facts_used": [], "missing": [],
                    "must_not_invent": [], "confidence": 1.0,
                    "invented_ids": [], "problems": [], "ok_to_show": True,
                    "provider": "canonical_mop", "analysis": (mop or {}).get("analysis") if isinstance(mop, dict) else None,
                }, "context_ids": ids}
            log.warning("canonical MOP draft rejected by deterministic verifier: %s", problems[:4])
        else:
            log.warning("canonical MOP draft unavailable: %s", body_text[:160])
    except Exception as exc:
        log.warning("canonical MOP draft failed: %s", str(exc)[:160])

    # Только аварийный fallback: не делает второй платный AI-вызов и опирается
    # лишь на доказанную причину + название объявления.
    fallback = _safe_contextual_fallback(db, account_id, avito_chat_id, item_title, reason)
    problems = verify(fallback, ctx)
    if is_duplicate_outgoing(db, account_id, avito_chat_id, fallback):
        problems.append("точный дубль предыдущего исходящего")
    return {"ok": True, "draft": {
        "message": fallback, "facts_used": [], "missing": [],
        "must_not_invent": [], "confidence": 1.0,
        "invented_ids": [], "problems": problems,
        "ok_to_show": not problems, "fallback": "contextual_no_ai"
    }, "context_ids": ids, "provider_fallback": True}

