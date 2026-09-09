# -*- coding: utf-8 -*-

"""
BORIS realtime AI emergency guard.

Не заменяет:
    app.usage.log_usage
    ai_budget
    клиентский биллинг

Назначение:
    не позволить прямым realtime OpenAI chat/completions
    бесконтрольно уйти в runaway.

Фактический расход продолжает логироваться старой
бизнес-логикой каждого модуля.
"""

import inspect
import os

from app.services.mass_ai_guard import guarded_post


OPENAI_CHAT_URL = (
    "https://api.openai.com/v1/chat/completions"
)


class OpenAIHTTPError(RuntimeError):
    """Safe status-aware OpenAI failure for reliability/circuit classification."""

    def __init__(self, status_code: int, request_id: str | None = None):
        self.status_code = int(status_code or 0)
        self.request_id = str(request_id or "").strip()[:120]
        if self.status_code in (401, 403):
            kind = "authentication"
        elif self.status_code == 429:
            kind = "rate_limit"
        elif self.status_code >= 500:
            kind = "provider_5xx"
        else:
            kind = "request"
        message = f"openai http {self.status_code} {kind}"
        if self.request_id:
            message += f" request_id={self.request_id}"
        super().__init__(message)


def _checked_openai_response(response, *, account_id=None):
    """Convert provider HTTP failures into reliability-visible exceptions.

    Response bodies are never logged because they may contain user text or
    provider diagnostics.
    """
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 200:
        return response
    request_id = str(
        (getattr(response, "headers", {}) or {}).get("x-request-id") or ""
    ).strip()
    print(
        "OPENAI_TEXT_HTTP_ERROR account=%s status=%s request_id=%s"
        % (str(account_id or "*"), status, request_id or "-"),
        flush=True,
    )
    raise OpenAIHTTPError(status, request_id)


def _nonempty(value):
    if value is None:
        return None

    try:
        s = str(value).strip()
    except Exception:
        return None

    return s or None


def _field(obj, name):
    if obj is None:
        return None

    try:
        if isinstance(obj, dict):
            return _nonempty(
                obj.get(name)
            )

        return _nonempty(
            getattr(
                obj,
                name,
                None,
            )
        )

    except Exception:
        return None


def _infer_account(frame):
    """
    Только доказуемый account_id из локальных переменных.

    Никаких:
      user_id -> account
      username -> account
      owner -> account

    Если доказать нельзя — global guard.
    """

    if frame is None:
        return None

    loc = frame.f_locals or {}

    for key in (
        "account_id",
        "acc_id",
    ):
        v = _nonempty(
            loc.get(key)
        )

        if v:
            return v

    for key in (
        "req",
        "request",
        "body",
        "payload",
        "item",
        "row",
        "data",
    ):
        v = _field(
            loc.get(key),
            "account_id",
        )

        if v:
            return v

    # Переменная account иногда является объектом.
    v = loc.get("account")

    if isinstance(v, str):
        v = _nonempty(v)

        if v:
            return v

    nested = _field(
        v,
        "account_id",
    )

    if nested:
        return nested

    return None


def _caller():
    frame = inspect.currentframe()

    try:
        caller = None

        if (
            frame
            and frame.f_back
            and frame.f_back.f_back
        ):
            caller = frame.f_back.f_back

        if caller is None:
            return (
                None,
                "unknown",
                "unknown",
            )

        account_id = _infer_account(
            caller
        )

        filename = (
            caller.f_code.co_filename
            or "unknown.py"
        )

        module = (
            filename
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
            .replace(".py", "")
        )

        function = (
            caller.f_code.co_name
            or "unknown"
        )

        return (
            account_id,
            module,
            function,
        )

    finally:
        del frame


def _env_rub(name, default):
    raw = os.getenv(
        name,
        str(default),
    )

    try:
        return max(
            int(float(raw)),
            0,
        )
    except Exception:
        return int(default)


def _daily_limit_kopeks(
    account_id,
):
    """
    Высокие аварийные потолки.

    Это НЕ тариф и НЕ рекомендуемый расход.

    account:
        2000 ₽/сутки default

    unknown-account:
        1000 ₽/сутки global default
    """

    if account_id:

        rub = _env_rub(
            "BORIS_REALTIME_AI_DAILY_RUB",
            2000,
        )

    else:

        rub = _env_rub(
            "BORIS_REALTIME_AI_GLOBAL_DAILY_RUB",
            1000,
        )

    return rub * 100


def realtime_guarded_post(
    requests_post,
    url,
    **kwargs
):
    """
    Полностью совместим с requests.post.

    Возвращает исходный Response.
    """

    if url != OPENAI_CHAT_URL:

        return requests_post(
            url,
            **kwargs
        )

    (
        account_id,
        module,
        function,
    ) = _caller()

    # Provider transport is isolated independently from the spend guard.
    # A text-model outage must not consume all MOP/ROP/API workers.
    from app.services.reliability import dependency_call

    def _provider_post(target_url, **target_kwargs):
        response = requests_post(target_url, **target_kwargs)
        return _checked_openai_response(response, account_id=account_id)

    def _reliable_post(target_url, **target_kwargs):
        return dependency_call("openai.text", _provider_post, target_url,
                               threshold=4, cooldown_seconds=90, account_id=account_id,
                               tenant_limit_per_minute=90, **target_kwargs)

    return guarded_post(
        _reliable_post,
        url,
        account_id=account_id,
        module=(
            "realtime_"
            + module
        ),
        operation=(
            "realtime:%s:%s"
            % (
                module,
                function,
            )
        ),
        # Защитный резерв на один text call.
        # После ответа mass guard заменяет его оценкой
        # по фактическому usage.
        est_kopeks=200,
        daily_limit_kopeks=
            _daily_limit_kopeks(
                account_id
            ),
        **kwargs
    )
