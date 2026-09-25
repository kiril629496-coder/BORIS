-- BORIS owner outreach canonical stored ceiling.
-- Actual sends remain ramped by application policy: 10/day for days 1-7,
-- 20/day for days 8-14, then 30/day. The stored active owner campaign
-- ceiling is canonical 30 so it cannot silently block the final ramp stage.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'prospect_campaigns'::regclass
          AND conname = 'prospect_campaigns_owner_outreach_daily_limit_max20'
    ) THEN
        ALTER TABLE prospect_campaigns
        DROP CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max20;
    END IF;

    UPDATE prospect_campaigns
    SET daily_limit = 30, updated_at = NOW()
    WHERE account_id = '__owner_outreach__'
      AND status = 'active'
      AND daily_limit IS DISTINCT FROM 30;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'prospect_campaigns'::regclass
          AND conname = 'prospect_campaigns_owner_outreach_daily_limit_max30'
    ) THEN
        ALTER TABLE prospect_campaigns
        ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max30
        CHECK (
            account_id IS DISTINCT FROM '__owner_outreach__'
            OR daily_limit <= 30
        ) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'prospect_campaigns'::regclass
          AND conname = 'prospect_campaigns_owner_outreach_daily_limit_min30'
    ) THEN
        ALTER TABLE prospect_campaigns
        ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_min30
        CHECK (
            account_id IS DISTINCT FROM '__owner_outreach__'
            OR daily_limit >= 30
        ) NOT VALID;
    END IF;
END
$$;

ALTER TABLE prospect_campaigns
VALIDATE CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max30;

ALTER TABLE prospect_campaigns
VALIDATE CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_min30;
