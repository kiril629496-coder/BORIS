-- BORIS INTERNAL MESSENGER V1
-- Additive/backward-safe: creates only new tenant-scoped tables and indexes.
CREATE TABLE IF NOT EXISTS internal_conversations (
  id BIGSERIAL PRIMARY KEY,
  account_id VARCHAR NOT NULL,
  kind VARCHAR NOT NULL CHECK (kind IN ('DM','GROUP')),
  title VARCHAR NULL,
  created_by_user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_internal_conversations_account_updated ON internal_conversations(account_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS internal_conversation_members (
  conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR NOT NULL CHECK (role IN ('OWNER','ADMIN','MEMBER')),
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_read_message_id BIGINT NULL,
  PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_internal_conversation_members_user ON internal_conversation_members(user_id, conversation_id);
CREATE TABLE IF NOT EXISTS internal_messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id) ON DELETE CASCADE,
  sender_user_id INTEGER NOT NULL REFERENCES users(id),
  body TEXT NOT NULL DEFAULT '',
  reply_to_message_id BIGINT NULL REFERENCES internal_messages(id),
  edited_at TIMESTAMPTZ NULL,
  deleted_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_internal_messages_conversation_created ON internal_messages(conversation_id, created_at, id);
CREATE TABLE IF NOT EXISTS internal_message_reactions (
  message_id BIGINT NOT NULL REFERENCES internal_messages(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reaction VARCHAR(32) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, user_id, reaction)
);
CREATE TABLE IF NOT EXISTS internal_message_pins (
  message_id BIGINT PRIMARY KEY REFERENCES internal_messages(id) ON DELETE CASCADE,
  pinned_by_user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS internal_message_attachments (
  id BIGSERIAL PRIMARY KEY,
  message_id BIGINT NOT NULL REFERENCES internal_messages(id) ON DELETE CASCADE,
  file_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(128) NOT NULL,
  storage_ref TEXT NOT NULL,
  size_bytes BIGINT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_internal_message_attachments_message ON internal_message_attachments(message_id);
