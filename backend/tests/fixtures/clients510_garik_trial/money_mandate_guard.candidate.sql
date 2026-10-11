CREATE OR REPLACE FUNCTION public.boris_guard_money_mandate_v1()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  acc text; cfg_budget numeric; cfg_hard_cap numeric; safetycap numeric; is_launch boolean;
  billing_json jsonb; payment_json jsonb; paid_until timestamptz; paid_at timestamptz;
  period_days integer; entitlement_active boolean;
  trial_json jsonb; trial_started timestamptz; trial_until timestamptz;
  trial_iso_pattern constant text := '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{1,6})?(Z|[+-](0[0-9]|1[0-5]):[0-5][0-9])$';
BEGIN
  IF NEW.status='active' AND NEW.revoked_at IS NULL
     AND 'cpx.raise_bid'=ANY(COALESCE(NEW.allowed_operations,ARRAY[]::text[])) THEN
    SELECT NULLIF(desired_state_json->>'hard_max_bid_rub','')::numeric INTO safetycap
      FROM control_requirements WHERE key='system.marketing.absolute_autonomous_bid_cap' AND status='active' LIMIT 1;
    IF safetycap IS NULL OR safetycap<=0 THEN
      RAISE EXCEPTION 'BORIS_MONEY_GUARD: CONTROL_PLANE_SAFETY_CAP missing or invalid';
    END IF;
    IF NEW.account_scope IS NULL OR cardinality(NEW.account_scope)=0 THEN
      RAISE EXCEPTION 'BORIS_MONEY_GUARD: raise mandate requires account scope';
    END IF;
    is_launch := NEW.source='new_feed_launch';
    IF is_launch AND cardinality(NEW.account_scope)<>1 THEN
      RAISE EXCEPTION 'BORIS_MONEY_GUARD: feed launch must be single-account scoped';
    END IF;
    FOREACH acc IN ARRAY NEW.account_scope LOOP
      -- BORIS_MONEY_PAID_ENTITLEMENT_DB_GUARD_V1: product scope/unlimited is not
      -- current service-period evidence. Automatic paid-tariff raise authority
      -- requires the same canonical paid-period truth as marketing_service_entitlement.
      IF NEW.source IN ('paid_tariff_kpi','new_feed_launch') THEN
        billing_json:=NULL; payment_json:=NULL; paid_until:=NULL; paid_at:=NULL;
        period_days:=0; entitlement_active:=FALSE;
        SELECT value::jsonb INTO billing_json
          FROM storage WHERE account_id=acc AND key='billing' ORDER BY id DESC LIMIT 1;
        SELECT value::jsonb INTO payment_json
          FROM storage WHERE account_id=acc AND key='payment_status' ORDER BY id DESC LIMIT 1;
        IF billing_json IS NOT NULL AND NULLIF(billing_json->>'paid_until','') IS NOT NULL THEN
          BEGIN paid_until:=(billing_json->>'paid_until')::timestamptz;
          EXCEPTION WHEN others THEN paid_until:=NULL; END;
          entitlement_active:=COALESCE(paid_until>now(),FALSE);
        ELSE
          IF payment_json IS NOT NULL AND NULLIF(payment_json->>'paid_at','') IS NOT NULL THEN
            BEGIN paid_at:=(payment_json->>'paid_at')::timestamptz;
            EXCEPTION WHEN others THEN paid_at:=NULL; END;
          END IF;
          BEGIN period_days:=COALESCE(NULLIF(payment_json->>'period_days','')::integer,0);
          EXCEPTION WHEN others THEN period_days:=0; END;
          entitlement_active:=COALESCE(paid_at IS NOT NULL AND period_days>0 AND (paid_at + make_interval(days=>period_days))>now(),FALSE);
        END IF;
        -- CLIENTS510_OWNER_AI_TRIAL_DB_PARITY_V1: only an existing explicit
        -- AI-marketer trial may substitute for its expired base period.
        -- New feed launch, MOP/ROP packages and unlimited flags gain no rights.
        IF NOT entitlement_active AND NEW.source='paid_tariff_kpi' THEN
          trial_json:=NULL; trial_started:=NULL; trial_until:=NULL;
          SELECT value::jsonb INTO trial_json FROM storage
            WHERE account_id=acc AND key='autopilot_settings' ORDER BY id DESC LIMIT 1;
          IF trial_json->'full_ai_marketer'='true'::jsonb
             AND trial_json->>'trial_scope'='ai_marketer_only'
             AND trial_json->>'trial_source' ~ '^owner_direct_instruction_[0-9]{8}$'
             AND jsonb_typeof(trial_json->'full_ai_marketer_activated_at')='string'
             AND jsonb_typeof(trial_json->'full_ai_marketer_trial_until')='string'
             AND trial_json->>'full_ai_marketer_activated_at' ~ trial_iso_pattern
             AND trial_json->>'full_ai_marketer_trial_until' ~ trial_iso_pattern THEN
            BEGIN
              trial_started:=NULLIF(trial_json->>'full_ai_marketer_activated_at','')::timestamptz;
              trial_until:=NULLIF(trial_json->>'full_ai_marketer_trial_until','')::timestamptz;
            EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
              trial_started:=NULL; trial_until:=NULL;
            END;
            IF trial_started IS NOT NULL AND trial_until IS NOT NULL
               AND isfinite(trial_started) AND isfinite(trial_until)
               AND trial_started<=now() AND now()<trial_until THEN
              entitlement_active:=TRUE;
              paid_until:=trial_until;
            END IF;
          END IF;
        END IF;
        IF NOT entitlement_active THEN
          RAISE EXCEPTION 'BORIS_MONEY_GUARD: paid marketer entitlement inactive for account %',acc;
        END IF;
        -- BORIS_MONEY_PAID_UNTIL_HARD_STOP_DB_V1: authority can never outlive
        -- the canonical paid service period, even if a producer requests days=365.
        IF paid_until IS NULL AND paid_at IS NOT NULL AND period_days>0 THEN
          paid_until := paid_at + make_interval(days=>period_days);
        END IF;
        IF paid_until IS NOT NULL AND (NEW.valid_until IS NULL OR NEW.valid_until>paid_until) THEN
          NEW.valid_until := paid_until;
        END IF;
      END IF;

      SELECT COALESCE((value::jsonb->>'daily_budget_limit_rub')::numeric,0),
             LEAST(safetycap,COALESCE(NULLIF((value::jsonb->>'hard_max_bid_rub')::numeric,0),safetycap))
        INTO cfg_budget,cfg_hard_cap
        FROM storage WHERE account_id=acc AND key='kpi_settings' ORDER BY id DESC LIMIT 1;
      cfg_budget:=COALESCE(cfg_budget,0); cfg_hard_cap:=COALESCE(cfg_hard_cap,safetycap);
      IF cfg_budget<=0 THEN RAISE EXCEPTION 'BORIS_MONEY_GUARD: account % has no positive explicit daily budget',acc; END IF;
      IF NEW.daily_budget_rub IS NULL OR NEW.daily_budget_rub<=0 OR NEW.daily_budget_rub>cfg_budget THEN
        RAISE EXCEPTION 'BORIS_MONEY_GUARD: mandate daily budget % invalid for account % configured %',NEW.daily_budget_rub,acc,cfg_budget;
      END IF;
      IF NEW.max_bid_rub IS NULL OR NEW.max_bid_rub<=0 OR NEW.max_bid_rub>cfg_hard_cap OR NEW.max_bid_rub>safetycap THEN
        RAISE EXCEPTION 'BORIS_MONEY_GUARD: max bid % invalid for account % cap %',NEW.max_bid_rub,acc,cfg_hard_cap;
      END IF;
    END LOOP;
    IF NEW.max_bid_delta_pct IS NULL OR NEW.max_bid_delta_pct<=0 OR NEW.max_bid_delta_pct>10 THEN RAISE EXCEPTION 'BORIS_MONEY_GUARD: max bid delta must be in (0,10]'; END IF;
    IF NEW.max_actions_run IS NULL OR NEW.max_actions_run<=0 OR NEW.max_actions_run>5 THEN RAISE EXCEPTION 'BORIS_MONEY_GUARD: max actions/run must be in [1,5]'; END IF;
    IF NEW.max_actions_day IS NULL OR NEW.max_actions_day<=0 OR NEW.max_actions_day>120 THEN RAISE EXCEPTION 'BORIS_MONEY_GUARD: max actions/day must be in [1,120]'; END IF;
    IF is_launch AND (NEW.max_actions_run<>1 OR NEW.max_actions_day>24) THEN RAISE EXCEPTION 'BORIS_MONEY_GUARD: feed launch must be exactly 1/run and <=24/day'; END IF;
  END IF;
  RETURN NEW;
END $function$
