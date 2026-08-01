"""
Глобальная сериализация запросов к GigaChat.

ПРИЧИНА: раньше был физлицовский доступ (scope=GIGACHAT_API_PERS) - всего 1 поток и
1 запрос в секунду. Несколько задач (разных аккаунтов) могли одновременно
стучаться в GigaChat, из-за чего шлюз Сбера иногда возвращал повреждённый/пустой
ответ вместо честного 429 - падало с "Expecting value: line 1 column 1 (char 0)".

РЕШЕНИЕ: один процесс-level lock + минимальный интервал между запросами (1.1 сек
с запасом). Все обращения к GigaChat в проекте идут ЧЕРЕЗ RateLimitedGigaChat -
тогда сколько бы задач ни запускалось параллельно, к самому GigaChat они пойдут
строго по очереди, одна за другой, без конкуренции.

13.07.2026: откат с корпоративного тарифа (scope=GIGACHAT_API_B2B) обратно на
физлицовский (scope=GIGACHAT_API_PERS) - корпоративный счёт не пополнен, прод сыпал
402 Payment Required на каждый запрос и весь трафик уходил в дорогой OpenAI-фолбэк.
Оплаченный пакет токенов лежит на физлицовском счёте, поэтому дефолт снова PERS
(1 поток/1 RPS - см. _MIN_INTERVAL ниже). Scope настраивается переменной окружения
GIGACHAT_SCOPE, дефолт GIGACHAT_API_PERS - на случай следующего переключения тарифа
не придётся снова править код.
"""

import os
import threading
import time

from gigachat import GigaChat as _RealGigaChat

_gigachat_lock = threading.Lock()
_last_call_ts = [0.0]
_MIN_INTERVAL = 1.1  # секунд между запросами - чуть больше 1 RPS лимита PERS с запасом

_waiting_count = [0]
_waiting_count_lock = threading.Lock()


def get_gigachat_waiting_count():
    """Сколько запросов сейчас ждут своей очереди к GigaChat - для панели Директора."""
    with _waiting_count_lock:
        return _waiting_count[0]


# ПРЕДОХРАНИТЕЛЬ ОТ 402: пока тариф Freemium исчерпан, GigaChat отдаёт 402 на КАЖДЫЙ
# запрос - без предохранителя каждый вызов всё равно сначала берёт общий лок и тратит
# время на заведомо провальную попытку, только потом падает в OpenAI. Как только видим
# 402 - запоминаем на 30 минут и следующие вызовы идут в OpenAI сразу, без похода к GigaChat.
_GIGA_BREAKER_TTL = 30 * 60
_giga_breaker_lock = threading.Lock()
_giga_breaker_tripped_at = [None]


def _giga_breaker_is_open():
    with _giga_breaker_lock:
        ts = _giga_breaker_tripped_at[0]
        if ts is None:
            return False
        if time.time() - ts >= _GIGA_BREAKER_TTL:
            _giga_breaker_tripped_at[0] = None
            return False
        return True


def _giga_breaker_trip():
    with _giga_breaker_lock:
        _giga_breaker_tripped_at[0] = time.time()


def reset_giga_breaker():
    """Сбросить предохранитель 402 - вызывается при старте приложения, чтобы
    старый кеш "GigaChat недоступен" (например, со старым Freemium-ключом)
    не блокировал запросы после перехода на новый B2B-ключ."""
    with _giga_breaker_lock:
        _giga_breaker_tripped_at[0] = None


