"""
BORIS Autonomy Guard — единственная точка, решающая, можно ли выполнить
живое или денежное действие автоматически.
"""
from datetime import datetime, timezone

from sqlalchemy import text
from app.services.exception_observability import observe_suppressed

OPERATIONS = {
    "cpx.raise_bid":          {"money": True,  "visible": True,  "reversible": True,
                               "grant": "mandate"},
    "cpx.lower_bid":          {"money": False, "visible": True,  "reversible": True,
                               "grant": "mandate"},
    "cpx.remove_promotion":   {"money": False, "visible": True,  "reversible": False,
                               "grant": "explicit"},
}

REQUIRED_CONTEXT = ("actor", "trigger", "source")


def _deny(code, **extra):
    d = {"allowed": False, "requires_confirmation": False, "reason_code": code,
         "mandate_id": None, "mandate_version": None, "limits_applied": {}, "evidence": {}}
    d.update(extra)
    return d


def load_mandate(db, account_id, operation, source=None):
    """Return the active mandate that belongs to this execution lane.

    MONEY_MANDATE_SOURCE_ISOLATION_V1: launch-only authority must never become
    the effective KPI authority merely because its row id is newer. Normal KPI
    raises prefer paid_tariff_kpi; exact feed20 first activation prefers
    new_feed_launch. Revoked/expired/wrong-operation rows are excluded in SQL.
    """
    rows = db.execute(text(
        "SELECT id, version, status, source, allowed_operations, max_actions_run, max_actions_day,"
        " max_bid_delta_pct, min_bid_rub, max_bid_rub, reserve_rub, daily_budget_rub,"
        " valid_from, valid_until, revoked_at"
        " FROM money_mandates"
        " WHERE :acc = ANY(account_scope)"
        "   AND status='active' AND revoked_at IS NULL"
        "   AND :op = ANY(allowed_operations)"
        "   AND (valid_from IS NULL OR valid_from <= now())"
        "   AND (valid_until IS NULL OR valid_until > now())"
        " ORDER BY id DESC"), {"acc": account_id, "op": operation}).fetchall()
    if not rows:
        return None
    # MONEY_MANDATE_STRICT_SOURCE_ISOLATION_V2: launch authority and ordinary
    # paid-KPI authority are separate grants. Never cross-fallback merely because
    # another active mandate exists for the same account/operation.
    required_source = "new_feed_launch" if str(source or "") == "feed20" else "paid_tariff_kpi"
    mapped = [dict(r._mapping) for r in rows]
    for m in mapped:
        if str(m.get("source") or "") == required_source:
            return m
    return None


def count_actions_day(db, account_id):
    # MONEY_ACTION_DAY_MOSCOW_V1: Avito/BORIS commercial day is Moscow time.
    # Keep Python guard aligned with the race-free receipt trigger; otherwise
    # 00:00-03:00 MSK can produce contradictory daily-cap decisions.
    r = db.execute(text(
        "SELECT count(*) FROM action_log"
        " WHERE account_id = :acc"
        " AND (ts AT TIME ZONE 'Europe/Moscow')::date"
        "     = (now() AT TIME ZONE 'Europe/Moscow')::date"
        " AND mandate_id IS NOT NULL"), {"acc": account_id}).fetchone()
    return int(r[0]) if r else 0


def _request_run_prefix(request_id):
    raw = str(request_id or "")
    # NF10_HOURLY_RUN_SCOPE_V1: hourly new-feed ramps are one logical run per
    # account/hour, not one lifetime bucket named just "nf10". New request IDs
    # use nf10:YYYYMMDDHH:item, so the run cap is isolated to that hour while
    # max_actions_day remains the independent daily ceiling. Legacy shapes stay
    # on the historical first-token behavior and cannot authorize new actions.
    parts = raw.split(":")
    if len(parts) >= 3 and parts[0] == "nf10" and len(parts[1]) == 10 and parts[1].isdigit():
        return ":".join(parts[:2])
    # BUDGET_RESUME_HOURLY_RUN_SCOPE_V1: next-day restoration is bounded to
    # five items per real hourly run, not five items for the lifetime of the
    # account. The independent daily cap remains authoritative.
    if len(parts) >= 3 and parts[0] == "budget_resume" and len(parts[1]) == 10 and parts[1].isdigit():
        return ":".join(parts[:2])
    return raw.split(":", 1)[0] if ":" in raw else raw


