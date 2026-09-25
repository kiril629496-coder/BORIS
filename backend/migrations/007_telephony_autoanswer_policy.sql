-- BORIS Phone autoanswer policy v1.
-- Canonical runtime implementation: app/services/telephony_core.py.
-- Safe/idempotent migration for existing telephony installations.
CREATE TABLE IF NOT EXISTS telephony_afterhours_settings (
  account_id text PRIMARY KEY,
  enabled boolean NOT NULL DEFAULT false,
  timezone text NOT NULL DEFAULT 'Europe/Moscow',
  work_days jsonb NOT NULL DEFAULT '[0,1,2,3,4]'::jsonb,
  work_start text NOT NULL DEFAULT '09:00',
  work_end text NOT NULL DEFAULT '18:00',
  callback_hour smallint NOT NULL DEFAULT 9,
  greeting text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS autoanswer_mode text NOT NULL DEFAULT 'afterhours';
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS no_answer_seconds smallint NOT NULL DEFAULT 25;
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS collect_phone boolean NOT NULL DEFAULT true;
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS collect_topic boolean NOT NULL DEFAULT true;
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS create_crm_task boolean NOT NULL DEFAULT true;
ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS voicemail_text text;

DO $$ BEGIN
  ALTER TABLE telephony_afterhours_settings ADD CONSTRAINT telephony_afterhours_mode_check
    CHECK (autoanswer_mode IN ('off','afterhours','no_answer','always'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_afterhours_settings ADD CONSTRAINT telephony_afterhours_no_answer_check
    CHECK (no_answer_seconds BETWEEN 5 AND 120);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
