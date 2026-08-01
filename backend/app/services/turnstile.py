"""
Проверка Cloudflare Turnstile через официальный Siteverify API.

TURNSTILE_SECRET_KEY живёт ТОЛЬКО в окружении и никогда не попадает
ни в ответ клиенту, ни в логи. На фронт уходит только TURNSTILE_SITE_KEY.

Политика при недоступности Cloudflare — fail-closed: нет внятного ответа,
значит проверка не пройдена. Сервер ходит на challenges.cloudflare.com
напрямую, без US-прокси (проверено 31.07: GET отдаёт 405, связь есть).
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SEC = 5


def is_enabled() -> bool:
    """Проверка включена, только если секрет задан в окружении."""
    return bool(os.environ.get("TURNSTILE_SECRET_KEY"))


def verify(token: str, remote_ip: str = "") -> tuple:
    """
    Возвращает (ok, reason).

    reason — короткий машинный код для лога, НЕ для показа пользователю:
      no_secret | empty_token | network | bad_status | <коды Cloudflare>
    """
    secret = os.environ.get("TURNSTILE_SECRET_KEY")
    if not secret:
        logger.warning("turnstile: TURNSTILE_SECRET_KEY не задан, проверка невозможна")
        return False, "no_secret"

    if not token:
        return False, "empty_token"

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        resp = requests.post(SITEVERIFY_URL, data=payload, timeout=TIMEOUT_SEC)
    except Exception as exc:
        # Пишем тип ошибки, но не payload — там секрет.
        logger.warning("turnstile: сеть недоступна (%s)", type(exc).__name__)
        return False, "network"

    if resp.status_code != 200:
        logger.warning("turnstile: siteverify вернул %s", resp.status_code)
        return False, "bad_status"

    try:
        data = resp.json()
    except Exception:
        logger.warning("turnstile: ответ не является JSON")
        return False, "bad_status"

    if data.get("success") is True:
        return True, "ok"

    codes = data.get("error-codes") or []
    logger.warning("turnstile: отказ, коды %s", codes)
    return False, ",".join(str(c) for c in codes) or "failed"
