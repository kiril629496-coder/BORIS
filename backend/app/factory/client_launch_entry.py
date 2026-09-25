"""
BORIS New Client Launch Entry.

Purpose:
- assess a real account
- run existing Factory pipeline
- return one human-readable outcome

NO external effects:
- no client messages
- no publishing
- no money
- no promotion
"""

from __future__ import annotations

from app.factory.launch_action_engine import bootstrap

from sqlalchemy import text


def get_account(db, account_id):
    return db.execute(
        text("""
            SELECT *
            FROM accounts
            WHERE account_id=:account_id
        """),
        {"account_id":account_id},
    ).mappings().first()


def get_provisioning(db, account_id):
    return db.execute(
        text("""
            SELECT *
            FROM factory_provisioning_state
            WHERE account_id=:account_id
        """),
        {"account_id":account_id},
    ).mappings().first()


def get_supervisor(db, account_id):
    return db.execute(
        text("""
            SELECT *
            FROM factory_supervisor_state
            WHERE account_id=:account_id
        """),
        {"account_id":account_id},
    ).mappings().first()


def get_missing_facts(db, account_id):
    return db.execute(
        text("""
            SELECT
                field,
                fact_kind,
                question_text,
                reason
            FROM factory_missing_fact_state
            WHERE account_id=:account_id
              AND status='WAITING_FACT'
            ORDER BY field
        """),
        {"account_id":account_id},
    ).mappings().all()


def get_owner_attention(db, account_id):
    return db.execute(
        text("""
            SELECT
                issue_type,
                priority,
                title,
                detail
            FROM factory_attention_state
            WHERE account_id=:account_id
              AND status='OPEN'
              AND route='OWNER_REQUIRED'
            ORDER BY priority DESC
        """),
        {"account_id":account_id},
    ).mappings().all()


def _classify_base_v1(db, account_id):
    account=get_account(db,account_id)

    if not account:
        return {
            "status":"BLOCKED",
            "reason":"ACCOUNT_NOT_FOUND",
            "human_message":
                "Клиент не найден в BORIS.",
        }

    provisioning=get_provisioning(
        db,
        account_id,
    )

    supervisor=get_supervisor(
        db,
        account_id,
    )

    facts=get_missing_facts(
        db,
        account_id,
    )

    owner=get_owner_attention(
        db,
        account_id,
    )

    if provisioning is None:
        # BORIS_BOOTSTRAP_BRIDGE_V2
        # A real registered account has no canonical Factory state yet.
        # Use the existing internal Factory bootstrap; do not duplicate
        # provisioning logic here.
        bootstrap_result = bootstrap(account_id)

        provisioning = get_provisioning(
            db,
            account_id,
        )

        if provisioning is None:
            return {
                "status":"BLOCKED",
                "reason":"FACTORY_BOOTSTRAP_INCOMPLETE",
                "human_message":
                    "Клиент создан, но Factory bootstrap не сформировал состояние.",
                "bootstrap":
                    bootstrap_result,
            }

    pstatus=provisioning.get(
        "provisioning_status"
    )

    if pstatus in {
        "BLOCKED_OWNER",
        "BLOCKED_PRODUCTS",
        "BLOCKED_PLATFORM",
        "BLOCKED_CORE",
    }:
        return {
            "status":"BLOCKED",
            "reason":pstatus,
            "human_message":
                "Клиент пока не готов к запуску: %s."
                % pstatus,
            "provisioning":
                dict(provisioning),
        }

    if facts:
        return {
            "status":"NEEDS_INPUT",
            "reason":"MISSING_FACTS",
            "human_message":
                "BORISу не хватает %d подтверждённых фактов."
                % len(facts),
            "questions":[
                dict(x)
                for x in facts
            ],
        }

    if owner:
        return {
            "status":"NEEDS_INPUT",
            "reason":"OWNER_REQUIRED",
            "human_message":
                "Нужно решение владельца по %d пунктам."
                % len(owner),
            "owner_items":[
                dict(x)
                for x in owner
            ],
        }

    if supervisor:
        status=supervisor.get(
            "operational_status"
        )

        if status=="HEALTHY":
            return {
                "status":"READY",
                "reason":"HEALTHY",
                "human_message":
                    "Клиент технически готов к работе.",
                "supervisor":
                    dict(supervisor),
            }

        return {
            "status":"NEEDS_INPUT",
            "reason":status,
            "human_message":
                "BORIS завершил внутреннюю настройку, но есть незакрытое состояние: %s."
                % status,
            "supervisor":
                dict(supervisor),
        }

    return {
        "status":"BLOCKED",
        "reason":"NO_SUPERVISOR_STATE",
        "human_message":
            "Factory отработала не полностью: нет Supervisor state.",
    }

# === BORIS_CLIENT_CAMPAIGN_AUTOPREPARE_BEGIN ===

# Safe completion bridge.
# Existing launch remains authoritative.
# This bridge may create only the INTERNAL campaign workspace.
import inspect as _boris_campaign_inspect

from app.db.session import SessionLocal as _BorisCampaignSessionLocal
from app.factory.client_completion import prepare_client as _boris_prepare_client


_BORIS_CAMPAIGN_ORIGINAL = _classify_base_v1


def _boris_extract_launch_account_id(args, kwargs):
    try:
        sig = _boris_campaign_inspect.signature(
            _BORIS_CAMPAIGN_ORIGINAL
        )

        bound = sig.bind_partial(
            *args,
            **kwargs,
        )

        values = bound.arguments

    except Exception:
        values = dict(
            kwargs
        )

    for key in (
        "account_id",
        "account",
    ):
        value = values.get(
            key
        )

        if isinstance(
            value,
            str,
        ) and value.strip():
            return value.strip()

        if isinstance(
            value,
            dict,
        ):
            candidate = value.get(
                "account_id"
            )

            if candidate:
                return str(
                    candidate
                ).strip()

        candidate = getattr(
            value,
            "account_id",
            None,
        )

        if candidate:
            return str(
                candidate
            ).strip()

    for key in (
        "body",
        "payload",
        "request",
        "data",
    ):
        value = values.get(
            key
        )

        if isinstance(
            value,
            dict,
        ):
            candidate = value.get(
                "account_id"
            )

            if candidate:
                return str(
                    candidate
                ).strip()

        candidate = getattr(
            value,
            "account_id",
            None,
        )

        if candidate:
            return str(
                candidate
            ).strip()

    return None


def _boris_complete_client_after_launch(
    account_id,
    result,
):
    """
    Best-effort SAFE internal completion.

    Never marks an externally incomplete client ready:
    prepare_client still requires Factory/TG/etc.
    """

    if not account_id:
        return result

    db = _BorisCampaignSessionLocal()

    try:
        completion = _boris_prepare_client(
            db,
            account_id,
        )

    except Exception as exc:
        db.rollback()

        completion = {
            "account_id":
                account_id,

            "full_client_ready":
                False,

            "completion_error":
                type(exc).__name__
                + ": "
                + str(exc)[:300],
        }

    finally:
        db.close()

    if isinstance(
        result,
        dict,
    ):
        out = dict(
            result
        )

        out[
            "client_completion"
        ] = completion

        return out

    return result


def classify(*args, **kwargs):

    result = _BORIS_CAMPAIGN_ORIGINAL(*args, **kwargs)

    account_id = _boris_extract_launch_account_id(
        args,
        kwargs,
    )

    return _boris_complete_client_after_launch(
        account_id,
        result,
    )


# === BORIS_CLIENT_CAMPAIGN_AUTOPREPARE_END ===
