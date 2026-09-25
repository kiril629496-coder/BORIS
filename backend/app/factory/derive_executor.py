"""FACTORY_F_V1 — execute ONLY safe internal DERIVE operations.

Write scope:
    factory_derived_state
    factory_executor_events

Never writes:
    client_config
    storage
    accounts
    billing
    money_mandates
    cron
    external APIs
"""

import hashlib
import json

from sqlalchemy import text

from app.factory import executors as X


ALLOWED_OPERATIONS = {
    "derive.timezone",
    "derive.business_hours_candidate",
    "derive.city",
    "derive.product_entitlement",
    "derive.knowledge_refs",
    "derive.avito_connection",
    "derive.access_roles",
    "derive.money_policy",
    # FACTORY_I_TONE_EXECUTOR_V1
    "derive.tone_of_voice",
}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def fingerprint(account_id, field, operation, value, evidence):
    raw=canonical({
        "account_id": account_id,
        "field": field,
        "operation": operation,
        "value": value,
        "evidence": evidence,
    }).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def _meaningful_value(value):
    """FACTORY_F_VALUE_PRESENT_V1.

    Presence of an evidence key is not proof of a value.

    Reject:
    - None
    - blank strings
    - empty list/dict/tuple

    Keep valid falsy business values:
    - 0
    - False
    """
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, dict, tuple)):
        return len(value) > 0

    return True


def extract_value(decision):
    """Fail closed: executor never invents or materializes empty evidence."""

    source=decision.get("source_action") or {}
    evidence=source.get("evidence") or {}

    for key in (
        "candidate_value",
        "normalized_value",
        "value",
    ):
        if key not in evidence:
            continue

        value=evidence[key]

        if not _meaningful_value(value):
            continue

        return (
            True,
            value,
            evidence,
        )

    return (
        False,
        None,
        evidence,
    )



def validate_semantics(field, operation, value):
    """FACTORY_F_SEMANTIC_GUARD_V1.

    Validate meaning, not merely presence.
    No guessing and no normalization of unknown values.
    """

    if operation == "derive.business_hours_candidate":
        if not isinstance(value, str):
            return False, "business_hours_not_string"

        import re

        m=re.fullmatch(
            r"([01]\d|2[0-3]):([0-5]\d)-"
            r"([01]\d|2[0-3]):([0-5]\d)",
            value.strip(),
        )

        if not m:
            return False, "business_hours_bad_format"

        h1,m1,h2,m2=map(int,m.groups())

        start=h1*60+m1
        end=h2*60+m2

        # 09:00-09:00 is not an evidence-backed working window.
        # It only means observed min/max collapsed to one point.
        if start == end:
            return False, "business_hours_zero_span"

    if operation == "derive.timezone":
        if not isinstance(value,str) or "/" not in value:
            return False, "timezone_bad_format"

    if operation == "derive.avito_connection":
        if not isinstance(value, str) or not value.startswith("avito:") or len(value) <= len("avito:"):
            return False, "avito_connection_bad_value"

    if operation == "derive.access_roles":
        if value is not True:
            return False, "access_roles_not_proven"

    if operation == "derive.money_policy":
        if field == "automation.money_actions":
            if value is not True:
                return False, "money_mandate_not_active_true"
        elif field == "limits.max_actions_run":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or value > 50:
                return False, "money_max_actions_bad_value"
        elif field == "limits.max_bid_delta_pct":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or value > 25:
                return False, "money_bid_delta_bad_value"
        elif field in ("limits.daily_budget_rub", "kpi.daily_budget_limit_rub"):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                return False, "money_daily_budget_bad_value"
        else:
            return False, "money_policy_field_not_allowed"

    # FACTORY_I_TONE_SEMANTIC_V1
    if operation == "derive.tone_of_voice":
        if not isinstance(value, str):
            return False, "tone_not_string"

        value=value.strip()

        if not value:
            return False, "tone_blank"

        if len(value) > 1000:
            return False, "tone_too_long"

    return True, None


