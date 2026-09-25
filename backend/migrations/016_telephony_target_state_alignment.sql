-- Runtime uses `timeout`; align the DB domain and add due-sweep index.
ALTER TABLE telephony_call_targets DROP CONSTRAINT IF EXISTS ck_telephony_targets_status;
ALTER TABLE telephony_call_targets ADD CONSTRAINT ck_telephony_targets_status CHECK (status IN ('ringing','answered','rejected','timeout','cancelled','ended')) NOT VALID;
ALTER TABLE telephony_call_targets VALIDATE CONSTRAINT ck_telephony_targets_status;
CREATE INDEX IF NOT EXISTS ix_telephony_targets_due ON telephony_call_targets(expires_at,account_id,call_id) WHERE status='ringing' AND expires_at IS NOT NULL;
