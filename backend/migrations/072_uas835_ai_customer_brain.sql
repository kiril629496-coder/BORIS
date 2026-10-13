BEGIN;
CREATE TABLE IF NOT EXISTS mop_customer_chat_identity (account_id varchar NOT NULL, avito_chat_id varchar NOT NULL, customer_key varchar(64) NOT NULL, identity_source varchar(32) NOT NULL, confidence smallint NOT NULL DEFAULT 0, first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (account_id,avito_chat_id));
CREATE INDEX IF NOT EXISTS ix_mop_customer_identity_customer ON mop_customer_chat_identity(account_id,customer_key,last_seen_at DESC);
CREATE TABLE IF NOT EXISTS mop_customer_memory (
  id bigserial PRIMARY KEY,
  account_id varchar NOT NULL,
  customer_key varchar(64) NOT NULL,
  memory_key varchar(120) NOT NULL,
  value_json jsonb NOT NULL,
  source_type varchar(40) NOT NULL,
  source_ref varchar(240),
  confidence smallint NOT NULL DEFAULT 0,
  status varchar(24) NOT NULL DEFAULT 'confirmed',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz,
  UNIQUE(account_id,customer_key,memory_key)
);
CREATE INDEX IF NOT EXISTS ix_mop_customer_memory_lookup
  ON mop_customer_memory(account_id,customer_key,status,last_seen_at DESC);

CREATE TABLE IF NOT EXISTS mop_ai_quality_events (
  id bigserial PRIMARY KEY,
  account_id varchar NOT NULL,
  avito_chat_id varchar,
  customer_key varchar(64),
  draft_id bigint,
  event_type varchar(48) NOT NULL,
  severity varchar(16) NOT NULL DEFAULT 'info',
  score smallint,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mop_ai_quality_account_time
  ON mop_ai_quality_events(account_id,created_at DESC);

COMMIT;
