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

    account_id = str(account_id or "").strip()
    if not account_id:
        raise RuntimeError("VISION_ACCOUNT_REQUIRED")
    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    fallback_key = str(os.environ.get("PLUSVIBE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    fallback_base = str(os.environ.get("BORIS_VISION_FALLBACK_BASE_URL") or os.environ.get("BORIS_DEEPSEEK_BASE_URL") or "https://plusvibeapi.ru/v1").rstrip("/")
    fallback_model = str(os.environ.get("BORIS_VISION_FALLBACK_MODEL") or "google/gemini-3.1-flash-lite").strip()
    if not api_key and not fallback_key:
        raise RuntimeError("VISION_PROVIDER_KEY_MISSING")

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

    def _uncertain_write(error_code: str, provider_request_id: str | None = None):
        os.makedirs(cache_root, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".paid-vision-uncertain-", suffix=".json", dir=cache_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({
                    "schema": 1,
                    "intent_hash": intent_hash,
                    "status": "provider_outcome_uncertain",
                    "operation": operation,
                    "provider_request_id": provider_request_id or None,
                    "error_code": str(error_code or "vision_transport_outcome_unknown"),
                }, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, uncertain_path)
            _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(error_code or "vision_transport_outcome_unknown")})
        except Exception:
            try: os.unlink(tmp_path)
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=156)
            raise

    def _cache_read():
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if obj.get("intent_hash") == intent_hash and isinstance(obj.get("result"), (dict, list)):
                return obj
        except Exception:
            return None
        return None

    cached = _cache_read()
    if cached is not None:
        if return_meta:
            return cached["result"], {"cache_hit": True, "provider_request_id": cached.get("provider_request_id"),
                                      "ambiguous_success": bool(cached.get("ambiguous_success")), "cost_rub": None}
        return cached["result"]
    uncertain = _uncertain_read()
    if uncertain is not None:
        _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(uncertain.get("error_code") or "unknown")})
        raise RuntimeError("VISION_OUTCOME_UNCERTAIN:%s" % str(uncertain.get("error_code") or "unknown"))

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
                raise RuntimeError("VISION_DB_POOL_BUSY_RETRYABLE")
            time.sleep(1.5 * (_db_try + 1))
    try:
        lock_db.execute(text("select pg_advisory_lock(:k)"), {"k": lock_key}); lock_held=True
        cached = _cache_read()
        if cached is not None:
            if return_meta:
                return cached["result"], {"cache_hit": True, "provider_request_id": cached.get("provider_request_id"),
                                          "ambiguous_success": bool(cached.get("ambiguous_success")), "cost_rub": None}
            return cached["result"]
        uncertain = _uncertain_read()
        if uncertain is not None:
            _audit("VISION_OUTCOME_UNCERTAIN", "quarantined", {"reason": str(uncertain.get("error_code") or "unknown")})
            raise RuntimeError("VISION_OUTCOME_UNCERTAIN:%s" % str(uncertain.get("error_code") or "unknown"))

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
            raise RuntimeError("VISION_BUDGET_RESERVE_FAILED:%s" % status)

        try:
            allowance = ai_budget.allow_next(budget_run_id)
            allowed = bool((allowance or {}).get("allowed")) if isinstance(allowance, dict) else bool(allowance)
            if not allowed:
                _audit("AI_BUDGET_NOT_ALLOWED", "blocked", {"reason": str((allowance or {}).get("reason") or "") if isinstance(allowance,dict) else ""})
                raise RuntimeError("VISION_BUDGET_NOT_ALLOWED")

            with _vision_burst_guard(account_id) as burst:
                if not burst.get("allowed"):
                    _code = str(burst.get("code") or "VISION_BURST_CAP")
                    _audit(_code, "blocked", burst)
                    if _code == "VISION_DAILY_CAP":
                        raise RuntimeError("VISION_DAILY_CAP:%s/%s" % (burst.get("daily_used"), burst.get("daily_cap")))
                    raise RuntimeError("VISION_BURST_CAP:%s/%s" % (burst.get("used"), burst.get("cap")))
                from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
                external_content, privacy_vault = safe_external_ai_payload(content)
                provider_name = "openai"
                provider_model = "gpt-4o-mini"
                response = None
                primary_status = 0
                if api_key:
                    try:
                        response = requests.post(
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
                        response = fallback_session.post(
                            fallback_base + "/chat/completions",
                            headers={
                                "Authorization": "Bearer " + fallback_key,
                                "Content-Type": "application/json",
                                "Idempotency-Key": intent_key,
                            },
                            json={
                                "model": fallback_model,
                                "messages": [{"role": "user", "content": external_content}],
                                "temperature": 0,
                                "max_tokens": int(max_tokens),
                            },
                            timeout=int(timeout),
                        )
                    except Exception:
                        _uncertain_write("vision_fallback_transport_outcome_unknown")
                        raise RuntimeError("VISION_OUTCOME_UNCERTAIN:vision_fallback_transport_outcome_unknown")
                    fallback_status = int(getattr(response, "status_code", 0) or 0)
                    if fallback_status != 200:
                        raise RuntimeError("VISION_FALLBACK_HTTP_ERROR:%s" % fallback_status)
                    _audit("VISION_FALLBACK_USED", "recovered", {
                        "primary_status": primary_status or None,
                        "fallback_model": fallback_model,
                    })
                elif not api_key:
                    raise RuntimeError("VISION_OPENAI_KEY_MISSING")
                elif primary_status != 200:
                    raise RuntimeError("VISION_PROVIDER_HTTP_ERROR:%s" % primary_status)

                exact_rid = str(response.headers.get("x-request-id") or "").strip()
                try:
                    body = response.json()
                except Exception:
                    _uncertain_write("provider_200_unparseable", exact_rid or None)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:provider_200_unparseable")
                usage = body.get("usage") or {}
                raw = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                if raw:
                    raw = str(restore_external_ai_output(raw, privacy_vault))
                raw = raw.replace("```json", "").replace("```", "").strip()
                if not raw:
                    _uncertain_write("provider_200_empty_result", exact_rid or None)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:provider_200_empty_result")
                try:
                    result = json.loads(raw)
                except Exception:
                    _uncertain_write("provider_200_bad_result_json", exact_rid or None)
                    raise RuntimeError("VISION_OUTCOME_UNCERTAIN:provider_200_bad_result_json")
                rid = exact_rid or ("ambiguous-vision:" + intent_hash)

                # Persist the paid result before ledger/budget reconciliation. If the
                # process dies after provider success, the same intent is still a cache
                # hit and cannot silently repurchase the vision proof.
                os.makedirs(cache_root, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=".paid-vision-", suffix=".json", dir=cache_root)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump({
                            "schema": 1,
                            "intent_hash": intent_hash,
                            "operation": operation,
                            "provider": provider_name,
                            "model": provider_model,
                            "provider_request_id": exact_rid or None,
                            "ambiguous_success": not bool(exact_rid),
                            "result": result,
                        }, fh, ensure_ascii=False, sort_keys=True)
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
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                        request_id=rid,
                        usage_details={**(usage if isinstance(usage,dict) else {}),
                                       "boris_intent_key": intent_key,
                                       "boris_provenance": {str(k)[:64]: v for k,v in (provenance or {}).items()
                                                            if isinstance(v,(str,int,float,bool)) or v is None}},
                    )
                    committed = ai_budget.commit(budget_run_id, rid)
                    if str((committed or {}).get("status") or "") in {"committed","already_committed"}:
                        break
                    if _acct_try < 3:
                        time.sleep(0.5 * (_acct_try + 1))
                if return_meta:
                    return result, {"cache_hit": False, "request_id": rid,
                                    "provider_request_id": exact_rid or None,
                                    "ambiguous_success": not bool(exact_rid),
                                    "cost_rub": float(cost_rub or 0)}
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
            if _provider in {"openai","boris_local"} and bool(_banner_qa.get("passed")) and _asset_key and _asset_key in _banner_url and (_provider=="openai" or _local_premium_banner):
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