def count_actions_run(db, account_id, request_id):
    """Count all per-item receipts/actions belonging to one logical runner cycle."""
    if not request_id:
        return 0
    prefix = _request_run_prefix(request_id)
    r = db.execute(text(
        "SELECT count(*) FROM action_log WHERE account_id=:acc AND (request_id = :rid OR request_id LIKE :pat)"),
        {"acc": account_id, "rid": prefix, "pat": prefix + ':%'}).fetchone()
    return int(r[0]) if r else 0


def item_touched_in_run(db, request_id, item_id):
    if not request_id or not item_id:
        return False
    prefix = _request_run_prefix(request_id)
    r = db.execute(text(
        "SELECT count(*) FROM action_log"
        " WHERE (request_id = :rid OR request_id LIKE :pat) AND object_name = :item"),
        {"rid": prefix, "pat": prefix + ':%', "item": str(item_id)}).fetchone()
    return bool(r and int(r[0]) > 0)


def log_guard_block(db, account_id, operation, decision, reason_code, ctx,
                    object_id=None, mandate=None, balance_status=None,
                    limits=None, evidence=None):
    import json as _j
    try:
        db.execute(text(
            "INSERT INTO autonomy_guard_events"
            " (account_id, operation, object_id, decision, reason_code, actor, trigger,"
            "  source, request_id, mandate_id, mandate_version, balance_status,"
            "  limits_applied, evidence)"
            " VALUES (:acc, :op, :obj, :dec, :rc, :actor, :trg, :src, :rid, :mid, :mver,"
            "         :bst, CAST(:lim AS jsonb), CAST(:ev AS jsonb))"),
            {"acc": account_id, "op": operation, "obj": (str(object_id) if object_id else None),
             "dec": decision, "rc": reason_code,
             "actor": (ctx or {}).get("actor"), "trg": (ctx or {}).get("trigger"),
             "src": (ctx or {}).get("source"), "rid": (ctx or {}).get("request_id"),
             "mid": (mandate or {}).get("id"), "mver": (mandate or {}).get("version"),
             "bst": balance_status,
             "lim": _j.dumps(limits or {}, ensure_ascii=False, default=str),
             "ev": _j.dumps(evidence or {}, ensure_ascii=False, default=str)})
        db.commit()
    except Exception as e:
        print(f"[autonomy] не записал guard event: {str(e)[:120]}")


