-- V8 STT state/lease hardening. Additive over 064/065.
-- ready means a non-empty accepted transcript; no_speech is a terminal non-error outcome.
ALTER TABLE internal_voice_assets
  DROP CONSTRAINT IF EXISTS internal_voice_assets_transcription_status_check;

ALTER TABLE internal_voice_assets
  ADD CONSTRAINT internal_voice_assets_transcription_status_check
  CHECK (transcription_status IN ('none','queued','processing','ready','no_speech','failed')) NOT VALID;

ALTER TABLE internal_voice_assets
  VALIDATE CONSTRAINT internal_voice_assets_transcription_status_check;

ALTER TABLE internal_voice_assets
  ADD COLUMN IF NOT EXISTS transcription_worker_id TEXT,
  ADD COLUMN IF NOT EXISTS transcription_lease_until TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS transcription_attempts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE internal_voice_assets
  DROP CONSTRAINT IF EXISTS internal_voice_assets_transcription_attempts_check;

ALTER TABLE internal_voice_assets
  ADD CONSTRAINT internal_voice_assets_transcription_attempts_check
  CHECK (transcription_attempts >= 0) NOT VALID;

ALTER TABLE internal_voice_assets
  VALIDATE CONSTRAINT internal_voice_assets_transcription_attempts_check;

CREATE INDEX IF NOT EXISTS internal_voice_processing_lease_idx
  ON internal_voice_assets(transcription_lease_until)
  WHERE transcription_status='processing';
