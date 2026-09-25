-- CONTROL_PLANE_KPI_HARD_CAP_DB_GUARD_V1
CREATE OR REPLACE FUNCTION boris_enforce_control_plane_kpi_hard_cap_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  desired_cap numeric;
  cfg jsonb;
BEGIN
  IF NEW.key <> 'kpi_settings' THEN
    RETURN NEW;
  END IF;
  SELECT NULLIF(desired_state_json->>'hard_max_bid_rub','')::numeric
    INTO desired_cap
    FROM control_requirements
   WHERE status='active'
     AND module='marketing'
     AND scope_type='account'
     AND scope_id=NEW.account_id
     AND desired_state_json ? 'hard_max_bid_rub'
   ORDER BY version DESC, updated_at DESC NULLS LAST, created_at DESC
   LIMIT 1;
  IF desired_cap IS NOT NULL AND desired_cap > 0 THEN
    BEGIN
      cfg := COALESCE(NULLIF(NEW.value,''),'{}')::jsonb;
    EXCEPTION WHEN others THEN
      cfg := '{}'::jsonb;
    END;
    cfg := jsonb_set(cfg,'{hard_max_bid_rub}',to_jsonb(desired_cap),true);
    NEW.value := cfg::text;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_control_plane_kpi_hard_cap_v1 ON storage;
CREATE TRIGGER trg_boris_control_plane_kpi_hard_cap_v1
BEFORE INSERT OR UPDATE OF value ON storage
FOR EACH ROW WHEN (NEW.key='kpi_settings')
EXECUTE FUNCTION boris_enforce_control_plane_kpi_hard_cap_v1();
