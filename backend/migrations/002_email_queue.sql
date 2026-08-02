-- 002_email_queue.sql — очередь отправки писем и журнал доставки.
-- Идемпотентна: повторный прогон ничего не портит и не трогает данные.
-- Времена — timestamp WITHOUT time zone, как во всей базе проекта.

CREATE TABLE IF NOT EXISTS email_queue (
    id                  SERIAL PRIMARY KEY,
    idempotency_key     VARCHAR(200),
    source              VARCHAR(64),
    to_addresses        TEXT NOT NULL,
    subject             TEXT NOT NULL,
    text_body           TEXT,
    html_body           TEXT,
    from_address        VARCHAR(255),
    from_name           VARCHAR(255),
    reply_to            VARCHAR(255),
    headers             TEXT,
    status              VARCHAR(32) NOT NULL DEFAULT 'queued',
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 5,
    next_attempt_at     TIMESTAMP,
    last_error          VARCHAR(255),
    provider_message_id VARCHAR(255),
    sent_at             TIMESTAMP,
    created_at          TIMESTAMP DEFAULT now(),
    updated_at          TIMESTAMP DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_email_queue_status') THEN
        ALTER TABLE email_queue ADD CONSTRAINT ck_email_queue_status
            CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'dead'));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_email_queue_idem
    ON email_queue (idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_email_queue_pick
    ON email_queue (status, next_attempt_at, id);

CREATE INDEX IF NOT EXISTS ix_email_queue_created ON email_queue (created_at);

CREATE TABLE IF NOT EXISTS email_delivery_events (
    id                  SERIAL PRIMARY KEY,
    email_queue_id      INTEGER,
    event_type          VARCHAR(32) NOT NULL,
    provider            VARCHAR(64),
    provider_message_id VARCHAR(255),
    details             TEXT,
    created_at          TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_email_events_queue ON email_delivery_events (email_queue_id);
CREATE INDEX IF NOT EXISTS ix_email_events_type ON email_delivery_events (event_type, created_at);