class RateLimitedGigaChat:
    """Drop-in замена для `from gigachat import GigaChat`. Держит глобальную
    блокировку на всё время работы блока `with GigaChat(...) as client:`."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs
        self._inner_cm = None
        self._inner = None

    def __enter__(self):
        with _waiting_count_lock:
            _waiting_count[0] += 1
        try:
            # ВАЖНО: таймаут на саму блокировку (120с) - без него один зависший
            # запрос к GigaChat мог держать lock НАВСЕГДА, ставя в вечную очередь
            # вообще все остальные запросы приложения (даже логин), что и произошло
            # 09.07.2026 - backend перестал отвечать полностью, пришлось перезапускать
            got_lock = _gigachat_lock.acquire(timeout=120)
        finally:
            with _waiting_count_lock:
                _waiting_count[0] -= 1
        if not got_lock:
            raise TimeoutError("Не удалось получить доступ к GigaChat за 120 секунд - другой запрос завис и держит очередь")
        elapsed = time.time() - _last_call_ts[0]
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._inner_cm = _RealGigaChat(*self._args, **self._kwargs)
        self._inner = self._inner_cm.__enter__()

        # ЗАЩИТА ОТ 429: оборачиваем .chat() ретраем с экспоненциальной паузой.
        # Централизовано здесь - защищает ВСЕ места вызова GigaChat в проекте разом,
        # без необходимости чинить каждую точку вызова по отдельности.
        _original_chat = self._inner.chat

        def _chat_with_retry(*call_args, **call_kwargs):
            from gigachat.exceptions import ResponseError
            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                try:
                    return _original_chat(*call_args, **call_kwargs)
                except ResponseError as e:
                    status = None
                    try:
                        status = e.args[1] if len(e.args) > 1 else None
                    except Exception:
                        pass
                    is_429 = status == 429 or "429" in str(e)
                    if is_429 and attempt < max_attempts:
                        wait_s = 2 ** attempt  # 2, 4, 8 секунд
                        print(f"[gigachat_pool] 429 получен, попытка {attempt}/{max_attempts}, жду {wait_s}с")
                        time.sleep(wait_s)
                        continue
                    raise

        self._inner.chat = _chat_with_retry
        return self._inner

    def __exit__(self, exc_type, exc_val, exc_tb):
        _last_call_ts[0] = time.time()
        try:
            return self._inner_cm.__exit__(exc_type, exc_val, exc_tb)
        finally:
            _gigachat_lock.release()


def _openai_fallback_completion(messages, temperature=None, max_completion_tokens=16000, attempts=3, timeout=150, account_id=None, operation=None):
    """Фолбэк на OpenAI gpt-5.4 через международный прокси (тот же приём, что в plan_items.py:
    новый прокси-порт на каждой попытке, т.к. отдельные порты пула часто дохлые).
    `messages` - список gigachat.models.Messages (роли совпадают с OpenAI: system/user/assistant)."""
    import requests as _requests
    from proxy_pool import get_intl_requests_proxies

    api_key = os.environ.get("OPENAI_API_KEY")
    oa_messages = [{"role": getattr(m.role, "value", m.role), "content": m.content} for m in messages]
    payload = {"model": "gpt-5.4", "messages": oa_messages, "max_completion_tokens": max_completion_tokens}
    if temperature is not None:
        payload["temperature"] = temperature

    last_err = None
    for attempt in range(1, attempts + 1):
        proxies = get_intl_requests_proxies()
        try:
            r = _requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json=payload, proxies=proxies, timeout=timeout,
            )
            r.raise_for_status()
            _d = r.json()
            try:
                from app.usage import log_usage as _log
                _u = _d.get("usage", {}) or {}
                _log(account_id, "openai", "gpt-5.4", operation,
                     _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0))
            except Exception as _ue:
                print("[usage]", str(_ue)[:100], flush=True)
            return _d["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            print(f"[gigachat_pool] OpenAI фолбэк, попытка {attempt}/{attempts} не удалась: {e}", flush=True)
            continue
    raise Exception(f"OpenAI фолбэк недоступен через прокси за {attempts} попыток: {last_err}")


def chat_with_fallback(messages, model=None, temperature=None, max_tokens=None, timeout=60, credentials=None, scope=None, account_id=None, operation=None):
    """Единая точка входа для всех вызовов чат-модели в проекте.

    Сериализованный вызов GigaChat через существующий пул (RateLimitedGigaChat - глобальный
    лок + троттлинг), а при ЛЮБОЙ ошибке (429, обрыв сети, битый JSON от шлюза Сбера и т.п.) -
    автоматический фолбэк на OpenAI gpt-5.4 через международный прокси, чтобы задача не падала
    целиком из-за лимита текущего тарифа (1 поток на PERS).

    `scope` - если не передан явно, берётся из переменной окружения GIGACHAT_SCOPE
    (дефолт GIGACHAT_API_PERS - см. шапку файла).
    `messages` - список gigachat.models.Messages. Возвращает текст ответа (str)."""
    from gigachat.models import Chat

    scope = scope or os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    model = model or os.environ.get("GIGACHAT_MODEL", "GigaChat-Pro")

    if _giga_breaker_is_open():
        print("[gigachat_pool] Предохранитель открыт (недавно был 402) - иду сразу в OpenAI, без попытки GigaChat", flush=True)
        return _openai_fallback_completion(messages, temperature=temperature, max_completion_tokens=max_tokens or 16000, account_id=account_id, operation=operation)

    creds = credentials or os.environ.get("GIGACHAT_KEY")
    chat_kwargs = {"messages": messages}
    if temperature is not None:
        chat_kwargs["temperature"] = temperature
    if max_tokens is not None:
        chat_kwargs["max_tokens"] = max_tokens

    try:
        with RateLimitedGigaChat(credentials=creds, scope=scope, model=model, verify_ssl_certs=False, timeout=timeout) as client:
            response = client.chat(Chat(**chat_kwargs))
            try:
                from app.usage import log_usage as _log
                _u = getattr(response, "usage", None)
                _log(account_id, "gigachat", model, operation,
                     getattr(_u, "prompt_tokens", 0) or 0, getattr(_u, "completion_tokens", 0) or 0)
            except Exception as _ue:
                print("[usage]", str(_ue)[:100], flush=True)
            return response.choices[0].message.content
    except Exception as e:
        if "402" in str(e) or "Payment Required" in str(e):
            _giga_breaker_trip()
        print(f"[gigachat_pool] GigaChat недоступен ({e}), фолбэк на OpenAI gpt-5.4", flush=True)
        return _openai_fallback_completion(messages, temperature=temperature, max_completion_tokens=max_tokens or 16000, account_id=account_id, operation=operation)
