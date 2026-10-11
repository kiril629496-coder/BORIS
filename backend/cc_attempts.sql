ALTER TABLE ai_run_budget ADD COLUMN attempt integer NOT NULL DEFAULT 1;
ALTER TABLE ai_run_budget ADD COLUMN id bigserial;
ALTER TABLE ai_run_budget DROP CONSTRAINT ai_run_budget_pkey;
ALTER TABLE ai_run_budget ADD CONSTRAINT ai_run_budget_pkey PRIMARY KEY (id);
ALTER TABLE ai_run_budget ADD CONSTRAINT uq_arb_run_attempt UNIQUE (run_id, attempt);
CREATE INDEX IF NOT EXISTS ix_arb_run_att ON ai_run_budget (run_id, attempt DESC);
ALTER TABLE ai_run_calls ADD COLUMN IF NOT EXISTS attempt integer NOT NULL DEFAULT 1;
