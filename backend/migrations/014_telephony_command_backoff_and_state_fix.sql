-- BORIS Phone: correct queue state contracts and stop waiting-provider hot polling.
ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
DROP INDEX IF EXISTS ix_telephony_commands_queued;
CREATE INDEX IF NOT EXISTS ix_telephony_commands_due
  ON telephony_commands(status,next_attempt_at,created_at,id)
  WHERE status IN ('queued','waiting_provider');
ALTER TABLE telephony_commands DROP CONSTRAINT IF EXISTS ck_telephony_commands_status;
ALTER TABLE telephony_commands ADD CONSTRAINT ck_telephony_commands_status
  CHECK (status IN ('queued','waiting_provider','processing','sent','done','failed','cancelled')) NOT VALID;
ALTER TABLE telephony_push_outbox DROP CONSTRAINT IF EXISTS ck_telephony_push_status;
ALTER TABLE telephony_push_outbox ADD CONSTRAINT ck_telephony_push_status
  CHECK (status IN ('pending','retry','waiting_configuration','sent','failed','cancelled')) NOT VALID;
ALTER TABLE telephony_commands VALIDATE CONSTRAINT ck_telephony_commands_status;
ALTER TABLE telephony_push_outbox VALIDATE CONSTRAINT ck_telephony_push_status;
