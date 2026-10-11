# -*- coding: utf-8 -*-
"""Fail-closed semantic media guard for Feed Factory.

Universal rule: the semantic source of truth is the CampaignItem content
(title + description + category/direction). Every linked image must have a
current item-specific visual proof for the exact content fingerprint and exact
media placement. Changing text or image makes the old proof stale and blocks
validation/export until a new vision audit passes.
"""
from __future__ import annotations
from app.services.exception_observability import observe_suppressed


_VISION_LOCK_ENGINE = None


def _vision_advisory_engine():
    """Dedicated NullPool for long-lived advisory locks around paid vision calls.

    Paid provider requests may take tens of seconds. Holding those locks on the
    main BORIS QueuePool can starve ai_budget/log_usage, especially in daemon
    runtimes whose normal DB pool is intentionally tiny.
    """
    global _VISION_LOCK_ENGINE
    if _VISION_LOCK_ENGINE is None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from app.db.session import DATABASE_URL
        _VISION_LOCK_ENGINE = create_engine(
            DATABASE_URL,
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={"application_name": "boris-vision-advisory-lock"},
        )
    return _VISION_LOCK_ENGINE


def _vision_estimate_kopeks() -> int:
    """Conservative pre-call reserve for one low-detail gpt-4o-mini vision QA."""
    import os
    try:
        return max(1, min(500, int(os.environ.get("BORIS_VISION_EST_KOPEKS", "100") or 100)))
    except Exception:
        return 100


def _vision_burst_guard(account_id: str):
    from contextlib import contextmanager
    @contextmanager
    def _guard():
        import datetime, hashlib, os
        from sqlalchemy import text
        try: cap=max(1,min(100,int(os.environ.get("BORIS_VISION_BURST_CAP","20") or 20)))
        except Exception: cap=20
        try: window=max(1,min(60,int(os.environ.get("BORIS_VISION_BURST_WINDOW_MIN","10") or 10)))
        except Exception: window=10
        try: daily=max(cap,min(1000,int(os.environ.get("BORIS_VISION_DAILY_CAP","100") or 100)))
        except Exception: daily=100
        fallback_model=str(os.environ.get("BORIS_VISION_FALLBACK_MODEL") or "google/gemini-3.1-flash-lite")
        raw=int.from_bytes(hashlib.blake2b(("vision-burst|"+str(account_id)).encode("utf-8"),digest_size=8).digest(),"big",signed=False)
        key=raw if raw < (1<<63) else raw-(1<<64)
        # Session-level advisory lock on an AUTOCOMMIT connection. This keeps
        # cross-process serialization without holding a PostgreSQL transaction
        # open while the caller performs the paid external vision request.
        db=_vision_advisory_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            db.execute(text("select pg_advisory_lock(:k)"),{"k":key})
            now=datetime.datetime.now(datetime.timezone.utc)
            cutoff=now-datetime.timedelta(minutes=window)
            day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
            provider_scope="((provider='openai' and model='gpt-4o-mini') or (provider='plusvibe' and model=:fm))"
            row=db.execute(text("select count(*),coalesce(sum(cost_rub),0) from api_usage where account_id=:a and "+provider_scope+" and created_at>=:c"),{"a":str(account_id),"fm":fallback_model,"c":cutoff}).fetchone()
            day_row=db.execute(text("select count(*),coalesce(sum(cost_rub),0) from api_usage where account_id=:a and "+provider_scope+" and created_at>=:d"),{"a":str(account_id),"fm":fallback_model,"d":day_start}).fetchone()
            used=int((row[0] if row else 0) or 0); spent=float((row[1] if row else 0) or 0)
            day_used=int((day_row[0] if day_row else 0) or 0); day_spent=float((day_row[1] if day_row else 0) or 0)
            code = "VISION_DAILY_CAP" if day_used >= daily else ("VISION_BURST_CAP" if used >= cap else None)
            yield {"allowed":code is None,"code":code,"used":used,"cap":cap,"window_min":window,"spent_rub":round(spent,2),
                   "daily_used":day_used,"daily_cap":daily,"daily_spent_rub":round(day_spent,2)}
        finally:
            try: db.execute(text("select pg_advisory_unlock(:k)"),{"k":key})
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=78)
            db.close()
    return _guard()


def _vision_no_charge_error(code, *, retryable=False, provider_calls=0, intent_key=None):
    """Internal proof for an admission failure or an explicit non-inference response."""
    error = RuntimeError(code)
    error.vision_meta = {"cost_known": True, "cost_rub": 0.0,
        "provider_cost_state": "not_requested" if provider_calls == 0 else "known_not_billed",
        "provider_calls": provider_calls, "provider_call_count_known": True,
        "retryable": retryable is True, "intent_key": intent_key}
    return error


def _vision_paid_metadata(account_id, operation, record, *, cache_hit):
    """Read historical cost from the existing ledger; keep incremental cache cost separate."""
    import json
    from decimal import Decimal, InvalidOperation
    from sqlalchemy import text
    from app.db.session import SessionLocal
    record = dict(record or {})
    rid = str(record.get("request_id") or record.get("provider_request_id") or
              ("ambiguous-vision:"+str(record.get("intent_hash")) if record.get("intent_hash") else "")).strip()
    row = None
    db = None
    try:
        db = SessionLocal()
        if rid:
            raw = db.execute(text("SELECT request_id,provider,model,operation,cost_rub,rate_source,pricing_version,"
                "provenance_json,prompt_tokens,completion_tokens FROM api_usage "
                "WHERE account_id=:a AND operation=:o AND request_id=:r"),
                {"a": str(account_id), "o": str(operation), "r": rid}).mappings().first()
            row = dict(raw) if raw is not None else None
    except Exception:
        row = None
    finally:
        if db is not None:
            db.close()
    known = False
    amount = None
    provenance = (row or {}).get("provenance_json") or {}
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except (TypeError, ValueError):
            provenance = {}
    try:
        value = (row or {}).get("cost_rub")
        amount = Decimal(str(value)) if value is not None and not isinstance(value, bool) else None
        known = bool(amount is not None and amount.is_finite() and amount >= 0 and
                     row.get("rate_source") in {"table", "explicit"} and row.get("pricing_version"))
        if isinstance(provenance, dict) and provenance.get("vision_usage_known") is False:
            known = False
        if known and row.get("rate_source") == "table" and not any(
                type(row.get(k)) is int and row[k] > 0 for k in ("prompt_tokens", "completion_tokens")):
            known = False
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        known = False
    historical = float(amount) if known else None
    return {
        "cache_hit": bool(cache_hit), "request_id": rid or None,
        "provider_request_id": record.get("provider_request_id"),
        "ambiguous_success": record.get("ambiguous_success") is True,
        "provider": (row or {}).get("provider") or record.get("provider"),
        "model": (row or {}).get("model") or record.get("model"),
        "operation": str(operation), "intent_key": "vision:"+str(record.get("intent_hash") or ""),
        "cost_known": known, "cost_rub": historical,
        "incremental_cost_rub": 0.0 if cache_hit else historical,
        "estimated_cost_rub": record.get("estimated_cost_rub"),
        "rate_source": (row or {}).get("rate_source"), "pricing_version": (row or {}).get("pricing_version"),
        "usage": record.get("usage"), "latency_ms": record.get("latency_ms"),
        "qa_version": record.get("qa_version"),
        "provider_calls": 0 if cache_hit else int(record.get("provider_calls") or 1),
        "provider_call_count_known": cache_hit or type(record.get("provider_calls")) is int,
        "historical_provider_calls": record.get("provider_calls"),
    }


def _premium_banner_receipt_matches(row, payload, link, asset):
    """A Premium-specific no-paid proof, for both a lone hero and an 8+1 gallery."""
    import re
    from app.services.banner_full_ai_premium import read_current_preview
    proof = payload.get("banner_qa") or {}
    if not isinstance(proof, dict) or proof.get("passed") is not True:
        return False
    if str(getattr(asset, "source_provider", "") or "").lower() != "openai_full_ai_premium":
        return False
    account = str(getattr(row, "account_id", "") or "")
    if not account or str(getattr(asset, "account_id", "") or "") != account:
        return False
    if (str(getattr(link, "role", "") or "") != "hero" or type(getattr(link, "position", None)) is not int
            or getattr(link, "position", None) != 0):
        return False
    source_ref = str(getattr(asset, "source_ref", "") or "")
    match = re.fullmatch(r"premium:([^:]+):v([1-9][0-9]*)", source_ref)
    if not match:
        return False
    banner_id, version = match.group(1), int(match.group(2))
    if proof.get("banner_id") != banner_id or type(proof.get("version")) is not int or proof.get("version") != version:
        return False
    key = _payload_local_image_keys({"images": [payload.get("banner_url")] }).get(0)
    if not key or key != str(getattr(asset, "storage_key", "") or "").lstrip("/"):
        return False
    try:
        current = read_current_preview(account, banner_id, version)
    except (ValueError, TypeError, RuntimeError, OSError):
        return False
    if not (current.get("status") == "ok" and current.get("receipt_current") is True
            and current.get("artifact_current") is True and current.get("qa_terminal") is True
            and current.get("qa_passed") is True and current.get("cost_reconciled") is True
            and current.get("cost_sla_pass") is True):
        return False
    if _payload_local_image_keys({"images": [current.get("url")]}).get(0) != key:
        return False
    if proof.get("artifact_sha256") != current.get("artifact_sha256") or proof.get("qa_version") != current.get("qa_version"):
        return False
    provenance = current.get("provenance") or {}
    if str(provenance.get("campaign_id") or "") != str(getattr(row, "campaign_id", "") or ""):
        return False
    if str(provenance.get("campaign_item_id") or provenance.get("item_id") or "") != str(getattr(row, "id", "") or ""):
        return False
    fingerprint = semantic_content_fingerprint(payload)
    return provenance.get("content_fingerprint") == fingerprint and proof.get("content_fingerprint") == fingerprint


