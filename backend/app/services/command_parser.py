# -*- coding: utf-8 -*-
"""Парсер намерений Command Center.

По образцу chat.parse_task: тот же провайдер, та же чистка ответа, та же
обработка не-JSON. Но возвращает intent + entities, а НЕ план шагов.
Сам parse_task не трогаем — он живой кирпич оркестратора.
"""
import json
import os
import re

from gigachat_pool import chat_with_fallback
from gigachat.models import Messages, MessagesRole

from app.services.command_redact import redact_text
from app.services.command_prompt import COMMAND_PARSER_PROMPT, INTENTS
from app.services.exception_observability import observe_suppressed

GIGACHAT_KEY = os.getenv("GIGACHAT_KEY")
_GC_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat" + "-Pro")

OPERATION = "command.parse"


def _strip_json(raw):
    s = (raw or "").strip()
    s = re.sub(r"^```json\s*|\s*```$", "", s, flags=re.MULTILINE).strip()
    return s


def _fast_path(text, today=None):
    """Детерминированный путь для частых коротких команд.

    Нужен не вместо AI-парсера, а как production fail-safe: базовые команды
    пользователя должны работать даже при временной деградации внешнего LLM.
    """
    s = re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))
    if not s:
        return None

    # Частая навигация: человек может сказать не только «открой», но и
    # «покажи объявления», «где делать баннеры», «хочу посмотреть переписки».
    # Для этих явных экранных запросов не зовём LLM: маршрут должен быть стабильным.
    nav = (
        (("баннер",), "баннеры"),
        (("фотостуди", "картинк", "изображен", "фото для объяв"), "фотостудия"),
        (("сообщен", "переписк", "диалог", "чат"), "сообщения"),
        (("объявлен", "кампан"), "объявления"),
        (("аналитик",), "аналитика"),
        (("подключ", "аккаунт авито"), "подключить аккаунты авито"),
        (("маркетинг",), "маркетинг"),
        (("продаж", "crm", "срм"), "продажи"),
        (("база знан", "память"), "база знаний"),
        (("социаль", "соцсет", "вконтакте", "телеграм"), "социальные сети"),
        (("режим борис", "режим работы"), "режим бориса"),
        (("ии-сотруд", "ai-сотруд"), "ии-сотрудники"),
        (("сайт",), "создание сайтов"),
        (("тариф", "оплат"), "тариф и оплата"),
        (("компан",), "данные компании"),
        (("главн", "домой"), "главная"),
    )
    nav_verbs = ("открой", "перейди", "зайди", "покажи", "посмотреть", "где делать", "где найти", "где посмотреть")
    if any(v in s for v in nav_verbs):
        # «покажи статистику» — это запрос данных, не переход в Аналитику.
        if not ("статист" in s or "показател" in s or "результат" in s):
            for keys, section in nav:
                if any(k in s for k in keys):
                    return {
                        "understood": True, "intent": "navigation", "confidence": 1.0,
                        "entities": {"section": section},
                        "summary": "Открыть нужный раздел BORIS",
                        "clarify_question": None, "error": None,
                    }

    # Audit Gateway context has its own deterministic help path. This keeps
    # audit facts server-side and avoids sending raw audit state to the parser.
    audit_markers = (
        "мой аудит", "мой score", "мой скор", "результат аудита",
        "после аудита", "по аудиту", "что нашел boris", "что нашел борис",
        "что boris нашел", "что борис нашел",
    )
    if any(w in s for w in audit_markers):
        return {
            "understood": True, "intent": "chit_chat", "confidence": 1.0,
            "entities": {"topic": "audit_help"},
            "summary": "Объяснить результаты последнего аудита BORIS",
            "clarify_question": None, "error": None,
        }

    # Вопросы «что делать дальше / как запустить» — запрос пошагового ведения,
    # а не ошибка парсинга и не случайный переход в один из экранов.
    help_markers = (
        "что делать дальше", "что мне делать дальше", "что надо сделать",
        "что нужно сделать", "что уже настроено", "следующий шаг",
        "как начать работу", "настроить работу", "как настроить",
        "как запустить рекламу", "как запустить авито", "запустить рекламу на авито",
        "создать объявлен", "сделать объявлен", "разместить на авито",
        "запустить работу", "что исправить", "что добавить",
    )
    if any(w in s for w in help_markers):
        return {
            "understood": True, "intent": "chit_chat", "confidence": 1.0,
            "entities": {"topic": "onboarding_help"},
            "summary": "Помочь с дальнейшей настройкой BORIS и запуском работы",
            "clarify_question": None, "error": None,
        }

    # Поиск без имени = показать последние карточки, а не загонять человека в тупик уточнения.
    if any(w in s for w in ("найди клиента", "покажи клиентов", "последние клиенты")):
        q = ""
        m = re.search(r"найди клиента\s+(.+)$", s)
        if m:
            q = m.group(1).strip(" .,!?")
        return {
            "understood": True, "intent": "client_search", "confidence": 1.0,
            "entities": ({"client": q} if q else {}),
            "summary": "Найти клиента" if q else "Показать последних клиентов",
            "clarify_question": None, "error": None,
        }

    # Самая частая голосовая команда: статистика без периода = сегодня.
    analytics_words = ("статистик", "аналитик", "показател", "результат")
    if any(w in s for w in analytics_words):
        period = "week" if any(w in s for w in ("недел", "7 дней", "семь дней")) else (
            "yesterday" if "вчера" in s else "today"
        )
        ent = {"period": period}
        if today and period in ("today", "yesterday"):
            try:
                import datetime as _d
                base = _d.date.fromisoformat(today)
                ent["date"] = (base - _d.timedelta(days=1)).isoformat() if period == "yesterday" else base.isoformat()
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=117)
        return {
            "understood": True, "intent": "analytics_query", "confidence": 1.0,
            "entities": ent,
            "summary": "Показать статистику за выбранный период",
            "clarify_question": None, "error": None,
        }

    return None


