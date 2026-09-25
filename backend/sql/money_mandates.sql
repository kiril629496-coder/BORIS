SET lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS money_mandates (
    id                   BIGSERIAL PRIMARY KEY,
    owner_kind           VARCHAR(16)  NOT NULL DEFAULT 'user',
    owner_id             INTEGER      NOT NULL,
    account_scope        TEXT[]       NOT NULL,
    allowed_operations   TEXT[]       NOT NULL,
    daily_budget_rub     NUMERIC(12,2),
    max_actions_run      INTEGER      NOT NULL,
    max_actions_day      INTEGER      NOT NULL,
    max_bid_delta_pct    INTEGER      NOT NULL,
    min_bid_rub          NUMERIC(10,2),
    max_bid_rub          NUMERIC(10,2) NOT NULL,
    reserve_rub          NUMERIC(10,2),
    valid_from           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    valid_until          TIMESTAMPTZ  NOT NULL,
    revoked_at           TIMESTAMPTZ,
    revoked_by           INTEGER,
    granted_by_user_id   INTEGER      NOT NULL,
    granted_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    confirmation_text    TEXT,
    confirmation_version VARCHAR(16),
    source               VARCHAR(32)  NOT NULL DEFAULT 'manual_sql',
    status               VARCHAR(16)  NOT NULL DEFAULT 'active',
    version              INTEGER      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_mm_owner  ON money_mandates (owner_kind, owner_id, status);
CREATE INDEX IF NOT EXISTS ix_mm_valid  ON money_mandates (status, valid_until);

CREATE TABLE IF NOT EXISTS money_mandate_events (
    id          BIGSERIAL PRIMARY KEY,
    mandate_id  BIGINT      NOT NULL,
    event       VARCHAR(32) NOT NULL,
    actor_type  VARCHAR(16) NOT NULL,
    actor_id    INTEGER,
    before_json JSONB,
    after_json  JSONB,
    reason      TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_mme_mandate ON money_mandate_events (mandate_id, at);

ALTER TABLE action_log ADD COLUMN IF NOT EXISTS trigger          VARCHAR(16);
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS mandate_id       BIGINT;
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS mandate_version  INTEGER;
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS request_id       VARCHAR(64);
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS balance_status   VARCHAR(24);
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS guard_reason     VARCHAR(64);