def _vision_chat_json(account_id: str, operation: str, content: list, max_tokens: int, timeout: int, return_meta: bool = False, provenance: dict | None = None):
    """Exactly-once financially guarded OpenAI vision/chat request.

    A deterministic intent is derived from the exact semantic request. The intent is
    serialized across API/worker processes, cached durably before downstream work,
    and sent to OpenAI as Idempotency-Key. This closes the paid retry gap when two
    Campaign/Feed Factory flows ask for the same vision proof concurrently or an
    HTTP response is lost after provider success.
    """
    import hashlib
    import json
    import os
    import tempfile
    import time
    import requests
    from sqlalchemy import text
    from sqlalchemy.exc import TimeoutError as SATimeoutError
    from app.services import ai_budget
    from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard
    from app.usage import log_usage
    from app.services.job_execution_clock import provider_call

    account_id = str(account_id or "").strip()
    if not account_id:
        raise RuntimeError("VISION_ACCOUNT_REQUIRED")
    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    fallback_key = str(os.environ.get("PLUSVIBE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    fallback_base = str(os.environ.get("BORIS_VISION_FALLBACK_BASE_URL") or os.environ.get("BORIS_DEEPSEEK_BASE_URL") or "https://plusvibeapi.ru/v1").rstrip("/")
    fallback_model = str(os.environ.get("BORIS_VISION_FALLBACK_MODEL") or "google/gemini-3.1-flash-lite").strip()

    intent_basis = json.dumps({
        "policy": "feed_vision_exactly_once_v1",
        "account_id": account_id,
        "operation": str(operation),
        "model": "gpt-4o-mini",
        "content": content,
        "max_tokens": int(max_tokens),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    intent_hash = hashlib.sha256(intent_basis.encode("utf-8")).hexdigest()
    intent_key = "vision:" + intent_hash
    run_id = "vision:%s:%s" % (account_id, intent_hash)
    cache_root = os.environ.get("BORIS_VISION_PAID_CACHE_DIR", "/root/BORIS/backend/.vision_qa_cache/paid")
    cache_path = os.path.join(cache_root, intent_hash + ".json")
    uncertain_path = cache_path + ".uncertain"

    def _audit(code: str, status: str = "blocked", details=None):
        _audit_ai_guard(account_id=account_id, source="vision_qa", operation=operation,
                        event_code=code, status=status, intent_key=intent_key,
                        details=details if isinstance(details, dict) else None)

    def _uncertain_read():
        try:
            with open(uncertain_path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            return obj if obj.get("intent_hash") == intent_hash else None
        except Exception:
            return None

    def _uncertain_write(error_code: str, provider_request_id: str | None = None, record: dict | None = None):
        os.makedirs(cache_root, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".paid-vision-uncertain-", suffix=".json", dir=cache_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({
                    **dict(record or {}),
                    "schema": 2,
                    "intent_hash": intent_hash,
                    "status": "provider_outcome_uncertain",
                    "operation": operation,
                    "provider_request_id": provider_request_id or None,
                    "error_code": str(error_code or "vision_transport_outcome_unknown"),
                }, fh, ensure_ascii=False, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, uncertain_path)
            _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(error_code or "vision_transport_outcome_unknown")})
        except Exception:
            try: os.unlink(tmp_path)
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=156)
            raise

    def _raise_uncertain(record):
        error = RuntimeError("VISION_OUTCOME_UNCERTAIN:%s" % str(record.get("error_code") or "unknown"))
        error.vision_meta = _vision_paid_metadata(account_id, operation, record, cache_hit=True)
        raise error

    def _cache_read():
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if obj.get("intent_hash") == intent_hash and "result" in obj:
                return obj
        except Exception:
            return None
        return None

    cached = _cache_read()
    if cached is not None:
        if return_meta:
            return cached["result"], _vision_paid_metadata(account_id, operation, cached, cache_hit=True)
        return cached["result"]
    uncertain = _uncertain_read()
    if uncertain is not None:
        _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(uncertain.get("error_code") or "unknown")})
        _raise_uncertain(uncertain)

    if not api_key and not fallback_key:
        raise _vision_no_charge_error("VISION_PROVIDER_KEY_MISSING", retryable=False)

    raw_key = int.from_bytes(hashlib.blake2b((account_id + "|" + intent_key).encode("utf-8"), digest_size=8).digest(), "big", signed=False)
    lock_key = raw_key if raw_key < (1 << 63) else raw_key - (1 << 64)
    # The paid-request idempotency lock must outlive the HTTP call, but the
    # PostgreSQL transaction must not. A session advisory lock on AUTOCOMMIT
    # provides exactly that: one intent at a time, zero idle-in-transaction.
    lock_db = None
    lock_held = False
    def _release_paid_lock():
        nonlocal lock_db, lock_held
        if lock_db is None:
            return
        if lock_held:
            try: lock_db.execute(text("select pg_advisory_unlock(:k)"), {"k": lock_key})
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=193)
            lock_held=False
        try: lock_db.close()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=196)
        lock_db=None
    # Temporary DB pool pressure is infrastructure backpressure, not a semantic
    # failure. Retry acquiring the idempotency lock before failing the QA job.
    for _db_try in range(4):
        try:
            lock_db = _vision_advisory_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
            break
        except SATimeoutError:
            if _db_try >= 3:
                raise _vision_no_charge_error("VISION_DB_POOL_BUSY_RETRYABLE", retryable=True)
            time.sleep(1.5 * (_db_try + 1))
    try:
        lock_db.execute(text("select pg_advisory_lock(:k)"), {"k": lock_key}); lock_held=True
        cached = _cache_read()
        if cached is not None:
            if return_meta:
                return cached["result"], _vision_paid_metadata(account_id, operation, cached, cache_hit=True)
            return cached["result"]
        uncertain = _uncertain_read()
        if uncertain is not None:
            _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(uncertain.get("error_code") or "unknown")})
            _raise_uncertain(uncertain)

        est = _vision_estimate_kopeks()
        token = None
        budget_run_id = run_id
        for _budget_try in range(4):
            token = ai_budget.reserve(
                account_id=account_id, module="media_vision", operation=operation,
                run_id=budget_run_id, est_kopeks=est, calls_planned=1,
            )
            status = str((token or {}).get("status") or "") if isinstance(token, dict) else ""
            # The provider intent/cache key is deterministic, but an accounting
            # reservation is single-use. A previous pre-provider rejection may have
            # released that reservation without producing cache bytes. Re-open ONLY
            # the accounting envelope with a new run id; the same provider Idempotency-Key
            # and advisory lock still make the paid vision intent exactly-once.
            if status == "exists" and str((token or {}).get("run_status") or "") not in {"reserved"}:
                import uuid as _uuid
                budget_run_id = run_id + ":retry:" + _uuid.uuid4().hex[:12]
                token = ai_budget.reserve(
                    account_id=account_id, module="media_vision", operation=operation,
                    run_id=budget_run_id, est_kopeks=est, calls_planned=1,
                )
                status = str((token or {}).get("status") or "") if isinstance(token, dict) else ""
            if status != "error":
                break
            if _budget_try < 3:
                time.sleep(1.5 * (_budget_try + 1))
        status = str((token or {}).get("status") or "") if isinstance(token, dict) else ""
        if status not in {"reserved", "observe", "exists"}:
            _audit("AI_BUDGET_RESERVE_FAILED", "blocked", {"status": status})
            raise _vision_no_charge_error("VISION_BUDGET_RESERVE_FAILED:%s" % status, retryable=True, intent_key=intent_key)

        try:
            allowance = ai_budget.allow_next(budget_run_id)
            allowed = bool((allowance or {}).get("allowed")) if isinstance(allowance, dict) else bool(allowance)
            if not allowed:
                _audit("AI_BUDGET_NOT_ALLOWED", "blocked", {"reason": str((allowance or {}).get("reason") or "") if isinstance(allowance,dict) else ""})
                raise _vision_no_charge_error("VISION_BUDGET_NOT_ALLOWED", retryable=True)

            with _vision_burst_guard(account_id) as burst:
                if not burst.get("allowed"):
                    _code = str(burst.get("code") or "VISION_BURST_CAP")
                    _audit(_code, "blocked", burst)
                    if _code == "VISION_DAILY_CAP":
                        raise _vision_no_charge_error("VISION_DAILY_CAP:%s/%s" % (burst.get("daily_used"), burst.get("daily_cap")), retryable=True, intent_key=intent_key)
                    raise _vision_no_charge_error("VISION_BURST_CAP:%s/%s" % (burst.get("used"), burst.get("cap")), retryable=True, intent_key=intent_key)
                from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
                external_content, privacy_vault = safe_external_ai_payload(content)
                provider_name = "openai"
                provider_model = "gpt-4o-mini"
                response = None
                primary_status = 0
                provider_started = time.monotonic()
                provider_calls = 0
                if api_key:
                    try:
                        provider_calls += 1
                        response = provider_call(requests.post,
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": "Bearer " + api_key,
                                "Content-Type": "application/json",
                                "Idempotency-Key": intent_key,
                            },
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": external_content}],
                                "temperature": 0,
                                "max_tokens": int(max_tokens),
                            },
                            timeout=int(timeout),
                        )
                    except Exception:
                        # A transport exception can happen after the provider has
                        # accepted/billed the request. Never fan out to a fallback
                        # in that ambiguous state; quarantine the exact intent.
                        _uncertain_write("vision_transport_outcome_unknown")
                        raise RuntimeError("VISION_OUTCOME_UNCERTAIN:vision_transport_outcome_unknown")
                    primary_status = int(getattr(response, "status_code", 0) or 0)

                # VISION_SAFE_PROVIDER_FALLBACK_V1:
                # Fall back only when OpenAI definitively rejected the request
                # before inference (or no OpenAI key exists). Never fall back from
                # transport/200-parse uncertainty, because that could double-buy
                # the same visual proof.
                safe_primary_reject = primary_status in {401, 402, 403, 404, 429}
                use_fallback = bool(fallback_key and (not api_key or safe_primary_reject))
                if use_fallback:
                    provider_name = "plusvibe"
                    provider_model = fallback_model
                    fallback_session = requests.Session()
                    fallback_session.trust_env = False
                    try:
                        _fallback_json={
                            "model": fallback_model,
                            "messages": [{"role": "user", "content": external_content}],
                            "temperature": 0,
                            "max_tokens": int(max_tokens),
                        }
                        # VISION_OBJECT_STRUCTURED_JSON_V1:
                        # Banner QA expects one JSON object. PlusVibe/Gemini can
                        # otherwise return malformed prose/JSON with HTTP 200,
                        # forcing paid recovery intents. Ask the OpenAI-compatible
                        # endpoint for a JSON object only for object-shaped QA.
                        # Media semantic QA expects arrays and must remain unchanged.
                        _object_json_ops={
                            "campaign_banner_vision_qa",
                            "campaign_banner_vision_qa_bad_json_recovery_v1",
                            "campaign_banner_vision_qa_bad_json_recovery_v2",
                            "campaign_banner_vision_qa_schema_retry_v1",
                            "campaign_banner_vision_qa_batch_v1",
                            "banner_premium_vision_qa",
                            "banner_premium_vision_qa_batch_v1",
                            "social_banner_vision_qa_v1",
                        }
                        if str(operation) in _object_json_ops:
                            _fallback_json["response_format"]={"type":"json_object"}
                        provider_calls += 1
                        response = provider_call(fallback_session.post,
                            fallback_base + "/chat/completions",
                            headers={
                                "Authorization": "Bearer " + fallback_key,
                                "Content-Type": "application/json",
                                "Idempotency-Key": intent_key,
                            },
                            json=_fallback_json,
                            timeout=int(timeout),
                        )
                    except Exception:
                        _uncertain_write("vision_fallback_transport_outcome_unknown")
                        raise RuntimeError("VISION_OUTCOME_UNCERTAIN:vision_fallback_transport_outcome_unknown")
                    fallback_status = int(getattr(response, "status_code", 0) or 0)
                    if fallback_status != 200:
                        if fallback_status in {401, 402, 403, 404, 429}:
                            raise _vision_no_charge_error("VISION_FALLBACK_HTTP_ERROR:%s" % fallback_status, retryable=fallback_status == 429, provider_calls=provider_calls, intent_key=intent_key)
                        _uncertain_write("VISION_FALLBACK_HTTP_ERROR:%s" % fallback_status)
                        raise RuntimeError("VISION_OUTCOME_UNCERTAIN:VISION_FALLBACK_HTTP_ERROR:%s" % fallback_status)
                    _audit("VISION_FALLBACK_USED", "recovered", {
                        "primary_status": primary_status or None,
                        "fallback_model": fallback_model,
                    })
                elif not api_key:
                    raise _vision_no_charge_error("VISION_OPENAI_KEY_MISSING", retryable=False)
                elif primary_status != 200:
                    if primary_status in {401, 402, 403, 404, 429}:
                        raise _vision_no_charge_error("VISION_PROVIDER_HTTP_ERROR:%s" % primary_status, retryable=primary_status == 429, provider_calls=provider_calls, intent_key=intent_key)
                    _uncertain_write("VISION_PROVIDER_HTTP_ERROR:%s" % primary_status)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:VISION_PROVIDER_HTTP_ERROR:%s" % primary_status)

                exact_rid = str(response.headers.get("x-request-id") or "").strip()
                try:
                    body = response.json()
                except Exception:
                    _uncertain_write("provider_200_unparseable", exact_rid or None)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:provider_200_unparseable")
                if not isinstance(body, dict):
                    _uncertain_write("provider_200_invalid_envelope", exact_rid or None)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:provider_200_invalid_envelope")
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                usage_known = any(type(usage.get(k)) is int and usage[k] > 0
                                  for k in ("prompt_tokens", "completion_tokens"))
                def _paid_payload_error(code):
                    # HTTP 200 has already incurred this attempt's cost even if
                    # its JSON result is unusable. Preserve quarantine and ledger.
                    failed_rid = exact_rid or ("ambiguous-vision:"+intent_hash)
                    failed_record = {"intent_hash": intent_hash, "request_id": failed_rid,
                        "provider_request_id": exact_rid or None, "provider": provider_name,
                        "model": provider_model, "usage": usage, "provider_calls": provider_calls,
                        "latency_ms": round((time.monotonic()-provider_started)*1000, 3),
                        "estimated_cost_rub": est/100.0, "qa_version": (provenance or {}).get("qa_version")}
                    _uncertain_write(code, exact_rid or None, failed_record)
                    _release_paid_lock()
                    log_usage(account_id, provider_name, provider_model, operation,
                        prompt_tokens=(usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] >= 0 else 0),
                        completion_tokens=(usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int and usage["completion_tokens"] >= 0 else 0), request_id=failed_rid,
                        usage_details={**usage, "boris_intent_key": intent_key,
                            "boris_provenance": {**dict(provenance or {}), "vision_usage_known": usage_known}})
                    ai_budget.commit(budget_run_id, failed_rid)
                    error = RuntimeError("VISION_OUTCOME_UNCERTAIN:"+code)
                    error.vision_meta = _vision_paid_metadata(account_id, operation,
                        failed_record, cache_hit=False)
                    raise error

                try:
                    raw = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                except (AttributeError, IndexError, KeyError, TypeError):
                    _paid_payload_error("provider_200_invalid_choices")
                if raw:
                    raw = str(restore_external_ai_output(raw, privacy_vault))
                raw = raw.replace("```json", "").replace("```", "").strip()
                if not raw:
                    _paid_payload_error("provider_200_empty_result")
                try:
                    result = json.loads(raw)
                except Exception:
                    _paid_payload_error("provider_200_bad_result_json")
                rid = exact_rid or ("ambiguous-vision:" + intent_hash)

                provider_latency_ms = round((time.monotonic()-provider_started)*1000, 3)
                cache_record = {"schema": 2, "intent_hash": intent_hash, "operation": operation,
                    "provider": provider_name, "model": provider_model, "request_id": rid,
                    "provider_request_id": exact_rid or None, "ambiguous_success": not bool(exact_rid),
                    "result": result, "usage": usage, "latency_ms": provider_latency_ms,
                    "provider_calls": provider_calls, "estimated_cost_rub": est/100.0,
                    "qa_version": (provenance or {}).get("qa_version")}

                # Persist the paid result before ledger/budget reconciliation. If the
                # process dies after provider success, the same intent is still a cache
                # hit and cannot silently repurchase the vision proof.
                os.makedirs(cache_root, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=".paid-vision-", suffix=".json", dir=cache_root)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(cache_record, fh, ensure_ascii=False, sort_keys=True)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp_path, cache_path)
                except Exception:
                    try: os.unlink(tmp_path)
                    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=328)
                    raise

                # The provider result is already durable. Release the SQLAlchemy
                # advisory-lock connection BEFORE financial reconciliation so a
                # pool_size=1 deployment cannot deadlock its own log_usage/commit.
                _release_paid_lock()
                cost_rub = 0
                for _acct_try in range(4):
                    cost_rub = log_usage(
                        account_id,
                        provider_name,
                        provider_model,
                        operation,
                        prompt_tokens=(usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] >= 0 else 0),
                        completion_tokens=(usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int and usage["completion_tokens"] >= 0 else 0),
                        request_id=rid,
                        usage_details={**(usage if isinstance(usage,dict) else {}),
                                       "boris_intent_key": intent_key,
                                       "boris_provenance": {**{str(k)[:64]: v for k,v in (provenance or {}).items()
                                                            if isinstance(v,(str,int,float,bool)) or v is None},
                                                            "vision_usage_known": usage_known}},
                    )
                    committed = ai_budget.commit(budget_run_id, rid)
                    if str((committed or {}).get("status") or "") in {"committed","already_committed"}:
                        break
                    if _acct_try < 3:
                        time.sleep(0.5 * (_acct_try + 1))
                if return_meta:
                    return result, _vision_paid_metadata(account_id, operation, cache_record, cache_hit=False)
                return result
        finally:
            ai_budget.release(budget_run_id)
    finally:
        # Safe whether the lock is still held or was deliberately released before
        # ledger reconciliation. Never dereference a None connection on success.
        _release_paid_lock()


