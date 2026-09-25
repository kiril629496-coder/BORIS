-- BORIS mailbox: durable sent-folder copy lease/backoff; avoids duplicate IMAP APPEND across workers.
ALTER TABLE mailbox_sent_copy_queue ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_mailbox_sent_copy_due
  ON mailbox_sent_copy_queue(created_at,id,next_attempt_at)
  WHERE status='pending';
CREATE INDEX IF NOT EXISTS ix_mailbox_sent_copy_sending_lease
  ON mailbox_sent_copy_queue(updated_at,id)
  WHERE status='sending';
