-- BORIS Phone minute alert delivery: bounded durable retry/backoff.
ALTER TABLE telephony_minute_alerts ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE telephony_minute_alerts ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_telephony_minute_alert_due
  ON telephony_minute_alerts(status,next_attempt_at,created_at,id)
  WHERE status IN ('created','retry');
