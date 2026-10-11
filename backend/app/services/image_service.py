"""ImageGenerationService — единственная точка платных генераций изображений ЕЦО.

Провайдер только OpenAI. Ветка generations идёт через существующую
banner_generator.generate_ai_image (она уже пишет расход в api_usage по
токенам), ветка edits добавлена здесь, потому что reference-картинок в проекте
не было вовсе. Обе ветки логируются ОДНИМ логгером app.usage.log_usage —
второго расчёта денег в BORIS не появляется.

Деньги: ai_budget.reserve до первого вызова, allow_next перед каждым кадром,
commit по request_id из api_usage (факт, а не оценка), release в finally.
Наружу уходит только продуктовая формулировка, себестоимость — нет.
"""
import hashlib
import inspect
import json
import os
import time
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text as _t, create_engine as _create_engine
from sqlalchemy.pool import NullPool as _NullPool
from app.db.session import DATABASE_URL as _DATABASE_URL

from app.services import ai_budget
from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard
from app.usage import log_usage
from app.services.exception_observability import observe_suppressed

MODEL = "gpt-image-1-mini"       # стандарт фотостудии, решение 15.08
PREMIUM_MODEL = "gpt-image-2"   # баннеры с текстом и премиум по запросу
DRAFT_MODEL = "gpt-image-1-mini"
DEFAULT_SIZE = "1024x1024"
MODULE = "photo_studio"

# Ориентиры сметы (копейки). Проверены на живых генерациях 07.08:
# gpt-image-2 medium 1024 = 5,01 ₽; mini low = 0,21 ₽. Reference добавляет
# image input токены — уточняется зондом, поэтому заложен запас.
EST_KOPEKS = {("gpt-image-2", "medium"): 501, ("gpt-image-2", "high"): 900,
              ("gpt-image-2", "low"): 260, ("gpt-image-1-mini", "low"): 21,
              ("gpt-image-1-mini", "medium"): 81}
EST_REFERENCE_EXTRA = 200
BANNER_MAX_COST_KOPEKS = 400
RETRY_OVERHEAD = 1.0   # paid images: one economic intent = one provider attempt
MAX_AUTO_RETRY = 0
HARD_MAX_RETRY = 1


def _atomic_write_bytes(path, payload: bytes):
    """Crash-safe paid artifact persistence: never expose a partial cache file."""
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".paid-image-",suffix=".tmp",dir=str(target.parent))
    try:
        with os.fdopen(fd,"wb") as out:
            out.write(bytes(payload)); out.flush(); os.fsync(out.fileno())
        os.replace(tmp,str(target))
    finally:
        if os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass
    return target


def _valid_image_artifact(path) -> bool:
    """Accept only a decodable completed raster as an idempotent paid cache hit."""
    target = Path(path)
    try:
        if not target.is_file() or target.stat().st_size < 64:
            return False
        from PIL import Image as _PILImage
        with _PILImage.open(target) as img:
            width, height = img.size
            img.verify()
        return int(width) >= 256 and int(height) >= 256
    except Exception:
        return False


def _invalid_existing_artifact_result(account_id, operation, save_path, source="image_generation"):
    _audit_ai_guard(
        account_id=account_id, source=source, operation=operation,
        event_code="PAID_IMAGE_ARTIFACT_INVALID", status="quarantined",
        intent_key="artifact:" + hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
        details={"path": str(save_path)[:500]},
    )
    return {
        "ok": False, "path": None, "request_id": None, "cost_rub": None,
        "error": "PAID_IMAGE_ARTIFACT_INVALID", "invalid_existing_artifact": True,
    }


def _atomic_write_marker(path, payload: bytes):
    """Crash-safe intent-marker persistence, separate from final paid image bytes."""
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".paid-intent-",suffix=".tmp",dir=str(target.parent))
    try:
        with os.fdopen(fd,"wb") as out:
            out.write(bytes(payload)); out.flush(); os.fsync(out.fileno())
        os.replace(tmp,str(target))
    finally:
        if os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass
    return target


def _uncertain_intent_path(save_path):
    return Path(str(save_path) + ".paid-intent-uncertain.json")


