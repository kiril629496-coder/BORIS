"""Config resolver: platform defaults -> owner config -> account override.

Stage B is SHADOW ONLY: nothing in production reads through this module yet.
It computes an effective config and reports which layer every value came from,
so the diff engine can compare it against the legacy state.
"""

from app.client_config import schema as S

LAYER_DEFAULT = "platform_default"
LAYER_OWNER = "owner_config"
LAYER_ACCOUNT = "account_override"


def _flatten(config):
    """{'kpi': {'x': node}} -> {'kpi.x': node}"""
    out = {}
    for section in S.SECTIONS:
        for name, node in (config.get(section) or {}).items():
            out["%s.%s" % (section, name)] = node
    return out


def _usable(node):
    """A node contributes to the effective config only if it is known."""
    return isinstance(node, dict) and node.get("state") == S.KNOWN


def merge(owner_config=None, account_config=None):
    """Pure merge. Returns {dotted: {value, state, source, layer}}."""
    eff = {}
    for dotted, default_value in S.PLATFORM_DEFAULTS.items():
        eff[dotted] = {"state": S.KNOWN, "value": default_value,
                       "source": "platform", "layer": LAYER_DEFAULT}
    for layer, cfg in ((LAYER_OWNER, owner_config), (LAYER_ACCOUNT, account_config)):
        if not cfg:
            continue
        for dotted, node in _flatten(cfg).items():
            if _usable(node):
                eff[dotted] = {"state": S.KNOWN, "value": node.get("value"),
                               "source": node.get("source"), "layer": layer}
            elif dotted not in eff:
                eff[dotted] = {"state": S.UNKNOWN, "reason": node.get("reason"),
                               "source_gap": node.get("source_gap"), "layer": layer}
    for dotted in S.FIELDS:
        if dotted not in eff:
            eff[dotted] = {"state": S.UNKNOWN, "reason": "not_collected",
                           "source_gap": "no layer provides this field", "layer": None}
    return eff


def load_rows(db, owner_kind, owner_id, account_id, statuses=("draft",)):
    """Latest config row per scope. Read-only."""
    from sqlalchemy import text
    sql = ("SELECT config, version, status, account_id FROM client_config"
           " WHERE billing_owner_kind=:k AND billing_owner_id=:o"
           "   AND COALESCE(account_id,'')=COALESCE(:a,'')"
           "   AND status = ANY(:st) ORDER BY version DESC LIMIT 1")
    row = db.execute(text(sql), {"k": owner_kind, "o": owner_id, "a": account_id,
                                 "st": list(statuses)}).fetchone()
    return row


def effective(db, owner_id, account_id, owner_kind="user", statuses=("draft",)):
    """Shadow effective config for one account. Never writes."""
    owner_row = load_rows(db, owner_kind, owner_id, None, statuses)
    acc_row = load_rows(db, owner_kind, owner_id, account_id, statuses)
    owner_cfg = owner_row[0] if owner_row else None
    acc_cfg = acc_row[0] if acc_row else None
    eff = merge(owner_cfg, acc_cfg)
    return {
        "account_id": account_id,
        "owner_user_id": owner_id,
        "effective": eff,
        "layers": {
            "owner_config_version": owner_row[1] if owner_row else None,
            "account_config_version": acc_row[1] if acc_row else None,
        },
        "counts": {
            "known": sum(1 for v in eff.values() if v.get("state") == S.KNOWN),
            "unknown": sum(1 for v in eff.values() if v.get("state") == S.UNKNOWN),
            "from_default": sum(1 for v in eff.values() if v.get("layer") == LAYER_DEFAULT),
            "from_account": sum(1 for v in eff.values() if v.get("layer") == LAYER_ACCOUNT),
        },
    }


def value_of(eff, dotted):
    node = eff.get(dotted) or {}
    return node.get("value") if node.get("state") == S.KNOWN else None
