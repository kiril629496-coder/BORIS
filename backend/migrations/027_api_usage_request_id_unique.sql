-- Financial idempotency: one provider request id must map to one BORIS usage row.
-- PostgreSQL UNIQUE still permits multiple NULL request_id values.
CREATE UNIQUE INDEX IF NOT EXISTS ux_api_usage_request_id
  ON api_usage(request_id);
