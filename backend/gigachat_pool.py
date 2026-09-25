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
from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
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


# Cluster-wide GigaChat circuit. PostgreSQL is authoritative so API-1, API-2 and
# the background worker observe the same outage state. Legacy helper names are
# preserved because startup/older modules import reset_giga_breaker().
_GIGA_BREAKER_TTL = 30 * 60
_OUTAGE_TTL = 30 * 60
_OUTAGE_THRESHOLD = 3

def _persistent_giga_allows():
    from app.db.session import SessionLocal
    from app.services.reliability import circuit_allows
    db=SessionLocal()
    try:
        ok=circuit_allows(db, "gigachat.chat"); db.commit(); return ok
    finally: db.close()

def _persistent_giga_success():
    from app.db.session import SessionLocal
    from app.services.reliability import circuit_success
    db=SessionLocal()
    try: circuit_success(db, "gigachat.chat"); db.commit()
    finally: db.close()

def _persistent_giga_failure(error, threshold=_OUTAGE_THRESHOLD, cooldown=_OUTAGE_TTL):
    from app.db.session import SessionLocal
    from app.services.reliability import circuit_failure
    db=SessionLocal()
    try:
        state=circuit_failure(db, "gigachat.chat", str(error), threshold=threshold, cooldown_seconds=cooldown); db.commit(); return state
    finally: db.close()

def _giga_breaker_is_open(): return not _persistent_giga_allows()
def _outage_should_skip(): return not _persistent_giga_allows()
def _outage_note_success(): _persistent_giga_success()
def _outage_note_5xx(): _persistent_giga_failure("gigachat 5xx", _OUTAGE_THRESHOLD, _OUTAGE_TTL)
def _outage_abandon_probe(): _persistent_giga_failure("gigachat transport failure", _OUTAGE_THRESHOLD, _OUTAGE_TTL)
def _giga_breaker_trip(): _persistent_giga_failure("402 Payment Required", 1, _GIGA_BREAKER_TTL)

def reset_giga_breaker():
    # Compatibility only. Do NOT clear persistent outage state at process startup:
    # restarting one API replica must never erase a cluster-wide provider circuit.
    return None


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


def _openai_text_burst_guard(account_id):
    """Serialize/scoped emergency OpenAI text fallback and stop short-window runaway."""
    from contextlib import contextmanager
    @contextmanager
    def _guard():
        if not str(account_id or "").strip():
            yield {"allowed": True, "scoped": False}
            return
        import hashlib, datetime
        from sqlalchemy import text as _sql_text
        from app.db.session import SessionLocal
        try:
            cap=max(1,min(200,int(os.environ.get("BORIS_OPENAI_TEXT_BURST_CAP","20") or 20)))
        except Exception:
            cap=20
        try:
            window=max(1,min(60,int(os.environ.get("BORIS_OPENAI_TEXT_BURST_WINDOW_MIN","10") or 10)))
        except Exception:
            window=10
        try:
            daily=max(cap,min(1000,int(os.environ.get("BORIS_OPENAI_TEXT_DAILY_CAP","100") or 100)))
        except Exception:
            daily=100
        raw=int.from_bytes(hashlib.blake2b(("openai-text-burst|"+str(account_id)).encode("utf-8"),digest_size=8).digest(),"big",signed=False)
        key=raw if raw < (1<<63) else raw-(1<<64)
        db=SessionLocal()
        try:
            db.execute(_sql_text("select pg_advisory_lock(:k)"),{"k":key})
            now=datetime.datetime.now(datetime.timezone.utc)
            cutoff=now-datetime.timedelta(minutes=window)
            day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
            row=db.execute(_sql_text("select count(*),coalesce(sum(cost_rub),0) from api_usage where account_id=:a and provider='openai' and model='gpt-5.4' and created_at>=:c"),{"a":str(account_id),"c":cutoff}).fetchone()
            day_row=db.execute(_sql_text("select count(*),coalesce(sum(cost_rub),0) from api_usage where account_id=:a and provider='openai' and model='gpt-5.4' and created_at>=:d"),{"a":str(account_id),"d":day_start}).fetchone()
            used=int((row[0] if row else 0) or 0); spent=float((row[1] if row else 0) or 0)
            day_used=int((day_row[0] if day_row else 0) or 0); day_spent=float((day_row[1] if day_row else 0) or 0)
            code = "OPENAI_TEXT_DAILY_CAP" if day_used >= daily else ("OPENAI_TEXT_BURST_CAP" if used >= cap else None)
            yield {"allowed":code is None,"code":code,"used":used,"cap":cap,"window_min":window,"spent_rub":round(spent,2),
                   "daily_used":day_used,"daily_cap":daily,"daily_spent_rub":round(day_spent,2),"scoped":True}
        finally:
            try: db.execute(_sql_text("select pg_advisory_unlock(:k)"),{"k":key})
            except Exception: pass
            db.close()
    return _guard()