def _read_uncertain_intent(save_path):
    marker = _uncertain_intent_path(save_path)
    try:
        if marker.is_file():
            with open(marker, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {"status": "uncertain"}
    except Exception:
        return {"status": "uncertain", "marker_unreadable": True}
    return None


def _write_uncertain_intent(save_path, payload):
    marker = _uncertain_intent_path(save_path)
    body = json.dumps(payload or {"status": "uncertain"}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    _atomic_write_marker(marker, body)
    return marker


def _clear_uncertain_intent(save_path):
    marker = _uncertain_intent_path(save_path)
    try:
        if marker.exists():
            marker.unlink()
    except OSError:
        pass


def estimate_kopeks(shots, model=MODEL, quality="medium", with_reference=True):
    base = EST_KOPEKS.get((model, quality), 501)
    extra = EST_REFERENCE_EXTRA if with_reference else 0
    return int(base + (shots - 1) * (base + extra)) if shots else 0


def banner_reference_cost_guard(operation, model=MODEL, quality="medium"):
    """Fail closed before a new paid banner reference edit can exceed 4 RUB.

    Cache hits are handled before this guard in generate() and remain free.
    Non-banner reference workflows keep their existing budget policy.
    """
    op = str(operation or "").strip().lower()
    applies = op.startswith("banner_") or op.startswith("campaign_openai_")
    estimated_kopeks = int(
        estimate_kopeks(1, model=model, quality=quality, with_reference=False)
        + EST_REFERENCE_EXTRA
    )
    return {
        "applies": bool(applies),
        "allowed": bool((not applies) or estimated_kopeks <= BANNER_MAX_COST_KOPEKS),
        "estimated_kopeks": estimated_kopeks,
        "estimated_cost_rub": round(estimated_kopeks / 100.0, 4),
        "max_cost_rub": round(BANNER_MAX_COST_KOPEKS / 100.0, 2),
        "model": str(model or ""),
        "quality": str(quality or ""),
        "operation": str(operation or ""),
    }


def _filter_kwargs(fn, kwargs):
    """Зовём чужой контракт только теми аргументами, которые он объявляет."""
    try:
        params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def budget_reserve(account_id, run_id, est_kopeks, operation, calls_planned):
    kw = _filter_kwargs(ai_budget.reserve, {
        "account_id": account_id, "module": MODULE, "operation": operation,
        "run_id": run_id, "est_kopeks": est_kopeks,
        "calls_planned": calls_planned, "dry_run": False})
    return ai_budget.reserve(**kw)


def budget_allow(run_id):
    return ai_budget.allow_next(run_id)


def budget_commit(run_id, request_id):
    return ai_budget.commit(run_id, request_id)


def budget_release(run_id):
    try:
        return ai_budget.release(run_id)
    except Exception as e:
        print("[image_service] release не прошёл: %s" % repr(e)[:120], flush=True)
        return None


@contextmanager
def _artifact_generation_lock(db, account_id, save_path):
    """Cross-process lock for one deterministic paid artifact.

    The lock owns a dedicated AUTOCOMMIT/NullPool connection. The caller's ORM
    session is intentionally not touched: a 240s image request must never hold an
    application transaction or consume its tiny shared pool while waiting on OpenAI.
    """
    identity = f"{account_id}|{Path(save_path).resolve()}".encode("utf-8", "ignore")
    raw = int.from_bytes(hashlib.blake2b(identity, digest_size=8).digest(), "big", signed=False)
    lock_key = raw if raw < (1 << 63) else raw - (1 << 64)
    engine=_create_engine(_DATABASE_URL,poolclass=_NullPool,pool_pre_ping=True,
                          connect_args={"application_name":"boris-paid-image-artifact-lock"})
    conn=engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(_t("SELECT pg_advisory_lock(:k)"), {"k": lock_key})
    try:
        yield
    finally:
        try: conn.execute(_t("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=198)
        try: conn.close()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=200)
        try: engine.dispose()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=202)


def _paid_image_burst_limits():
    try:
        cap = max(1, min(100, int(os.environ.get("BORIS_PAID_IMAGE_BURST_CAP", "10") or 10)))
    except Exception:
        cap = 10
    try:
        window = max(1, min(60, int(os.environ.get("BORIS_PAID_IMAGE_BURST_WINDOW_MIN", "10") or 10)))
    except Exception:
        window = 10
    try:
        daily = max(cap, min(500, int(os.environ.get("BORIS_PAID_IMAGE_DAILY_CAP", "50") or 50)))
    except Exception:
        daily = 50
    return cap, window, daily


@contextmanager
def _paid_image_account_guard(db, account_id, override=False):
    """Serialize paid images per account and stop short-window runaway jobs.

    Uses an isolated AUTOCOMMIT connection so the account-wide economic lock may
    safely cover provider latency without occupying the caller's ORM transaction.
    """
    raw = int.from_bytes(
        hashlib.blake2b(("paid-image-burst|" + str(account_id)).encode("utf-8", "ignore"), digest_size=8).digest(),
        "big", signed=False,
    )
    lock_key = raw if raw < (1 << 63) else raw - (1 << 64)
    engine=_create_engine(_DATABASE_URL,poolclass=_NullPool,pool_pre_ping=True,
                          connect_args={"application_name":"boris-paid-image-account-lock"})
    conn=engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(_t("SELECT pg_advisory_lock(:k)"), {"k": lock_key})
    try:
        cap, window, daily = _paid_image_burst_limits()
        _override_mode=("all" if override is True else str(override or "").strip().lower())
        if _override_mode not in {"all","burst_only"}:
            _override_mode="none"
        import datetime as _dt
        now=_dt.datetime.now(_dt.timezone.utc)
        cutoff = now - _dt.timedelta(minutes=window)
        day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
        row = conn.execute(_t(
            "SELECT count(*), coalesce(sum(cost_rub),0) "
            "FROM api_usage WHERE account_id=:a AND coalesce(images,0)>0 AND created_at>=:cut"
        ), {"a": str(account_id), "cut": cutoff}).fetchone()
        used = int((row[0] if row else 0) or 0)
        spent = float((row[1] if row else 0) or 0)
        day_row=conn.execute(_t("SELECT count(*),coalesce(sum(cost_rub),0) FROM api_usage WHERE account_id=:a AND coalesce(images,0)>0 AND created_at>=:day"),{"a":str(account_id),"day":day_start}).fetchone()
        day_used=int((day_row[0] if day_row else 0) or 0); day_spent=float((day_row[1] if day_row else 0) or 0)
        if _override_mode!="all" and day_used >= daily:
            yield {"allowed":False,"code":"PAID_IMAGE_DAILY_CAP","used":used,"cap":cap,"window_min":window,
                   "daily_used":day_used,"daily_cap":daily,"spent_rub":round(spent,2),"daily_spent_rub":round(day_spent,2),
                   "override_mode":_override_mode}
            return
        if _override_mode=="none" and used >= cap:
            yield {"allowed":False,"code":"PAID_IMAGE_BURST_CAP","used":used,"cap":cap,"window_min":window,
                   "daily_used":day_used,"daily_cap":daily,"spent_rub":round(spent,2),"daily_spent_rub":round(day_spent,2),
                   "override_mode":_override_mode}
            return
        yield {"allowed": True, "override": bool(override), "override_mode":_override_mode,
               "cap": cap, "window_min": window, "daily_cap":daily,
               "daily_used":day_used,"daily_spent_rub":round(day_spent,2)}
    finally:
        try: conn.execute(_t("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=263)
        try: conn.close()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=265)
        try: engine.dispose()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=267)






def generate_banner_financially_guarded(
    db,
    account_id,
    prompt,
    save_path,
    model=MODEL,
    quality="low",
    size="1024x1024",
    operation="генерация изображения",
    burst_override=False,
    provenance=None,
):
    """
    Единый финансово-защищённый путь для banner generations.

    НЕ вызывает banner_generator.

    Цепочка:
        reserve
        -> allow_next
        -> shared OpenAI transport
        -> usage
        -> commit/release

    Реальная генерация НЕ выполняется автоматически:
    функция вызывается только production caller'ом.
    """

    # Durable artifact idempotency before budget reservation/provider access.
    # Callers that intentionally regenerate must supply a new save_path. A fast
    # pre-check avoids DB lock traffic for the common cache-hit case. The second
    # check under the advisory lock closes concurrent missing-file races.
    output_path = Path(save_path)
    if output_path.is_file():
        if not _valid_image_artifact(output_path):
            return _invalid_existing_artifact_result(account_id, operation, save_path)
        _clear_uncertain_intent(save_path)
        return {
            "ok": True,
            "path": save_path,
            "request_id": None,
            "cost_rub": 0.0,
            "error": None,
            "cache_hit": True,
        }
    uncertain = _read_uncertain_intent(save_path)
    if uncertain is not None:
        _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                        event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                        intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                        details={"reason": str(uncertain.get("error_code") or uncertain.get("status") or "unknown")})
        return {
            "ok": False, "path": None, "request_id": uncertain.get("provider_request_id"),
            "cost_rub": None, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
            "ambiguous_provider_outcome": True, "intent": uncertain,
        }

    with _artifact_generation_lock(db, account_id, save_path):
        if output_path.is_file():
            if not _valid_image_artifact(output_path):
                return _invalid_existing_artifact_result(account_id, operation, save_path)
            _clear_uncertain_intent(save_path)
            return {
                "ok": True,
                "path": save_path,
                "request_id": None,
                "cost_rub": 0.0,
                "error": None,
                "cache_hit": True,
            }
        uncertain = _read_uncertain_intent(save_path)
        if uncertain is not None:
            _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                            event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                            intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                            details={"reason": str(uncertain.get("error_code") or uncertain.get("status") or "unknown")})
            return {
                "ok": False, "path": None, "request_id": uncertain.get("provider_request_id"),
                "cost_rub": None, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
                "ambiguous_provider_outcome": True, "intent": uncertain,
            }
        with _paid_image_account_guard(db, account_id, override=burst_override) as _burst:
            if not _burst.get("allowed"):
                _code=str(_burst.get("code") or "PAID_IMAGE_BURST_CAP")
                _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                                event_code=_code, status="blocked",
                                intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                details=_burst)
                return {
                    "ok": False,
                    "path": None,
                    "request_id": None,
                    "cost_rub": None,
                    "error": _code,
                    "burst": _burst,
                }
            return _generate_banner_after_artifact_lock(
                db=db, account_id=account_id, prompt=prompt, save_path=save_path,
                model=model, quality=quality, size=size, operation=operation, provenance=provenance,
            )


