"""
Модуль управления ставками CPX-продвижения (оплата за просмотры).
API Avito: раздел "Настройка цены целевого действия" (cpxpromo).
Ставки и бюджеты — ВЕЗДЕ в копейках (bidPenny, valuePenny). В рубли делим на 100.
"""
from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from app.api.avito import get_avito_token
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/cpxpromo", tags=["cpxpromo"])

BASE = "https://api.avito.ru/cpxpromo/1"


def _token(account_id: str) -> str:
    """Достаём строку access_token из get_avito_token (учитываем оба формата)."""
    t = get_avito_token(account_id)
    if isinstance(t, dict):
        return t.get("access_token") or t.get("token") or ""
    return t or ""


def _hard_cap(account_id: str) -> float:
    from app.db.session import SessionLocal
    from app.services.marketing_money_policy import effective_hard_bid_cap
    db = SessionLocal()
    try:
        return float(effective_hard_bid_cap(db, account_id))
    finally:
        db.close()


def _money_state(account_id: str) -> dict:
    from app.db.session import SessionLocal
    from app.services.marketing_money_policy import effective_daily_budget_limit, latest_confirmed_spend
    from app.services.marketing_signal_guard import money_spend_signal_eligible, money_raise_signals_eligible
    import math, time
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        limit = effective_daily_budget_limit(db, account_id)
        spend = latest_confirmed_spend(db, account_id, max_age_seconds=900) or {}
        # CPXPROMO_SPEND_SANITY_15M_V1: direct money endpoints share the same
        # <=15m fail-closed signal as the autonomous marketer. Reject corrupt,
        # non-finite or materially future provider timestamps at this boundary.
        if spend.get("status") == "ok":
            try:
                ts = float(spend.get("timestamp") or 0)
                val = float(spend.get("spent_today_rub"))
                if (not math.isfinite(ts) or not math.isfinite(val) or ts <= 0 or val < 0
                        or ts > time.time() + 60
                        or not money_spend_signal_eligible(db, account_id, spend)):
                    spend = {**spend, "status":"unknown", "reason":"spending_degraded_or_invalid"}
            except Exception:
                spend = {"status":"unknown", "reason":"spending_invalid"}
        # CPXPROMO_STATS_FRESH_15M_V1: a direct bid raise must not be weaker than
        # the autonomous marketer. Preserve stale daily_stats for reporting, but
        # only a complete same-day snapshot collected <=15m ago is money proof.
        stats_fresh = False
        stats_age_seconds = None
        try:
            # CPXPROMO_MOSCOW_STATS_DAY_V1: spend/KPI accounting follows the
            # BORIS Moscow marketing day. UTC date is still yesterday during
            # 00:00-02:59 MSK and would falsely reject otherwise fresh stats.
            from app.services.marketing_clock import marketing_today_iso
            day = marketing_today_iso()
            row = db.execute(__import__("sqlalchemy").text(
                "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
            ), {"a": account_id, "k": "daily_stats:" + day}).fetchone()
            if row:
                import json as _json
                stats = _json.loads(row[0] or "{}")
                collected = datetime.fromisoformat(str(stats.get("collected_at") or "").replace("Z", "+00:00"))
                if collected.tzinfo is None:
                    collected = collected.replace(tzinfo=timezone.utc)
                stats_age_seconds = (datetime.now(timezone.utc) - collected.astimezone(timezone.utc)).total_seconds()
                stats_fresh = bool((stats.get("completeness") or {}).get("complete") is True
                                   and -60 <= stats_age_seconds <= 900)
        except Exception:
            stats_fresh = False
            stats_age_seconds = None
        # CPXPROMO_FINAL_SPEND_STATS_COHERENCE_V1: direct CPX raises reuse the
        # canonical provider-boundary money guard, including the <=5m spend/stats
        # snapshot-coherence rule. This prevents a fresh spend sample from being
        # combined with older item evidence that is still individually <=15m.
        raise_signals_ok, raise_signals = money_raise_signals_eligible(
            db, account_id, max_age_seconds=900, max_snapshot_skew_seconds=300
        )
        return {"daily_budget_limit_rub": limit, "spend": spend,
                "stats_fresh": stats_fresh, "stats_age_seconds": stats_age_seconds,
                "raise_signals_ok": bool(raise_signals_ok),
                "raise_signals": raise_signals}
    finally:
        db.close()


# CPXPROMO_DIRECT_SHARED_THROTTLE_V1: all direct CPX surfaces, including
# read-only UI probes and spend-reducing remove calls, respect provider
# Retry-After. This prevents a UI/manual path from pressuring an account while
# another trusted worker has already observed a 429.
def _direct_cpx_throttle_remaining(account_id: str) -> int:
    from app.services.avito_account_throttle import account_throttle_remaining
    return int(account_throttle_remaining(account_id) or 0)

