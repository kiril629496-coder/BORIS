"""FACTORY_G_V1 — runtime read-through adapter.

Priority:
1. explicit legacy runtime setting
2. active account Client Config
3. active owner Client Config
4. validated Factory derived state
5. missing

Factory-derived values never overwrite source data.

Runtime policy is stricter than Factory materialization:
- timezone may be used after semantic validation
- business_hours derived from observed messages remains SHADOW
  when evidence says needs_confirmation=true
"""

import json
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.factory import derive_executor as DX


SUPPORTED = {
    "profile.timezone",
    "kpi.business_hours",
}


def _meaningful(value):
    return DX._meaningful_value(value)


def _json_value(raw):
    if raw is None:
        return None

    if isinstance(raw, (dict,list,bool,int,float)):
        return raw

    try:
        return json.loads(raw)
    except Exception:
        return raw


def _node_value(config, field):
    if not isinstance(config, dict):
        return None

    section,name=field.split(".",1)

    node=(config.get(section) or {}).get(name)

    if not isinstance(node,dict):
        return None

    # Only known/confirmed-like nodes participate.
    state=node.get("state")

    if state not in (
        "known",
        "KNOWN",
        "confirmed",
        "CONFIRMED",
    ):
        return None

    value=node.get("value")

    if not _meaningful(value):
        return None

    return {
        "value":value,
        "source":node.get("source"),
    }


def _explicit_runtime(db, account_id, field):
    """Legacy explicit runtime settings, if somebody already configured them."""

    row=db.execute(text("""
        SELECT value
        FROM storage
        WHERE account_id=:a
          AND key='kpi_runtime_settings'
        ORDER BY id DESC
        LIMIT 1
    """), {"a":account_id}).first()

    if not row:
        return None

    raw=_json_value(row[0])

    if not isinstance(raw,dict):
        return None

    key={
        "profile.timezone":"timezone",
        "kpi.business_hours":"business_hours",
    }.get(field)

    if not key or key not in raw:
        return None

    value=raw.get(key)

    if not _meaningful(value):
        return None

    return {
        "value":value,
        "source":"storage:kpi_runtime_settings."+key,
        "layer":"legacy_explicit",
    }


def _client_config_rows(db, account_id):
    """Read active account + owner config without modifying resolver contract."""

    account=db.execute(text("""
        SELECT owner_user_id
        FROM accounts
        WHERE account_id=:a
    """), {"a":account_id}).first()

    if not account:
        return None,None

    owner_id=account[0]

    if owner_id is None:
        return None,None

    account_cfg=db.execute(text("""
        SELECT config
        FROM client_config
        WHERE billing_owner_id=:o
          AND account_id=:a
          AND status='active'
        ORDER BY version DESC
        LIMIT 1
    """), {
        "o":owner_id,
        "a":account_id,
    }).first()

    owner_cfg=db.execute(text("""
        SELECT config
        FROM client_config
        WHERE billing_owner_id=:o
          AND account_id IS NULL
          AND status='active'
        ORDER BY version DESC
        LIMIT 1
    """), {
        "o":owner_id,
    }).first()

    return (
        account_cfg[0] if account_cfg else None,
        owner_cfg[0] if owner_cfg else None,
    )


