# -*- coding: utf-8 -*-
"""Журнал действий и откат.

Правило 0 — клиент видит, что система делала от его имени.
Правило 5 — изменение можно вернуть. Откат работает там, где он технически
возможен: ставка возвращается на прежнее значение из записи журнала.
Публикацию объявлений и отправленные сообщения вернуть нельзя — журнал в этом
случае отдаёт список того, что снимать вручную.
"""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.auth import get_current_user
from app.services.action_log import recent, log_action, ACTOR_USER

router = APIRouter(prefix="/api/action_log", tags=["action_log"])

# Какие действия умеем возвращать
ROLLBACKABLE = ("Снизил ставку", "Поднял ставку")


def _allowed(db, user, account_id: str) -> bool:
    """Свой аккаунт по владению или по выданному доступу."""
    if getattr(user, "role", "") == "owner":
        return True
    uid = getattr(user, "id", 0)
    if db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"),
                  {"a": account_id, "u": uid}).fetchone():
        return True
    return bool(db.execute(text(
        "SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a"
        " AND can_view = TRUE"), {"u": uid, "a": account_id}).fetchone())


@router.get("/recent")
def list_recent(account_id: str, limit: int = 50, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        if not _allowed(db, user, account_id):
            return {"status": "error", "message": "Нет доступа к этому аккаунту"}
    finally:
        db.close()
    items = recent(account_id, limit)
    # Помечаем, что можно вернуть — фронт покажет кнопку только у этих
    for it in items:
        it["можно_вернуть"] = it.get("действие") in ROLLBACKABLE
    return {"status": "ok", "записи": items}


class RollbackBody(BaseModel):
    action_id: int


def _rollback_unknown_readonly_reconcile(receipt_data: dict, current_bid_penny, *, now=None, grace_seconds: int = 300) -> dict:
    """Classify an ambiguous prior rollback from authoritative current bid only.

    Pure/DB-free helper so the exactly-once recovery contract has deterministic
    regression coverage without touching Avito or client data.  A fresh retry is
    allowed only when the provider is still at the exact pre-write bid after the
    eventual-consistency grace.  Any third value, missing timestamp, or young
    ambiguity stays fail-closed.
    """
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    old_penny = receipt_data.get("old_bid_penny") if isinstance(receipt_data, dict) else None
    ambiguous_at = (receipt_data.get("updated_at") or receipt_data.get("started_at")) if isinstance(receipt_data, dict) else None
    age_seconds = None
    try:
        when = datetime.fromisoformat(str(ambiguous_at).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (now.astimezone(timezone.utc) - when.astimezone(timezone.utc)).total_seconds())
    except Exception:
        age_seconds = None
    if (old_penny is not None and current_bid_penny is not None
            and int(current_bid_penny) == int(old_penny)
            and age_seconds is not None and age_seconds >= max(0, int(grace_seconds))):
        return {"decision": "reconciled_not_applied", "age_seconds": age_seconds}
    return {"decision": "blocked", "age_seconds": age_seconds}


@router.post("/rollback")
def rollback(body: RollbackBody, user=Depends(get_current_user)):
    import httpx
    from app.api.avito import get_avito_token
    db = SessionLocal()
    try:
        r = db.execute(text(
            "SELECT account_id, action, object_name, before_val, after_val"
            " FROM action_log WHERE id = :i"), {"i": body.action_id}).fetchone()
        if not r:
            return {"status": "error", "message": "Запись не найдена"}
        acc, action, obj, before, after = r[0], r[1], r[2], r[3], r[4]
        if not _allowed(db, user, acc):
            return {"status": "error", "message": "Нет доступа к этому аккаунту"}
        if action not in ROLLBACKABLE:
            return {"status": "error",
                    "message": "Это действие вернуть нельзя: %s" % action}
        m = re.search(r"(\d+)", before or "")
        if not m:
            return {"status": "error", "message": "В записи нет прежней ставки"}
        old_rub = int(m.group(1))
        # Current money-safety policy always wins over historical rollback data.
        # A rollback must never resurrect an old 100–500 ₽ bid after the owner
        # has a stricter red-line ceiling in force.
        from app.services.marketing_money_policy import effective_hard_bid_cap
        hard_cap = float(effective_hard_bid_cap(db, acc))
        if hard_cap > 0 and old_rub > hard_cap + 1e-9:
            return {"status": "blocked", "reason_code": "hard_bid_cap",
                    "message": "Прежняя ставка %s ₽ выше текущей красной цены %s ₽" % (old_rub, int(hard_cap)),
                    "hard_max_bid_rub": hard_cap, "changed_avito": False}

        tk = get_avito_token(acc)
        tok = tk.get("access_token") if isinstance(tk, dict) else tk
        if not tok:
            return {"status": "error", "message": "Не удалось получить токен Avito"}

        # ACTION_LOG_ROLLBACK_EXACTLY_ONCE_V1: rollback is a real money mutation.
        # Never hardcode actionTypeID and never blindly replay an ambiguous POST.
        # First read the current provider state, preserve Avito's exact action type,
        # and reconcile a previous unknown delivery before considering another write.
        from app.models.storage import Storage as _St
        import json as _json_rb
        from datetime import datetime as _dt_rb, timezone as _tz_rb
        receipt_key = f"action_log_rollback:{int(body.action_id)}"
        receipt = db.query(_St).filter(_St.account_id == acc, _St.key == receipt_key).order_by(_St.id.desc()).first()
        try:
            receipt_data = _json_rb.loads(receipt.value or "{}") if receipt else {}
        except Exception:
            receipt_data = {}

        headers = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
        # ACTION_LOG_ROLLBACK_SHARED_THROTTLE_V1: rollback is provider I/O too.
        # A Retry-After observed by any trusted BORIS worker is binding for this tenant.
        from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
        _shared_retry = account_throttle_remaining(acc)
        if _shared_retry > 0:
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_shared_retry),"changed_avito":False}

        def _record_rb_429(response, source: str) -> int:
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
                        retry = max(0, int((when.astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                retry = 30
            return record_account_throttle(acc, retry or 30, source=source)

        try:
            pre = httpx.get(f"https://api.avito.ru/cpxpromo/1/getBids/{int(obj)}", headers={"Authorization": "Bearer " + tok}, timeout=20)
        except Exception as exc:
            return {"status": "blocked", "reason_code": "rollback_provider_state_unknown",
                    "message": "Avito не подтвердил текущую ставку; повтор денежного действия запрещён.",
                    "changed_avito": False, "detail": type(exc).__name__}
        if pre.status_code == 429:
            _retry = _record_rb_429(pre, "action_log_rollback_getbids_429")
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_retry),"changed_avito":False}
        if pre.status_code != 200:
            return {"status": "blocked", "reason_code": "rollback_provider_state_unknown",
                    "code": pre.status_code, "message": pre.text[:200], "changed_avito": False}
        try:
            pre_data = pre.json() or {}
        except Exception:
            pre_data = {}
        current_manual = pre_data.get("manual") or {}
        current_bid_penny = current_manual.get("bidPenny")
        action_type_id = pre_data.get("actionTypeID") or current_manual.get("actionTypeID")
        if action_type_id is None:
            return {"status": "blocked", "reason_code": "rollback_action_type_unknown",
                    "message": "Avito не вернул тип целевого действия; BORIS не будет угадывать его.",
                    "changed_avito": False}
        target_penny = int(old_rub) * 100
        if current_bid_penny is not None and int(current_bid_penny) == target_penny:
            if receipt:
                receipt_data.update({"status":"reconciled","observed_bid_penny":target_penny,"reconciled_at":_dt_rb.now(_tz_rb.utc).isoformat()})
                receipt.value=_json_rb.dumps(receipt_data,ensure_ascii=False); db.commit()
            return {"status":"ok","item_id":obj,"bid_rub":old_rub,"idempotent":True,"changed_avito":False}

        # ROLLBACK_RAISE_MONEY_GUARD_V1: rollback is not a policy bypass. If the
        # historical target is above the current live bid, re-validate every
        # current money red line before the provider mutation. Lowering remains
        # available without requiring growth authorization.
        if current_bid_penny is None:
            return {"status":"blocked","reason_code":"rollback_provider_state_unknown",
                    "message":"Avito не подтвердил текущую ставку; направление отката неизвестно.",
                    "changed_avito":False}
        if target_penny > int(current_bid_penny):
            from app.services.marketing_money_policy import effective_daily_budget_limit, effective_hard_bid_cap, latest_confirmed_spend
            from app.services.marketing_signal_guard import money_spend_signal_eligible, money_stats_snapshot_eligible, money_raise_signals_eligible
            limit = effective_daily_budget_limit(db, acc)
            cap = float(effective_hard_bid_cap(db, acc) or 0)
            signal = latest_confirmed_spend(db, acc, max_age_seconds=900) or {}
            if not limit or float(limit) <= 0:
                return {"status":"blocked","reason_code":"no_explicit_daily_budget","changed_avito":False}
            if not money_spend_signal_eligible(db, acc, signal):
                return {"status":"blocked","reason_code":"blocked_spendings_unavailable","changed_avito":False}
            # ROLLBACK_RAISE_STATS_FRESH_15M_V1: a rollback that raises the live
            # bid is a money-increasing action, so historical intent cannot bypass
            # the same complete <=15m stats requirement used by the marketer.
            _stats_ok, _stats_evidence = money_stats_snapshot_eligible(db, acc, max_age_seconds=900)
            if not _stats_ok:
                return {"status":"blocked","reason_code":"blocked_stats_unavailable",
                        "stats_age_seconds":_stats_evidence.get("age_seconds"),"changed_avito":False}
            # ROLLBACK_RAISE_COHERENT_SIGNAL_V1: individual freshness is not enough;
            # rollback-raises reuse the canonical final spend/stats <=5m coherence
            # proof before creating a provider mutation receipt.
            _signals_ok, _signals_evidence = money_raise_signals_eligible(
                db, acc, max_age_seconds=900, max_snapshot_skew_seconds=300
            )
            if not _signals_ok:
                return {"status":"blocked","reason_code":"money_signal_stale_or_degraded",
                        "money_signal":_signals_evidence,"changed_avito":False}
            spent = float(signal.get("spent_today_rub"))
            if spent >= float(limit) * 0.90 - 1e-9:
                return {"status":"blocked","reason_code":"blocked_daily_budget_near_limit",
                        "daily_budget_limit_rub":float(limit),"spent_today_rub":spent,"changed_avito":False}
            target_rub = float(target_penny) / 100.0
            current_rub = float(current_bid_penny) / 100.0
            if cap > 0 and target_rub > cap + 1e-9:
                return {"status":"blocked","reason_code":"hard_bid_cap","hard_max_bid_rub":cap,"changed_avito":False}
            if current_rub <= 0:
                return {"status":"blocked","reason_code":"blocked_bid_delta_unprovable","changed_avito":False}
            delta_pct = (target_rub-current_rub)*100.0/current_rub
            if delta_pct > 10.0 + 1e-9:
                return {"status":"blocked","reason_code":"blocked_bid_delta_exceeded",
                        "delta_pct":round(delta_pct,2),"changed_avito":False}

        if str(receipt_data.get("status") or "") in {"attempting", "delivery_unknown"}:
            # ACTION_LOG_ROLLBACK_UNKNOWN_READONLY_RECONCILE_V2: if a previous
            # rollback outcome was ambiguous, classify only the already-fetched
            # authoritative current bid. The pure helper is regression-tested and
            # cannot perform provider I/O or mutate client state by itself.
            _reconcile = _rollback_unknown_readonly_reconcile(
                receipt_data, current_bid_penny, now=_dt_rb.now(_tz_rb.utc), grace_seconds=300
            )
            _ambiguous_age = _reconcile.get("age_seconds")
            if _reconcile.get("decision") == "reconciled_not_applied":
                receipt_data.update({"status":"reconciled_not_applied",
                                     "observed_bid_penny":int(current_bid_penny),
                                     "reconciled_at":_dt_rb.now(_tz_rb.utc).isoformat(),
                                     "reconcile_reason":"provider_still_at_prewrite_bid_after_grace"})
                receipt.value=_json_rb.dumps(receipt_data,ensure_ascii=False); db.commit()
            else:
                return {"status": "blocked", "reason_code": "rollback_delivery_unknown",
                        "message": "Предыдущий откат не подтверждён Avito; слепой повтор запрещён.",
                        "changed_avito": False,
                        "reconcile_age_seconds": int(_ambiguous_age) if _ambiguous_age is not None else None}

        payload_receipt={"status":"attempting","action_id":int(body.action_id),"item_id":int(obj),
                         "target_bid_penny":target_penny,"old_bid_penny":int(current_bid_penny),
                         "action_type_id":int(action_type_id),
                         "started_at":_dt_rb.now(_tz_rb.utc).isoformat()}
        if receipt:
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False)
        else:
            receipt=_St(account_id=acc,key=receipt_key,value=_json_rb.dumps(payload_receipt,ensure_ascii=False)); db.add(receipt)
        db.commit()
        # ROLLBACK_RAISE_FINAL_COHERENT_SIGNAL_RECHECK_V1: receipt persistence and
        # local guard work can cross the freshness/coherence boundary. Re-prove the
        # current DB-only money signals immediately before any rollback that raises.
        if target_penny > int(current_bid_penny):
            from app.services.marketing_signal_guard import money_raise_signals_eligible as _rollback_final_signals
            _final_signals_ok, _final_signals_evidence = _rollback_final_signals(
                db, acc, max_age_seconds=900, max_snapshot_skew_seconds=300
            )
            if not _final_signals_ok:
                payload_receipt.update({"status":"failed","reason":"money_signal_stale_or_degraded",
                                        "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
                receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
                return {"status":"blocked","reason_code":"money_signal_stale_or_degraded",
                        "money_signal":_final_signals_evidence,"changed_avito":False}
        # ACTION_LOG_ROLLBACK_FINAL_SHARED_THROTTLE_RECHECK_V1: another worker may
        # receive 429 after our initial getBids. Recheck immediately before write.
        _shared_retry = account_throttle_remaining(acc)
        if _shared_retry > 0:
            payload_receipt.update({"status":"failed","reason":"avito_account_throttled",
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_shared_retry),"changed_avito":False}
        try:
            from app.api.cpx_advisor import _provider_set_manual_bid
            resp = _provider_set_manual_bid(
                account_id=acc,
                token=tok,
                item_id=int(obj),
                action_type_id=int(action_type_id),
                bid_penny=int(target_penny),
                timeout=30,
            )
        except Exception as exc:
            payload_receipt.update({"status":"delivery_unknown","error":type(exc).__name__,"updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown",
                    "message":"Результат запроса к Avito неизвестен; BORIS не повторяет денежное действие вслепую.",
                    "changed_avito":False}
        if resp.status_code == 429:
            _retry = _record_rb_429(resp, "action_log_rollback_setmanual_429")
            payload_receipt.update({"status":"failed","http_status":429,"reason":"avito_account_throttled",
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"avito_account_throttled",
                    "retry_after_seconds":int(_retry),"changed_avito":False}
        if resp.status_code != 200:
            ambiguous = int(resp.status_code) >= 500
            payload_receipt.update({"status":"delivery_unknown" if ambiguous else "failed",
                                    "http_status":int(resp.status_code),"body":resp.text[:200],
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status": "blocked" if ambiguous else "error", "code": resp.status_code,
                    "reason_code":"rollback_delivery_unknown" if ambiguous else "rollback_provider_rejected",
                    "message": resp.text[:200], "changed_avito":False}
        # ACTION_LOG_ROLLBACK_EXACT_POSTWRITE_VERIFY_V2: provider HTTP 200 is
        # acceptance, not proof that the exact rollback target is now live. Keep
        # the durable receipt attempting until getBids confirms target exactly.
        payload_receipt.update({"status":"attempting","http_status":200,
                                "provider_accepted":True,
                                "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
        receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
        _verify_retry = account_throttle_remaining(acc)
        if _verify_retry > 0:
            payload_receipt.update({"status":"delivery_unknown","reason":"postwrite_verify_throttled",
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown",
                    "retry_after_seconds":int(_verify_retry),"changed_avito":False}
        try:
            post = httpx.get(f"https://api.avito.ru/cpxpromo/1/getBids/{int(obj)}",
                             headers={"Authorization":"Bearer "+tok}, timeout=20)
        except Exception as exc:
            payload_receipt.update({"status":"delivery_unknown","reason":"postwrite_verify_transport",
                                    "verify_error":type(exc).__name__,
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown",
                    "message":"Avito принял откат, но BORIS не подтвердил итоговую ставку; слепой повтор запрещён",
                    "changed_avito":False}
        if post.status_code == 429:
            _retry = _record_rb_429(post, "action_log_rollback_postverify_429")
            payload_receipt.update({"status":"delivery_unknown","reason":"postwrite_verify_429",
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown",
                    "retry_after_seconds":int(_retry),"changed_avito":False}
        if post.status_code != 200:
            payload_receipt.update({"status":"delivery_unknown","reason":"postwrite_verify_http",
                                    "verify_http_status":int(post.status_code),
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown","changed_avito":False}
        try:
            post_data=post.json() or {}
        except Exception:
            post_data={}
        post_manual=post_data.get("manual") or {}
        try:
            observed=int(post_manual.get("bidPenny"))
        except Exception:
            observed=-1
        if observed != int(target_penny):
            payload_receipt.update({"status":"delivery_unknown","reason":"postwrite_bid_mismatch",
                                    "observed_bid_penny":observed if observed >= 0 else None,
                                    "updated_at":_dt_rb.now(_tz_rb.utc).isoformat()})
            receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
            return {"status":"blocked","reason_code":"rollback_delivery_unknown",
                    "observed_bid_penny":observed if observed >= 0 else None,"changed_avito":False}
        payload_receipt.update({"status":"succeeded","http_status":200,"observed_bid_penny":observed,
                                "finished_at":_dt_rb.now(_tz_rb.utc).isoformat()})
        receipt.value=_json_rb.dumps(payload_receipt,ensure_ascii=False); db.commit()
        log_action(account_id=acc, action="Вернул прежнюю ставку",
                   object_kind="объявление", object_name=str(obj),
                   before_val=after or "", after_val="%s руб" % old_rub,
                   reason="откат действия #%s по решению человека; ставка подтверждена Avito" % body.action_id,
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="action_log.rollback")
        return {"status": "ok", "item_id": obj, "bid_rub": old_rub,
                "observed_bid_penny":observed}
    finally:
        db.close()