def _openai_fallback_completion(messages, temperature=None, max_completion_tokens=16000, attempts=1, timeout=150, account_id=None, operation=None, request_id=None, financial_guard=False, paid_provenance=None):
    """One safe OpenAI gpt-5.4 fallback attempt per BORIS business intent."""
    import uuid
    import requests as _requests
    from proxy_pool import get_intl_requests_proxies
    from app.services.reliability import dependency_call, ProviderDeferred, CircuitOpen
    from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard

    api_key = os.environ.get("OPENAI_API_KEY")
    oa_messages = [{"role": getattr(m.role, "value", m.role), "content": m.content} for m in messages]
    oa_messages, privacy_vault = safe_external_ai_payload(oa_messages)
    payload = {"model": "gpt-5.4", "messages": oa_messages, "max_completion_tokens": max_completion_tokens}
    if temperature is not None:
        payload["temperature"] = temperature

    # Paid fallback is deliberately single-attempt. The account-scoped guard is
    # held across provider I/O + usage persistence so concurrent workers cannot
    # all observe the same free burst slot. Campaign/Feed Factory additionally
    # enables ai_budget, while legacy callers preserve their existing policy.
    attempts = 1
    budget_run_id = None
    if financial_guard:
        from app.services import ai_budget as _ai_budget
        # A stable idempotency key must map to a stable financial run. If the
        # provider accepted the request but the socket died before BORIS got a
        # response, the next identical business intent will see the existing
        # released/exhausted run and will NOT silently buy the same text again.
        if request_id:
            _intent_digest = __import__("hashlib").sha256(
                (str(account_id or "unknown") + "|" + str(request_id)).encode("utf-8")
            ).hexdigest()[:40]
            budget_run_id = "campaign-text-intent:" + _intent_digest
        else:
            budget_run_id = "campaign-text:" + str(account_id or "unknown") + ":" + uuid.uuid4().hex
        _reserve = _ai_budget.reserve(
            account_id=account_id, module="campaign_text", operation=operation or "campaign_text",
            run_id=budget_run_id, est_kopeks=max(100, int(os.environ.get("BORIS_CAMPAIGN_TEXT_EST_KOPEKS", "2000") or 2000)), calls_planned=1)
        _status = str((_reserve or {}).get("status") or "") if isinstance(_reserve, dict) else ""
        if _status not in {"reserved", "observe", "exists"}:
            _audit_ai_guard(account_id=account_id, source="openai_text", operation=operation,
                            event_code="CAMPAIGN_TEXT_BUDGET_RESERVE_FAILED", status="blocked",
                            intent_key=str(request_id or budget_run_id or "")[:160], details={"status": _status})
            raise RuntimeError("CAMPAIGN_TEXT_BUDGET_RESERVE_FAILED:" + _status)
        _allow = _ai_budget.allow_next(budget_run_id)
        if not bool((_allow or {}).get("allowed")):
            _ai_budget.release(budget_run_id)
            _audit_ai_guard(account_id=account_id, source="openai_text", operation=operation,
                            event_code="CAMPAIGN_TEXT_BUDGET_NOT_ALLOWED", status="blocked",
                            intent_key=str(request_id or budget_run_id or "")[:160],
                            details={"reason": str((_allow or {}).get("reason") or "")})
            raise RuntimeError("CAMPAIGN_TEXT_BUDGET_NOT_ALLOWED:" + str((_allow or {}).get("reason") or ""))
    with _openai_text_burst_guard(account_id) as burst:
        if not burst.get("allowed"):
            if budget_run_id:
                _ai_budget.release(budget_run_id)
            _code = str(burst.get("code") or "OPENAI_TEXT_BURST_CAP")
            _audit_ai_guard(account_id=account_id, source="openai_text", operation=operation,
                            event_code=_code, status="blocked",
                            intent_key=str(request_id or budget_run_id or "")[:160], details=burst)
            if _code == "OPENAI_TEXT_DAILY_CAP":
                raise RuntimeError("OPENAI_TEXT_DAILY_CAP:%s/%s" % (burst.get("daily_used"), burst.get("daily_cap")))
            raise RuntimeError("OPENAI_TEXT_BURST_CAP:%s/%s" % (burst.get("used"), burst.get("cap")))
        try:
            # OpenAI text must use the known-good service egress first.  The
            # legacy PROXY_INTL_* pool is also used by Pexels/Avito helpers and
            # can contain stale/rotating credentials; passing it here caused
            # valid Campaign text intents to fail with HTTP 407 even while the
            # service-level HTTPS_PROXY could reach api.openai.com.
            _openai_proxy = (os.environ.get("BORIS_OPENAI_PROXY") or
                             os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or
                             os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
            proxies = ({"http": _openai_proxy, "https": _openai_proxy}
                       if _openai_proxy else get_intl_requests_proxies())
            r = dependency_call(
                "openai.text", _requests.post,
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                         **({"Idempotency-Key": str(request_id)} if request_id else {})},
                json=payload, proxies=proxies, timeout=timeout,
                account_id=account_id, tenant_limit_per_minute=90,
            )
            r.raise_for_status()
            data = r.json()
            provider_request_id = str(r.headers.get("x-request-id") or "").strip()
            # A successful response without x-request-id is financially ambiguous.
            # Keep the spend, mark it explicitly, and never issue a hidden retry.
            accounting_request_id = str(request_id or provider_request_id or ("ambiguous-text:" + str(account_id or "unknown") + ":" + uuid.uuid4().hex)).strip()
            from app.usage import log_usage as _log
            usage = data.get("usage", {}) or {}
            try:
                _log(account_id, "openai", "gpt-5.4", operation,
                     usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                     request_id=accounting_request_id, usage_details={**(usage if isinstance(usage,dict) else {}),
                         "boris_intent_key": str(request_id or accounting_request_id),
                         "boris_provenance": {str(k)[:64]: v for k,v in (paid_provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
            except Exception as first_log_error:
                # DB accounting retry is free and must not re-call the provider.
                try:
                    _log(account_id, "openai", "gpt-5.4", operation,
                         usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                         request_id=accounting_request_id, usage_details={**(usage if isinstance(usage,dict) else {}),
                         "boris_intent_key": str(request_id or accounting_request_id),
                         "boris_provenance": {str(k)[:64]: v for k,v in (paid_provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
                except Exception as second_log_error:
                    print("[usage][critical] OpenAI text already paid but api_usage persistence failed: %s / %s" %
                          (str(first_log_error)[:120], str(second_log_error)[:120]), flush=True)
            if budget_run_id:
                _commit = _ai_budget.commit(budget_run_id, accounting_request_id)
                if str((_commit or {}).get("status") or "") not in {"committed", "already_committed"}:
                    # Provider success is irreversible. Preserve the paid output so
                    # outer idempotency can cache it and prevent a second purchase;
                    # api_usage/request_id remains the reconciliation source of truth.
                    print("[ai_budget][reconcile] campaign text paid success commit pending: %s request_id=%s" %
                          (str((_commit or {}).get("status") or "unknown"), accounting_request_id), flush=True)
            return str(restore_external_ai_output(data["choices"][0]["message"]["content"], privacy_vault))
        except (ProviderDeferred, CircuitOpen):
            raise
        except Exception as e:
            _audit_ai_guard(account_id=account_id, source="openai_text", operation=operation,
                            event_code="OPENAI_TEXT_PROVIDER_FAILED", status="failed",
                            intent_key=str(request_id or budget_run_id or "")[:160],
                            details={"reason": type(e).__name__})
            print("[gigachat_pool] OpenAI fallback single attempt failed: %s" % str(e)[:180], flush=True)
            raise Exception("OpenAI fallback unavailable after one safe attempt: %s" % str(e)[:180])
        finally:
            if budget_run_id:
                try: _ai_budget.release(budget_run_id)
                except Exception: pass


def chat_with_fallback(messages, model=None, temperature=None, max_tokens=None, timeout=60, credentials=None, scope=None, account_id=None, operation=None, single_paid_attempt=False, paid_request_id=None, paid_provenance=None):
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

    # Feed Factory paid copy is an economic intent, not an availability probe.
    # In this scoped mode do exactly one provider request: no GigaChat retries,
    # no GigaChat->OpenAI cascade. Other BORIS callers keep legacy fallback.
    if single_paid_attempt:
        return _openai_fallback_completion(
            messages, temperature=temperature, max_completion_tokens=max_tokens or 16000,
            timeout=max(timeout, 150), account_id=account_id, operation=operation,
            request_id=paid_request_id, financial_guard=True, paid_provenance=paid_provenance)

    if _giga_breaker_is_open():
        print("[gigachat_pool] Предохранитель открыт (недавно был 402) - иду сразу в OpenAI, без попытки GigaChat", flush=True)
        return _openai_fallback_completion(messages, temperature=temperature, max_completion_tokens=max_tokens or 16000, account_id=account_id, operation=operation)

    if _outage_should_skip():
        return _openai_fallback_completion(messages, temperature=temperature,
                                           max_completion_tokens=max_tokens or 16000,
                                           account_id=account_id, operation=operation)

    creds = credentials or os.environ.get("GIGACHAT_KEY")

    # GIGACHAT_EXTERNAL_PII_BOUNDARY_V1:
    # GigaChat is also an external AI provider. Keep one reversible vault for
    # the whole conversation so the same client value gets the same token in
    # history, send only tokenized text, then restore the answer locally.
    safe_messages = []
    giga_privacy_vault = None
    from gigachat.models import Messages
    for message in messages:
        safe_content, giga_privacy_vault = safe_external_ai_payload(
            str(getattr(message, "content", "") or ""),
            giga_privacy_vault,
        )
        if hasattr(message, "model_copy"):
            safe_message = message.model_copy(update={"content": safe_content})
        else:
            safe_message = Messages(role=getattr(message, "role", None), content=safe_content)
        safe_messages.append(safe_message)

    chat_kwargs = {"messages": safe_messages}
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
            _outage_note_success()
            return str(restore_external_ai_output(
                response.choices[0].message.content,
                giga_privacy_vault,
            ))
    except Exception as e:
        if "402" in str(e) or "Payment Required" in str(e):
            _giga_breaker_trip()
        elif _is_5xx(e):
            _outage_note_5xx()
        else:
            _outage_abandon_probe()
        print(f"[gigachat_pool] GigaChat недоступен ({e}), фолбэк на OpenAI gpt-5.4", flush=True)
        return _openai_fallback_completion(messages, temperature=temperature, max_completion_tokens=max_tokens or 16000, account_id=account_id, operation=operation)
