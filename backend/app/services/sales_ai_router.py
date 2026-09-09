# -*- coding: utf-8 -*-
"""BORIS sales text AI router.

One provider-selection layer for MOP / ROP / training.

Policy:
    OpenAI (when usable and budget allows)
        -> DeepSeek remote fallback
        -> Gemini CLI free fallback
        -> local Ollama/Qwen fallback

The router NEVER sends a client message and NEVER mutates CRM/Avito business
state. Callers keep their own business-intent locks/caches and delivery logic.

OpenAI provider/billing failures degrade to a free provider.  OpenAI recovery is
automatic through the existing reliability circuit: while OPEN it is skipped;
after opened_until expires exactly one dependency_call gets the half-open lease.
A successful probe closes the circuit, therefore the next requests use OpenAI
again without an owner switch.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.db.session import SessionLocal
from app.models.reliability import ReliabilityCircuit
from app.services.reliability import CircuitOpen, ProviderDeferred, dependency_call


class SalesAIUnavailable(RuntimeError):
    def __init__(self, message: str, *, chain: list[dict] | None = None):
        super().__init__(message)
        self.chain = list(chain or [])


class SalesAIProviderUnavailable(SalesAIUnavailable):
    pass


class SalesAIBudgetBlocked(SalesAIUnavailable):
    pass


class SalesAIValidationError(RuntimeError):
    """Caller/request contract error. Do not hide behind provider fallback."""
    pass


class SalesAIOutputInvalid(SalesAIUnavailable):
    """Provider answered, but the model output cannot satisfy the requested contract."""
    pass


@contextmanager
def _local_inference_guard(operation: str):
    """Serialize local MOP inference across live replies and training.

    SALES_AI_LOCAL_INFERENCE_LOCK_V1: local Ollama is a shared CPU/RAM resource.
    A second MOP inference must fail fast into normal provider recovery instead
    of running concurrently and starving production/client messaging.
    """
    if not str(operation or "").startswith("mop_"):
        yield
        return

    lock_path = str(
        os.getenv("BORIS_SALES_LOCAL_LOCK_PATH")
        or "/tmp/boris_sales_ai_local.lock"
    )
    try:
        lock_file = open(lock_path, "a+")
    except PermissionError:
        # The shared advisory-lock file may have been created by an operator/
        # deploy user with read-only permissions for the runtime UID. flock(2)
        # does not require a writable descriptor, so reuse the existing file
        # read-only instead of disabling the local AI fallback.
        lock_file = open(lock_path, "r")
    acquired = False
    try:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            acquired = True
        except BlockingIOError as exc:
            raise SalesAIProviderUnavailable(
                "local MOP inference busy; retry via provider recovery"
            ) from exc
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        lock_file.close()


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _circuit_snapshot(dependency: str, account_id: str | None = None) -> dict:
    """Read-only circuit snapshot. No half-open lease is acquired here."""
    keys = [str(dependency)]
    if account_id:
        try:
            from app.services.reliability import _account_circuit_key
            keys.append(_account_circuit_key(str(dependency), str(account_id)))
        except Exception:
            pass

    db = SessionLocal()
    try:
        rows = db.query(ReliabilityCircuit).filter(
            ReliabilityCircuit.dependency.in_(keys)
        ).all()
        out = {}
        for row in rows:
            out[row.dependency] = {
                "state": str(row.state or "closed"),
                "opened_until": row.opened_until,
                "failures": int(row.consecutive_failures or 0),
            }
        return out
    finally:
        db.close()


def _circuit_blocks(dependency: str, account_id: str | None = None) -> bool:
    now = _now_utc()
    for row in _circuit_snapshot(dependency, account_id).values():
        state = str(row.get("state") or "closed")
        opened_until = row.get("opened_until")
        if state not in {"open", "half_open"}:
            continue
        # Expired OPEN is intentionally a candidate: dependency_call will grant
        # exactly one half-open probe lease.
        if opened_until is not None:
            try:
                if opened_until.tzinfo is None:
                    opened_until = opened_until.replace(tzinfo=timezone.utc)
                if opened_until <= now:
                    continue
            except Exception:
                pass
        return True
    return False


def _gemini_daily_quota_blocks() -> bool:
    """Fail closed on a proven daily Gemini quota until a real probe succeeds.

    GEMINI_DAILY_RESET_PROBE_LATCH_V1:
    Time reaching reset_epoch only permits the canonical provider watcher to
    probe Gemini. It does *not* make live MOP/ROP traffic the health probe.
    The watcher changes ext_ai_providers.state to AVAILABLE only after a real
    successful CLI call; until then sales traffic stays on the next safe
    provider (normally local Ollama).
    """
    db = SessionLocal()
    try:
        from sqlalchemy import text as _sql_text
        from app.ext_api.aiprov import (
            GEMINI_DAILY_QUOTA_SIGNS,
            gemini_daily_quota_reset_epoch,
        )
        row = db.execute(
            _sql_text(
                "SELECT state,note FROM ext_ai_providers "
                "WHERE name='gemini_cli' LIMIT 1"
            )
        ).mappings().first()
        if not row:
            return False
        state = str(row.get("state") or "")
        note = str(row.get("note") or "")
        note_low = note.lower()
        daily_marked = any(sign in note_low for sign in GEMINI_DAILY_QUOTA_SIGNS)
        if state == "RATE_LIMITED" and daily_marked:
            # Before reset: ordinary durable quota fence.
            reset = gemini_daily_quota_reset_epoch(note)
            if reset and float(reset) > time.time():
                return True
            # At/after reset: keep live customer traffic fenced until the
            # canonical watcher proves recovery and writes AVAILABLE.
            return True
        return False
    except Exception:
        # Only durable, explicit quota evidence may suppress Gemini.
        return False
    finally:
        db.close()


def _deepseek_provider_blocked() -> bool:
    """Read-only durable provider latch; no network call."""
    try:
        from app.ext_api import aiprov as _aiprov
        row = _aiprov.state("deepseek") or {}
        return bool(row) and not bool(row.get("usable", True))
    except Exception:
        # Missing provider-state evidence must not invent an outage.
        return False


def provider_order(account_id: str | None = None) -> list[str]:
    order: list[str] = []
    if (
        _truthy("BORIS_SALES_OPENAI_ENABLED", True)
        and str(os.getenv("OPENAI_API_KEY") or "").strip()
        and not _openai_billing_blocked()
        and not _circuit_blocks("openai.text", account_id)
    ):
        order.append("openai")

    if (
        _truthy("BORIS_SALES_DEEPSEEK_ENABLED", False)
        and str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
        and not _deepseek_provider_blocked()
        and not _circuit_blocks("deepseek.sales", account_id)
    ):
        order.append("deepseek")

    if (
        _truthy("BORIS_SALES_GEMINI_ENABLED", True)
        and not _gemini_daily_quota_blocks()
        and not _circuit_blocks("gemini.sales", account_id)
    ):
        order.append("gemini")

    # SALES_ROUTER_NO_GIGACHAT_FALLBACK_V1:
    # Sales/MOP/ROP policy is intentionally limited to:
    # paid OpenAI primary -> DeepSeek remote -> free Gemini CLI -> local Ollama/Qwen.
    # GigaChat credentials must never silently change this policy.
    if _truthy("BORIS_SALES_LOCAL_ENABLED", True):
        order.append("ollama")

    return order


def _response_output_text(body: dict) -> str:
    # Responses API convenience field is not guaranteed in raw HTTP JSON.
    direct = str(body.get("output_text") or "").strip()
    if direct:
        return direct
    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                value = part.get("text")
                if isinstance(value, dict):
                    value = value.get("value")
                if value:
                    chunks.append(str(value))
    return "\n".join(chunks).strip()


def _json_value(text_value: str) -> Any:
    raw = str(text_value or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "", 1).replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception as exc:
        raise SalesAIOutputInvalid("provider returned invalid JSON") from exc


def _provider_error_kind(status: int, code: str = "") -> str:
    code = str(code or "").lower()
    if status in (401, 403):
        return "authentication"
    # OPENAI_BILLING_BEFORE_429_V1:
    # credit_balance_exhausted is returned with HTTP 429, but it is not a
    # seconds/minutes rate window. Classify durable billing evidence before the
    # generic 429 branch so BORIS does not hammer an empty paid balance every
    # short circuit cooldown.
    if status == 402 or "billing" in code or "credit" in code:
        return "billing"
    if status == 429 or "quota" in code or "rate" in code:
        return "quota"
    if status >= 500:
        return "provider_5xx"
    return "request"


def _openai_billing_blocked() -> bool:
    """Read-only durable billing latch shared with the external provider state."""
    try:
        from app.ext_api import aiprov
        row = aiprov.state("openai") or {}
        return (
            row.get("state") == aiprov.UNAVAILABLE_BILLING
            and bool(row.get("cooling"))
        )
    except Exception:
        # A broken state reader must not silently disable the primary provider.
        return False


def _openai_default_call(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    model: str,
    max_output_tokens: int,
    idempotency_key: str,
    module: str,
    timeout: int,
) -> dict:
    import requests
    from app.services import ai_budget
    from app.usage import log_usage

    key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise SalesAIProviderUnavailable("openai key missing")

    intent = str(idempotency_key or "").strip()
    if not intent:
        intent = hashlib.sha256(
            (str(account_id) + "\n" + str(operation) + "\n" + prompt).encode("utf-8")
        ).hexdigest()

    run_id = "sales-ai:%s:%s" % (
        str(module or "sales"),
        hashlib.sha256((str(account_id) + ":" + intent).encode("utf-8")).hexdigest(),
    )
    estimate = max(1, int(os.getenv("BORIS_SALES_OPENAI_EST_KOPEKS", "300") or 300))
    token = ai_budget.reserve(
        account_id=str(account_id),
        module=str(module or "sales"),
        operation=str(operation),
        run_id=run_id,
        est_kopeks=estimate,
        calls_planned=1,
    )
    state = str((token or {}).get("status") or "") if isinstance(token, dict) else ""
    if state not in {"reserved", "observe", "exists"}:
        raise SalesAIBudgetBlocked("openai internal ai budget unavailable")

    allowance = ai_budget.allow_next(run_id)
    if not bool((allowance or {}).get("allowed") if isinstance(allowance, dict) else allowance):
        reason = str((allowance or {}).get("reason") or "not_allowed")
        raise SalesAIBudgetBlocked("openai internal ai budget blocked: " + reason)

    provider_idem = "sales-ai:" + hashlib.sha256(
        (str(account_id) + ":" + intent).encode("utf-8")
    ).hexdigest()

    def _post():
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                    "Idempotency-Key": provider_idem,
                },
                json={
                    "model": model,
                    "input": prompt,
                    "max_output_tokens": int(max_output_tokens),
                },
                timeout=int(timeout),
            )
        except Exception as exc:
            # Provider outcome can be ambiguous. Stop this paid run; free
            # fallback may continue because it cannot create a second client send.
            try:
                ai_budget.stop(run_id, "openai_transport_unknown_free_fallback_only")
            except Exception:
                pass
            raise SalesAIProviderUnavailable(
                "openai transport unavailable: " + type(exc).__name__
            ) from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            code = ""
            try:
                code = str(((response.json() or {}).get("error") or {}).get("code") or "")
            except Exception:
                code = ""
            kind = _provider_error_kind(status, code)
            if kind == "billing":
                # OPENAI_BILLING_LATCH_V1:
                # A proven credit/billing failure is durable enough to skip
                # repeated paid probes for a bounded long cooldown. Gemini/local
                # take traffic immediately; after retry_at the normal provider
                # order automatically admits one new OpenAI probe.
                try:
                    from app.ext_api import aiprov as _aiprov
                    _aiprov.mark(
                        "openai",
                        _aiprov.UNAVAILABLE_BILLING,
                        "sales OpenAI billing unavailable: " + (code or str(status)),
                        retry_after=max(
                            3600,
                            int(os.getenv("BORIS_SALES_OPENAI_BILLING_RETRY_SEC", "21600") or 21600),
                        ),
                    )
                except Exception:
                    pass
            try:
                ai_budget.release(run_id)
            except Exception:
                pass
            if kind in {"authentication", "quota", "provider_5xx", "billing"}:
                raise SalesAIProviderUnavailable(
                    "openai http %s %s%s" % (status, kind, (":" + code) if code else "")
                )
            raise SalesAIValidationError("openai http %s request" % status)
        return response

    try:
        response = dependency_call(
            "openai.text",
            _post,
            threshold=1,
            cooldown_seconds=max(
                90, int(os.getenv("BORIS_SALES_OPENAI_COOLDOWN_SEC", "900") or 900)
            ),
            account_id=str(account_id or "") or None,
            tenant_limit_per_minute=max(
                1, int(os.getenv("BORIS_SALES_OPENAI_TENANT_RPM", "60") or 60)
            ),
        )
    except (CircuitOpen, ProviderDeferred):
        try:
            ai_budget.release(run_id)
        except Exception:
            pass
        raise
    except SalesAIProviderUnavailable:
        raise

    body = response.json() or {}
    text_out = _response_output_text(body)
    if not text_out:
        try:
            ai_budget.stop(run_id, "openai_empty_response")
        except Exception:
            pass
        raise SalesAIValidationError("openai returned empty text")

    usage = body.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    request_id = str((getattr(response, "headers", {}) or {}).get("x-request-id") or "").strip()
    if not request_id:
        request_id = "ambiguous-openai:sales:" + hashlib.sha256(
            (str(account_id) + ":" + intent).encode("utf-8")
        ).hexdigest()[:24]

    cost = float(log_usage(
        account_id=str(account_id),
        provider="openai",
        model=str(model),
        operation=str(operation),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_id=request_id,
        usage_details=usage,
    ) or 0.0)
    commit = ai_budget.commit(run_id, request_id)
    if str((commit or {}).get("status") or "") in {"committed", "already_committed"}:
        ai_budget.release(run_id)

    return {
        "text": text_out,
        "provider": "openai",
        "model": str(model),
        "request_id": request_id,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "cost_rub": round(cost, 4),
    }


def _deepseek_default_call(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    model: str,
    timeout: int,
    max_output_tokens: int,
    module: str,
    expect_json: bool = False,
) -> dict:
    """One bounded DeepSeek ChatCompletions call with durable provider fencing."""
    import requests
    from app.usage import log_usage
    from app.ext_api import aiprov as _aiprov

    key = str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise SalesAIProviderUnavailable("deepseek key missing")

    base = str(
        os.getenv("BORIS_DEEPSEEK_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    endpoint = base + "/chat/completions"
    module_name = str(module or "sales").strip().lower()
    fast_non_thinking = bool(module_name == "mop" or str(operation or "").startswith("mop_"))

    messages = []
    if expect_json:
        messages.append({
            "role": "system",
            "content": "Return only valid JSON. The final response must be a JSON object.",
        })
    messages.append({"role": "user", "content": str(prompt)})

    body = {
        "model": str(model),
        "messages": messages,
        "max_tokens": max(64, int(max_output_tokens or 900)),
        "stream": False,
        "thinking": {"type": "disabled" if fast_non_thinking else "enabled"},
    }
    if fast_non_thinking:
        body["temperature"] = float(os.getenv("BORIS_DEEPSEEK_MOP_TEMPERATURE", "0.15") or 0.15)
    else:
        body["reasoning_effort"] = str(
            os.getenv("BORIS_DEEPSEEK_REASONING_EFFORT") or "high"
        )
    if expect_json:
        body["response_format"] = {"type": "json_object"}

    def _run():
        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=max(5, int(timeout)),
            )
        except Exception as exc:
            raise SalesAIProviderUnavailable(
                "deepseek transport unavailable: " + type(exc).__name__
            ) from exc

        try:
            data = response.json()
        except Exception as exc:
            raise SalesAIProviderUnavailable(
                "deepseek invalid response envelope"
            ) from exc

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code >= 300:
            err = data.get("error") if isinstance(data, dict) else {}
            err = err if isinstance(err, dict) else {}
            safe_code = str(err.get("code") or err.get("type") or "")[:80]
            if status_code in (400, 422):
                raise SalesAIValidationError(
                    "deepseek request rejected http=%s code=%s"
                    % (status_code, safe_code or "invalid_parameters")
                )
            if status_code == 402:
                state = _aiprov.UNAVAILABLE_BILLING
                retry_after = max(
                    3600,
                    int(os.getenv("BORIS_DEEPSEEK_BILLING_RETRY_SEC", "21600") or 21600),
                )
                reason = "billing"
            elif status_code in (401, 403):
                state = _aiprov.AUTH_ERROR
                retry_after = max(
                    300,
                    int(os.getenv("BORIS_DEEPSEEK_AUTH_RETRY_SEC", "1800") or 1800),
                )
                reason = "authentication"
            elif status_code == 429:
                state = _aiprov.RATE_LIMITED
                retry_after = max(
                    30,
                    int(os.getenv("BORIS_DEEPSEEK_RATE_RETRY_SEC", "300") or 300),
                )
                reason = "rate_limit"
            else:
                state = _aiprov.TEMP_ERROR
                retry_after = max(
                    30,
                    int(os.getenv("BORIS_DEEPSEEK_TEMP_RETRY_SEC", "120") or 120),
                )
                reason = "provider_error"
            try:
                _aiprov.mark(
                    "deepseek",
                    state,
                    "sales DeepSeek http=%s code=%s" % (status_code, safe_code or reason),
                    retry_after=retry_after,
                )
            except Exception:
                pass
            raise SalesAIProviderUnavailable(
                "deepseek %s http=%s" % (reason, status_code)
            )

        try:
            _aiprov.mark("deepseek", _aiprov.AVAILABLE, "sales DeepSeek call PASS")
        except Exception:
            pass
        return data

    try:
        data = dependency_call(
            "deepseek.sales",
            _run,
            threshold=max(
                2,
                int(os.getenv("BORIS_SALES_DEEPSEEK_CIRCUIT_THRESHOLD", "2") or 2),
            ),
            cooldown_seconds=max(
                60,
                int(os.getenv("BORIS_SALES_DEEPSEEK_COOLDOWN_SEC", "90") or 90),
            ),
            account_id=str(account_id or "") or None,
            tenant_limit_per_minute=max(
                1,
                int(os.getenv("BORIS_SALES_DEEPSEEK_TENANT_RPM", "60") or 60),
            ),
        )
    except (CircuitOpen, ProviderDeferred):
        raise

    choices = data.get("choices") if isinstance(data, dict) else None
    message = ((choices or [{}])[0].get("message") or {}) if choices else {}
    text_out = str(message.get("content") or "").strip()
    if not text_out:
        raise SalesAIOutputInvalid("deepseek returned empty content")

    usage = data.get("usage") if isinstance(data, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    request_id = str(data.get("id") or "").strip()
    if not request_id:
        request_id = "deepseek:sales:" + hashlib.sha256(
            (str(account_id) + ":" + str(operation) + ":" + text_out).encode("utf-8")
        ).hexdigest()[:24]

    cost = float(log_usage(
        account_id=str(account_id),
        provider="deepseek",
        model=str(model),
        operation=str(operation),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_id=request_id,
        usage_details={
            "input_tokens_details": {"cached_tokens": cache_hit},
            "deepseek_prompt_cache_hit_tokens": cache_hit,
            "deepseek_prompt_cache_miss_tokens": int(
                usage.get("prompt_cache_miss_tokens") or 0
            ),
            "deepseek_reasoning_tokens": int(
                ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                if isinstance(usage.get("completion_tokens_details"), dict)
                else 0
            ),
        },
    ) or 0.0)

    return {
        "text": text_out,
        "provider": "deepseek",
        "model": str(model),
        "request_id": request_id,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_cache_hit_tokens": cache_hit,
        },
        "cost_rub": round(cost, 4),
    }

def _gemini_default_call(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    model: str,
    timeout: int,
) -> dict:
    from app.usage import log_usage

    def _run():
        with tempfile.TemporaryDirectory(prefix="boris_sales_gemini_") as work:
            # CLIENT_PROMPT_TOOL_ISOLATION_V1:
            # real customer text is untrusted input. Gemini CLI is allowed to
            # generate text only; every tool (file/shell/web/MCP/subagent) is
            # denied by an admin policy with the highest practical priority.
            policy_path = os.path.join(work, "sales-deny-tools.toml")
            with open(policy_path, "w", encoding="utf-8") as fh:
                fh.write(
                    '[[rule]]\n'
                    'toolName = "*"\n'
                    'decision = "deny"\n'
                    'priority = 999\n'
                    'denyMessage = "Sales generation is text-only; tools are disabled."\n'
                )
            os.chmod(policy_path, 0o600)

            env = dict(os.environ)
            env.setdefault("TERM", "xterm-256color")
            argv = [
                str(os.getenv("BORIS_SALES_GEMINI_BIN") or "gemini"),
                "-m", str(model),
                "-p", str(prompt),
                "-o", "json",
                "--approval-mode", "plan",
                "--admin-policy", policy_path,
                "--skip-trust",
            ]
            try:
                proc = subprocess.run(
                    argv,
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=int(timeout),
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise SalesAIProviderUnavailable("gemini timeout") from exc

            if int(proc.returncode or 0) != 0:
                detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).lower()
                if "user location is not supported" in detail or "unsupported location" in detail:
                    marker = "shared_unsupported_location_400"
                elif "429" in detail or "quota" in detail or "rate limit" in detail:
                    marker = "shared_quota_429"
                else:
                    marker = "cli_error"
                # Do not include raw CLI output here: it may contain customer text.
                raise SalesAIProviderUnavailable("gemini " + marker)

            try:
                envelope = json.loads(proc.stdout or "{}")
            except Exception as exc:
                raise SalesAIProviderUnavailable("gemini invalid envelope") from exc
            text_out = str((envelope or {}).get("response") or "").strip()
            if not text_out:
                raise SalesAIProviderUnavailable("gemini empty response")
            return text_out

    try:
        text_out = dependency_call(
            "gemini.sales",
            _run,
            threshold=1,
            cooldown_seconds=max(
                120, int(os.getenv("BORIS_SALES_GEMINI_COOLDOWN_SEC", "900") or 900)
            ),
            account_id=str(account_id or "") or None,
            tenant_limit_per_minute=max(
                1, int(os.getenv("BORIS_SALES_GEMINI_TENANT_RPM", "30") or 30)
            ),
        )
    except (CircuitOpen, ProviderDeferred):
        raise

    request_id = "gemini-cli:sales:" + hashlib.sha256(
        (str(account_id) + ":" + str(operation) + ":" + text_out).encode("utf-8")
    ).hexdigest()[:24]
    log_usage(
        account_id=str(account_id),
        provider="gemini",
        model=str(model),
        operation=str(operation),
        prompt_tokens=0,
        completion_tokens=0,
        cost_rub=0.0,
        request_id=request_id,
    )
    return {
        "text": text_out,
        "provider": "gemini",
        "model": str(model),
        "request_id": request_id,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "cost_rub": 0.0,
    }


def _gigachat_default_call(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    model: str,
    timeout: int,
    max_output_tokens: int,
) -> dict:
    """One direct GigaChat attempt. No hidden OpenAI fallback."""
    from app.usage import log_usage
    from gigachat.models import Chat, Messages, MessagesRole
    from gigachat_pool import (
        RateLimitedGigaChat,
        _giga_breaker_trip,
        _outage_abandon_probe,
        _outage_note_success,
    )

    creds = str(os.getenv("GIGACHAT_KEY") or "").strip()
    if not creds:
        raise SalesAIProviderUnavailable("gigachat key missing")

    scope = str(os.getenv("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS")
    try:
        with RateLimitedGigaChat(
            credentials=creds,
            scope=scope,
            model=str(model),
            verify_ssl_certs=False,
            timeout=int(timeout),
        ) as client:
            response = client.chat(
                Chat(
                    messages=[
                        Messages(role=MessagesRole.USER, content=str(prompt))
                    ],
                    temperature=0.1,
                    max_tokens=max(64, int(max_output_tokens)),
                )
            )
    except Exception as exc:
        detail = str(exc)
        if "402" in detail or "Payment Required" in detail:
            try:
                _giga_breaker_trip()
            except Exception:
                pass
        else:
            try:
                _outage_abandon_probe()
            except Exception:
                pass
        raise SalesAIProviderUnavailable(
            "gigachat unavailable: " + type(exc).__name__
        ) from exc

    try:
        _outage_note_success()
    except Exception:
        pass

    text_out = str(response.choices[0].message.content or "").strip()
    if not text_out:
        raise SalesAIProviderUnavailable("gigachat empty response")

    usage_obj = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    request_id = "gigachat:sales:" + hashlib.sha256(
        (str(account_id) + ":" + str(operation) + ":" + text_out).encode("utf-8")
    ).hexdigest()[:24]
    cost = float(log_usage(
        account_id=str(account_id),
        provider="gigachat",
        model=str(model),
        operation=str(operation),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_id=request_id,
        usage_details={
            "boris_provenance": {
                "source": "sales_ai_router",
                "direct_provider_call": True,
            }
        },
    ) or 0.0)
    return {
        "text": text_out,
        "provider": "gigachat",
        "model": str(model),
        "request_id": request_id,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "cost_rub": round(cost, 4),
    }


def _ollama_default_call(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    model: str,
    timeout: int,
    format_schema: dict | None = None,
    temperature: float | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
) -> dict:
    from app.usage import log_usage

    # LOCAL_AI_PRODUCTION_PRESSURE_FENCE_V1:
    # Even the emergency local model can consume significant CPU on this host. Never start
    # a local fallback while the scheduler already sees real CPU/RAM pressure;
    # fail fast into the normal provider/retry path instead of starving
    # messaging, CRM, telephony and autonomous recovery.
    try:
        from app.ext_api import sched as _sched
        _pressure = _sched.pressure()
    except Exception as exc:
        raise SalesAIProviderUnavailable(
            "local ollama deferred: production pressure check unavailable"
        ) from exc

    if _pressure.get("under_pressure"):
        # LOCAL_AI_TRANSIENT_PRESSURE_CONFIRM_V1:
        # sched.pressure() intentionally samples CPU for only ~150 ms. On a
        # shared VM, iowait / steal can spike for one sample while there is
        # otherwise enough CPU and RAM for a bounded fallback. Do not turn that
        # single transient sample into a client-visible provider outage.
        #
        # Re-check only short CPU-sample signals (busy/iowait/steal).
        # Hard pressure from RAM, swap or sustained load still fails immediately.
        _transient_reasons = {
            "CPU занят выше безопасного порога",
            "слишком высокий CPU iowait",
            "гипервизор отбирает слишком много CPU",
        }
        _reasons = set(str(x) for x in (_pressure.get("reasons") or []))
        if _reasons and _reasons.issubset(_transient_reasons):
            time.sleep(0.25)
            try:
                _confirmed_pressure = _sched.pressure()
            except Exception:
                _confirmed_pressure = _pressure
            if not _confirmed_pressure.get("under_pressure"):
                _pressure = _confirmed_pressure
            else:
                _pressure = _confirmed_pressure

    # LOCAL_AI_HISTORICAL_LOAD_RECOVERY_FLOOR_V1:
    # Use the same recovery-floor semantics as sched.admission(): a stale/high
    # load average alone must not block a small local fallback when the current
    # CPU/RAM sample proves immediate headroom. Real iowait/CPU/RAM/swap
    # pressure stays fail-closed.
    _historical_headroom = bool(
        _pressure.get("under_pressure")
        and _pressure.get("historical_load_only")
        and _pressure.get("instant_headroom")
    )
    if _pressure.get("under_pressure") and not _historical_headroom:
        reasons = ", ".join(_pressure.get("reasons") or [])
        raise SalesAIProviderUnavailable(
            "local ollama deferred: server under pressure" +
            (": " + reasons if reasons else "")
        )

    body = {
        "model": str(model),
        "prompt": str(prompt),
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "temperature": float(
                temperature
                if temperature is not None
                else (os.getenv("BORIS_SALES_LOCAL_TEMPERATURE", "0.1") or 0.1)
            ),
            "num_ctx": int(
                num_ctx
                if num_ctx is not None
                else (os.getenv("BORIS_SALES_LOCAL_NUM_CTX", "4096") or 4096)
            ),
            "num_predict": int(
                num_predict
                if num_predict is not None
                else (os.getenv("BORIS_SALES_LOCAL_NUM_PREDICT", "768") or 768)
            ),
        },
    }
    if isinstance(format_schema, dict):
        body["format"] = format_schema
    req = urllib.request.Request(
        str(os.getenv("BORIS_SALES_OLLAMA_URL") or "http://127.0.0.1:11434/api/generate"),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        local_timeout = max(
            5,
            min(
                int(timeout),
                int(os.getenv("BORIS_SALES_LOCAL_TIMEOUT_MAX", "45") or 45),
            ),
        )
        with _local_inference_guard(operation):
            with urllib.request.urlopen(req, timeout=local_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
    except SalesAIProviderUnavailable:
        raise
    except Exception as exc:
        detail = str(exc).strip().replace("\n", " ")[:220]
        raise SalesAIProviderUnavailable(
            "ollama unavailable: " + type(exc).__name__ + (": " + detail if detail else "")
        ) from exc

    text_out = str((payload or {}).get("response") or "").strip()
    if not text_out:
        raise SalesAIProviderUnavailable("ollama empty response")

    request_id = "ollama:sales:" + hashlib.sha256(
        (str(account_id) + ":" + str(operation) + ":" + text_out).encode("utf-8")
    ).hexdigest()[:24]
    log_usage(
        account_id=str(account_id),
        provider="ollama",
        model=str(model),
        operation=str(operation),
        prompt_tokens=0,
        completion_tokens=0,
        cost_rub=0.0,
        request_id=request_id,
    )
    return {
        "text": text_out,
        "provider": "ollama",
        "model": str(model),
        "request_id": request_id,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "cost_rub": 0.0,
    }


def _normalize_result(value: Any, provider: str, model: str) -> dict:
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        raise SalesAIValidationError(provider + " adapter returned invalid result")
    text_out = str(value.get("text") or "").strip()
    if not text_out:
        raise SalesAIOutputInvalid(provider + " adapter returned empty text")
    out = dict(value)
    out["text"] = text_out
    out.setdefault("provider", provider)
    out.setdefault("model", model)
    out.setdefault("request_id", "")
    out.setdefault("usage", {"prompt_tokens": 0, "completion_tokens": 0})
    out.setdefault("cost_rub", 0.0)
    return out


def generate_text(
    *,
    account_id: str,
    operation: str,
    prompt: str,
    module: str = "sales",
    idempotency_key: str = "",
    openai_model: str | None = None,
    deepseek_model: str | None = None,
    gemini_model: str | None = None,
    gigachat_model: str | None = None,
    local_model: str | None = None,
    max_output_tokens: int = 900,
    timeout: int = 120,
    expect_json: bool = False,
    local_format: dict | None = None,
    local_temperature: float | None = None,
    local_num_ctx: int | None = None,
    local_num_predict: int | None = None,
    openai_call: Callable[..., Any] | None = None,
    deepseek_call: Callable[..., Any] | None = None,
    gemini_call: Callable[..., Any] | None = None,
    gigachat_call: Callable[..., Any] | None = None,
    local_call: Callable[..., Any] | None = None,
) -> dict:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id required")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt required")

    openai_model = str(
        openai_model or os.getenv("BORIS_SALES_OPENAI_MODEL") or "gpt-5.4-mini"
    )
    _module_name = str(module or "sales").strip().lower()
    deepseek_model = str(
        deepseek_model
        or (
            os.getenv("BORIS_MOP_DEEPSEEK_MODEL")
            if _module_name == "mop"
            else os.getenv("BORIS_ROP_DEEPSEEK_MODEL")
            if _module_name == "rop"
            else None
        )
        or os.getenv("BORIS_SALES_DEEPSEEK_MODEL")
        or ("deepseek-v4-pro" if _module_name == "rop" else "deepseek-v4-flash")
    )
    gemini_model = str(
        gemini_model or os.getenv("BORIS_SALES_GEMINI_MODEL") or "gemini-2.5-flash"
    )
    gigachat_model = str(
        gigachat_model or os.getenv("BORIS_SALES_GIGACHAT_MODEL")
        or os.getenv("GIGACHAT_MODEL") or "GigaChat-Max"
    )
    # SALES_LOCAL_EMERGENCY_MODEL_V1:
    # Live sales fallback must stay on the lightweight model by default; slower
    # analysis callers may still override local_model explicitly.
    local_model = str(
        local_model or os.getenv("BORIS_SALES_LOCAL_MODEL") or "qwen3:1.7b"
    )

    adapters = {
        "openai": openai_call or _openai_default_call,
        "deepseek": deepseek_call or _deepseek_default_call,
        "gemini": gemini_call or _gemini_default_call,
        "gigachat": gigachat_call or _gigachat_default_call,
        "ollama": local_call or _ollama_default_call,
    }
    models = {
        "openai": openai_model,
        "deepseek": deepseek_model,
        "gemini": gemini_model,
        "gigachat": gigachat_model,
        "ollama": local_model,
    }

    chain: list[dict] = []
    order = provider_order(account_id)
    if not order:
        raise SalesAIUnavailable("no sales AI provider enabled")

    for provider in order:
        model = models[provider]
        kwargs = {
            "account_id": account_id,
            "operation": str(operation),
            "prompt": prompt,
            "model": model,
            "timeout": int(timeout),
        }
        if provider == "openai":
            kwargs.update({
                "max_output_tokens": int(max_output_tokens),
                "idempotency_key": str(idempotency_key or ""),
                "module": str(module or "sales"),
            })
        elif provider == "deepseek":
            kwargs.update({
                "max_output_tokens": int(max_output_tokens),
                "module": str(module or "sales"),
                "expect_json": bool(expect_json),
            })
        elif provider == "gigachat":
            kwargs.update({
                "max_output_tokens": int(max_output_tokens),
            })
        elif provider == "ollama":
            kwargs.update({
                "format_schema": local_format if isinstance(local_format, dict) else None,
                "temperature": local_temperature,
                "num_ctx": local_num_ctx,
                "num_predict": local_num_predict,
            })
        try:
            result = _normalize_result(adapters[provider](**kwargs), provider, model)
            if expect_json:
                try:
                    result["json"] = _json_value(result["text"])
                except SalesAIOutputInvalid:
                    # GIGACHAT_MOP_TEXT_ENVELOPE_V1:
                    # GigaChat can occasionally obey the conversational task but
                    # ignore the JSON-only formatting instruction. For the live
                    # MOP contract we can losslessly wrap that natural reply into
                    # the known schema; messenger policy guards still validate the
                    # actual text afterwards. Do not guess schemas for other ops.
                    if provider == "gigachat" and str(operation) == "mop_messenger_reply":
                        wrapped = {
                            "reply_text": str(result["text"]).strip(),
                            "human_handoff": False,
                            "handoff_reason": None,
                        }
                        result["json"] = wrapped
                        result["text"] = json.dumps(wrapped, ensure_ascii=False)
                    else:
                        raise
            result["fallback_chain"] = list(chain)
            return result
        except SalesAIOutputInvalid as exc:
            # Invalid model output is not an infrastructure outage, but another
            # model may still satisfy the same safe contract without a second
            # paid call.
            chain.append({
                "provider": provider,
                "reason": "invalid_output",
                "detail": str(exc)[:180],
            })
            continue
        except SalesAIValidationError:
            # Caller/request contract bugs must stay visible and must not be
            # disguised as a provider outage.
            raise
        except (SalesAIProviderUnavailable, SalesAIBudgetBlocked, CircuitOpen, ProviderDeferred) as exc:
            chain.append({
                "provider": provider,
                "reason": type(exc).__name__,
                "detail": str(exc)[:180],
            })
            continue
        except Exception as exc:
            # Unknown OpenAI errors may represent caller bugs, so fail closed.
            # Free-provider unknown failures may safely degrade to the next free layer.
            if provider == "openai":
                raise
            chain.append({
                "provider": provider,
                "reason": type(exc).__name__,
                "detail": str(exc)[:180],
            })
            continue

    raise SalesAIUnavailable(
        "all sales AI providers unavailable: "
        + ", ".join(str(x.get("provider")) for x in chain),
        chain=chain,
    )


def readiness(account_id: str | None = None) -> dict:
    """Read-only provider readiness for client sales/MOP traffic.

    Never calls paid or remote AI. Provider order already applies circuit/quota
    fences. Local Ollama is considered usable only when production pressure is
    low and its HTTP daemon answers a bounded read-only tags request.
    """
    order = provider_order(account_id)
    remote_ready = [p for p in order if p != "ollama"]
    if remote_ready:
        return {
            "ready": True,
            "order": order,
            "ready_providers": remote_ready,
            "reason": "remote_provider_available",
        }

    if "ollama" not in order:
        return {
            "ready": False,
            "order": order,
            "ready_providers": [],
            "reason": "no_provider_candidate",
        }

    try:
        from app.ext_api import sched as _sched
        pressure = _sched.pressure()
    except Exception as exc:
        return {
            "ready": False,
            "order": order,
            "ready_providers": [],
            "reason": "local_pressure_check_failed:" + type(exc).__name__,
        }
    if pressure.get("under_pressure"):
        # LOCAL_READINESS_TRANSIENT_PRESSURE_CONFIRM_V1:
        # Keep readiness semantics aligned with the real Ollama call. A single
        # short CPU/iowait/steal sample must not make live MOP fast-fail before
        # _ollama_default_call gets the same bounded confirmation chance.
        transient_reasons = {
            "CPU занят выше безопасного порога",
            "слишком высокий CPU iowait",
            "гипервизор отбирает слишком много CPU",
        }
        reasons = set(str(x) for x in (pressure.get("reasons") or []))
        if reasons and reasons.issubset(transient_reasons):
            time.sleep(0.25)
            try:
                confirmed = _sched.pressure()
            except Exception:
                confirmed = pressure
            pressure = confirmed

        historical_headroom = bool(
            pressure.get("under_pressure")
            and pressure.get("historical_load_only")
            and pressure.get("instant_headroom")
        )
        if pressure.get("under_pressure") and not historical_headroom:
            return {
                "ready": False,
                "order": order,
                "ready_providers": [],
                "reason": "local_fenced_by_production_pressure",
                "pressure_reasons": list(pressure.get("reasons") or []),
            }

    try:
        req = urllib.request.Request(
            str(os.getenv("BORIS_SALES_OLLAMA_URL") or "http://127.0.0.1:11434/api/generate")
            .replace("/api/generate", "/api/tags"),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            local_ok = int(getattr(response, "status", 0) or 0) == 200
    except Exception:
        local_ok = False
    return {
        "ready": bool(local_ok),
        "order": order,
        "ready_providers": ["ollama"] if local_ok else [],
        "reason": "local_provider_available" if local_ok else "local_provider_unavailable",
    }


def status(account_id: str | None = None) -> dict:
    return {
        "order": provider_order(account_id),
        "readiness": readiness(account_id),
        "openai_circuit": _circuit_snapshot("openai.text", account_id),
        "deepseek_circuit": _circuit_snapshot("deepseek.sales", account_id),
        "gemini_circuit": _circuit_snapshot("gemini.sales", account_id),
        "models": {
            "openai": str(os.getenv("BORIS_SALES_OPENAI_MODEL") or "gpt-5.4-mini"),
            "deepseek": str(os.getenv("BORIS_SALES_DEEPSEEK_MODEL") or "deepseek-v4-flash"),
            "gemini": str(os.getenv("BORIS_SALES_GEMINI_MODEL") or "gemini-2.5-flash"),
            "ollama": str(os.getenv("BORIS_SALES_LOCAL_MODEL") or "qwen3:1.7b"),
        },
        "policy": "openai_if_usable_then_deepseek_then_free_gemini_then_local",
    }
