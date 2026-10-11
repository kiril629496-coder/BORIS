#!/usr/bin/env python3
"""Bounded first-bid pacing for owner-authorized daily budgets.

DAILY_BUDGET_FIRST_BID_PACING_V1
When an account is materially under its owner-defined daily spend target and
has no currently promoted CPX candidates, activate at most two zero-bid items
through the canonical provider-probe first-bid path. Selection is evidence-led:
current contacts -> 7d contacts -> 7d views. Every mutation still passes live
provider getBids, hard bid cap, owner budget, balance, mandate, receipt and
measurement guards inside cpx_advisor.apply_one.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.marketing_clock import marketing_today_iso
from app.services.marketing_signal_guard import money_raise_signals_eligible
from app.services.control_plane_adapters_ext import marketing_service_entitlement
from kpi_goal_runner import get_goal_auto_accounts
from app.api.cpx_advisor import ApplyOneBody, apply_one

MSK = ZoneInfo("Europe/Moscow")
PACING_TARGET_RATIO = 0.98
MAX_FIRST_BIDS_PER_ACCOUNT_DAY = 2
ACTIVE_SCOPE = {
    "pbi_artashes_plitka_mo_83993", "evgeniy_peregorodki_12447",
    "mbroker_27859", "vasyailchenko_82650", "planetazayavki_65985",
    "7473337_04122",
}


def _load(db, account_id: str, key: str):
    row = db.query(Storage).filter(
        Storage.account_id == account_id, Storage.key == key
    ).order_by(Storage.id.desc()).first()
    if not row or not row.value:
        return {}
    try:
        return json.loads(row.value)
    except Exception:
        return {}


def _expected_ratio(now_msk: datetime) -> float:
    seconds = now_msk.hour * 3600 + now_msk.minute * 60 + now_msk.second
    elapsed = max(0.0, min(1.0, seconds / 86400.0))
    return min(PACING_TARGET_RATIO, elapsed * PACING_TARGET_RATIO)


def _latest_raise_effects(db, account_id: str) -> dict[int, str]:
    rows = _load(db, account_id, "cpx_learning_journal") or []
    out = {}
    if isinstance(rows, list):
        for rec in reversed(rows):
            if not isinstance(rec, dict) or str(rec.get("action") or "") != "raise":
                continue
            try:
                iid = int(rec.get("item_id"))
            except Exception:
                continue
            out.setdefault(iid, str(rec.get("effect") or ""))
    return out


def _candidate_rows(advice: dict, latest_effect: dict[int, str]):
    recs = advice.get("recommendations") or {}
    rows = []
    seen = set()
    for group in ("raise", "watching"):
        for rec in (recs.get(group) or []):
            if not isinstance(rec, dict):
                continue
            try:
                iid = int(rec.get("id") or rec.get("item_id") or 0)
            except Exception:
                continue
            if not iid or iid in seen:
                continue
            seen.add(iid)
            if bool(rec.get("promotion_active")):
                continue
            if rec.get("bid_rub") not in (None, 0, 0.0):
                continue
            if str(rec.get("write_capability") or "") not in {"provider_probe_money", "canonical"}:
                continue
            if str(rec.get("boris_measure_status") or "") == "waiting_measurement":
                continue
            if latest_effect.get(iid) == "worsened":
                continue
            contacts_today = int(rec.get("contacts") or 0)
            contacts_7d = int(rec.get("contacts_7d") or 0)
            views_7d = int(rec.get("views_7d") or 0)
            if contacts_today <= 0 and contacts_7d <= 0 and views_7d < 5:
                continue
            score = (
                1 if contacts_today > 0 else 0,
                contacts_today,
                contacts_7d,
                views_7d,
                1 if str(rec.get("write_capability") or "") == "canonical" else 0,
            )
            rows.append((score, iid, rec))
    rows.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    return rows


def run_account(account_id: str, now_msk: datetime):
    db = SessionLocal()
    try:
        entitlement = marketing_service_entitlement(db, account_id) or {}
        if str(entitlement.get("state") or "").lower() != "active":
            return {"account_id": account_id, "status": "blocked_entitlement"}
        kpi = _load(db, account_id, "kpi_settings") or {}
        budget = float(kpi.get("daily_budget_limit_rub") or 0)
        if budget <= 0 or not bool(kpi.get("bid_autopilot")):
            return {"account_id": account_id, "status": "disabled_or_no_budget"}
        ok, money = money_raise_signals_eligible(db, account_id, max_age_seconds=900.0)
        if not ok:
            return {"account_id": account_id, "status": "money_signal_wait", "money": money}
        ds = _load(db, account_id, "daily_stats:" + marketing_today_iso()) or {}
        spending = ds.get("spending") if isinstance(ds.get("spending"), dict) else {}
        if str(spending.get("status") or "") != "ok":
            return {"account_id": account_id, "status": "spend_unavailable"}
        spent = float(spending.get("all_spend_rub") or 0)
        ratio = spent / budget
        expected = _expected_ratio(now_msk)
        if ratio >= expected:
            return {"account_id": account_id, "status": "on_pace", "spent": spent, "budget": budget,
                    "ratio": round(ratio, 4), "expected": round(expected, 4)}
        advice = _load(db, account_id, "cpx_advice") or {}
        effects = _latest_raise_effects(db, account_id)
        candidates = _candidate_rows(advice, effects)
    finally:
        db.close()

    details = []
    applied = 0
    for _, iid, rec in candidates:
        if applied >= MAX_FIRST_BIDS_PER_ACCOUNT_DAY:
            break
        result = apply_one(ApplyOneBody(
            account_id=account_id,
            item_id=int(iid),
            action="raise",
            max_bid_delta_pct=10,
            actor_type="boris_auto",
            source="daily_budget_pacing_first_bid",
            trigger="daily_budget_underpace",
            request_id=f"budget-first-bid:{account_id}:{marketing_today_iso()}:{iid}",
        )) or {}
        changed = bool(result.get("status") == "ok" and result.get("new_bid_rub") is not None
                       and float(result.get("new_bid_rub") or 0) > float(result.get("old_bid_rub") or 0))
        if changed:
            applied += 1
        details.append({
            "item_id": iid,
            "title": rec.get("title"),
            "contacts_today": int(rec.get("contacts") or 0),
            "contacts_7d": int(rec.get("contacts_7d") or 0),
            "views_7d": int(rec.get("views_7d") or 0),
            "status": result.get("status"),
            "changed_avito": changed,
            "old_bid_rub": result.get("old_bid_rub"),
            "new_bid_rub": result.get("new_bid_rub"),
            "reason": result.get("blocked_by") or result.get("reason_code") or result.get("reason") or result.get("message"),
        })
        if str(result.get("blocked_by") or result.get("reason_code") or "") in {
            "account_measurement_backlog_wait", "native_first_bid_measurement_wait",
            "native_first_bid_unresolved_receipt", "blocked_daily_budget_near_limit",
            "blocked_daily_budget_exhausted", "blocked_insufficient_balance",
            "shared_avito_account_throttle", "avito_account_throttled",
        }:
            break
    return {"account_id": account_id, "status": "ok", "spent": spent, "budget": budget,
            "ratio": round(ratio, 4), "expected": round(expected, 4),
            "candidate_count": len(candidates), "applied": applied, "details": details[:8]}


def main():
    now = datetime.now(MSK)
    out = []
    for cfg in get_goal_auto_accounts():
        aid = str(cfg.get("account_id") or "")
        if not aid or aid not in ACTIVE_SCOPE or float(cfg.get("target_leads_per_day") or 0) <= 0:
            continue
        try:
            out.append(run_account(aid, now))
        except Exception as exc:
            out.append({"account_id": aid, "status": "error", "error": type(exc).__name__, "detail": str(exc)[:220]})
    print(json.dumps({"marker": "DAILY_BUDGET_FIRST_BID_PACING_V1", "now_msk": now.isoformat(), "accounts": out}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
