-- BORIS Phone: bounded STT retry/backoff and crash recovery for recording pipeline.
ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS transcript_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS next_transcript_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_telephony_recording_download_due
  ON telephony_recordings(updated_at,id,next_download_at)
  WHERE status IN ('available','download_retry','downloading');
CREATE INDEX IF NOT EXISTS ix_telephony_recording_transcript_due
  ON telephony_recordings(updated_at,id,next_transcript_at)
  WHERE status='available' AND transcript_status IN ('pending','retry','processing','blocked_entitlement');
