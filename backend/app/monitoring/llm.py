# -*- coding: utf-8 -*-
"""
LLM-анализ находки для системы мониторинга BORIS.

Собственный клиент модели НЕ создаётся. Используется существующий пул проекта
(app.gigachat_pool.chat_with_fallback) — вместе с его circuit-breaker,
переключением GigaChat -> OpenAI и учётом расхода в api_usage.

Сигнатура пула определяется НА МЕСТЕ через inspect, а не угадывается: разные
версии проекта могут принимать messages / prompt / system+user. Если ни одна
известная форма не подошла, модуль честно возвращает правило-резюме и пишет
в лог, что именно он увидел, — находка при этом не теряется НИКОГДА.

Проверка на сервере одной командой:
    venv/bin/python3 monitor_runner.py --probe-llm
"""
from __future__ import annotations

import inspect
import json
import os
import logging
import re
from typing import Any

from .rules import INTENT_MARKERS, normalize_text

log = logging.getLogger("monitor.llm")

MAX_TEXT_CHARS = 1200        # длинные простыни модели не нужны
DEFAULT_TIMEOUT = 25

# Учёт расхода. По умолчанию ВЫКЛЮЧЕН: предполагается, что chat_with_fallback
# сам пишет в api_usage. Включать ТОЛЬКО если --probe-llm показал, что вызовы
# фонового воркера в учёт не попадают — иначе получим двойной счёт.
LOG_USAGE = os.getenv("MONITOR_LLM_LOG_USAGE", "0") == "1"
USAGE_ACCOUNT = os.getenv("MONITOR_LLM_ACCOUNT", "boris_monitoring")

SYSTEM_PROMPT = (
    "Ты аналитик отдела продаж. Тебе дают сообщение из публичного чата. "
    "Определи, ищет ли автор исполнителя, и опиши это коротко и без выдумок. "
    "Отвечай ТОЛЬКО JSON-объектом без пояснений и без markdown."
)

USER_TEMPLATE = """Сообщение из источника «{source}»:
---
{text}
---
Сработавшие ключевые слова: {terms}

Верни JSON с полями:
"summary": одно-два предложения о том, что человеку нужно, без домыслов
"intent": одно из buy_service, ask_advice, offer_service, discussion, job_post, other
"urgency": high, medium или low
"city": город, ТОЛЬКО если он назван в тексте, иначе null
"service": какая услуга нужна, коротко, иначе null
"reasons": список из 2-4 коротких причин, почему это может быть клиент
"reply": вежливый ответ от лица подрядчика, 2-3 предложения, без цен и обещаний

Ничего не придумывай. Если чего-то нет в тексте — ставь null."""


# ------------------------------------------------------- подключение к пулу

_STRATEGY: str | None = None
_FN: Any = None
_LAST_IO: dict[str, Any] = {"prompt": 0, "answer": 0, "pool_logs_usage": False}


def _import_pool():
    """
    gigachat_pool лежит в КОРНЕ бэкенда (проверено на сервере), но на случай
    переезда пробуем и пакет app.
    """
    try:
        import gigachat_pool  # noqa: PLC0415
        return gigachat_pool
    except ImportError:
        from app import gigachat_pool  # noqa: PLC0415
        return gigachat_pool


def _build_messages(system: str, user: str, merged: bool = False) -> list:
    """
    Пул принимает список объектов Messages из SDK GigaChat.
    Если SDK недоступен, отдаём словари — так адаптер переживёт смену пула.
    """
    try:
        from gigachat.models import Messages, MessagesRole  # noqa: PLC0415
        if merged:
            return [Messages(role=MessagesRole.USER, content=f"{system}\n\n{user}")]
        return [Messages(role=MessagesRole.SYSTEM, content=system),
                Messages(role=MessagesRole.USER, content=user)]
    except Exception:  # noqa: BLE001
        if merged:
            return [{"role": "user", "content": f"{system}\n\n{user}"}]
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]


