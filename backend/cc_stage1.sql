CREATE TABLE IF NOT EXISTS boris_commands (
  id BIGSERIAL PRIMARY KEY,
  command_id UUID NOT NULL UNIQUE,
  user_id INTEGER NOT NULL,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  account_id VARCHAR,
  source VARCHAR NOT NULL,
  raw_text TEXT,
  transcript TEXT,
  text_purged_at TIMESTAMP,
  asr_model VARCHAR,
  audio_seconds NUMERIC,
  audio_format VARCHAR,
  locale VARCHAR,
  device VARCHAR,
  page_context JSONB,
  entity_type VARCHAR,
  entity_id VARCHAR,
  intent VARCHAR,
  confidence NUMERIC,
  entities JSONB,
  operation VARCHAR,
  risk_level VARCHAR,
  permission VARCHAR,
  needs_confirmation BOOLEAN DEFAULT FALSE,
  human_summary TEXT,
  clarify_question TEXT,
  steps JSONB,
  stage VARCHAR,
  status VARCHAR NOT NULL,
  units INTEGER DEFAULT 1,
  units_charged_at TIMESTAMP,
  result JSONB,
  error_code VARCHAR,
  request_id VARCHAR,
  created_at TIMESTAMP DEFAULT now(),
  expires_at TIMESTAMP,
  confirmed_at TIMESTAMP,
  executed_at TIMESTAMP,
  duration_ms INTEGER);
CREATE INDEX IF NOT EXISTS ix_bc_user ON boris_commands (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_bc_owner ON boris_commands (billing_owner_kind, billing_owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_bc_live ON boris_commands (status) WHERE status IN ('parsed','awaiting_confirm','confirmed','executing');
CREATE INDEX IF NOT EXISTS ix_bc_purge ON boris_commands (created_at) WHERE text_purged_at IS NULL;
CREATE TABLE IF NOT EXISTS boris_command_events (
  id BIGSERIAL PRIMARY KEY,
  command_id UUID NOT NULL,
  ts TIMESTAMP DEFAULT now(),
  event VARCHAR NOT NULL,
  actor VARCHAR,
  payload_redacted JSONB);
CREATE INDEX IF NOT EXISTS ix_bce_cmd ON boris_command_events (command_id, ts);
CREATE TABLE IF NOT EXISTS product_limits (
  id BIGSERIAL PRIMARY KEY,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  resource VARCHAR NOT NULL,
  purchased INTEGER DEFAULT 0,
  used INTEGER DEFAULT 0,
  period_start DATE,
  paid_until DATE,
  updated_at TIMESTAMP DEFAULT now(),
  CONSTRAINT uq_product_limit UNIQUE (billing_owner_kind, billing_owner_id, resource));
CREATE TABLE IF NOT EXISTS product_features (
  id BIGSERIAL PRIMARY KEY,
  billing_owner_kind VARCHAR NOT NULL DEFAULT 'user',
  billing_owner_id INTEGER NOT NULL,
  flag VARCHAR NOT NULL,
  enabled BOOLEAN DEFAULT FALSE,
  source VARCHAR DEFAULT 'tariff',
  updated_at TIMESTAMP DEFAULT now(),
  CONSTRAINT uq_product_feature UNIQUE (billing_owner_kind, billing_owner_id, flag));
ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS user_id INTEGER;
ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS command_id UUID;
ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS audio_seconds NUMERIC;
