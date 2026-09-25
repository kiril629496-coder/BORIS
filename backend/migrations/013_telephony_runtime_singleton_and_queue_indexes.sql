-- BORIS Phone runtime: queue hot paths. Singleton ownership is enforced by PostgreSQL advisory lock in runner.
CREATE INDEX IF NOT EXISTS ix_telephony_commands_queued ON telephony_commands(status,created_at,id) WHERE status='queued';
DO $$ BEGIN
  ALTER TABLE telephony_commands ADD CONSTRAINT ck_telephony_commands_status CHECK (status IN ('queued','waiting_provider','processing','sent','done','failed','cancelled')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_push_outbox ADD CONSTRAINT ck_telephony_push_status CHECK (status IN ('pending','retry','waiting_configuration','sent','failed','cancelled')) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
