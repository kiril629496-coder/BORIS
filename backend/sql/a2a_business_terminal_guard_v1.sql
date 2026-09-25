-- BUSINESS_GOAL_TERMINAL_ORDER_DB_GUARD_V1
-- Durable database boundary: stale producers may not resurrect development
-- work after a client business result has been confirmed. Reopening after a
-- real regression is owned by fresh business-truth reconciliation, not INSERT.
CREATE OR REPLACE FUNCTION boris_guard_a2a_business_terminal_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
      SELECT 1 FROM ext_a2a_orders o
       WHERE o.dev_job_id = NEW.dev_job_id
         AND o.blocked_reason = 'business_goal_already_confirmed'
  ) THEN
    RAISE EXCEPTION 'BORIS_A2A_GUARD: business goal already confirmed for dev_job_id=%', NEW.dev_job_id
      USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_a2a_business_terminal_v1 ON ext_a2a_orders;
CREATE TRIGGER trg_boris_a2a_business_terminal_v1
BEFORE INSERT ON ext_a2a_orders
FOR EACH ROW EXECUTE FUNCTION boris_guard_a2a_business_terminal_v1();
