"""Diff engine: legacy effective state vs Client Config effective state."""

from app.client_config import collector as C
from app.client_config import resolver as R
from app.client_config import schema as S

ABSENT = "__absent__"
SAME = "same"
DIFFERENT = "different"
MISSING_IN_CONFIG = "missing_in_config"
MISSING_IN_LEGACY = "missing_in_legacy"
UNKNOWN = "unknown"


def _side(node):
    if node is None:
        return ("absent", ABSENT, None)
    if node.get("state") == S.KNOWN:
        return ("known", node.get("value"), node.get("source"))
    return ("unknown", ABSENT, node.get("source_gap"))


def compare(legacy_eff, config_eff):
    out = {}
    for dotted in sorted(set(legacy_eff) | set(config_eff) | set(S.FIELDS)):
        l_state, l_value, l_source = _side(legacy_eff.get(dotted))
        c_state, c_value, c_source = _side(config_eff.get(dotted))
        if l_state == "known" and c_state == "known":
            status = SAME if l_value == c_value else DIFFERENT
        elif l_state == "known":
            status = MISSING_IN_CONFIG
        elif c_state == "known":
            status = MISSING_IN_LEGACY
        else:
            status = UNKNOWN
        out[dotted] = {"field": dotted, "status": status,
                       "legacy_state": l_state, "config_state": c_state,
                       "legacy_value": l_value, "config_value": c_value,
                       "legacy_source": l_source, "config_source": c_source}
    return out


def legacy_effective(db, account_id):
    cu = db.connection().connection.cursor()
    ctx = C.gather(cu, account_id)
    cfg = C.build_config(ctx)
    flat = {}
    for section in S.SECTIONS:
        for name, node in (cfg.get(section) or {}).items():
            flat["%s.%s" % (section, name)] = node
    return flat


def diff_account(db, owner_id, account_id):
    legacy = legacy_effective(db, account_id)
    conf = R.effective(db, owner_id, account_id)["effective"]
    rows = compare(legacy, conf)
    counts = {}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"account_id": account_id, "rows": rows, "counts": counts}
