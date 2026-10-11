-- One accepted message per user/conversation/client UUID, even after a lost response.
-- Receipts live for the lifetime of the conversation, not a short retry window.
CREATE TABLE IF NOT EXISTS internal_message_receipts (
 conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id),
 client_message_id UUID NOT NULL,
 request_hash TEXT NOT NULL,
 response_json JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (conversation_id,user_id,client_message_id)
);
