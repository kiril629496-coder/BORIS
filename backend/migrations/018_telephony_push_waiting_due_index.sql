-- Push dispatcher also polls waiting_configuration after its retry lease.
-- Keep the partial due index aligned with the runtime status predicate.
DROP INDEX IF EXISTS ix_telephony_push_due;
CREATE INDEX IF NOT EXISTS ix_telephony_push_due
  ON telephony_push_outbox(created_at, id, next_attempt_at)
  WHERE status IN ('pending','retry','waiting_configuration');
