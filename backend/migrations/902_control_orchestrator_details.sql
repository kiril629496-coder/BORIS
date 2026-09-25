BEGIN;
CREATE TABLE IF NOT EXISTS control_execution_steps (
 id bigserial PRIMARY KEY, execution_id uuid NOT NULL REFERENCES control_executions(id) ON DELETE CASCADE,
 step_no integer NOT NULL, name varchar(160) NOT NULL, status varchar(32) NOT NULL DEFAULT 'pending',
 input_json jsonb NOT NULL DEFAULT '{}'::jsonb, output_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 error_code varchar(96), error_message text, started_at timestamptz, finished_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(execution_id,step_no)
);
CREATE INDEX IF NOT EXISTS ix_control_execution_steps_exec ON control_execution_steps(execution_id);
CREATE INDEX IF NOT EXISTS ix_control_execution_steps_status ON control_execution_steps(status);

CREATE TABLE IF NOT EXISTS control_execution_attempts (
 id bigserial PRIMARY KEY, execution_id uuid NOT NULL REFERENCES control_executions(id) ON DELETE CASCADE,
 step_id bigint REFERENCES control_execution_steps(id) ON DELETE CASCADE, attempt_no integer NOT NULL,
 strategy varchar(96) NOT NULL DEFAULT 'default', status varchar(32) NOT NULL DEFAULT 'started',
 error_code varchar(96), error_message text, result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(execution_id,step_id,attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_control_execution_attempts_exec ON control_execution_attempts(execution_id);
CREATE INDEX IF NOT EXISTS ix_control_execution_attempts_status ON control_execution_attempts(status);

CREATE TABLE IF NOT EXISTS control_rule_conflicts (
 id uuid PRIMARY KEY,
 left_requirement_id uuid NOT NULL REFERENCES control_requirements(id) ON DELETE CASCADE,
 right_requirement_id uuid NOT NULL REFERENCES control_requirements(id) ON DELETE CASCADE,
 field varchar(160) NOT NULL, left_value_json jsonb NOT NULL, right_value_json jsonb NOT NULL,
 state varchar(24) NOT NULL DEFAULT 'open', resolution text,
 detected_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
 UNIQUE(left_requirement_id,right_requirement_id,field)
);
CREATE INDEX IF NOT EXISTS ix_control_rule_conflicts_state ON control_rule_conflicts(state,detected_at DESC);
COMMIT;