def _generate_banner_after_artifact_lock(
    db, account_id, prompt, save_path, model, quality, size, operation, provenance=None
):
    """Paid portion; caller must hold `_artifact_generation_lock`."""
    import time
    from app.usage import log_usage
    from app.services.openai_image_transport import generate_image_http

    output_path = Path(save_path)
    run_id = (
        f"banner:{account_id}:"
        f"{int(time.time() * 1000000)}"
    )

    est_kopeks = estimate_kopeks(
        1,
        model=model,
        quality=quality,
        with_reference=False,
    )

    token = budget_reserve(
        account_id,
        run_id,
        est_kopeks,
        operation,
        1,
    )

    reserve_status = str((token or {}).get("status") or "") if isinstance(token, dict) else ""
    if reserve_status not in {"reserved", "observe", "exists"}:
        _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                        event_code="AI_BUDGET_RESERVE_FAILED", status="blocked",
                        intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                        details={"status": reserve_status})
        return {
            "ok": False,
            "path": None,
            "request_id": None,
            "cost_rub": None,
            "error": "AI_BUDGET_RESERVE_FAILED",
            "budget": token,
        }

    try:
        allowance = budget_allow(run_id)
        allowed = bool((allowance or {}).get("allowed")) if isinstance(allowance, dict) else bool(allowance)
        if not allowed:
            budget_release(run_id)
            _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                            event_code="AI_BUDGET_NOT_ALLOWED", status="blocked",
                            intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                            details={"reason": str((allowance or {}).get("reason") or "") if isinstance(allowance,dict) else ""})

            return {
                "ok": False,
                "path": None,
                "request_id": None,
                "cost_rub": None,
                "error": "AI_BUDGET_NOT_ALLOWED",
                "budget": allowance,
            }

        _provider_idem="boris-image:"+hashlib.sha256(
            (str(account_id)+":"+str(save_path)).encode("utf-8","ignore")
        ).hexdigest()
        # Crash-before-response barrier: from this point the provider outcome is
        # economically unknown until bytes are durably persisted. A worker death
        # cannot silently repurchase the same deterministic save_path intent.
        _write_uncertain_intent(save_path, {
            "schema":1,"status":"provider_in_progress","account_id":str(account_id),
            "run_id":run_id,"provider_idempotency_key":_provider_idem,
            "provider_request_id":None,"error_code":"provider_outcome_unknown",
            "created_at_unix":int(time.time()),
        })
        result = generate_image_http(
            prompt=prompt,
            size=size,
            quality=quality,
            model=model,
            max_attempts=1,
            account_id=account_id,
            idempotency_key=_provider_idem,
        )

        if not result.get("ok"):
            if result.get("ambiguous_transport"):
                # The provider may already have accepted/charged this exact image
                # intent. Persist a durable quarantine marker keyed by save_path;
                # subsequent identical calls stop before provider access instead
                # of guessing and buying a duplicate image.
                _write_uncertain_intent(save_path, {
                    "schema": 1, "status": "provider_outcome_uncertain",
                    "account_id": str(account_id), "run_id": run_id,
                    "provider_idempotency_key": _provider_idem,
                    "provider_request_id": result.get("request_id"),
                    "error_code": result.get("error_code") or "transport_outcome_unknown",
                    "created_at_unix": int(time.time()),
                })
                _audit_ai_guard(account_id=account_id, source="image_generation", operation=operation,
                                event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                                intent_key="image:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                details={"reason": str(result.get("error_code") or "transport_outcome_unknown")})
                budget_release(run_id)
                return {
                    "ok": False, "path": None, "request_id": result.get("request_id"),
                    "cost_rub": None, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
                    "ambiguous_provider_outcome": True,
                }
            # Definitive provider HTTP failure means no ambiguous paid outcome;
            # this exact intent may be retried later under normal retry policy.
            _clear_uncertain_intent(save_path)
            budget_release(run_id)

            return {
                "ok": False,
                "path": None,
                "request_id": result.get("request_id"),
                "cost_rub": None,
                "error": "OPENAI_IMAGE_GENERATION_FAILED",
            }

        provider_request_id = str(result.get("request_id") or "").strip()
        # Successful provider response without x-request-id is an ambiguous
        # financial success, not an exact provider identity. Preserve the spend
        # under an explicitly marked accounting id and persist the bytes; never
        # silently retry the paid request. When x-request-id exists, keep it exact.
        request_id = provider_request_id or ("ambiguous-image:" + account_id + ":" + hashlib.sha256(
            (str(save_path)+":"+str(time.time_ns())).encode("utf-8", "ignore")
        ).hexdigest()[:32])
        request_id_exact = bool(provider_request_id)

        cost = log_usage(
            account_id,
            "openai",
            model,
            operation,
            images=1,
            request_id=request_id,
            size=result.get("size") or size,
            quality=result.get("quality") or quality,
            usage_details={**(result.get("usage") or {}), "boris_provenance": {str(k)[:64]: v for k,v in (provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}},
        )
        # Provider cost is already incurred once a successful paid response is
        # logged. Commit before decoding/persisting bytes so a local disk error
        # cannot make the financial ledger pretend the provider call was free.
        budget_commit(run_id, request_id)

        payload = result.get("path_data")
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            raise ValueError("OPENAI_IMAGE_BYTES_EMPTY")

        output_path = _atomic_write_bytes(save_path, bytes(payload))

        if not _valid_image_artifact(output_path):
            _write_uncertain_intent(save_path, {
                "status": "artifact_invalid_after_paid_response",
                "error_code": "PAID_IMAGE_ARTIFACT_INVALID",
                "provider_request_id": provider_request_id or None,
            })
            raise IOError("PAID_IMAGE_ARTIFACT_INVALID")
        _clear_uncertain_intent(save_path)

        return {
            "ok": True,
            "path": save_path,
            "request_id": request_id,
            "provider_request_id": provider_request_id or None,
            "request_id_exact": request_id_exact,
            "ambiguous_success": not request_id_exact,
            "cost_rub": float(cost or 0),
            "error": None,
        }

    except Exception:
        try:
            budget_release(run_id)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=553)
        raise

def generate(db, account_id, prompt, save_path, model=MODEL, quality="medium",
             size=DEFAULT_SIZE, operation="photo_master", reference_path=None,
             burst_override=False, provenance=None):
    """Один платный кадр. reference_path -> ветка edits, иначе generations."""
    if reference_path:
        output_path = Path(save_path)
        if output_path.is_file():
            if not _valid_image_artifact(output_path):
                res = _invalid_existing_artifact_result(account_id, operation, save_path, source="image_edit")
                res["operation_used"] = "edits"
                return res
            _clear_uncertain_intent(save_path)
            return {"ok": True, "path": save_path, "request_id": None, "cost_rub": 0.0,
                    "operation_used": "edits", "error": None, "cache_hit": True}
        uncertain = _read_uncertain_intent(save_path)
        if uncertain is not None:
            _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                            event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                            intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                            details={"reason": str(uncertain.get("error_code") or uncertain.get("status") or "unknown")})
            return {"ok": False, "path": None, "request_id": uncertain.get("provider_request_id"),
                    "cost_rub": None, "operation_used": "edits",
                    "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
                    "ambiguous_provider_outcome": True, "intent": uncertain}
        with _artifact_generation_lock(db, account_id, save_path):
            if output_path.is_file():
                if not _valid_image_artifact(output_path):
                    res = _invalid_existing_artifact_result(account_id, operation, save_path, source="image_edit")
                    res["operation_used"] = "edits"
                    return res
                _clear_uncertain_intent(save_path)
                return {"ok": True, "path": save_path, "request_id": None, "cost_rub": 0.0,
                        "operation_used": "edits", "error": None, "cache_hit": True}
            uncertain = _read_uncertain_intent(save_path)
            if uncertain is not None:
                _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                                intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                details={"reason": str(uncertain.get("error_code") or uncertain.get("status") or "unknown")})
                return {"ok": False, "path": None, "request_id": uncertain.get("provider_request_id"),
                        "cost_rub": None, "operation_used": "edits",
                        "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
                        "ambiguous_provider_outcome": True, "intent": uncertain}
            _reference_cost_guard = banner_reference_cost_guard(operation, model=model, quality=quality)
            if not _reference_cost_guard.get("allowed"):
                _audit_ai_guard(
                    account_id=account_id, source="image_edit", operation=operation,
                    event_code="BANNER_REFERENCE_COST_CAP_EXCEEDED", status="blocked",
                    intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                    details=_reference_cost_guard,
                )
                return {
                    "ok": False, "path": None, "request_id": None, "cost_rub": None,
                    "operation_used": "edits", "error": "BANNER_REFERENCE_COST_CAP_EXCEEDED",
                    "estimated_cost_rub": _reference_cost_guard.get("estimated_cost_rub"),
                    "max_cost_rub": _reference_cost_guard.get("max_cost_rub"),
                    "cost_guard": _reference_cost_guard,
                }
            with _paid_image_account_guard(db, account_id, override=burst_override) as _burst:
                if not _burst.get("allowed"):
                    _code=str(_burst.get("code") or "PAID_IMAGE_BURST_CAP")
                    _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                    event_code=_code, status="blocked",
                                    intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                    details=_burst)
                    return {"ok":False,"path":None,"request_id":None,"cost_rub":None,
                            "operation_used":"edits","error":_code,"burst":_burst}
                # Reference edits were historically outside ai_budget. Reserve before
                # the first possible paid request and gate the single provider attempt.
                run_id=f"image-edit:{account_id}:{int(time.time()*1000000)}"
                per_call=estimate_kopeks(1, model=model, quality=quality, with_reference=False)+EST_REFERENCE_EXTRA
                token=budget_reserve(account_id,run_id,per_call*HARD_MAX_RETRY,operation,HARD_MAX_RETRY)
                reserve_status=str((token or {}).get("status") or "") if isinstance(token,dict) else ""
                if reserve_status not in {"reserved","observe","exists"}:
                    _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                    event_code="AI_BUDGET_RESERVE_FAILED", status="blocked",
                                    intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                    details={"status": reserve_status})
                    return {"ok":False,"path":None,"request_id":None,"cost_rub":None,
                            "operation_used":"edits","error":"AI_BUDGET_RESERVE_FAILED","budget":token}
                def _allow_edit_attempt():
                    allowance=budget_allow(run_id)
                    return bool((allowance or {}).get("allowed")) if isinstance(allowance,dict) else bool(allowance)
                def _commit_paid_edit(request_id):
                    if request_id:
                        budget_commit(run_id, request_id)
                try:
                    if not _allow_edit_attempt():
                        _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                        event_code="AI_BUDGET_NOT_ALLOWED", status="blocked",
                                        intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32])
                        return {"ok":False,"path":None,"request_id":None,"cost_rub":None,
                                "operation_used":"edits","error":"AI_BUDGET_NOT_ALLOWED"}
                    _edit_provider_idem="boris-image-edit:"+hashlib.sha256(
                        (str(account_id)+":"+str(save_path)).encode("utf-8","ignore")
                    ).hexdigest()
                    _write_uncertain_intent(save_path, {
                        "schema":1,"status":"provider_in_progress","account_id":str(account_id),
                        "run_id":run_id,"provider_idempotency_key":_edit_provider_idem,
                        "provider_request_id":None,"error_code":"provider_outcome_unknown",
                        "created_at_unix":int(time.time()),
                    })
                    result = _edit_with_reference(account_id, prompt, save_path, reference_path,
                                                  model, quality, size, operation,
                                                  allow_attempt=None,
                                                  on_paid_request=_commit_paid_edit,
                                                  provenance=provenance)
                    if isinstance(result, dict) and result.get("ambiguous_provider_outcome"):
                        _write_uncertain_intent(save_path, {
                            "schema": 1,
                            "status": "provider_outcome_uncertain",
                            "account_id": str(account_id),
                            "run_id": run_id,
                            "provider_idempotency_key": result.get("provider_idempotency_key"),
                            "provider_request_id": result.get("provider_request_id") or result.get("request_id"),
                            "error_code": result.get("error_code") or "edit_transport_outcome_unknown",
                            "created_at_unix": int(time.time()),
                        })
                        _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                        event_code="PAID_IMAGE_OUTCOME_UNCERTAIN", status="quarantined",
                                        intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32],
                                        details={"reason": str(result.get("error_code") or "edit_transport_outcome_unknown")})
                        result["error"] = "PAID_IMAGE_OUTCOME_UNCERTAIN"
                    elif isinstance(result, dict) and str(result.get("error") or "") == "AI_BUDGET_NOT_ALLOWED":
                        _clear_uncertain_intent(save_path)
                        _audit_ai_guard(account_id=account_id, source="image_edit", operation=operation,
                                        event_code="AI_BUDGET_NOT_ALLOWED", status="blocked",
                                        intent_key="image-edit:"+hashlib.sha256((str(account_id)+":"+str(save_path)).encode("utf-8","ignore")).hexdigest()[:32])
                    elif isinstance(result, dict) and result.get("ok"):
                        _clear_uncertain_intent(save_path)
                    else:
                        # A normal provider HTTP rejection is definitive; only
                        # ambiguous outcomes remain quarantined for manual reconcile.
                        _clear_uncertain_intent(save_path)
                    return result
                finally:
                    # Release only the unused reservation; committed provider cost is
                    # immutable in ai_budget/api_usage.
                    budget_release(run_id)
    else:
        # Keep the exact provider request_id/cost from the canonical guarded call.
        # The old adapter returned only a path and then guessed the request_id from
        # "latest image in the last 5 seconds", which is unsafe under concurrency.
        res = generate_banner_financially_guarded(
            db=db, account_id=account_id, prompt=prompt, save_path=save_path,
            model=model, quality=quality, size=size, operation=operation,
            burst_override=burst_override, provenance=provenance,
        )
        if isinstance(res, dict):
            res.setdefault("operation_used", "generations")
            return res
        return {"ok":False,"path":None,"request_id":None,"cost_rub":None,
                "operation_used":"generations","error":"INVALID_IMAGE_SERVICE_RESULT"}


