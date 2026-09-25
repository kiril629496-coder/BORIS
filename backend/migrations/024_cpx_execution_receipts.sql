-- BORIS CPX: durable exactly-once intent/result ledger for live bid mutations.
-- A request is never blindly retried after provider I/O becomes ambiguous.
CREATE TABLE IF NOT EXISTS cpx_execution_receipts (
  id bigserial PRIMARY KEY,
  account_id text NOT NULL,
  request_key text NOT NULL,
  item_id bigint NOT NULL,
  action text NOT NULL,
  source text,
  status text NOT NULL DEFAULT 'prepared',
  old_bid_penny bigint,
  intended_bid_penny bigint,
  observed_bid_penny bigint,
  provider_http_status integer,
  result_json jsonb,
  error_text text,
  created_at timestamptz NOT NULL DEFAULT now(),
  attempted_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id,request_key)
);
CREATE INDEX IF NOT EXISTS ix_cpx_execution_receipts_reconcile
  ON cpx_execution_receipts(attempted_at,id)
  WHERE status='delivery_unknown';
CREATE INDEX IF NOT EXISTS ix_cpx_execution_receipts_item
  ON cpx_execution_receipts(account_id,item_id,created_at DESC);
