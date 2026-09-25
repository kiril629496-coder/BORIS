-- BORIS_KPI_MONEY_RECONCILE_DB_V2_MAX150
-- CONTROL_PLANE_SAFETY_CAP: Control Plane is the source of truth for the absolute autonomous cap.
CREATE OR REPLACE FUNCTION boris_reconcile_money_from_kpi_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cfg jsonb; budget numeric; bidcap numeric; safetycap numeric; latest_id bigint;
BEGIN
  IF NEW.key <> 'kpi_settings' THEN RETURN NEW; END IF;
  SELECT max(id) INTO latest_id FROM storage WHERE account_id=NEW.account_id AND key='kpi_settings';
  IF latest_id IS DISTINCT FROM NEW.id THEN RETURN NEW; END IF;
  SELECT NULLIF(desired_state_json->>'hard_max_bid_rub','')::numeric INTO safetycap
    FROM control_requirements WHERE key='system.marketing.absolute_autonomous_bid_cap' AND status='active' LIMIT 1;
  IF safetycap IS NULL OR safetycap<=0 THEN
    RAISE EXCEPTION 'BORIS_MONEY_GUARD: CONTROL_PLANE_SAFETY_CAP missing or invalid';
  END IF;
  BEGIN cfg := COALESCE(NEW.value,'{}')::jsonb; EXCEPTION WHEN others THEN cfg := '{}'::jsonb; END;
  BEGIN budget := COALESCE(NULLIF(cfg->>'daily_budget_limit_rub','')::numeric,0); EXCEPTION WHEN others THEN budget := 0; END;
  BEGIN bidcap := COALESCE(NULLIF(cfg->>'hard_max_bid_rub','')::numeric,safetycap); EXCEPTION WHEN others THEN bidcap := safetycap; END;
  bidcap := LEAST(GREATEST(bidcap,0),safetycap);
  IF budget <= 0 THEN
    UPDATE money_mandates SET status='revoked', revoked_at=now(), revoked_by=NULL, version=version+1
     WHERE NEW.account_id=ANY(account_scope) AND status='active' AND revoked_at IS NULL
       AND 'cpx.raise_bid'=ANY(allowed_operations);
  ELSE
    UPDATE money_mandates SET daily_budget_rub=LEAST(COALESCE(daily_budget_rub,budget),budget),
           max_bid_rub=LEAST(COALESCE(max_bid_rub,bidcap),bidcap,safetycap),
           max_bid_delta_pct=LEAST(COALESCE(max_bid_delta_pct,10),10),
           max_actions_run=LEAST(COALESCE(max_actions_run,5),5),
           max_actions_day=LEAST(COALESCE(max_actions_day,120),120),version=version+1
     WHERE NEW.account_id=ANY(account_scope) AND status='active' AND revoked_at IS NULL
       AND 'cpx.raise_bid'=ANY(allowed_operations);
    UPDATE money_mandates SET max_actions_run=LEAST(max_actions_run,1),max_actions_day=LEAST(max_actions_day,24),version=version+1
     WHERE NEW.account_id=ANY(account_scope) AND status='active' AND revoked_at IS NULL
       AND source='new_feed_launch' AND 'cpx.raise_bid'=ANY(allowed_operations);
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_reconcile_money_from_kpi_v1 ON storage;
CREATE TRIGGER trg_boris_reconcile_money_from_kpi_v1 AFTER INSERT OR UPDATE OF value ON storage
FOR EACH ROW WHEN (NEW.key='kpi_settings') EXECUTE FUNCTION boris_reconcile_money_from_kpi_v1();