def _derived(db, account_id, field):
    """FACTORY_H_RUNTIME_ELIGIBILITY_V1.

    Runtime never trusts historical derived state by itself.

    A value is usable only while the account is CURRENTLY:
    - present
    - owned
    - zero-touch eligible

    This protects runtime if eligibility changes after F materialized a value.
    """

    row=db.execute(text("""
        SELECT
            d.value,
            d.evidence,
            d.operation,
            d.source_fingerprint,
            f.eligible_zero_touch,
            f.launch_status,
            a.owner_user_id
        FROM factory_derived_state d
        JOIN factory_launch_state f
          ON f.account_id=d.account_id
        JOIN accounts a
          ON a.account_id=d.account_id
        WHERE d.account_id=:a
          AND d.field=:f
        LIMIT 1
    """), {
        "a":account_id,
        "f":field,
    }).mappings().first()

    if not row:
        return None

    if row["owner_user_id"] is None:
        return {
            "usable":False,
            "value":None,
            "layer":"factory_derived",
            "reason":"owner_missing",
        }

    if not row["eligible_zero_touch"]:
        return {
            "usable":False,
            "value":None,
            "layer":"factory_derived",
            "reason":"account_not_zero_touch_eligible",
        }

    value=row["value"]
    evidence=row["evidence"] or {}
    operation=row["operation"]

    ok,reason=DX.validate_semantics(
        field,
        operation,
        value,
    )

    if not ok:
        return {
            "usable":False,
            "value":None,
            "layer":"factory_derived",
            "reason":"semantic_invalid:"+str(reason),
        }

    if (
        field=="kpi.business_hours"
        and evidence.get("needs_confirmation") is True
    ):
        return {
            "usable":False,
            "value":value,
            "layer":"factory_derived",
            "source":evidence.get("source"),
            "confidence":evidence.get("confidence"),
            "reason":"confirmation_required",
            "shadow_value":value,
        }

    return {
        "usable":True,
        "value":value,
        "layer":"factory_derived",
        "source":evidence.get("source"),
        "confidence":evidence.get("confidence"),
        "fingerprint":row["source_fingerprint"],
    }


def resolve_field(db, account_id, field):
    if field not in SUPPORTED:
        return {
            "status":"blocked",
            "field":field,
            "value":None,
            "layer":None,
            "reason":"field_not_supported",
        }

    explicit=_explicit_runtime(
        db,
        account_id,
        field,
    )

    if explicit:
        return {
            "status":"ok",
            "field":field,
            **explicit,
        }

    try:
        account_cfg,owner_cfg=_client_config_rows(
            db,
            account_id,
        )
    except Exception:
        # Client Config absence/schema issue must not break runtime fallback.
        account_cfg=owner_cfg=None

    node=_node_value(
        account_cfg,
        field,
    )

    if node:
        return {
            "status":"ok",
            "field":field,
            "value":node["value"],
            "source":node["source"],
            "layer":"client_config_account",
        }

    node=_node_value(
        owner_cfg,
        field,
    )

    if node:
        return {
            "status":"ok",
            "field":field,
            "value":node["value"],
            "source":node["source"],
            "layer":"client_config_owner",
        }

    derived=_derived(
        db,
        account_id,
        field,
    )

    if derived:
        if derived.get("usable"):
            return {
                "status":"ok",
                "field":field,
                **{
                    k:v
                    for k,v in derived.items()
                    if k!="usable"
                },
            }

        return {
            "status":"shadow",
            "field":field,
            **{
                k:v
                for k,v in derived.items()
                if k!="usable"
            },
        }

    return {
        "status":"missing",
        "field":field,
        "value":None,
        "layer":None,
        "reason":"no_runtime_source",
    }


def resolve_runtime(db, account_id):
    return {
        field:resolve_field(
            db,
            account_id,
            field,
        )
        for field in sorted(SUPPORTED)
    }


def local_now(db, account_id, fallback_datetime):
    """Return account-local now only when runtime timezone is safely resolved."""

    resolved=resolve_field(
        db,
        account_id,
        "profile.timezone",
    )

    if resolved.get("status")!="ok":
        return fallback_datetime, {
            "used":False,
            "resolution":resolved,
        }

    tzname=resolved.get("value")

    try:
        tz=ZoneInfo(tzname)
    except Exception:
        return fallback_datetime, {
            "used":False,
            "resolution":resolved,
            "reason":"invalid_zoneinfo",
        }

    # Use an aware instant, then convert to client timezone.
    import datetime as _dt

    now_utc=_dt.datetime.now(
        _dt.timezone.utc
    )

    return now_utc.astimezone(tz), {
        "used":True,
        "resolution":resolved,
    }
