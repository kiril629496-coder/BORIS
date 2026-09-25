-- BORIS Phone runtime hot-path indexes. Idempotent and safe on current small tables.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_telephony_events_account_id ON telephony_events(account_id,id);
CREATE INDEX IF NOT EXISTS ix_telephony_devices_online ON telephony_devices(account_id,last_seen_at DESC) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_telephony_targets_device_call ON telephony_call_targets(account_id,device_id,call_id,created_at DESC);
CREATE INDEX IF NOT EXISTS ix_telephony_calls_device_state ON telephony_calls(account_id,device_id,state) WHERE device_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_telephony_calls_device_answered ON telephony_calls(account_id,device_id,answered_at DESC) WHERE device_id IS NOT NULL AND answered_at IS NOT NULL;
COMMIT;
