#!/usr/bin/env python3
"""Daily-budget growth guard for Avito CPX promotion.

Purpose: protect the owner's daily budget without destroying the traffic that is
already working toward KPI. This stage is not a second marketing engine: it never
raises bids, creates promotion, changes content or chooses growth hypotheses.

Policy:
- below the adaptive reserve line: observe only;
- at/above the reserve line: freeze NEW money growth, preserve every existing
  paid promotion and let KPI/CPL logic decide later bounded changes;
- a zero budget also freezes growth only and never means "remove everything".

Historical destructive brake helpers remain only so cpx_budget_resume can repair
old incidents. The active brake_account path is non-destructive.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import SessionLocal
from app.api.avito import get_avito_token, _audit_log
from app.models.storage import Storage
from app.services.marketing_money_policy import (
    effective_daily_budget_limit,
    latest_confirmed_spend,
    adaptive_budget_brake_policy,
    reconcile_presence_budget_growth_policy,
)
from app.services.reliability import ProviderDeferred
from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
from cpx_cap_reconciler import (
    _active_item_ids,
    _stored_inventory_ids,
    _bulk_promotions,
    _remove,
)

# CPX_BUDGET_BRAKE_SAFETY_LOWER_LANE_V1: legacy contract marker retained.
# KPI_CONTINUITY_BUDGET_GUARD_V1 is the active policy: crossing the reserve
# line blocks NEW autonomous growth but does not remove already-active CPX.
# This prevents the guard itself from zeroing traffic before the KPI target is met.
#
# CPX_BUDGET_BRAKE_SPEND_FRESH_15M_V1: stale/unknown spend is enough to block
# growth but never authorizes a provider mutation.
FRESH_SECONDS = 900
# CPX_BUDGET_BRAKE_EARLY_CUTOFF_V1: the adaptive line is now a GROWTH FREEZE,
# not a paid-promotion kill switch.
BRAKE_START_RATIO = 0.90  # fallback growth-freeze threshold
MOSCOW = ZoneInfo("Europe/Moscow")
RESUME_STATE_KEY = "cpx_budget_resume_state"


def _shared_throttle_guard(account_id: str) -> None:
    # CPX_BUDGET_BRAKE_SHARED_THROTTLE_V1
    remaining = account_throttle_remaining(account_id)
    if remaining > 0:
        raise ProviderDeferred("avito_account_throttled", remaining)


def _token(account_id: str) -> str:
    raw = get_avito_token(account_id)
    if isinstance(raw, dict):
        return str(raw.get("access_token") or raw.get("token") or "")
    return str(raw or "")


def _latest_spending(db, account_id: str) -> dict:
    """Thin compatibility wrapper around the authoritative money policy."""
    return latest_confirmed_spend(db, account_id, max_age_seconds=FRESH_SECONDS)


def _save_runtime(db, account_id: str, payload: dict) -> None:
    value = json.dumps(payload, ensure_ascii=False)

    def _write(target_db):
        row = target_db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "cpx_budget_brake_runtime",
        ).order_by(Storage.id.desc()).first()
        if row:
            row.value = value
        else:
            target_db.add(Storage(account_id=account_id, key="cpx_budget_brake_runtime", value=value))
        target_db.commit()

    try:
        _write(db)
    except DBAPIError:
        # Persisting the safety snapshot is not a money action. Any DBAPI
        # failure may leave the account-scoped session unusable even when the
        # driver does not mark connection_invalidated. Retry ONLY this runtime
        # persistence on a fresh session; never rerun Avito removal calls.
        try:
            db.rollback()
        except Exception:
            pass
        fresh = SessionLocal()
        try:
            _write(fresh)
        finally:
            fresh.close()


def _load_resume_state(account_id: str) -> dict:
    fresh = SessionLocal()
    try:
        row = fresh.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == RESUME_STATE_KEY,
        ).order_by(Storage.id.desc()).first()
        if not row or not row.value:
            return {}
        try:
            data = json.loads(row.value) or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    finally:
        fresh.close()


def _save_resume_state(account_id: str, payload: dict) -> None:
    value = json.dumps(payload, ensure_ascii=False)
    fresh = SessionLocal()
    try:
        row = fresh.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == RESUME_STATE_KEY,
        ).order_by(Storage.id.desc()).first()
        if row:
            row.value = value
        else:
            fresh.add(Storage(account_id=account_id, key=RESUME_STATE_KEY, value=value))
        fresh.commit()
    finally:
        fresh.close()


def _record_resume_snapshot(account_id: str, paid_entries: list[dict],
                            spent_today_rub: float, daily_budget_limit_rub: float,
                            brake_reason: str = "daily_budget_cutoff") -> None:
    """Persist exact pre-brake promotion evidence before the first remove call."""
    if not paid_entries:
        return
    day_msk = datetime.now(MOSCOW).date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    state = _load_resume_state(account_id)
    if str(state.get("brake_day_msk") or "") != day_msk:
        state = {
            "version": 1,
            "account_id": account_id,
            "brake_day_msk": day_msk,
            "status": "braking",
            "created_at": now_iso,
            "items": {},
        }
    items = state.get("items")
    if not isinstance(items, dict):
        items = {}
        state["items"] = items
    for entry in paid_entries:
        try:
            iid = int(entry.get("item_id"))
        except Exception:
            continue
        manual = entry.get("manual") if isinstance(entry.get("manual"), dict) else {}
        auto = entry.get("auto") if isinstance(entry.get("auto"), dict) else {}
        try:
            bid_penny = int(manual.get("bidPenny") or 0) or None
        except Exception:
            bid_penny = None
        try:
            action_type_id = int(entry.get("action_type_id") or 5)
        except Exception:
            action_type_id = 5
        current = items.get(str(iid)) if isinstance(items.get(str(iid)), dict) else {}
        current.update({
            "item_id": iid,
            "status": "brake_pending",
            "original_mode": ("manual" if manual else "auto" if auto else "unknown"),
            "bid_penny": bid_penny,
            "action_type_id": action_type_id,
            "snapshot_at": now_iso,
            "restore_target_source": ("exact_pre_brake_bid" if bid_penny else "avito_live_rec_bid"),
        })
        items[str(iid)] = current
    state.update({
        "status": "braking",
        "spent_today_rub": float(spent_today_rub),
        "daily_budget_limit_rub": float(daily_budget_limit_rub),
        "brake_reason": str(brake_reason or "daily_budget_cutoff"),
        "updated_at": now_iso,
    })
    _save_resume_state(account_id, state)


def _mark_resume_item(account_id: str, item_id: int, status: str, **extra) -> None:
    """Durably bind next-day restoration only to provider-confirmed removals."""
    state = _load_resume_state(account_id)
    items = state.get("items")
    if not isinstance(items, dict):
        return
    item = items.get(str(int(item_id)))
    if not isinstance(item, dict):
        return
    item["status"] = str(status)
    item["updated_at"] = datetime.now(timezone.utc).isoformat()
    for key, value in extra.items():
        item[str(key)] = value
    items[str(int(item_id))] = item
    state["items"] = items
    braked = sum(1 for x in items.values() if isinstance(x, dict) and x.get("status") == "braked")
    pending = sum(1 for x in items.values() if isinstance(x, dict) and x.get("status") == "brake_pending")
    state["braked_items"] = braked
    state["brake_pending_items"] = pending
    if braked > 0 and pending == 0:
        state["status"] = (
            "pending_authorized_budget"
            if str(state.get("brake_reason") or "") == "explicit_zero_budget"
            else "pending_next_day"
        )
        state["braked_at"] = datetime.now(timezone.utc).isoformat()
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_resume_state(account_id, state)


def _accounts(db, account_filter: str | None) -> list[str]:
    rows = db.execute(text("""
        SELECT account_id
          FROM accounts
         WHERE avito_client_id IS NOT NULL AND avito_client_secret IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM reliability_kill_switches r
                WHERE r.account_id=accounts.account_id AND r.blocked=true
                  AND r.module IN ('actions','marketing','background')
           )
         ORDER BY account_id
    """)).fetchall()
    out=[]
    for (account_id,) in rows:
        account_id=str(account_id)
        if account_filter and account_id != account_filter:
            continue
        _effective = effective_daily_budget_limit(db, account_id)
        _configured = _configured_daily_budget_state(db, account_id)
        if (
            _effective is not None
            or (
                bool(_configured.get("present"))
                and float(_configured.get("value") or 0) <= 0
            )
        ):
            out.append(account_id)
    return out


def _supersede_measurements_after_brake(account_id: str, item_ids: list[int]) -> int:
    """DB-only causal cleanup after paid promotion is authoritatively off."""
    # CPX_BUDGET_BRAKE_MEASURE_SUPERSEDE_V1: a raise/lower experiment cannot
    # remain active after the safety brake removed its paid promotion. Never
    # classify this window as winner/loser; simply free the measurement lock.
    wanted={int(x) for x in (item_ids or []) if str(x).isdigit()}
    if not wanted:
        return 0
    fresh=SessionLocal(); changed=0
    try:
        rows=fresh.query(Storage).filter(
            Storage.account_id==account_id,
            Storage.key.like("cpx_measure:%"),
        ).all()
        now=datetime.now(timezone.utc).isoformat()
        for row in rows:
            try: state=json.loads(row.value or "{}")
            except Exception: continue
            if state.get("status")!="waiting_measurement": continue
            try: iid=int(state.get("item_id") or 0)
            except Exception: iid=0
            if iid not in wanted or str(state.get("action") or "") not in {"raise","lower"}:
                continue
            state["status"]="superseded"
            state["finished_at"]=now
            state["effect"]="budget_brake_removed_promotion"
            state["decision"]="do_not_learn_from_braked_window"
            state["reason_code"]="budget_brake_removed_promotion"
            state["reason"]="Budget brake снял платное продвижение; старое денежное измерение закрыто без winner/loser verdict."
            state["scale_eligible"]=False
            row.value=json.dumps(state,ensure_ascii=False)
            changed+=1
        if changed: fresh.commit()
        return changed
    finally:
        fresh.close()


def _inventory(db, client: httpx.Client, headers: dict, account_id: str) -> tuple[list[int],dict]:
    try:
        ids = _active_item_ids(client, headers)
        return ids, {"source":"live_core","complete":True,"expected":len(ids),"ids":len(ids)}
    except Exception as exc:
        if "429" not in str(exc):
            raise
        ids, expected = _stored_inventory_ids(db, account_id)
        if not ids:
            raise
        complete = expected <= 0 or len(ids) >= max(1, int(expected * 0.95))
        return ids, {"source":"stored_snapshot_fallback","complete":bool(complete),
                     "expected":int(expected or len(ids)),"ids":len(ids),"live_error":"HTTP 429"}


def _configured_daily_budget_state(db, account_id: str) -> dict:
    """Distinguish explicit owner zero from genuinely missing budget config."""
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == "kpi_settings",
    ).order_by(Storage.id.desc()).first()
    if not row or not row.value:
        return {"present": False, "value": None}
    try:
        data = json.loads(row.value) or {}
    except Exception:
        return {"present": False, "value": None}
    if not isinstance(data, dict) or "daily_budget_limit_rub" not in data:
        return {"present": False, "value": None}
    try:
        value = float(data.get("daily_budget_limit_rub") or 0)
    except Exception:
        return {"present": True, "value": 0.0, "invalid": True}
    return {"present": True, "value": max(0.0, value)}


def _brake_explicit_zero_budget(db, account_id: str, out: dict, apply: bool) -> dict:
    """ZERO_BUDGET_PRESERVE_EXISTING_CPX_V2.

    A zero / not-authorized daily budget blocks *growth* only. It is not
    permission to remove an already-active promotion. Existing live CPX is
    preserved unless the owner explicitly requests a stop or another safety
    rule proves a real budget/economic breach.
    """
    out["policy_version"] = "ZERO_BUDGET_PRESERVE_EXISTING_CPX_V2"
    out["zero_budget_authority"] = "growth_block_only"
    out["daily_budget_limit_rub"] = 0.0
    out["can_increase_spend"] = False
    out["existing_promotions_preserved"] = True
    out["removed"] = 0
    out["status"] = "zero_budget_growth_blocked_existing_preserved"

    spend = _latest_spending(db, account_id) or {}
    if spend.get("spent_today_rub") is not None:
        try:
            out["spent_today_rub"] = float(spend.get("spent_today_rub"))
        except Exception:
            pass
    if spend.get("age_seconds") is not None:
        try:
            out["spend_age_sec"] = round(float(spend.get("age_seconds")), 1)
        except Exception:
            pass
    out["spending_date"] = spend.get("spending_date")

    # Read-only observability only. A provider/read failure must never be
    # converted into a destructive fallback.
    try:
        _shared_throttle_guard(account_id)
        tok = _token(account_id)
        if tok:
            headers = {"Authorization": f"Bearer {tok}"}
            with httpx.Client() as client:
                client._boris_account_id = account_id
                ids, inv = _inventory(db, client, headers, account_id)
                out["inventory"] = inv
                promos = _bulk_promotions(client, headers, ids) if ids else []
                current_paid = sum(
                    1 for p in promos
                    if p.get("manualPromotion") or p.get("autoPromotion")
                )
                out["paid_promotions_before"] = current_paid
                out["paid_promotions_after"] = current_paid
    except Exception as exc:
        out["observation_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"

    _save_runtime(db, account_id, out)
    return out


def brake_account(db, account_id: str, apply: bool) -> dict:
    now = datetime.now(timezone.utc)
    limit = effective_daily_budget_limit(db, account_id)
    spend = _latest_spending(db, account_id)
    out = {"account_id":account_id,"apply":bool(apply),"daily_budget_limit_rub":limit,
           "status":"running","spent_today_rub":None,"spend_age_sec":None,
           "paid_promotions_before":None,"removed":0,"failed":0,
           "paid_promotions_after":None,"errors":[],"checked_at":now.isoformat()}
    if not limit or limit <= 0:
        _cfg = _configured_daily_budget_state(db, account_id)
        if bool(_cfg.get("present")) and float(_cfg.get("value") or 0) <= 0:
            return _brake_explicit_zero_budget(db, account_id, out, bool(apply))
        out["status"]="no_limit"; _save_runtime(db,account_id,out); return out
    # PRESENCE_BUDGET_GROWTH_SELF_HEAL_WIRE_V1: before KPI/additive growth runs,
    # reconcile the non-CPX presence-spend conflict. This never removes listings;
    # it only freezes additional inventory growth and records durable evidence.
    try:
        out["presence_budget_pressure"] = reconcile_presence_budget_growth_policy(
            db, account_id, float(limit), mutate=bool(apply)
        )
    except Exception as _presence_exc:
        out["presence_budget_pressure"] = {
            "policy_version":"PRESENCE_BUDGET_GROWTH_SELF_HEAL_V1",
            "action":"reconcile_error",
            "changed_policy":False,
            "error":f"{type(_presence_exc).__name__}:{str(_presence_exc)[:160]}",
        }
    spend_status=str((spend or {}).get("status") or "unknown")
    if spend_status == "unknown":
        out["status"]="spend_unknown"; _save_runtime(db,account_id,out); return out
    if spend.get("spent_today_rub") is not None:
        out["spent_today_rub"]=float(spend["spent_today_rub"])
    out["spend_age_sec"]=round(float(spend.get("age_seconds") or 0),1) if spend.get("age_seconds") is not None else None
    out["spending_date"]=spend.get("spending_date")
    # Compute the adaptive cutoff before stale handling. Same-day confirmed
    # spend is monotonic, so a stale sample that already crossed the adaptive
    # safety line is valid evidence for LOWER-ONLY braking, never for growth.
    _adaptive = adaptive_budget_brake_policy(db, account_id, float(limit), out["spent_today_rub"])
    _cutoff = float(_adaptive.get("cutoff_ratio") or BRAKE_START_RATIO)
    out["adaptive_budget_policy"] = _adaptive
    # CPX_BUDGET_BRAKE_CUTOFF_OBSERVABILITY_V1: expose the exact effective
    # money cutoff at top level so guardian/owner telemetry can verify the
    # policy without reverse-engineering a nested payload.
    out["adaptive_cutoff_pct"] = round(_cutoff * 100, 1)
    out["adaptive_reserve_rub"] = _adaptive.get("reserve_rub")
    out["adaptive_policy_reason"] = _adaptive.get("reason")
    out["brake_start_pct"] = out["adaptive_cutoff_pct"]
    if spend_status == "stale":
        # CPX_BUDGET_BRAKE_MONOTONIC_OVERLIMIT_V2
        _same_day = str(spend.get("spending_date") or "") == now.date().isoformat()
        _already_over = out["spent_today_rub"] is not None and float(out["spent_today_rub"]) >= float(limit) * _cutoff
        if not (_same_day and _already_over):
            out["status"]="spend_stale"; _save_runtime(db,account_id,out); return out
        out["stale_monotonic_overlimit_proof"] = True
    if spend_status not in {"ok","stale"} or out["spent_today_rub"] is None:
        out["status"]="spend_unknown"; _save_runtime(db,account_id,out); return out
    ratio=float(out["spent_today_rub"])/float(limit)
    out["budget_used_pct"]=round(ratio*100,1)
    # CPX_BUDGET_BRAKE_ADAPTIVE_RESERVE_V1: fixed 90% was too late for
    # high-velocity accounts. Protect four hours of evidenced burn so provider
    # reporting lag / one delayed hourly cycle cannot routinely cross the red line.
    if ratio < _cutoff:
        out["status"]="within_budget"; _save_runtime(db,account_id,out); return out
    out["early_cutoff"] = bool(ratio < 1.0)
    out["brake_start_pct"] = round(_cutoff * 100, 1)

    # KPI_CONTINUITY_BUDGET_GUARD_V1:
    # The old implementation removed every live promotion here. That destroyed
    # traffic while KPI was still behind (PBI incident 2026-09-10: 259/259 and
    # later 250/250 promotions removed around ~80% of the then configured budget).
    # Crossing the reserve line must therefore revoke only NEW growth authority.
    # Existing CPX stays exactly as the provider has it; ordinary KPI/CPL logic
    # may later make bounded, evidence-backed changes.
    out["policy_version"] = "KPI_CONTINUITY_BUDGET_GUARD_V1"
    out["budget_guard_mode"] = "freeze_growth_preserve_delivery"
    out["can_increase_spend"] = False
    out["existing_promotions_preserved"] = True
    out["budget_exceeded"] = bool(ratio >= 1.0)
    out["removed"] = 0
    out["failed"] = 0
    out["status"] = "growth_frozen_existing_preserved"
    _save_runtime(db, account_id, out)
    return out

    # Legacy destructive recovery code below is intentionally unreachable from
    # brake_account. It is retained temporarily because cpx_budget_resume imports
    # the historical snapshot helpers to restore incidents created before V1.
    try:
        # Shared Retry-After must win before token/provider I/O.
        _shared_throttle_guard(account_id)
    except ProviderDeferred as exc:
        out["status"]="deferred"; out["retry_after_seconds"]=exc.retry_after_seconds
        out["errors"].append("provider_deferred:avito_account_throttled")
        _save_runtime(db,account_id,out); return out
    tok=_token(account_id)
    if not tok:
        out["status"]="blocked"; out["errors"].append("no_avito_token"); _save_runtime(db,account_id,out); return out
    headers={"Authorization":f"Bearer {tok}"}
    with httpx.Client() as client:
        try:
            _shared_throttle_guard(account_id)
            ids, inv = _inventory(db,client,headers,account_id)
            out["inventory"]=inv
            _shared_throttle_guard(account_id)
            promos=_bulk_promotions(client,headers,ids) if ids else []
            paid=[]
            paid_entries=[]
            for p in promos:
                manual=p.get("manualPromotion") or {}
                auto=p.get("autoPromotion") or {}
                if manual or auto:
                    try:
                        iid=int(p.get("itemID"))
                    except Exception:
                        continue
                    paid.append(iid)
                    paid_entries.append({
                        "item_id":iid,
                        "manual":manual if isinstance(manual,dict) else {},
                        "auto":auto if isinstance(auto,dict) else {},
                        "action_type_id":int(p.get("actionTypeID") or 5),
                    })
            paid=sorted(set(paid))
            out["paid_promotions_before"]=len(paid)
            if not apply:
                out["status"]="would_brake" if paid else "budget_exhausted_no_promo"
                _save_runtime(db,account_id,out); return out
            # BUDGET_BRAKE_DURABLE_RESUME_SNAPSHOT_V1:
            # Persist exact pre-remove promotion state BEFORE the first provider
            # mutation. A crash after remove must never leave tomorrow's worker
            # guessing which listing was intentionally stopped by the budget cap.
            if paid_entries:
                _record_resume_snapshot(
                    account_id, paid_entries,
                    float(out["spent_today_rub"] or 0),
                    float(limit),
                )
            for iid in paid:
                try:
                    _shared_throttle_guard(account_id)
                    rr=_remove(client,headers,iid)
                    if rr.status_code==200:
                        out["removed"]+=1
                        _mark_resume_item(account_id,iid,"braked",
                                          removed_at=datetime.now(timezone.utc).isoformat())
                        _audit_log(account_id,"cpx_budget_brake_remove",
                                   f"объявление {iid}: платное продвижение снято; расход {out['spent_today_rub']:.0f} ₽ достиг лимита {float(limit):.0f} ₽",
                                   "boris_budget_guard")
                    else:
                        out["failed"]+=1
                        _mark_resume_item(account_id,iid,"remove_failed",
                                          remove_http_status=int(rr.status_code))
                        out["errors"].append(f"remove:{iid}:http_{rr.status_code}")
                except ProviderDeferred:
                    # CPX_BUDGET_BRAKE_ACCOUNT_THROTTLE_STOP_V1: do not swallow
                    # confirmed 429 and continue removing/verifying this tenant.
                    raise
                except Exception as exc:
                    out["failed"]+=1
                    _mark_resume_item(account_id,iid,"remove_failed",
                                      remove_error=f"{type(exc).__name__}:{str(exc)[:100]}")
                    out["errors"].append(f"remove:{iid}:{type(exc).__name__}:{str(exc)[:100]}")
            _shared_throttle_guard(account_id)
            verify=_bulk_promotions(client,headers,ids) if ids else []
            remaining=0
            for p in verify:
                if p.get("manualPromotion") or p.get("autoPromotion"):
                    remaining+=1
            out["paid_promotions_after"]=remaining
            if not inv.get("complete",True):
                out["status"]="needs_retry"
            elif remaining==0 and out["failed"]==0:
                out["status"]="braked"
                out["measurements_superseded"]=_supersede_measurements_after_brake(account_id,ids)
            else:
                out["status"]="fail"
        except ProviderDeferred as exc:
            # Any confirmed CPX 429 from the imported reconciler helpers becomes
            # tenant-wide backpressure and terminates this account immediately.
            record_account_throttle(account_id, exc.retry_after_seconds, source="cpx_budget_brake_429")
            out["status"]="deferred"; out["retry_after_seconds"]=exc.retry_after_seconds
            out["errors"].append(f"provider_deferred:{exc.dependency}")
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            # CPX_BUDGET_BRAKE_TRANSPORT_DEFERRED_V1: provider transport loss is
            # not a policy failure. This lane can only LOWER spend, so keep the
            # account fail-closed, persist the unresolved brake obligation and
            # let Guardian / the next canonical timer cycle retry it. Never replay
            # an ambiguous remove call inside this exception path.
            out["status"]="deferred"
            out["transport_retry_required"]=True
            out["errors"].append(f"provider_deferred:{type(exc).__name__}:{str(exc)[:150]}")
        except Exception as exc:
            out["status"]="error"; out["failed"]+=1
            out["errors"].append(f"{type(exc).__name__}:{str(exc)[:180]}")
    _save_runtime(db,account_id,out)
    return out


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--apply",action="store_true"); ap.add_argument("--account",default="")
    args=ap.parse_args()
    lock=open("/root/BORIS/backend/run/cpx_budget_brake.lock","w")
    try:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print("CPX_BUDGET_BRAKE_SKIP overlap",flush=True); return 0
    db=SessionLocal(); failures=[]
    try:
        accounts=_accounts(db,args.account.strip() or None)
    finally:
        db.close()
    try:
        print(f"CPX_BUDGET_BRAKE accounts={len(accounts)} apply={args.apply}",flush=True)
        for account_id in accounts:
            account_db=SessionLocal()
            try:
                out=brake_account(account_db,account_id,args.apply)
            except Exception as exc:
                out={"account_id":account_id,"apply":bool(args.apply),"status":"error",
                     "errors":[f"{type(exc).__name__}:{str(exc)[:180]}"],
                     "checked_at":datetime.now(timezone.utc).isoformat()}
            finally:
                account_db.close()
            print(json.dumps(out,ensure_ascii=False),flush=True)
            if out.get("status") in {"fail","error","blocked","needs_retry"}:
                failures.append(account_id)
    finally:
        lock.close()
    print(f"CPX_BUDGET_BRAKE_DONE failures={failures}",flush=True)
    return 1 if failures else 0


if __name__=="__main__":
    raise SystemExit(main())
