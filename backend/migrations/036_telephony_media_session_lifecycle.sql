-- 036_telephony_media_session_lifecycle.sql
-- Durable ownership for short-lived media sessions. No media/provider secret is stored here.
CREATE TABLE IF NOT EXISTS telephony_media_sessions (
    id bigserial PRIMARY KEY,
    account_id text NOT NULL,
    call_id text NOT NULL,
    device_id text NOT NULL,
    provider text NOT NULL,
    endpoint_id text NOT NULL,
    transport text NOT NULL,
    managed_by text NOT NULL DEFAULT 'provider',
    status text NOT NULL DEFAULT 'active',
    session_fingerprint text NOT NULL,
    expires_at timestamptz NOT NULL,
    cleanup_attempts integer NOT NULL DEFAULT 0,
    next_cleanup_at timestamptz,
    last_error text,
    cleaned_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_media_endpoint
ON telephony_media_sessions(endpoint_id);

CREATE INDEX IF NOT EXISTS ix_telephony_media_due
ON telephony_media_sessions(expires_at,id,next_cleanup_at)
WHERE status IN ('active','cleanup_pending','await_expiry');

CREATE INDEX IF NOT EXISTS ix_telephony_media_call
ON telephony_media_sessions(account_id,call_id,device_id,created_at DESC);

ALTER TABLE telephony_media_sessions
    DROP CONSTRAINT IF EXISTS ck_telephony_media_transport;
ALTER TABLE telephony_media_sessions
    ADD CONSTRAINT ck_telephony_media_transport
    CHECK (transport IN ('webrtc','sip_ws','sip_tls')) NOT VALID;

ALTER TABLE telephony_media_sessions
    DROP CONSTRAINT IF EXISTS ck_telephony_media_managed_by;
ALTER TABLE telephony_media_sessions
    ADD CONSTRAINT ck_telephony_media_managed_by
    CHECK (managed_by IN ('provider','boris_gateway')) NOT VALID;

ALTER TABLE telephony_media_sessions
    DROP CONSTRAINT IF EXISTS ck_telephony_media_status;
ALTER TABLE telephony_media_sessions
    ADD CONSTRAINT ck_telephony_media_status
    CHECK (status IN ('active','cleanup_pending','await_expiry','closed','expired')) NOT VALID;

ALTER TABLE telephony_media_sessions
    DROP CONSTRAINT IF EXISTS ck_telephony_media_cleanup_attempts;
ALTER TABLE telephony_media_sessions
    ADD CONSTRAINT ck_telephony_media_cleanup_attempts
    CHECK (cleanup_attempts BETWEEN 0 AND 12) NOT VALID;

ALTER TABLE telephony_media_sessions VALIDATE CONSTRAINT ck_telephony_media_transport;
ALTER TABLE telephony_media_sessions VALIDATE CONSTRAINT ck_telephony_media_managed_by;
ALTER TABLE telephony_media_sessions VALIDATE CONSTRAINT ck_telephony_media_status;
ALTER TABLE telephony_media_sessions VALIDATE CONSTRAINT ck_telephony_media_cleanup_attempts;
