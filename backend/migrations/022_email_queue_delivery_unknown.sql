-- BORIS email queue: fail closed after stale/ambiguous SMTP execution.
ALTER TABLE email_queue DROP CONSTRAINT IF EXISTS ck_email_queue_status;
ALTER TABLE email_queue ADD CONSTRAINT ck_email_queue_status
  CHECK (status IN ('queued','sending','sent','failed','dead','cancelled','expired','delivery_unknown')) NOT VALID;
ALTER TABLE email_queue VALIDATE CONSTRAINT ck_email_queue_status;
CREATE INDEX IF NOT EXISTS ix_email_queue_sending_lease
  ON email_queue(updated_at,id) WHERE status='sending';
