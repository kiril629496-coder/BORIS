-- 037_telphin_sip_trunk_state.sql
-- Runtime state for Telphin SIP registration. No SIP/API secrets are stored here.
CREATE TABLE IF NOT EXISTS telephony_telphin_trunk_state (
    account_id text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    registrar text,
    registration_id text,
    endpoint_id text,
    status text NOT NULL DEFAULT 'waiting_credentials',
    rendered_sha256 text,
    applied_sha256 text,
    last_rendered_at timestamptz,
    last_applied_at timestamptz,
    last_health_at timestamptz,
    last_health_status text,
    last_registered_at timestamptz,
    last_error text,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_telphin_trunk_state_status
ON telephony_telphin_trunk_state(status,last_health_at,account_id);

ALTER TABLE telephony_telphin_trunk_state
  DROP CONSTRAINT IF EXISTS ck_telphin_trunk_status;
ALTER TABLE telephony_telphin_trunk_state
  ADD CONSTRAINT ck_telphin_trunk_status
  CHECK (status IN ('waiting_credentials','configured_unverified','apply_deferred','registered','unregistered','rejected','degraded','disabled')) NOT VALID;
ALTER TABLE telephony_telphin_trunk_state VALIDATE CONSTRAINT ck_telphin_trunk_status;
