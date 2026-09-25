from __future__ import annotations

import hashlib
import json
import secrets
import time

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)

from fastapi.responses import HTMLResponse

from pydantic import BaseModel

from sqlalchemy import text

from app.api.auth import get_current_user

from .db import SessionLocal

from .service import (
    user_id,
    ensure_default_pipeline,
    create_audit,
    crm_owner_user_id,
)

from .vault import (
    load_secret,
    save_secret,
)

from .external import (
    connector_for,
    AmoCRM,
    Bitrix24,
    ExternalCRMError,
)


router = APIRouter(
    prefix="/integration",
    tags=["CRM Integrations"],
)


PROVIDERS = {
    "amocrm",
    "bitrix24",
}


class ConfigureBody(BaseModel):

    provider: str

    client_id: str

    client_secret: str

    external_domain: str = ""

    redirect_uri: str

    mode: str = "bidirectional"


class MapBody(BaseModel):

    boris_stage_id: int

    external_pipeline_id: str

    external_stage_id: str

    external_pipeline_name: str = ""

    external_stage_name: str = ""


class WritesBody(BaseModel):

    enabled: bool


def uid(
    current_user,
) -> int:

    try:
        return user_id(
            current_user
        )

    except Exception as exc:
        raise HTTPException(
            401,
            "CRM_USER_CONTEXT_MISSING",
        ) from exc


def validate_provider(
    provider: str,
):

    if provider not in PROVIDERS:
        raise HTTPException(
            400,
            "UNSUPPORTED_PROVIDER",
        )


def connection(
    db,
    owner: int,
    provider: str,
):

    return (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_connections

                WHERE owner_user_id=:owner
                  AND provider=:provider
            """),
            {
                "owner": owner,
                "provider": provider,
            },
        )
        .mappings()
        .first()
    )


@router.post("/configure")
def configure(
    body: ConfigureBody,
    current_user=Depends(
        get_current_user
    ),
):

    owner = uid(
        current_user
    )

    validate_provider(
        body.provider
    )

    if body.mode not in {
        "boris_master",
        "bidirectional",
        "external_master",
    }:
        raise HTTPException(
            400,
            "INVALID_SYNC_MODE",
        )

    domain = (
        body.external_domain
        .strip()
        .replace(
            "https://",
            "",
        )
        .replace(
            "http://",
            "",
        )
        .strip("/")
    )

    db = SessionLocal()

    try:

        webhook_secret = (
            secrets.token_urlsafe(
                32
            )
        )

        db.execute(
            text("""
                INSERT INTO boris_crm_connections (
                    owner_user_id,
                    provider,
                    mode,
                    status,
                    external_domain,
                    webhook_secret,
                    external_writes_enabled,
                    settings_json
                )

                VALUES (
                    :owner,
                    :provider,
                    :mode,
                    'configured',
                    :domain,
                    :webhook_secret,
                    FALSE,
                    '{}'::jsonb
                )

                ON CONFLICT (
                    owner_user_id,
                    provider
                )

                DO UPDATE SET
                    mode=EXCLUDED.mode,
                    status='configured',
                    external_domain=
                        EXCLUDED.external_domain,
                    webhook_secret=
                        COALESCE(
                            boris_crm_connections.webhook_secret,
                            EXCLUDED.webhook_secret
                        ),
                    external_writes_enabled=FALSE,
                    updated_at=NOW()
            """),
            {
                "owner": owner,
                "provider":
                    body.provider,
                "mode":
                    body.mode,
                "domain":
                    domain or None,
                "webhook_secret":
                    webhook_secret,
            },
        )

        current_secret = load_secret(
            db,
            owner,
            body.provider,
        )

        current_secret.update({
            "client_id":
                body.client_id.strip(),

            "client_secret":
                body.client_secret.strip(),

            "redirect_uri":
                body.redirect_uri.strip(),
        })

        save_secret(
            db,
            owner,
            body.provider,
            current_secret,
        )

        create_audit(
            db,
            owner,
            action=
                "crm_integration_configured",

            entity_type=
                "crm_connection",

            actor_type=
                "user",

            actor_id=
                owner,

            reason=
                body.provider,
        )

        db.commit()

        return {
            "ok": True,
            "provider":
                body.provider,
            "status":
                "configured",
            "external_writes":
                False,
        }

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


@router.get("/status")
def status(
    current_user=Depends(
        get_current_user
    ),
):

    owner = crm_owner_user_id(
        current_user
    )

    db = SessionLocal()

    try:

        rows = (
            db.execute(
                text("""
                    SELECT
                        provider,
                        mode,
                        status,
                        external_domain,
                        external_account_id,
                        external_writes_enabled,
                        last_sync_at,
                        last_error,

                        (
                            SELECT COUNT(*)

                            FROM boris_crm_stage_map sm

                            WHERE sm.owner_user_id=
                                c.owner_user_id

                              AND sm.provider=
                                c.provider
                        ) AS mapped_stages

                    FROM boris_crm_connections c

                    WHERE owner_user_id=:owner

                    ORDER BY provider
                """),
                {
                    "owner": owner
                },
            )
            .mappings()
            .all()
        )

        return [
            dict(x)
            for x in rows
        ]

    finally:

        db.close()


@router.post("/{provider}/oauth/start")
def oauth_start(
    provider: str,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        conn = connection(
            db,
            owner,
            provider,
        )

        if not conn:
            raise HTTPException(
                400,
                "INTEGRATION_NOT_CONFIGURED",
            )

        state = (
            secrets.token_urlsafe(
                40
            )
        )

        db.execute(
            text("""
                INSERT INTO boris_crm_oauth_states (
                    state_token,
                    owner_user_id,
                    provider,
                    expires_at
                )

                VALUES (
                    :state,
                    :owner,
                    :provider,
                    NOW()
                    + INTERVAL '15 minutes'
                )
            """),
            {
                "state":
                    state,
                "owner":
                    owner,
                "provider":
                    provider,
            },
        )

        connector = connector_for(
            provider,
            db,
            owner,
        )

        url = connector.oauth_url(
            state
        )

        db.commit()

        return {
            "ok": True,
            "url": url,
        }

    except HTTPException:

        db.rollback()
        raise

    except Exception as exc:

        db.rollback()

        raise HTTPException(
            400,
            str(exc),
        )

    finally:

        db.close()


def consume_state(
    db,
    state: str,
    provider: str,
):

    row = (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_oauth_states

                WHERE state_token=:state
                  AND provider=:provider
                  AND used_at IS NULL
                  AND expires_at > NOW()

                FOR UPDATE
            """),
            {
                "state":
                    state,
                "provider":
                    provider,
            },
        )
        .mappings()
        .first()
    )

    if not row:
        raise HTTPException(
            400,
            "INVALID_OR_EXPIRED_STATE",
        )

    db.execute(
        text("""
            UPDATE boris_crm_oauth_states

            SET used_at=NOW()

            WHERE id=:id
        """),
        {
            "id":
                row["id"]
        },
    )

    return row


