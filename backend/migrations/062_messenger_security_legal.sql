-- BORIS MESSENGER SECURITY + LEGAL V1
-- Additive/backward-safe: per-user legal acceptance evidence and revocable auth sessions.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS security_epoch INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS auth_user_sessions (
  session_id VARCHAR(64) PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  security_epoch INTEGER NOT NULL DEFAULT 0,
  user_agent VARCHAR(500) NULL,
  ip_hash CHAR(64) NULL,
  device_label VARCHAR(160) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ NULL,
  revoked_reason VARCHAR(160) NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_user_sessions_user_active
  ON auth_user_sessions(user_id, revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS ix_auth_user_sessions_last_seen
  ON auth_user_sessions(last_seen_at);

CREATE TABLE IF NOT EXISTS messenger_legal_acceptances (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  document_type VARCHAR(32) NOT NULL,
  document_version VARCHAR(32) NOT NULL,
  document_hash CHAR(64) NOT NULL,
  document_title VARCHAR(240) NOT NULL,
  document_snapshot TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip_hash CHAR(64) NULL,
  user_agent VARCHAR(500) NULL,
  session_id VARCHAR(64) NULL,
  CONSTRAINT uq_messenger_legal_acceptance
    UNIQUE(user_id, document_type, document_version, document_hash)
);
CREATE INDEX IF NOT EXISTS ix_messenger_legal_acceptances_user
  ON messenger_legal_acceptances(user_id, document_type, accepted_at DESC);
