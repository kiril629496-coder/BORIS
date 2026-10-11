-- BORIS Messenger append-only security audit ledger.

CREATE TABLE IF NOT EXISTS messenger_security_audit (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
  account_id VARCHAR(255) NULL,
  action VARCHAR(96) NOT NULL,
  entity_type VARCHAR(64) NULL,
  entity_id VARCHAR(255) NULL,
  session_id VARCHAR(64) NULL,
  request_id VARCHAR(128) NULL,
  result VARCHAR(32) NOT NULL DEFAULT 'ok',
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_messenger_security_audit_user_time
  ON messenger_security_audit(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_messenger_security_audit_account_time
  ON messenger_security_audit(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_messenger_security_audit_action_time
  ON messenger_security_audit(action, created_at DESC);

CREATE OR REPLACE FUNCTION messenger_security_audit_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'messenger_security_audit is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_messenger_security_audit_immutable
  ON messenger_security_audit;
CREATE TRIGGER trg_messenger_security_audit_immutable
BEFORE UPDATE OR DELETE ON messenger_security_audit
FOR EACH ROW EXECUTE FUNCTION messenger_security_audit_immutable();
