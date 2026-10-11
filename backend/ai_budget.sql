CREATE TABLE IF NOT EXISTS ai_run_budget (
  run_id             text PRIMARY KEY,
  billing_owner_kind varchar(16) NOT NULL,
  billing_owner_id   integer     NOT NULL,
  resource           varchar     NOT NULL,
  module             varchar(64),
  operation          varchar(128),
  status             varchar(16) NOT NULL DEFAULT 'reserved',
  reserved_kopeks    integer     NOT NULL DEFAULT 0,
  committed_kopeks   integer     NOT NULL DEFAULT 0,
  calls_planned      integer     NOT NULL DEFAULT 1,
  calls_used         integer     NOT NULL DEFAULT 0,
  stop_reason        text,
  override_reason    text,
  created_at         timestamp   NOT NULL DEFAULT now(),
  updated_at         timestamp   NOT NULL DEFAULT now(),
  expires_at         timestamp   NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_arb_scope ON ai_run_budget
  (billing_owner_kind, billing_owner_id, resource, status);
CREATE INDEX IF NOT EXISTS ix_arb_ttl ON ai_run_budget (status, expires_at);

CREATE TABLE IF NOT EXISTS ai_run_calls (
  request_id  varchar(64) PRIMARY KEY,
  run_id      text        NOT NULL,
  status      varchar(16) NOT NULL DEFAULT 'pending',
  cost_kopeks integer     NOT NULL DEFAULT 0,
  created_at  timestamp   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_arc_run ON ai_run_calls (run_id);