def _image_data_uri(asset) -> str:
    import base64
    from app.services.media_storage import abs_path
    from app.services.ai_privacy import sanitize_external_reference_image_bytes
    with open(abs_path(asset.storage_key), "rb") as fh:
        safe_bytes, _mime = sanitize_external_reference_image_bytes(
            fh.read(), max_side=384, blur_radius=3.5
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
        "plastering_service": lambda x: any(k in x for k in ("plaster", "plastering", "штукатур")),
        "mortgage_broker": lambda x: any(k in x for k in (
            "mortgage broker", "mortgage consultation", "mortgage meeting",
            "home loan", "loan consultation", "loan documents",
            "contract discussion", "financial documents",
            "real estate consultation", "realtor meeting",
            "person offering consultation", "person offering help",
            "consultation meeting", "client consultation", "service steps",
            "mortgage concept", "home financing", "property documents",
            "ипотеч", "кредит", "документ", "договор", "консультац"
        )),
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


SEMANTIC_POLICY_VERSION = "20260902_service_context_v2"


def classify_item_media_vision(payload: dict, linked_media: list, account_id: str | None = None, provenance: dict | None = None) -> list[dict]:
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

    title=str(payload.get("title") or "").strip()
    desc=str(payload.get("description") or "").strip()
    category=str(payload.get("category_path") or payload.get("category") or "").strip()
    direction=str(payload.get("direction") or payload.get("offer") or "").strip()
    prompt=("You are a strict advertising photo QA. Decide whether EACH image clearly matches what this ad sells. "
            "The ad text is the semantic source of truth. Do not trust file names, search queries or existing tags. "
            "For physical goods reject related-but-different products, wrong room/category, generic interiors where the sold object is not clearly visible, and ambiguous images. "
            "For made-to-order glass shower products, judge the visibly sold glass structure, not incidental bathroom fixtures: a clear fixed glass shower partition/panel may match a shower-partition ad even when photographed inside a complete bathroom or shower area. However, when the ad explicitly says WITHOUT DOORS / fixed partition / walk-in panel, reject images whose dominant sold object is a closed shower cabin or clearly hinged/sliding shower door. "
            "For INTANGIBLE SERVICES, do not require a photograph to literally show the service being performed. Accept a clear professional visual representation of the advertised service context (for example mortgage/home-loan consultation, relevant documents, apartment/home purchase discussion, broker-client meeting) when it is semantically aligned with the ad; reject unrelated lifestyle scenes or a context belonging to another service. "
            "For made-to-order furniture, a finished real example of the exact sold furniture type IS a valid match even when staged inside an interior: "
            "kitchen cabinetry/counter/island for a custom-kitchen ad; hallway cabinet/shoe cabinet/bench/coat storage for hallway furniture; "
            "closet/wardrobe/shelving/hanging storage for wardrobe ads; bathroom vanity/cabinet/sink storage for bathroom-furniture ads. "
            "A generic room without the sold object is still NOT a match. "
            "For vehicle rental/sale ads, a gallery is allowed to show useful parts of the same type of vehicle: exterior, cabin/interior, dashboard, seats, sunroof, trunk, wheels, lights and close-up vehicle details. Do NOT reject a valid vehicle interior/detail merely because the full exterior is not visible. Reject unrelated vehicles or non-vehicle images. "
            "Return strict JSON array, one object per image in order: {\"index\":1,\"match\":true,\"confidence\":0.0,\"subject\":\"what is visibly shown\",\"reason\":\"short\"}. "
            f"TITLE: {title}\nCATEGORY: {category}\nDIRECTION/OFFER: {direction}\nDESCRIPTION: {desc[:5000]}")
    content=[{"type":"text","text":prompt}]
    for idx,(_,asset) in enumerate(pending,1):
        content.append({"type":"text","text":f"IMAGE {idx}"})
        content.append({"type":"image_url","image_url":{"url":_image_data_uri(asset),"detail":"low"}})
    acc=str(account_id or (getattr(pending[0][1],"account_id","") if pending else "") or "").strip()
    out=_vision_chat_json(acc,"media_semantic_item_vision",content,max_tokens=1200,timeout=120,provenance=provenance)
    if not isinstance(out,list) or len(out)!=len(pending):
        raise RuntimeError(f"vision semantic verdict count mismatch: {len(out) if isinstance(out,list) else 'not-list'} != {len(pending)}")
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
    content += [{"type":"text","text":"GENERATED BANNER"},{"type":"image_url","image_url":{"url":_image_data_uri(banner_asset),"detail":"high"}}]
    out=_vision_chat_json(acc,"campaign_banner_vision_qa",content,max_tokens=350,timeout=90,provenance=provenance)
    if not isinstance(out,dict): raise RuntimeError("banner QA returned invalid verdict")
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
