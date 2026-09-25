-- BORIS CPX receipt pre-provider safety barriers.
-- Idempotent; safe to re-apply after DB restore/migration.

CREATE OR REPLACE FUNCTION boris_cpx_receipt_raise_authority_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- CPX_RECEIPT_STRICT_RAISE_AUTHORITY_V1
  IF NEW.action = 'raise' AND COALESCE(NEW.source,'') <> 'feed20' THEN
    IF NOT EXISTS (
      SELECT 1 FROM money_mandates m
      WHERE NEW.account_id = ANY(m.account_scope)
        AND m.status='active' AND m.revoked_at IS NULL
        AND m.source='paid_tariff_kpi'
        AND 'cpx.raise_bid'=ANY(m.allowed_operations)
        AND (m.valid_from IS NULL OR m.valid_from<=now())
        AND (m.valid_until IS NULL OR m.valid_until>now())
    ) THEN
      RAISE EXCEPTION 'blocked_no_paid_kpi_raise_mandate' USING ERRCODE='P0001';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION boris_cpx_receipt_atomic_capacity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected_source text;
  operation_name text;
  run_cap integer;
  day_cap integer;
  run_prefix text;
  run_count integer;
  day_count integer;
BEGIN
  -- CPX_RECEIPT_ATOMIC_ACTION_CAP_V1
  IF EXISTS (SELECT 1 FROM cpx_execution_receipts r WHERE r.account_id=NEW.account_id AND r.request_key=NEW.request_key) THEN
    RETURN NEW;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('cpx-action-cap:' || NEW.account_id));
  IF EXISTS (SELECT 1 FROM cpx_execution_receipts r WHERE r.account_id=NEW.account_id AND r.request_key=NEW.request_key) THEN
    RETURN NEW;
  END IF;

  expected_source := CASE WHEN COALESCE(NEW.source,'')='feed20' THEN 'new_feed_launch' ELSE 'paid_tariff_kpi' END;
  operation_name := CASE WHEN NEW.action='raise' THEN 'cpx.raise_bid' WHEN NEW.action='lower' THEN 'cpx.lower_bid' ELSE NULL END;
  IF operation_name IS NULL THEN RETURN NEW; END IF;

  SELECT LEAST(COALESCE(m.max_actions_run,5),5), LEAST(COALESCE(m.max_actions_day,120),120)
    INTO run_cap,day_cap
  FROM money_mandates m
  WHERE NEW.account_id=ANY(m.account_scope)
    AND m.status='active' AND m.revoked_at IS NULL
    AND m.source=expected_source
    AND operation_name=ANY(m.allowed_operations)
    AND (m.valid_from IS NULL OR m.valid_from<=now())
    AND (m.valid_until IS NULL OR m.valid_until>now())
  ORDER BY m.id DESC LIMIT 1;

  IF run_cap IS NULL OR day_cap IS NULL THEN
    RAISE EXCEPTION 'blocked_no_matching_money_mandate_atomic' USING ERRCODE='P0001';
  END IF;

  -- CPX_FEED20_EXACTLY_ONE_PER_HOUR_V1: first-bid launch is exactly one
  -- per natural hourly KPI run; daily mandate remains the <=24/day ceiling.
  IF COALESCE(NEW.source,'')='feed20' THEN
    run_cap := LEAST(run_cap, 1);
  END IF;

  -- CPX_MOSCOW_MONEY_DAY_V1: Avito/BORIS commercial day is Moscow time.
  -- The DB host runs UTC, so current_date would keep yesterday's action budget
  -- alive until 03:00 MSK and then reset three hours late.
  SELECT count(*) INTO day_count
  FROM cpx_execution_receipts r
  WHERE r.account_id=NEW.account_id
    AND (r.created_at AT TIME ZONE 'Europe/Moscow')::date
        = (now() AT TIME ZONE 'Europe/Moscow')::date
    AND r.status IN ('prepared','attempting','delivery_unknown','succeeded','reconciled');
  IF day_count>=day_cap THEN
    RAISE EXCEPTION 'blocked_max_actions_day_atomic count=% limit=%',day_count,day_cap USING ERRCODE='P0001';
  END IF;

  -- CPX_ATOMIC_RUN_SCOPE_V3: run capacity is scoped to the real
  -- execution window encoded by each native request key, never to a permanent
  -- action-family prefix. This keeps max_actions_run race-free without turning
  -- "5 per run" into "5 for the lifetime of the account".
  run_prefix := CASE
    WHEN COALESCE(NEW.source,'')='feed20'
      THEN substring(split_part(COALESCE(NEW.request_key,''),':',1) from '^f20v3_[0-9]{8}T[0-9]{2}')
    WHEN COALESCE(NEW.source,'') IN ('new_feed_hourly_ramp','budget_resume')
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' ||
           split_part(COALESCE(NEW.request_key,''),':',2)
    WHEN COALESCE(NEW.source,'')='kpi_gap_boost'
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' ||
           split_part(COALESCE(NEW.request_key,''),':',2) || ':' ||
           split_part(COALESCE(NEW.request_key,''),':',3)
    WHEN COALESCE(NEW.source,'')='profitable_day_push'
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' ||
           split_part(COALESCE(NEW.request_key,''),':',2) || ':' ||
           split_part(COALESCE(NEW.request_key,''),':',3)
    ELSE split_part(COALESCE(NEW.request_key,''),':',1)
  END;

  IF run_prefix<>'' THEN
    SELECT count(*) INTO run_count
    FROM cpx_execution_receipts r
    WHERE r.account_id=NEW.account_id
      AND COALESCE(r.source,'')=COALESCE(NEW.source,'')
      AND (
        CASE
          WHEN COALESCE(NEW.source,'')='feed20'
            THEN substring(split_part(COALESCE(r.request_key,''),':',1) from '^f20v3_[0-9]{8}T[0-9]{2}')
          WHEN COALESCE(NEW.source,'') IN ('new_feed_hourly_ramp','budget_resume')
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' ||
                 split_part(COALESCE(r.request_key,''),':',2)
          WHEN COALESCE(NEW.source,'')='kpi_gap_boost'
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' ||
                 split_part(COALESCE(r.request_key,''),':',2) || ':' ||
                 split_part(COALESCE(r.request_key,''),':',3)
          WHEN COALESCE(NEW.source,'')='profitable_day_push'
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' ||
                 split_part(COALESCE(r.request_key,''),':',2) || ':' ||
                 split_part(COALESCE(r.request_key,''),':',3)
          ELSE split_part(COALESCE(r.request_key,''),':',1)
        END
      )=run_prefix
      AND r.status IN ('prepared','attempting','delivery_unknown','succeeded','reconciled');
    IF run_count>=run_cap THEN
      RAISE EXCEPTION 'blocked_max_actions_run_atomic count=% limit=%',run_count,run_cap USING ERRCODE='P0001';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_cpx_receipt_atomic_action_cap ON cpx_execution_receipts;
CREATE TRIGGER trg_cpx_receipt_atomic_action_cap BEFORE INSERT ON cpx_execution_receipts
FOR EACH ROW EXECUTE FUNCTION boris_cpx_receipt_atomic_capacity_guard();

DROP TRIGGER IF EXISTS trg_cpx_receipt_raise_authority_guard ON cpx_execution_receipts;
CREATE TRIGGER trg_cpx_receipt_raise_authority_guard BEFORE INSERT ON cpx_execution_receipts
FOR EACH ROW EXECUTE FUNCTION boris_cpx_receipt_raise_authority_guard();
