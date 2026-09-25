-- BORIS Phone: make runtime due-queue indexes match actual ORDER BY and partial status predicates.
DROP INDEX IF EXISTS ix_telephony_commands_due;
CREATE INDEX IF NOT EXISTS ix_telephony_commands_due
  ON telephony_commands(created_at, id, next_attempt_at)
  WHERE status IN ('queued','waiting_provider');
DROP INDEX IF EXISTS ix_telephony_push_due;
DROP INDEX IF EXISTS ix_telephony_push_pending;
CREATE INDEX IF NOT EXISTS ix_telephony_push_due
  ON telephony_push_outbox(created_at, id, next_attempt_at)
  WHERE status IN ('pending','retry','waiting_configuration');
DROP INDEX IF EXISTS ix_telephony_targets_due;
CREATE INDEX IF NOT EXISTS ix_telephony_targets_due
  ON telephony_call_targets(expires_at, id, account_id, call_id)
  WHERE status='ringing' AND expires_at IS NOT NULL;
