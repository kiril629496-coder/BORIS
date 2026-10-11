-- BORIS INTERNAL MESSENGER REALTIME EVENTS V1
-- Additive/backward-safe durable cursor for account-scoped Messenger SSE.
CREATE TABLE IF NOT EXISTS internal_messenger_events (
  id BIGSERIAL PRIMARY KEY,
  account_id VARCHAR NOT NULL,
  event_type VARCHAR(96) NOT NULL,
  entity_type VARCHAR(64) NOT NULL,
  entity_id VARCHAR(128) NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_internal_messenger_events_account_id
  ON internal_messenger_events(account_id, id);
CREATE INDEX IF NOT EXISTS ix_internal_messenger_events_created
  ON internal_messenger_events(created_at);
