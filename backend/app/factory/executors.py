"""FACTORY_E_V2 — BORIS Factory executor policy registry.

This registry classifies operations only.
It DOES NOT execute them.

Security contract:

AUTO_ALLOWED:
    internal + invisible + reversible + no money

CONFIRM_REQUIRED:
    visible or operationally meaningful change

MANDATE_REQUIRED:
    money / bids / budget / paid autonomy

BLOCKED:
    unknown operation or account not eligible

NOOP:
    nothing has to be done
"""

AUTO_ALLOWED = "AUTO_ALLOWED"
CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
MANDATE_REQUIRED = "MANDATE_REQUIRED"
BLOCKED = "BLOCKED"
NOOP = "NOOP"


REGISTRY = {
    "ask.client": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": True,
        "reversible": True,
    },

    # ---------------------------------------------------------
    # GREEN — internal derivation only
    # ---------------------------------------------------------

    "derive.timezone": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "derive.business_hours_candidate": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "derive.city": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "derive.product_entitlement": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    # FACTORY_EXISTING_PLATFORM_TRUTH_V1
    # These operations verify already-existing production facts. They do not
    # assign credentials, grant access, call Avito, or mutate platform state.
    "derive.avito_connection": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "derive.access_roles": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    # Existing active mandate -> internal normalized policy only.
    # This never creates/extends a mandate and never authorizes a new operation.
    "derive.money_policy": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "derive.knowledge_refs": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },


    # FACTORY_I_TONE_V1
    # Internal style hint only. AUTO only when a concrete existing value
    # is already present in evidence. Never invent tone from niche.
    "derive.tone_of_voice": {
        "policy": AUTO_ALLOWED,
        "risk": "green",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    # ---------------------------------------------------------
    # YELLOW — internal but changes effective operation
    # ---------------------------------------------------------

    "migrate.schedule": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "assign.avito_connection": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "assign.access_roles": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "assign.telegram_route": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": True,
        "reversible": True,
    },

    "assign.product_mode": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": False,
        "reversible": True,
    },

    "assign.report_schedule": {
        "policy": CONFIRM_REQUIRED,
        "risk": "yellow",
        "money": False,
        "visible": True,
        "reversible": True,
    },

    # ---------------------------------------------------------
    # RED — money / autonomous paid behaviour
    # ---------------------------------------------------------

    "money.enable_actions": {
        "policy": MANDATE_REQUIRED,
        "risk": "red",
        "money": True,
        "visible": True,
        "reversible": True,
    },

    "money.change_bid": {
        "policy": MANDATE_REQUIRED,
        "risk": "red",
        "money": True,
        "visible": True,
        "reversible": True,
    },

    "money.change_budget": {
        "policy": MANDATE_REQUIRED,
        "risk": "red",
        "money": True,
        "visible": True,
        "reversible": True,
    },

    # ---------------------------------------------------------
    # RED — client/public visible
    # ---------------------------------------------------------

    "client.send_message": {
        "policy": CONFIRM_REQUIRED,
        "risk": "red",
        "money": False,
        "visible": True,
        "reversible": False,
    },

    "client.publish": {
        "policy": CONFIRM_REQUIRED,
        "risk": "red",
        "money": False,
        "visible": True,
        "reversible": False,
    },
}


def policy_for(operation):
    item = REGISTRY.get(operation)

    if item is None:
        return {
            "operation": operation,
            "policy": BLOCKED,
            "risk": "unknown",
            "money": False,
            "visible": False,
            "reversible": False,
            "reason": "executor_not_registered",
        }

    out = dict(item)
    out["operation"] = operation

    return out


def structurally_safe_auto(operation):
    p = policy_for(operation)

    return bool(
        p["policy"] == AUTO_ALLOWED
        and p["risk"] == "green"
        and not p["money"]
        and not p["visible"]
        and p["reversible"]
    )


def effective_policy(operation, account_eligible):
    """Account gate is applied AFTER operation policy.

    Even a green operation cannot execute automatically
    for an account outside ZERO_TOUCH eligibility.
    """

    p = policy_for(operation)

    if p["policy"] == AUTO_ALLOWED and not account_eligible:
        p = dict(p)
        p["policy"] = BLOCKED
        p["reason"] = "account_not_zero_touch_eligible"

    return p
