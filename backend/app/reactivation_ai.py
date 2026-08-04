# -*- coding: utf-8 -*-
"""AI-анализ кандидата реактивации. Возвращает СТРУКТУРУ, не свободный текст.
Модель классифицирует диалог, решение принимает код.
Расход пишется автоматически обёрткой chat_with_fallback (account_id + operation)."""
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import text

log = logging.getLogger(__name__)

OPERATION = "reactivation_candidate_analysis"
MAX_MESSAGES = 24
MAX_CHARS = 600
LOW_CONFIDENCE = 0.70

# Причины отвечают на вопрос «почему диалог остановился или требует действия».
REASONS_ALLOWED = ("no_reply", "price_requested", "estimate_sent", "seller_action_missing",
                   "asked_to_contact_later", "not_interested_now", "purchase_confirmed",
                   "do_not_contact", "other")
# Зарезервированы, но в автоматическом отборе не участвуют.
REASONS_RESERVED = ("old_customer", "no_phone")

# Цели отвечают на вопрос «что делать следующим шагом».
GOALS_ALLOWED = ("clarify_relevance", "request_phone", "continue_estimate",
                 "send_missing_information", "complete_promised_action",
                 "contact_at_requested_time", "offer_alternative", "close_dialog", "no_action")

TEMPERATURES = ("hot", "warm", "cold")

REQUIRED = ("eligible", "reason", "confidence", "lead_temperature", "purchase_probability",
            "last_meaningful_stage", "phone_received", "purchase_confirmed",
            "asked_not_to_contact", "recommended_message_goal", "summary",
            "evidence_message_ids", "needs_manager_action")

SYSTEM = """Ты аналитик отдела продаж. Тебе дают переписку продавца с покупателем на Avito.
Задача: классифицировать, что произошло с диалогом и что уместно сделать дальше.

ЖЁСТКИЕ ПРАВИЛА:
1. Отвечай ТОЛЬКО валидным JSON без markdown, без пояснений, без ``` .
2. Не придумывай факт покупки. purchase_confirmed=true только если покупатель прямо
   подтвердил заказ, оплату или доставку. Старый диалог сам по себе покупкой не является.
3. do_not_contact=true СТРОГО при явном запрете писать: «не пишите», «прекратите»,
   «удалите мой номер», «не беспокойте». Фразы «не актуально», «уже купил», «пока не нужно» —
   это отказ или закрытие, но НЕ запрет писать навсегда: для них reason="not_interested_now",
   а asked_not_to_contact=false.
4. evidence_message_ids — только те id, которые есть во входных данных. Не выдумывай id.
5. Если данных мало или вывод неоднозначен — ставь confidence ниже 0.7, не угадывай.
6. Оценивай только эту переписку. Никаких сведений о других клиентах у тебя нет.
7. reason отвечает ПОЧЕМУ ДИАЛОГ ОСТАНОВИЛСЯ, recommended_message_goal — ЧТО ДЕЛАТЬ ДАЛЬШЕ.
   Это разные списки. Значение из списка целей в поле reason недопустимо.
8. ДОЛГ ПРОДАВЦА. Если покупатель ждёт от продавца обещанного (расчёт, цену, фото, наличие,
   документы, условия), а продавец не прислал — reason="seller_action_missing",
   needs_manager_action=true, recommended_message_goal="complete_promised_action".
   В summary укажи, ЧТО именно продавец не выполнил. В evidence_message_ids включи
   и запрос покупателя, и обещание продавца.
9. Если eligible=false, поле rejection_reason обязательно и должно объяснять отказ
   одной короткой фразой по-русски. Пустым оно быть не может.
10. МОЛЧАНИЕ ПОКУПАТЕЛЯ — ЭТО ЦЕЛЕВОЙ СЛУЧАЙ, А НЕ ПОВОД ДЛЯ ОТКАЗА. Если покупатель
   задал вопрос, получил ответ и перестал отвечать — это именно то, ради чего мы пишем
   повторно: eligible=true, reason="no_reply" (или "estimate_sent", если был расчёт).
   Фраза «больше не отвечал» сама по себе НИКОГДА не является основанием для eligible=false.
11. eligible=false допустим ТОЛЬКО в этих случаях: подтверждена покупка · явный отказ
   или «не актуально» · запрет писать · товар не относится к этому продавцу ·
   диалог не про покупку (спам, бот, вопрос не по теме) · в диалоге нет содержания.
12. evidence_message_ids не может быть пустым: укажи хотя бы один id, на котором
   основан вывод.

ФОРМАТ ОТВЕТА:
{"eligible": true|false,
 "rejection_reason": "если eligible=false — почему, иначе null",
 "reason": "почему остановился: no_reply|price_requested|estimate_sent|seller_action_missing|asked_to_contact_later|not_interested_now|purchase_confirmed|do_not_contact|other",
 "confidence": 0.0-1.0,
 "lead_temperature": "hot|warm|cold",
 "purchase_probability": 0.0-1.0,
 "last_meaningful_stage": "коротко, что было последним содержательным шагом",
 "phone_received": true|false,
 "purchase_confirmed": true|false,
 "asked_not_to_contact": true|false,
 "needs_manager_action": true|false,
 "recommended_contact_at": "YYYY-MM-DD или null",
 "recommended_message_goal": "что делать: clarify_relevance|request_phone|continue_estimate|send_missing_information|complete_promised_action|contact_at_requested_time|offer_alternative|close_dialog|no_action",
 "summary": "одно предложение по-русски",
 "evidence_message_ids": [id, id]}"""