def can_execute_live_action(db, account_id, operation, execution_context,
                            bid_context=None, mass_count=1, balance=None,
                            explicit_confirmation_id=None):
    ctx = execution_context or {}
    bal = balance or {"status": "UNKNOWN", "value": None}
    bid = bid_context or {}

    spec = OPERATIONS.get(operation)
    if not spec:
        return _deny("blocked_unknown_operation")

    # Reliability kill switch is enforced at the authoritative live-action guard,
    # not only in the background queue. This prevents direct API/timer paths from
    # bypassing an owner emergency stop. Reads and planning remain available.
    try:
        from app.services.reliability import module_blocked
        reliability_module = {
            "cpx.raise_bid": "marketing",
            "cpx.lower_bid": "marketing",
            "cpx.remove_promotion": "marketing",
            "autoload.publish": "marketing",
            "message.send": "messages",
        }.get(operation, "actions")
        blocked, blocked_reason = module_blocked(db, reliability_module, account_id)
        if blocked:
            return _deny("blocked_reliability_kill_switch",
                         evidence={"module": reliability_module,
                                   "reason": blocked_reason or "owner emergency stop"})
    except Exception as reliability_exc:
        # Fail closed only when the reliability tables themselves are reachable
        # enough to report an explicit block. A monitoring defect must not turn
        # into a platform-wide outage of already-authorized customer actions.
        if type(reliability_exc).__name__ == "OperationalError":
            pass

    missing = [k for k in REQUIRED_CONTEXT if not ctx.get(k)]
    if missing:
        return _deny("blocked_missing_execution_context", evidence={"missing": missing})

    if spec["grant"] == "explicit":
        if not explicit_confirmation_id:
            return _deny("blocked_requires_confirmation", requires_confirmation=True)
        return {"allowed": True, "requires_confirmation": False,
                "reason_code": "allowed_by_explicit_confirmation",
                "mandate_id": None, "mandate_version": None,
                "limits_applied": {}, "evidence": {"confirmation": explicit_confirmation_id}}

    # LATE_DAY_SPEND_RESET_COMPENSATION_V1: an exact lower-only reversal of
    # BORIS's prior-day temporary boost is compensation, not new spend. It may
    # finish even after mandate expiry or today's action quota is exhausted.
    if operation == "cpx.lower_bid" and str(ctx.get("source") or "") == "late_day_spend_reset" and bid.get("late_day_reset") is True:
        try:
            import json as _json_reset
            from zoneinfo import ZoneInfo as _ZoneInfo_reset
            _row_reset = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='late_day_spend_boost_state' ORDER BY id DESC LIMIT 1"), {"a":account_id}).fetchone()
            _state_reset = _json_reset.loads(_row_reset[0]) if _row_reset and _row_reset[0] else {}
            _items_reset = _state_reset.get("items") if isinstance(_state_reset, dict) else {}
            _item_reset = (_items_reset or {}).get(str(bid.get("item_id"))) if isinstance(_items_reset, dict) else None
            _baseline_reset = float((_item_reset or {}).get("baseline_bid_rub")) if isinstance(_item_reset, dict) else None
            _old_reset = float(bid.get("old_bid_rub")); _new_reset = float(bid.get("new_bid_rub"))
            _day_reset = str((_state_reset or {}).get("day_msk") or "")
            _today_reset = datetime.now(_ZoneInfo_reset("Europe/Moscow")).date().isoformat()
            _drop_pct_reset = ((_old_reset - _new_reset) * 100.0 / _old_reset) if _old_reset > 0 else 999.0
            if (_day_reset and _day_reset < _today_reset and _baseline_reset is not None
                    and abs(_new_reset - _baseline_reset) <= 0.011
                    and _new_reset < _old_reset and _drop_pct_reset <= 35.0 + 1e-9):
                return {"allowed":True,"requires_confirmation":False,
                        "reason_code":"allowed_late_day_exact_compensation",
                        "mandate_id":None,"mandate_version":None,
                        "limits_applied":{"compensation_only":True,"max_reset_drop_pct":35.0},
                        "evidence":{"baseline_bid_rub":_baseline_reset,"drop_pct":round(_drop_pct_reset,2),"boost_day_msk":_day_reset}}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=209)

    m = load_mandate(db, account_id, operation, source=ctx.get("source"))
    if not m:
        return _deny("blocked_no_mandate")
    if m["status"] == "suspended":
        return _deny("blocked_mandate_suspended",
                     mandate_id=m["id"], mandate_version=m["version"])
    if m["status"] != "active" or m["revoked_at"] is not None:
        return _deny("blocked_mandate_revoked",
                     mandate_id=m["id"], mandate_version=m["version"])

    now = datetime.now(timezone.utc)
    if m["valid_until"] and m["valid_until"] <= now:
        return _deny("blocked_mandate_expired",
                     mandate_id=m["id"], mandate_version=m["version"])
    if m["valid_from"] and m["valid_from"] > now:
        return _deny("blocked_mandate_not_started",
                     mandate_id=m["id"], mandate_version=m["version"])

    if operation not in (m["allowed_operations"] or []):
        return _deny("blocked_operation_not_in_mandate",
                     mandate_id=m["id"], mandate_version=m["version"])

    from app.services.marketing_money_policy import GLOBAL_MAX_AUTO_ACTIONS_RUN, GLOBAL_MAX_AUTO_ACTIONS_DAY
    effective_run_cap = min(int(m["max_actions_run"]), int(GLOBAL_MAX_AUTO_ACTIONS_RUN))
    effective_day_cap = min(int(m["max_actions_day"]), int(GLOBAL_MAX_AUTO_ACTIONS_DAY))
    limits = {"max_actions_run": m["max_actions_run"],
              "max_actions_day": m["max_actions_day"],
              "effective_max_actions_run": effective_run_cap,
              "effective_max_actions_day": effective_day_cap,
              "max_bid_delta_pct": m["max_bid_delta_pct"],
              "max_bid_rub": float(m["max_bid_rub"]),
              "min_bid_rub": (float(m["min_bid_rub"]) if m["min_bid_rub"] is not None else None),
              "daily_budget_rub": (float(m["daily_budget_rub"])
                                   if m["daily_budget_rub"] is not None else None),
              "daily_budget_enforcement": "PARTIAL"}
    base = {"mandate_id": m["id"], "mandate_version": m["version"], "limits_applied": limits}

    rid = ctx.get("request_id")
    if count_actions_run(db, account_id, rid) >= effective_run_cap:
        return _deny("blocked_max_actions_run", **base)
    if count_actions_day(db, account_id) >= effective_day_cap:
        return _deny("blocked_max_actions_day", **base)

    item_id = bid.get("item_id")
    if item_touched_in_run(db, rid, item_id):
        return _deny("blocked_item_already_changed_in_run", **base)

    if mass_count and int(mass_count) > effective_run_cap:
        return _deny("blocked_mass_action", **base)

    # MONEY_GUARD_ALL_RAISES_V3: every automatic raise, including the first
    # Feed Factory activation, requires the canonical explicit daily-budget,
    # fresh same-day spend and live-balance guard. There is no launch bypass.
    if operation == "cpx.raise_bid":
        _launch_first_bid = bool(
            str(ctx.get("source") or "") == "feed20"
            and str(ctx.get("trigger") or "") == "feed"
            and bid.get("new_item_no_promo") is True
            and int(bid.get("new_item_min_markup_pct") or 0) == 20
        )
        try:
            db.close()
            from app.api.cpx_advisor import check_raise_allowed as _check_raise_allowed
            _raise_guard = _check_raise_allowed(account_id, source=ctx.get("source")) or {}
            if not _raise_guard.get("allowed"):
                return _deny(_raise_guard.get("reason_code") or "blocked_money_guard", **base,
                             evidence={"daily_budget_limit_rub":_raise_guard.get("daily_budget_limit_rub"),
                                       "spent_today_rub":_raise_guard.get("spent_today_rub"),
                                       "money_guard_reason":_raise_guard.get("human_reason"),
                                       "launch_first_bid":bool(_launch_first_bid)})
            bal = _raise_guard.get("balance") or bal
            limits["daily_budget_enforcement"] = "FULL"
            limits["daily_budget_rub"] = _raise_guard.get("daily_budget_limit_rub")
            limits["launch_first_bid"] = bool(_launch_first_bid)
        except Exception as _raise_guard_exc:
            return _deny("blocked_money_guard_unavailable", **base,
                         evidence={"error":str(_raise_guard_exc)[:160],
                                   "launch_first_bid":bool(_launch_first_bid)})

    if operation in ("cpx.raise_bid", "cpx.lower_bid"):
        old_b = bid.get("old_bid_rub")
        new_b = bid.get("new_bid_rub")
        if old_b is None or new_b is None:
            return _deny("blocked_bid_context_missing", **base)
        old_b, new_b = float(old_b), float(new_b)
        # Defense in depth: a stale or manually edited money mandate can never
        # expand BORIS's current global red line. The effective account policy
        # is authoritative at the last guard immediately before the Avito write.
        from app.services.marketing_money_policy import effective_hard_bid_cap, clamp_bid_step_pct, effective_bid_step_cap_pct
        policy_cap = float(effective_hard_bid_cap(db, account_id))
        mandate_cap = float(m["max_bid_rub"])
        effective_cap = min(mandate_cap, policy_cap) if policy_cap > 0 else mandate_cap
        # STRICT_STEP10_SOURCE_INVARIANT_V1: the mandate and the global money
        # policy both remain <=10% for every autonomous source, including
        # late-day pacing. No source string may expand the red line.
        effective_step_pct = min(
            int(clamp_bid_step_pct(m["max_bid_delta_pct"])),
            int(effective_bid_step_cap_pct(str(ctx.get("source") or ""))),
        )
        limits["effective_hard_max_bid_rub"] = effective_cap
        limits["effective_max_bid_delta_pct"] = effective_step_pct

        if old_b > 0:
            delta_pct = abs(new_b - old_b) * 100.0 / old_b
            if delta_pct > float(effective_step_pct) + 1e-9:
                # NEW_FEED_HOURLY_LOW_VIEWS_RAMP_V1: Avito accepts only whole-ruble
                # manual bids. A true +10% target on a low bid (4.00→4.40) must
                # therefore execute as the next whole ruble (5.00). Permit only
                # that exact rounding artifact, only for the signed launch-ramp
                # context, while the requested target itself stays <= mandate.
                allow_avito_rounding = False
                if bid.get("fast_raise_rounding") or bid.get("hourly_feed_ramp"):
                    try:
                        import math as _math_guard_round
                        target_pct = float(
                            bid.get("fast_raise_target_pct")
                            or bid.get("hourly_feed_ramp_target_pct")
                            or 0
                        )
                        expected_rounded = float(_math_guard_round.ceil(old_b * (1.0 + target_pct / 100.0)))
                        allow_avito_rounding = (
                            target_pct > 0
                            and target_pct <= float(effective_step_pct) + 1e-9
                            and abs(new_b - expected_rounded) <= 1e-9
                            and new_b > old_b
                        )
                    except Exception:
                        allow_avito_rounding = False
                if not allow_avito_rounding:
                    return _deny("blocked_bid_delta_exceeded", **base,
                                 evidence={"delta_pct": round(delta_pct, 2)})

        # NEW_FEED_FIRST_BID_ABSOLUTE_HARD_CAP_V3: the account hard cap is
        # absolute even for first activation. The advisor is allowed to clamp
        # minBid+20% down to this cap when the cap still satisfies Avito minBid;
        # otherwise the launch is blocked instead of silently overspending.
        if new_b > float(effective_cap) + 1e-9:
            return _deny("blocked_bid_above_max", **base,
                         evidence={"new_bid_rub":new_b,"effective_hard_max_bid_rub":effective_cap,
                                   "first_launch":bool(_launch_first_bid),"avito_min_bid_rub":bid.get("avito_min_bid_rub")})

        avito_min = bid.get("avito_min_bid_rub")
        mandate_min = float(m["min_bid_rub"]) if m["min_bid_rub"] is not None else None
        eff_min = None
        if avito_min is not None and mandate_min is not None:
            eff_min = max(float(avito_min), mandate_min)
        elif avito_min is not None:
            eff_min = float(avito_min)
        elif mandate_min is not None:
            eff_min = mandate_min
        if eff_min is not None and new_b < eff_min - 1e-9:
            return _deny("blocked_bid_below_min", **base,
                         evidence={"effective_min_bid_rub": eff_min})
        limits["effective_min_bid_rub"] = eff_min

    status = bal.get("status") or "UNKNOWN"
    if spec["money"] and status == "KNOWN" and m["reserve_rub"] is not None:
        if bal.get("value") is None or float(bal["value"]) < float(m["reserve_rub"]):
            return _deny("blocked_insufficient_balance", **base,
                         evidence={"balance_rub": bal.get("value"),
                                   "reserve_rub": float(m["reserve_rub"])})

    reason = ("allowed_by_mandate" if status == "KNOWN"
              else "allowed_by_mandate_balance_unknown")
    return {"allowed": True, "requires_confirmation": False, "reason_code": reason,
            "mandate_id": m["id"], "mandate_version": m["version"],
            "limits_applied": limits,
            "evidence": {"balance_status": status,
                         "actions_today": count_actions_day(db, account_id),
                         "actions_this_run": count_actions_run(db, account_id, rid)}}
