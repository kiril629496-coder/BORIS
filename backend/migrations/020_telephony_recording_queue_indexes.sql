-- BORIS Phone: recording queue indexes aligned with actual due ORDER BY plus stale lease recovery.
DROP INDEX IF EXISTS ix_telephony_recording_download_due;
CREATE INDEX ix_telephony_recording_download_due
  ON telephony_recordings(created_at,id,next_download_at)
  WHERE status IN ('available','download_retry') AND source_url IS NOT NULL AND local_path IS NULL AND download_attempts<5;
CREATE INDEX IF NOT EXISTS ix_telephony_recording_download_lease
  ON telephony_recordings(updated_at,id) WHERE status='downloading';
DROP INDEX IF EXISTS ix_telephony_recording_transcript_due;
CREATE INDEX ix_telephony_recording_transcript_due
  ON telephony_recordings(created_at,id,next_transcript_at)
  WHERE status='available' AND local_path IS NOT NULL AND transcript_status IN ('pending','retry','blocked_entitlement');
CREATE INDEX IF NOT EXISTS ix_telephony_recording_transcript_lease
  ON telephony_recordings(updated_at,id) WHERE transcript_status='processing';
