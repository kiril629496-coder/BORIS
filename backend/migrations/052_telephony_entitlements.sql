-- 052_telephony_entitlements.sql
-- Canonical paid BORIS Phone entitlement schema.
-- Runtime code consumes this table; schema ownership belongs to migrations.
BEGIN;

CREATE TABLE IF NOT EXISTS telephony_entitlements (
    account_id text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT true,
    period_start timestamptz NOT NULL,
    paid_until timestamptz NOT NULL,
    price_rub integer,
    commercial_ref text NOT NULL,
    source text NOT NULL DEFAULT 'platform_owner',
    actor_user_id bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_telephony_entitlements_active
    ON telephony_entitlements(enabled, paid_until, account_id);

COMMIT;