@router.get(
    "/amocrm/oauth/callback",
    response_class=HTMLResponse,
)
def amo_callback(
    code: str = "",
    referer: str = "",
    state: str = "",
    error: str = "",
):

    if error:
        return HTMLResponse(
            "<h3>amoCRM: доступ не предоставлен.</h3>",
            400,
        )

    db = SessionLocal()

    try:

        state_row = consume_state(
            db,
            state,
            "amocrm",
        )

        owner = int(
            state_row[
                "owner_user_id"
            ]
        )

        connector = AmoCRM(
            db,
            owner,
        )

        connector.exchange_code(
            code,
            referer,
        )

        conn = connection(
            db,
            owner,
            "amocrm",
        )

        destination = (
            "https://boris-ai.pro"
            "/api/crm/integration/webhook/amocrm"
            "?token="
            + conn[
                "webhook_secret"
            ]
        )

        try:
            connector.subscribe_webhooks(
                destination
            )
        except Exception as exc:

            db.execute(
                text("""
                    UPDATE boris_crm_connections
                    SET last_error=:error
                    WHERE owner_user_id=:owner
                      AND provider='amocrm'
                """),
                {
                    "owner":
                        owner,
                    "error":
                        "webhook:"
                        + str(exc)[:1000],
                },
            )

        db.commit()

        return HTMLResponse(
            """
            <!doctype html>
            <html>
            <body style="font-family:Arial;padding:30px">
            <h2>amoCRM подключена к BORIS</h2>
            <p>Окно можно закрыть.</p>
            <script>
            if(window.opener){
                window.opener.postMessage(
                    {provider:"amocrm",status:"connected"},
                    "*"
                );
            }
            </script>
            </body>
            </html>
            """
        )

    except Exception as exc:

        db.rollback()

        return HTMLResponse(
            "<h3>Ошибка подключения amoCRM</h3>"
            "<pre>"
            + str(exc)[:1000]
            + "</pre>",
            400,
        )

    finally:

        db.close()


