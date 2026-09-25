-- BORIS owner outreach canonical daily-cap correction.
-- Historical max20 migration remains intact; this migration moves the live
-- safety ceiling to 30 so the production ramp is 10/day -> 20/day -> 30/day.
-- Campaign-level daily_limit may still be set lower deliberately.
BEGIN;

ALTER TABLE prospect_campaigns
DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_max20;

ALTER TABLE prospect_campaigns
DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_max30;

ALTER TABLE prospect_campaigns
ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max30
CHECK (
    account_id IS DISTINCT FROM '__owner_outreach__'
    OR daily_limit <= 30
) NOT VALID;

ALTER TABLE prospect_campaigns
VALIDATE CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max30;

COMMIT;
