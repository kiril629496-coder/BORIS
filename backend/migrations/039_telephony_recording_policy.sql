CREATE TABLE IF NOT EXISTS telephony_recording_settings (
    account_id text PRIMARY KEY,
    policy text NOT NULL DEFAULT 'manual',
    acknowledged_at timestamptz,
    acknowledged_by_user_id bigint,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_telephony_recording_policy CHECK (policy IN ('manual','disabled','not_required'))
);
