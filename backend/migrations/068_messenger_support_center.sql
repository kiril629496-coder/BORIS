ALTER TABLE support_tickets
  ADD COLUMN IF NOT EXISTS internal_conversation_id BIGINT REFERENCES internal_conversations(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS support_category VARCHAR(64),
  ADD COLUMN IF NOT EXISTS priority VARCHAR(8) NOT NULL DEFAULT 'P2',
  ADD COLUMN IF NOT EXISTS module VARCHAR(64),
  ADD COLUMN IF NOT EXISTS context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS first_response_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_client_message_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_support_message_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_support_ticket_internal_conversation
  ON support_tickets(internal_conversation_id)
  WHERE internal_conversation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_support_tickets_owner_inbox
  ON support_tickets(status, priority, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_support_tickets_account_thread
  ON support_tickets(account_id, updated_at DESC);