def _resolve() -> tuple[Any, str] | tuple[None, str]:
    """Находит функцию пула и определяет, в какой форме её звать."""
    global _FN, _STRATEGY
    if _FN is not None and _STRATEGY:
        return _FN, _STRATEGY
    try:
        gigachat_pool = _import_pool()
    except Exception as e:  # noqa: BLE001
        return None, f"пул недоступен: {type(e).__name__}: {e}"

    fn = getattr(gigachat_pool, "chat_with_fallback", None)
    if fn is None:
        return None, "в gigachat_pool нет chat_with_fallback"

    try:
        params = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = []

    if params and params[0] not in ("prompt", "system", "text"):
        strategy = "messages"     # первый позиционный аргумент — список Messages
    elif "messages" in params:
        strategy = "messages"
    elif "system" in params and "user" in params:
        strategy = "system_user"
    elif "prompt" in params:
        strategy = "prompt"
    elif params:
        strategy = "positional"
    else:
        return None, "не удалось разобрать сигнатуру chat_with_fallback"

    _FN, _STRATEGY = fn, strategy
    log.info("LLM-пул подключён, форма вызова: %s (параметры: %s)", strategy, params)
    return fn, strategy


def _find_usage_logger() -> tuple[Any, list[str]] | tuple[None, list[str]]:
    """
    Ищет функцию учёта расхода в проекте, не угадывая её расположение.
    Возвращает (функция, имена параметров).
    """
    for mod_name in ("app.usage", "app.api.usage", "usage"):
        try:
            mod = __import__(mod_name, fromlist=["*"])
        except Exception:  # noqa: BLE001
            continue
        for fn_name in ("log_usage", "record_usage", "add_usage"):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                try:
                    return fn, list(inspect.signature(fn).parameters)
                except (TypeError, ValueError):
                    return fn, []
    return None, []


