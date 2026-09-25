-- BORIS outreach reply alerts: durable fail-closed execution lease.
ALTER TABLE prospect_reply_alerts
  ADD COLUMN IF NOT EXISTS attempted_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_prospect_reply_alert_pending
  ON prospect_reply_alerts(created_at,id)
  WHERE status='pending';

CREATE INDEX IF NOT EXISTS ix_prospect_reply_alert_sending
  ON prospect_reply_alerts(attempted_at,id)
  WHERE status='sending';
