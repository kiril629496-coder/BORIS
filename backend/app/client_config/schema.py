SCHEMA_VERSION = 1
SUPPORTED_OWNER_KINDS = ("user",)
UNKNOWN = "unknown"
KNOWN = "known"
FIELDS = {
    "profile.city": ("profile", "needs_input"),
    "profile.timezone": ("profile", "default"),
    "profile.tone_of_voice": ("mop", "needs_input"),
    "products.mop": ("mop", "default"),
    "products.rop": ("rop", "default"),
    "products.cpx": ("cpx", "default"),
    "products.reactivation": ("react", "default"),
    "products.feed": ("feed", "default"),
    "kpi.target_leads_per_day": ("kpi", "block:kpi"),
    "kpi.max_cost_per_lead_rub": ("kpi", "block:kpi"),
    "kpi.daily_budget_limit_rub": ("kpi", "block:kpi"),
    "kpi.business_hours": ("kpi", "needs_input"),
    "automation.mode": ("automation", "default"),
    "automation.money_actions": ("automation", "block:money"),
    "limits.max_actions_run": ("automation", "block:money"),
    "limits.max_bid_delta_pct": ("automation", "block:money"),
    "limits.daily_budget_rub": ("automation", "block:money"),
    "channels.avito": ("core", "block:all"),
    "channels.telegram_routes": ("reports", "block:reports"),
    "knowledge.facts_scope": ("mop", "needs_input"),
    "knowledge.prompt_ref": ("mop", "needs_input"),
    "schedule.mop_poll": ("mop", "needs_input"),
    "schedule.autopilot": ("cpx", "needs_input"),
    "schedule.memory_extract": ("mop", "needs_input"),
    "schedule.reports": ("reports", "default"),
    "access.roles": ("core", "block:all"),
}
# SAFE_DEFAULTS_V2: a default is allowed only when absence means LESS autonomy.
# products.* removed: "no proof of purchase" is not "product off" - the truth
# about products lives in entitlements.has_entitlement().
# profile.timezone removed: a guessed timezone shifts schedules and client
# facing communication, same class as invented business hours.
PLATFORM_DEFAULTS = {
    "automation.mode": "off",
    "schedule.reports": {"enabled": False, "cadence": "daily"},
}
SAFETY_CAPS = {"limits.max_bid_delta_pct": 25, "limits.max_actions_run": 50}
ALLOWED_CADENCE = ("hourly", "intraday", "daily", "weekly")
ALLOWED_AUTOMATION_MODES = ("off", "advisor", "approval", "autopilot")
SECTIONS = ("profile", "products", "kpi", "automation", "limits",
            "channels", "knowledge", "schedule", "access")

def _plain(v):
    """Coerce DB types (Decimal, datetime, uuid) to JSON-safe values."""
    import datetime, uuid
    from decimal import Decimal
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return dict((str(k), _plain(x)) for k, x in v.items())
    if isinstance(v, (str, int, float)):
        return v
    return str(v)


def known(value, source):
    return {"state": KNOWN, "value": _plain(value), "source": source}

def unknown(reason, source_gap):
    return {"state": UNKNOWN, "reason": reason, "source_gap": source_gap}

def empty_config():
    cfg = {"schema_version": SCHEMA_VERSION}
    for s in SECTIONS:
        cfg[s] = {}
    return cfg

def get_field(config, dotted):
    section, _, name = dotted.partition(".")
    return (config.get(section) or {}).get(name)