DIRECTION_TO_CLASS = {
    "Кухни на заказ": "kitchen",
    "Прихожие на заказ": "hallway_entryway",
    "Гардеробные на заказ": "walk_in_wardrobe",
    "Мебель для ванных комнат на заказ": "bathroom_furniture",
}


def expected_class(payload: dict, row=None) -> str:
    direction = str(payload.get("direction") or getattr(row, "segment", "") or "").strip()
    return DIRECTION_TO_CLASS.get(direction, "")


def semantic_content_fingerprint(payload: dict) -> str:
    import hashlib, json
    basis = {
        "title": str(payload.get("title") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "category": str(payload.get("category") or "").strip(),
        "category_path": str(payload.get("category_path") or "").strip(),
        "direction": str(payload.get("direction") or "").strip(),
        "offer": str(payload.get("offer") or "").strip(),
    }
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _tag_value(tags, prefix: str) -> str:
    for tag in tags or []:
        s = str(tag)
        if s.startswith(prefix):
            return s[len(prefix):]
    return ""


def _linked_media(db, row):
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    return (db.query(MediaLink, MediaAsset).join(MediaAsset, MediaAsset.id == MediaLink.media_id)
            .filter(MediaLink.entity_type == "campaign_item", MediaLink.entity_id == str(row.id), MediaAsset.deleted_at.is_(None))
            .order_by(MediaLink.position.asc(), MediaLink.id.asc()).all())


def _payload_local_image_keys(payload: dict) -> dict[int, str]:
    """Return exact local storage_key by payload image position when it is knowable.

    Feed Factory may later create uq_* transport variants while rendering XML.
    This guard intentionally compares CampaignItem payload to MediaLink before that
    transport step, so stale semantic links cannot certify a different image list.
    External URLs/media-id-only shapes are left unresolved rather than guessed.
    """
    from urllib.parse import unquote
    images = payload.get("images") or []
    if not isinstance(images, list):
        return {}
    out: dict[int, str] = {}
    for pos, item in enumerate(images):
        raw = ""
        storage_key = ""
        if isinstance(item, dict):
            storage_key = str(item.get("storage_key") or "").strip()
            raw = str(item.get("url") or item.get("image") or item.get("src") or "").strip()
        else:
            raw = str(item or "").strip()
        key = ""
        if storage_key:
            key = storage_key.lstrip("/")
        elif "/images/" in raw:
            key = raw.split("/images/", 1)[1].split("?", 1)[0].lstrip("/")
        elif raw.startswith("images/"):
            key = raw[len("images/"):].split("?", 1)[0].lstrip("/")
        if key:
            out[int(pos)] = unquote(key)
    return out


def validate_linked_media_semantics(db, row, payload: dict) -> list[dict]:
    """Return blocking semantic errors. Universal mode is category-agnostic."""
    guard = payload.get("media_semantic_guard") or {}
    if not isinstance(guard, dict):
        guard = {}
    mode = str(guard.get("mode") or "").lower()
    if payload.get("feed_factory") and mode not in {"strict", "universal", "universal_text_image"}:
        # PREMIUM_BANNER_ONLY_DEDICATED_QA_V1: reuse dedicated Premium proof only
        # for the exact single current hero artifact; every other case stays fail-closed.
        _banner_qa = payload.get("banner_qa") or {}
        _banner_url = str(payload.get("banner_url") or "").strip()
        if bool(_banner_qa.get("passed")) and _banner_url:
            _premium_links = _linked_media(db, row)
            if len(_premium_links) == 1:
                _link, _asset = _premium_links[0]
                _provider = str(getattr(_asset, "source_provider", "") or "").strip().lower()
                _asset_key = str(getattr(_asset, "storage_key", "") or "").lstrip("/")
                _banner_key = ""
                if "/images/" in _banner_url:
                    _banner_key = _banner_url.split("/images/", 1)[1].split("?", 1)[0].lstrip("/")
                elif _banner_url.startswith("images/"):
                    _banner_key = _banner_url[len("images/"):].split("?", 1)[0].lstrip("/")
                if (_provider == "openai_full_ai_premium"
                        and _banner_key and _asset_key == _banner_key
                        and str(getattr(_link, "role", "") or "") == "hero"
                        and int(getattr(_link, "position", 0) or 0) == 0
                        and _premium_banner_receipt_matches(row, payload, _link, _asset)):
                    return []
        return [{"field":"images","code":"media_semantic_guard_missing","message":"Feed Factory: фотографии не проверены против названия/описания/категории объявления"}]
    if mode not in {"strict", "universal", "universal_text_image"}:
        return []
    links = _linked_media(db, row)
    if not links:
        return [{"field":"images","code":"media_semantic_no_links","message":"Нет фотографий для семантической проверки"}]

    # MEDIA_SEMANTIC_PLACEMENT_COUNT_GUARD_V1:
    # A durable semantic review may certify an exact publication layout (for
    # example PLANETA: QA-approved banner + seven gallery photos = 8). A later
    # rewrite must never silently shrink payload/images and MediaLinks together,
    # because that would otherwise look internally consistent and bypass the
    # banner-specific guard if banner metadata was also dropped.
    _review = payload.get("media_semantic_review") or {}
    if isinstance(_review, dict):
        try:
            _expected_placements = int(_review.get("placements") or 0)
        except Exception:
            _expected_placements = 0
        if _expected_placements > 0:
            _images = payload.get("images") or []
            _payload_count = len(_images) if isinstance(_images, list) else 0
            _linked_count = len(links)
            if _payload_count != _expected_placements or _linked_count != _expected_placements:
                return [{
                    "field": "images",
                    "code": "media_semantic_placement_count_drift",
                    "expected_placements": _expected_placements,
                    "payload_images": _payload_count,
                    "linked_media": _linked_count,
                    "message": (
                        f"Семантическая проверка подтверждала {_expected_placements} изображений, "
                        f"но сейчас в объявлении {_payload_count}, а связанных файлов {_linked_count}. "
                        "BORIS должен восстановить исходную проверенную галерею до публикации."
                    ),
                }]

    min_conf = float(guard.get("min_confidence") or 0.80)

    # BANNER_HERO_CANONICAL_BINDING_V1: an already QA-approved banner is part
    # of the publication contract. Detect when a later workflow silently drops
    # it from canonical hero position so local reconciliation can restore the
    # existing file without any new paid AI/provider call.
    _banner_qa = payload.get("banner_qa") or {}
    _banner_url = str(payload.get("banner_url") or "").strip()
    if bool(_banner_qa.get("passed")) and _banner_url:
        _banner_keys = _payload_local_image_keys({"images": [_banner_url]})
        _banner_key = str(_banner_keys.get(0) or "")
        if _banner_key:
            _hero_link, _hero_asset = links[0]
            _hero_key = str(getattr(_hero_asset, "storage_key", "") or "").lstrip("/")
            _hero_pos = int(getattr(_hero_link, "position", 0) or 0)
            if _hero_pos != 0 or _hero_key != _banner_key:
                return [{
                    "field": "images",
                    "code": "media_banner_hero_drift",
                    "media_id": int(getattr(_hero_asset, "id", 0) or 0),
                    "position": _hero_pos,
                    "expected_storage_key": _banner_key,
                    "linked_storage_key": _hero_key,
                    "message": "Проверенный баннер потерял первое место в галерее; BORIS должен восстановить существующий баннер без новой генерации",
                }]

    # Universal item-specific proof: exact title/description/category fingerprint + exact media/position.
    if mode in {"universal", "universal_text_image"}:
        fp = semantic_content_fingerprint(payload)
        evidence = payload.get("media_semantic_evidence") or []
        by_key = {(int(e.get("media_id") or 0), int(e.get("position") or 0)): e for e in evidence if isinstance(e, dict)}
        errors = []
        # MEDIA_LINK_PAYLOAD_BINDING_V1: semantic proof is valid only for the same
        # concrete image that the CampaignItem payload will publish at this position.
        _payload_keys = _payload_local_image_keys(payload)
        for link, asset in links:
            _pos = int(getattr(link, "position", 0) or 0)
            _expected_key = str(_payload_keys.get(_pos) or "")
            _linked_key = str(getattr(asset, "storage_key", "") or "").lstrip("/")
            if _expected_key and _linked_key != _expected_key:
                errors.append({
                    "field": "images",
                    "code": "media_semantic_link_payload_drift",
                    "media_id": int(getattr(asset, "id", 0) or 0),
                    "position": _pos,
                    "expected_storage_key": _expected_key,
                    "linked_storage_key": _linked_key,
                    "message": f"Фото {_pos+1}: фактическое изображение изменилось, требуется заново связать и проверить текущий файл",
                })
        if errors:
            return errors
        for link, asset in links:
            # GLOBAL AVITO CREATIVE QUALITY GATE. Legacy deterministic/uniqueness
            # renderers produced text-heavy, distorted, low-quality pseudo-banners.
            # They may stay in historical storage, but can never pass Feed Factory
            # publication again. Only owner uploads, stock assets that passed vision,
            # and current premium AI creatives are eligible.
            _provider=str(getattr(asset,"source_provider","") or "").strip().lower()
            if _provider in {"owner_approved","user_approved","approved"}:
                errors.append({"field":"images","code":"media_provenance_invalid","media_id":asset.id,"position":link.position,"provider":_provider,"message":f"Фото {link.position+1}: статус согласования ошибочно записан как источник файла"})
                continue
            if _provider in {"boris_deterministic","boris_unique"}:
                errors.append({"field":"images","code":"legacy_visual_provider_blocked","media_id":asset.id,"position":link.position,"provider":_provider,"message":f"Фото {link.position+1} создано устаревшим генератором низкого качества и запрещено для Avito"})
                continue
            # Current premium AI banner has its own stricter visual QA against
            # title/description + mobile readability + composition. Do not make it
            # pass a second generic subject classifier that is weaker for services.
            _banner_qa=payload.get("banner_qa") or {}
            _banner_url=str(payload.get("banner_url") or "")
            _asset_key=str(getattr(asset,"storage_key","") or "")
            _local_premium_banner=(
                _provider=="boris_local" and
                str(getattr(asset,"source_ref","") or "")=="campaign_stock_visual_local_text_v1" and
                str(payload.get("banner_visual_renderer") or "")=="approved_semantic_media" and
                str(payload.get("banner_text_renderer") or "")=="local_pillow"
            )
            if _provider == "openai_full_ai_premium":
                if _premium_banner_receipt_matches(row, payload, link, asset):
                    continue
                # UNREQUIRED_PREMIUM_HERO_AS_MEDIA_V1:
                # Some legacy campaigns generated Premium heroes before the
                # durable order later proved banners were not requested. Such an
                # asset may be treated as ordinary media only through the normal
                # exact item-specific semantic proof below; never through a stale
                # Premium receipt and never when banners are required.
                if payload.get("_order_banner_required") is not False:
                    errors.append({"field":"images","code":"premium_banner_receipt_invalid","media_id":asset.id,
                        "position":link.position,"message":"Текущий баннер не имеет действующего подтверждения QA для этого объявления"})
                    continue
            if _provider in {"openai","boris_local"} and _banner_qa.get("passed") is True and _asset_key and _asset_key in _banner_url and (_provider == "openai" or _local_premium_banner):
                continue
            e = by_key.get((int(asset.id), int(link.position)))
            if not e:
                errors.append({"field":"images","code":"media_semantic_proof_missing","media_id":asset.id,"position":link.position,"message":f"Фото {link.position+1} не проверено против названия и описания объявления"})
                continue
            if str(e.get("content_fingerprint") or "") != fp:
                errors.append({"field":"images","code":"media_semantic_proof_stale","media_id":asset.id,"position":link.position,"message":f"Фото {link.position+1}: проверка устарела после изменения названия/описания/категории"})
                continue
            try: conf = float(e.get("confidence") or 0)
            except Exception: conf = 0.0
            # Service-photo false-positive guard. For mortgage brokerage a generic
            # portrait/lifestyle frame is not valid ad evidence even if an older
            # vision verdict accidentally marked it as a match. Require the visible
            # subject label itself to carry mortgage/consultation/document context.
            _family=_semantic_family(payload)
            _subject=str(e.get("subject") or "")
            if _family=="mortgage_broker" and not _clear_subject_family_match(payload,_subject):
                errors.append({"field":"images","code":"media_semantic_subject_context_missing","media_id":asset.id,"position":link.position,"confidence":conf,"subject":_subject,"message":f"Фото {link.position+1}: нет видимого контекста ипотечной консультации/документов"})
                continue
            if not bool(e.get("match")):
                errors.append({"field":"images","code":"media_semantic_mismatch","media_id":asset.id,"position":link.position,"confidence":conf,"subject":e.get("subject"),"message":f"Фото {link.position+1} не соответствует тому, что продаётся в объявлении"})
            elif conf < min_conf:
                errors.append({"field":"images","code":"media_semantic_low_confidence","media_id":asset.id,"position":link.position,"confidence":conf,"message":f"Фото {link.position+1} проверено недостаточно уверенно"})
        return errors

    # Backward-compatible furniture class guard.
    expected = str(guard.get("expected_class") or expected_class(payload, row) or "").strip()
    if not expected:
        return [{"field":"images","code":"media_semantic_expected_missing","message":"Не задан ожидаемый тип объекта для проверки фотографий"}]
    errors=[]
    for link,asset in links:
        actual=_tag_value(asset.tags,"semantic_class:")
        confidence=_tag_value(asset.tags,"semantic_confidence:")
        reviewed=any(str(t)=="semantic_reviewed" for t in (asset.tags or []))
        try: conf=float(confidence)
        except Exception: conf=0.0
        if not reviewed or not actual:
            errors.append({"field":"images","code":"media_semantic_unreviewed","media_id":asset.id,"position":link.position,"message":f"Фото {link.position+1} не прошло независимую визуальную проверку"})
        elif actual != expected:
            errors.append({"field":"images","code":"media_semantic_mismatch","media_id":asset.id,"position":link.position,"expected":expected,"actual":actual,"message":f"Фото {link.position+1} не соответствует типу объекта объявления"})
        elif conf < min_conf:
            errors.append({"field":"images","code":"media_semantic_low_confidence","media_id":asset.id,"position":link.position,"confidence":conf,"message":f"Фото {link.position+1} классифицировано недостаточно уверенно"})
    return errors


def _image_data_uri(asset, *, max_side: int = 384, blur_radius: float = 3.5) -> str:
    import base64
    from app.services.media_storage import abs_path
    from app.services.ai_privacy import sanitize_external_reference_image_bytes
    with open(abs_path(asset.storage_key), "rb") as fh:
        safe_bytes, _mime = sanitize_external_reference_image_bytes(
            fh.read(), max_side=max_side, blur_radius=blur_radius
        )
    return "data:image/jpeg;base64,"+base64.b64encode(safe_bytes).decode()


def classify_asset_vision(asset, account_id: str | None = None) -> dict:
    """Legacy generic furniture classifier; retained for existing strict guards."""
    data=_image_data_uri(asset)
    prompt=("Classify the visibly dominant furniture/interior subject. Return strict JSON only: "
            "{\"class\":\"kitchen|hallway_entryway|walk_in_wardrobe|bathroom_furniture|other_ambiguous\",\"confidence\":0..1,\"reason\":\"short\"}. "
            "Kitchen requires clear kitchen cabinetry/counter/appliance context. Hallway requires entrance/hall furniture such as shoe cabinet, bench, coat storage or hallway cabinet. "
            "Walk-in wardrobe requires closet/dressing-room shelving or hanging storage. Bathroom furniture requires vanity/cabinet/sink storage. Generic room/shelves/corridor => other_ambiguous. Be conservative.")
    acc=str(account_id or getattr(asset,"account_id","") or "").strip()
    out=_vision_chat_json(
        acc,
        "media_semantic_asset_vision",
        [{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":data,"detail":"low"}}],
        max_tokens=180,
        timeout=90,
    )
    return {"class":str(out.get("class") or "other_ambiguous"),"confidence":float(out.get("confidence") or 0),"reason":str(out.get("reason") or "")}


def _semantic_family(payload: dict) -> str:
    text=" ".join(str(payload.get(k) or "") for k in ("title","direction","offer","category","category_path")).lower().replace("ё","е")
    if "кух" in text: return "kitchen"
    if any(x in text for x in ("прихож", "обувниц")): return "hallway_furniture"
    if any(x in text for x in ("гардероб", "шкаф", "система хранения")): return "wardrobe"
    if "ванн" in text and any(x in text for x in ("мебел", "тумб", "шкаф", "гарнитур")): return "bathroom_furniture"
    if any(x in text for x in ("ремонт фар", "фары", "фар ")): return "headlight"
    if any(x in text for x in ("аренда авто", "аренда toyota", "аренда camry", "авто с выкупом", "машин с выкупом")): return "car_rental"
    # Plastering must win before the generic token "покраск" used by car body
    # repair. Ads like "штукатурка стен под покраску" are plastering services,
    # not automotive paint jobs.
    if any(x in text for x in ("штукатур", "plaster")): return "plastering_service"
    if any(x in text for x in ("кузов", "покраск", "автосервис")): return "car_repair"
    if any(x in text for x in ("тротуар", "брусчат", "плитк")) and not "ванн" in text: return "paving"
    if any(x in text for x in ("электросамокат", "самокат")): return "scooter"

    # GLAZING_SEMANTIC_FAMILY_V1:
    # Keep window/glazing families deliberately narrow. Campaign 512 proved that
    # sibling copy variants reuse the same verified assets many times; without
    # families every variant repurchased Vision. Never collapse doors, balconies,
    # facades, partitions, terrace systems and generic windows into one family.
    if "входн" in text and "групп" in text:
        return "glazing_entrance_group"
    if "алюминиев" in text and "двер" in text:
        return "glazing_aluminum_door"
    if "алюминиев" in text and "перегород" in text:
        return "glazing_aluminum_partition"
    if ("террас" in text or "веранд" in text or "бесед" in text) and "раздвиж" in text:
        return "glazing_terrace_sliding"
    if "замен" in text and "холод" in text and "тепл" in text and "остекл" in text:
        return "glazing_cold_to_warm"
    if "балкон" in text or "лоджи" in text:
        return "glazing_balcony"
    if "фасад" in text or "витраж" in text:
        return "glazing_facade_storefront"
    if any(x in text for x in ("магазин", "офис", "кафе", "салон")) and "остекл" in text:
        return "glazing_commercial_storefront"
    if "тепл" in text and "алюминиев" in text and "остекл" in text:
        return "glazing_warm_aluminum"
    if "нестандарт" in text or "больших размеров" in text or "большие проем" in text:
        return "glazing_large_custom"
    if any(x in text for x in ("застройщик", "подрядчик", "архитектор")) and "остекл" in text:
        return "glazing_b2b_project"
    if "панорам" in text or "окна в пол" in text:
        return "glazing_panoramic"
    if ("окн" in text or "остекл" in text) and any(x in text for x in ("пвх", "алюмин", "окна", "окно")):
        return "glazing_window_general"

    # Mortgage/loan brokerage is one semantic family across geo/title variants.
    # This lets a paid, positive vision proof for the SAME asset be reused across
    # sibling mortgage cards while a copy/category change still invalidates the
    # exact per-item fingerprint. Generic finance imagery is not accepted here;
    # the visible-subject check below remains conservative.
    if any(x in text for x in ("ипотек", "ипотечн", "кредит", "рефинанс")): return "mortgage_broker"
    return ""


def _clear_subject_family_match(payload: dict, subject: str) -> bool:
    """Second conservative bilingual check against the model's visible-subject label.

    This corrects false negatives such as subject='kitchen with wooden cabinets'
    for an ad 'Кухни на заказ', but does NOT accept generic rooms for furniture ads.
    """
    fam=_semantic_family(payload); s=str(subject or "").lower().replace("ё","е")
    if not fam or not s: return False
    checks={
        "kitchen": lambda x: "kitchen" in x and any(k in x for k in ("cabinet","counter","island","kitchenette","кух")),
        "hallway_furniture": lambda x: any(k in x for k in ("hallway cabinet","entryway cabinet","shoe cabinet","coat storage","coat rack","hallway furniture","bench and mirror","прихож","обувниц")),
        "wardrobe": lambda x: any(k in x for k in ("wardrobe","closet","walk-in","dressing room","clothing storage","shelving and hanging","гардероб","шкаф")),
        "bathroom_furniture": lambda x: ("bathroom" in x or "ванн" in x) and any(k in x for k in ("vanity","cabinet","storage","sink cabinet","тумб","шкаф","мебел")),
        "headlight": lambda x: any(k in x for k in ("headlight","car light","lamp assembly","фара")),
        "car_rental": lambda x: any(k in x for k in ("car", "vehicle", "automobile", "interior", "dashboard", "seat", "sunroof", "trunk", "wheel", "toyota", "camry", "автомоб", "машин", "салон", "панел", "сиден", "люк", "багаж")),
        "car_repair": lambda x: any(k in x for k in ("car body","body repair","paint booth","auto repair","vehicle repair","кузов")),
        "paving": lambda x: any(k in x for k in ("paving","paver","paving stone","paving tile","брусчат","тротуар")),
        "scooter": lambda x: any(k in x for k in ("electric scooter","e-scooter","scooter","электросамокат")),

        # Conservative visible-subject checks for glazing families. A positive
        # asset cache is stamped only when both the model verdict is positive and
        # this subject text clearly names the sold object family.
        "glazing_entrance_group": lambda x: any(k in x for k in (
            "entrance group","entry group","storefront entrance","glass entrance","entrance door system",
            "входная группа","входн"
        )),
        "glazing_aluminum_door": lambda x: (
            any(k in x for k in ("aluminum","aluminium","алюминиев","алюминие"))
            and any(k in x for k in ("door","двер"))
        ),
        "glazing_aluminum_partition": lambda x: any(k in x for k in (
            "aluminum partition","aluminium partition","glass partition","office partition","перегород"
        )),
        "glazing_terrace_sliding": lambda x: any(k in x for k in (
            "sliding glass","sliding glazing","sliding door","terrace glazing","veranda glazing","patio glazing",
            "раздвиж","террас","веранд","бесед"
        )),
        "glazing_balcony": lambda x: any(k in x for k in (
            "balcony glazing","balcony window","balcony enclosure","loggia glazing","balcony","loggia","балкон","лоджи"
        )),
        "glazing_cold_to_warm": lambda x: (
            any(k in x for k in ("balcony","loggia","glazing","балкон","лоджи","остекл"))
            and any(k in x for k in ("warm","insulated","thermal","тепл","утепл"))
        ),
        "glazing_facade_storefront": lambda x: any(k in x for k in (
            "glass facade","facade glazing","curtain wall","storefront glazing","shopfront glazing",
            "витраж","фасад","glass wall"
        )),
        "glazing_commercial_storefront": lambda x: any(k in x for k in (
            "storefront","shopfront","office glazing","commercial glazing","cafe glazing","salon glazing",
            "glass facade","витраж","остекл"
        )),
        "glazing_warm_aluminum": lambda x: (
            any(k in x for k in ("aluminum","aluminium","алюмин"))
            and any(k in x for k in ("window","glazing","glass","окн","остекл"))
        ),
        "glazing_large_custom": lambda x: any(k in x for k in (
            "large window","oversized window","custom window","large glazing","custom glazing",
            "large glass","нестандарт","больш"
        )),
        "glazing_b2b_project": lambda x: any(k in x for k in (
            "commercial glazing","project glazing","building facade","curtain wall","architectural glazing",
            "facade glazing","storefront","витраж","фасад","остекл"
        )),
        "glazing_panoramic": lambda x: any(k in x for k in (
            "panoramic window","floor-to-ceiling window","floor to ceiling window",
            "floor-to-ceiling glazing","large glass wall","панорам","окна в пол","floor to ceiling"
        )),
        "glazing_window_general": lambda x: any(k in x for k in (
            "window","windows","glazing","glass window","окон","окно","остекл"
        )),

        "plastering_service": lambda x: any(k in x for k in ("plaster", "plastering", "штукатур")),
        "mortgage_broker": lambda x: (
            any(k in x for k in (
                "mortgage broker", "mortgage consultation", "mortgage meeting",
                "home loan", "loan consultation", "loan documents",
                "contract discussion", "financial documents",
                "real estate consultation", "realtor meeting",
                "person offering consultation", "person offering help",
                "consultation meeting", "client consultation", "service steps",
                "mortgage concept", "home financing", "property documents",
                "ипотеч", "кредит", "документ", "договор", "консультац"
            ))
            or (
                any(k in x for k in (
                    "consult", "discuss", "review", "analysis", "analyz",
                    "meeting", "broker", "clipboard"
                ))
                and any(k in x for k in (
                    "document", "financial", "mortgage", "loan", "refinanc",
                    "property", "apartment", "building", "home", "real estate"
                ))
            )
        ),
    }
    fn=checks.get(fam)
    return bool(fn and fn(s))


def _asset_family_cache(asset) -> tuple[str, float, str]:
    family=""; conf=0.0; subject=""
    tags=[str(x or "") for x in (getattr(asset,"tags",None) or [])]
    for s in tags:
        if s.startswith("vision_family_confirmed:"):
            family=s.split(":",1)[1].strip()
        elif s.startswith("vision_family_confidence:"):
            try: conf=float(s.split(":",1)[1])
            except Exception: conf=0.0
        elif s.startswith("vision_subject:"):
            subject=s.split(":",1)[1].strip()
    # LEGACY_SEMANTIC_CLASS_FAMILY_CACHE_V1: old independent asset QA used
    # semantic_class:* before the universal family cache existed. Reuse only
    # explicit positive reviewed classes at >=0.80; ambiguous/unknown classes
    # never become positive evidence. This prevents repeated paid vision calls.
    if not family and "semantic_reviewed" in tags:
        legacy_class=""; legacy_conf=0.0; legacy_subject=""
        for s in tags:
            if s.startswith("semantic_class:"): legacy_class=s.split(":",1)[1].strip()
            elif s.startswith("semantic_confidence:"):
                try: legacy_conf=float(s.split(":",1)[1])
                except Exception: legacy_conf=0.0
            elif s.startswith("semantic_reason:"): legacy_subject=s.split(":",1)[1].strip()
        aliases={
            "kitchen":"kitchen",
            "entryway":"hallway_furniture",
            "hallway_entryway":"hallway_furniture",
            "walk_in_wardrobe":"wardrobe",
            "wardrobe":"wardrobe",
            "bathroom_furniture":"bathroom_furniture",
        }
        mapped=aliases.get(legacy_class,"")
        if mapped and legacy_conf>=0.80:
            family=mapped; conf=legacy_conf; subject=legacy_subject or legacy_class.replace("_"," ")
    return family,conf,subject


def _stamp_asset_family_cache(asset, family: str, confidence: float, subject: str) -> None:
    if not family or float(confidence or 0) < 0.80:
        return
    tags=[str(t) for t in (getattr(asset,"tags",None) or []) if not str(t).startswith(("vision_family_confirmed:","vision_family_confidence:","vision_subject:"))]
    tags += [f"vision_family_confirmed:{family}",f"vision_family_confidence:{float(confidence):.3f}",("vision_subject:"+str(subject or "")[:180])]
    asset.tags=tags


SEMANTIC_POLICY_VERSION = "20261006_glazing_family_v1"


def classify_item_media_vision(payload: dict, linked_media: list, account_id: str | None = None, provenance: dict | None = None, allow_provider: bool = True) -> list[dict]:
    """Compare actual images directly with title+description+category for ANY category/account.

    Exact current evidence is a durable cache: same content fingerprint, same
    media id and same position must never buy the same vision proof twice.
    Positive legacy proofs remain reusable. Negative/low-confidence proofs are
    reused only when they were produced by the CURRENT semantic policy, so a QA
    policy improvement can repair an old false negative exactly once instead of
    poisoning the asset forever or paying on every retry.
    """
    fp = semantic_content_fingerprint(payload)
    existing = payload.get("media_semantic_evidence") or []
    # Per-card negative memory: once Vision has rejected a concrete asset for
    # this exact semantic fingerprint, later autonomous stock retries must not
    # pay to reconsider or silently relink the same bad image.
    rejected_ids=set()
    for raw in (payload.get("media_semantic_rejections") or []):
        if not isinstance(raw,dict) or str(raw.get("content_fingerprint") or "")!=fp:
            continue
        try: rejected_ids.add(int(raw.get("media_id") or 0))
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=765)
    by_key = {}
    for e in existing:
        if not isinstance(e, dict):
            continue
        if str(e.get("content_fingerprint") or "") != fp:
            continue
        if str(e.get("checker") or "") not in {"openai_vision_text_image", "openai_vision_text_image_cache", "openai_vision_family_cache"}:
            continue
        try:
            _conf=float(e.get("confidence") or 0)
        except Exception:
            _conf=0.0
        _positive=bool(e.get("match")) and _conf>=0.80
        if (not _positive) and str(e.get("policy_version") or "") != SEMANTIC_POLICY_VERSION:
            continue
        try:
            key = (int(e.get("media_id") or 0), int(e.get("position") or 0))
        except Exception:
            continue
        by_key[key] = e
    cached_by_key = {}
    pending = []
    family=_semantic_family(payload)
    for link, asset in linked_media:
        key = (int(asset.id), int(link.position))
        if int(asset.id) in rejected_ids:
            cached_by_key[key]={"match":False,"confidence":0.0,"subject":"previously rejected asset","reason":"reused per-card negative semantic memory","cached":True,"checker":"openai_vision_negative_cache"}
            continue
        e = by_key.get(key)
        if e:
            cached_by_key[key] = {
                "match": bool(e.get("match")),
                "confidence": float(e.get("confidence") or 0),
                "subject": str(e.get("subject") or "")[:240],
                "reason": str(e.get("reason") or "")[:320],
                "cached": True,
                "checker": str(e.get("checker") or "openai_vision_text_image_cache"),
            }
            continue
        cached_family,cached_conf,cached_subject=_asset_family_cache(asset)
        if family and cached_family==family and cached_conf>=0.80:
            cached_by_key[key]={
                "match":True,"confidence":cached_conf,"subject":cached_subject[:240],
                "reason":"reused paid vision proof for the same semantic family",
                "cached":True,"checker":"openai_vision_family_cache",
            }
            continue
        pending.append((link, asset))
    if not pending:
        return [cached_by_key[(int(asset.id), int(link.position))] for link, asset in linked_media]
    if not bool(allow_provider):
        # COST_ANOMALY_CACHE_ONLY_V1: preserve the budget circuit. Reuse every
        # existing exact/family proof, but represent unresolved placements as a
        # deterministic non-PASS instead of invoking Vision. The caller can try a
        # different already-proven asset or keep only this item in the repair set.
        blocked_keys={(int(asset.id),int(link.position)) for link,asset in pending}
        out=[]
        for link,asset in linked_media:
            key=(int(asset.id),int(link.position))
            if key in cached_by_key:
                out.append(cached_by_key[key])
            elif key in blocked_keys:
                out.append({"match":False,"confidence":0.0,"subject":"","reason":"paid semantic QA blocked by campaign cost anomaly; no reusable proof exists","cached":False,"checker":"cost_anomaly_cache_only"})
            else:
                out.append({"match":False,"confidence":0.0,"subject":"","reason":"semantic proof unavailable in cache-only mode","cached":False,"checker":"cost_anomaly_cache_only"})
        return out

    title=str(payload.get("title") or "").strip()
    desc=str(payload.get("description") or "").strip()
    category=str(payload.get("category_path") or payload.get("category") or "").strip()
    direction=str(payload.get("direction") or payload.get("offer") or "").strip()
    prompt=("You are a strict advertising photo QA. Decide whether EACH image clearly matches what this ad sells. "
            "The ad text is the semantic source of truth. Do not trust file names, search queries or existing tags. "
            "For physical goods reject related-but-different products, wrong room/category, generic interiors where the sold object is not clearly visible, and ambiguous images. "
            "IMPORTANT CLADDING RULE: when the verified ad explicitly sells a facade/exterior CLADDING PANEL whose product name or description says brick, clinker brick, aged brick, stone, wild stone or slate texture, a close-up product sample may visually resemble real masonry. Accept such a close-up when it is consistent with the stated panel texture; do NOT reject it merely because the visible surface looks like brick/stone/slate. Also accept a completed facade where that cladding is the clearly relevant finish. Still reject unrelated masonry/building-material categories when the ad does not sell that cladding-panel texture. "
            "For made-to-order glass shower products, judge the visibly sold glass structure, not incidental bathroom fixtures: a clear fixed glass shower partition/panel may match a shower-partition ad even when photographed inside a complete bathroom or shower area. However, when the ad explicitly says WITHOUT DOORS / fixed partition / walk-in panel, reject images whose dominant sold object is a closed shower cabin or clearly hinged/sliding shower door. "
            "For INTANGIBLE SERVICES, do not require a photograph to literally show the service being performed. Accept a clear professional visual representation of the advertised service context (for example mortgage/home-loan consultation, relevant documents, apartment/home purchase discussion, broker-client meeting) when it is semantically aligned with the ad; reject unrelated lifestyle scenes or a context belonging to another service. "
            "For made-to-order furniture, a finished real example of the exact sold furniture type IS a valid match even when staged inside an interior: "
            "kitchen cabinetry/counter/island for a custom-kitchen ad; hallway cabinet/shoe cabinet/bench/coat storage for hallway furniture; "
            "closet/wardrobe/shelving/hanging storage for wardrobe ads; bathroom vanity/cabinet/sink storage for bathroom-furniture ads. "
            "A generic room without the sold object is still NOT a match. "
            "For vehicle rental/sale ads, a gallery is allowed to show useful parts of the same type of vehicle: exterior, cabin/interior, dashboard, seats, sunroof, trunk, wheels, lights and close-up vehicle details. Do NOT reject a valid vehicle interior/detail merely because the full exterior is not visible. Reject unrelated vehicles or non-vehicle images. "
            "Return strict JSON array, one object per image in order: {\"index\":1,\"match\":true,\"confidence\":0.0,\"subject\":\"what is visibly shown\",\"reason\":\"short\"}. "
            f"TITLE: {title}\nCATEGORY: {category}\nDIRECTION/OFFER: {direction}\nDESCRIPTION: {desc[:5000]}")
    acc=str(account_id or (getattr(pending[0][1],"account_id","") if pending else "") or "").strip()
    # Large multi-image requests are truncated by some OpenAI-compatible vision
    # providers. Keep each paid intent bounded and deterministic, then concatenate
    # verdicts in the original placement order. Every chunk must match its exact
    # image count; a short/malformed chunk still fails closed.
    out=[]
    _chunk_size=4
    for _chunk_start in range(0,len(pending),_chunk_size):
        _chunk=pending[_chunk_start:_chunk_start+_chunk_size]
        content=[{"type":"text","text":prompt}]
        for _local_idx,(_,asset) in enumerate(_chunk,1):
            content.append({"type":"text","text":f"IMAGE {_local_idx}"})
            content.append({"type":"image_url","image_url":{"url":_image_data_uri(asset),"detail":"low"}})
        _chunk_provenance=dict(provenance or {})
        _chunk_provenance.update({"vision_chunk_start":int(_chunk_start),"vision_chunk_size":len(_chunk)})
        try:
            _chunk_out=_vision_chat_json(acc,"media_semantic_item_vision",content,max_tokens=1200,timeout=120,provenance=_chunk_provenance)
        except RuntimeError as _chunk_exc:
            # VISION_BAD_JSON_RECOVERY_V1:
            # HTTP-200 with malformed model JSON is a definitive paid provider
            # outcome, not a transport ambiguity. Do not repeat that economic
            # intent. Instead enter the already-supported one-image recovery path
            # below under a distinct operation/idempotency identity.
            _chunk_msg=str(_chunk_exc or "")
            if (_chunk_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_bad_result_json")
                    or _chunk_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_empty_result")):
                _chunk_out=None
            else:
                raise
        if not isinstance(_chunk_out,list) or len(_chunk_out)!=len(_chunk):
            # VISION_SHAPE_RECOVERY_V1:
            # A definitive HTTP-200 semantic response can still be malformed for
            # multi-image prompts on OpenAI-compatible fallback providers. This is
            # not transport ambiguity, so a second bounded economic intent is safe.
            # Recover one image at a time with a stricter output contract. Never
            # accept/coerce the malformed batch and never bypass fail-closed QA.
            _recovered=[]
            for _recover_idx,(_, _recover_asset) in enumerate(_chunk,1):
                _recover_prompt=(
                    prompt
                    + "\nRECOVERY CONTRACT: There is EXACTLY ONE image in this request. "
                    + "Return ONLY a JSON array containing EXACTLY ONE object. "
                    + "The object must have index=1, match boolean, confidence number 0..1, "
                    + "subject string, reason string. No markdown, no wrapper object, no prose."
                )
                _recover_content=[
                    {"type":"text","text":_recover_prompt},
                    {"type":"text","text":"IMAGE 1"},
                    {"type":"image_url","image_url":{"url":_image_data_uri(_recover_asset),"detail":"low"}},
                ]
                _recover_provenance=dict(_chunk_provenance)
                _recover_provenance.update({
                    "vision_shape_recovery":True,
                    "vision_recovery_local_index":int(_recover_idx),
                    "vision_recovery_global_index":int(_chunk_start+_recover_idx),
                })
                _recover_operation=(
                    "media_semantic_item_vision_bad_json_recovery_v1"
                    if _chunk_out is None
                    else "media_semantic_item_vision_shape_recovery_v1"
                )
                try:
                    _one=_vision_chat_json(
                        acc,_recover_operation,
                        _recover_content,max_tokens=500,timeout=120,
                        provenance=_recover_provenance,
                    )
                except RuntimeError as _one_json_exc:
                    _one_json_msg=str(_one_json_exc or "")
                    if (_one_json_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_bad_result_json")
                            or _one_json_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_empty_result")):
                        # VISION_SINGLE_BAD_JSON_RECOVERY_V2:
                        # The first one-image recovery itself returned malformed
                        # HTTP-200 JSON. Retry only as a distinct stricter intent;
                        # never coerce malformed text into a semantic PASS.
                        _recover_content_v2=list(_recover_content)+[{"type":"text","text":
                            "FINAL JSON RECOVERY: Return one JSON object only, not an array. "
                            "Required keys: match boolean, confidence number 0..1, subject string, reason string. "
                            "No markdown, no prose, no code fence."}]
                        _recover_provenance_v2=dict(_recover_provenance)
                        _recover_provenance_v2["vision_single_bad_json_recovery_v2"]=True
                        _one=_vision_chat_json(
                            acc,"media_semantic_item_vision_bad_json_recovery_v2",
                            _recover_content_v2,max_tokens=350,timeout=120,
                            provenance=_recover_provenance_v2,
                        )
                    else:
                        raise
                # Some OpenAI-compatible fallbacks obey the one-image semantic
                # contract but return the single verdict object directly instead
                # of wrapping it in a one-element JSON array. This is unambiguous:
                # normalize only a dict that has the required verdict fields.
                if isinstance(_one,dict) and {"match","confidence"}.issubset(set(_one)):
                    _one=[_one]
                if not isinstance(_one,list) or len(_one)!=1 or not isinstance(_one[0],dict):
                    raise RuntimeError(
                        "vision semantic recovery verdict mismatch: "
                        f"{len(_one) if isinstance(_one,list) else 'not-list'} != 1"
                    )
                _recovered.append(_one[0])
            _chunk_out=_recovered
        out.extend(_chunk_out)
    # Conservative second pass against the visible-subject label. It only repairs
    # obvious false negatives; it never turns a generic/ambiguous room into a match.
    for verdict in out:
        if not isinstance(verdict,dict):
            continue
        subject=str(verdict.get("subject") or "")
        verdict["checker"]="openai_vision_text_image"
        if _clear_subject_family_match(payload,subject):
            try: conf=float(verdict.get("confidence") or 0)
            except Exception: conf=0.0
            if not bool(verdict.get("match")) or conf < 0.80:
                verdict["match"]=True
                verdict["confidence"]=max(conf,0.90)
                verdict["reason"]=(str(verdict.get("reason") or "")+"; clear visible subject matches sold object family")[:320]
    # Persist a conservative asset-level positive proof. It is reusable only for
    # the exact same semantic family; different families still require vision QA.
    if family:
        for (_,asset),verdict in zip(pending,out):
            try: conf=float(verdict.get("confidence") or 0)
            except Exception: conf=0.0
            subject=str(verdict.get("subject") or "")
            if bool(verdict.get("match")) and conf>=0.80 and _clear_subject_family_match(payload,subject):
                _stamp_asset_family_cache(asset,family,conf,subject)
    fresh_iter=iter(out)
    merged=[]
    for link,asset in linked_media:
        key=(int(asset.id),int(link.position))
        merged.append(cached_by_key.get(key) or next(fresh_iter))
    return merged




def semantic_family_from_source_ref(source_ref: str) -> str:
    """Map BORIS-owned Campaign 512 generation provenance to semantic family."""
    ref=str(source_ref or "").strip().lower()
    if ref.startswith("campaign512_partitions_v4:"):
        return "glazing_aluminum_partition"
    if not ref.startswith(("campaign512_massmarket_v2:","campaign512_massmarket_v3:")):
        return ""
    parts=ref.split(":")
    slug=parts[1] if len(parts)>1 else ""
    mapping={
        "panoramic_home":"glazing_panoramic",
        "panoramic":"glazing_panoramic",
        "floor_windows":"glazing_panoramic",
        "floor":"glazing_panoramic",
        "balcony_loggia":"glazing_balcony",
        "balcony":"glazing_balcony",
        "terrace_sliding":"glazing_terrace_sliding",
        "terrace":"glazing_terrace_sliding",
        "commercial":"glazing_commercial_storefront",
        "facade":"glazing_facade_storefront",
        "warm_aluminum":"glazing_warm_aluminum",
        "cold_to_warm":"glazing_cold_to_warm",
        "large_custom":"glazing_large_custom",
        "b2b":"glazing_b2b_project",
        "partitions":"glazing_aluminum_partition",
        "entrance":"glazing_entrance_group",
        "aluminum_doors":"glazing_aluminum_door",
    }
    return mapping.get(slug,"")


def backfill_asset_family_cache_from_provenance(db, account_id: str, limit: int = 3000) -> dict:
    """Reuse BORIS-owned deterministic generation provenance without provider QA."""
    from app.models.media_asset import MediaAsset
    aid=str(account_id or "").strip()
    if not aid:
        return {"scanned":0,"stamped":0}
    rows=(db.query(MediaAsset)
          .filter(MediaAsset.account_id==aid,
                  MediaAsset.deleted_at.is_(None),
                  MediaAsset.status=="approved",
                  MediaAsset.source_provider.in_(["openai_photo","openai_photo_v3"]),
                  MediaAsset.source_ref.like("campaign512_%"))
          .order_by(MediaAsset.id.asc())
          .limit(max(1,min(int(limit or 3000),10000))).all())
    stamped=0; reused=0; conflicts=0; unmapped=0
    for asset in rows:
        family=semantic_family_from_source_ref(asset.source_ref)
        if not family:
            unmapped+=1
            continue
        existing_family,existing_conf,_existing_subject=_asset_family_cache(asset)
        if existing_family and existing_family!=family:
            conflicts+=1
            continue
        if existing_family==family and existing_conf>=1.0:
            reused+=1
            continue
        _stamp_asset_family_cache(asset,family,1.0,"provenance:"+str(asset.source_ref or "")[:160])
        stamped+=1
    if stamped:
        db.flush()
    return {
        "scanned":len(rows),"stamped":stamped,"reused":reused,
        "conflicts":conflicts,"unmapped":unmapped,
    }

def reusable_family_cache_candidate(payload: dict, evidence: dict):
    """Return a conservative asset-family proof candidate from old item evidence."""
    if not isinstance(payload,dict) or not isinstance(evidence,dict):
        return None
    if evidence.get("match") is not True:
        return None
    checker=str(evidence.get("checker") or "")
    if checker not in {
        "openai_vision_text_image",
        "openai_vision_text_image_cache",
        "openai_vision_family_cache",
    }:
        return None
    try:
        confidence=float(evidence.get("confidence") or 0)
        media_id=int(evidence.get("media_id") or 0)
    except (TypeError,ValueError):
        return None
    subject=str(evidence.get("subject") or "").strip()
    family=_semantic_family(payload)
    if media_id<=0 or confidence<0.80 or not family or not subject:
        return None
    if not _clear_subject_family_match(payload,subject):
        return None
    return {
        "media_id":media_id,
        "family":family,
        "confidence":confidence,
        "subject":subject[:180],
    }


def backfill_asset_family_cache_from_evidence(db, account_id: str, campaign_ids=None, limit: int = 3000) -> dict:
    """Promote existing positive item proofs into reusable asset-family cache.

    This performs no provider calls. It only reuses evidence that already passed
    the same conservative visible-subject family check used for new Vision PASSes.
    Existing conflicting asset-family cache is never overwritten.
    """
    import json
    from app.models.campaign_item import CampaignItem
    from app.models.media_asset import MediaAsset

    aid=str(account_id or "").strip()
    if not aid:
        return {"scanned":0,"candidates":0,"stamped":0}
    q=(db.query(CampaignItem)
       .filter(CampaignItem.account_id==aid,
               CampaignItem.status!="superseded")
       .order_by(CampaignItem.id.desc()))
    ids=[]
    for raw in (campaign_ids or []):
        try:
            cid=int(raw)
        except (TypeError,ValueError):
            cid=0
        if cid>0 and cid not in ids:
            ids.append(cid)
    if ids:
        q=q.filter(CampaignItem.campaign_id.in_(ids))
    rows=q.limit(max(1,min(int(limit or 3000),10000))).all()
    candidates={}
    for row in rows:
        raw=getattr(row,"payload_json",None)
        if isinstance(raw,str):
            try: payload=json.loads(raw or "{}")
            except Exception: payload={}
        elif isinstance(raw,dict):
            payload=dict(raw)
        else:
            payload={}
        for evidence in (payload.get("media_semantic_evidence") or []):
            candidate=reusable_family_cache_candidate(payload,evidence)
            if not candidate:
                continue
            mid=int(candidate["media_id"])
            current=candidates.get(mid)
            if current is None or float(candidate["confidence"])>float(current["confidence"]):
                candidates[mid]=candidate
    if not candidates:
        return {"scanned":len(rows),"candidates":0,"stamped":0}
    assets=(db.query(MediaAsset)
            .filter(MediaAsset.account_id==aid,
                    MediaAsset.id.in_(sorted(candidates)),
                    MediaAsset.deleted_at.is_(None),
                    MediaAsset.status=="approved")
            .all())
    stamped=0; conflicts=0; reused=0
    for asset in assets:
        candidate=candidates.get(int(asset.id))
        if not candidate:
            continue
        existing_family,existing_conf,_existing_subject=_asset_family_cache(asset)
        if existing_family and existing_family!=candidate["family"]:
            conflicts+=1
            continue
        if existing_family==candidate["family"] and existing_conf>=float(candidate["confidence"]):
            reused+=1
            continue
        _stamp_asset_family_cache(
            asset,candidate["family"],float(candidate["confidence"]),candidate["subject"]
        )
        stamped+=1
    if stamped:
        db.flush()
    return {
        "scanned":len(rows),
        "candidates":len(candidates),
        "stamped":stamped,
        "reused":reused,
        "conflicts":conflicts,
    }

def stamp_item_semantic_evidence(payload: dict, linked_media: list, verdicts: list[dict]) -> None:
    import datetime
    fp=semantic_content_fingerprint(payload); now=datetime.datetime.now(datetime.timezone.utc).isoformat(); evidence=[]
    for (link,asset), verdict in zip(linked_media,verdicts):
        evidence.append({"media_id":int(asset.id),"position":int(link.position),"content_fingerprint":fp,"match":bool(verdict.get("match")),"confidence":float(verdict.get("confidence") or 0),"subject":str(verdict.get("subject") or "")[:240],"reason":str(verdict.get("reason") or "")[:320],"checked_at":now,"checker":str(verdict.get("checker") or "openai_vision_text_image")[:64],"policy_version":SEMANTIC_POLICY_VERSION})
    payload["media_semantic_guard"]={"mode":"universal_text_image","min_confidence":0.80,"source_of_truth":"title+description+category+direction"}
    payload["media_semantic_evidence"]=evidence


def stamp_semantic_review(asset, verdict: dict) -> None:
    tags=[str(t) for t in (asset.tags or []) if not str(t).startswith(("semantic_class:","semantic_confidence:","semantic_reason:")) and str(t)!="semantic_reviewed"]
    tags += ["semantic_reviewed",f"semantic_class:{verdict['class']}",f"semantic_confidence:{float(verdict['confidence']):.2f}",(f"semantic_reason:{verdict.get('reason','')}")[:240]]
    asset.tags=tags


def _banner_qa_cache_path(payload: dict, banner_asset, reference_asset, account_id: str) -> tuple[str,str]:
    import hashlib, json, os
    from app.services.media_storage import abs_path

    def _artifact_sha(asset):
        if asset is None:
            return ""
        key=str(getattr(asset,"storage_key","") or "")
        try:
            h=hashlib.sha256()
            with open(abs_path(key),"rb") as fh:
                for chunk in iter(lambda: fh.read(1024*1024),b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            # Storage key remains a conservative fallback for legacy/missing files.
            return "key:"+key

    basis={
        "policy":"campaign_banner_vision_qa_v8_content_sha_bottom_caption",
        "public_artifact_preprocess":"sharp_768_no_blur_v1",
        "account_id":str(account_id or ""),
        "content_fingerprint":semantic_content_fingerprint(payload),
        "banner_storage_key":str(getattr(banner_asset,"storage_key","") or ""),
        "banner_artifact_sha256":_artifact_sha(banner_asset),
        "reference_storage_key":str(getattr(reference_asset,"storage_key","") or "") if reference_asset is not None else "",
        "reference_artifact_sha256":_artifact_sha(reference_asset),
    }
    digest=hashlib.sha256(json.dumps(basis,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()
    root=os.environ.get("BORIS_VISION_QA_CACHE_DIR","/root/BORIS/backend/.vision_qa_cache/banner")
    return os.path.join(root,digest+".json"),digest


def _validate_campaign_banner_vision_locked(payload: dict, banner_asset, reference_asset=None, account_id: str | None = None, provenance: dict | None = None) -> dict:
    """Fail-closed QA for generated campaign banners: identity, copy integrity, mobile readability."""
    import json, os, tempfile
    acc=str(account_id or getattr(banner_asset,"account_id","") or getattr(reference_asset,"account_id","") or "").strip()
    cache_path,cache_key=_banner_qa_cache_path(payload,banner_asset,reference_asset,acc)
    try:
        if os.path.isfile(cache_path):
            with open(cache_path,"r",encoding="utf-8") as fh:
                cached=json.load(fh)
            verdict=cached.get("verdict") if isinstance(cached,dict) and cached.get("cache_key")==cache_key else None
            if isinstance(verdict,dict):
                out=dict(verdict); out["cache_hit"]=True; out["checker"]="durable_banner_qa_cache"
                if reference_asset is None:
                    out["identity_ok"]=True; out["plate_ok"]=True
                    out["issues"]=[x for x in list(out.get("issues") or []) if not any(k in str(x).lower() for k in ("identity","reference","vehicle/object","plate"))]
                    out["qa_scope"]="copy_readability_and_composition"
                return out
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=936)
    title=str(payload.get("title") or "").strip()
    desc=str(payload.get("description") or "").strip()
    prompt=(
        "You are a strict QA inspector for a generated Avito advertising banner. Return strict JSON only: "
        "{\"pass\":true,\"identity_ok\":true,\"text_ok\":true,\"mobile_ok\":true,\"claims_ok\":true,\"plate_ok\":true,\"composition_ok\":true,\"professional_ok\":true,\"visual_hierarchy_ok\":true,\"issues\":[\"...\"]}. "
        "Reject if the vehicle/object differs from the reference, including make/model/generation/body/color/wheels/lights/visible marks. "
        "If a plate is visible in both images, reject any changed, mirrored, invented or corrupted characters. "
        "Reject garbled, misspelled, pseudo-Cyrillic, tiny, overlapping or unreadable banner text. "
        "Reject more than one main headline plus one short supporting phrase, or text covering the main vehicle/object. "
        "Reject cheap template-like layouts: washed-out gray flyers, tiny captions, fragmented card grids, pseudo-interface blocks, random badges, excessive boxes, weak contrast, crooked alignment, clipped people, awkward crops, stretched faces or objects, decorative clutter, collage noise, and typography that looks amateur or broken. "
        "Reject if the visual hierarchy is unclear at mobile-card size: one dominant subject, one dominant message, generous clean space, intentional alignment and premium composition are mandatory. "
        "Reject any price, phone, address, guarantee, deposit, term or commercial claim not explicitly supported by the ad payload. "
        "The banner can paraphrase the verified title but must not invent facts. Mobile card readability and professional art direction are mandatory.\n"
        f"VERIFIED TITLE: {title}\nVERIFIED DESCRIPTION: {desc[:2200]}"
    )
    content=[{"type":"text","text":prompt}]
    if reference_asset is not None:
        content += [{"type":"text","text":"REFERENCE OWNER PHOTO"},{"type":"image_url","image_url":{"url":_image_data_uri(reference_asset),"detail":"low"}}]
    # The generated/composed banner is the exact advertising artifact intended for
    # publication. Keep metadata stripping and bounded downscaling, but do NOT
    # apply the private-reference whole-frame blur here: QA must be able to judge
    # typography, focus and mobile readability. Owner/reference images above keep
    # the stricter 384px + blur privacy treatment.
    content += [{"type":"text","text":"GENERATED BANNER"},{"type":"image_url","image_url":{"url":_image_data_uri(banner_asset,max_side=768,blur_radius=0.0),"detail":"high"}}]
    try:
        out=_vision_chat_json(acc,"campaign_banner_vision_qa",content,max_tokens=350,timeout=90,provenance=provenance)
    except RuntimeError as _banner_json_exc:
        _banner_json_msg=str(_banner_json_exc or "")
        if (_banner_json_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_bad_result_json")
                or _banner_json_msg.startswith("VISION_OUTCOME_UNCERTAIN:provider_200_empty_result")):
            # BANNER_BAD_JSON_RECOVERY_V1:
            # A definitive HTTP-200 malformed payload is not transport ambiguity.
            # Use a distinct recovery intent with an even stricter output contract;
            # the result still must satisfy every banner QA invariant below.
            _recovery_content=list(content)+[{"type":"text","text":
                "BAD JSON RECOVERY CONTRACT: Return ONLY one valid JSON object. "
                "No markdown, no prose, no array, no code fence. Include exactly these keys: "
                "pass, identity_ok, text_ok, mobile_ok, claims_ok, plate_ok, composition_ok, "
                "professional_ok, visual_hierarchy_ok, issues. All *_ok/pass values are booleans; "
                "issues is an array of short strings."}]
            _recovery_provenance=dict(provenance or {})
            _recovery_provenance["bad_json_recovery"]=True
            out=_vision_chat_json(
                acc,"campaign_banner_vision_qa_bad_json_recovery_v1",
                _recovery_content,max_tokens=350,timeout=90,provenance=_recovery_provenance
            )
        else:
            raise
    # Some OpenAI-compatible vision providers occasionally wrap the requested
    # single JSON object in a one-element array. Normalize that shape without
    # weakening QA or buying another request.
    if isinstance(out,list):
        _dicts=[x for x in out if isinstance(x,dict)]
        if len(_dicts)==1:
            out=_dicts[0]
    if not isinstance(out,dict):
        # Fail closed, but make one bounded schema-repair request. This is a
        # distinct intent and can never silently turn an invalid provider reply
        # into PASS: the repaired object still has to satisfy every invariant below.
        _retry_content=list(content)+[{"type":"text","text":
            "SCHEMA REPAIR: Return exactly one JSON object, not an array, string, markdown or explanation. "
            "Include every required boolean key and issues as an array."}]
        _retry_provenance=dict(provenance or {})
        _retry_provenance["schema_retry"]=True
        out=_vision_chat_json(acc,"campaign_banner_vision_qa_schema_retry_v1",_retry_content,
                              max_tokens=350,timeout=90,provenance=_retry_provenance)
        if isinstance(out,list):
            _dicts=[x for x in out if isinstance(x,dict)]
            if len(_dicts)==1:
                out=_dicts[0]
    if not isinstance(out,dict):
        raise RuntimeError("banner QA returned invalid verdict after schema retry")
    # Identity/plate checks only apply when an authoritative owner reference exists.
    # Service banners (mortgage, consulting, etc.) have no locked physical object or plate;
    # requiring those booleans caused valid service creatives to fail closed.
    # When BORIS uses an already semantic-approved real photo and overlays the
    # verified Campaign title with the deterministic local Pillow compositor,
    # copy integrity is proven by construction. Vision still owns mobile layout,
    # composition, professionalism and claims checks, but an OCR/paraphrase
    # false-negative must not reject the exact locally-rendered title.
    _local_verified_text=(
        str(payload.get("banner_visual_renderer") or "") in {"approved_semantic_media","openai_visual_only"} and
        str(payload.get("banner_text_renderer") or "")=="local_pillow" and
        str(payload.get("banner_text_source") or "") in {"verified_title_exact","verified_title_prefix"}
    )
    if _local_verified_text:
        out["vision_text_ok"]=bool(out.get("text_ok"))
        out["text_ok"]=True
        out["text_integrity_proof"]="deterministic_local_verified_title_prefix"
        out["issues"]=[x for x in list(out.get("issues") or []) if "text" not in str(x).lower() and "title" not in str(x).lower()]
        # Absence of a commercial claim is safe, not a failure. Some Vision
        # verdicts incorrectly set claims_ok=false with wording like
        # "no price/phone/guarantee present". Only unsupported claims should fail.
        _claim_issues=[str(x).lower() for x in list(out.get("issues") or [])]
        if (not bool(out.get("claims_ok")) and _claim_issues
                and all(("no commercial claim" in x or "no price" in x or "lacking supported commercial info" in x)
                        for x in _claim_issues)):
            out["claims_ok"]=True
            out["claims_normalization"]="no_claim_is_safe"
            out["issues"]=[x for x in list(out.get("issues") or [])
                           if not any(k in str(x).lower() for k in ("no commercial claim","no price","lacking supported commercial info"))]
    # Compute PASS from the independent checks. Never include the model's own
    # aggregate `pass` flag in this recomputation; doing so made a repairable
    # OCR false-negative permanently sticky even after every required invariant
    # had been proven true.
    required=["text_ok","mobile_ok","claims_ok","composition_ok","professional_ok","visual_hierarchy_ok"]
    if reference_asset is not None:
        required += ["identity_ok","plate_ok"]
        out["qa_scope"]="reference_identity_and_copy"
    else:
        # Identity/plate have no meaning without an authoritative reference.
        # Normalize them to N/A-pass and remove model chatter about a reference
        # that was never supplied; this keeps client QA truthful and readable.
        out["identity_ok"]=True; out["plate_ok"]=True
        out["issues"]=[x for x in list(out.get("issues") or []) if not any(k in str(x).lower() for k in ("identity","reference","vehicle/object","plate"))]
        out["qa_scope"]="copy_readability_and_composition"
    out["pass"]=all(bool(out.get(k)) for k in required)
    out["cache_hit"]=False
    out["checker"]="openai_vision_banner_qa"
    try:
        cache_dir=os.path.dirname(cache_path)
        os.makedirs(cache_dir,exist_ok=True)
        fd,tmp_path=tempfile.mkstemp(prefix=".banner-qa-",suffix=".json",dir=cache_dir)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as fh:
                json.dump({"schema":1,"cache_key":cache_key,"verdict":out},fh,ensure_ascii=False,sort_keys=True)
            os.replace(tmp_path,cache_path)
        except Exception:
            try: os.unlink(tmp_path)
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1005)
            raise
    except Exception as _suppressed_exc:
        # QA result stays authoritative even if cache persistence itself failed.
        observe_suppressed(__name__, _suppressed_exc, line=1007)
    return out



def _campaign_banner_batch_eligible(payload: dict, reference_asset=None) -> bool:
    """Only deterministic local-text banners without owner-reference identity checks may batch."""
    if reference_asset is not None:
        return False
    return (
        str(payload.get("banner_visual_renderer") or "") in {"approved_semantic_media","openai_visual_only"}
        and str(payload.get("banner_text_renderer") or "")=="local_pillow"
        and str(payload.get("banner_text_source") or "") in {"verified_title_exact","verified_title_prefix"}
    )


def _campaign_banner_batch_normalize(payload: dict, raw: dict) -> dict:
    out=dict(raw or {})
    # Exact local title is proven by construction. Keep model evidence but do not
    # let OCR/paraphrase noise reject deterministic Pillow text.
    out["vision_text_ok"]=bool(out.get("text_ok"))
    out["text_ok"]=True
    out["text_integrity_proof"]="deterministic_local_verified_title_prefix"
    issues=[str(x)[:240] for x in (out.get("issues") or [])][:8]
    issues=[x for x in issues if "text" not in x.lower() and "title" not in x.lower()]
    claim_issues=[x.lower() for x in issues]
    if (not bool(out.get("claims_ok")) and claim_issues
            and all(("no commercial claim" in x or "no price" in x or "lacking supported commercial info" in x)
                    for x in claim_issues)):
        out["claims_ok"]=True
        out["claims_normalization"]="no_claim_is_safe"
        issues=[x for x in issues
                if not any(k in x.lower() for k in ("no commercial claim","no price","lacking supported commercial info"))]
    out["issues"]=issues
    out["identity_ok"]=True
    out["plate_ok"]=True
    out["qa_scope"]="copy_readability_and_composition"
    required=["text_ok","mobile_ok","claims_ok","composition_ok","professional_ok","visual_hierarchy_ok"]
    out["pass"]=all(bool(out.get(k)) for k in required)
    out["cache_hit"]=False
    out["checker"]="openai_vision_banner_qa_batch"
    return out


def _campaign_banner_cache_read(payload: dict, banner_asset, account_id: str) -> dict | None:
    import json, os
    cache_path,cache_key=_banner_qa_cache_path(payload,banner_asset,None,account_id)
    try:
        if not os.path.isfile(cache_path):
            return None
        with open(cache_path,"r",encoding="utf-8") as fh:
            cached=json.load(fh)
        verdict=cached.get("verdict") if isinstance(cached,dict) and cached.get("cache_key")==cache_key else None
        if isinstance(verdict,dict):
            out=dict(verdict); out["cache_hit"]=True; out["checker"]="durable_banner_qa_cache"
            return out
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1340)
    return None


def _campaign_banner_cache_write(payload: dict, banner_asset, account_id: str, verdict: dict) -> None:
    import fcntl, json, os, tempfile
    cache_path,cache_key=_banner_qa_cache_path(payload,banner_asset,None,account_id)
    os.makedirs(os.path.dirname(cache_path),exist_ok=True)
    lock_path=cache_path+".lock"
    with open(lock_path,"a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(),fcntl.LOCK_EX)
        try:
            # Never overwrite a canonical verdict written by a concurrent single QA.
            existing=_campaign_banner_cache_read(payload,banner_asset,account_id)
            if existing is not None:
                return
            fd,tmp_path=tempfile.mkstemp(prefix=".banner-qa-batch-",suffix=".json",dir=os.path.dirname(cache_path))
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as fh:
                    json.dump({"schema":1,"cache_key":cache_key,"verdict":verdict},fh,ensure_ascii=False,sort_keys=True)
                os.replace(tmp_path,cache_path)
            except Exception:
                try: os.unlink(tmp_path)
                except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1362)
                raise
        finally:
            fcntl.flock(lock_fh.fileno(),fcntl.LOCK_UN)


def validate_campaign_banners_vision_batch(
    entries: list[dict],
    account_id: str | None = None,
    provenance: dict | None = None,
) -> dict:
    """Batch dedicated Campaign banner QA for up to four deterministic banners.

    This is only a cache-seeding fast-path. Existing single-banner QA remains the
    authority for unresolved/missing verdicts and for any ineligible banner.
    """
    entries=list(entries or [])
    if not entries:
        return {"ok":True,"verdicts":{},"cache_hits":[],"unresolved":[],"provider_called":False}
    if len(entries)>4:
        raise ValueError("campaign banner QA batch max is 4")
    acc=str(account_id or "").strip()
    verdicts={}; cache_hits=[]; unresolved=[]; pending=[]
    seen=set()
    for entry in entries:
        try: item_id=int(entry.get("item_id") or 0)
        except Exception: item_id=0
        payload=entry.get("payload") if isinstance(entry.get("payload"),dict) else {}
        asset=entry.get("banner_asset")
        reference=entry.get("reference_asset")
        if item_id<=0 or item_id in seen or asset is None:
            if item_id>0: unresolved.append(item_id)
            continue
        seen.add(item_id)
        if not _campaign_banner_batch_eligible(payload,reference):
            unresolved.append(item_id); continue
        cached=_campaign_banner_cache_read(payload,asset,acc)
        if cached is not None:
            verdicts[item_id]=cached; cache_hits.append(item_id); continue
        pending.append((item_id,payload,asset))
    if not pending:
        return {"ok":True,"verdicts":verdicts,"cache_hits":cache_hits,
                "unresolved":sorted(set(unresolved)),"provider_called":False}

    prompt=(
        "BATCH CAMPAIGN BANNER QA. Inspect each Avito banner independently. "
        "Return ONLY one JSON object with key results. results is an array in the same item order. "
        "Each result must contain item_id, text_ok, mobile_ok, claims_ok, composition_ok, "
        "professional_ok, visual_hierarchy_ok, issues. All *_ok values are booleans; issues is an array. "
        "These banners use deterministic local text; judge visible readability/composition/claims, "
        "but do not invent missing facts. Reject if the sold object is obscured, crop/layout is awkward, "
        "text is unreadable at mobile-card size, unsupported price/phone/guarantee/address appears, "
        "or art direction looks cheap/template-like. No reference-identity/plate check applies."
    )
    content=[{"type":"text","text":prompt}]
    for idx,(item_id,payload,asset) in enumerate(pending,1):
        title=str(payload.get("title") or "").strip()
        desc=str(payload.get("description") or "").strip()
        content.append({"type":"text","text":
            f"ITEM {item_id} | VERIFIED TITLE: {title} | VERIFIED DESCRIPTION: {desc[:1200]} | IMAGE {idx}"})
        content.append({"type":"image_url","image_url":{
            "url":_image_data_uri(asset,max_side=768,blur_radius=0.0),"detail":"high"}})

    batch_provenance=dict(provenance or {})
    batch_provenance["batch_size"]=len(pending)
    batch_provenance["item_ids"]=",".join(str(x[0]) for x in pending)
    try:
        raw=_vision_chat_json(
            acc,"campaign_banner_vision_qa_batch_v1",content,
            max_tokens=max(500,min(1600,300*len(pending))),timeout=120,
            provenance=batch_provenance,
        )
    except RuntimeError as exc:
        return {"ok":False,"verdicts":verdicts,"cache_hits":cache_hits,
                "unresolved":sorted(set(unresolved+[x[0] for x in pending])),
                "provider_called":True,"error":str(exc)[:240]}

    results=raw.get("results") if isinstance(raw,dict) else None
    if not isinstance(results,list):
        return {"ok":False,"verdicts":verdicts,"cache_hits":cache_hits,
                "unresolved":sorted(set(unresolved+[x[0] for x in pending])),
                "provider_called":True,"error":"batch_invalid_shape"}
    by_id={}
    for result in results:
        if not isinstance(result,dict):
            continue
        try: rid=int(result.get("item_id") or 0)
        except Exception: rid=0
        if rid>0 and rid not in by_id:
            by_id[rid]=result

    for item_id,payload,asset in pending:
        raw_verdict=by_id.get(item_id)
        required_raw=("mobile_ok","claims_ok","composition_ok","professional_ok","visual_hierarchy_ok")
        if not isinstance(raw_verdict,dict) or any(k not in raw_verdict for k in required_raw):
            unresolved.append(item_id)
            continue
        verdict=_campaign_banner_batch_normalize(payload,raw_verdict)
        _campaign_banner_cache_write(payload,asset,acc,verdict)
        verdicts[item_id]=verdict

    return {
        "ok":not bool(unresolved),
        "verdicts":verdicts,
        "cache_hits":cache_hits,
        "unresolved":sorted(set(unresolved)),
        "provider_called":True,
        "batch_size":len(pending),
    }


def validate_campaign_banner_vision(payload: dict, banner_asset, reference_asset=None, account_id: str | None = None, provenance: dict | None = None) -> dict:
    """Cross-process serialized wrapper for durable paid banner QA cache."""
    import fcntl, os
    acc=str(account_id or getattr(banner_asset,"account_id","") or getattr(reference_asset,"account_id","") or "").strip()
    cache_path,_=_banner_qa_cache_path(payload,banner_asset,reference_asset,acc)
    os.makedirs(os.path.dirname(cache_path),exist_ok=True)
    lock_path=cache_path+".lock"
    with open(lock_path,"a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(),fcntl.LOCK_EX)
        try:
            # Cache is re-read inside the locked implementation after waiting,
            # so a concurrent replica that already paid makes this call free.
            return _validate_campaign_banner_vision_locked(payload,banner_asset,reference_asset,account_id,provenance)
        finally:
            fcntl.flock(lock_fh.fileno(),fcntl.LOCK_UN)
