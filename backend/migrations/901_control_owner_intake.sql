BEGIN;
CREATE TABLE IF NOT EXISTS control_owner_instructions (
 id uuid PRIMARY KEY,
 idempotency_key varchar(160) NOT NULL UNIQUE,
 user_id integer,
 source varchar(32) NOT NULL DEFAULT 'command_center',
 source_ref varchar(160),
 account_id varchar(255),
 raw_text text NOT NULL,
 intent varchar(32) NOT NULL,
 requirement_type varchar(32),
 scope_type varchar(24) NOT NULL DEFAULT 'global',
 scope_id varchar(255) NOT NULL DEFAULT '*',
 modules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
 linked_requirement_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
 status varchar(24) NOT NULL DEFAULT 'captured',
 compiler_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_control_owner_intent ON control_owner_instructions(intent,created_at DESC);
CREATE INDEX IF NOT EXISTS ix_control_owner_account ON control_owner_instructions(account_id,created_at DESC);
CREATE INDEX IF NOT EXISTS ix_control_owner_status ON control_owner_instructions(status,created_at DESC);
COMMIT;
