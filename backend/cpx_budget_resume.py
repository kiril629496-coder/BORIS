#!/usr/bin/env python3
"""Next-Moscow-day restoration for CPX promotions stopped by cpx_budget_brake.

This is a bounded compensation lane, not a second growth engine:
- only items durably proven as removed by the prior-day budget brake are eligible;
- never resumes on the same Moscow day as the brake;
- requires fresh same-day Avito spend/stats and an active money mandate;
- pauses when KPI is met, CPL is above the owner's red line, or spend >=90% cap;
- restores at most five items per hourly run;
- exact pre-brake manual bid wins; legacy missing snapshots use live Avito recBid;
- hard bid cap, provider min/max, action caps, balance guard and receipt barrier all bind.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app.api.avito import get_avito_token, _audit_log
from app.api.cpx_advisor import (
    _cpx_receipt_get,
    _cpx_receipt_mark,
    _cpx_receipt_prepare,
    _owner_manual_bid_override,
    check_raise_allowed,
)
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.action_log import ACTOR_BORIS_AUTO, log_action
from app.services.autonomy import can_execute_live_action
from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
from app.services.marketing_money_policy import (
    effective_daily_budget_limit,
    effective_hard_bid_cap,
    latest_confirmed_spend,
    presence_budget_pressure,
)
from app.services.reliability import ProviderDeferred
from cpx_budget_brake import _load_resume_state, _save_resume_state
from cpx_cap_reconciler import _active_item_ids, _bulk_promotions, _get_bid_detail

MOSCOW = ZoneInfo("Europe/Moscow")
MAX_RESUME_PER_RUN = 5
FRESH_SECONDS = 900
SAFETY_RATIO = 0.90


def _token(account_id: str) -> str:
    raw = get_avito_token(account_id)
    if isinstance(raw, dict):
        return str(raw.get("access_token") or raw.get("token") or "")
    return str(raw or "")


def _stats(db, account_id: str, day_msk: str) -> dict:
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == f"daily_stats:{day_msk}",
    ).order_by(Storage.id.desc()).first()
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _kpi(db, account_id: str) -> dict:
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == "kpi_settings",
    ).order_by(Storage.id.desc()).first()
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _mark_state_item(account_id: str, item_id: int, status: str, **extra) -> None:
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
    pending_states = {"braked", "resume_retry", "provider_accepted", "verify_pending"}
    pending = sum(
        1 for x in items.values()
        if isinstance(x, dict) and str(x.get("status") or "") in pending_states
    )
    restored = sum(
        1 for x in items.values()
        if isinstance(x, dict) and str(x.get("status") or "") in {"restored", "already_promoted"}
    )
    state["pending_items"] = pending
    state["restored_items"] = restored
    if pending == 0:
        state["status"] = "complete"
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        state["status"] = "resuming"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_resume_state(account_id, state)


def _set_state_status(account_id: str, status: str, **extra) -> None:
    state = _load_resume_state(account_id)
    if not state:
        return
    state["status"] = str(status)
    for key, value in extra.items():
        state[str(key)] = value
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_resume_state(account_id, state)


def _fresh_business_gate(db, account_id: str, today_msk: str) -> tuple[bool, str, dict]:
    limit = effective_daily_budget_limit(db, account_id)
    if not limit or float(limit) <= 0:
        return False, "no_daily_budget", {"daily_budget_limit_rub": limit}
    # PRESENCE_BUDGET_RESUME_GUARD_V1: a new Moscow day must not automatically
    # restore CPX when completed provider evidence shows that placement/presence
    # alone already exceeds the owner's daily red line.
    presence_pressure = presence_budget_pressure(db, account_id, float(limit))
    if bool(presence_pressure.get("blocked")):
        return False, "presence_budget_pressure", {
            "daily_budget_limit_rub": float(limit),
            "presence_budget_pressure": presence_pressure,
        }
    spend = latest_confirmed_spend(db, account_id, max_age_seconds=FRESH_SECONDS) or {}
    if str(spend.get("status") or "") != "ok":
        return False, "spend_not_fresh", {"spend": spend, "daily_budget_limit_rub": float(limit)}
    if str(spend.get("spending_date") or "") != today_msk:
        return False, "spend_wrong_day", {"spend": spend, "daily_budget_limit_rub": float(limit)}
    try:
        spent = float(spend.get("spent_today_rub"))
    except Exception:
        return False, "spend_invalid", {"spend": spend, "daily_budget_limit_rub": float(limit)}
    if spent >= float(limit) * SAFETY_RATIO:
        return False, "budget_near_or_over_limit", {
            "spent_today_rub": spent, "daily_budget_limit_rub": float(limit)
        }

    stats = _stats(db, account_id, today_msk)
    complete = bool((stats.get("completeness") or {}).get("complete") is True)
    try:
        collected = datetime.fromisoformat(str(stats.get("collected_at") or "").replace("Z", "+00:00"))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        stats_age = (datetime.now(timezone.utc) - collected.astimezone(timezone.utc)).total_seconds()
    except Exception:
        stats_age = 10**9
    if not complete or stats_age < -60 or stats_age > FRESH_SECONDS:
        return False, "stats_not_fresh", {
            "stats_complete": complete, "stats_age_seconds": round(stats_age, 1)
        }
    # CPX_BUDGET_RESUME_SPEND_STATS_COHERENCE_V1: restoring a stopped bid is
    # a real money raise. Individually fresh spend and stats are insufficient if
    # they describe materially different provider moments. Reuse the canonical
    # <=15m freshness + <=5m snapshot-skew proof used by other paid lanes.
    from app.services.marketing_signal_guard import money_raise_signals_eligible
    _signals_ok, _signals = money_raise_signals_eligible(
        db, account_id, max_age_seconds=FRESH_SECONDS, max_snapshot_skew_seconds=300
    )
    if not _signals_ok:
        return False, "money_signal_stale_or_degraded", {
            "money_signal": _signals,
            "stats_age_seconds": round(stats_age, 1),
            "daily_budget_limit_rub": float(limit),
        }

    kpi = _kpi(db, account_id)
    items = stats.get("items") if isinstance(stats.get("items"), list) else []
    contacts = sum(int((x or {}).get("contacts") or 0) for x in items if isinstance(x, dict))
    try:
        target = float(kpi.get("target_leads_per_day") or 0)
    except Exception:
        target = 0.0
    try:
        max_cpl = float(kpi.get("max_cost_per_lead_rub") or 0)
    except Exception:
        max_cpl = 0.0
    cpl = (spent / contacts) if contacts > 0 else None
    if target > 0 and contacts >= target:
        return False, "daily_kpi_met", {
            "contacts_today": contacts, "target_leads_per_day": target,
            "spent_today_rub": spent,
        }
    if cpl is not None and max_cpl > 0 and cpl > max_cpl:
        return False, "daily_cpl_above_limit", {
            "contacts_today": contacts, "cpl_rub": round(cpl, 2),
            "max_cpl_rub": max_cpl, "spent_today_rub": spent,
        }
    if contacts <= 0 and max_cpl > 0 and spent >= 1.10 * max_cpl:
        return False, "zero_lead_economic_emergency", {
            "contacts_today": 0, "spent_today_rub": spent, "max_cpl_rub": max_cpl,
        }
    return True, "ok", {
        "contacts_today": contacts,
        "target_leads_per_day": target,
        "max_cpl_rub": max_cpl,
        "cpl_rub": round(cpl, 2) if cpl is not None else None,
        "spent_today_rub": spent,
        "daily_budget_limit_rub": float(limit),
        "stats_age_seconds": round(stats_age, 1),
    }


def _target_from_detail(db, account_id: str, entry: dict, detail: dict) -> tuple[int | None, str, dict]:
    manual = detail.get("manual") if isinstance(detail.get("manual"), dict) else {}
    try:
        provider_min = int(manual.get("minBidPenny") or 0)
    except Exception:
        provider_min = 0
    try:
        provider_max = int(manual.get("maxBidPenny") or 0)
    except Exception:
        provider_max = 0
    try:
        rec_bid = int(manual.get("recBidPenny") or 0)
    except Exception:
        rec_bid = 0
    cap_rub = float(effective_hard_bid_cap(db, account_id) or 0)
    cap_penny = int(math.floor(cap_rub * 100.0)) if cap_rub > 0 else 0

    override = _owner_manual_bid_override(db, account_id, int(entry.get("item_id")))
    target = None
    source = ""
    if override:
        try:
            target = int(round(float(override.get("bid_rub")) * 100.0))
            source = "owner_manual_override"
        except Exception:
            target = None
    if target is None:
        try:
            exact = int(entry.get("bid_penny") or 0)
        except Exception:
            exact = 0
        if exact > 0:
            target = exact
            source = "exact_pre_brake_bid"
    if target is None and rec_bid > 0:
        target = rec_bid
        source = "avito_live_rec_bid"
    if target is None:
        ladder = manual.get("bids") if isinstance(manual.get("bids"), list) else []
        positive = []
        for x in ladder:
            try:
                value = int((x or {}).get("valuePenny") or 0)
                compare = float((x or {}).get("compare") or 0)
            except Exception:
                continue
            if value > 0 and compare > 0:
                positive.append(value)
        if positive:
            target = min(positive)
            source = "avito_live_effective_min"
    if target is None or target <= 0:
        return None, "no_safe_target", {
            "provider_min_penny": provider_min, "provider_max_penny": provider_max,
            "hard_cap_penny": cap_penny, "rec_bid_penny": rec_bid,
        }

    # Avito setManual accepts whole-ruble bid values. Clamp down to every hard
    # ceiling, then lift only to the provider minimum if that minimum itself fits.
    target = int(target // 100) * 100
    min_whole = int(math.ceil(provider_min / 100.0) * 100) if provider_min > 0 else 100
    ceilings = [x for x in (cap_penny, provider_max) if x and x > 0]
    ceiling = min(ceilings) if ceilings else None
    if ceiling is not None:
        target = min(target, int(ceiling // 100) * 100)
    if target < min_whole:
        target = min_whole
    if ceiling is not None and target > ceiling:
        return None, "provider_min_above_cap", {
            "provider_min_penny": provider_min, "provider_max_penny": provider_max,
            "hard_cap_penny": cap_penny, "target_penny": target,
        }
    return target, source, {
        "provider_min_penny": provider_min,
        "provider_max_penny": provider_max,
        "hard_cap_penny": cap_penny,
        "rec_bid_penny": rec_bid,
    }


def resume_account(account_id: str, apply: bool = False) -> dict:
    now_utc = datetime.now(timezone.utc)
    today_msk = datetime.now(MOSCOW).date().isoformat()
    state = _load_resume_state(account_id)
    out = {
        "account_id": account_id,
        "apply": bool(apply),
        "status": "running",
        "today_msk": today_msk,
        "restored": 0,
        "already_promoted": 0,
        "blocked": 0,
        "failed": 0,
        "verified": 0,
        "errors": [],
        "checked_at": now_utc.isoformat(),
    }
    if not state:
        out["status"] = "no_resume_state"
        return out
    brake_day = str(state.get("brake_day_msk") or "")
    out["brake_day_msk"] = brake_day
    if not brake_day:
        out["status"] = "invalid_resume_state"
        return out
    if brake_day >= today_msk:
        out["status"] = "waiting_next_moscow_day"
        return out

    db = SessionLocal()
    try:
        gate_ok, gate_reason, gate = _fresh_business_gate(db, account_id, today_msk)
    finally:
        db.close()
    out["business_gate"] = gate
    if not gate_ok:
        out["status"] = "paused_" + gate_reason
        _set_state_status(account_id, out["status"], last_gate=gate)
        return out

    items = state.get("items")
    if not isinstance(items, dict):
        out["status"] = "invalid_resume_items"
        return out
    pending = [
        dict(v) for v in items.values()
        if isinstance(v, dict) and str(v.get("status") or "") in
        {"braked", "resume_retry", "provider_accepted", "verify_pending"}
    ]
    pending.sort(key=lambda x: int(x.get("item_id") or 0))
    out["pending_before"] = len(pending)
    if not pending:
        out["status"] = "complete"
        _set_state_status(account_id, "complete", completed_at=now_utc.isoformat())
        return out

    retry = int(account_throttle_remaining(account_id) or 0)
    if retry > 0:
        out["status"] = "deferred_avito_throttle"
        out["retry_after_seconds"] = retry
        return out
    tok = _token(account_id)
    if not tok:
        out["status"] = "blocked_no_avito_token"
        return out
    headers = {"Authorization": f"Bearer {tok}"}

    accepted = {}
    accepted_request_ids = {}
    accepted_meta = {}
    with httpx.Client() as client:
        # CPX_BUDGET_RESUME_CLIENT_TENANT_THROTTLE_IDENTITY_V1: helpers imported
        # from cpx_cap_reconciler check the tenant-wide Retry-After ledger before
        # every provider call only when the client carries the canonical account
        # identity. Without this binding, inventory/detail/verify reads could race
        # past a sibling worker's freshly published 429.
        client._boris_account_id = account_id
        try:
            active = set(_active_item_ids(client, headers))
            active_pending = [x for x in pending if int(x.get("item_id") or 0) in active]
            out["active_pending"] = len(active_pending)
            if not active_pending:
                out["status"] = "waiting_items_active"
                return out
            promos = _bulk_promotions(
                client, headers, [int(x.get("item_id")) for x in active_pending]
            )
            promo_map = {}
            for p in promos:
                try:
                    promo_map[int(p.get("itemID"))] = p
                except Exception:
                    continue

            candidates = []
            for entry in active_pending:
                iid = int(entry.get("item_id"))
                p = promo_map.get(iid) or {}
                if p.get("manualPromotion") or p.get("autoPromotion"):
                    out["already_promoted"] += 1
                    if apply:
                        _mark_state_item(
                            account_id, iid, "already_promoted",
                            restored_at=now_utc.isoformat(),
                            restore_reason="promotion_already_present",
                        )
                    continue
                candidates.append(entry)
            out["candidates"] = len(candidates)
            if not apply:
                out["status"] = "would_resume" if candidates else "nothing_to_resume"
                out["would_resume_ids"] = [int(x.get("item_id")) for x in candidates[:MAX_RESUME_PER_RUN]]
                return out

            run_stamp = datetime.now(MOSCOW).strftime("%Y%m%d%H")
            for entry in candidates[:MAX_RESUME_PER_RUN]:
                iid = int(entry.get("item_id"))
                request_id = f"budget_resume:{run_stamp}:{iid}"
                try:
                    retry = int(account_throttle_remaining(account_id) or 0)
                    if retry > 0:
                        out["status"] = "deferred_avito_throttle"
                        out["retry_after_seconds"] = retry
                        break
                    detail = _get_bid_detail(client, headers, iid)
                    db = SessionLocal()
                    try:
                        target, target_source, evidence = _target_from_detail(
                            db, account_id, entry, detail
                        )
                    finally:
                        db.close()
                    if not target:
                        out["blocked"] += 1
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason=target_source,
                            last_target_evidence=evidence,
                        )
                        continue
                    action_type = int(detail.get("actionTypeID") or entry.get("action_type_id") or 5)

                    db = SessionLocal()
                    try:
                        guard = can_execute_live_action(
                            db,
                            account_id,
                            "cpx.raise_bid",
                            {
                                "actor": "boris_auto",
                                "trigger": "daily_budget_reset",
                                "source": "budget_resume",
                                "request_id": request_id,
                            },
                            bid_context={
                                "item_id": iid,
                                "old_bid_rub": 0.0,
                                "new_bid_rub": target / 100.0,
                                "applied_step_pct": 0,
                                "avito_min_bid_rub": evidence.get("provider_min_penny", 0) / 100.0,
                                "new_item_no_promo": False,
                                "budget_resume": True,
                                "brake_day_msk": brake_day,
                            },
                            balance={"status": "UNKNOWN", "value": None},
                        )
                    finally:
                        try:
                            db.close()
                        except Exception:
                            pass
                    if not guard.get("allowed"):
                        out["blocked"] += 1
                        reason = guard.get("reason_code") or "money_guard_blocked"
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason=reason,
                            last_guard=guard,
                        )
                        if reason in {"blocked_max_actions_run", "blocked_max_actions_day"}:
                            break
                        continue

                    # Final money proof immediately before receipt/provider write.
                    final_money = check_raise_allowed(account_id) or {}
                    if not final_money.get("allowed"):
                        out["blocked"] += 1
                        reason = final_money.get("reason_code") or "final_money_guard_blocked"
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason=reason,
                            final_money_guard=final_money,
                        )
                        out["status"] = "paused_" + reason
                        break
                    # CPX_BUDGET_RESUME_FINAL_COHERENCE_RECHECK_V1: the live
                    # balance guard above may perform provider I/O. Re-read the
                    # canonical spend/stats pair afterwards, at the final local
                    # DB boundary, so a pair that crossed the 15m/5m limits while
                    # waiting can never authorize a restore write.
                    from app.services.marketing_signal_guard import money_raise_signals_eligible as _resume_signals_ok
                    _signal_db = SessionLocal()
                    try:
                        _final_signal_ok, _final_signal = _resume_signals_ok(
                            _signal_db, account_id,
                            max_age_seconds=FRESH_SECONDS,
                            max_snapshot_skew_seconds=300,
                        )
                    finally:
                        _signal_db.close()
                    if not _final_signal_ok:
                        out["blocked"] += 1
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason="money_signal_stale_or_degraded",
                            final_money_signal=_final_signal,
                        )
                        out["status"] = "paused_money_signal_stale_or_degraded"
                        break

                    prior = _cpx_receipt_get(account_id, request_id)
                    if prior and str(prior.get("status") or "") in {"succeeded", "reconciled"}:
                        accepted[iid] = int(prior.get("observed_bid_penny") or target)
                        accepted_request_ids[iid] = request_id
                        continue
                    if prior and str(prior.get("status") or "") in {
                        "prepared", "attempting", "delivery_unknown"
                    }:
                        out["blocked"] += 1
                        _mark_state_item(
                            account_id, iid, "verify_pending",
                            last_block_reason="cpx_execution_reconciliation",
                        )
                        continue

                    receipt, created = _cpx_receipt_prepare(
                        account_id, request_id, iid, "raise", "budget_resume", 0, target
                    )
                    if not created:
                        if str((receipt or {}).get("status") or "") == "capacity_blocked":
                            out["blocked"] += 1
                            out["status"] = "paused_" + str(
                                (receipt or {}).get("reason_code") or "money_capacity"
                            )
                            break
                        out["blocked"] += 1
                        _mark_state_item(
                            account_id, iid, "verify_pending",
                            last_block_reason="cpx_execution_reconciliation",
                        )
                        continue

                    _cpx_receipt_mark(account_id, request_id, "attempting")
                    # CPX_BUDGET_RESUME_FINAL_SHARED_THROTTLE_RECHECK_V1:
                    # another trusted worker can publish Retry-After after our
                    # earlier getBids/guard phase. Re-check at the last possible
                    # boundary before the real money write and fail closed.
                    retry = int(account_throttle_remaining(account_id) or 0)
                    if retry > 0:
                        _cpx_receipt_mark(
                            account_id, request_id, "failed",
                            http_status=429,
                            result={"reason": "shared_avito_account_throttle",
                                    "retry_after_seconds": retry},
                        )
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason="shared_avito_account_throttle",
                        )
                        out["status"] = "deferred_avito_throttle"
                        out["retry_after_seconds"] = retry
                        break
                    try:
                        wr = client.post(
                            "https://api.avito.ru/cpxpromo/1/setManual",
                            headers={**headers, "Content-Type": "application/json"},
                            json={
                                "actionTypeID": action_type,
                                "bidPenny": int(target),
                                "itemID": iid,
                            },
                            timeout=20,
                        )
                    except Exception as exc:
                        _cpx_receipt_mark(
                            account_id, request_id, "delivery_unknown", error=repr(exc)
                        )
                        _mark_state_item(
                            account_id, iid, "verify_pending",
                            last_block_reason="delivery_unknown",
                        )
                        out["failed"] += 1
                        continue
                    if wr.status_code == 429:
                        # CPX_BUDGET_RESUME_RETRY_AFTER_HTTP_DATE_V1: Avito may
                        # return either delta-seconds or an RFC HTTP-date. Preserve
                        # either form in the tenant-wide throttle ledger.
                        retry_after = 30
                        try:
                            raw_retry = str(wr.headers.get("Retry-After") or "").strip()
                            if raw_retry:
                                try:
                                    retry_after = max(5, int(float(raw_retry)))
                                except Exception:
                                    from email.utils import parsedate_to_datetime as _parse_resume_ra
                                    when = _parse_resume_ra(raw_retry)
                                    if when.tzinfo is None:
                                        when = when.replace(tzinfo=timezone.utc)
                                    retry_after = max(5, int((when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
                        except Exception:
                            retry_after = 30
                        retry_after = int(record_account_throttle(
                            account_id, retry_after, source="cpx_budget_resume_429"
                        ) or retry_after)
                        _cpx_receipt_mark(
                            account_id, request_id, "failed",
                            http_status=429,
                            result={"reason": "avito_account_throttled"},
                        )
                        _mark_state_item(
                            account_id, iid, "resume_retry",
                            last_block_reason="avito_account_throttled",
                        )
                        out["status"] = "deferred_avito_throttle"
                        out["retry_after_seconds"] = retry_after
                        break
                    if wr.status_code != 200:
                        # CPX_BUDGET_RESUME_AMBIGUOUS_5XX_FAIL_CLOSED_V1:
                        # a provider 5xx after setManual is not proof that the
                        # money mutation was rejected. Avito may have applied the
                        # write before returning an upstream/server error. Persist
                        # delivery_unknown and require reconciliation instead of
                        # allowing the next budget-resume cycle to replay blindly.
                        if int(wr.status_code) >= 500:
                            _cpx_receipt_mark(
                                account_id, request_id, "delivery_unknown",
                                http_status=wr.status_code,
                                result={"body": wr.text[:200], "reason": "provider_5xx_ambiguous"},
                            )
                            _mark_state_item(
                                account_id, iid, "verify_pending",
                                last_block_reason=f"setmanual_http_{wr.status_code}_ambiguous",
                            )
                        else:
                            _cpx_receipt_mark(
                                account_id, request_id, "failed",
                                http_status=wr.status_code,
                                result={"body": wr.text[:200]},
                            )
                            _mark_state_item(
                                account_id, iid, "resume_retry",
                                last_block_reason=f"setmanual_http_{wr.status_code}",
                            )
                        out["failed"] += 1
                        continue

                    # CPX_BUDGET_RESUME_VERIFY_BEFORE_SUCCESS_V2: HTTP 200 proves
                    # provider acceptance, not the exact resulting bid. Keep the
                    # durable receipt non-terminal until the read-back below proves
                    # observed == intended. This prevents a verify 429/timeout from
                    # leaving a false succeeded receipt that a later run would trust.
                    _cpx_receipt_mark(
                        account_id, request_id, "attempting",
                        http_status=wr.status_code,
                        result={
                            "budget_resume": True,
                            "provider_accepted": True,
                            "target_source": target_source,
                            "brake_day_msk": brake_day,
                        },
                    )
                    _mark_state_item(
                        account_id, iid, "provider_accepted",
                        provider_accepted_at=datetime.now(timezone.utc).isoformat(),
                        restored_bid_penny=int(target),
                        restore_target_source=target_source,
                        request_id=request_id,
                    )
                    accepted[iid] = int(target)
                    accepted_request_ids[iid] = request_id
                    accepted_meta[iid] = {
                        "mandate_id": guard.get("mandate_id"),
                        "mandate_version": guard.get("mandate_version"),
                        "balance_status": str((final_money.get("balance") or {}).get("status") or "UNKNOWN"),
                        "guard_reason": guard.get("reason_code"),
                    }

                except ProviderDeferred as exc:
                    retry = int(record_account_throttle(
                        account_id, int(exc.retry_after_seconds),
                        source="cpx_budget_resume_provider_deferred",
                    ) or exc.retry_after_seconds)
                    out["status"] = "deferred_avito_throttle"
                    out["retry_after_seconds"] = retry
                    break
                except Exception as exc:
                    out["failed"] += 1
                    out["errors"].append(f"{iid}:{type(exc).__name__}:{str(exc)[:160]}")
                    _mark_state_item(
                        account_id, iid, "resume_retry",
                        last_block_reason=f"{type(exc).__name__}:{str(exc)[:120]}",
                    )

            if accepted:
                try:
                    verify = _bulk_promotions(client, headers, sorted(accepted))
                    verify_map = {}
                    for p in verify:
                        try:
                            verify_map[int(p.get("itemID"))] = p
                        except Exception:
                            continue
                    for iid, target in accepted.items():
                        p = verify_map.get(iid) or {}
                        manual = p.get("manualPromotion") if isinstance(p.get("manualPromotion"), dict) else {}
                        try:
                            observed = int(manual.get("bidPenny") or 0)
                        except Exception:
                            observed = 0
                        # CPX_BUDGET_RESUME_EXACT_VERIFY_V1: provider acceptance is
                        # not enough to prove the intended money state. The read-back
                        # must equal the exact bid BORIS requested; any zero or third
                        # value is ambiguous (including eventual consistency) and is
                        # handed to the canonical read-only receipt reconciler rather
                        # than being falsely labelled restored.
                        if observed == int(target):
                            _request_id = str(accepted_request_ids.get(iid) or "")
                            if _request_id:
                                _cpx_receipt_mark(
                                    account_id, _request_id, "succeeded",
                                    http_status=200, observed_bid_penny=observed,
                                    result={
                                        "budget_resume": True,
                                        "reconciliation": "exact_post_write_readback",
                                        "intended_bid_penny": int(target),
                                        "observed_bid_penny": int(observed),
                                    },
                                )
                            _mark_state_item(
                                account_id, iid, "restored",
                                verified_at=datetime.now(timezone.utc).isoformat(),
                                observed_bid_penny=observed,
                            )
                            _meta = accepted_meta.get(iid) or {}
                            log_action(
                                account_id=account_id,
                                action="Возобновил продвижение",
                                object_kind="объявление",
                                object_name=str(iid),
                                before_val="остановлено дневным лимитом",
                                after_val=f"ставка {target/100:.0f} руб",
                                reason="новый московский день; восстановление после budget-brake; ставка подтверждена Avito",
                                actor=ACTOR_BORIS_AUTO,
                                source="budget_resume",
                                trigger="daily_budget_reset",
                                mandate_id=_meta.get("mandate_id"),
                                mandate_version=_meta.get("mandate_version"),
                                request_id=_request_id or None,
                                balance_status=_meta.get("balance_status") or "UNKNOWN",
                                guard_reason=_meta.get("guard_reason"),
                            )
                            _audit_log(
                                account_id,
                                "cpx_budget_resume",
                                f"объявление {iid}: продвижение восстановлено после дневного budget-brake; Avito подтвердил ставку {target/100:.0f} ₽",
                                "boris_budget_guard",
                            )
                            out["verified"] += 1
                            out["restored"] += 1
                        else:
                            _request_id = str(accepted_request_ids.get(iid) or "")
                            if _request_id:
                                _cpx_receipt_mark(
                                    account_id, _request_id, "delivery_unknown",
                                    http_status=200, observed_bid_penny=observed,
                                    result={
                                        "reconciliation": "post_write_bid_mismatch",
                                        "intended_bid_penny": int(target),
                                        "observed_bid_penny": int(observed),
                                    },
                                    error="post-write verification did not observe intended bid",
                                )
                            _mark_state_item(
                                account_id, iid, "verify_pending",
                                last_block_reason="post_write_bid_mismatch",
                                intended_bid_penny=int(target),
                                observed_bid_penny=int(observed),
                            )
                except ProviderDeferred as exc:
                    # A verify call may itself be the first worker to observe
                    # provider 429. Publish that Retry-After tenant-wide so the
                    # remaining BORIS workers stop immediately instead of relying
                    # only on this local result.
                    _verify_retry = int(record_account_throttle(
                        account_id, int(exc.retry_after_seconds),
                        source="cpx_budget_resume_verify_deferred",
                    ) or exc.retry_after_seconds)
                    out["status"] = "deferred_avito_throttle"
                    out["retry_after_seconds"] = _verify_retry
                except Exception as exc:
                    out["errors"].append(f"verify:{type(exc).__name__}:{str(exc)[:160]}")

        except ProviderDeferred as exc:
            retry = int(record_account_throttle(
                account_id, int(exc.retry_after_seconds), source="cpx_budget_resume_429"
            ) or exc.retry_after_seconds)
            out["status"] = "deferred_avito_throttle"
            out["retry_after_seconds"] = retry
        except Exception as exc:
            out["status"] = "error"
            out["failed"] += 1
            out["errors"].append(f"{type(exc).__name__}:{str(exc)[:180]}")

    if out["status"] == "running":
        current = _load_resume_state(account_id)
        remaining = sum(
            1 for x in ((current.get("items") or {}).values())
            if isinstance(x, dict) and str(x.get("status") or "") in
            {"braked", "resume_retry", "provider_accepted", "verify_pending"}
        )
        out["pending_after"] = remaining
        out["status"] = "complete" if remaining == 0 else "partial"
    return out


def _accounts(account_filter: str | None) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.query(Storage.account_id).filter(
            Storage.key == "cpx_budget_resume_state"
        ).distinct().all()
        out = sorted({str(r[0]) for r in rows if r and r[0]})
    finally:
        db.close()
    if account_filter:
        out = [x for x in out if x == account_filter]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--account", default="")
    args = ap.parse_args()
    lock = open("/root/BORIS/backend/run/cpx_budget_resume.lock", "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("CPX_BUDGET_RESUME_SKIP overlap", flush=True)
        return 0
    failures = []
    try:
        accounts = _accounts(args.account.strip() or None)
        print(f"CPX_BUDGET_RESUME accounts={len(accounts)} apply={args.apply}", flush=True)
        for account_id in accounts:
            out = resume_account(account_id, apply=args.apply)
            print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
            if out.get("status") in {"error", "invalid_resume_state", "invalid_resume_items"}:
                failures.append(account_id)
    finally:
        lock.close()
    print(f"CPX_BUDGET_RESUME_DONE failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
