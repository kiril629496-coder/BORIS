-- Durable mobile push retry scheduling.
BEGIN;
ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_telephony_push_due ON telephony_push_outbox(status,next_attempt_at,created_at,id);
COMMIT;