def _edit_with_reference(account_id, prompt, save_path, reference_path, model,
                         quality, size, operation, allow_attempt=None,
                         on_paid_request=None, provenance=None):
    """Ветка /v1/images/edits: мастер-кадр уходит как reference.

    Именно это даёт согласованность серии: модель видит исходное изделие, а не
    пересобирает его из текста заново.
    """
    import base64
    import requests
    from app.services.reliability import dependency_call, ProviderDeferred, CircuitOpen

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "нет OPENAI_API_KEY", "path": None,
                "request_id": None, "cost_rub": None, "operation_used": "edits"}
    provider_idem = "boris-image-edit:" + hashlib.sha256(
        (str(account_id)+":"+str(save_path)).encode("utf-8","ignore")
    ).hexdigest()
    for attempt in (1,):
        try:
            if allow_attempt is not None and not allow_attempt():
                return {"ok": False, "error": "AI_BUDGET_NOT_ALLOWED", "path": None,
                        "request_id": None, "cost_rub": None, "operation_used": "edits"}
            import mimetypes
            from app.services.ai_privacy import sanitize_external_reference_image_bytes
            with open(reference_path, "rb") as f:
                _clean_bytes, _clean_mime = sanitize_external_reference_image_bytes(
                    f.read(), max_side=384, blur_radius=3.5
                )
                files = {"image[]": ("reference-image", _clean_bytes, _clean_mime)}
            from app.services.ai_privacy import safe_external_ai_payload
            external_prompt, _privacy_vault = safe_external_ai_payload(str(prompt))
            data = {"model": model, "prompt": external_prompt, "size": size,
                    "quality": quality, "n": "1"}
            # Use the same production OpenAI transport policy as normal image
            # generation. Do not pin edits to the international scraping proxy:
            # that proxy is for geo-blocked content providers and used to be a
            # single point of failure for every reference edit.
            resp = dependency_call(
                "openai.images.edit", requests.post,
                "https://api.openai.com/v1/images/edits",
                headers={
                    "Authorization": "Bearer %s" % api_key,
                    "Idempotency-Key": provider_idem,
                },
                files=files, data=data, timeout=300, account_id=account_id,
                tenant_limit_per_minute=12)
            if resp.status_code != 200:
                print("[image_service] edits попытка %d: %s %s"
                      % (attempt, resp.status_code, resp.text[:180]), flush=True)
                return {"ok": False, "error": "OPENAI_IMAGE_EDIT_HTTP_ERROR", "path": None,
                        "request_id": str(resp.headers.get("x-request-id") or "").strip() or None,
                        "cost_rub": None, "operation_used": "edits",
                        "error_code": "provider_http_error", "ambiguous_provider_outcome": False}
            provider_rid = str(resp.headers.get("x-request-id") or "").strip()
            rid = provider_rid or ("ambiguous-edit:" + str(account_id) + ":" + hashlib.sha256(
                (str(save_path)+":"+str(time.time_ns())).encode("utf-8", "ignore")
            ).hexdigest()[:32])
            rid_exact = bool(provider_rid)
            try:
                body = resp.json()
            except Exception:
                return {"ok": False, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN", "path": None,
                        "request_id": provider_rid or None, "provider_request_id": provider_rid or None,
                        "provider_idempotency_key": provider_idem, "cost_rub": None,
                        "operation_used": "edits", "error_code": "provider_200_unparseable",
                        "ambiguous_provider_outcome": True}
            b64 = (body.get("data") or [{}])[0].get("b64_json")
            if not b64:
                return {"ok": False, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN", "path": None,
                        "request_id": provider_rid or None, "provider_request_id": provider_rid or None,
                        "provider_idempotency_key": provider_idem, "cost_rub": None,
                        "operation_used": "edits", "error_code": "provider_200_empty_image",
                        "ambiguous_provider_outcome": True}
            cost = 0.0
            cost = log_usage(account_id, "openai", model, operation,
                             images=1, request_id=rid,
                             size=body.get("size") or size,
                             quality=body.get("quality") or quality,
                             usage_details={
                                 **(body.get("usage") if isinstance(body.get("usage"), dict) else {}),
                                 "boris_provenance": {
                                     str(k)[:64]: v for k, v in (provenance or {}).items()
                                     if isinstance(v, (str, int, float, bool)) or v is None
                                 },
                             })
            # The provider has already charged this response. Commit financial
            # fact before local persistence so a disk/write failure cannot erase cost.
            if on_paid_request is not None:
                on_paid_request(rid)
            _atomic_write_bytes(save_path, base64.b64decode(b64))
            if not _valid_image_artifact(save_path):
                _write_uncertain_intent(save_path, {
                    "status": "artifact_invalid_after_paid_response",
                    "error_code": "PAID_IMAGE_ARTIFACT_INVALID",
                    "provider_request_id": provider_rid or None,
                })
                return {"ok": False, "path": None, "request_id": rid, "provider_request_id": provider_rid or None,
                        "cost_rub": float(cost or 0), "operation_used": "edits",
                        "error": "PAID_IMAGE_ARTIFACT_INVALID", "ambiguous_provider_outcome": True}
            return {"ok": True, "path": save_path, "request_id": rid,
                    "provider_request_id": provider_rid or None,
                    "request_id_exact": rid_exact, "ambiguous_success": not rid_exact,
                    "cost_rub": float(cost or 0), "operation_used": "edits",
                    "error": None}
        except (ProviderDeferred, CircuitOpen):
            raise
        except Exception as e:
            print("[image_service] edits попытка %d упала: %s"
                  % (attempt, repr(e)[:160]), flush=True)
            # A timeout/disconnect can happen after the provider accepted the edit.
            # Treat the financial outcome as unknown and quarantine this stable
            # save_path intent instead of blindly buying the same edit again.
            return {"ok": False, "error": "PAID_IMAGE_OUTCOME_UNCERTAIN", "path": None,
                    "request_id": None, "provider_request_id": None,
                    "provider_idempotency_key": provider_idem, "cost_rub": None,
                    "operation_used": "edits", "error_code": "edit_transport_outcome_unknown",
                    "ambiguous_provider_outcome": True}
    return {"ok": False, "error": "edits не удался после единственной безопасной попытки", "path": None,
            "request_id": None, "cost_rub": None, "operation_used": "edits"}


def estimate_range(shots, model=MODEL, quality="medium", with_reference=True):
    """Честный диапазон для клиента: обычный случай и случай со срывами связи."""
    low = estimate_kopeks(shots, model, quality, with_reference)
    return {"min_kopeks": low, "max_kopeks": int(low * RETRY_OVERHEAD),
            "text": "примерно %.0f \u20bd, при сбоях связи до %.0f \u20bd"
                    % (low / 100, low * RETRY_OVERHEAD / 100)}
