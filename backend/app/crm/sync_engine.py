from __future__ import annotations

import traceback

from datetime import datetime
from sqlalchemy import text

from .db import SessionLocal
from .external import (
    connector_for,
    ExternalCRMError,
)


def external_mapping(
    db,
    owner: int,
    provider: str,
    entity_type: str,
    boris_entity_id: int,
):

    return (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_external_entities

                WHERE owner_user_id=:owner
                  AND provider=:provider
                  AND entity_type=:entity_type
                  AND boris_entity_id=:entity_id
            """),
            {
                "owner": owner,
                "provider": provider,
                "entity_type": entity_type,
                "entity_id": boris_entity_id,
            },
        )
        .mappings()
        .first()
    )


def save_mapping(
    db,
    owner: int,
    provider: str,
    entity_type: str,
    boris_entity_id: int,
    external_entity_id: str,
):

    db.execute(
        text("""
            INSERT INTO boris_crm_external_entities (
                owner_user_id,
                provider,
                entity_type,
                boris_entity_id,
                external_entity_id
            )

            VALUES (
                :owner,
                :provider,
                :entity_type,
                :boris_id,
                :external_id
            )

            ON CONFLICT (
                owner_user_id,
                provider,
                entity_type,
                boris_entity_id
            )

            DO UPDATE SET
                external_entity_id=
                    EXCLUDED.external_entity_id,
                updated_at=NOW()
        """),
        {
            "owner": owner,
            "provider": provider,
            "entity_type": entity_type,
            "boris_id": boris_entity_id,
            "external_id": str(
                external_entity_id
            ),
        },
    )


def stage_mapping(
    db,
    owner: int,
    provider: str,
    boris_stage_id: int,
):

    return (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_stage_map

                WHERE owner_user_id=:owner
                  AND provider=:provider
                  AND boris_stage_id=:stage_id
            """),
            {
                "owner": owner,
                "provider": provider,
                "stage_id": boris_stage_id,
            },
        )
        .mappings()
        .first()
    )


def load_contact(
    db,
    owner: int,
    entity_id: int,
):

    return (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_contacts

                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "id": entity_id,
                "owner": owner,
            },
        )
        .mappings()
        .first()
    )


def load_deal(
    db,
    owner: int,
    entity_id: int,
):

    return (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_deals

                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "id": entity_id,
                "owner": owner,
            },
        )
        .mappings()
        .first()
    )


def ensure_external_contact(
    db,
    connector,
    owner: int,
    provider: str,
    contact_id: int,
):

    mapping = external_mapping(
        db,
        owner,
        provider,
        "contact",
        contact_id,
    )

    contact = load_contact(
        db,
        owner,
        contact_id,
    )

    if not contact:
        raise ExternalCRMError(
            "BORIS_CONTACT_NOT_FOUND"
        )

    if mapping:

        connector.update_contact(
            mapping[
                "external_entity_id"
            ],
            dict(contact),
        )

        return str(
            mapping[
                "external_entity_id"
            ]
        )

    external_id = (
        connector.create_contact(
            dict(contact)
        )
    )

    save_mapping(
        db,
        owner,
        provider,
        "contact",
        contact_id,
        external_id,
    )

    return str(
        external_id
    )


def sync_contact_event(
    db,
    connector,
    owner: int,
    provider: str,
    entity_id: int,
):

    ensure_external_contact(
        db,
        connector,
        owner,
        provider,
        entity_id,
    )


def sync_deal_event(
    db,
    connector,
    owner: int,
    provider: str,
    entity_id: int,
):

    deal = load_deal(
        db,
        owner,
        entity_id,
    )

    if not deal:
        raise ExternalCRMError(
            "BORIS_DEAL_NOT_FOUND"
        )

    stage = stage_mapping(
        db,
        owner,
        provider,
        deal[
            "stage_id"
        ],
    )

    if not stage:
        raise ExternalCRMError(
            "STAGE_MAPPING_REQUIRED:"
            + str(
                deal[
                    "stage_id"
                ]
            )
        )

    mapping = external_mapping(
        db,
        owner,
        provider,
        "deal",
        entity_id,
    )

    if mapping:

        connector.update_deal(
            mapping[
                "external_entity_id"
            ],
            dict(deal),
            stage[
                "external_pipeline_id"
            ],
            stage[
                "external_stage_id"
            ],
        )

        external_deal_id = str(
            mapping[
                "external_entity_id"
            ]
        )

    else:

        external_deal_id = (
            connector.create_deal(
                dict(deal),
                stage[
                    "external_pipeline_id"
                ],
                stage[
                    "external_stage_id"
                ],
            )
        )

        save_mapping(
            db,
            owner,
            provider,
            "deal",
            entity_id,
            external_deal_id,
        )


    if deal.get(
        "contact_id"
    ):

        external_contact_id = (
            ensure_external_contact(
                db,
                connector,
                owner,
                provider,
                int(
                    deal[
                        "contact_id"
                    ]
                ),
            )
        )

        connector.link_contact(
            external_deal_id,
            external_contact_id,
        )


