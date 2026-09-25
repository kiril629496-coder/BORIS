-- COMMAND_CENTER_TERMINAL_DB_GUARD_V1
-- Last-line protection: a completed/failed/cancelled/expired command is immutable
-- with respect to lifecycle state.  Payload retention/purge fields may still be
-- updated normally; only illegal status resurrection is rejected.
CREATE OR REPLACE FUNCTION boris_guard_command_terminal_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.status IN ('done','failed','cancelled','expired')
     AND NEW.status IS DISTINCT FROM OLD.status THEN
    RAISE EXCEPTION 'BORIS_COMMAND_TERMINAL_IMMUTABLE: % -> % for %',
      OLD.status, NEW.status, OLD.command_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_boris_command_terminal_status ON boris_commands;
CREATE TRIGGER trg_boris_command_terminal_status
BEFORE UPDATE OF status ON boris_commands
FOR EACH ROW
EXECUTE FUNCTION boris_guard_command_terminal_status();