@router.get(
    "/bitrix24/oauth/callback",
    response_class=HTMLResponse,
)
def bitrix_callback(
    code: str = "",
    state: str = "",
    domain: str = "",
):

    db = SessionLocal()

    try:

        state_row = consume_state(
            db,
            state,
            "bitrix24",
        )

        owner = int(
            state_row[
                "owner_user_id"
            ]
        )

        connector = Bitrix24(
            db,
            owner,
        )

        connector.exchange_code(
            code,
            domain,
        )

        conn = connection(
            db,
            owner,
            "bitrix24",
        )

        destination = (
            "https://boris-ai.pro"
            "/api/crm/integration/webhook/bitrix24"
            "?token="
            + conn[
                "webhook_secret"
            ]
        )

        try:
            connector.subscribe_webhooks(
                destination
            )
        except Exception as exc:

            db.execute(
                text("""
                    UPDATE boris_crm_connections

                    SET last_error=:error

                    WHERE owner_user_id=:owner
                      AND provider='bitrix24'
                """),
                {
                    "owner":
                        owner,
                    "error":
                        "webhook:"
                        + str(exc)[:1000],
                },
            )

        db.commit()

        return HTMLResponse(
            """
            <!doctype html>
            <html>
            <body style="font-family:Arial;padding:30px">
            <h2>Битрикс24 подключён к BORIS</h2>
            <p>Окно можно закрыть.</p>
            <script>
            if(window.opener){
                window.opener.postMessage(
                    {provider:"bitrix24",status:"connected"},
                    "*"
                );
            }
            </script>
            </body>
            </html>
            """
        )

    except Exception as exc:

        db.rollback()

        return HTMLResponse(
            "<h3>Ошибка подключения Битрикс24</h3>"
            "<pre>"
            + str(exc)[:1000]
            + "</pre>",
            400,
        )

    finally:

        db.close()