def _log_usage(model: str, prompt_chars: int, answer_chars: int) -> None:
    """Пишет расход САМ — только если LOG_USAGE=1. Ошибка учёта не роняет анализ."""
    if not LOG_USAGE:
        return
    fn, params = _find_usage_logger()
    if fn is None:
        log.warning("MONITOR_LLM_LOG_USAGE=1, но функция учёта не найдена")
        return
    candidate = {
        "account_id": USAGE_ACCOUNT, "model": model or "unknown",
        "operation": "monitoring_analyze", "kind": "monitoring_analyze",
        "prompt_tokens": max(1, prompt_chars // 4),
        "completion_tokens": max(1, answer_chars // 4),
        "tokens_in": max(1, prompt_chars // 4),
        "tokens_out": max(1, answer_chars // 4),
    }
    kwargs = {k: v for k, v in candidate.items() if not params or k in params}
    try:
        fn(**kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("учёт расхода не записан: %s", e)


def describe_pool() -> dict[str, Any]:
    """Для --probe-llm: что именно модуль увидел в проекте."""
    try:
        gigachat_pool = _import_pool()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    fn = getattr(gigachat_pool, "chat_with_fallback", None)
    if fn is None:
        return {"available": False,
                "error": "нет chat_with_fallback",
                "candidates": [n for n in dir(gigachat_pool)
                               if "chat" in n.lower() or "complete" in n.lower()]}
    try:
        sig = str(inspect.signature(fn))
    except (TypeError, ValueError):
        sig = "(не определяется)"
    _, strategy = _resolve()
    usage_fn, usage_params = _find_usage_logger()
    try:
        pool_params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        pool_params = set()
    pool_logs_usage = {"account_id", "operation"} <= pool_params
    return {
        "available": True,
        "signature": f"chat_with_fallback{sig}",
        "strategy": strategy,
        "module": getattr(gigachat_pool, "__name__", "?"),
        "module_file": getattr(gigachat_pool, "__file__", "?"),
        "usage_logger": (f"{usage_fn.__module__}.{usage_fn.__name__}{tuple(usage_params)}"
                         if usage_fn else None),
        "pool_logs_usage_itself": pool_logs_usage,
        "self_logging_enabled": LOG_USAGE,
    }


def _extract_text(resp: Any) -> str:
    """Пул может вернуть строку, словарь или объект — разбираем все три случая."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for k in ("content", "text", "answer", "result", "message"):
            v = resp.get(k)
            if isinstance(v, str) and v.strip():
                return v
        ch = resp.get("choices")
        if isinstance(ch, list) and ch:
            m = ch[0].get("message") if isinstance(ch[0], dict) else None
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                return m["content"]
        return ""
    for attr in ("content", "text"):
        v = getattr(resp, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    return str(resp)


def call_llm(user_prompt: str, system_prompt: str = SYSTEM_PROMPT) -> str:
    fn, strategy = _resolve()
    if fn is None:
        raise RuntimeError(strategy)

    global _LAST_IO
    if strategy == "messages":
        extra = {}
        pool_logs_usage = False
        try:
            allowed = set(inspect.signature(fn).parameters)
            for k, v in (("temperature", 0.2), ("max_tokens", 700),
                         ("account_id", USAGE_ACCOUNT),
                         ("operation", "monitoring_analyze")):
                if k in allowed:
                    extra[k] = v
            # если пул принимает и account_id, и operation — он логирует расход сам
            pool_logs_usage = {"account_id", "operation"} <= allowed
        except (TypeError, ValueError):
            pass
        _LAST_IO["pool_logs_usage"] = pool_logs_usage
        try:
            return _extract_text(fn(_build_messages(system_prompt, user_prompt), **extra))
        except Exception:  # noqa: BLE001
            # часть версий SDK не принимает системную роль — сливаем в одно сообщение
            return _extract_text(fn(_build_messages(system_prompt, user_prompt, merged=True), **extra))
    if strategy == "system_user":
        return _extract_text(fn(system=system_prompt, user=user_prompt))
    if strategy == "prompt":
        return _extract_text(fn(prompt=f"{system_prompt}\n\n{user_prompt}"))
    return _extract_text(fn(f"{system_prompt}\n\n{user_prompt}"))


# --------------------------------------------------------------- разбор JSON

def _parse_json(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


# ------------------------------------------------------- запасной вариант

def rule_based(text: str, terms: list[str], breakdown: dict[str, int],
               bucket_label: str) -> dict[str, Any]:
    """
    Резюме без модели. Работает всегда: если пул недоступен, лид уходит
    менеджеру с понятным описанием, а не пропадает.
    """
    low = normalize_text(text)
    asking = any(m in low for m in INTENT_MARKERS)
    reasons = []
    if asking:
        reasons.append("прямо ищет исполнителя")
    if breakdown.get("keyword"):
        reasons.append("совпало: " + ", ".join(terms[:3]))
    if breakdown.get("service"):
        reasons.append("названа услуга")
    if breakdown.get("freshness", 0) >= 10:
        reasons.append("сообщение свежее")
    if breakdown.get("contact"):
        reasons.append("зовёт писать в личные сообщения")

    head = text.strip().replace("\n", " ")
    if len(head) > 160:
        head = head[:157] + "…"
    return {
        "summary": head,
        "intent": "buy_service" if asking else "discussion",
        "urgency": "high" if breakdown.get("freshness", 0) >= 15 and asking else "medium",
        "city": None, "service": None,
        "reasons": reasons or [bucket_label],
        "reply": None,
        "source": "rules",
    }


# ------------------------------------------------------------- точка входа

def analyze(text: str, terms: list[str], breakdown: dict[str, int],
            bucket_label: str, source_title: str = "") -> dict[str, Any]:
    """
    Возвращает словарь для monitor_messages.ai + summary + suggested_reply.
    Исключение наружу не выпускает НИКОГДА: ошибка модели не должна съесть лид.
    """
    snippet = (text or "")[:MAX_TEXT_CHARS]
    try:
        raw = call_llm(USER_TEMPLATE.format(
            source=source_title or "неизвестен",
            text=snippet,
            terms=", ".join(terms) or "—",
        ))
        data = _parse_json(raw)
        if not data.get("summary"):
            raise ValueError("модель не вернула summary")
        # само-учёт ТОЛЬКО если пул не залогировал сам (иначе двойной счёт)
        if not _LAST_IO.get("pool_logs_usage"):
            _log_usage(os.getenv("MONITOR_LLM_MODEL", "gigachat"),
                       len(snippet) + len(SYSTEM_PROMPT), len(raw or ""))
    except Exception as e:  # noqa: BLE001
        log.warning("LLM-анализ не удался (%s) — беру правило-резюме", e)
        return rule_based(text, terms, breakdown, bucket_label)

    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {
        "summary": str(data.get("summary"))[:600],
        "intent": str(data.get("intent") or "other")[:32],
        "urgency": str(data.get("urgency") or "medium")[:8],
        "city": (str(data["city"])[:64] if data.get("city") else None),
        "service": (str(data["service"])[:120] if data.get("service") else None),
        "reasons": [str(r)[:120] for r in reasons][:4],
        "reply": (str(data["reply"])[:1200] if data.get("reply") else None),
        "source": "llm",
    }
