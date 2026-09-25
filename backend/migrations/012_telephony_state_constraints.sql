-- BORIS Phone: prevent impossible persisted runtime states while preserving provider-neutral extensibility.
DO $$ BEGIN
  ALTER TABLE telephony_calls ADD CONSTRAINT ck_telephony_calls_direction CHECK (direction IN ('inbound','outbound')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_calls ADD CONSTRAINT ck_telephony_calls_state CHECK (state IN ('new','created','ringing','active','on_hold','transferring','ended','missed','rejected','busy','failed','cancelled')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_calls ADD CONSTRAINT ck_telephony_calls_agent_mode CHECK (agent_mode IN ('human','voice_agent','afterhours_agent','fallback_ivr')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_devices ADD CONSTRAINT ck_telephony_devices_presence CHECK (presence IN ('offline','online','free','busy','dnd')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_call_targets ADD CONSTRAINT ck_telephony_targets_status CHECK (status IN ('ringing','answered','rejected','timeout','cancelled','ended')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
