# -*- coding: utf-8 -*-
"""Генерация персонального черновика реактивации.
Модель ПРОДОЛЖАЕТ конкретный разговор, а не пишет шаблон «актуально ли ещё».
Всё, что она сочинила сверх переписки, ловится детерминированными проверками."""
import json
import logging
import re

from sqlalchemy import text

from app.reactivation_ai import build_context, _extract_text

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


def _digits(s):
    return set(re.findall(r"\d[\d\s]{2,}\d|\d{3,}", re.sub(r"[.,]", "", s or "")))


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
    task = ("Причина, по которой диалог остановился: %s\n"
            "Цель сообщения: %s — %s\n"
            "Что известно о диалоге: %s\n\nПЕРЕПИСКА:\n%s"
            % (reason or "no_reply", goal or "clarify_relevance",
               GOAL_HINT.get(goal or "", "продолжить разговор по существу"),
               summary or "-", ctx))
    # ТОЛЬКО OpenAI. Решения «писать или не писать клиенту» не должны зависеть от того,
    # какая модель сегодня доступна: A/B 05.08 показал у GigaChat ложные разрешения —
    # он не видит невыполненных обещаний продавца и пропускает отказы клиентов.
    from gigachat_pool import _openai_fallback_completion
    from gigachat.models import Messages, MessagesRole
    sys_role = getattr(MessagesRole, "SYSTEM", MessagesRole.USER)
    resp = _openai_fallback_completion(
        [Messages(role=sys_role, content=SYSTEM),
         Messages(role=MessagesRole.USER, content=task)],
        temperature=0.3, max_completion_tokens=700,
        account_id=account_id, operation=OPERATION)
    body = _extract_text(resp).strip()
    body = re.sub(r"^```(?:json)?|```$", "", body, flags=re.M).strip()
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        return {"ok": False, "error": "модель вернула не JSON", "raw": body[:300]}
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        return {"ok": False, "error": "JSON не разобран: %s" % e, "raw": body[:300]}
    for k in ("message", "facts_used", "confidence"):
        if k not in d:
            return {"ok": False, "error": "нет поля %s" % k}
    facts = [f for f in (d.get("facts_used") or []) if isinstance(f, dict)]
    invented_ids = [f.get("message_id") for f in facts
                    if f.get("message_id") is not None and f.get("message_id") not in ids]
    d["facts_used"] = [f for f in facts if f.get("message_id") in ids or f.get("message_id") is None]
    d["invented_ids"] = invented_ids
    d["problems"] = verify(d.get("message"), ctx)
    d["ok_to_show"] = not d["problems"] and not invented_ids
    return {"ok": True, "draft": d, "context_ids": ids}
