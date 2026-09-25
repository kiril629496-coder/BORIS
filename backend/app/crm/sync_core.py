"""
BORIS CRM — Sync Core Foundation.

Stage E.1 deliberately contains NO external CRM writes.

Responsibilities:
    - connection state
    - external entity mapping
    - outbox
    - attempts
    - webhook idempotency
    - conflicts
    - sync state
    - audit

External adapters are expected to use this layer later.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from .db import SessionLocal


SCHEMA_VERSION = "e1"


ALLOWED_PROVIDERS = {
    "amocrm",
    "bitrix24",
}


ALLOWED_ENTITY_TYPES = {
    "contact",
    "company",
    "deal",
    "task",
}


ALLOWED_OUTBOX_OPERATIONS = {
    "upsert_contact",
    "upsert_company",
    "upsert_deal",
    "update_deal_stage",
    "create_task",
}


ALLOWED_ORIGINS = {
    "boris",
    "amocrm",
    "bitrix24",
    "webhook",
    "import",
    "manual",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def payload_hash(value: Any) -> str:
    raw = json_dumps(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def install_sync_core() -> None:
    """
    Idempotent database installation.

    Every statement is safe to run repeatedly.
    """

    db = SessionLocal()

    statements = [

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    ]

    try:
        for statement in statements:
            db.execute(text(statement))

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def connection_status(
    owner_user_id: int,
    provider: str,
) -> dict:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported_provider")

    db = SessionLocal()

    try:
        row = db.execute(
            text(
                """
                SELECT
                    id,
                    provider,
                    status,
                    external_account_id,
                    external_account_name,
                    scopes_json,
                    last_sync_at,
                    last_error,
                    updated_at
                FROM crm_external_connections
                WHERE owner_user_id=:owner
                  AND provider=:provider
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
            },
        ).mappings().first()

        if not row:
            return {
                "provider": provider,
                "status": "disconnected",
                "configured": False,
                "external_writes_enabled": False,
            }

        return {
            "id": row["id"],
            "provider": row["provider"],
            "status": row["status"],
            "configured": True,
            "external_account_id": row["external_account_id"],
            "external_account_name": row["external_account_name"],
            "scopes": (
                json.loads(row["scopes_json"])
                if row["scopes_json"]
                else []
            ),
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
            "updated_at": row["updated_at"],
            "external_writes_enabled": row["status"] == "active",
        }

    finally:
        db.close()


def enqueue(
    *,
    owner_user_id: int,
    provider: str,
    operation: str,
    entity_type: str,
    boris_entity_id: Optional[int],
    payload: dict,
    origin: str = "boris",
    external_entity_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Queue-only operation.

    IMPORTANT:
        This function NEVER performs external HTTP.
    """

    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported_provider")

    if operation not in ALLOWED_OUTBOX_OPERATIONS:
        raise ValueError("unsupported_operation")

    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError("unsupported_entity_type")

    if origin not in ALLOWED_ORIGINS:
        raise ValueError("unsupported_origin")

    body_hash = payload_hash(payload)

    if not idempotency_key:
        idempotency_key = (
            f"{provider}:{operation}:{entity_type}:"
            f"{boris_entity_id}:{body_hash}"
        )

    db = SessionLocal()

    try:

        existing = db.execute(
            text(
                """
                SELECT
                    id,
                    status,
                    attempts
                FROM crm_sync_outbox
                WHERE idempotency_key=:key
                """
            ),
            {"key": idempotency_key},
        ).mappings().first()

        if existing:
            return {
                "queued": False,
                "deduplicated": True,
                "outbox_id": existing["id"],
                "status": existing["status"],
                "attempts": existing["attempts"],
            }

        result = db.execute(
            text(
                """
                INSERT INTO crm_sync_outbox(
                    owner_user_id,
                    provider,
                    operation,
                    entity_type,
                    boris_entity_id,
                    external_entity_id,
                    origin,
                    idempotency_key,
                    payload_json,
                    payload_hash
                )
                VALUES(
                    :owner,
                    :provider,
                    :operation,
                    :entity_type,
                    :boris_id,
                    :external_id,
                    :origin,
                    :key,
                    :payload,
                    :payload_hash
                )
                RETURNING id
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
                "operation": operation,
                "entity_type": entity_type,
                "boris_id": boris_entity_id,
                "external_id": external_entity_id,
                "origin": origin,
                "key": idempotency_key,
                "payload": json_dumps(payload),
                "payload_hash": body_hash,
            },
        )

        outbox_id = result.scalar_one()

        db.execute(
            text(
                """
                INSERT INTO crm_sync_audit(
                    owner_user_id,
                    provider,
                    action,
                    entity_type,
                    boris_entity_id,
                    external_entity_id,
                    origin,
                    after_json,
                    reason
                )
                VALUES(
                    :owner,
                    :provider,
                    'outbox_enqueue',
                    :entity_type,
                    :boris_id,
                    :external_id,
                    :origin,
                    :payload,
                    'queued_without_external_write'
                )
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
                "entity_type": entity_type,
                "boris_id": boris_entity_id,
                "external_id": external_entity_id,
                "origin": origin,
                "payload": json_dumps(payload),
            },
        )

        db.commit()

        return {
            "queued": True,
            "deduplicated": False,
            "outbox_id": outbox_id,
            "status": "pending",
            "external_write": False,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def stats(owner_user_id: int) -> dict:
    db = SessionLocal()

    try:

        outbox = db.execute(
            text(
                """
                SELECT status, COUNT(*) AS n
                FROM crm_sync_outbox
                WHERE owner_user_id=:owner
                GROUP BY status
                ORDER BY status
                """
            ),
            {"owner": owner_user_id},
        ).mappings().all()

        conflicts = db.execute(
            text(
                """
                SELECT resolution, COUNT(*) AS n
                FROM crm_sync_conflicts
                WHERE owner_user_id=:owner
                GROUP BY resolution
                ORDER BY resolution
                """
            ),
            {"owner": owner_user_id},
        ).mappings().all()

        return {
            "outbox": {
                str(x["status"]): int(x["n"])
                for x in outbox
            },
            "conflicts": {
                str(x["resolution"]): int(x["n"])
                for x in conflicts
            },
        }

    finally:
        db.close()
