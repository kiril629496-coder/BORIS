-- BORIS Phone Voice Agent policy durability v1.
-- Canonical runtime implementation remains app/services/telephony_core.py.
CREATE TABLE IF NOT EXISTS telephony_voice_agent_settings (
  account_id text PRIMARY KEY,
  enabled boolean NOT NULL DEFAULT false,
  afterhours_enabled boolean NOT NULL DEFAULT true,
  voice_name text NOT NULL DEFAULT 'default',
  language text NOT NULL DEFAULT 'ru',
  max_call_minutes smallint NOT NULL DEFAULT 15,
  allow_prices boolean NOT NULL DEFAULT false,
  allow_commitments boolean NOT NULL DEFAULT false,
  allow_crm_write boolean NOT NULL DEFAULT true,
  handoff_on_unknown boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE telephony_voice_agent_settings ADD COLUMN IF NOT EXISTS disclosure_required boolean NOT NULL DEFAULT true;
ALTER TABLE telephony_voice_agent_settings ADD COLUMN IF NOT EXISTS disclosure_text text NOT NULL DEFAULT 'Здравствуйте! Вам отвечает виртуальный помощник BORIS.';
DO $$ BEGIN
  ALTER TABLE telephony_voice_agent_settings ADD CONSTRAINT telephony_voice_agent_duration_check CHECK (max_call_minutes BETWEEN 1 AND 60);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS telephony_voice_agent_sessions (
  id bigserial PRIMARY KEY,
  account_id text NOT NULL,
  call_id text NOT NULL,
  mode text NOT NULL,
  state text NOT NULL DEFAULT 'active',
  customer_phone text,
  qualification text,
  topic text,
  summary text,
  next_action text,
  handoff_reason text,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(account_id,call_id)
);
ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS disclosure_played_at timestamptz;
ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS callback_phone text;
ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS callback_phone_confirmed boolean NOT NULL DEFAULT false;
ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS preferred_contact_time text;
