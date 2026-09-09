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

        """
        CREATE TABLE IF NOT EXISTS crm_external_connections (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            provider VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'disconnected',
            external_account_id VARCHAR(255),
            external_account_name VARCHAR(500),
            token_ref VARCHAR(500),
            scopes_json TEXT,
            metadata_json TEXT,
            last_sync_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(owner_user_id, provider)
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_external_connections_owner
        ON crm_external_connections(owner_user_id)
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_external_entities (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            provider VARCHAR(32) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            boris_entity_id BIGINT NOT NULL,
            external_entity_id VARCHAR(255) NOT NULL,
            external_parent_id VARCHAR(255),
            external_url TEXT,
            payload_hash VARCHAR(128),
            last_external_updated_at TIMESTAMPTZ,
            last_synced_at TIMESTAMPTZ,
            metadata_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(owner_user_id, provider, entity_type, boris_entity_id),
            UNIQUE(provider, entity_type, external_entity_id)
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_external_entities_lookup
        ON crm_external_entities(
            owner_user_id,
            provider,
            entity_type
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_sync_outbox (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            provider VARCHAR(32) NOT NULL,
            operation VARCHAR(64) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            boris_entity_id BIGINT,
            external_entity_id VARCHAR(255),
            origin VARCHAR(32) NOT NULL DEFAULT 'boris',
            idempotency_key VARCHAR(255) NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash VARCHAR(128) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            locked_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(idempotency_key)
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_sync_outbox_ready
        ON crm_sync_outbox(status, available_at)
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_sync_outbox_owner
        ON crm_sync_outbox(owner_user_id, provider, status)
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_sync_attempts (
            id BIGSERIAL PRIMARY KEY,
            outbox_id BIGINT NOT NULL,
            attempt_no INTEGER NOT NULL,
            status VARCHAR(32) NOT NULL,
            http_status INTEGER,
            response_hash VARCHAR(128),
            error_code VARCHAR(128),
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            UNIQUE(outbox_id, attempt_no)
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_sync_attempts_outbox
        ON crm_sync_attempts(outbox_id)
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_webhook_events (
            id BIGSERIAL PRIMARY KEY,
            provider VARCHAR(32) NOT NULL,
            external_event_id VARCHAR(500) NOT NULL,
            event_type VARCHAR(255),
            payload_hash VARCHAR(128) NOT NULL,
            payload_json TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'received',
            processed_at TIMESTAMPTZ,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(provider, external_event_id)
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_webhook_events_provider_status
        ON crm_webhook_events(provider, status)
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_sync_conflicts (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            provider VARCHAR(32) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            boris_entity_id BIGINT,
            external_entity_id VARCHAR(255),
            conflict_type VARCHAR(64) NOT NULL,
            boris_payload_json TEXT,
            external_payload_json TEXT,
            resolution VARCHAR(64) NOT NULL DEFAULT 'pending',
            resolved_by BIGINT,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_sync_conflicts_pending
        ON crm_sync_conflicts(owner_user_id, resolution)
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_sync_state (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            provider VARCHAR(32) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            cursor_value TEXT,
            last_success_at TIMESTAMPTZ,
            last_attempt_at TIMESTAMPTZ,
            last_error TEXT,
            imported_count INTEGER NOT NULL DEFAULT 0,
            exported_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(owner_user_id, provider, entity_type)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS crm_sync_audit (
            id BIGSERIAL PRIMARY KEY,
            owner_user_id BIGINT,
            provider VARCHAR(32),
            action VARCHAR(128) NOT NULL,
            entity_type VARCHAR(32),
            boris_entity_id BIGINT,
            external_entity_id VARCHAR(255),
            origin VARCHAR(32),
            before_json TEXT,
            after_json TEXT,
            reason TEXT,
            correlation_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_crm_sync_audit_owner_time
        ON crm_sync_audit(owner_user_id, created_at)
        """,

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