def _context_block(page_context=None, entity_type=None, entity_id=None, today=None):
    parts = []
    if today:
        parts.append("Сегодня: %s" % today)
    if page_context:
        section = page_context.get("section") or page_context.get("route")
        if section:
            parts.append("Открыт раздел: %s" % section)
    if entity_type and entity_id:
        parts.append("Открыт объект: %s %s" % (entity_type, entity_id))
    if not parts:
        return ""
    return "\n\nКОНТЕКСТ ЭКРАНА:\n" + "\n".join(parts)


def parse_command(text, account_id=None, page_context=None,
                  entity_type=None, entity_id=None, today=None):
    """Возвращает словарь разбора. Никогда не бросает наружу."""
    fast = _fast_path(text, today=today)
    if fast is not None:
        return fast

    safe = redact_text(text, limit=2000)
    if not safe:
        return {"understood": False, "intent": None, "confidence": 0.0,
                "entities": {}, "summary": "", "clarify_question": None,
                "error": "empty_text"}

    user = safe + _context_block(page_context, entity_type, entity_id, today)
    messages = [
        Messages(role=MessagesRole.SYSTEM, content=COMMAND_PARSER_PROMPT),
        Messages(role=MessagesRole.USER, content=user),
    ]

    raw = ""
    try:
        raw = chat_with_fallback(messages, model=_GC_MODEL,
                                 credentials=GIGACHAT_KEY,
                                 account_id=account_id, operation=OPERATION)
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError:
        return {"understood": False, "intent": None, "confidence": 0.0,
                "entities": {}, "summary": "",
                "clarify_question": "Не разобрал команду. Повторите другими словами?",
                "error": "bad_json", "raw": (raw or "")[:300]}
    except Exception as e:
        return {"understood": False, "intent": None, "confidence": 0.0,
                "entities": {}, "summary": "", "clarify_question": None,
                "error": "parser_failed", "detail": str(e)[:200]}

    intent = data.get("intent")
    if intent not in INTENTS:
        intent = None
    ent = data.get("entities")
    if not isinstance(ent, dict):
        ent = {}
    try:
        conf = float(data.get("confidence") or 0)
    except Exception:
        conf = 0.0

    clarify = data.get("clarify_question")
    if isinstance(clarify, str) and not clarify.strip():
        clarify = None

    return {"understood": bool(data.get("understood")) and intent is not None,
            "intent": intent,
            "confidence": max(0.0, min(conf, 1.0)),
            "entities": ent,
            "summary": (data.get("summary") or "")[:500],
            "clarify_question": clarify,
            "error": None}
