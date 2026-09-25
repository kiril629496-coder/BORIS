-- 038_telephony_media_bridge_binding.sql
-- Durable non-secret binding metadata between a provider channel and temporary browser media leg.
ALTER TABLE telephony_media_sessions
  ADD COLUMN IF NOT EXISTS bridge_id text,
  ADD COLUMN IF NOT EXISTS media_channel_id text,
  ADD COLUMN IF NOT EXISTS activated_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_bridge_error text;

CREATE INDEX IF NOT EXISTS ix_telephony_media_bridge
ON telephony_media_sessions(bridge_id)
WHERE bridge_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_media_channel
ON telephony_media_sessions(media_channel_id)
WHERE media_channel_id IS NOT NULL;
