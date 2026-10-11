-- BORIS TEAM INVITE / MEMBERSHIP V1
CREATE TABLE IF NOT EXISTS team_memberships (
  id BIGSERIAL PRIMARY KEY,
  account_id VARCHAR NOT NULL,
  user_id INTEGER NULL REFERENCES users(id),
  name VARCHAR NOT NULL,
  phone VARCHAR NOT NULL,
  phone_normalized VARCHAR NOT NULL,
  email VARCHAR NULL,
  role VARCHAR NOT NULL CHECK (role IN ('OWNER','MANAGER_LEAD','MANAGER','VIEWER')),
  permissions_override JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR NOT NULL CHECK (status IN ('INVITED','ACTIVE','REVOKED')),
  invite_token_hash VARCHAR NULL,
  invite_expires_at TIMESTAMPTZ NULL,
  invite_accepted_at TIMESTAMPTZ NULL,
  last_login_at TIMESTAMPTZ NULL,
  pwa_installed BOOLEAN NULL,
  pwa_standalone_confirmed BOOLEAN NULL,
  push_status VARCHAR NOT NULL DEFAULT 'INACTIVE' CHECK (push_status IN ('ACTIVE','INACTIVE')),
  assigned_chats_count INTEGER NOT NULL DEFAULT 0,
  assigned_tasks_count INTEGER NOT NULL DEFAULT 0,
  created_by_user_id INTEGER NOT NULL REFERENCES users(id),
  revoked_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_memberships_active_phone ON team_memberships(account_id, phone_normalized) WHERE status IN ('INVITED','ACTIVE');
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_memberships_invite_hash ON team_memberships(invite_token_hash) WHERE invite_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_team_memberships_account_status ON team_memberships(account_id, status);
CREATE INDEX IF NOT EXISTS ix_team_memberships_user ON team_memberships(user_id);
