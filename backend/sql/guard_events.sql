SET lock_timeout = '5s';

ALTER TABLE action_log ALTER COLUMN trigger TYPE VARCHAR(32);

CREATE TABLE IF NOT EXISTS autonomy_guard_events (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id      VARCHAR(64) NOT NULL,
    operation       VARCHAR(48) NOT NULL,
    object_id       VARCHAR(64),
    decision        VARCHAR(16) NOT NULL,
    reason_code     VARCHAR(64) NOT NULL,
    actor           VARCHAR(16),
    trigger         VARCHAR(32),
    source          VARCHAR(64),
    request_id      VARCHAR(64),
    mandate_id      BIGINT,
    mandate_version INTEGER,
    balance_status  VARCHAR(24),
    limits_applied  JSONB,
    evidence        JSONB
);

CREATE INDEX IF NOT EXISTS ix_age_acc ON autonomy_guard_events (account_id, ts);
CREATE INDEX IF NOT EXISTS ix_age_req ON autonomy_guard_events (request_id);
