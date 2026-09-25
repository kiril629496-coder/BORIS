"""Behavioural shadow: legacy_decision vs config_shadow_decision.

Nothing here publishes, sends, spends, bids, creates mandates or changes any
runtime state. Every function is a read.

IMPORTANT LIMITATION, stated on purpose: the legacy side REPLICATES the
semantics of the production gates, it does not call them (calling them would
risk side effects). Each replication carries the file:line it mirrors, so the
two can be re-verified when the original changes.
"""

import datetime
import json

from app.client_config import entitlements as E
from app.client_config import resolver as R

SAME = "same"
DIFFERENT = "different"
SHADOW_UNKNOWN = "shadow_unknown"
LEGACY_UNKNOWN = "legacy_unknown"
NOT_COMPARABLE = "not_comparable"

KNOWN = "known"
UNKNOWN = "unknown"


def _d(value, state=KNOWN, **ev):
    return {"value": value, "state": state, "evidence": ev}


def _unknown(reason, **ev):
    ev["reason"] = reason
    return {"value": None, "state": UNKNOWN, "evidence": ev}


def _storage(db, account_id, key):
    from sqlalchemy import text
    row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                     {"a": account_id, "k": key}).fetchone()
    if not row:
        return None
    v = row[0]
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _mandate(db, account_id):
    from sqlalchemy import text
    row = db.execute(text(
        "SELECT id, status, allowed_operations, max_actions_run, max_bid_delta_pct"
        " FROM money_mandates WHERE :a = ANY(account_scope)"
        " ORDER BY id DESC LIMIT 1"), {"a": account_id}).fetchone()
    return row


def legacy_money_allowed(db, account_id):
    """mirrors app/services/autonomy.py:30 + cpx_advisor_runner.py:88"""
    row = _mandate(db, account_id)
    if not row:
        return _d(False, source="money_mandates: no row", gate="autonomy.py:30")
    status = (row[1] or "").lower()
    return _d(status == "active", mandate_id=row[0], status=status, gate="autonomy.py:30")


def legacy_bid_step(db, account_id):
    """mirrors app/api/cpx_advisor.py:874 and services/intraday.py:161"""
    row = _mandate(db, account_id)
    if not row or row[4] is None:
        return _unknown("no_mandate_step", gate="cpx_advisor.py:874")
    return _d(int(row[4]), gate="cpx_advisor.py:874")


def legacy_autopilot_on(db, account_id):
    """mirrors kpi_goal_runner.py:47 - only mode == goal_auto is autonomous"""
    st = _storage(db, account_id, "autopilot_settings") or {}
    mode = st.get("mode")
    if mode is None:
        return _unknown("no_autopilot_settings", gate="kpi_goal_runner.py:47")
    return _d(mode == "goal_auto", mode=mode, gate="kpi_goal_runner.py:47")


def legacy_kpi_target(db, account_id):
    """mirrors cpx_advisor_runner.py:73 and client_report.py:57"""
    st = _storage(db, account_id, "kpi_settings") or {}
    v = st.get("target_leads_per_day")
    if v is None:
        return _unknown("no_kpi_settings", gate="cpx_advisor_runner.py:73")
    return _d(float(v), gate="cpx_advisor_runner.py:73")


def legacy_mop_allowed(db, account_id):
    """mirrors app/api/messenger.py:591"""
    b = _storage(db, account_id, "billing")
    if not b or "manager_msgs_purchased" not in b:
        return _unknown("no_manager_keys", gate="messenger.py:591")
    try:
        purchased = int(b.get("manager_msgs_purchased") or 0)
    except (TypeError, ValueError):
        purchased = 0
    return _d(purchased > 0, purchased=purchased, gate="messenger.py:591")


def legacy_rop_allowed(db, account_id):
    """mirrors app/api/calltracking.py:1088"""
    b = _storage(db, account_id, "billing")
    if not b or "rop_minutes_purchased" not in b:
        return _unknown("no_rop_keys", gate="calltracking.py:1088")
    try:
        purchased = float(b.get("rop_minutes_purchased") or 0)
    except (TypeError, ValueError):
        purchased = 0.0
    return _d(purchased > 0, purchased=purchased, gate="calltracking.py:1088")