def validate_decision(packet, decision):
    if not packet.get("eligible_zero_touch"):
        return False, "account_not_eligible"

    if packet.get("owner_user_id") is None:
        return False, "owner_missing"

    operation=decision.get("operation")

    if operation not in ALLOWED_OPERATIONS:
        return False, "operation_not_allowed_in_F"

    if not X.structurally_safe_auto(operation):
        return False, "operation_not_structurally_safe"

    if decision.get("policy") != X.AUTO_ALLOWED:
        return False, "decision_not_auto_allowed"

    if decision.get("money"):
        return False, "money_forbidden"

    if decision.get("visible"):
        return False, "visible_forbidden"

    ok,value,evidence=extract_value(decision)

    if not ok:
        return False, "no_derived_value"

    semantic_ok, semantic_reason = validate_semantics(
        decision.get("field"),
        operation,
        value,
    )

    if not semantic_ok:
        return False, semantic_reason

    return True, None


def preview(packet, decision):
    valid,reason=validate_decision(
        packet,
        decision,
    )

    if not valid:
        return {
            "result": "SKIP",
            "reason": reason,
            "account_id": packet["account_id"],
            "field": decision.get("field"),
            "operation": decision.get("operation"),
        }

    ok,value,evidence=extract_value(
        decision
    )

    fp=fingerprint(
        packet["account_id"],
        decision["field"],
        decision["operation"],
        value,
        evidence,
    )

    return {
        "result": "READY",
        "account_id": packet["account_id"],
        "field": decision["field"],
        "operation": decision["operation"],
        "value": value,
        "evidence": evidence,
        "source_fingerprint": fp,
    }


def apply_one(db, packet, decision):
    item=preview(
        packet,
        decision,
    )

    if item["result"] != "READY":
        return item

    existing=db.execute(text("""
        SELECT
            value,
            source_fingerprint
        FROM factory_derived_state
        WHERE account_id=:a
          AND field=:f
    """), {
        "a":item["account_id"],
        "f":item["field"],
    }).mappings().first()

    if (
        existing
        and existing["source_fingerprint"]
            == item["source_fingerprint"]
    ):
        item["result"]="UNCHANGED"
        return item

    before=(
        existing["value"]
        if existing
        else None
    )

    db.execute(text("""
        INSERT INTO factory_derived_state (
            account_id,
            field,
            operation,
            value,
            evidence,
            source_fingerprint,
            created_at,
            updated_at
        )
        VALUES (
            :a,
            :f,
            :o,
            CAST(:v AS jsonb),
            CAST(:e AS jsonb),
            :fp,
            now(),
            now()
        )
        ON CONFLICT(account_id,field)
        DO UPDATE SET
            operation=EXCLUDED.operation,
            value=EXCLUDED.value,
            evidence=EXCLUDED.evidence,
            source_fingerprint=EXCLUDED.source_fingerprint,
            updated_at=now()
    """), {
        "a":item["account_id"],
        "f":item["field"],
        "o":item["operation"],
        "v":canonical(item["value"]),
        "e":canonical(item["evidence"]),
        "fp":item["source_fingerprint"],
    })

    db.execute(text("""
        INSERT INTO factory_executor_events (
            account_id,
            field,
            operation,
            event_type,
            before_value,
            after_value,
            source_fingerprint,
            created_at
        )
        VALUES (
            :a,
            :f,
            :o,
            :event,
            CAST(:before AS jsonb),
            CAST(:after AS jsonb),
            :fp,
            now()
        )
    """), {
        "a":item["account_id"],
        "f":item["field"],
        "o":item["operation"],
        "event": (
            "CREATE"
            if existing is None
            else "UPDATE"
        ),
        "before": (
            canonical(before)
            if before is not None
            else None
        ),
        "after":canonical(
            item["value"]
        ),
        "fp":item["source_fingerprint"],
    })

    item["result"]=(
        "CREATED"
        if existing is None
        else "UPDATED"
    )

    return item
