"""READ-ONLY collector: legacy state -> candidate config. Never writes, never invents."""

import json
from app.client_config import schema as S

# legacy vocabulary -> config vocabulary; anything not listed stays unknown
LEGACY_MODE_MAP = {"goal_auto": "autopilot", "autopilot": "autopilot",
                   "advisor": "advisor", "approval": "approval", "off": "off"}


def rows(cu, sql, args=()):
    cu.execute(sql, args)
    cols = [d[0] for d in cu.description]
    return [dict(zip(cols, r)) for r in cu.fetchall()]


def _json(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _deep_find(v, key)
            if r is not None:
                return r
    return None


def gather(cu, account_id):
    """Collect raw legacy state for one account. Read-only."""
    ctx = {"account_id": account_id}
    st = {}
    for r in rows(cu, "SELECT key, value FROM storage WHERE account_id=%s", (account_id,)):
        st[r["key"]] = _json(r["value"])
    ctx["storage"] = st
    ctx["account"] = (rows(cu, "SELECT * FROM accounts WHERE account_id=%s LIMIT 1",
                           (account_id,)) or [{}])[0]
    ctx["bindings"] = rows(cu, "SELECT * FROM ai_bindings WHERE account_id=%s", (account_id,))
    ctx["routes"] = rows(cu, "SELECT * FROM tg_routes WHERE account_id=%s", (account_id,))
    ctx["mandates"] = rows(cu, "SELECT * FROM money_mandates WHERE %s = ANY(account_scope)",
                           (account_id,))
    ctx["react"] = rows(cu, "SELECT * FROM reactivation_settings WHERE account_id=%s", (account_id,))
    ctx["prompts"] = rows(cu, "SELECT * FROM messenger_prompts WHERE account_id=%s", (account_id,))
    ctx["access"] = rows(cu, "SELECT * FROM user_account_access WHERE account_id=%s", (account_id,))
    ctx["facts"] = rows(cu, "SELECT count(*) AS n FROM client_facts WHERE account_id=%s",
                        (account_id,))[0]["n"]
    return ctx


def build_config(ctx):
    """Pure mapping legacy -> config. Unknown stays unknown."""
    cfg = S.empty_config()
    st = ctx.get("storage") or {}
    acc = ctx.get("account") or {}
    kpi = st.get("kpi_settings") or {}
    auto = st.get("autopilot_settings") or {}
    feed = st.get("feed_prefs") or {}
    billing = st.get("billing") or {}
    mand = (ctx.get("mandates") or [None])[0]

    # profile
    cfg["profile"]["city"] = (S.known(feed["city"], "storage:feed_prefs.city")
                              if feed.get("city") else
                              S.unknown("no_source", "storage:feed_prefs.city"))
    cfg["profile"]["timezone"] = S.unknown("not_stored", "no source in DB")
    cfg["profile"]["tone_of_voice"] = S.unknown("not_stored", "no source in DB")

    # products: typed source proves True/False, untyped JSON proves only True
    binds = set(b.get("product") for b in (ctx.get("bindings") or []))
    def product(name, bind_key, billing_key):
        if bind_key and bind_key in binds:
            return S.known(True, "ai_bindings")
        if billing_key and billing.get(billing_key):
            return S.known(True, "storage:billing." + billing_key)
        return S.unknown("no_evidence", "ai_bindings + storage:billing (untyped)")
    cfg["products"]["mop"] = product("mop", "mop", "manager_msgs_purchased")
    cfg["products"]["rop"] = product("rop", "rop", "rop_minutes_purchased")
    cfg["products"]["cpx"] = (S.known(True, "money_mandates") if mand else
                              (S.known(bool(kpi.get("bid_autopilot")), "storage:kpi_settings.bid_autopilot")
                               if "bid_autopilot" in kpi else
                               S.unknown("no_evidence", "money_mandates + kpi_settings")))
    rs = (ctx.get("react") or [None])[0]
    cfg["products"]["reactivation"] = (S.known(bool(rs.get("enabled")), "reactivation_settings")
                                       if rs else S.known(False, "reactivation_settings: no row"))
    cfg["products"]["feed"] = (S.known(True, "storage:feed_items") if st.get("feed_items")
                               else S.unknown("no_evidence", "storage:feed_items"))

    # kpi
    for field, key in (("target_leads_per_day", "target_leads_per_day"),
                       ("max_cost_per_lead_rub", "max_cost_per_lead_rub"),
                       ("daily_budget_limit_rub", "daily_budget_limit_rub")):
        cfg["kpi"][field] = (S.known(kpi[key], "storage:kpi_settings." + key)
                             if kpi.get(key) is not None else
                             S.unknown("no_source", "storage:kpi_settings." + key))
    cfg["kpi"]["business_hours"] = S.unknown("not_stored", "no source in DB")

    # automation and limits
    raw_mode = auto.get("mode")
    if raw_mode is None:
        cfg["automation"]["mode"] = S.unknown("no_source", "storage:autopilot_settings.mode")
    elif raw_mode in LEGACY_MODE_MAP:
        cfg["automation"]["mode"] = S.known(
            LEGACY_MODE_MAP[raw_mode],
            "storage:autopilot_settings.mode (legacy=%s)" % raw_mode)
    else:
        cfg["automation"]["mode"] = S.unknown(
            "unmapped_legacy_value", "autopilot_settings.mode=%s" % raw_mode)
    cfg["automation"]["money_actions"] = (S.known("mandate:%s" % mand.get("id"), "money_mandates")
                                          if mand else
                                          S.known(False, "money_mandates: no row for account"))
    for field, key in (("max_actions_run", "max_actions_run"),
                       ("daily_budget_rub", "daily_budget_rub"),
                       ("max_bid_delta_pct", "max_bid_delta_pct")):
        v = mand.get(key) if mand else None
        if v is None and mand:
            v = _deep_find(mand, key)
        cfg["limits"][field] = (S.known(float(v) if isinstance(v, (int, float)) else v,
                                        "money_mandates." + key)
                                if v is not None else
                                S.unknown("no_mandate_field", "money_mandates." + key))

    # channels
    cfg["channels"]["avito"] = (S.known("account:" + str(ctx.get("account_id")), "accounts.avito_client_id")
                                if acc.get("avito_client_id") else
                                S.unknown("no_credentials", "accounts.avito_client_id"))
    cfg["channels"]["telegram_routes"] = S.known(
        [{"thread_key": r.get("thread_key")} for r in (ctx.get("routes") or [])], "tg_routes")

    # knowledge (refs only)
    cfg["knowledge"]["facts_scope"] = S.known(
        {"kind": "client_facts", "count": ctx.get("facts", 0)}, "client_facts")
    pr = [p for p in (ctx.get("prompts") or []) if p.get("is_active")]
    cfg["knowledge"]["prompt_ref"] = (S.known("messenger_prompts:%s" % pr[0].get("id"), "messenger_prompts")
                                      if pr else S.known(None, "messenger_prompts: no active row"))

    # schedule: policies only, crontab is not a DB source
    for name, gap in (("mop_poll", "cron: messenger_runner --only=<acc>"),
                      ("autopilot", "cron: ads_autopilot.py <acc>"),
                      ("memory_extract", "cron: client_memory_runner extract <acc>")):
        cfg["schedule"][name] = S.unknown("source_is_crontab", gap)
    has_rep = any(r.get("thread_key") == "reports" for r in (ctx.get("routes") or []))
    cfg["schedule"]["reports"] = S.known({"enabled": has_rep, "cadence": "daily"}, "tg_routes")

    # access
    cfg["access"]["roles"] = S.known(
        [{"user_id": a.get("user_id"), "role": a.get("role")} for a in (ctx.get("access") or [])],
        "user_account_access")
    return cfg
