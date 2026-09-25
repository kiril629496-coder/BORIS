"""
BORIS — client-facing completion layer.

Existing Factory answers:
    "Can BORIS internally operate this account?"

This layer answers:
    "Is the CLIENT operationally ready?"

No publishing.
No money.
No Telegram send.
No AI.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.db.session import engine
from app.services.exception_observability import observe_suppressed


def _insp():
    return sa_inspect(engine)


def _tables():
    return set(_insp().get_table_names())


def _cols(table):
    if table not in _tables():
        return set()

    return {
        c["name"]
        for c in _insp().get_columns(table)
    }


def _count(db, table, condition, params):

    if table not in _tables():
        return 0

    try:
        return int(
            db.execute(
                text(
                    "SELECT COUNT(*) "
                    f"FROM {table} "
                    f"WHERE {condition}"
                ),
                params,
            ).scalar()
            or 0
        )
    except Exception:
        db.rollback()
        return 0


def _account(db, account_id):

    cols = _cols("accounts")

    wanted = [
        c for c in (
            "account_id",
            "name",
            "owner_user_id",
            "telegram_chat_id",
        )
        if c in cols
    ]

    if not wanted:
        return None

    return db.execute(
        text(
            "SELECT "
            + ",".join(wanted)
            + " FROM accounts "
              "WHERE account_id=:a LIMIT 1"
        ),
        {"a": account_id},
    ).mappings().first()


def _factory_state(db, account_id):

    cols = _cols(
        "factory_provisioning_state"
    )

    wanted = [
        c for c in (
            "account_id",
            "eligible_zero_touch",
            "ready_internal",
            "ready_schedule",
            "launch_status",
            "active_schedules",
            "product_count",
            "blockers",
        )
        if c in cols
    ]

    if not wanted:
        return {}

    row = db.execute(
        text(
            "SELECT "
            + ",".join(wanted)
            + " FROM factory_provisioning_state "
              "WHERE account_id=:a LIMIT 1"
        ),
        {"a": account_id},
    ).mappings().first()

    return dict(row or {})


def _workspace(db, account_id):

    return db.execute(
        text(
            """
            SELECT
                account_id,
                campaign_id,
                campaign_name
            FROM factory_client_workspace
            WHERE account_id=:a
            """
        ),
        {"a": account_id},
    ).mappings().first()


def _campaign_exists(db, campaign_id):

    if not campaign_id:
        return False

    if "campaigns" not in _tables():
        return False

    return bool(
        db.execute(
            text(
                """
                SELECT 1
                FROM campaigns
                WHERE id=:id
                LIMIT 1
                """
            ),
            {"id": campaign_id},
        ).scalar()
    )


def ensure_campaign_workspace(
    db,
    account_id,
):

    existing = _workspace(
        db,
        account_id,
    )

    if (
        existing
        and existing.get("campaign_id")
        and _campaign_exists(
            db,
            existing["campaign_id"],
        )
    ):
        return {
            "ready": True,
            "campaign_id":
                int(existing["campaign_id"]),
            "created": False,
        }

    account = _account(
        db,
        account_id,
    )

    if not account:
        return {
            "ready": False,
            "reason": "account_missing",
        }

    from app.services import (
        campaign_service as CS,
    )

    fn = getattr(
        CS,
        "new_campaign",
        None,
    )

    if not callable(fn):
        return {
            "ready": False,
            "reason": "new_campaign_missing",
        }

    sig = inspect.signature(fn)

    kwargs = {
        "name":
            "BORIS Launch — "
            + str(
                account.get("name")
                or account_id
            )
    }

    if (
        "owner_user_id"
        in sig.parameters
    ):
        kwargs["owner_user_id"] = (
            account.get("owner_user_id")
        )

    if (
        "account_id"
        in sig.parameters
    ):
        kwargs["account_id"] = (
            account_id
        )

    try:

        result = fn(
            db,
            **kwargs,
        )

        campaign_id = getattr(
            result,
            "id",
            None,
        )

        if (
            campaign_id is None
            and isinstance(result, dict)
        ):
            campaign_id = (
                result.get("id")
                or result.get("campaign_id")
            )

        if campaign_id is None:
            return {
                "ready": False,
                "reason":
                    "campaign_id_unresolved",
            }

        db.execute(
            text(
                """
                INSERT INTO factory_client_workspace
                (
                    account_id,
                    campaign_id,
                    campaign_name,
                    source,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    :a,
                    :c,
                    :n,
                    'factory_z11',
                    now(),
                    now()
                )

                ON CONFLICT(account_id)
                DO UPDATE SET
                    campaign_id=excluded.campaign_id,
                    campaign_name=excluded.campaign_name,
                    source='factory_z11',
                    updated_at=now()
                """
            ),
            {
                "a": account_id,
                "c": int(campaign_id),
                "n": kwargs["name"],
            },
        )

        db.commit()

        return {
            "ready": True,
            "campaign_id":
                int(campaign_id),
            "created": True,
        }

    except Exception as exc:

        db.rollback()

        return {
            "ready": False,
            "reason":
                type(exc).__name__
                + ": "
                + str(exc)[:250],
        }


def _media_state(
    db,
    account_id,
):

    subsystem = (
        "media_assets"
        in _tables()
    )

    assets = 0

    if (
        subsystem
        and "account_id"
        in _cols("media_assets")
    ):

        assets = _count(
            db,
            "media_assets",
            "account_id=:a "
            "AND deleted_at IS NULL",
            {"a": account_id},
        )

    #
    # Existing media subsystem is enough for launch preparation.
    # Client does not need existing images before intake/content generation.
    #
    return {
        "ready": subsystem,
        "assets": assets,
    }


def _telegram_state(
    db,
    account_id,
):

    routes = 0

    if (
        "tg_routes"
        in _tables()
        and "account_id"
        in _cols("tg_routes")
    ):

        routes = _count(
            db,
            "tg_routes",
            "account_id=:a",
            {"a": account_id},
        )

    account = _account(
        db,
        account_id,
    )

    account_chat = bool(
        account
        and account.get(
            "telegram_chat_id"
        )
    )

    return {
        "ready":
            bool(
                routes
                or account_chat
            ),

        "routes":
            routes,

        "account_chat":
            account_chat,
    }


def _monitor_state(
    db,
    account_id,
):

    code = any(
        os.path.isfile(f)
        for f in (
            "monitor_runner.py",
            "monitor_stage5_runner.py",
            "monitor_source_discovery.py",
        )
    )

    cron = False

    try:

        p = subprocess.run(
            ["crontab", "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )

        c = p.stdout or ""

        cron = bool(
            "monitor_runner.py" in c
            or "monitor_stage5_runner.py" in c
        )

    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=457)

    objects = 0

    for table in (
        "monitor_sources",
        "monitor_rules",
    ):

        if (
            table in _tables()
            and "account_id"
            in _cols(table)
        ):

            objects += _count(
                db,
                table,
                "account_id=:a",
                {"a": account_id},
            )

    return {
        "ready":
            bool(
                code
                and cron
            ),

        "code":
            code,

        "cron":
            cron,

        "objects":
            objects,
    }


def _posting_state(
    db,
    account_id,
):

    code = os.path.isfile(
        "posting_runner.py"
    )

    feed = False

    if (
        "storage"
        in _tables()
        and {
            "account_id",
            "key",
        }.issubset(
            _cols("storage")
        )
    ):

        feed = bool(
            db.execute(
                text(
                    """
                    SELECT 1
                    FROM storage
                    WHERE account_id=:a
                      AND key IN
                      (
                          'feed_prefs',
                          'feed_items'
                      )
                    LIMIT 1
                    """
                ),
                {"a": account_id},
            ).scalar()
        )

    return {
        "ready":
            code,

        "code":
            code,

        "feed_configured":
            feed,

        #
        # Always false in this service.
        #
        "published":
            False,
    }


def evaluate(
    db,
    account_id,
    *,
    ensure_campaign=False,
):

    factory = _factory_state(
        db,
        account_id,
    )

    internal_ready = bool(
        factory.get(
            "eligible_zero_touch"
        )
        or factory.get(
            "ready_internal"
        )
    )

    if ensure_campaign:

        campaign = (
            ensure_campaign_workspace(
                db,
                account_id,
            )
        )

    else:

        ws = _workspace(
            db,
            account_id,
        )

        campaign_id = (
            ws.get("campaign_id")
            if ws
            else None
        )

        campaign = {
            "ready":
                _campaign_exists(
                    db,
                    campaign_id,
                ),

            "campaign_id":
                campaign_id,

            "created":
                False,
        }

    media = _media_state(
        db,
        account_id,
    )

    tg = _telegram_state(
        db,
        account_id,
    )

    monitor = _monitor_state(
        db,
        account_id,
    )

    posting = _posting_state(
        db,
        account_id,
    )

    blockers = []

    if not internal_ready:
        blockers.append(
            "INTERNAL_FACTORY"
        )

    if not campaign["ready"]:
        blockers.append(
            "CAMPAIGN"
        )

    if not media["ready"]:
        blockers.append(
            "MEDIA"
        )

    if not monitor["ready"]:
        blockers.append(
            "MONITOR"
        )

    if not tg["ready"]:
        blockers.append(
            "TELEGRAM"
        )

    if not posting["ready"]:
        blockers.append(
            "POSTING"
        )

    full_ready = bool(
        internal_ready
        and campaign["ready"]
        and media["ready"]
        and monitor["ready"]
        and tg["ready"]
        and posting["ready"]
    )

    return {
        "account_id":
            account_id,

        "internal_ready":
            internal_ready,

        "campaign_ready":
            bool(
                campaign["ready"]
            ),

        "campaign_id":
            campaign.get(
                "campaign_id"
            ),

        "media_ready":
            bool(
                media["ready"]
            ),

        "monitor_ready":
            bool(
                monitor["ready"]
            ),

        "tg_ready":
            bool(
                tg["ready"]
            ),

        "posting_ready":
            bool(
                posting["ready"]
            ),

        #
        # Publishing is separate approval.
        #
        "posting_approved":
            False,

        "needs_approval":
            bool(
                posting["ready"]
            ),

        "full_client_ready":
            full_ready,

        "blockers":
            blockers,

        "evidence": {
            "factory": factory,
            "campaign": campaign,
            "media": media,
            "telegram": tg,
            "monitor": monitor,
            "posting": posting,
        },
    }


def persist(
    db,
    result,
):

    db.execute(
        text(
            """
            INSERT INTO factory_client_completion
            (
                account_id,
                internal_ready,
                campaign_ready,
                campaign_id,
                media_ready,
                monitor_ready,
                tg_ready,
                posting_ready,
                posting_approved,
                full_client_ready,
                needs_approval,
                blockers,
                evidence,
                created_at,
                updated_at
            )
            VALUES
            (
                :account_id,
                :internal_ready,
                :campaign_ready,
                :campaign_id,
                :media_ready,
                :monitor_ready,
                :tg_ready,
                :posting_ready,
                :posting_approved,
                :full_client_ready,
                :needs_approval,
                CAST(:blockers AS jsonb),
                CAST(:evidence AS jsonb),
                now(),
                now()
            )

            ON CONFLICT(account_id)
            DO UPDATE SET
                internal_ready=excluded.internal_ready,
                campaign_ready=excluded.campaign_ready,
                campaign_id=excluded.campaign_id,
                media_ready=excluded.media_ready,
                monitor_ready=excluded.monitor_ready,
                tg_ready=excluded.tg_ready,
                posting_ready=excluded.posting_ready,
                posting_approved=excluded.posting_approved,
                full_client_ready=excluded.full_client_ready,
                needs_approval=excluded.needs_approval,
                blockers=excluded.blockers,
                evidence=excluded.evidence,
                updated_at=now()
            """
        ),
        {
            "account_id":
                result["account_id"],

            "internal_ready":
                result["internal_ready"],

            "campaign_ready":
                result["campaign_ready"],

            "campaign_id":
                result["campaign_id"],

            "media_ready":
                result["media_ready"],

            "monitor_ready":
                result["monitor_ready"],

            "tg_ready":
                result["tg_ready"],

            "posting_ready":
                result["posting_ready"],

            "posting_approved":
                result["posting_approved"],

            "full_client_ready":
                result["full_client_ready"],

            "needs_approval":
                result["needs_approval"],

            "blockers":
                json.dumps(
                    result["blockers"],
                    ensure_ascii=False,
                ),

            "evidence":
                json.dumps(
                    result["evidence"],
                    ensure_ascii=False,
                    default=str,
                ),
        },
    )

    db.commit()


def prepare_client(
    db,
    account_id,
):

    result = evaluate(
        db,
        account_id,
        ensure_campaign=True,
    )

    persist(
        db,
        result,
    )

    return result
