"""
BORIS CRM schema v1.

CRM is BORIS-owned even when amoCRM / Bitrix24 is connected.

External CRMs are synchronization targets/sources, NOT the internal
source of truth for BORIS automation and AI.
"""

from sqlalchemy import text
from app.crm.db import SessionLocal


DDL = r"""
CREATE TABLE IF NOT EXISTS boris_crm_contacts (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    display_name        TEXT,
    first_name          TEXT,
    last_name           TEXT,

    primary_phone       TEXT,
    primary_email       TEXT,

    company_name        TEXT,

    source              TEXT,
    source_ref          TEXT,

    avito_user_id       TEXT,
    avito_chat_id       TEXT,
    telegram_id         TEXT,
    vk_id               TEXT,

    status              TEXT NOT NULL DEFAULT 'active',

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_contacts_owner
    ON boris_crm_contacts(owner_user_id);

CREATE INDEX IF NOT EXISTS ix_boris_crm_contacts_phone
    ON boris_crm_contacts(owner_user_id, primary_phone);

CREATE INDEX IF NOT EXISTS ix_boris_crm_contacts_email
    ON boris_crm_contacts(owner_user_id, primary_email);

CREATE INDEX IF NOT EXISTS ix_boris_crm_contacts_avito_chat
    ON boris_crm_contacts(owner_user_id, avito_chat_id);


CREATE TABLE IF NOT EXISTS boris_crm_companies (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    name                TEXT NOT NULL,
    phone               TEXT,
    email               TEXT,
    website             TEXT,

    source              TEXT,
    source_ref          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_companies_owner
    ON boris_crm_companies(owner_user_id);


CREATE TABLE IF NOT EXISTS boris_crm_pipelines (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    name                TEXT NOT NULL,
    code                TEXT,
    is_default          BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_pipelines_owner
    ON boris_crm_pipelines(owner_user_id);


CREATE TABLE IF NOT EXISTS boris_crm_stages (
    id                  BIGSERIAL PRIMARY KEY,
    pipeline_id         BIGINT NOT NULL REFERENCES boris_crm_pipelines(id)
                            ON DELETE CASCADE,

    name                TEXT NOT NULL,
    code                TEXT,
    position            INTEGER NOT NULL DEFAULT 0,

    semantic_type       TEXT NOT NULL DEFAULT 'open',

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_stages_pipeline
    ON boris_crm_stages(pipeline_id, position);


CREATE TABLE IF NOT EXISTS boris_crm_deals (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    contact_id          BIGINT REFERENCES boris_crm_contacts(id)
                            ON DELETE SET NULL,

    company_id          BIGINT REFERENCES boris_crm_companies(id)
                            ON DELETE SET NULL,

    pipeline_id         BIGINT REFERENCES boris_crm_pipelines(id)
                            ON DELETE SET NULL,

    stage_id            BIGINT REFERENCES boris_crm_stages(id)
                            ON DELETE SET NULL,

    title               TEXT NOT NULL,

    amount_kopeks       BIGINT,
    currency            TEXT NOT NULL DEFAULT 'RUB',

    source              TEXT,
    source_ref          TEXT,

    avito_account_id    TEXT,
    avito_item_id       TEXT,
    avito_chat_id       TEXT,

    responsible_user_id BIGINT,

    status              TEXT NOT NULL DEFAULT 'open',

    next_action_at      TIMESTAMPTZ,
    last_activity_at    TIMESTAMPTZ,

    lost_reason         TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_deals_owner
    ON boris_crm_deals(owner_user_id);

CREATE INDEX IF NOT EXISTS ix_boris_crm_deals_contact
    ON boris_crm_deals(contact_id);

CREATE INDEX IF NOT EXISTS ix_boris_crm_deals_stage
    ON boris_crm_deals(stage_id);

CREATE INDEX IF NOT EXISTS ix_boris_crm_deals_avito_chat
    ON boris_crm_deals(owner_user_id, avito_chat_id);


CREATE TABLE IF NOT EXISTS boris_crm_tasks (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    deal_id             BIGINT REFERENCES boris_crm_deals(id)
                            ON DELETE CASCADE,

    contact_id          BIGINT REFERENCES boris_crm_contacts(id)
                            ON DELETE SET NULL,

    title               TEXT NOT NULL,
    description         TEXT,

    assigned_user_id    BIGINT,

    due_at              TIMESTAMPTZ,

    status              TEXT NOT NULL DEFAULT 'open',
    source              TEXT NOT NULL DEFAULT 'manual',

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_tasks_owner_due
    ON boris_crm_tasks(owner_user_id, status, due_at);


CREATE TABLE IF NOT EXISTS boris_crm_activities (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    deal_id             BIGINT REFERENCES boris_crm_deals(id)
                            ON DELETE CASCADE,

    contact_id          BIGINT REFERENCES boris_crm_contacts(id)
                            ON DELETE SET NULL,

    activity_type       TEXT NOT NULL,

    channel             TEXT,
    direction           TEXT,

    title               TEXT,
    body                TEXT,

    source              TEXT,
    source_ref          TEXT,

    actor_type          TEXT,
    actor_id            TEXT,

    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_activities_deal
    ON boris_crm_activities(deal_id, created_at DESC);


CREATE TABLE IF NOT EXISTS boris_crm_conversations (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    contact_id          BIGINT REFERENCES boris_crm_contacts(id)
                            ON DELETE SET NULL,

    deal_id             BIGINT REFERENCES boris_crm_deals(id)
                            ON DELETE SET NULL,

    channel             TEXT NOT NULL,

    account_id          TEXT,
    external_chat_id    TEXT NOT NULL,

    title               TEXT,

    unread_count        INTEGER NOT NULL DEFAULT 0,

    last_message_at     TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(owner_user_id, channel, account_id, external_chat_id)
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_conversations_owner_last
    ON boris_crm_conversations(owner_user_id, last_message_at DESC);


CREATE TABLE IF NOT EXISTS boris_crm_messages (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    conversation_id     BIGINT NOT NULL REFERENCES boris_crm_conversations(id)
                            ON DELETE CASCADE,

    external_message_id TEXT,

    direction           TEXT NOT NULL,
    sender_type         TEXT,

    body                TEXT,

    sent_at             TIMESTAMPTZ NOT NULL,

    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(conversation_id, external_message_id)
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_messages_conversation
    ON boris_crm_messages(conversation_id, sent_at);


CREATE TABLE IF NOT EXISTS boris_crm_connections (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    provider            TEXT NOT NULL,

    mode                TEXT NOT NULL DEFAULT 'boris_master',

    status              TEXT NOT NULL DEFAULT 'disabled',

    external_account_id TEXT,
    external_domain     TEXT,

    settings_json       JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(owner_user_id, provider)
);

CREATE INDEX IF NOT EXISTS ix_boris_crm_connections_owner
    ON boris_crm_connections(owner_user_id);


CREATE TABLE IF NOT EXISTS boris_crm_external_entities (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    provider            TEXT NOT NULL,

    entity_type         TEXT NOT NULL,
    boris_entity_id     BIGINT NOT NULL,
    external_entity_id  TEXT NOT NULL,

    external_version    TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(owner_user_id, provider, entity_type, boris_entity_id),
    UNIQUE(owner_user_id, provider, entity_type, external_entity_id)
);


CREATE TABLE IF NOT EXISTS boris_crm_sync_outbox (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    provider            TEXT NOT NULL,

    event_id            TEXT NOT NULL UNIQUE,
    idempotency_key     TEXT NOT NULL UNIQUE,

    entity_type         TEXT NOT NULL,
    entity_id           BIGINT NOT NULL,

    operation           TEXT NOT NULL,

    payload_json        JSONB NOT NULL DEFAULT '{}'::jsonb,

    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,

    next_attempt_at     TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);


CREATE TABLE IF NOT EXISTS boris_crm_sync_events (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    provider            TEXT NOT NULL,

    external_event_id   TEXT,

    event_type          TEXT,

    entity_type         TEXT,
    external_entity_id  TEXT,

    payload_json        JSONB NOT NULL DEFAULT '{}'::jsonb,

    status              TEXT NOT NULL DEFAULT 'received',

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(provider, external_event_id)
);


CREATE TABLE IF NOT EXISTS boris_crm_sync_conflicts (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    provider            TEXT NOT NULL,

    entity_type         TEXT NOT NULL,
    entity_id           BIGINT NOT NULL,

    field_name          TEXT,

    boris_value         TEXT,
    external_value      TEXT,

    resolution          TEXT,

    status              TEXT NOT NULL DEFAULT 'open',

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ
);


CREATE TABLE IF NOT EXISTS boris_crm_audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT NOT NULL,

    actor_type          TEXT,
    actor_id            TEXT,

    action              TEXT NOT NULL,

    entity_type         TEXT NOT NULL,
    entity_id           BIGINT,

    before_json         JSONB,
    after_json          JSONB,

    reason              TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_crm_audit_owner_created
    ON boris_crm_audit_log(owner_user_id, created_at DESC);
"""


def install_schema():
    db = SessionLocal()
    try:
        for statement in DDL.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            db.execute(text(statement))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    install_schema()
    print("CRM_SCHEMA_OK")
