ALTER TABLE internal_calls
  ALTER COLUMN peer_user_id DROP NOT NULL;

ALTER TABLE internal_calls
  ADD COLUMN IF NOT EXISTS call_mode TEXT NOT NULL DEFAULT 'direct';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='internal_calls'::regclass
      AND conname='internal_calls_call_mode_check'
  ) THEN
    ALTER TABLE internal_calls
      ADD CONSTRAINT internal_calls_call_mode_check
      CHECK (call_mode IN ('direct','group'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_internal_calls_active_mode_account
  ON internal_calls(account_id,call_mode,state,created_at DESC);

CREATE INDEX IF NOT EXISTS ix_internal_call_participants_user_state
  ON internal_call_participants(user_id,state,updated_at DESC);
