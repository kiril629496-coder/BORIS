-- BORIS prospect email open tracking
-- Idempotent DDL for first-party tracking pixel analytics.

CREATE TABLE IF NOT EXISTS email_open_trackers (
    id BIGSERIAL PRIMARY KEY,
    email_queue_id BIGINT NOT NULL UNIQUE REFERENCES email_queue(id) ON DELETE CASCADE,
    token VARCHAR(96) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_opened_at TIMESTAMPTZ NULL,
    last_opened_at TIMESTAMPTZ NULL,
    open_count INTEGER NOT NULL DEFAULT 0,
    proxy_hint_count INTEGER NOT NULL DEFAULT 0,
    first_user_agent VARCHAR(1000) NULL,
    last_user_agent VARCHAR(1000) NULL
);

CREATE TABLE IF NOT EXISTS email_open_events (
    id BIGSERIAL PRIMARY KEY,
    tracker_id BIGINT NOT NULL REFERENCES email_open_trackers(id) ON DELETE CASCADE,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_agent VARCHAR(1000) NULL,
    proxy_hint BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_email_open_events_tracker_opened
    ON email_open_events(tracker_id, opened_at DESC);

CREATE INDEX IF NOT EXISTS ix_email_open_trackers_first_opened
    ON email_open_trackers(first_opened_at);


CREATE TABLE IF NOT EXISTS email_tracking_policy (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
    activated_at TIMESTAMPTZ NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1
);

INSERT INTO email_tracking_policy(id, activated_at, policy_version)
VALUES (
    TRUE,
    COALESCE((SELECT MIN(created_at) FROM email_open_trackers), NOW()),
    1
)
ON CONFLICT(id) DO NOTHING;


-- Supports open -> reply latency and reply-without-open analytics without
-- repeated scans of the inbound reply history.
CREATE INDEX IF NOT EXISTS ix_prospect_replies_member_human_received
    ON prospect_inbound_replies(member_id, received_at)
    WHERE COALESCE(message_kind,'human')='human';