def _direct_cpx_record_429(account_id: str, response, source: str) -> int:
    from app.services.avito_account_throttle import record_account_throttle
    retry = 30
    try:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if raw:
            try:
                retry = max(0, int(float(raw)))
            except Exception:
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                retry = max(0, int((when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        retry = 30
    return int(record_account_throttle(account_id, retry or 30, source=source) or 0)


def _write_blocked(account_id: str) -> tuple[bool, str | None]:
    """Tenant-wide master OFF before any CPX mutation or token lookup."""
    from app.db.session import SessionLocal
    from app.services.reliability import module_blocked
    db = SessionLocal()
    try:
        return module_blocked(db, "marketing", account_id)
    finally:
        db.close()


def _direct_unresolved_money_receipt(account_id: str, item_id: int) -> dict | None:
    """Return any unresolved CPX write for this item before new provider I/O.

    A transport/5xx ambiguity is durable state, not merely a response to one
    HTTP caller.  Until the canonical receipt reconciler observes provider
    truth, every later direct request for the same item must fail closed.
    """
    from sqlalchemy import text
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT request_key,status,action,old_bid_penny,intended_bid_penny,attempted_at,updated_at
            FROM cpx_execution_receipts
            WHERE account_id=:a AND item_id=:i
              AND action IN ('raise','lower','archive')
              AND status IN ('attempting','delivery_unknown')
            ORDER BY id DESC LIMIT 1
        """), {"a": str(account_id), "i": int(item_id)}).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


class ItemIDsBody(BaseModel):
    account_id: str = "otdushi"
    item_ids: list[int]


@router.get("/bids/{item_id}")
def get_bids(item_id: int, account_id: str = "otdushi"):
    """Детальная инфо по одному объявлению: действующая ставка, прогнозы бюджет→просмотры.
    GET /cpxpromo/1/getBids/{itemId} (лимит 20 RPM)."""
    _retry = _direct_cpx_throttle_remaining(account_id)
    if _retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
    tok = _token(account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    # CPXPROMO_READ_FINAL_SHARED_THROTTLE_RECHECK_V1: token resolution can race
    # with a sibling 429; do not issue an extra CPX read once shared cooldown exists.
    _retry = _direct_cpx_throttle_remaining(account_id)
    if _retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
    try:
        r = httpx.get(f"{BASE}/getBids/{item_id}",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    except Exception as exc:
        return {"status":"degraded","reason_code":"provider_transport_error",
                "message":"Avito временно недоступен; чтение ставки будет повторено штатным циклом",
                "error_type":type(exc).__name__}
    if r.status_code != 200:
        if r.status_code == 429:
            _retry = _direct_cpx_record_429(account_id, r, "cpxpromo_bids_429")
            return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    return {"status": "ok", "data": r.json()}


@router.post("/promotions")
def get_promotions(body: ItemIDsBody):
    """Текущие ставки/бюджеты пачкой (до 200 объявлений) — основной метод для таблицы.
    POST /cpxpromo/1/getPromotionsByItemIds (лимит 400 RPM)."""
    _retry = _direct_cpx_throttle_remaining(body.account_id)
    if _retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    _retry = _direct_cpx_throttle_remaining(body.account_id)
    if _retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
    try:
        r = httpx.post(f"{BASE}/getPromotionsByItemIds",
                       headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                       json={"itemIDs": body.item_ids[:200]}, timeout=30)
    except Exception as exc:
        return {"status":"degraded","reason_code":"provider_transport_error",
                "message":"Avito временно недоступен; чтение продвижений будет повторено штатным циклом",
                "error_type":type(exc).__name__}
    if r.status_code != 200:
        if r.status_code == 429:
            _retry = _direct_cpx_record_429(body.account_id, r, "cpxpromo_promotions_429")
            return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry}
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    return {"status": "ok", "data": r.json()}


@router.post("/remove")
def remove_promotion(body: ItemIDsBody):
    """Остановить продвижение, переключить на цены из прайс-листа.
    POST /cpxpromo/1/remove (лимит 300 RPM). МЕНЯЕТ реальное состояние — логируем."""
    from app.api.avito import _audit_log
    blocked, reason = _write_blocked(body.account_id)
    if blocked:
        return {"status":"blocked","reason_code":"account_disabled","message":reason or "Действия BORIS для аккаунта отключены","changed_avito":False}
    # CPXPROMO_REMOVE_BATCH_CAP_V1: direct remove is still a live client
    # mutation. The provider accepts large batches, but BORIS permits at most
    # five live actions per run. Reject oversized cohorts before throttle/token
    # lookup so an invalid request creates zero provider pressure.
    # CPXPROMO_REMOVE_DEDUPE_V1: one logical item may appear only once in a
    # direct remove run. Duplicate IDs must not create duplicate provider writes,
    # duplicate durable receipts, or consume multiple money-action slots.
    _requested_ids = []
    _seen_remove_ids = set()
    for _raw_iid in body.item_ids[:200]:
        try:
            _iid = int(_raw_iid)
        except Exception:
            return {"status":"blocked","reason_code":"invalid_item_id",
                    "changed_avito":False}
        if _iid <= 0:
            return {"status":"blocked","reason_code":"invalid_item_id",
                    "changed_avito":False}
        if _iid in _seen_remove_ids:
            continue
        _seen_remove_ids.add(_iid)
        _requested_ids.append(_iid)
    _remove_run_cap = 5
    if len(_requested_ids) > _remove_run_cap:
        return {"status":"blocked","reason_code":"blocked_mass_action",
                "message":"За один запуск разрешено не более 5 изменений продвижения",
                "requested_actions":len(_requested_ids),"max_actions_run":_remove_run_cap,
                "changed_avito":False}
    _retry = _direct_cpx_throttle_remaining(body.account_id)
    if _retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled","retry_after_seconds":_retry,"changed_avito":False}
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}

    from app.db.session import SessionLocal as _RemoveSession
    from app.services.autonomy import can_execute_live_action as _remove_live_guard
    from app.api.cpx_advisor import _cpx_receipt_prepare as _remove_receipt_prepare, _cpx_receipt_mark as _remove_receipt_mark
    from app.services.action_log import log_action as _remove_log_action, ACTOR_BORIS_AUTO as _REMOVE_ACTOR
    import uuid as _uuid_remove
    from datetime import datetime as _remove_dt, timezone as _remove_tz
    # CPXPROMO_REMOVE_RUN_SCOPE_V1: all items in one direct remove call/hour
    # share one logical run prefix so action_log can enforce <=5/run and the
    # Moscow-day journal counts every successful per-item mutation.
    _remove_run_prefix = "cpxpromo-remove-" + _remove_dt.now(_remove_tz.utc).strftime("%Y%m%dT%H")

    results = []
    throttled = False
    throttle_retry = 0
    # CPXPROMO_REMOVE_DURABLE_RECEIPT_V1: every removal gets the same durable
    # exactly-once receipt/reconciliation contract as other CPX mutations. A
    # timeout or provider 5xx is ambiguous and must never be blindly replayed.
    for iid in _requested_ids:
        _pending = _direct_unresolved_money_receipt(body.account_id, iid)
        if _pending:
            results.append({"itemID":iid,"status":"not_attempted","code":"reconciliation_pending",
                            "reason":"cpx_execution_reconciliation",
                            "request_key":_pending.get("request_key")})
            continue

        _retry = _direct_cpx_throttle_remaining(body.account_id)
        if _retry > 0:
            throttled = True
            throttle_retry = int(_retry)
            results.append({"itemID": iid, "code": 429, "status":"not_attempted",
                            "reason":"avito_account_throttled",
                            "retry_after_seconds":throttle_retry})
            break

        _request_key = _remove_run_prefix + ":" + _uuid_remove.uuid4().hex
        _guard_db = _RemoveSession()
        try:
            _guard = _remove_live_guard(
                _guard_db, body.account_id, "cpx.remove_promotion",
                {"actor":"boris_direct_api","trigger":"direct_cpxpromo_remove",
                 "source":"paid_tariff_kpi","request_id":_request_key},
                bid_context={"item_id":int(iid)},
                balance={"status":"UNKNOWN","value":None},
                mass_count=len(_requested_ids),
            )
        finally:
            try: _guard_db.close()
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=315)
        if not _guard.get("allowed"):
            results.append({"itemID":iid,"status":"not_attempted","code":"guard_blocked",
                            "reason":_guard.get("reason_code") or "blocked_money_guard"})
            continue

        _receipt, _created = _remove_receipt_prepare(
            body.account_id, _request_key, iid, "archive", "paid_tariff_kpi", None, None
        )
        if not _created:
            results.append({"itemID":iid,"status":"not_attempted","code":"receipt_blocked",
                            "reason":(_receipt or {}).get("reason_code") or "cpx_execution_reconciliation",
                            "request_key":_request_key})
            continue
        _remove_receipt_mark(body.account_id, _request_key, "attempting")

        # CPXPROMO_REMOVE_FINAL_SHARED_THROTTLE_V1: receipt/guard persistence can
        # race with a sibling 429; recheck at the last local boundary before POST.
        _retry = _direct_cpx_throttle_remaining(body.account_id)
        if _retry > 0:
            _remove_receipt_mark(body.account_id, _request_key, "failed", http_status=429,
                                 result={"reason":"shared_avito_account_throttle",
                                         "retry_after_seconds":int(_retry)})
            throttled = True
            throttle_retry = int(_retry)
            results.append({"itemID":iid,"code":429,"status":"not_attempted",
                            "reason":"avito_account_throttled","request_key":_request_key,
                            "retry_after_seconds":throttle_retry})
            break
        try:
            from app.api.cpx_advisor import _provider_remove_promotion
            r = _provider_remove_promotion(
                account_id=body.account_id, token=tok, item_id=iid, timeout=30
            )
        except Exception as exc:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown", error=repr(exc))
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key})
            continue

        if r.status_code == 429:
            throttle_retry = _direct_cpx_record_429(body.account_id, r, "cpxpromo_remove_429")
            _remove_receipt_mark(body.account_id, _request_key, "failed", http_status=429,
                                 result={"reason":"avito_account_throttled",
                                         "retry_after_seconds":int(throttle_retry)})
            results.append({"itemID":iid,"code":429,"status":"blocked",
                            "reason":"avito_account_throttled","request_key":_request_key,
                            "retry_after_seconds":throttle_retry})
            throttled = True
            break
        if int(r.status_code) >= 500:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"provider_5xx_ambiguous","body":r.text[:200]})
            results.append({"itemID":iid,"code":int(r.status_code),"status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key})
            continue
        if r.status_code not in (200,201,204):
            _remove_receipt_mark(body.account_id, _request_key, "failed",
                                 http_status=int(r.status_code),
                                 result={"reason":"provider_rejected","body":r.text[:200]})
            results.append({"itemID":iid,"code":int(r.status_code),"status":"error",
                            "reason":"provider_rejected","request_key":_request_key})
            continue

        # CPXPROMO_REMOVE_EXACT_POSTWRITE_VERIFY_V1: provider HTTP success only
        # proves acceptance of the remove request. Do not claim the promotion is
        # actually off, or consume a successful action-log slot, until a fresh
        # read-back confirms that neither manual nor auto promotion remains.
        _remove_receipt_mark(body.account_id, _request_key, "attempting",
                             http_status=int(r.status_code),
                             result={"source":"cpxpromo_direct_remove","provider_status":"accepted"})
        _verify_retry = _direct_cpx_throttle_remaining(body.account_id)
        if _verify_retry > 0:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"postremove_verify_throttled",
                                         "retry_after_seconds":int(_verify_retry)})
            throttled = True
            throttle_retry = int(_verify_retry)
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key,
                            "retry_after_seconds":throttle_retry})
            break
        try:
            _vr = httpx.post(f"{BASE}/getPromotionsByItemIds",
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                             json={"itemIDs":[iid]}, timeout=30)
        except Exception as exc:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code), error=repr(exc),
                                 result={"reason":"postremove_verify_transport"})
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key})
            continue
        if _vr.status_code == 429:
            throttle_retry = _direct_cpx_record_429(body.account_id, _vr, "cpxpromo_remove_verify_429")
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"postremove_verify_429",
                                         "retry_after_seconds":int(throttle_retry)})
            throttled = True
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key,
                            "retry_after_seconds":throttle_retry})
            break
        if _vr.status_code != 200:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"postremove_verify_http",
                                         "verify_http_status":int(_vr.status_code)})
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key})
            continue
        try:
            _verify_payload = _vr.json() or {}
            _verify_items = _verify_payload.get("items") if isinstance(_verify_payload, dict) else None
            if not isinstance(_verify_items, list):
                _verify_items = None
        except Exception:
            _verify_items = None
        _verify_item = next((x for x in (_verify_items or []) if isinstance(x,dict)
                             and int(x.get("itemID") or 0) == int(iid)), {})
        # Match the canonical archive receipt reconciler: a valid provider
        # response with no promotion row for the requested item is proof that
        # promotion is absent. Invalid/unparseable response is never proof.
        _promotion_off = bool(
            _verify_items is not None
            and not _verify_item.get("manualPromotion")
            and not _verify_item.get("autoPromotion")
        )
        if not _promotion_off:
            _remove_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"postremove_promotion_still_present_or_unproven"},
                                 error="post-write verification did not prove promotion off")
            results.append({"itemID":iid,"code":"delivery_unknown","status":"blocked",
                            "reason":"delivery_unknown","request_key":_request_key})
            continue
        _remove_receipt_mark(body.account_id, _request_key, "succeeded",
                             http_status=int(r.status_code),
                             result={"source":"cpxpromo_direct_remove",
                                     "reconciliation":"exact_postremove_readback",
                                     "promotion_active":False})
        # CPXPROMO_REMOVE_PER_ITEM_ACTION_LOG_V1: guard capacity is computed
        # from action_log. Persist every verified successful item immediately;
        # one aggregate row for a five-item batch would undercount run/day caps.
        _remove_log_action(
            account_id=body.account_id, action="Остановил продвижение",
            object_kind="объявление", object_name=str(iid),
            before_val="продвижение включено", after_val="продвижение выключено",
            reason="direct CPX remove; состояние подтверждено Avito", actor=_REMOVE_ACTOR, source="paid_tariff_kpi",
            trigger="direct_cpxpromo_remove", mandate_id=_guard.get("mandate_id"),
            mandate_version=_guard.get("mandate_version"), request_id=_request_key,
            balance_status="UNKNOWN", guard_reason=_guard.get("reason_code"),
        )
        results.append({"itemID":iid,"code":int(r.status_code),"status":"ok",
                        "request_key":_request_key})

    changed_ids = [x.get("itemID") for x in results if x.get("status") == "ok"]
    if changed_ids:
        _audit_log(body.account_id, "cpxpromo_remove", f"остановлено продвижение: {changed_ids}", "user")
    if throttled:
        return {"status":"blocked","reason_code":"avito_account_throttled",
                "retry_after_seconds":int(throttle_retry or 0),
                "changed_avito":bool(changed_ids),"results":results}
    if any(x.get("reason") == "delivery_unknown" for x in results):
        return {"status":"blocked","reason_code":"delivery_unknown",
                "changed_avito":bool(changed_ids),"results":results}
    _safety_blocked = [x for x in results if x.get("status") in {"not_attempted","blocked","error"}]
    if _safety_blocked:
        _first_reason = str(_safety_blocked[0].get("reason") or "cpx_remove_partial_block")
        return {"status":"blocked","reason_code":_first_reason,
                "changed_avito":bool(changed_ids),"results":results}
    return {"status": "ok", "changed_avito":bool(changed_ids), "results": results}


class SetManualBody(BaseModel):
    account_id: str = "otdushi"
    item_id: int
    action_type_id: int = 5          # 1-звонок | 5-пакет кликов | 7-мессенджер
    bid_penny: int                   # цена целевого действия в копейках (700 = 7р)
    limit_penny: int | None = None   # дневной лимит трат в копейках (None = без лимита)


class SetAutoBody(BaseModel):
    account_id: str = "otdushi"
    item_id: int
    action_type_id: int = 5
    budget_penny: int
    budget_type: str = "1d"          # "1d" сутки | "7d" неделя | "30d" месяц


@router.post("/set_manual")
def set_manual(body: SetManualBody):
    """Ручная ставка + дневной лимит. POST /cpxpromo/1/setManual (scope cpxpromo:edit).
    МЕНЯЕТ реальные расходы клиента — обязательный аудит."""
    from app.api.avito import _audit_log
    blocked, reason = _write_blocked(body.account_id)
    if blocked:
        return {"status":"blocked","reason_code":"account_disabled","message":reason or "Действия BORIS для аккаунта отключены","changed_avito":False}
    # CPXPROMO_DIRECT_DURABLE_AMBIGUITY_FENCE_V1: an earlier write with an
    # unknown provider outcome blocks every later direct write for the item.
    # Reconciliation is read-only and will clear this fence only after Avito
    # truth proves either the intended or the old bid.
    _pending_receipt = _direct_unresolved_money_receipt(body.account_id, body.item_id)
    if _pending_receipt:
        return {"status":"blocked","reason_code":"cpx_execution_reconciliation",
                "message":"Предыдущее изменение ставки ещё сверяется с Avito; слепой повтор запрещён",
                "request_key":_pending_receipt.get("request_key"),
                "execution_status":_pending_receipt.get("status"),"changed_avito":False}
    cap = _hard_cap(body.account_id)
    requested_rub = float(body.bid_penny or 0) / 100.0
    if cap > 0 and requested_rub > cap + 1e-9:
        return {"status":"blocked","reason_code":"hard_bid_cap",
                "message":f"Ставка {requested_rub:.0f} ₽ выше красной цены {cap:.0f} ₽",
                "hard_max_bid_rub":cap,"changed_avito":False}
    money = _money_state(body.account_id)
    daily_limit = money.get("daily_budget_limit_rub")
    spend = money.get("spend") if isinstance(money.get("spend"), dict) else {}
    spent = float(spend.get("spent_today_rub") or 0) if spend.get("status") == "ok" else None
    # CPXPROMO_DAILY_BUDGET_PRETOKEN_GUARD_V1: an exhausted confirmed daily
    # budget must fail closed before throttle/token/provider I/O. This protects
    # direct/manual calls as well as autonomous callers from overspend.
    if daily_limit and float(daily_limit) > 0 and spent is not None and spent >= float(daily_limit) - 1e-9:
        return {"status":"blocked","reason_code":"blocked_daily_budget_exhausted",
                "message":"Дневной рекламный бюджет исчерпан",
                "daily_budget_limit_rub":float(daily_limit),"spent_today_rub":float(spent),
                "changed_avito":False}
    # CPXPROMO_DIRECT_ITEM_LIMIT_FAIL_CLOSED_V1: the authoritative BORIS budget
    # is the explicit account daily budget.  Provider-side per-item limit writes
    # are not enabled here because the canonical receipt reconciler can currently
    # prove the bid, but not prove an ambiguous limitPenny outcome.  Never claim
    # exactly-once safety for a field we cannot reconcile from provider truth.
    if body.limit_penny is not None:
        return {"status":"blocked","reason_code":"item_limit_mutation_not_authoritative",
                "message":"Лимит объявления не меняется через direct CPX; используйте явный дневной бюджет аккаунта",
                "daily_budget_limit_rub":float(daily_limit) if daily_limit else None,
                "changed_avito":False}
    from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
    # CPXPROMO_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1: direct money writes must not
    # immediately pressure a tenant while another trusted stage is inside a
    # provider-advertised Retry-After window.
    _shared_retry = account_throttle_remaining(body.account_id)
    if _shared_retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled",
                "retry_after_seconds":int(_shared_retry),
                "message":"Avito временно ограничил запросы по аккаунту; повтор после Retry-After",
                "changed_avito":False}
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    # CPXPROMO_DIRECTION_STEP_GUARD_V1: prove current bid before deciding whether
    # this direct mutation is a raise. A raise needs explicit positive budget,
    # fresh/sane spend, <90% budget use and an exact <=10% real bid delta.
    # CPXPROMO_DIRECTION_READ_FINAL_SHARED_THROTTLE_RECHECK_V1: token resolution
    # can race with a sibling 429. Re-read the shared tenant cooldown immediately
    # before the direction probe so a confirmed Retry-After stops further I/O.
    _direction_retry = account_throttle_remaining(body.account_id)
    if _direction_retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled",
                "retry_after_seconds":int(_direction_retry),
                "message":"Avito временно ограничил запросы по аккаунту; изменение не выполнено",
                "changed_avito":False}
    try:
        br = httpx.get(f"{BASE}/getBids/{body.item_id}",
                       headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        # CPXPROMO_DIRECTION_TRANSPORT_FAIL_CLOSED_V1: direction is money proof.
        # A transport failure before setManual is not permission to guess whether
        # the requested bid is a raise or lower. Return a stable degraded block so
        # autonomous callers can retry on their normal cycle without a route 500.
        return {"status":"blocked","reason_code":"blocked_bid_direction_unknown",
                "message":"Не удалось подтвердить текущую ставку из-за временной ошибки Avito; изменение не выполнено",
                "error_type":type(exc).__name__,"changed_avito":False}
    if br.status_code != 200:
        if br.status_code == 429:
            _retry_after = 30
            try:
                _raw_ra = str(br.headers.get("Retry-After") or "").strip()
                if _raw_ra:
                    try:
                        _retry_after = max(0, int(float(_raw_ra)))
                    except Exception:
                        from email.utils import parsedate_to_datetime as _parse_ra
                        from datetime import datetime as _dt_ra, timezone as _tz_ra
                        _when = _parse_ra(_raw_ra)
                        if _when.tzinfo is None:
                            _when = _when.replace(tzinfo=_tz_ra.utc)
                        _retry_after = max(0, int((_when.astimezone(_tz_ra.utc) - _dt_ra.now(_tz_ra.utc)).total_seconds()))
            except Exception:
                _retry_after = 30
            _retry_after = record_account_throttle(body.account_id, _retry_after or 30, source="cpxpromo_getbids_429")
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_retry_after),
                    "message":"Avito ограничил CPX-запросы; изменение не выполнено",
                    "changed_avito":False}
        return {"status":"blocked","reason_code":"blocked_bid_direction_unknown",
                "message":"Не удалось подтвердить текущую ставку; изменение не выполнено",
                "daily_budget_limit_rub":daily_limit,
                "spend_status":spend.get("status"),"changed_avito":False}
    current_manual = (br.json() or {}).get("manual") or {}
    current_rub = float(current_manual.get("bidPenny") or 0) / 100.0
    is_raise = requested_rub > current_rub + 1e-9
    if is_raise:
        # CPXPROMO_RAISE_RECHECK_AFTER_DIRECTION_V1: the live direction lookup
        # can spend up to 20s on provider I/O. Re-read the local money proof only
        # after direction is known so a snapshot cannot cross the 15m boundary
        # between the initial request preflight and the actual write.
        money = _money_state(body.account_id)
        daily_limit = money.get("daily_budget_limit_rub")
        spend = money.get("spend") if isinstance(money.get("spend"), dict) else {}
        spent = float(spend.get("spent_today_rub") or 0) if spend.get("status") == "ok" else None
        # CPXPROMO_RAISE_REQUIRES_FRESH_STATS_V1: direct raises share the same
        # complete <=15m inventory/stats requirement as autonomous money lanes.
        if not bool(money.get("stats_fresh")):
            return {"status":"blocked","reason_code":"blocked_stats_unavailable",
                    "message":"Нет свежей полной статистики Avito; повышение ставки запрещено",
                    "stats_age_seconds":money.get("stats_age_seconds"),"changed_avito":False}
        if not bool(money.get("raise_signals_ok")):
            return {"status":"blocked","reason_code":"money_signal_stale_or_degraded",
                    "message":"Spend/stats сигналы недостаточно согласованы для повышения ставки",
                    "money_signal":money.get("raise_signals"),"changed_avito":False}
        if not daily_limit or float(daily_limit) <= 0:
            return {"status":"blocked","reason_code":"no_explicit_daily_budget",
                    "message":"Повышение ставки запрещено без явного дневного бюджета",
                    "current_bid_rub":current_rub,"requested_bid_rub":requested_rub,"changed_avito":False}
        if spent is None:
            return {"status":"blocked","reason_code":"blocked_spendings_unavailable",
                    "message":"Нет свежих подтверждённых расходов Avito; повышение ставки запрещено",
                    "daily_budget_limit_rub":float(daily_limit),"changed_avito":False}
        if spent >= float(daily_limit) * 0.90 - 1e-9:
            _budget_reason = ("blocked_daily_budget_exhausted"
                              if spent >= float(daily_limit) - 1e-9
                              else "blocked_daily_budget_near_limit")
            return {"status":"blocked","reason_code":_budget_reason,
                    "message":"Рекламный бюджет близок к лимиту или исчерпан — повышение ставки запрещено",
                    "daily_budget_limit_rub":float(daily_limit),"spent_today_rub":spent,"changed_avito":False}
        if current_rub <= 0:
            return {"status":"blocked","reason_code":"blocked_bid_delta_unprovable",
                    "message":"Нельзя доказать процент повышения от нулевой текущей ставки",
                    "changed_avito":False}
        delta_pct = (requested_rub - current_rub) * 100.0 / current_rub
        if delta_pct > 10.0 + 1e-9:
            return {"status":"blocked","reason_code":"blocked_bid_delta_exceeded",
                    "message":"Повышение ставки превышает системный шаг 10%",
                    "current_bid_rub":current_rub,"requested_bid_rub":requested_rub,
                    "delta_pct":round(delta_pct,2),"changed_avito":False}
    # CPXPROMO_FINAL_RAISE_SIGNAL_RECHECK_V1: all earlier checks are necessary,
    # but the 15m proof must still be valid at the last local boundary before
    # provider mutation. This DB-only re-read prevents a near-expiry snapshot
    # from crossing the freshness limit during guard/audit work.
    if is_raise:
        final_money = _money_state(body.account_id)
        final_spend = final_money.get("spend") if isinstance(final_money.get("spend"), dict) else {}
        # CPXPROMO_FINAL_COHERENT_SIGNAL_RECHECK_V1: the final provider boundary
        # repeats the canonical <=15m + <=5m-coherence proof, not only individual
        # freshness. Any loss of coherence during direction/guard work blocks write.
        if (not bool(final_money.get("stats_fresh"))
                or final_spend.get("status") != "ok"
                or not bool(final_money.get("raise_signals_ok"))):
            return {"status":"blocked","reason_code":"money_signal_stale_or_degraded",
                    "message":"Денежные сигналы потеряли свежесть или согласованность перед изменением ставки",
                    "stats_age_seconds":final_money.get("stats_age_seconds"),
                    "money_signal":final_money.get("raise_signals"),
                    "changed_avito":False}

    # CPXPROMO_DIRECT_CANONICAL_MONEY_GUARD_V1: the direct API is still a live
    # money boundary.  It must use the same paid-KPI mandate, run/day caps,
    # hard-cap and step authority as systemd/autonomous lanes; route reachability
    # alone is never permission to mutate a client bid.
    _current_bid_penny = int(round(current_rub * 100.0))
    if int(body.bid_penny) == _current_bid_penny:
        return {"status":"ok","reason_code":"no_bid_change","changed_avito":False,
                "current_bid_rub":current_rub,"requested_bid_rub":requested_rub}

    _receipt_action = "raise" if is_raise else "lower"
    _operation = "cpx.raise_bid" if is_raise else "cpx.lower_bid"
    import uuid as _uuid_direct
    from datetime import datetime as _direct_dt, timezone as _direct_tz
    # CPXPROMO_DIRECT_ATOMIC_RUN_SCOPE_V1: the DB receipt trigger scopes the
    # authoritative max_actions_run by the first ':'-delimited request-key
    # segment.  A UUID-only prefix would make every HTTP request its own run and
    # silently bypass the <=5/run barrier.  Direct API writes therefore share a
    # bounded hourly execution window, while UUID keeps per-action idempotency.
    _direct_run_prefix = "cpxpromo-direct-" + _direct_dt.now(_direct_tz.utc).strftime("%Y%m%dT%H")
    _request_key = _direct_run_prefix + ":" + _uuid_direct.uuid4().hex
    from app.db.session import SessionLocal as _DirectSession
    from app.services.autonomy import can_execute_live_action as _direct_live_guard
    _guard_db = _DirectSession()
    try:
        _direct_guard = _direct_live_guard(
            _guard_db, body.account_id, _operation,
            {"actor":"boris_direct_api","trigger":"direct_cpxpromo",
             "source":"paid_tariff_kpi","request_id":_request_key},
            bid_context={"item_id":int(body.item_id),
                         "old_bid_rub":float(current_rub),
                         "new_bid_rub":float(requested_rub)},
            balance={"status":"UNKNOWN","value":None},
        )
    finally:
        try:
            _guard_db.close()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=717)
    if not _direct_guard.get("allowed"):
        return {"status":"blocked","reason_code":_direct_guard.get("reason_code") or "blocked_money_guard",
                "changed_avito":False,"guard":_direct_guard}

    # CPXPROMO_DIRECT_DURABLE_RECEIPT_V1: reserve the mutation in the canonical
    # atomic receipt ledger before provider write.  This is the durable exactly-
    # once fence used by the read-only reconciliation worker after timeout/5xx.
    from app.api.cpx_advisor import _cpx_receipt_prepare as _direct_receipt_prepare, _cpx_receipt_mark as _direct_receipt_mark
    _receipt, _receipt_created = _direct_receipt_prepare(
        body.account_id, _request_key, body.item_id, _receipt_action,
        "paid_tariff_kpi", _current_bid_penny, int(body.bid_penny)
    )
    if not _receipt_created:
        return {"status":"blocked","reason_code":(_receipt or {}).get("reason_code") or "cpx_execution_reconciliation",
                "execution_status":(_receipt or {}).get("status"),
                "request_key":_request_key,"changed_avito":False}
    _direct_receipt_mark(body.account_id, _request_key, "attempting")

    payload = {"actionTypeID": body.action_type_id, "bidPenny": body.bid_penny, "itemID": body.item_id}
    if body.limit_penny is not None:
        payload["limitPenny"] = body.limit_penny

    # CPXPROMO_FINAL_SHARED_THROTTLE_RECHECK_V1: compatibility marker for the
    # production rollout contract; V2 behavior keeps this fence at the last local boundary.
    # as the final local operation before the paid provider POST. Payload
    # construction happens first so there is no avoidable race window after
    # the last shared Retry-After observation.
    _final_shared_retry = account_throttle_remaining(body.account_id)
    if _final_shared_retry > 0:
        _direct_receipt_mark(body.account_id, _request_key, "failed", http_status=429,
                             result={"reason":"shared_avito_account_throttle",
                                     "retry_after_seconds":int(_final_shared_retry)})
        return {"status":"blocked","reason_code":"avito_account_throttled",
                "retry_after_seconds":int(_final_shared_retry),
                "message":"Avito временно ограничил запросы по аккаунту; изменение не выполнено",
                "request_key":_request_key,"changed_avito":False}

    # CPXPROMO_SETMANUAL_AMBIGUOUS_DELIVERY_FAIL_CLOSED_V1: once the provider
    # request is sent, a transport timeout/disconnect has an unknown outcome.
    # Surface that explicitly and forbid blind replay; callers must reconcile
    # provider truth before attempting another money write.
    try:
        from app.api.cpx_advisor import _provider_set_manual_bid
        r = _provider_set_manual_bid(
            account_id=body.account_id,
            token=tok,
            item_id=body.item_id,
            action_type_id=body.action_type_id,
            bid_penny=body.bid_penny,
            timeout=30,
        )
    except Exception as exc:
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown", error=repr(exc))
        return {"status":"blocked","reason_code":"delivery_unknown",
                "message":"Результат изменения ставки в Avito не подтверждён; автоматический повтор запрещён до сверки фактической ставки",
                "delivery_unknown":True,"changed_avito":False,
                "request_key":_request_key,"error_type":type(exc).__name__}
    if r.status_code != 200:
        if r.status_code == 429:
            _retry_after = 30
            try:
                _raw_ra = str(r.headers.get("Retry-After") or "").strip()
                if _raw_ra:
                    try:
                        _retry_after = max(0, int(float(_raw_ra)))
                    except Exception:
                        from email.utils import parsedate_to_datetime as _parse_write_ra
                        from datetime import datetime as _write_dt, timezone as _write_tz
                        _when = _parse_write_ra(_raw_ra)
                        if _when.tzinfo is None:
                            _when = _when.replace(tzinfo=_write_tz.utc)
                        _retry_after = max(0, int((_when.astimezone(_write_tz.utc) - _write_dt.now(_write_tz.utc)).total_seconds()))
            except Exception:
                _retry_after = 30
            _retry_after = record_account_throttle(body.account_id, _retry_after or 30, source="cpxpromo_setmanual_429")
            _direct_receipt_mark(body.account_id, _request_key, "failed", http_status=429,
                                 result={"reason":"avito_account_throttled",
                                         "retry_after_seconds":int(_retry_after)})
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_retry_after),
                    "message":"Avito ограничил CPX-запросы; изменение не выполнено",
                    "request_key":_request_key,"changed_avito":False}
        # CPXPROMO_SETMANUAL_AMBIGUOUS_5XX_FAIL_CLOSED_V1: a provider 5xx
        # after the paid write is not proof of rejection. Avito may have
        # committed the bid before an upstream/server error reached BORIS.
        # Treat 5xx like a transport ambiguity and forbid blind replay until
        # provider truth is reconciled.
        if int(r.status_code) >= 500:
            _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                                 http_status=int(r.status_code),
                                 result={"reason":"provider_5xx_ambiguous","body":r.text[:200]})
            return {"status":"blocked","reason_code":"delivery_unknown",
                    "message":"Avito вернул неоднозначную серверную ошибку после изменения ставки; автоматический повтор запрещён до сверки фактической ставки",
                    "delivery_unknown":True,"changed_avito":False,
                    "request_key":_request_key,"code":int(r.status_code)}
        _direct_receipt_mark(body.account_id, _request_key, "failed",
                             http_status=int(r.status_code),
                             result={"reason":"provider_rejected","body":r.text[:200]})
        return {"status": "error", "code": r.status_code, "message": r.text[:300],
                "request_key":_request_key,"changed_avito":False}
    # CPXPROMO_SETMANUAL_EXACT_POSTWRITE_VERIFY_V1: HTTP 200 only proves that
    # Avito accepted the request. Keep the durable receipt non-terminal until
    # an exact getBids read-back proves observed == intended. Any verification
    # failure is ambiguous delivery state, never permission to claim success.
    _direct_receipt_mark(body.account_id, _request_key, "attempting",
                         http_status=200,
                         result={"source":"cpxpromo_direct","provider_status":"accepted_pending_verify"})
    _verify_retry = account_throttle_remaining(body.account_id)
    if _verify_retry > 0:
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                             http_status=200, result={"reason":"postwrite_verify_throttled"})
        return {"status":"blocked","reason_code":"delivery_unknown",
                "message":"Avito принял изменение, но точная ставка ещё не подтверждена; слепой повтор запрещён",
                "retry_after_seconds":int(_verify_retry),"request_key":_request_key,
                "changed_avito":False}
    try:
        _vr = httpx.get(f"{BASE}/getBids/{body.item_id}",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    except Exception as exc:
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                             http_status=200, error=f"verify:{type(exc).__name__}:{exc}")
        return {"status":"blocked","reason_code":"delivery_unknown",
                "message":"Avito принял изменение, но BORIS не смог подтвердить итоговую ставку; слепой повтор запрещён",
                "request_key":_request_key,"changed_avito":False}
    if _vr.status_code == 429:
        _retry_after = 30
        try:
            _raw_ra = str(_vr.headers.get("Retry-After") or "").strip()
            if _raw_ra:
                try:
                    _retry_after = max(0, int(float(_raw_ra)))
                except Exception:
                    from email.utils import parsedate_to_datetime as _parse_verify_ra
                    from datetime import datetime as _verify_dt, timezone as _verify_tz
                    _when = _parse_verify_ra(_raw_ra)
                    if _when.tzinfo is None:
                        _when = _when.replace(tzinfo=_verify_tz.utc)
                    _retry_after = max(0, int((_when.astimezone(_verify_tz.utc)-_verify_dt.now(_verify_tz.utc)).total_seconds()))
        except Exception:
            _retry_after = 30
        _retry_after = record_account_throttle(body.account_id, _retry_after or 30, source="cpxpromo_postwrite_verify_429")
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                             http_status=200, result={"reason":"postwrite_verify_429"})
        return {"status":"blocked","reason_code":"delivery_unknown",
                "retry_after_seconds":int(_retry_after),
                "message":"Avito принял изменение, но проверка ставки отложена по Retry-After; слепой повтор запрещён",
                "request_key":_request_key,"changed_avito":False}
    if _vr.status_code != 200:
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                             http_status=200, result={"reason":"postwrite_verify_http","verify_http_status":int(_vr.status_code)})
        return {"status":"blocked","reason_code":"delivery_unknown",
                "message":"Avito принял изменение, но точная ставка не подтверждена; слепой повтор запрещён",
                "request_key":_request_key,"changed_avito":False}
    try:
        _vdata = _vr.json() or {}
    except Exception:
        _vdata = {}
    _vmanual = _vdata.get("manual") if isinstance(_vdata.get("manual"), dict) else {}
    try:
        _observed_penny = int(_vmanual.get("bidPenny"))
    except Exception:
        _observed_penny = -1
    if _observed_penny != int(body.bid_penny):
        _direct_receipt_mark(body.account_id, _request_key, "delivery_unknown",
                             http_status=200, observed_bid_penny=(_observed_penny if _observed_penny >= 0 else None),
                             result={"reason":"postwrite_bid_mismatch","intended_bid_penny":int(body.bid_penny),
                                     "observed_bid_penny":_observed_penny})
        return {"status":"blocked","reason_code":"delivery_unknown",
                "message":"Avito не подтвердил запрошенную ставку; слепой повтор запрещён до сверки",
                "request_key":_request_key,"changed_avito":False}
    _direct_receipt_mark(body.account_id, _request_key, "succeeded",
                         http_status=200, observed_bid_penny=_observed_penny,
                         result={"source":"cpxpromo_direct","provider_status":"exact_readback"})
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200] or "ok (пустой ответ)"}
    lim = f", лимит/день {body.limit_penny/100:.0f}р" if body.limit_penny else ""
    _audit_log(body.account_id, "cpxpromo_set_manual",
               f"объявление {body.item_id}: ставка {body.bid_penny/100:.0f}р{lim}; подтверждено Avito", "user")
    return {"status": "ok", "data": data, "request_key":_request_key,
            "observed_bid_penny":_observed_penny,"changed_avito":True}


@router.post("/set_auto")
def set_auto(body: SetAutoBody):
    """Авто-настройка (Avito сам крутит ставку под бюджет). POST /cpxpromo/1/setAuto.
    ВНИМАНИЕ: недоступно в категории «Транспорт». МЕНЯЕТ расходы — аудит."""
    from app.api.avito import _audit_log
    blocked, reason = _write_blocked(body.account_id)
    if blocked:
        return {"status":"blocked","reason_code":"account_disabled","message":reason or "Действия BORIS для аккаунта отключены","changed_avito":False}
    cap = _hard_cap(body.account_id)
    # CPXPROMO_SETAUTO_HARD_CAP_FAIL_CLOSED_V1: Avito setAuto chooses bids on
    # the provider side, so BORIS cannot prove/enforce the authoritative per-bid
    # hard cap (global/account <=150 RUB). Missing account cap is not permission
    # to bypass that policy; with a cap configured setAuto is still incompatible.
    if cap <= 0:
        return {"status":"blocked","reason_code":"hard_bid_cap_missing",
                "message":"Авто-продвижение Avito запрещено без явного лимита ставки; BORIS управляет денежными действиями только через контролируемый ручной контур.",
                "changed_avito":False}
    return {"status":"blocked","reason_code":"auto_promotion_incompatible_with_hard_bid_cap",
            "message":f"Авто-продвижение Avito отключено: BORIS контролирует красную цену {cap:.0f} ₽ вручную",
            "hard_max_bid_rub":cap,"changed_avito":False}
