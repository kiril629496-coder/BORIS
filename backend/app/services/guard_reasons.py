# -*- coding: utf-8 -*-
"""Перевод технических причин AI Budget Guard в пользовательские.

Внутри BORIS причина остаётся технической (`reserve_spent`,
`usage_not_ready`, `budget_exhausted`) и пишется в audit — владельцу
платформы нужна точная диагностика.

Наружу уходит ТОЛЬКО результат user_reason(). Неизвестный код НИКОГДА
не возвращается клиенту как есть — иначе первый же новый guard_reason
утечёт наружу сырой строкой.
"""

FALLBACK = "AI-операция временно недоступна"

USER_REASONS = {
    "budget_exhausted": "Лимит операции исчерпан",
    "reserve_spent": "Лимит операции исчерпан",
    "calls_exhausted": "Лимит операции исчерпан",
    "blocked": "Лимит операции исчерпан",
    "overspent": "Лимит операции исчерпан",
    "run_not_found": "Операцию безопасно не удалось запустить",
    "error": "Операцию безопасно не удалось запустить",
    "no_request_id": "Операцию безопасно не удалось запустить",
    "usage_not_ready": "Обработка продолжится позже",
    "expired": "Обработка продолжится позже",
    "stopped": "Массовая операция временно приостановлена",
}


def user_reason(code) -> str:
    """Пользовательская причина. Для неизвестного кода — безопасный текст."""
    if not code:
        return FALLBACK
    return USER_REASONS.get(str(code).strip().lower(), FALLBACK)


def is_known(code) -> bool:
    """Известен ли код. Нужен тестам и аудиту, не клиентскому ответу."""
    return bool(code) and str(code).strip().lower() in USER_REASONS


def safe_dto(result, keep=()):
    """Клиентский DTO из внутреннего ответа guard.

    Всё, что не в белом списке, отбрасывается: reserved_kopeks,
    committed_kopeks, run_id, request_id и прочая внутренняя механика
    наружу не выходят даже случайно.
    """
    if not isinstance(result, dict):
        return {"reason": FALLBACK}
    out = {k: result[k] for k in keep if k in result}
    out["reason"] = user_reason(result.get("reason") or result.get("status"))
    return out