@router.get("/{provider}/pipelines")
def remote_pipelines(
    provider: str,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        connector = connector_for(
            provider,
            db,
            owner,
        )

        return {
            "provider":
                provider,

            "pipelines":
                connector.pipelines(),
        }

    except Exception as exc:

        raise HTTPException(
            400,
            str(exc),
        )

    finally:

        db.close()


@router.get("/{provider}/mapping")
def mapping(
    provider: str,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        pipeline_id = ensure_default_pipeline(
            db,
            owner,
        )

        stages = (
            db.execute(
                text("""
                    SELECT
                        s.id,
                        s.name,
                        s.position,

                        m.external_pipeline_id,
                        m.external_stage_id,
                        m.external_pipeline_name,
                        m.external_stage_name

                    FROM boris_crm_stages s

                    LEFT JOIN boris_crm_stage_map m
                      ON m.boris_stage_id=s.id
                     AND m.owner_user_id=:owner
                     AND m.provider=:provider

                    WHERE s.pipeline_id=:pipeline

                    ORDER BY s.position,s.id
                """),
                {
                    "owner":
                        owner,
                    "provider":
                        provider,
                    "pipeline":
                        pipeline_id,
                },
            )
            .mappings()
            .all()
        )

        return [
            dict(x)
            for x in stages
        ]

    finally:

        db.close()


@router.post("/{provider}/mapping")
def set_mapping(
    provider: str,
    body: MapBody,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        stage = (
            db.execute(
                text("""
                    SELECT
                        s.id,
                        s.pipeline_id

                    FROM boris_crm_stages s

                    JOIN boris_crm_pipelines p
                      ON p.id=s.pipeline_id

                    WHERE s.id=:stage
                      AND p.owner_user_id=:owner
                """),
                {
                    "stage":
                        body.boris_stage_id,
                    "owner":
                        owner,
                },
            )
            .mappings()
            .first()
        )

        if not stage:
            raise HTTPException(
                404,
                "BORIS_STAGE_NOT_FOUND",
            )

        db.execute(
            text("""
                INSERT INTO boris_crm_stage_map (
                    owner_user_id,
                    provider,
                    boris_pipeline_id,
                    boris_stage_id,
                    external_pipeline_id,
                    external_stage_id,
                    external_pipeline_name,
                    external_stage_name
                )

                VALUES (
                    :owner,
                    :provider,
                    :pipeline,
                    :stage,
                    :external_pipeline,
                    :external_stage,
                    :external_pipeline_name,
                    :external_stage_name
                )

                ON CONFLICT (
                    owner_user_id,
                    provider,
                    boris_stage_id
                )

                DO UPDATE SET
                    external_pipeline_id=
                        EXCLUDED.external_pipeline_id,

                    external_stage_id=
                        EXCLUDED.external_stage_id,

                    external_pipeline_name=
                        EXCLUDED.external_pipeline_name,

                    external_stage_name=
                        EXCLUDED.external_stage_name,

                    updated_at=NOW()
            """),
            {
                "owner":
                    owner,

                "provider":
                    provider,

                "pipeline":
                    stage[
                        "pipeline_id"
                    ],

                "stage":
                    body.boris_stage_id,

                "external_pipeline":
                    body.external_pipeline_id,

                "external_stage":
                    body.external_stage_id,

                "external_pipeline_name":
                    body.external_pipeline_name,

                "external_stage_name":
                    body.external_stage_name,
            },
        )

        db.commit()

        return {
            "ok": True,
        }

    except HTTPException:

        db.rollback()
        raise

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


@router.post("/{provider}/writes")
def writes(
    provider: str,
    body: WritesBody,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        conn = connection(
            db,
            owner,
            provider,
        )

        if not conn:
            raise HTTPException(
                404,
                "CONNECTION_NOT_FOUND",
            )

        if body.enabled:

            if conn[
                "status"
            ] != "active":
                raise HTTPException(
                    400,
                    "OAUTH_CONNECTION_NOT_ACTIVE",
                )

            stage_count = (
                db.execute(
                    text("""
                        SELECT COUNT(*)

                        FROM boris_crm_stage_map

                        WHERE owner_user_id=:owner
                          AND provider=:provider
                    """),
                    {
                        "owner":
                            owner,
                        "provider":
                            provider,
                    },
                )
                .scalar_one()
            )

            if stage_count == 0:
                raise HTTPException(
                    400,
                    "STAGE_MAPPING_REQUIRED",
                )

        db.execute(
            text("""
                UPDATE boris_crm_connections

                SET
                    external_writes_enabled=:enabled,
                    updated_at=NOW()

                WHERE owner_user_id=:owner
                  AND provider=:provider
            """),
            {
                "enabled":
                    body.enabled,
                "owner":
                    owner,
                "provider":
                    provider,
            },
        )

        db.commit()

        return {
            "ok": True,
            "external_writes":
                body.enabled,
        }

    finally:

        db.close()


@router.post("/{provider}/enqueue-all")
def enqueue_all(
    provider: str,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    db = SessionLocal()

    try:

        conn = connection(
            db,
            owner,
            provider,
        )

        if (
            not conn
            or conn[
                "status"
            ] != "active"
            or not conn[
                "external_writes_enabled"
            ]
        ):
            raise HTTPException(
                400,
                "EXTERNAL_WRITES_NOT_ENABLED",
            )

        contacts = (
            db.execute(
                text("""
                    SELECT id

                    FROM boris_crm_contacts

                    WHERE owner_user_id=:owner
                """),
                {
                    "owner": owner
                },
            )
            .scalars()
            .all()
        )

        deals = (
            db.execute(
                text("""
                    SELECT id

                    FROM boris_crm_deals

                    WHERE owner_user_id=:owner
                """),
                {
                    "owner": owner
                },
            )
            .scalars()
            .all()
        )

        total = 0

        for entity_type, ids in (
            ("contact", contacts),
            ("deal", deals),
        ):

            for entity_id in ids:

                event_id = (
                    "manual:"
                    + secrets.token_hex(
                        16
                    )
                )

                db.execute(
                    text("""
                        INSERT INTO boris_crm_sync_outbox (
                            owner_user_id,
                            provider,
                            event_id,
                            idempotency_key,
                            entity_type,
                            entity_id,
                            operation,
                            payload_json,
                            status,
                            next_attempt_at
                        )

                        VALUES (
                            :owner,
                            :provider,
                            :event,
                            :event,
                            :entity_type,
                            :entity_id,
                            'upsert',
                            '{}'::jsonb,
                            'pending',
                            NOW()
                        )
                    """),
                    {
                        "owner":
                            owner,
                        "provider":
                            provider,
                        "event":
                            event_id,
                        "entity_type":
                            entity_type,
                        "entity_id":
                            entity_id,
                    },
                )

                total += 1

        db.commit()

        return {
            "ok": True,
            "queued":
                total,
        }

    finally:

        db.close()


def webhook_owner(
    db,
    provider: str,
    token: str,
):

    row = (
        db.execute(
            text("""
                SELECT *

                FROM boris_crm_connections

                WHERE provider=:provider
                  AND webhook_secret=:token
                  AND status='active'
            """),
            {
                "provider":
                    provider,
                "token":
                    token,
            },
        )
        .mappings()
        .first()
    )

    if not row:
        raise HTTPException(
            403,
            "INVALID_WEBHOOK_TOKEN",
        )

    return row


def dedupe(
    db,
    provider: str,
    key: str,
) -> bool:

    result = db.execute(
        text("""
            INSERT INTO boris_crm_webhook_dedupe (
                provider,
                event_key
            )

            VALUES (
                :provider,
                :key
            )

            ON CONFLICT DO NOTHING

            RETURNING id
        """),
        {
            "provider":
                provider,
            "key":
                key,
        },
    ).first()

    return bool(
        result
    )


def find_boris_from_external(
    db,
    owner: int,
    provider: str,
    entity_type: str,
    external_id: str,
):

    row = (
        db.execute(
            text("""
                SELECT boris_entity_id

                FROM boris_crm_external_entities

                WHERE owner_user_id=:owner
                  AND provider=:provider
                  AND entity_type=:entity_type
                  AND external_entity_id=:external
            """),
            {
                "owner":
                    owner,
                "provider":
                    provider,
                "entity_type":
                    entity_type,
                "external":
                    str(
                        external_id
                    ),
            },
        )
        .first()
    )

    return (
        int(row[0])
        if row
        else None
    )


def external_stage_to_boris(
    db,
    owner: int,
    provider: str,
    external_pipeline: str,
    external_stage: str,
):

    row = (
        db.execute(
            text("""
                SELECT boris_stage_id

                FROM boris_crm_stage_map

                WHERE owner_user_id=:owner
                  AND provider=:provider
                  AND external_pipeline_id=:pipeline
                  AND external_stage_id=:stage

                LIMIT 1
            """),
            {
                "owner":
                    owner,
                "provider":
                    provider,
                "pipeline":
                    str(
                        external_pipeline
                    ),
                "stage":
                    str(
                        external_stage
                    ),
            },
        )
        .first()
    )

    return (
        int(row[0])
        if row
        else None
    )


def apply_external_deal(
    db,
    owner: int,
    provider: str,
    external_id: str,
):

    connector = connector_for(
        provider,
        db,
        owner,
    )

    remote = connector.get_deal(
        external_id
    )

    boris_id = find_boris_from_external(
        db,
        owner,
        provider,
        "deal",
        external_id,
    )

    if provider == "amocrm":

        title = remote.get(
            "name"
        )

        amount_kopeks = int(
            remote.get(
                "price",
                0,
            )
            or 0
        ) * 100

        pipeline = str(
            remote.get(
                "pipeline_id",
                ""
            )
        )

        stage = str(
            remote.get(
                "status_id",
                ""
            )
        )

    else:

        title = remote.get(
            "TITLE"
        )

        amount_kopeks = int(
            float(
                remote.get(
                    "OPPORTUNITY",
                    0,
                )
                or 0
            )
            * 100
        )

        pipeline = str(
            remote.get(
                "CATEGORY_ID",
                0,
            )
        )

        stage = str(
            remote.get(
                "STAGE_ID",
                "",
            )
        )

    boris_stage = (
        external_stage_to_boris(
            db,
            owner,
            provider,
            pipeline,
            stage,
        )
    )

    if not boris_id:

        return {
            "mapped":
                False,
            "external_id":
                str(
                    external_id
                ),
        }

    db.execute(
        text("""
            SELECT set_config(
                'boris.crm_sync_origin',
                :provider,
                TRUE
            )
        """),
        {
            "provider":
                provider
        },
    )

    if boris_stage:

        db.execute(
            text("""
                UPDATE boris_crm_deals

                SET
                    title=COALESCE(
                        :title,
                        title
                    ),

                    amount_kopeks=
                        :amount,

                    stage_id=
                        :stage,

                    updated_at=NOW(),

                    last_activity_at=NOW()

                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "title":
                    title,

                "amount":
                    amount_kopeks,

                "stage":
                    boris_stage,

                "id":
                    boris_id,

                "owner":
                    owner,
            },
        )

    else:

        db.execute(
            text("""
                UPDATE boris_crm_deals

                SET
                    title=COALESCE(
                        :title,
                        title
                    ),

                    amount_kopeks=
                        :amount,

                    updated_at=NOW(),

                    last_activity_at=NOW()

                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "title":
                    title,

                "amount":
                    amount_kopeks,

                "id":
                    boris_id,

                "owner":
                    owner,
            },
        )

    return {
        "mapped":
            True,

        "boris_id":
            boris_id,
    }


async def parse_webhook(
    request: Request,
):

    content_type = (
        request.headers.get(
            "content-type",
            ""
        )
    )

    if "application/json" in content_type:

        try:
            return await request.json()
        except Exception:
            return {}

    raw = (
        await request.body()
    ).decode(
        errors="ignore"
    )

    return parse_qs(
        raw,
        keep_blank_values=True,
    )


@router.post("/webhook/amocrm")
async def amo_webhook(
    request: Request,
    token: str = Query(...),
):

    db = SessionLocal()

    try:

        conn = webhook_owner(
            db,
            "amocrm",
            token,
        )

        owner = int(
            conn[
                "owner_user_id"
            ]
        )

        payload = await parse_webhook(
            request
        )

        raw = json.dumps(
            payload,
            sort_keys=True,
            default=str,
        )

        key = hashlib.sha256(
            raw.encode()
        ).hexdigest()

        if not dedupe(
            db,
            "amocrm",
            key,
        ):
            db.rollback()

            return {
                "ok": True,
                "duplicate": True,
            }

        external_ids = set()

        if isinstance(
            payload,
            dict,
        ):

            for k, values in payload.items():

                if (
                    "leads[" in str(k)
                    and str(k).endswith(
                        "[id]"
                    )
                ):

                    if isinstance(
                        values,
                        list,
                    ):
                        external_ids.update(
                            str(x)
                            for x in values
                        )

        results = []

        for external_id in external_ids:

            results.append(
                apply_external_deal(
                    db,
                    owner,
                    "amocrm",
                    external_id,
                )
            )

        db.commit()

        return {
            "ok": True,
            "deals":
                results,
        }

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


@router.post("/webhook/bitrix24")
async def bitrix_webhook(
    request: Request,
    token: str = Query(...),
):

    db = SessionLocal()

    try:

        conn = webhook_owner(
            db,
            "bitrix24",
            token,
        )

        owner = int(
            conn[
                "owner_user_id"
            ]
        )

        payload = await parse_webhook(
            request
        )

        raw = json.dumps(
            payload,
            sort_keys=True,
            default=str,
        )

        key = hashlib.sha256(
            raw.encode()
        ).hexdigest()

        if not dedupe(
            db,
            "bitrix24",
            key,
        ):
            db.rollback()

            return {
                "ok": True,
                "duplicate": True,
            }

        external_id = None

        if isinstance(
            payload,
            dict,
        ):

            for key_name in (
                "data[FIELDS][ID]",
                "data[FIELDS][Id]",
            ):

                value = payload.get(
                    key_name
                )

                if value:
                    if isinstance(
                        value,
                        list,
                    ):
                        external_id = (
                            value[0]
                        )
                    else:
                        external_id = value

        result = None

        if external_id:

            result = apply_external_deal(
                db,
                owner,
                "bitrix24",
                str(
                    external_id
                ),
            )

        db.commit()

        return {
            "ok": True,
            "deal":
                result,
        }

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


@router.post("/{provider}/import")
def import_remote(
    provider: str,
    limit: int = 100,
    current_user=Depends(
        get_current_user
    ),
):

    validate_provider(
        provider
    )

    owner = uid(
        current_user
    )

    limit = max(
        1,
        min(
            int(limit),
            250,
        ),
    )

    db = SessionLocal()

    try:

        connector = connector_for(
            provider,
            db,
            owner,
        )

        remote_deals = (
            connector.list_deals(
                limit
            )
        )

        imported = 0
        mapped = 0
        unmapped = 0

        db.execute(
            text("""
                SELECT set_config(
                    'boris.crm_sync_origin',
                    :provider,
                    TRUE
                )
            """),
            {
                "provider":
                    provider
            },
        )

        pipeline_id = ensure_default_pipeline(
            db,
            owner,
        )

        fallback_stage = (
            db.execute(
                text("""
                    SELECT id

                    FROM boris_crm_stages

                    WHERE pipeline_id=:pipeline

                    ORDER BY position

                    LIMIT 1
                """),
                {
                    "pipeline":
                        pipeline_id
                },
            )
            .scalar_one()
        )

        for remote in remote_deals:

            if provider == "amocrm":

                external_id = str(
                    remote["id"]
                )

                title = (
                    remote.get(
                        "name"
                    )
                    or
                    "Сделка amoCRM"
                )

                amount = int(
                    remote.get(
                        "price",
                        0,
                    )
                    or 0
                ) * 100

                ext_pipeline = str(
                    remote.get(
                        "pipeline_id",
                        ""
                    )
                )

                ext_stage = str(
                    remote.get(
                        "status_id",
                        ""
                    )
                )

            else:

                external_id = str(
                    remote["ID"]
                )

                title = (
                    remote.get(
                        "TITLE"
                    )
                    or
                    "Сделка Битрикс24"
                )

                amount = int(
                    float(
                        remote.get(
                            "OPPORTUNITY",
                            0,
                        )
                        or 0
                    )
                    * 100
                )

                ext_pipeline = str(
                    remote.get(
                        "CATEGORY_ID",
                        0,
                    )
                )

                ext_stage = str(
                    remote.get(
                        "STAGE_ID",
                        ""
                    )
                )

            existing = (
                find_boris_from_external(
                    db,
                    owner,
                    provider,
                    "deal",
                    external_id,
                )
            )

            stage_id = (
                external_stage_to_boris(
                    db,
                    owner,
                    provider,
                    ext_pipeline,
                    ext_stage,
                )
            )

            if stage_id:
                mapped += 1
            else:
                unmapped += 1
                stage_id = (
                    fallback_stage
                )

            if existing:

                db.execute(
                    text("""
                        UPDATE boris_crm_deals

                        SET
                            title=:title,
                            amount_kopeks=:amount,
                            stage_id=:stage,
                            updated_at=NOW()

                        WHERE id=:id
                          AND owner_user_id=:owner
                    """),
                    {
                        "title":
                            title,
                        "amount":
                            amount,
                        "stage":
                            stage_id,
                        "id":
                            existing,
                        "owner":
                            owner,
                    },
                )

            else:

                boris_id = (
                    db.execute(
                        text("""
                            INSERT INTO boris_crm_deals (
                                owner_user_id,
                                pipeline_id,
                                stage_id,
                                title,
                                amount_kopeks,
                                currency,
                                source,
                                source_ref,
                                status,
                                last_activity_at
                            )

                            VALUES (
                                :owner,
                                :pipeline,
                                :stage,
                                :title,
                                :amount,
                                'RUB',
                                :provider,
                                :source_ref,
                                'open',
                                NOW()
                            )

                            RETURNING id
                        """),
                        {
                            "owner":
                                owner,
                            "pipeline":
                                pipeline_id,
                            "stage":
                                stage_id,
                            "title":
                                title,
                            "amount":
                                amount,
                            "provider":
                                provider,
                            "source_ref":
                                provider
                                + ":"
                                + external_id,
                        },
                    )
                    .scalar_one()
                )

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
                            'deal',
                            :boris_id,
                            :external_id
                        )

                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "owner":
                            owner,
                        "provider":
                            provider,
                        "boris_id":
                            boris_id,
                        "external_id":
                            external_id,
                    },
                )

                imported += 1

        db.commit()

        return {
            "ok": True,
            "remote":
                len(
                    remote_deals
                ),
            "created":
                imported,
            "mapped_stage":
                mapped,
            "fallback_stage":
                unmapped,
        }

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()
