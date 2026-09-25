-- BORIS CPX feed20 first-bid run-scope hardening.
-- Idempotent; narrows only source='feed20' to exactly one starter bid per account/hour.

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

  -- CPX_FEED20_EXACTLY_ONE_PER_HOUR_V1: natural KPI cadence is hourly and the
  -- owner rule is exactly one first-bid launch per trusted run, <=24/day.
  -- feed20 callers historically generated a fresh microsecond prefix per item,
  -- which defeated max_actions_run=1. Make the DB source of truth group every
  -- f20v3 request in the same UTC hour and clamp only this launch lane to 1.
  IF COALESCE(NEW.source,'')='feed20' THEN
    run_cap := LEAST(run_cap, 1);
  END IF;

  -- CPX_MOSCOW_MONEY_DAY_V1
  SELECT count(*) INTO day_count
  FROM cpx_execution_receipts r
  WHERE r.account_id=NEW.account_id
    AND (r.created_at AT TIME ZONE 'Europe/Moscow')::date
        = (now() AT TIME ZONE 'Europe/Moscow')::date
    AND r.status IN ('prepared','attempting','delivery_unknown','succeeded','reconciled');
  IF day_count>=day_cap THEN
    RAISE EXCEPTION 'blocked_max_actions_day_atomic count=% limit=%',day_count,day_cap USING ERRCODE='P0001';
  END IF;

  -- CPX_ATOMIC_RUN_SCOPE_V3: preserve the canonical run-scope contract while
  -- CPX_FEED20_EXACTLY_ONE_PER_HOUR_V1 narrows feed20 to one launch/hour.
  run_prefix := CASE
    WHEN COALESCE(NEW.source,'')='feed20'
      THEN substring(split_part(COALESCE(NEW.request_key,''),':',1) from '^f20v3_[0-9]{8}T[0-9]{2}')
    WHEN COALESCE(NEW.source,'') IN ('new_feed_hourly_ramp','budget_resume')
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' || split_part(COALESCE(NEW.request_key,''),':',2)
    WHEN COALESCE(NEW.source,'')='kpi_gap_boost'
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' || split_part(COALESCE(NEW.request_key,''),':',2) || ':' || split_part(COALESCE(NEW.request_key,''),':',3)
    WHEN COALESCE(NEW.source,'')='profitable_day_push'
      THEN split_part(COALESCE(NEW.request_key,''),':',1) || ':' || split_part(COALESCE(NEW.request_key,''),':',2) || ':' || split_part(COALESCE(NEW.request_key,''),':',3)
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
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' || split_part(COALESCE(r.request_key,''),':',2)
          WHEN COALESCE(NEW.source,'')='kpi_gap_boost'
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' || split_part(COALESCE(r.request_key,''),':',2) || ':' || split_part(COALESCE(r.request_key,''),':',3)
          WHEN COALESCE(NEW.source,'')='profitable_day_push'
            THEN split_part(COALESCE(r.request_key,''),':',1) || ':' || split_part(COALESCE(r.request_key,''),':',2) || ':' || split_part(COALESCE(r.request_key,''),':',3)
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
