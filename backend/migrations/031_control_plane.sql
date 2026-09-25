BEGIN;
CREATE TABLE IF NOT EXISTS control_requirements (
 id uuid PRIMARY KEY, key varchar(160) NOT NULL UNIQUE, title varchar(500) NOT NULL,
 raw_owner_text text, normalized_rule text NOT NULL, requirement_type varchar(32) NOT NULL,
 scope_type varchar(24) NOT NULL DEFAULT 'global', scope_id varchar(255) NOT NULL DEFAULT '*',
 module varchar(64) NOT NULL DEFAULT 'system', priority varchar(16) NOT NULL DEFAULT 'normal',
 status varchar(24) NOT NULL DEFAULT 'active', desired_state_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 conditions_json jsonb NOT NULL DEFAULT '[]'::jsonb, actions_json jsonb NOT NULL DEFAULT '[]'::jsonb,
 constraints_json jsonb NOT NULL DEFAULT '[]'::jsonb, verification_json jsonb NOT NULL DEFAULT '[]'::jsonb,
 dependencies_json jsonb NOT NULL DEFAULT '[]'::jsonb, source_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 version integer NOT NULL DEFAULT 1, superseded_by uuid, created_by integer,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 last_applied_at timestamptz, last_verified_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_control_req_scope_module ON control_requirements(scope_type,scope_id,module,status);
CREATE INDEX IF NOT EXISTS ix_control_req_type ON control_requirements(requirement_type);
CREATE INDEX IF NOT EXISTS ix_control_req_priority ON control_requirements(priority);

CREATE TABLE IF NOT EXISTS control_requirement_versions (
 id bigserial PRIMARY KEY, requirement_id uuid NOT NULL REFERENCES control_requirements(id) ON DELETE CASCADE,
 version integer NOT NULL, snapshot_json jsonb NOT NULL, change_reason text, changed_by integer,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(requirement_id,version)
);
CREATE INDEX IF NOT EXISTS ix_control_req_versions_req ON control_requirement_versions(requirement_id);

CREATE TABLE IF NOT EXISTS control_executions (
 id uuid PRIMARY KEY, requirement_id uuid NOT NULL REFERENCES control_requirements(id) ON DELETE CASCADE,
 correlation_id varchar(64) NOT NULL, account_id varchar(255), module varchar(64) NOT NULL,
 status varchar(32) NOT NULL DEFAULT 'received', plan_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 actual_state_json jsonb NOT NULL DEFAULT '{}'::jsonb, attempt integer NOT NULL DEFAULT 0,
 max_attempts integer NOT NULL DEFAULT 3, idempotency_key varchar(160) NOT NULL UNIQUE,
 block_reason text, error_code varchar(96), error_message text, started_at timestamptz,
 finished_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_control_exec_req ON control_executions(requirement_id);
CREATE INDEX IF NOT EXISTS ix_control_exec_status ON control_executions(status);
CREATE INDEX IF NOT EXISTS ix_control_exec_module ON control_executions(module);
CREATE INDEX IF NOT EXISTS ix_control_exec_account ON control_executions(account_id);
CREATE INDEX IF NOT EXISTS ix_control_exec_correlation ON control_executions(correlation_id);

CREATE TABLE IF NOT EXISTS control_evidence (
 id bigserial PRIMARY KEY, execution_id uuid NOT NULL REFERENCES control_executions(id) ON DELETE CASCADE,
 requirement_id uuid NOT NULL REFERENCES control_requirements(id) ON DELETE CASCADE,
 level varchar(8) NOT NULL, evidence_type varchar(48) NOT NULL, source varchar(96) NOT NULL,
 passed boolean NOT NULL DEFAULT false, payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_control_evidence_exec ON control_evidence(execution_id);
CREATE INDEX IF NOT EXISTS ix_control_evidence_req ON control_evidence(requirement_id);
CREATE INDEX IF NOT EXISTS ix_control_evidence_level ON control_evidence(level,passed);

CREATE TABLE IF NOT EXISTS control_incidents (
 id uuid PRIMARY KEY, requirement_id uuid REFERENCES control_requirements(id) ON DELETE SET NULL,
 execution_id uuid REFERENCES control_executions(id) ON DELETE SET NULL, incident_type varchar(64) NOT NULL,
 priority varchar(8) NOT NULL DEFAULT 'P2', state varchar(24) NOT NULL DEFAULT 'open',
 module varchar(64) NOT NULL DEFAULT 'system', account_id varchar(255), symptom text NOT NULL,
 root_cause text NOT NULL DEFAULT 'unknown', remediation text, prevention text,
 details_json jsonb NOT NULL DEFAULT '{}'::jsonb, opened_at timestamptz NOT NULL DEFAULT now(),
 resolved_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_control_inc_state ON control_incidents(state,priority);
CREATE INDEX IF NOT EXISTS ix_control_inc_req ON control_incidents(requirement_id);
CREATE INDEX IF NOT EXISTS ix_control_inc_exec ON control_incidents(execution_id);
CREATE INDEX IF NOT EXISTS ix_control_inc_module_account ON control_incidents(module,account_id);

CREATE TABLE IF NOT EXISTS control_module_contracts (
 id bigserial PRIMARY KEY, module varchar(64) NOT NULL UNIQUE, enabled boolean NOT NULL DEFAULT true,
 contract_json jsonb NOT NULL DEFAULT '{}'::jsonb, capabilities_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 verification_stale_seconds integer NOT NULL DEFAULT 3600, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
