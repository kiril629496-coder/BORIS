-- Additive V8 migration. Existing attachments remain the media storage contract.
ALTER TABLE internal_messages ADD COLUMN IF NOT EXISTS message_type TEXT NOT NULL DEFAULT 'text';
ALTER TABLE internal_messages ADD CONSTRAINT internal_messages_type_check CHECK (message_type IN ('text','voice','system'));
CREATE TABLE internal_voice_assets (
 id UUID PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 conversation_id BIGINT NOT NULL REFERENCES internal_conversations(id),
 uploader_user_id INTEGER NOT NULL REFERENCES users(id),
 message_id BIGINT UNIQUE REFERENCES internal_messages(id),
 attachment_id BIGINT UNIQUE REFERENCES internal_message_attachments(id),
 original_storage_ref TEXT NOT NULL UNIQUE,
 playback_storage_ref TEXT NOT NULL UNIQUE,
 duration_ms INTEGER NOT NULL CHECK (duration_ms > 0 AND duration_ms <= 600050),
 codec TEXT NOT NULL,
 mime TEXT NOT NULL,
 size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
 original_size_bytes BIGINT NOT NULL CHECK (original_size_bytes > 0 AND original_size_bytes <= 26214400),
 waveform_peaks JSONB NOT NULL DEFAULT '[]',
 transcription_status TEXT NOT NULL DEFAULT 'none' CHECK (transcription_status IN ('none','queued','processing','ready','failed')),
 transcription_text TEXT,
 transcription_language TEXT,
 transcription_engine TEXT NOT NULL DEFAULT 'faster-whisper',
 transcription_model TEXT NOT NULL,
 transcription_segments JSONB NOT NULL DEFAULT '[]',
 transcription_started_at TIMESTAMPTZ,
 transcription_completed_at TIMESTAMPTZ,
 transcription_error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 deleted_at TIMESTAMPTZ,
 CHECK ((message_id IS NULL) = (attachment_id IS NULL))
);
CREATE INDEX internal_voice_pending_idx ON internal_voice_assets(created_at) WHERE message_id IS NULL;
CREATE INDEX internal_voice_account_idx ON internal_voice_assets(account_id,conversation_id);
-- Jobs use background_jobs with kind=messenger_voice_transcription and a unique
-- fingerprint (asset UUID + immutable model version). Dedicated worker owns kind;
-- general API/background slots do not import/load Whisper.