def process_one(
    db,
    row: dict,
):

    owner = int(
        row[
            "owner_user_id"
        ]
    )

    provider = row[
        "provider"
    ]

    connector = connector_for(
        provider,
        db,
        owner,
    )

    if row[
        "entity_type"
    ] == "contact":

        sync_contact_event(
            db,
            connector,
            owner,
            provider,
            int(
                row[
                    "entity_id"
                ]
            ),
        )

    elif row[
        "entity_type"
    ] == "deal":

        sync_deal_event(
            db,
            connector,
            owner,
            provider,
            int(
                row[
                    "entity_id"
                ]
            ),
        )

    else:

        raise ExternalCRMError(
            "UNSUPPORTED_ENTITY:"
            + str(
                row[
                    "entity_type"
                ]
            )
        )


def claim_batch(
    db,
    limit: int = 25,
):

    rows = (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_sync_outbox

                WHERE status IN (
                    'pending',
                    'retry'
                )

                  AND (
                    next_attempt_at IS NULL
                    OR next_attempt_at <= NOW()
                  )

                ORDER BY id

                FOR UPDATE SKIP LOCKED

                LIMIT :limit
            """),
            {
                "limit": limit
            },
        )
        .mappings()
        .all()
    )

    for row in rows:

        db.execute(
            text("""
                UPDATE boris_crm_sync_outbox

                SET
                    status='processing',
                    attempts=attempts+1

                WHERE id=:id
            """),
            {
                "id":
                    row["id"]
            },
        )

    db.commit()

    return [
        dict(x)
        for x in rows
    ]


def finish(
    db,
    row_id: int,
):

    db.execute(
        text("""
            UPDATE boris_crm_sync_outbox

            SET
                status='done',
                completed_at=NOW()

            WHERE id=:id
        """),
        {
            "id": row_id
        },
    )


def fail(
    db,
    row: dict,
    error: Exception,
):

    attempts = int(
        row.get(
            "attempts",
            0,
        )
    ) + 1

    delay = min(
        3600,
        max(
            30,
            2 ** min(
                attempts,
                10,
            )
        ),
    )

    status = (
        "failed"
        if attempts >= 10
        else "retry"
    )

    db.execute(
        text("""
            UPDATE boris_crm_sync_outbox

            SET
                status=:status,

                payload_json=
                    payload_json
                    || CAST(
                        :error_json
                        AS jsonb
                    ),

                next_attempt_at=
                    NOW()
                    + (
                        :delay
                        * INTERVAL '1 second'
                    )

            WHERE id=:id
        """),
        {
            "status": status,
            "delay": delay,
            "id": row["id"],
            "error_json":
                (
                    '{"last_error":'
                    + __import__(
                        "json"
                    ).dumps(
                        str(error)[
                            :1500
                        ]
                    )
                    + "}"
                ),
        },
    )


def run_once():

    # Reuse the existing CRM sync worker for Avito Calltracking ingestion.
    # Exactly one connected account is checked per worker iteration; no second daemon.
    try:
        from .calltracking_sync import sync_one_due_account
        ct = sync_one_due_account()
        if ct.get("status") == "ok" and ct.get("created"):
            print("CALLTRACKING_CRM_SYNC", ct.get("account_id"), "created=", ct.get("created"), flush=True)
    except Exception as exc:
        print("CALLTRACKING_CRM_SYNC_FAIL", repr(exc)[:500], flush=True)
    try:
        from .calltracking_sync import send_due_client_agreement_reminders
        rr = send_due_client_agreement_reminders()
        if rr.get("sent"):
            print("CALL_AGREEMENT_CLIENT_REMINDER sent=", rr.get("sent"), flush=True)
    except Exception as exc:
        print("CALL_AGREEMENT_CLIENT_REMINDER_FAIL", repr(exc)[:500], flush=True)

    db = SessionLocal()

    try:

        rows = claim_batch(
            db,
            25,
        )

    finally:

        db.close()


    for row in rows:

        db = SessionLocal()

        try:

            process_one(
                db,
                row,
            )

            finish(
                db,
                row["id"],
            )

            db.commit()

            print(
                "SYNC_OK",
                row["provider"],
                row["entity_type"],
                row["entity_id"],
                flush=True,
            )

        except Exception as exc:

            db.rollback()

            try:

                fail(
                    db,
                    row,
                    exc,
                )

                db.commit()

            except Exception:
                db.rollback()

            print(
                "SYNC_FAIL",
                row["id"],
                repr(exc)[:500],
                flush=True,
            )

        finally:

            db.close()


if __name__ == "__main__":
    run_once()
