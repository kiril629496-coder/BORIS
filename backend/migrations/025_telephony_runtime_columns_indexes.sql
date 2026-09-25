-- 025_telephony_runtime_columns_indexes.sql
-- Formalize all remaining runtime telephony ALTER/INDEX DDL so API/runtime startup no longer executes it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_provider_call
                               ON telephony_calls(account_id,provider,provider_call_id)
                               WHERE provider_call_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_calls_account_started
                               ON telephony_calls(account_id,started_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_provider_event
                               ON telephony_events(account_id,provider,provider_event_id)
                               WHERE provider_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_webhook_nonces_received ON telephony_webhook_nonces(received_at);

CREATE INDEX IF NOT EXISTS ix_telephony_devices_account
                               ON telephony_devices(account_id,last_seen_at DESC);

ALTER TABLE telephony_devices ADD COLUMN IF NOT EXISTS push_token_enc text;

ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_telephony_push_due ON telephony_push_outbox(created_at,id,next_attempt_at) WHERE status IN ('pending','retry','waiting_configuration');

CREATE INDEX IF NOT EXISTS ix_telephony_targets_device
                               ON telephony_call_targets(account_id,device_id,status,created_at DESC);

CREATE INDEX IF NOT EXISTS ix_telephony_events_account_id ON telephony_events(account_id,id);

CREATE INDEX IF NOT EXISTS ix_telephony_devices_online ON telephony_devices(account_id,last_seen_at DESC) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_targets_device_call ON telephony_call_targets(account_id,device_id,call_id,created_at DESC);

CREATE INDEX IF NOT EXISTS ix_telephony_calls_device_state ON telephony_calls(account_id,device_id,state) WHERE device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_calls_device_answered ON telephony_calls(account_id,device_id,answered_at DESC) WHERE device_id IS NOT NULL AND answered_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_targets_call
                               ON telephony_call_targets(account_id,call_id,status);

CREATE INDEX IF NOT EXISTS ix_telephony_targets_due
                               ON telephony_call_targets(expires_at,id,account_id,call_id) WHERE status='ringing' AND expires_at IS NOT NULL;

ALTER TABLE telephony_call_targets ADD COLUMN IF NOT EXISTS stage text NOT NULL DEFAULT 'primary';

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_cost_idempotency
                               ON telephony_cost_ledger(account_id,idempotency_key)
                               WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_audit_account_created
                               ON telephony_audit(account_id,created_at DESC);

ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS last_error text;

ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS processed_at timestamptz;

ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS ix_telephony_routes_account ON telephony_routes(account_id,enabled,priority,id);

ALTER TABLE telephony_routes ADD COLUMN IF NOT EXISTS strategy text NOT NULL DEFAULT 'ring_all';

CREATE INDEX IF NOT EXISTS ix_telephony_commands_call ON telephony_commands(account_id,call_id,created_at DESC);

ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;

ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_telephony_commands_due ON telephony_commands(created_at,id,next_attempt_at) WHERE status IN ('queued','waiting_provider');

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_recording_provider ON telephony_recordings(account_id,provider,provider_recording_id) WHERE provider_recording_id IS NOT NULL;

ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS download_attempts integer NOT NULL DEFAULT 0;

ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS next_download_at timestamptz;

ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS last_error text;

ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS transcript_attempts integer NOT NULL DEFAULT 0;

ALTER TABLE telephony_recordings ADD COLUMN IF NOT EXISTS next_transcript_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_telephony_recording_download_due ON telephony_recordings(created_at,id,next_download_at) WHERE status IN ('available','download_retry') AND source_url IS NOT NULL AND local_path IS NULL AND download_attempts<5;

CREATE INDEX IF NOT EXISTS ix_telephony_recording_download_lease ON telephony_recordings(updated_at,id) WHERE status='downloading';

CREATE INDEX IF NOT EXISTS ix_telephony_recording_transcript_due ON telephony_recordings(created_at,id,next_transcript_at) WHERE status='available' AND local_path IS NOT NULL AND transcript_status IN ('pending','retry','blocked_entitlement');

CREATE INDEX IF NOT EXISTS ix_telephony_recording_transcript_lease ON telephony_recordings(updated_at,id) WHERE transcript_status='processing';

CREATE INDEX IF NOT EXISTS ix_telephony_quality_account ON telephony_call_quality(account_id,total_score,updated_at DESC);

ALTER TABLE telephony_calls ADD COLUMN IF NOT EXISTS agent_mode text NOT NULL DEFAULT 'human';

ALTER TABLE telephony_calls ADD COLUMN IF NOT EXISTS recording_notice_status text NOT NULL DEFAULT 'unknown';

ALTER TABLE telephony_calls ADD COLUMN IF NOT EXISTS ai_assist_enabled boolean NOT NULL DEFAULT true;

ALTER TABLE telephony_ai_suggestions ADD COLUMN IF NOT EXISTS grounding_json jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE telephony_ai_suggestions ADD COLUMN IF NOT EXISTS confidence smallint;

ALTER TABLE telephony_report_settings ADD COLUMN IF NOT EXISTS minute_alerts_enabled boolean NOT NULL DEFAULT false;

ALTER TABLE telephony_minute_alerts ADD COLUMN IF NOT EXISTS delivered_at timestamptz;

ALTER TABLE telephony_minute_alerts ADD COLUMN IF NOT EXISTS last_error text;

ALTER TABLE telephony_minute_alerts ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS ix_telephony_coaching_open ON telephony_coaching_plans(account_id,status,created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_transcript_event ON telephony_transcript_chunks(account_id,source,source_event_id) WHERE source_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_transcript_call ON telephony_transcript_chunks(account_id,call_id,seq,id);

ALTER TABLE telephony_voice_agent_settings ADD COLUMN IF NOT EXISTS disclosure_required boolean NOT NULL DEFAULT true;

ALTER TABLE telephony_voice_agent_settings ADD COLUMN IF NOT EXISTS disclosure_text text NOT NULL DEFAULT 'Здравствуйте! Вам отвечает виртуальный помощник BORIS.';

ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS disclosure_played_at timestamptz;

ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS callback_phone text;

ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS callback_phone_confirmed boolean NOT NULL DEFAULT false;

ALTER TABLE telephony_voice_agent_sessions ADD COLUMN IF NOT EXISTS preferred_contact_time text;

CREATE INDEX IF NOT EXISTS ix_telephony_callback_links_source ON telephony_callback_links(account_id,source_call_id);

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS autoanswer_mode text NOT NULL DEFAULT 'afterhours';

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS no_answer_seconds smallint NOT NULL DEFAULT 25;

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS collect_phone boolean NOT NULL DEFAULT true;

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS collect_topic boolean NOT NULL DEFAULT true;

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS create_crm_task boolean NOT NULL DEFAULT true;

ALTER TABLE telephony_afterhours_settings ADD COLUMN IF NOT EXISTS voicemail_text text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_ai_suggestion_key ON telephony_ai_suggestions(account_id,idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telephony_ai_suggestions_call ON telephony_ai_suggestions(account_id,call_id,status,created_at DESC);