def legacy_reports_enabled(db, account_id):
    """mirrors client_report.py delivery via tg_routes"""
    from sqlalchemy import text
    row = db.execute(text("SELECT count(*) FROM tg_routes WHERE account_id=:a"
                          " AND thread_key='reports'"), {"a": account_id}).fetchone()
    return _d(bool(row and row[0]), routes=row[0] if row else 0, gate="tg_routes")


def shadow_money_allowed(eff, db, account_id):
    node = eff.get("automation.money_actions") or {}
    if node.get("state") != "known":
        return _unknown("config_unknown", field="automation.money_actions")
    v = node.get("value")
    return _d(bool(v) and v is not False, raw=v)


def shadow_bid_step(eff):
    return _pick(eff, "limits.max_bid_delta_pct", int)


def shadow_autopilot_on(eff):
    node = eff.get("automation.mode") or {}
    if node.get("state") != "known":
        return _unknown("config_unknown", field="automation.mode")
    return _d(node.get("value") == "autopilot", mode=node.get("value"))


def shadow_kpi_target(eff):
    return _pick(eff, "kpi.target_leads_per_day", float)


def shadow_product(db, account_id, product):
    """Config side asks the normalized entitlement resolver, not raw JSON."""
    r = E.has_entitlement(db, account_id, product)
    if r.get("state") != "known":
        return _unknown(r.get("reason"), source=r.get("source"))
    return _d(bool(r.get("allowed")), reason=r.get("reason"))


def shadow_reports_enabled(eff):
    node = eff.get("schedule.reports") or {}
    if node.get("state") != "known":
        return _unknown("config_unknown", field="schedule.reports")
    v = node.get("value") or {}
    return _d(bool(v.get("enabled")), raw=v)


def _pick(eff, dotted, cast):
    node = eff.get(dotted) or {}
    if node.get("state") != "known" or node.get("value") is None:
        return _unknown("config_unknown", field=dotted)
    try:
        return _d(cast(node.get("value")), field=dotted)
    except (TypeError, ValueError):
        return _unknown("bad_type", field=dotted, raw=node.get("value"))


ALLOW_DECISIONS = ("money_allowed", "autopilot_on", "mop_allowed", "rop_allowed")


def compare_one(name, legacy, shadow):
    if legacy["state"] == UNKNOWN and shadow["state"] == UNKNOWN:
        result = NOT_COMPARABLE
    elif shadow["state"] == UNKNOWN:
        result = SHADOW_UNKNOWN
    elif legacy["state"] == UNKNOWN:
        result = LEGACY_UNKNOWN
    elif legacy["value"] == shadow["value"]:
        result = SAME
    else:
        result = DIFFERENT
    effective = shadow["value"] if shadow["state"] == KNOWN else None
    if name in ALLOW_DECISIONS and shadow["state"] == UNKNOWN:
        effective = "cannot_decide"
    return {"decision": name, "result": result,
            "legacy_value": legacy["value"], "shadow_value": shadow["value"],
            "shadow_effective": effective,
            "legacy_state": legacy["state"], "shadow_state": shadow["state"],
            "legacy_evidence": legacy["evidence"], "shadow_evidence": shadow["evidence"]}


def run_account(db, owner_id, account_id):
    eff = R.effective(db, owner_id, account_id)["effective"]
    pairs = [
        ("money_allowed", legacy_money_allowed(db, account_id), shadow_money_allowed(eff, db, account_id)),
        ("bid_step_pct", legacy_bid_step(db, account_id), shadow_bid_step(eff)),
        ("autopilot_on", legacy_autopilot_on(db, account_id), shadow_autopilot_on(eff)),
        ("kpi_target", legacy_kpi_target(db, account_id), shadow_kpi_target(eff)),
        ("mop_allowed", legacy_mop_allowed(db, account_id), shadow_product(db, account_id, "mop")),
        ("rop_allowed", legacy_rop_allowed(db, account_id), shadow_product(db, account_id, "rop")),
        ("reports_enabled", legacy_reports_enabled(db, account_id), shadow_reports_enabled(eff)),
    ]
    return [compare_one(n, l, s) for n, l, s in pairs]
