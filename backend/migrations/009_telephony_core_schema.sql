-- 009_telephony_core_schema.sql
-- Formal, idempotent baseline for BORIS Phone core. Runtime ensure_schema remains a compatibility guard.
BEGIN;
CREATE TABLE IF NOT EXISTS telephony_webhook_nonces (
  account_id text NOT NULL, provider text NOT NULL, nonce_hash text NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(account_id,provider,nonce_hash)
);
CREATE INDEX IF NOT EXISTS ix_telephony_webhook_nonces_received ON telephony_webhook_nonces(received_at);
COMMIT;
