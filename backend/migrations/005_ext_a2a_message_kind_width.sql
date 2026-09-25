-- Expand A2A message kind storage to match current architecture enum values.
-- Required by DEV_PAID_PROVIDER_BLOCKED (25 chars) and future descriptive kinds.
ALTER TABLE ext_a2a_messages
    ALTER COLUMN kind TYPE VARCHAR(64);
