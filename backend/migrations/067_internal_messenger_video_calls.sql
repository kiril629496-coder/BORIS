-- BORIS Messenger V9: additive video assets + WebRTC call signaling.
-- Expands the existing message type contract without rewriting Voice V8.
ALTER TABLE internal_messages DROP CONSTRAINT IF EXISTS internal_messages_type_check;
ALTER TABLE internal_messages
  ADD CONSTRAINT internal_messages_type_check
  CHECK (message_type IN ('text','voice','video','video_note','system')) NOT VALID;
ALTER TABLE internal_messages VALIDATE CONSTRAINT internal_messages_type_check;

CREATE TABLE IF NOT EXISTS internal_video_assets (
 id UUID PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id) ON DELETE CASCADE,
 uploader_user_id INTEGER NOT NULL REFERENCES users(id),
 message_id BIGINT UNIQUE REFERENCES internal_messages(id) ON DELETE SET NULL,
 attachment_id BIGINT UNIQUE REFERENCES internal_message_attachments(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK (kind IN ('video','video_note')),
 original_storage_ref TEXT NOT NULL UNIQUE,
 playback_storage_ref TEXT NOT NULL UNIQUE,
 thumbnail_storage_ref TEXT NOT NULL UNIQUE,
 original_mime TEXT NOT NULL,
 original_size_bytes BIGINT NOT NULL CHECK (original_size_bytes > 0 AND original_size_bytes <= 157286400),
 playback_mime TEXT NOT NULL DEFAULT 'video/mp4',
 playback_size_bytes BIGINT,
 duration_ms INTEGER,
 width INTEGER,
 height INTEGER,
 processing_status TEXT NOT NULL DEFAULT 'queued' CHECK (processing_status IN ('queued','processing','ready','failed')),
 processing_error_code TEXT,
 processing_worker_id TEXT,
 processing_lease_until TIMESTAMPTZ,
 processing_attempts INTEGER NOT NULL DEFAULT 0 CHECK (processing_attempts >= 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 processed_at TIMESTAMPTZ,
 deleted_at TIMESTAMPTZ,
 CHECK ((message_id IS NULL) = (attachment_id IS NULL))
);
CREATE INDEX IF NOT EXISTS internal_video_pending_idx
  ON internal_video_assets(created_at) WHERE message_id IS NULL;
CREATE INDEX IF NOT EXISTS internal_video_account_idx
  ON internal_video_assets(account_id,conversation_id);
CREATE INDEX IF NOT EXISTS internal_video_processing_idx
  ON internal_video_assets(processing_lease_until) WHERE processing_status='processing';

CREATE TABLE IF NOT EXISTS internal_calls (
 id UUID PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id) ON DELETE CASCADE,
 caller_user_id INTEGER NOT NULL REFERENCES users(id),
 peer_user_id INTEGER NOT NULL REFERENCES users(id),
 client_call_id UUID NOT NULL,
 call_type TEXT NOT NULL CHECK (call_type IN ('audio','video')),
 state TEXT NOT NULL CHECK (state IN ('created','ringing','accepted','connecting','connected','reconnecting','ended','missed','declined','busy','failed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 ringing_at TIMESTAMPTZ,
 answered_at TIMESTAMPTZ,
 connected_at TIMESTAMPTZ,
 ended_at TIMESTAMPTZ,
 ended_reason TEXT,
 history_message_id BIGINT UNIQUE REFERENCES internal_messages(id) ON DELETE SET NULL,
 UNIQUE(conversation_id,caller_user_id,client_call_id)
);
CREATE INDEX IF NOT EXISTS internal_calls_active_peer_idx
  ON internal_calls(account_id,peer_user_id,created_at DESC)
  WHERE state IN ('created','ringing','accepted','connecting','connected','reconnecting');
CREATE INDEX IF NOT EXISTS internal_calls_active_caller_idx
  ON internal_calls(account_id,caller_user_id,created_at DESC)
  WHERE state IN ('created','ringing','accepted','connecting','connected','reconnecting');
CREATE INDEX IF NOT EXISTS internal_calls_conversation_idx
  ON internal_calls(conversation_id,created_at DESC);

CREATE TABLE IF NOT EXISTS internal_call_participants (
 call_id UUID NOT NULL REFERENCES internal_calls(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id),
 state TEXT NOT NULL DEFAULT 'invited' CHECK (state IN ('invited','ringing','accepted','connected','left','declined')),
 accepted_session_id TEXT,
 joined_at TIMESTAMPTZ,
 left_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(call_id,user_id)
);

CREATE TABLE IF NOT EXISTS internal_call_events (
 id BIGSERIAL PRIMARY KEY,
 call_id UUID NOT NULL REFERENCES internal_calls(id) ON DELETE CASCADE,
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 seq INTEGER NOT NULL,
 actor_user_id INTEGER NOT NULL REFERENCES users(id),
 event_type TEXT NOT NULL CHECK (event_type IN ('offer','answer','ice','renegotiate','ice_restart','mute','camera','heartbeat')),
 payload_json JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(call_id,seq)
);
CREATE INDEX IF NOT EXISTS internal_call_events_call_idx
  ON internal_call_events(call_id,seq);

-- SDP/ICE are intentionally short-lived. Terminal call handlers also erase
-- signal payloads immediately; this index supports bounded maintenance purge.
CREATE INDEX IF NOT EXISTS internal_call_events_created_idx
  ON internal_call_events(created_at);
