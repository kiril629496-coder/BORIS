-- BORIS Phone: distinguish provider acceptance from actual authenticated device receipt.
ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS device_received_at timestamptz;
ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS device_received_via text;
CREATE INDEX IF NOT EXISTS ix_telephony_push_device_received
  ON telephony_push_outbox(account_id,device_id,device_received_at DESC)
  WHERE device_received_at IS NOT NULL;