def build_context(db, account_id, avito_chat_id, item_title=None):
    rows = db.execute(text(
        "SELECT id, direction, to_timestamp(avito_created_at) AS ts, coalesce(text,'')"
        " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
        "   AND coalesce(msg_type,'') <> 'system'"
        " ORDER BY avito_created_at DESC, id DESC LIMIT :n"),
        {"a": account_id, "c": avito_chat_id, "n": MAX_MESSAGES}).fetchall()
    rows = list(reversed(rows))
    ids = [r[0] for r in rows]
    lines = []
    for mid, direction, ts, body in rows:
        who = "ПОКУПАТЕЛЬ" if str(direction).lower().startswith("in") else "ПРОДАВЕЦ"
        body = re.sub(r"\s+", " ", body).strip()[:MAX_CHARS]
        lines.append("[%d] %s %s: %s" % (mid, ts.strftime("%d.%m.%Y"), who, body))
    head = "Объявление: %s\nСегодня: %s\n\n" % (
        item_title or "не указано", datetime.now(timezone.utc).strftime("%d.%m.%Y"))
    return head + "\n".join(lines), ids


def _extract_text(resp):
    """Обёртка может вернуть строку, словарь OpenAI-формата или кортеж."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, (list, tuple)):
        return _extract_text(resp[0]) if resp else ""
    if isinstance(resp, dict):
        ch = resp.get("choices")
        if ch:
            msg = (ch[0] or {}).get("message") or {}
            return msg.get("content") or (ch[0] or {}).get("text") or ""
        for k in ("content", "text", "answer", "result"):
            if resp.get(k):
                return resp[k]
    return str(resp)


def parse_verdict(raw, allowed_ids):
    body = _extract_text(raw).strip()
    body = re.sub(r"^```(?:json)?|```$", "", body, flags=re.M).strip()
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        return None, "модель вернула не JSON"
    try:
        data = json.loads(m.group(0))
    except Exception as e:
        return None, "JSON не разобран: %s" % e
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        return None, "нет обязательных полей: %s" % ", ".join(missing)
    try:
        data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
        data["purchase_probability"] = max(0.0, min(1.0, float(data["purchase_probability"])))
    except Exception:
        return None, "confidence или purchase_probability не число"
    if not data.get("eligible") and not str(data.get("rejection_reason") or "").strip():
        return None, "eligible=false без rejection_reason"
    raw_reason = str(data.get("reason") or "").strip()
    if raw_reason in REASONS_RESERVED:
        data["reason_raw"] = raw_reason
        data["reason"] = "other"
        data["reserved_reason"] = raw_reason
    elif raw_reason not in REASONS_ALLOWED:
        data["reason_raw"] = raw_reason
        data["reason"] = "other"
    if str(data.get("lead_temperature") or "").strip() not in TEMPERATURES:
        data["lead_temperature"] = "warm"
    if str(data.get("recommended_message_goal") or "").strip() not in GOALS_ALLOWED:
        data["goal_raw"] = data.get("recommended_message_goal")
        data["recommended_message_goal"] = "no_action"
    ev = [i for i in (data.get("evidence_message_ids") or []) if isinstance(i, int)]
    invented = [i for i in ev if i not in allowed_ids]
    data["evidence_message_ids"] = [i for i in ev if i in allowed_ids]
    data["invented_ids"] = invented
    return data, None


def decide(data):
    """Код принимает решение, а не модель. Возвращает (вердикт, объяснение)."""
    if data.get("asked_not_to_contact") or data.get("reason") == "do_not_contact":
        return "do_not_contact", "явный запрет писать"
    if data.get("needs_manager_action") or data.get("reason") == "seller_action_missing":
        return "manager_action", "продавец не выполнил обещанное"
    if data.get("reserved_reason") == "old_customer" and not data.get("purchase_confirmed"):
        return "needs_review", "old_customer без подтверждённой сделки"
    if data.get("purchase_confirmed") or data.get("reason") == "purchase_confirmed":
        return "not_eligible", "сделка подтверждена"
    if data.get("reason") == "not_interested_now":
        return "not_interested_now", "клиент сказал, что сейчас не нужно"
    if not data.get("eligible"):
        return "not_eligible", str(data.get("rejection_reason") or "модель признала неподходящим")
    if not data.get("evidence_message_ids"):
        return "needs_review", "нет подтверждающих сообщений"
    if data["confidence"] < LOW_CONFIDENCE:
        return "needs_review", "низкая уверенность %.2f" % data["confidence"]
    return "candidate", "подходит"


def analyze(db, account_id, avito_chat_id, item_title=None, model=None):
    ctx, ids = build_context(db, account_id, avito_chat_id, item_title)
    if not ids:
        return {"ok": False, "error": "в диалоге нет сообщений"}
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    # Формат сообщений — объекты Messages, как во всех остальных вызовах пула.
    # Словари ломают ветку фолбэка на OpenAI: она читает m.role.
    sys_role = getattr(MessagesRole, "SYSTEM", MessagesRole.USER)
    resp = chat_with_fallback(
        [Messages(role=sys_role, content=SYSTEM),
         Messages(role=MessagesRole.USER, content=ctx)],
        model=model, temperature=0, max_tokens=800,
        account_id=account_id, operation=OPERATION)
    data, err = parse_verdict(resp, set(ids))
    if err:
        log.warning("реактивация: разбор ответа не удался (%s), чат %s", err, avito_chat_id)
        return {"ok": False, "error": err, "raw": _extract_text(resp)[:400]}
    verdict, why = decide(data)
    return {"ok": True, "verdict": verdict, "why": why, "data": data, "context_ids": ids}
