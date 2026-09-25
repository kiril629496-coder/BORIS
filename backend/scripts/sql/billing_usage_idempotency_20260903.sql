-- BILLING_USAGE_IDEMPOTENCY_V1
CREATE TABLE IF NOT EXISTS boris_billing_usage_intents (
  id BIGSERIAL PRIMARY KEY,
  account_id VARCHAR(255) NOT NULL,
  unit VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE(account_id, unit, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_boris_billing_usage_intents_created
  ON boris_billing_usage_intents(created_at DESC);
