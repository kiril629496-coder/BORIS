-- BORIS Phone minute alert crash-lease recovery.
CREATE INDEX IF NOT EXISTS ix_telephony_minute_alert_sending_lease
  ON telephony_minute_alerts(updated_at,id)
  WHERE status='sending';
