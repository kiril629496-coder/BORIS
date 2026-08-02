ALTER TABLE email_queue ADD COLUMN IF NOT EXISTS ref_type   VARCHAR(32);
ALTER TABLE email_queue ADD COLUMN IF NOT EXISTS ref_id     VARCHAR(64);
ALTER TABLE email_queue ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;
ALTER TABLE email_queue DROP CONSTRAINT IF EXISTS ck_email_queue_status;
ALTER TABLE email_queue ADD CONSTRAINT ck_email_queue_status
    CHECK (status IN ('queued','sending','sent','failed','dead','cancelled','expired'));
CREATE INDEX IF NOT EXISTS ix_email_queue_ref ON email_queue (ref_type, ref_id);
