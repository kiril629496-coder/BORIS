from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text
from app.db.session import SessionLocal

CALL_STATES = {
    'new','connecting','ringing','answered','active','on_hold','transferring',
    'transferred','ended','missed','rejected','busy','failed','cancelled'
}
FINAL_STATES = {'transferred','ended','missed','rejected','busy','failed','cancelled'}
# Authoritative monotonic state machine. Provider webhooks may arrive late/out of order;
# never regress a live/final call back to ringing/connecting.
CALL_STATE_TRANSITIONS = {
    'new': {'connecting','ringing','active','ended','missed','rejected','busy','failed','cancelled'},
    'connecting': {'ringing','active','ended','missed','rejected','busy','failed','cancelled'},
    'ringing': {'active','ended','missed','rejected','busy','failed','cancelled'},
    'answered': {'active','on_hold','transferring','transferred','ended','failed','cancelled'},
    'active': {'on_hold','transferring','transferred','ended','failed','cancelled'},
    'on_hold': {'active','transferring','transferred','ended','failed','cancelled'},
    'transferring': {'active','on_hold','transferred','ended','failed','cancelled'},
    'transferred': set(), 'ended': set(), 'missed': set(), 'rejected': set(), 'busy': set(), 'failed': set(), 'cancelled': set(),
}
def _call_transition_allowed(current: str|None, target: str|None) -> bool:
    if not target or target not in CALL_STATES: return False
    if not current: return target in CALL_STATES
    if current == target: return True
    return target in CALL_STATE_TRANSITIONS.get(current,set())

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

# TELEPHONY_SCHEMA_READONLY_FAST_PATH_V1
# Short-lived workers/guardians start in a fresh Python process, so the
# process-local _SCHEMA_READY flag is false every time. Re-running dozens of
# CREATE/ALTER/INDEX statements on every health tick can itself contend with
# normal traffic. Prove the already-migrated schema read-only first; fall back
# to the existing serialized DDL bootstrap only when proof is incomplete.
_TELEPHONY_REQUIRED_TABLES = frozenset({
    'telephony_afterhours_sessions','telephony_afterhours_settings','telephony_ai_suggestions',
    'telephony_audit','telephony_call_quality','telephony_call_targets','telephony_callback_links',
    'telephony_calls','telephony_coaching_plans','telephony_commands','telephony_copilot_settings',
    'telephony_cost_ledger','telephony_devices','telephony_events','telephony_manager_actions','telephony_media_sessions','telephony_minute_alerts',
    'telephony_minute_packages','telephony_minute_usage','telephony_outbound_intents',
    'telephony_provider_configs','telephony_push_outbox','telephony_recordings','telephony_entitlements',
    'telephony_report_deliveries','telephony_report_settings','telephony_routes',
    'telephony_transcript_chunks','telephony_voice_agent_postcall',
    'telephony_voice_agent_qualification','telephony_voice_agent_sessions',
    'telephony_voice_agent_settings','telephony_voice_agent_turns','telephony_webhook_nonces',
})
_TELEPHONY_REQUIRED_COLUMNS = frozenset({
    ('telephony_commands','idempotency_key'),('telephony_commands','request_hash'),
    ('telephony_outbound_intents','to_number'),
    ('telephony_push_outbox','device_received_at'),('telephony_push_outbox','device_received_via'),
    ('telephony_push_outbox','sent_push_token_hash'),
})
_TELEPHONY_REQUIRED_INDEXES = frozenset({
    'ix_telephony_outbound_intents_reconcile','ix_telephony_outbound_intents_status',
    'ix_telephony_media_due','ix_telephony_media_call','ux_telephony_media_endpoint',
    'ux_telephony_calls_provider_identity','ux_telephony_commands_idempotency',
    'ux_telephony_events_provider_identity','ux_telephony_push_active_intent',
    'ux_telephony_recordings_provider_identity','ux_telephony_manager_actions_one_open',
    'ix_telephony_manager_actions_history','ix_telephony_entitlements_active',
})

def _telephony_schema_fast_path_ready() -> bool:
    db = SessionLocal()
    try:
        migrations_dir = Path(__file__).resolve().parents[2] / 'migrations'
        expected = {}
        for path in migrations_dir.glob('*_telephony_*.sql'):
            try:
                version = int(path.name.split('_', 1)[0])
            except Exception:
                return False
            expected[path.name] = (version, hashlib.sha256(path.read_bytes()).hexdigest())
        if not expected:
            return False
        applied = {
            str(r['filename']): (int(r['version']), str(r['sha256']))
            for r in db.execute(text(
                "SELECT version,filename,sha256 FROM boris_schema_migrations WHERE filename LIKE '%telephony%'"
            )).mappings().all()
        }
        if any(applied.get(name) != proof for name, proof in expected.items()):
            return False
        tables = {
            str(r[0]) for r in db.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'telephony_%'"
            )).all()
        }
        if not _TELEPHONY_REQUIRED_TABLES.issubset(tables):
            return False
        columns = {
            (str(r[0]), str(r[1])) for r in db.execute(text(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name LIKE 'telephony_%'"
            )).all()
        }
        if not _TELEPHONY_REQUIRED_COLUMNS.issubset(columns):
            return False
        indexes = {
            str(r[0]) for r in db.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            )).all()
        }
        return _TELEPHONY_REQUIRED_INDEXES.issubset(indexes)
    except Exception:
        return False
    finally:
        db.close()

PROVIDERS: dict[str, dict[str, Any]] = {
    # Zadarma adapter is kept for legacy/international accounts, but BORIS's
    # current client market is Russia, where it must not be offered as a selectable operator.
    'zadarma': {'name':'Zadarma','capabilities':['sip','api','webhooks','recordings'],'ru_selectable':False,'availability_note':'Не предлагается для РФ'},
    'novofon': {'name':'Novofon','capabilities':['sip','api','webhooks','recordings'],'ru_selectable':True},
    'uis': {'name':'UIS / CoMagic','capabilities':['sip','api','webhooks','recordings'],'ru_selectable':True},
    'mango': {'name':'MANGO','capabilities':['sip','api','webhooks','recordings'],'ru_selectable':True},
    'telphin': {'name':'Телфин','capabilities':['api','outbound_callback','call_control'],'ru_selectable':True},
    'mcn': {'name':'MCN Telecom','capabilities':['sip','asterisk','call_control','recordings','sip_media','own_calltracking','carrier_cdr'],'ru_selectable':True},
    'sip': {'name':'Другой SIP','capabilities':['sip'],'ru_selectable':True},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone(value: str | None) -> str:
    raw = re.sub(r'\D+', '', str(value or ''))
    if len(raw) == 11 and raw.startswith('8'):
        raw = '7' + raw[1:]
    if len(raw) == 10:
        raw = '7' + raw
    return ('+' + raw) if raw else ''


def _call_id() -> str:
    return 'call_' + uuid.uuid4().hex


def _device_id() -> str:
    return 'dev_' + uuid.uuid4().hex


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    if _telephony_schema_fast_path_ready():
        _SCHEMA_READY = True
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        # Another process may have completed migrations while this thread was
        # waiting. Re-prove schema before taking the DDL advisory lock.
        if _telephony_schema_fast_path_ready():
            _SCHEMA_READY = True
            return
        db = SessionLocal()
        try:
            # Serialize runtime schema bootstrap across API replicas.  A process-local
            # threading.Lock is not enough when :8000 and :8001 start together; without
            # this, concurrent CREATE/ALTER/INDEX statements can deadlock PostgreSQL.
            db.execute(text("SELECT pg_advisory_xact_lock(721946301)"))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_calls (
                id text PRIMARY KEY,
                account_id text NOT NULL,
                provider text,
                provider_call_id text,
                direction text NOT NULL,
                from_number text,
                to_number text,
                extension text,
                user_id bigint,
                device_id text,
                state text NOT NULL DEFAULT 'new',
                termination_reason text,
                started_at timestamptz NOT NULL DEFAULT now(),
                ringing_at timestamptz,
                answered_at timestamptz,
                ended_at timestamptz,
                talk_duration_sec integer NOT NULL DEFAULT 0,
                wait_duration_sec integer NOT NULL DEFAULT 0,
                crm_contact_id bigint,
                crm_deal_id bigint,
                source text,
                source_ref text,
                recording_status text NOT NULL DEFAULT 'not_requested',
                recording_url text,
                transcription_status text NOT NULL DEFAULT 'pending',
                ai_analysis_status text NOT NULL DEFAULT 'pending',
                qualification text,
                summary text,
                next_action text,
                callback_due_at timestamptz,
                callback_status text,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_calls_provider_identity ON telephony_calls(account_id,provider,provider_call_id) WHERE provider IS NOT NULL AND provider_call_id IS NOT NULL AND provider_call_id<>''"))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_events (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                call_id text,
                provider text,
                provider_event_id text,
                event_type text NOT NULL,
                payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )'''))
            # Provider event identity is an immutable ingress idempotency boundary.
            # The advisory lock in apply_event serializes normal workers; this DB
            # constraint additionally prevents duplicate durable events if a future
            # caller bypasses that helper or two code generations overlap during a rollout.
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_events_provider_identity ON telephony_events(account_id,provider,provider_event_id) WHERE provider IS NOT NULL AND provider_event_id IS NOT NULL AND provider_event_id<>''"))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_webhook_nonces (
                account_id text NOT NULL, provider text NOT NULL, nonce_hash text NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(account_id,provider,nonce_hash)
            )'''))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_devices (
                id text PRIMARY KEY,
                account_id text NOT NULL,
                user_id bigint,
                name text NOT NULL,
                platform text NOT NULL,
                app_version text,
                push_kind text,
                push_token_hash text,
                capabilities_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                presence text NOT NULL DEFAULT 'offline',
                last_seen_at timestamptz,
                revoked_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_push_outbox (
                id bigserial PRIMARY KEY, account_id text NOT NULL, device_id text NOT NULL, call_id text,
                event_type text NOT NULL, payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                status text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
                last_error text, created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
                device_received_at timestamptz, device_received_via text, sent_push_token_hash text,
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text("ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS device_received_at timestamptz"))
            db.execute(text("ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS device_received_via text"))
            db.execute(text("ALTER TABLE telephony_push_outbox ADD COLUMN IF NOT EXISTS sent_push_token_hash text"))
            # Durable exactly-once boundary for an active mobile push intent. The
            # advisory lock in _queue_device_push handles normal concurrent callers,
            # while this partial unique index also protects against future helper
            # bypasses or overlapping code generations during a rollout.
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_push_active_intent ON telephony_push_outbox(account_id,device_id,call_id,event_type) WHERE status IN ('pending','retry','waiting_configuration','sent')"))
            # Canonical dispatcher index. Do not recreate the obsolete broad
            # (status,created_at,id) index: migration 017 intentionally removed it.
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_call_targets (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                call_id text NOT NULL,
                device_id text NOT NULL,
                user_id bigint,
                status text NOT NULL DEFAULT 'ringing',
                priority integer NOT NULL DEFAULT 100,
                route_id bigint,
                expires_at timestamptz,
                answered_at timestamptz,
                ended_at timestamptz,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(account_id,call_id,device_id)
            )'''))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_cost_ledger (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                call_id text,
                category text NOT NULL,
                provider text,
                amount_rub numeric(14,4) NOT NULL DEFAULT 0,
                units numeric(14,4),
                unit_name text,
                idempotency_key text,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_provider_configs (
                account_id text PRIMARY KEY,
                provider text NOT NULL,
                credentials_enc text,
                public_config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                webhook_secret_hash text,
                webhook_secret_enc text,
                status text NOT NULL DEFAULT 'credentials_required',
                last_health_at timestamptz,
                last_health_status text,
                last_error text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''
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
            )'''))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_telephony_entitlements_active ON telephony_entitlements(enabled,paid_until,account_id)"))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_media_sessions (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                call_id text NOT NULL,
                device_id text NOT NULL,
                provider text NOT NULL,
                endpoint_id text NOT NULL,
                transport text NOT NULL,
                managed_by text NOT NULL DEFAULT 'provider',
                status text NOT NULL DEFAULT 'active',
                session_fingerprint text NOT NULL,
                expires_at timestamptz NOT NULL,
                cleanup_attempts integer NOT NULL DEFAULT 0,
                next_cleanup_at timestamptz,
                last_error text,
                cleaned_at timestamptz,
                bridge_id text,
                media_channel_id text,
                activated_at timestamptz,
                last_bridge_error text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text("ALTER TABLE telephony_media_sessions ADD COLUMN IF NOT EXISTS bridge_id text"))
            db.execute(text("ALTER TABLE telephony_media_sessions ADD COLUMN IF NOT EXISTS media_channel_id text"))
            db.execute(text("ALTER TABLE telephony_media_sessions ADD COLUMN IF NOT EXISTS activated_at timestamptz"))
            db.execute(text("ALTER TABLE telephony_media_sessions ADD COLUMN IF NOT EXISTS last_bridge_error text"))
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_media_endpoint ON telephony_media_sessions(endpoint_id)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_telephony_media_due ON telephony_media_sessions(expires_at,id,next_cleanup_at) WHERE status IN ('active','cleanup_pending','await_expiry')"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_telephony_media_call ON telephony_media_sessions(account_id,call_id,device_id,created_at DESC)"))
            db.execute(text('''
            CREATE TABLE IF NOT EXISTS telephony_audit (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                action text NOT NULL,
                actor_user_id bigint,
                call_id text,
                provider text,
                result text,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_routes (
                id bigserial PRIMARY KEY, account_id text NOT NULL, name text NOT NULL,
                priority integer NOT NULL DEFAULT 100, enabled boolean NOT NULL DEFAULT true,
                source text, source_ref text, number_pattern text, destination_kind text NOT NULL DEFAULT 'device',
                destination_value text, ring_timeout_sec integer NOT NULL DEFAULT 25,
                fallback_kind text, fallback_value text, schedule_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_commands (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL,
                command text NOT NULL, payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                requested_by bigint, device_id text, status text NOT NULL DEFAULT 'queued',
                provider text, provider_command_id text, error text,
                created_at timestamptz NOT NULL DEFAULT now(), executed_at timestamptz
            )'''))
            db.execute(text("ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS idempotency_key text"))
            db.execute(text("ALTER TABLE telephony_commands ADD COLUMN IF NOT EXISTS request_hash text"))
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_commands_idempotency ON telephony_commands(account_id,call_id,idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key<>''"))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_outbound_intents (
                account_id text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
                status text NOT NULL DEFAULT 'processing', provider text, provider_call_id text, call_id text,
                to_number text,
                last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(account_id,idempotency_key)
            )'''))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_telephony_outbound_intents_status ON telephony_outbound_intents(account_id,status,updated_at DESC)"))
            db.execute(text("ALTER TABLE telephony_outbound_intents ADD COLUMN IF NOT EXISTS to_number text"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_telephony_outbound_intents_reconcile ON telephony_outbound_intents(account_id,provider,to_number,status,updated_at DESC)"))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_recordings (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL,
                provider text, provider_recording_id text, source_url text, local_path text,
                duration_sec integer, status text NOT NULL DEFAULT 'pending', checksum text,
                transcript text, transcript_status text NOT NULL DEFAULT 'pending',
                analysis_json jsonb NOT NULL DEFAULT '{}'::jsonb, analysis_status text NOT NULL DEFAULT 'pending',
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_recordings_provider_identity ON telephony_recordings(account_id,provider,provider_recording_id) WHERE provider_recording_id IS NOT NULL AND provider_recording_id<>''"))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_call_quality (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL, manager_user_id bigint, evaluator text NOT NULL DEFAULT 'rules', version text NOT NULL DEFAULT 'v1', need_score smallint, timeline_score smallint, budget_score smallint, presentation_score smallint, objection_score smallint, next_step_score smallint, total_score smallint, strengths_json jsonb NOT NULL DEFAULT '[]'::jsonb, gaps_json jsonb NOT NULL DEFAULT '[]'::jsonb, evidence_json jsonb NOT NULL DEFAULT '[]'::jsonb, status text NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,call_id,version)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_recording_settings (
                account_id text PRIMARY KEY, policy text NOT NULL DEFAULT 'manual',
                acknowledged_at timestamptz, acknowledged_by_user_id bigint,
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT ck_telephony_recording_policy CHECK (policy IN ('manual','disabled','not_required'))
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_report_settings (
                account_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT false, frequency text NOT NULL DEFAULT 'daily',
                send_hour smallint NOT NULL DEFAULT 9, timezone text NOT NULL DEFAULT 'Europe/Moscow',
                email_to text, telegram_chat_id text, telegram_thread_id bigint, last_sent_key text, last_sent_at timestamptz,
                last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_coaching_plans (
                id bigserial PRIMARY KEY, account_id text NOT NULL, manager_user_id bigint, skill_key text NOT NULL, skill_label text NOT NULL,
                source_call_id text, source_quality_id bigint, baseline_score smallint, status text NOT NULL DEFAULT 'open',
                training_goal text NOT NULL, recommended_scenario text NOT NULL, sparring_session_id text, completed_at timestamptz,
                followup_score smallint, improvement smallint, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(account_id,source_call_id,skill_key)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_manager_actions (
                id bigserial PRIMARY KEY,
                account_id text NOT NULL,
                action_code text NOT NULL,
                actor text NOT NULL DEFAULT 'manager',
                title text NOT NULL,
                action_text text NOT NULL,
                assigned_user_id bigint,
                due_at timestamptz,
                status text NOT NULL DEFAULT 'open',
                resolution text,
                first_seen_at timestamptz NOT NULL DEFAULT now(),
                last_seen_at timestamptz NOT NULL DEFAULT now(),
                completed_at timestamptz,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb
            )'''))
            db.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_manager_actions_one_open
              ON telephony_manager_actions(account_id) WHERE status='open'"""))
            db.execute(text("""CREATE INDEX IF NOT EXISTS ix_telephony_manager_actions_history
              ON telephony_manager_actions(account_id,first_seen_at DESC,id DESC)"""))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_report_deliveries (
                id bigserial PRIMARY KEY, account_id text NOT NULL, report_key text NOT NULL, channel text NOT NULL, recipient text,
                status text NOT NULL DEFAULT 'pending', error text, created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
                UNIQUE(account_id,report_key,channel)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_transcript_chunks (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL,
                seq bigint NOT NULL, speaker text, text_content text NOT NULL,
                start_ms integer, end_ms integer, is_final boolean NOT NULL DEFAULT true,
                source text NOT NULL DEFAULT 'stream', source_event_id text,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(account_id,call_id,source,seq)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_copilot_settings (
                account_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true, human_call_only boolean NOT NULL DEFAULT true,
                min_interval_seconds smallint NOT NULL DEFAULT 12, max_suggestions_per_call smallint NOT NULL DEFAULT 12,
                allow_price_suggestions boolean NOT NULL DEFAULT false, allow_commitments boolean NOT NULL DEFAULT false,
                updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_minute_packages (
                account_id text PRIMARY KEY, package_minutes integer NOT NULL DEFAULT 300, used_seconds bigint NOT NULL DEFAULT 0,
                cycle_start date NOT NULL DEFAULT CURRENT_DATE, cycle_end date, overage_mode text NOT NULL DEFAULT 'notify',
                soft_limit_percent smallint NOT NULL DEFAULT 90, updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_minute_usage (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text, usage_kind text NOT NULL, seconds integer NOT NULL,
                ai_cost_rub numeric(12,4) NOT NULL DEFAULT 0, provider_cost_rub numeric(12,4) NOT NULL DEFAULT 0, infra_cost_rub numeric(12,4) NOT NULL DEFAULT 0,
                included_features jsonb NOT NULL DEFAULT '[]'::jsonb, idempotency_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,idempotency_key)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_minute_alerts (
                id bigserial PRIMARY KEY, account_id text NOT NULL, threshold smallint NOT NULL, cycle_start date NOT NULL,
                usage_percent numeric(8,2) NOT NULL, status text NOT NULL DEFAULT 'created', created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(account_id,threshold,cycle_start)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_voice_agent_qualification (
                account_id text NOT NULL, call_id text NOT NULL, need text, product text, parameters text, geography text, timeline text, budget text,
                urgency text, qualification text, next_step text, completeness smallint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(account_id,call_id)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_voice_agent_turns (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL, turn_no integer NOT NULL, speaker text NOT NULL,
                text_content text NOT NULL, intent text, action text, evidence_json jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(account_id,call_id,turn_no,speaker)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_voice_agent_settings (
                account_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT false, afterhours_enabled boolean NOT NULL DEFAULT true,
                voice_name text NOT NULL DEFAULT 'default', language text NOT NULL DEFAULT 'ru', max_call_minutes smallint NOT NULL DEFAULT 15,
                allow_prices boolean NOT NULL DEFAULT false, allow_commitments boolean NOT NULL DEFAULT false, allow_crm_write boolean NOT NULL DEFAULT true,
                handoff_on_unknown boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_voice_agent_sessions (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL, mode text NOT NULL, state text NOT NULL DEFAULT 'active',
                customer_phone text, qualification text, topic text, summary text, next_action text, handoff_reason text,
                started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz, metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, UNIQUE(account_id,call_id)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_voice_agent_postcall (
                account_id text NOT NULL, call_id text NOT NULL, status text NOT NULL DEFAULT 'pending',
                crm_status text, crm_task_id bigint, metering_status text, usage_id bigint, rop_score numeric(6,2),
                cost_recorded boolean NOT NULL DEFAULT false, cost_nonzero boolean NOT NULL DEFAULT false,
                outcome_json jsonb NOT NULL DEFAULT '{}'::jsonb, finalized_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(account_id,call_id)
            )'''))
            db.execute(text("""CREATE TABLE IF NOT EXISTS telephony_callback_links (
              id bigserial PRIMARY KEY, account_id text NOT NULL, source_call_id text NOT NULL, callback_call_id text NOT NULL, status text NOT NULL DEFAULT 'answered',
              callback_answered_at timestamptz, within_sla boolean, crm_deal_id bigint, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE(account_id,callback_call_id))"""))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_afterhours_settings (
                account_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT false, timezone text NOT NULL DEFAULT 'Europe/Moscow',
                work_days jsonb NOT NULL DEFAULT '[0,1,2,3,4]'::jsonb, work_start text NOT NULL DEFAULT '09:00', work_end text NOT NULL DEFAULT '18:00',
                callback_hour smallint NOT NULL DEFAULT 9, greeting text, updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_afterhours_sessions (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL, state text NOT NULL DEFAULT 'ask_phone',
                captured_phone text, confirmed_phone text, topic text, attempts smallint NOT NULL DEFAULT 0, next_callback_at timestamptz, crm_task_id bigint,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,call_id)
            )'''))
            db.execute(text('''CREATE TABLE IF NOT EXISTS telephony_ai_suggestions (
                id bigserial PRIMARY KEY, account_id text NOT NULL, call_id text NOT NULL,
                trigger_chunk_id bigint, kind text NOT NULL DEFAULT 'next_best_action',
                suggestion_text text NOT NULL, evidence_json jsonb NOT NULL DEFAULT '[]'::jsonb,
                status text NOT NULL DEFAULT 'active', model text, idempotency_key text,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )'''))
    
            db.commit()
            _SCHEMA_READY = True
        finally:
            db.close()


def _phone_entitlement_until(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or '').strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def active_phone_entitlements() -> list[dict[str, Any]]:
    """Canonical active paid BORIS Phone products.

    Phone entitlement is intentionally isolated from account_slots. Existing
    account/Inbox slots never grant Phone and there is no legacy fallback.
    """
    ensure_schema()
    db = SessionLocal()
    try:
        rows = [
            dict(r) for r in db.execute(text("""
                SELECT e.account_id,
                       COALESCE(NULLIF(btrim(a.name),''), e.account_id) AS display_name,
                       e.paid_until,
                       e.price_rub,
                       e.source,
                       'telephony_entitlement'::text AS entitlement_source
                FROM telephony_entitlements e
                JOIN accounts a ON a.account_id=e.account_id
                WHERE e.enabled=true AND e.paid_until>now()
                ORDER BY e.account_id
            """)).mappings().all()
        ]
    finally:
        db.close()
    return rows


def phone_entitlement_status(account_id: str) -> dict[str, Any]:
    ensure_schema()
    aid = str(account_id or '').strip()
    if not aid:
        return {'status':'invalid_account','active':False,'account_id':''}
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT e.account_id,e.enabled,e.period_start,e.paid_until,e.price_rub,
                   e.source,e.commercial_ref,e.updated_at,
                   COALESCE(NULLIF(btrim(a.name),''),e.account_id) display_name
            FROM telephony_entitlements e
            JOIN accounts a ON a.account_id=e.account_id
            WHERE e.account_id=:a
        """), {'a':aid}).mappings().first()
        if not row:
            return {
                'status':'not_entitled','active':False,'account_id':aid,
                'truth':'only canonical telephony_entitlements grant BORIS Phone; account_slots are ignored',
            }
    finally:
        db.close()

    until = _phone_entitlement_until(row.get('paid_until'))
    active = bool(row.get('enabled')) and bool(until and until > utcnow())
    ref = str(row.get('commercial_ref') or '')
    return {
        'status':'active' if active else ('revoked' if not row.get('enabled') else 'expired'),
        'active':active,
        'account_id':aid,
        'display_name':str(row.get('display_name') or aid)[:240],
        'period_start':_phone_entitlement_until(row.get('period_start')).isoformat() if row.get('period_start') else None,
        'paid_until':until.isoformat() if until else None,
        'price_rub':row.get('price_rub'),
        'source':str(row.get('source') or '')[:80] or None,
        'has_commercial_ref':bool(ref),
        'commercial_ref_hash':hashlib.sha256(ref.encode('utf-8')).hexdigest()[:16] if ref else None,
        'updated_at':str(row.get('updated_at') or '')[:80] or None,
        'truth':'canonical paid BORIS Phone entitlement; commercial reference value is never returned',
    }


def provision_paid_phone_entitlement(
    account_id: str,
    paid_until: Any,
    commercial_ref: str,
    actor_user_id: int | None = None,
    price_rub: int | None = None,
    source: str = 'platform_owner_confirmed',
    allow_zero_price: bool = False,
) -> dict[str, Any]:
    """Create/renew Phone only from explicit commercial evidence.

    A future paid_until, non-empty commercial reference and explicit price are
    mandatory for every new commercial reference. Zero-price grants require a
    separate explicit flag. Renewals may extend but never silently shorten an
    already active paid period.
    """
    ensure_schema()
    aid = str(account_id or '').strip()
    ref = str(commercial_ref or '').strip()
    until = _phone_entitlement_until(paid_until)
    if not aid:
        return {'status':'invalid_account','active':False}
    if not ref or len(ref) > 500:
        return {'status':'commercial_reference_required','active':False,'account_id':aid}
    if until is None or until <= utcnow():
        return {'status':'future_paid_until_required','active':False,'account_id':aid}
    if price_rub is not None:
        try:
            price = int(price_rub)
        except (TypeError, ValueError):
            return {'status':'invalid_price','active':False,'account_id':aid}
        if price < 0:
            return {'status':'invalid_price','active':False,'account_id':aid}
    else:
        price = None
    src = re.sub(r'[^A-Za-z0-9_.:-]+','_',str(source or 'platform_owner_confirmed'))[:80] or 'platform_owner_confirmed'
    now = utcnow()
    duplicate_same_ref = False
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {'k':'phone-entitlement|'+aid})
        account = db.execute(text("""
            SELECT account_id,owner_user_id,name FROM accounts
            WHERE account_id=:a LIMIT 1
        """), {'a':aid}).mappings().first()
        if not account:
            db.rollback()
            return {'status':'account_not_found','active':False,'account_id':aid}
        current = db.execute(text("""
            SELECT enabled,paid_until,commercial_ref,price_rub FROM telephony_entitlements
            WHERE account_id=:a FOR UPDATE
        """), {'a':aid}).mappings().first()
        current_until = _phone_entitlement_until(current.get('paid_until')) if current else None
        if current and str(current.get('commercial_ref') or '').strip() == ref:
            db.rollback()
            duplicate_same_ref = True
        elif price is None:
            db.rollback()
            return {
                'status':'price_required',
                'active':bool(current and current.get('enabled') and current_until and current_until > now),
                'account_id':aid,
            }
        elif price == 0 and not bool(allow_zero_price):
            db.rollback()
            return {
                'status':'zero_price_confirmation_required',
                'active':bool(current and current.get('enabled') and current_until and current_until > now),
                'account_id':aid,
            }
        elif (
            current
            and bool(current.get('enabled'))
            and current_until is not None
            and current_until > now
            and until < current_until
        ):
            db.rollback()
            return {
                'status':'would_shorten_paid_period',
                'active':True,
                'account_id':aid,
                'paid_until':current_until.isoformat(),
            }
        if not duplicate_same_ref:
            db.execute(text("""
            INSERT INTO telephony_entitlements(
                account_id,enabled,period_start,paid_until,price_rub,
                commercial_ref,source,actor_user_id,created_at,updated_at
            ) VALUES(:a,true,:ps,:pu,:pr,:cr,:s,:u,now(),now())
            ON CONFLICT(account_id) DO UPDATE SET
                enabled=true,
                period_start=CASE
                    WHEN telephony_entitlements.enabled=false
                      OR telephony_entitlements.paid_until<=now()
                    THEN excluded.period_start
                    ELSE telephony_entitlements.period_start
                END,
                paid_until=GREATEST(telephony_entitlements.paid_until,excluded.paid_until),
                price_rub=COALESCE(excluded.price_rub,telephony_entitlements.price_rub),
                commercial_ref=excluded.commercial_ref,
                source=excluded.source,
                actor_user_id=excluded.actor_user_id,
                updated_at=now()
        """), {
            'a':aid,'ps':now,'pu':until,'pr':price,'cr':ref,'s':src,'u':actor_user_id,
        })
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    out = phone_entitlement_status(aid)
    if duplicate_same_ref:
        out['idempotent_replay'] = True
        _audit(
            aid,'phone.entitlement.duplicate_replay',result='ignored',
            actor_user_id=actor_user_id,
            metadata={
                'source':src,
                'commercial_ref_hash':hashlib.sha256(ref.encode('utf-8')).hexdigest()[:16],
            },
        )
        return out
    _audit(
        aid,'phone.entitlement.activated',result='ok',actor_user_id=actor_user_id,
        metadata={
            'paid_until':out.get('paid_until'),
            'price_rub':price,
            'source':src,
            'commercial_ref_hash':hashlib.sha256(ref.encode('utf-8')).hexdigest()[:16],
        },
    )
    return out


def revoke_phone_entitlement(
    account_id: str,
    actor_user_id: int | None = None,
    reason: str = 'commercial_revoked',
) -> dict[str, Any]:
    ensure_schema()
    aid = str(account_id or '').strip()
    if not aid:
        return {'status':'invalid_account','active':False}
    safe_reason = re.sub(r'[^A-Za-z0-9_.:-]+','_',str(reason or 'commercial_revoked'))[:120]
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {'k':'phone-entitlement|'+aid})
        updated = db.execute(text("""
            UPDATE telephony_entitlements
            SET enabled=false,actor_user_id=:u,source=:s,updated_at=now()
            WHERE account_id=:a
            RETURNING account_id
        """), {'a':aid,'u':actor_user_id,'s':'revoked:'+safe_reason}).first()
        db.commit()
    finally:
        db.close()
    if not updated:
        return {'status':'not_entitled','active':False,'account_id':aid}
    _audit(
        aid,'phone.entitlement.revoked',result='ok',actor_user_id=actor_user_id,
        metadata={'reason':safe_reason},
    )
    return phone_entitlement_status(aid)



def provider_catalog() -> list[dict[str, Any]]:
    from app.services.telephony_adapters import adapter_status
    items = []
    for key, meta in PROVIDERS.items():
        runtime = adapter_status(key)
        items.append({
            'key': key,
            'name': meta.get('name') or key,
            'implemented': bool(runtime.get('implemented')),
            'available': bool(runtime.get('available')),
            'ru_selectable': bool(meta.get('ru_selectable', True)),
            'availability_note': meta.get('availability_note'),
            'capabilities': runtime.get('capabilities') or [],
        })
    return items


def selected_provider(account_id: str) -> str | None:
    ensure_schema(); db=SessionLocal()
    try:
        p=db.execute(text("SELECT provider FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).scalar()
    finally: db.close()
    p=str(p or '').strip().lower()
    if p in PROVIDERS: return p
    from app.api.revenue_analytics import _setup_get
    cfg = _setup_get(account_id)
    p = str(cfg.get('telephony_provider') or '').strip().lower()
    return p if p in PROVIDERS else None


def provider_status(account_id: str) -> dict[str, Any]:
    ensure_schema(); p=selected_provider(account_id); db=SessionLocal()
    try:
        calls_30d = db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a AND started_at>=now()-interval '30 days'"), {'a':account_id}).scalar() or 0
        online = db.execute(text("SELECT count(*) FROM telephony_devices WHERE account_id=:a AND revoked_at IS NULL AND presence<>'offline' AND last_seen_at>=now()-interval '90 seconds'"), {'a':account_id}).scalar() or 0
        cfg=db.execute(text("SELECT status,last_health_at,last_health_status,last_error,(credentials_enc IS NOT NULL AND credentials_enc<>'') has_credentials FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally: db.close()
    from app.services.telephony_adapters import adapter_status
    ast=adapter_status(p)
    configured=bool(cfg and cfg.get('has_credentials'))
    verified=bool(cfg and cfg.get('status')=='connected' and cfg.get('last_health_status')=='ok' and ast.get('implemented'))
    state='connected' if verified else ('adapter_pending' if configured and not ast.get('implemented') else ('configured_unverified' if configured else ('credentials_required' if p else 'not_selected')))
    telphin_sip=None
    if p=='telphin':
        try:
            from app.services.telphin_sip_trunk import trunk_status
            telphin_sip=trunk_status(account_id)
        except Exception:
            telphin_sip={'status':'unavailable','enabled':False,'ready':False}
    return {
        'status':'ok','product':'BORIS Phone','provider':p,'provider_configured':configured,'provider_verified':verified,
        'provider_state':state,'provider_health':dict(cfg) if cfg else None,'adapter':ast,'telphin_sip':telphin_sip,
        'calls_30d':int(calls_30d),'online_devices':int(online),
        'capabilities': PROVIDERS.get(p,{}).get('capabilities',[]) if p else [],
        'platforms': {'web':'ui_ready_media_waiting_provider' if not verified else 'ui_ready_media_contract_pending',
                      'windows':'desktop_source_ready_media_waiting_provider' if not verified else 'desktop_source_ready_media_contract_pending',
                      'macos':'desktop_source_ready_media_waiting_provider' if not verified else 'desktop_source_ready_media_contract_pending',
                      'android':'native_source_ready_media_waiting_provider' if not verified else 'native_source_ready_media_contract_pending',
                      'ios':'native_source_ready_media_waiting_provider' if not verified else 'native_source_ready_media_contract_pending'},
    }

def _validate_native_push_material(push_kind: str | None, push_token: str | None, platform: str | None=None) -> tuple[str|None,str|None,str|None]:
    """Normalize native push material before any persistence.

    A registration endpoint must not be weaker than token-rotation: malformed or
    half-specified push credentials are rejected before encryption/DB writes.
    """
    kind=str(push_kind or '').strip().lower(); token=str(push_token or '').strip()
    if not kind and not token: return None,None,None
    if not kind: return None,None,'invalid_push_kind'
    if kind not in {'apns_voip','fcm'}: return None,None,'invalid_push_kind'
    if not token or len(token)>4096: return None,None,'invalid_push_token'
    if kind=='apns_voip' and not re.fullmatch(r'[0-9a-fA-F]{32,256}',token): return None,None,'invalid_push_token'
    if kind=='fcm' and len(token)<20: return None,None,'invalid_push_token'
    plat=str(platform or '').strip().lower()
    if plat:
        expected={'ios':'apns_voip','android':'fcm'}.get(plat)
        if expected is None: return None,None,'push_not_supported_for_platform'
        if kind!=expected: return None,None,'push_platform_mismatch'
    return kind,token,None


def register_device(account_id: str, user_id: int | None, name: str, platform: str,
                    app_version: str = '', capabilities: dict | None = None,
                    device_id: str | None = None, push_kind: str | None = None,
                    push_token: str | None = None) -> dict[str, Any]:
    """Register/update a device without ever reviving a device explicitly revoked by the owner."""
    ensure_schema(); device_id = str(device_id or _device_id()).strip()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}',device_id):
        return {'status':'invalid_device_id','message':'Некорректный идентификатор устройства'}
    # Device platform is an identity/routing field, not display text. Keep one
    # canonical lowercase value so SQL outbox readiness (ios/android) cannot
    # disagree with case-insensitive push validation on reconnect/register.
    platform = str(platform or 'web').strip().lower() or 'web'
    kind,token,push_error=_validate_native_push_material(push_kind,push_token,platform)
    if push_error: return {'status':push_error}
    # Registration data is controlled by the client. Persist only client-owned
    # boolean capabilities; server/owner-managed fields such as PBX `extension`
    # must never be writable through reconnect/register.
    raw_caps=capabilities if isinstance(capabilities,dict) else {}
    client_cap_keys={'calls','crm','notifications','media','background','native_call_ui'}
    client_caps={k:bool(raw_caps.get(k)) for k in client_cap_keys if k in raw_caps}
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    token_enc=None
    if token:
        if not os.getenv('FERNET_KEY'): return {'status':'encryption_unavailable','message':'Не настроено шифрование push-токенов'}
        from app.crypto_utils import encrypt_secret
        token_enc=encrypt_secret(token)
    db=SessionLocal()
    try:
        row=db.execute(text('''INSERT INTO telephony_devices
          (id,account_id,user_id,name,platform,app_version,push_kind,push_token_hash,push_token_enc,capabilities_json,presence,last_seen_at)
          VALUES (:id,:a,:u,:n,:p,:v,:pk,:ph,:pe,CAST(:c AS jsonb),'online',now())
          ON CONFLICT (id) DO UPDATE SET name=excluded.name, platform=excluded.platform,
          app_version=excluded.app_version, user_id=COALESCE(excluded.user_id,telephony_devices.user_id),
          push_kind=CASE WHEN telephony_devices.platform=excluded.platform
                         THEN COALESCE(excluded.push_kind,telephony_devices.push_kind)
                         ELSE excluded.push_kind END,
          push_token_hash=CASE WHEN telephony_devices.platform=excluded.platform
                               THEN COALESCE(excluded.push_token_hash,telephony_devices.push_token_hash)
                               ELSE excluded.push_token_hash END,
          push_token_enc=CASE WHEN telephony_devices.platform=excluded.platform
                              THEN COALESCE(excluded.push_token_enc,telephony_devices.push_token_enc)
                              ELSE excluded.push_token_enc END,
          capabilities_json=(COALESCE(telephony_devices.capabilities_json,'{}'::jsonb)
                             - 'calls' - 'crm' - 'notifications' - 'media' - 'background' - 'native_call_ui')
                             || excluded.capabilities_json,
          presence='online',last_seen_at=now(),updated_at=now()
          WHERE telephony_devices.account_id=excluded.account_id AND telephony_devices.revoked_at IS NULL
            AND (telephony_devices.user_id IS NULL OR excluded.user_id IS NULL OR telephony_devices.user_id=excluded.user_id)
          RETURNING id,revoked_at'''),
          {'id':device_id,'a':account_id,'u':user_id,'n':name[:120],'p':platform[:40],'v':app_version[:40],
           'pk':kind,'ph':token_hash,'pe':token_enc,'c':json.dumps(client_caps,ensure_ascii=False)}).mappings().first()
        if not row:
            existing=db.execute(text("SELECT account_id,revoked_at FROM telephony_devices WHERE id=:d"),{'d':device_id}).mappings().first()
            db.rollback()
            if existing and existing.get('account_id')!=account_id: return {'status':'device_conflict'}
            if existing and existing.get('revoked_at') is None: return {'status':'device_conflict_user','device_id':device_id,'message':'Этот идентификатор устройства уже принадлежит другому пользователю.'}
            return {'status':'revoked','device_id':device_id,'message':'Устройство отключено владельцем. Создайте новое подключение.'}
        db.commit()
    finally: db.close()
    return {'status':'ok','device_id':device_id}

def set_device_extension(account_id: str, device_id: str, extension: str | None) -> dict[str,Any]:
    """Owner-managed PBX extension used for safe employee transfer shortcuts."""
    ensure_schema(); raw=str(extension or '').strip(); ext=re.sub(r'[^0-9*#]','',raw)[:16]
    if raw and (not ext or ext!=raw): return {'status':'invalid_extension','message':'Добавочный может содержать только цифры, * и #'}
    db=SessionLocal()
    try:
        row=db.execute(text("""UPDATE telephony_devices
          SET capabilities_json=jsonb_set(COALESCE(capabilities_json,'{}'::jsonb),'{extension}',to_jsonb(CAST(:ext AS text)),true),updated_at=now()
          WHERE account_id=:a AND id=:d AND revoked_at IS NULL RETURNING id,name,platform,capabilities_json"""),{'ext':ext,'a':account_id,'d':device_id}).mappings().first()
        db.commit()
    finally: db.close()
    if not row:return {'status':'not_found'}
    return {'status':'ok','device_id':device_id,'extension':ext or None}

def update_device_push_token(account_id: str, device_id: str, push_kind: str, push_token: str) -> dict[str,Any]:
    """Rotate a native push token for an already authenticated BORIS Phone device.
    Token is encrypted at rest; responses/logs never expose the raw token.
    """
    ensure_schema()
    pdb=SessionLocal()
    try:
        platform=pdb.execute(text("SELECT platform FROM telephony_devices WHERE account_id=:a AND id=:d AND revoked_at IS NULL"),{'a':account_id,'d':device_id}).scalar()
    finally:
        pdb.close()
    if not platform: return {'status':'not_found'}
    kind,token,push_error=_validate_native_push_material(push_kind,push_token,str(platform))
    if push_error: return {'status':push_error}
    if not kind or not token: return {'status':'invalid_push_token'}
    if not os.getenv('FERNET_KEY'): return {'status':'encryption_unavailable'}
    from app.crypto_utils import encrypt_secret
    token_hash=hashlib.sha256(token.encode()).hexdigest(); token_enc=encrypt_secret(token)
    db=SessionLocal()
    try:
        row=db.execute(text("""UPDATE telephony_devices SET push_kind=:k,push_token_hash=:h,push_token_enc=:e,updated_at=now()
          WHERE account_id=:a AND id=:d AND revoked_at IS NULL RETURNING id,platform,push_kind,push_token_hash"""),
          {'k':kind,'h':token_hash,'e':token_enc,'a':account_id,'d':device_id}).mappings().first()
        db.commit()
    finally: db.close()
    if not row:return {'status':'not_found'}
    return {'status':'ok','device_id':str(row['id']),'platform':str(row['platform'] or ''),'push_kind':str(row['push_kind'] or ''),'token_fingerprint':str(row['push_token_hash'] or '')[:12]}

def clear_device_push_token(account_id: str, device_id: str) -> dict[str,Any]:
    """Forget a rotated/invalidated mobile push token without deleting the device."""
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("""UPDATE telephony_devices
          SET push_kind=NULL,push_token_hash=NULL,push_token_enc=NULL,updated_at=now()
          WHERE account_id=:a AND id=:d AND revoked_at IS NULL
          RETURNING id,platform"""),{'a':account_id,'d':device_id}).mappings().first()
        if row:
            db.execute(text("""UPDATE telephony_push_outbox SET status='cancelled',last_error='push token invalidated',updated_at=now()
              WHERE account_id=:a AND device_id=:d AND status IN ('pending','retry','waiting_configuration')"""),{'a':account_id,'d':device_id})
        db.commit()
        return {'status':'ok','device_id':device_id,'push_ready':False} if row else {'status':'not_found'}
    finally: db.close()


def device_runtime_health(account_id: str, device_id: str) -> dict[str,Any]:
    """Truthful device readiness; does not claim native push/media without runtime evidence."""
    ensure_schema(); db=SessionLocal()
    try:
        r=db.execute(text("""SELECT id,name,platform,app_version,presence,last_seen_at,revoked_at,push_kind,push_token_hash,capabilities_json
          FROM telephony_devices WHERE account_id=:a AND id=:d"""),{'a':account_id,'d':device_id}).mappings().first()
        push=db.execute(text("""SELECT
          max(sent_at) FILTER (WHERE status='sent') AS last_provider_accepted_at,
          max(device_received_at) AS last_device_received_at,
          count(*) FILTER (WHERE status='sent') AS provider_accepted_count,
          count(*) FILTER (WHERE device_received_at IS NOT NULL) AS device_received_count
          FROM telephony_push_outbox WHERE account_id=:a AND device_id=:d AND sent_push_token_hash=:ph"""),{'a':account_id,'d':device_id,'ph':str((r or {}).get('push_token_hash') or '')}).mappings().first() or {}
    finally: db.close()
    if not r: return {'status':'not_found','device_id':device_id}
    d=dict(r); last=d.get('last_seen_at'); online=bool(last and not d.get('revoked_at') and (utcnow()-last).total_seconds()<90 and d.get('presence')!='offline')
    comp=client_compatibility(str(d.get('platform') or 'web'),str(d.get('app_version') or '0.0.0'))
    caps=d.get('capabilities_json') if isinstance(d.get('capabilities_json'),dict) else {}
    notifications_capable=(str(d.get('platform') or '').lower()!='android' or bool(caps.get('notifications')))
    return {'status':'ok','device_id':device_id,'platform':d.get('platform'),'presence':d.get('presence'),'online':online,
      'revoked':bool(d.get('revoked_at')),'push_ready':bool(d.get('push_kind') and d.get('push_token_hash')),
      'push_kind':d.get('push_kind'),'compatible':bool(comp.get('supported')),'upgrade_required':bool(comp.get('upgrade_required')),
      'notifications_capable':notifications_capable,'incoming_ui_ready':bool(online and notifications_capable),
      'push_provider_accepted_count':int(push.get('provider_accepted_count') or 0),'push_device_received_count':int(push.get('device_received_count') or 0),
      'last_push_provider_accepted_at':push.get('last_provider_accepted_at'),'last_push_received_at':push.get('last_device_received_at'),
      'media_capable':bool((d.get('capabilities_json') or {}).get('media')),'last_seen_at':last}


def heartbeat_device(account_id: str, device_id: str, presence: str = 'online') -> dict[str, Any]:
    ensure_schema(); allowed={'online','free','busy','dnd','offline'}
    presence=str(presence or 'online').strip().lower()
    # Presence controls ringing eligibility. Unknown client values must never
    # silently promote a DND/offline device back to online.
    if presence not in allowed:
        return {'status':'invalid_presence','device_id':device_id,'presence':presence}
    db=SessionLocal()
    try:
        row=db.execute(text('''UPDATE telephony_devices SET presence=:p,last_seen_at=now(),updated_at=now()
             WHERE id=:id AND account_id=:a AND revoked_at IS NULL RETURNING id'''),{'p':presence,'id':device_id,'a':account_id}).fetchone()
        db.commit()
    finally: db.close()
    return {'status':'ok' if row else 'not_found','device_id':device_id,'presence':presence}


def list_devices(account_id: str) -> list[dict[str, Any]]:
    ensure_schema(); db=SessionLocal()
    try:
        rows=db.execute(text('''SELECT id,user_id,name,platform,app_version,presence,last_seen_at,revoked_at,capabilities_json,push_kind,push_token_hash
              FROM telephony_devices WHERE account_id=:a ORDER BY last_seen_at DESC NULLS LAST'''),{'a':account_id}).mappings().all()
    finally: db.close()
    now=utcnow(); out=[]
    for r in rows:
        d=dict(r); ls=d.get('last_seen_at'); d['online']=bool(ls and not d.get('revoked_at') and str(d.get('presence') or '') not in {'offline',''} and (now-ls).total_seconds()<90)
        d['push_ready']=bool(d.get('push_kind') and d.get('push_token_hash')); d['push_kind']=d.get('push_kind') or None; d.pop('push_token_hash',None)
        caps=d.get('capabilities_json') if isinstance(d.get('capabilities_json'),dict) else {}
        ext=re.sub(r'[^0-9*#]','',str(caps.get('extension') or ''))[:16]
        d['capabilities_json']={'media':bool(caps.get('media')),'extension':ext or None}
        out.append(d)
    return out



def sweep_stale_devices(account_id: str|None=None, stale_seconds: int=90) -> dict[str,Any]:
    """Mark sleeping/disconnected clients offline. Heartbeat will restore them automatically."""
    ensure_schema(); stale_seconds=max(30,min(int(stale_seconds),600)); db=SessionLocal()
    try:
        if account_id:
            rows=db.execute(text("""UPDATE telephony_devices SET presence='offline',updated_at=now()
              WHERE account_id=:a AND revoked_at IS NULL AND presence<>'offline'
                AND (last_seen_at IS NULL OR last_seen_at < now()-(:sec||' seconds')::interval) RETURNING id"""),{'a':account_id,'sec':stale_seconds}).fetchall()
        else:
            rows=db.execute(text("""UPDATE telephony_devices SET presence='offline',updated_at=now()
              WHERE revoked_at IS NULL AND presence<>'offline'
                AND (last_seen_at IS NULL OR last_seen_at < now()-(:sec||' seconds')::interval) RETURNING id"""),{'sec':stale_seconds}).fetchall()
        db.commit(); return {'status':'ok','offline_marked':len(rows)}
    finally: db.close()


def _route_local_devices(account_id: str, route: dict[str,Any]|None) -> list[dict[str,Any]]:
    route=route or {}; kind=str(route.get('destination_kind') or 'device').lower(); value=str(route.get('destination_value') or 'all_online')
    db=SessionLocal()
    try:
        base="""SELECT id,user_id,name,platform,presence,last_seen_at,capabilities_json FROM telephony_devices
          WHERE account_id=:a AND revoked_at IS NULL AND last_seen_at>=now()-interval '90 seconds'
            AND presence IN ('online','free')
            AND (lower(COALESCE(platform,''))<>'android' OR COALESCE((capabilities_json->>'notifications')::boolean,false))"""
        params={'a':account_id}
        if kind=='device' and value and value!='all_online': base += " AND id=:v"; params['v']=value
        elif kind=='user' and value:
            try: params['u']=int(value); base += " AND user_id=:u"
            except Exception: return []
        elif kind in {'sip','external'}: return []
        # group currently means all available account devices; once team groups are canonical it can narrow here.
        rows=[dict(x) for x in db.execute(text(base+" ORDER BY CASE platform WHEN 'desktop' THEN 0 WHEN 'windows' THEN 0 WHEN 'macos' THEN 0 WHEN 'ios' THEN 1 WHEN 'android' THEN 1 ELSE 2 END,last_seen_at DESC"),params).mappings().all()]
        strategy=str(route.get('strategy') or 'ring_all').strip().lower()
        if strategy=='round_robin' and rows:
            ids=[x['id'] for x in rows]
            last={x['device_id']:x['last_at'] for x in db.execute(text("SELECT device_id,max(created_at) last_at FROM telephony_call_targets WHERE account_id=:a AND device_id=ANY(:ids) GROUP BY device_id"),{'a':account_id,'ids':ids}).mappings().all()}
            rows.sort(key=lambda x:(last.get(x['id']) is not None,last.get(x['id']) or datetime.min.replace(tzinfo=timezone.utc)))
            rows=rows[:1]
        elif strategy=='least_busy' and rows:
            ids=[x['id'] for x in rows]
            active={x['device_id']:int(x['n']) for x in db.execute(text("SELECT device_id,count(*) n FROM telephony_calls WHERE account_id=:a AND device_id=ANY(:ids) AND state IN ('active','on_hold','transferring') GROUP BY device_id"),{'a':account_id,'ids':ids}).mappings().all()}
            today={x['device_id']:int(x['n']) for x in db.execute(text("SELECT device_id,count(*) n FROM telephony_calls WHERE account_id=:a AND device_id=ANY(:ids) AND answered_at>=date_trunc('day',now()) GROUP BY device_id"),{'a':account_id,'ids':ids}).mappings().all()}
            rows.sort(key=lambda x:(active.get(x['id'],0),today.get(x['id'],0),-(x.get('last_seen_at').timestamp() if x.get('last_seen_at') else 0)))
            rows=rows[:1]
        return rows
    finally: db.close()


def _queue_device_push(account_id: str, device_id: str, call_id: str, event_type: str) -> None:
    """Durable opaque push intent, serialized cross-process per device/call/event."""
    db=SessionLocal()
    try:
        intent=f'{account_id}|{device_id}|{call_id}|{event_type}'
        lock_id=int.from_bytes(hashlib.blake2b(intent.encode('utf-8','ignore'),digest_size=8).digest(),'big',signed=False)
        if lock_id >= (1 << 63): lock_id -= (1 << 64)
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock_id})
        dev=db.execute(text("SELECT push_kind,push_token_enc,revoked_at FROM telephony_devices WHERE account_id=:a AND id=:d"),{'a':account_id,'d':device_id}).mappings().first()
        if not dev or dev.get('revoked_at') is not None or not dev.get('push_kind') or not dev.get('push_token_enc'): return
        if str(event_type or '')=='call.ringing':
            live=db.execute(text("""SELECT c.state,t.status AS target_status,t.expires_at
              FROM telephony_calls c LEFT JOIN telephony_call_targets t
                ON t.account_id=c.account_id AND t.call_id=c.id AND t.device_id=:d
              WHERE c.account_id=:a AND c.id=:c"""),{'a':account_id,'c':call_id,'d':device_id}).mappings().first()
            expires=(live or {}).get('expires_at')
            if not (live and str(live.get('state') or '')=='ringing' and str(live.get('target_status') or '')=='ringing' and (expires is None or expires>utcnow())):
                return
        exists=db.execute(text("SELECT 1 FROM telephony_push_outbox WHERE account_id=:a AND device_id=:d AND call_id=:c AND event_type=:e AND status IN ('pending','retry','waiting_configuration','sent') LIMIT 1"),{'a':account_id,'d':device_id,'c':call_id,'e':event_type}).first()
        if not exists:
            db.execute(text('''INSERT INTO telephony_push_outbox(account_id,device_id,call_id,event_type,payload_json)
              VALUES(:a,:d,:c,:e,CAST(:p AS jsonb))'''),{'a':account_id,'d':device_id,'c':call_id,'e':event_type,'p':json.dumps({'call_id':call_id,'event':event_type},ensure_ascii=False)})
        db.commit()
    finally: db.close()


def push_outbox(account_id: str|None=None, limit: int=100) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal(); limit=max(1,min(int(limit),500))
    try:
        if account_id:
            rows=db.execute(text("SELECT id,account_id,device_id,call_id,event_type,payload_json,status,attempts,last_error,created_at,sent_at,device_received_at,device_received_via FROM telephony_push_outbox WHERE account_id=:a ORDER BY id DESC LIMIT :l"),{'a':account_id,'l':limit}).mappings().all()
        else:
            rows=db.execute(text("SELECT id,account_id,device_id,call_id,event_type,payload_json,status,attempts,last_error,created_at,sent_at,device_received_at,device_received_via FROM telephony_push_outbox ORDER BY id DESC LIMIT :l"),{'l':limit}).mappings().all()
        return [dict(x) for x in rows]
    finally: db.close()


def ack_mobile_push(account_id: str, device_id: str, push_id: int, via: str='native', receipt_token: str|None=None) -> dict[str,Any]:
    """Record proof that an authenticated BORIS Phone client actually received a push intent.

    Provider HTTP 200 only means APNs/FCM accepted the request. This receipt is
    account+device scoped and is the only evidence used for the device-delivery gate.
    """
    ensure_schema()
    try: pid=int(push_id)
    except Exception: return {'status':'invalid_push_id'}
    if pid<=0: return {'status':'invalid_push_id'}
    channel=str(via or 'native').strip().lower()[:40]
    if channel not in {'android','ios','native','webview'}: channel='native'
    db=SessionLocal()
    try:
        row=db.execute(text("""UPDATE telephony_push_outbox
          SET device_received_at=COALESCE(device_received_at,now()),device_received_via=COALESCE(device_received_via,:v),updated_at=now()
          WHERE id=:i AND account_id=:a AND device_id=:d AND status='sent'
            AND sent_push_token_hash=:ph
            AND EXISTS (SELECT 1 FROM telephony_devices td WHERE td.account_id=:a AND td.id=:d AND td.revoked_at IS NULL AND td.push_token_hash=:ph)
          RETURNING id,call_id,event_type,device_received_at,device_received_via"""),{'i':pid,'a':account_id,'d':device_id,'v':channel,'ph':hashlib.sha256(str(receipt_token or '').encode()).hexdigest() if receipt_token else ''}).mappings().first()
        db.commit()
    finally: db.close()
    if not row: return {'status':'not_found_or_not_sent_or_unverified_device'}
    _audit(account_id,'push.device_received','ok',None,call_id=row.get('call_id'),metadata={'push_id':pid,'device_id':device_id,'via':row.get('device_received_via')})
    return {'status':'ok','push_id':pid,'received_at':row.get('device_received_at'),'via':row.get('device_received_via')}


def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

_APNS_JWT_CACHE: dict[str,Any] = {'token':None,'issued_at':0}
_FCM_TOKEN_CACHE: dict[str,Any] = {'token':None,'expires_at':0}

def _apns_auth_token() -> str:
    """Create an ES256 APNs provider JWT without exposing key material."""
    import time
    now=int(time.time())
    cached=_APNS_JWT_CACHE.get('token')
    if cached and now-int(_APNS_JWT_CACHE.get('issued_at') or 0)<45*60:return str(cached)
    team=os.getenv('BORIS_APNS_TEAM_ID','').strip(); kid=os.getenv('BORIS_APNS_KEY_ID','').strip(); pem=os.getenv('BORIS_APNS_PRIVATE_KEY','').replace('\\n','\n').strip()
    if not team or not kid or not pem: raise RuntimeError('apns_credentials_missing')
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    header=_b64url(json.dumps({'alg':'ES256','kid':kid},separators=(',',':')).encode())
    payload=_b64url(json.dumps({'iss':team,'iat':now},separators=(',',':')).encode())
    signing=(header+'.'+payload).encode('ascii')
    key=serialization.load_pem_private_key(pem.encode(),password=None)
    der=key.sign(signing,ec.ECDSA(hashes.SHA256())); r,ss=decode_dss_signature(der)
    raw=r.to_bytes(32,'big')+ss.to_bytes(32,'big'); token=header+'.'+payload+'.'+_b64url(raw)
    _APNS_JWT_CACHE.update({'token':token,'issued_at':now}); return token

def _send_apns_push(device_token: str, payload: dict[str,Any]) -> tuple[str,str|None]:
    import httpx
    bundle=os.getenv('BORIS_APNS_BUNDLE_ID','').strip()
    if not bundle:return 'waiting_configuration','apns bundle id not configured'
    env=os.getenv('BORIS_APNS_ENV','production').strip().lower(); host='https://api.sandbox.push.apple.com' if env=='sandbox' else 'https://api.push.apple.com'
    body={'aps':{'content-available':1},'call_id':str(payload.get('call_id') or ''),'event':str(payload.get('event') or ''),'push_id':str(payload.get('push_id') or '')}
    try:
        token=_apns_auth_token()
        with httpx.Client(http2=True,timeout=8.0) as client:
            r=client.post(f'{host}/3/device/{device_token}',headers={'authorization':'bearer '+token,'apns-topic':bundle+'.voip','apns-push-type':'voip','apns-priority':'10','apns-expiration':'0'},json=body)
        if r.status_code==200:return 'sent',None
        try: reason=str((r.json() or {}).get('reason') or '')[:120]
        except Exception: reason='http_'+str(r.status_code)
        if r.status_code in {429,500,503}:return 'retry',f'apns {r.status_code} {reason}'.strip()
        return 'failed',f'apns {r.status_code} {reason}'.strip()
    except Exception as exc:
        return 'retry',('apns transport '+exc.__class__.__name__)[:180]

def _load_fcm_service_account() -> dict[str,Any]:
    raw=os.getenv('BORIS_FCM_SERVICE_ACCOUNT_JSON','').strip()
    if not raw: raise RuntimeError('fcm_credentials_missing')
    if raw.startswith('{'): return json.loads(raw)
    path=Path(raw)
    if path.is_file(): return json.loads(path.read_text())
    raise RuntimeError('fcm_credentials_invalid')

def _fcm_service_assertion(now:int|None=None) -> str:
    """Build the RS256 OAuth service-account assertion. Split out so crypto can be verified offline."""
    import time
    ts=int(now or time.time()); sa=_load_fcm_service_account(); email=str(sa.get('client_email') or ''); pem=str(sa.get('private_key') or '')
    if not email or not pem: raise RuntimeError('fcm_credentials_invalid')
    header=_b64url(b'{"alg":"RS256","typ":"JWT"}')
    claims=_b64url(json.dumps({'iss':email,'scope':'https://www.googleapis.com/auth/firebase.messaging','aud':'https://oauth2.googleapis.com/token','iat':ts,'exp':ts+3600},separators=(',',':')).encode())
    signing=(header+'.'+claims).encode('ascii')
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key=serialization.load_pem_private_key(pem.encode(),password=None); sig=key.sign(signing,padding.PKCS1v15(),hashes.SHA256())
    return header+'.'+claims+'.'+_b64url(sig)

def _fcm_access_token() -> str:
    import time,httpx
    now=int(time.time()); cached=_FCM_TOKEN_CACHE.get('token')
    if cached and now<int(_FCM_TOKEN_CACHE.get('expires_at') or 0)-60:return str(cached)
    assertion=_fcm_service_assertion(now)
    with httpx.Client(timeout=8.0) as client:
        r=client.post('https://oauth2.googleapis.com/token',data={'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion})
    if r.status_code!=200: raise RuntimeError('fcm_oauth_http_'+str(r.status_code))
    data=r.json(); token=str(data.get('access_token') or ''); expires=int(data.get('expires_in') or 3600)
    if not token: raise RuntimeError('fcm_oauth_token_missing')
    _FCM_TOKEN_CACHE.update({'token':token,'expires_at':now+expires}); return token

def _send_fcm_push(device_token: str, payload: dict[str,Any]) -> tuple[str,str|None]:
    import httpx
    project=os.getenv('BORIS_FCM_PROJECT_ID','').strip()
    if not project:return 'waiting_configuration','fcm project not configured'
    body={'message':{'token':device_token,'data':{'call_id':str(payload.get('call_id') or ''),'event':str(payload.get('event') or ''),'push_id':str(payload.get('push_id') or '')},'android':{'priority':'HIGH'}}}
    try:
        access=_fcm_access_token()
        with httpx.Client(timeout=8.0) as client:
            r=client.post(f'https://fcm.googleapis.com/v1/projects/{project}/messages:send',headers={'authorization':'Bearer '+access},json=body)
        if r.status_code in {200,201}:return 'sent',None
        if r.status_code in {429,500,502,503,504}:return 'retry','fcm http '+str(r.status_code)
        return 'failed','fcm http '+str(r.status_code)
    except Exception as exc:
        return 'retry',('fcm transport '+exc.__class__.__name__)[:180]

def _sanitize_push_error(value: Any, fallback: str='push failed') -> str:
    """Persist only bounded, credential-redacted APNs/FCM diagnostics."""
    msg=str(value or fallback)[:2000]
    msg=re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+',r'\1[REDACTED]',msg)
    msg=re.sub(r'(?i)((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|client[_-]?secret|token|secret|password|private[_-]?key)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',msg)
    msg=re.sub(r'(?i)([?&](?:token|key|secret|signature|sig|auth)=)[^&\s]+',r'\1[REDACTED]',msg)
    return (msg[:240] or fallback)


def _sanitize_provider_status(value: Any, credentials: Any=None, fallback: str='provider_error') -> str:
    """Return a stable status token without persisting provider credential material.

    Adapter status is untrusted just like error/payload fields. Some SDKs echo
    credential values as a plain alphanumeric status; punctuation-only filtering
    does not redact those values. Remove exact configured credentials and common
    auth/query forms before reducing the value to a bounded status token.
    """
    raw=str(value or fallback)[:2000]
    def _walk(v):
        if isinstance(v,dict):
            for vv in v.values(): yield from _walk(vv)
        elif isinstance(v,(list,tuple,set)):
            for vv in v: yield from _walk(vv)
        elif v is not None:
            yield str(v)
    for secret_value in sorted({x for x in _walk(credentials) if len(x)>=4},key=len,reverse=True):
        raw=raw.replace(secret_value,'[REDACTED]')
    raw=re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+',r'\1[REDACTED]',raw)
    raw=re.sub(r'(?i)((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|client[_-]?secret|token|secret|password|private[_-]?key)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',raw)
    raw=re.sub(r'(?i)([?&](?:token|key|secret|signature|sig|auth)=)[^&\s]+',r'\1[REDACTED]',raw)
    token=re.sub(r'[^A-Za-z0-9._:-]+','_',raw).strip('_.:-')[:100]
    if not token or 'REDACTED' in token.upper():
        return fallback
    return token


def dispatch_mobile_pushes(limit: int=100, account_id:str|None=None, include_synthetic: bool=False) -> dict[str,Any]:
    """Durable mobile push dispatch without holding DB locks across APNs/FCM I/O.

    A due intent receives a two-minute `next_attempt_at` execution lease before
    network send. Crash recovery is automatic when the lease expires; no second
    queue/status domain is introduced.
    """
    ensure_schema(); cap=max(1,min(int(limit),500)); processed=sent=waiting=failed=0
    apns_ready=bool(os.getenv('BORIS_APNS_TEAM_ID') and os.getenv('BORIS_APNS_KEY_ID') and os.getenv('BORIS_APNS_PRIVATE_KEY') and os.getenv('BORIS_APNS_BUNDLE_ID'))
    fcm_ready=bool(os.getenv('BORIS_FCM_PROJECT_ID') and os.getenv('BORIS_FCM_SERVICE_ACCOUNT_JSON'))
    # `waiting_configuration` is a sleeping state, not a five-minute retry loop.
    # Wake rows only after the exact platform configuration and device token exist.
    # This avoids perpetual queue churn while still resuming automatically when
    # operators install real APNs/FCM credentials later.
    cdb=SessionLocal(); configuration_requeued=0
    try:
        configuration_requeued=cdb.execute(text("""UPDATE telephony_push_outbox o
          SET status='pending',next_attempt_at=now(),last_error='configuration available; requeued',updated_at=now()
          FROM telephony_devices d
          WHERE o.status='waiting_configuration' AND d.id=o.device_id AND d.account_id=o.account_id
            AND d.revoked_at IS NULL AND d.push_token_enc IS NOT NULL AND d.push_token_enc<>''
            AND ((d.platform='ios' AND d.push_kind='apns_voip' AND :apns_ready)
              OR (d.platform='android' AND d.push_kind='fcm' AND :fcm_ready))
            AND (:account_id IS NULL OR o.account_id=:account_id)
            AND (:include_synthetic OR NOT (lower(o.account_id) ~ '^__.*qa' OR lower(o.account_id) ~ '^qa[-_]'))
          RETURNING o.id"""),{'apns_ready':apns_ready,'fcm_ready':fcm_ready,'account_id':account_id,'include_synthetic':bool(include_synthetic)}).rowcount or 0
        cdb.commit()
    finally:
        cdb.close()
    # Terminalize any legacy/exhausted retry row before provider I/O. This keeps
    # the hard maximum at five actual sends even after a crash or older runtime
    # left attempts=5 in retry state.
    xdb=SessionLocal()
    try:
        exhausted=xdb.execute(text("""UPDATE telephony_push_outbox SET status='failed',last_error=COALESCE(last_error,'retry limit reached'),next_attempt_at=NULL,updated_at=now()
          WHERE status='retry' AND attempts>=5
            AND (:account_id IS NULL OR account_id=:account_id)
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
          RETURNING id"""),{'account_id':account_id,'include_synthetic':bool(include_synthetic)}).rowcount or 0
        xdb.commit(); failed+=int(exhausted)
    finally:
        xdb.close()
    # APNs/FCM acceptance is not device delivery. If a ringing push was accepted
    # but no authenticated device receipt arrives, requeue the SAME outbox intent
    # with bounded attempts while the call and this device target are still ringing.
    # This preserves one durable push_id and avoids both silent loss and duplicate rows.
    rdb=SessionLocal()
    try:
        receipt_requeued=rdb.execute(text("""UPDATE telephony_push_outbox o
          SET status='retry',next_attempt_at=now(),last_error='provider accepted; device receipt missing',updated_at=now()
          FROM telephony_calls c, telephony_call_targets t
          WHERE o.status='sent' AND o.device_received_at IS NULL AND o.event_type='call.ringing'
            AND o.sent_at IS NOT NULL AND o.sent_at<now()-interval '20 seconds' AND o.attempts<3
            AND c.account_id=o.account_id AND c.id=o.call_id AND c.state='ringing'
            AND t.account_id=o.account_id AND t.call_id=o.call_id AND t.device_id=o.device_id AND t.status='ringing'
            AND (:account_id IS NULL OR o.account_id=:account_id)
            AND (:include_synthetic OR NOT (lower(o.account_id) ~ '^__.*qa' OR lower(o.account_id) ~ '^qa[-_]'))
          RETURNING o.id"""),{'account_id':account_id,'include_synthetic':bool(include_synthetic)}).rowcount or 0
        terminal_receipt_requeued=rdb.execute(text("""UPDATE telephony_push_outbox o
          SET status='retry',next_attempt_at=now(),last_error='provider accepted terminal event; device receipt missing',updated_at=now()
          FROM telephony_calls c
          WHERE o.status='sent' AND o.device_received_at IS NULL
            AND o.event_type IN ('call.transferred','call.ended','call.missed','call.rejected','call.busy','call.failed','call.cancelled')
            AND o.sent_at IS NOT NULL AND o.sent_at<now()-interval '20 seconds' AND o.attempts<3
            AND c.account_id=o.account_id AND c.id=o.call_id
            AND c.state IN ('transferred','ended','missed','rejected','busy','failed','cancelled')
            AND (:account_id IS NULL OR o.account_id=:account_id)
            AND (:include_synthetic OR NOT (lower(o.account_id) ~ '^__.*qa' OR lower(o.account_id) ~ '^qa[-_]'))
          RETURNING o.id"""),{'account_id':account_id,'include_synthetic':bool(include_synthetic)}).rowcount or 0
        receipt_requeued=int(receipt_requeued)+int(terminal_receipt_requeued)
        rdb.commit()
    finally:
        rdb.close()
    for _ in range(cap):
        # Phase 1: claim exactly one due intent and release PostgreSQL immediately.
        db=SessionLocal(); raw=None
        try:
            raw=db.execute(text("""SELECT o.id,o.account_id,o.device_id,o.call_id,o.event_type,o.payload_json,o.attempts,o.status,
              d.platform,d.push_kind,d.push_token_enc,d.revoked_at
              FROM telephony_push_outbox o JOIN telephony_devices d ON d.id=o.device_id AND d.account_id=o.account_id
              WHERE o.status IN ('pending','retry')
                AND (o.next_attempt_at IS NULL OR o.next_attempt_at<=now())
                AND (:account_id IS NULL OR o.account_id=:account_id)
                AND (:include_synthetic OR NOT (lower(o.account_id) ~ '^__.*qa' OR lower(o.account_id) ~ '^qa[-_]'))
              ORDER BY o.created_at,o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED"""),{'account_id':account_id,'include_synthetic':bool(include_synthetic)}).mappings().first()
            if raw:
                db.execute(text("UPDATE telephony_push_outbox SET next_attempt_at=now()+interval '2 minutes',updated_at=now() WHERE id=:i"),{'i':raw['id']})
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()
        if not raw:
            break

        r=dict(raw); processed+=1
        def _update(sql: str, params: dict|None=None) -> None:
            udb=SessionLocal()
            try:
                udb.execute(text(sql),{'i':r['id'],**(params or {})})
                udb.commit()
            finally:
                udb.close()

        if r.get('revoked_at') is not None:
            _update("UPDATE telephony_push_outbox SET status='cancelled',last_error='device revoked',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            failed+=1; continue
        kind=str(r.get('push_kind') or '').lower(); platform=str(r.get('platform') or '').lower()
        expected_kind={'ios':'apns_voip','android':'fcm'}.get(platform)
        if not expected_kind or kind!=expected_kind:
            _update("UPDATE telephony_push_outbox SET status='failed',attempts=attempts+1,last_error='push platform/kind mismatch',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            failed+=1; continue
        if not r.get('push_token_enc'):
            _update("UPDATE telephony_push_outbox SET status='waiting_configuration',last_error='push token unavailable',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            waiting+=1; continue
        if kind=='apns_voip':
            ready=bool(os.getenv('BORIS_APNS_TEAM_ID') and os.getenv('BORIS_APNS_KEY_ID') and os.getenv('BORIS_APNS_PRIVATE_KEY') and os.getenv('BORIS_APNS_BUNDLE_ID'))
            transport='apns'
        else:
            ready=bool(os.getenv('BORIS_FCM_PROJECT_ID') and os.getenv('BORIS_FCM_SERVICE_ACCOUNT_JSON'))
            transport='fcm'
        if not ready:
            _update("UPDATE telephony_push_outbox SET status='waiting_configuration',last_error=:e,next_attempt_at=NULL,updated_at=now() WHERE id=:i",{'e':transport+' credentials not configured'})
            waiting+=1; continue
        from app.crypto_utils import decrypt_secret
        try: device_token=decrypt_secret(str(r['push_token_enc']))
        except Exception:
            _update("UPDATE telephony_push_outbox SET status='failed',attempts=attempts+1,last_error='push token decrypt failed',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            failed+=1; continue

        # Revalidate the device immediately before the external provider boundary.
        # The queue claim intentionally releases PostgreSQL before APNs/FCM I/O;
        # without this fresh read a revoke or token rotation that happened after
        # claim could send to a stale/revoked native token.
        vdb=SessionLocal()
        try:
            live=vdb.execute(text("""SELECT platform,push_kind,push_token_hash,push_token_enc,revoked_at
              FROM telephony_devices WHERE account_id=:a AND id=:d"""),
              {'a':r['account_id'],'d':r['device_id']}).mappings().first()
            live_call=vdb.execute(text("""SELECT c.state,t.status AS target_status,t.expires_at
              FROM telephony_calls c LEFT JOIN telephony_call_targets t
                ON t.account_id=c.account_id AND t.call_id=c.id AND t.device_id=:d
              WHERE c.account_id=:a AND c.id=:c"""),
              {'a':r['account_id'],'c':r['call_id'],'d':r['device_id']}).mappings().first()
        finally:
            vdb.close()
        if not live or live.get('revoked_at') is not None:
            _update("UPDATE telephony_push_outbox SET status='cancelled',last_error='device revoked before provider send',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            failed+=1; continue
        if str(r.get('event_type') or '')=='call.ringing':
            expires=(live_call or {}).get('expires_at')
            ringing_live=bool(live_call and str(live_call.get('state') or '')=='ringing' and str(live_call.get('target_status') or '')=='ringing' and (expires is None or expires>utcnow()))
            if not ringing_live:
                _update("UPDATE telephony_push_outbox SET status='cancelled',last_error='call no longer ringing before provider send',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
                failed+=1; continue
        live_platform=str(live.get('platform') or '').lower(); live_kind=str(live.get('push_kind') or '').lower()
        if live_platform!=platform or live_kind!=kind or not live.get('push_token_enc') or not live.get('push_token_hash'):
            _update("UPDATE telephony_push_outbox SET status='waiting_configuration',last_error='device push material changed before provider send',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
            waiting+=1; continue
        live_hash=str(live.get('push_token_hash') or '')
        snapshot_hash=hashlib.sha256(device_token.encode()).hexdigest()
        if live_hash!=snapshot_hash:
            # Token rotated after claim. Use the current encrypted material rather
            # than sending to the stale token captured by the queue SELECT.
            try: device_token=decrypt_secret(str(live['push_token_enc']))
            except Exception:
                _update("UPDATE telephony_push_outbox SET status='failed',attempts=attempts+1,last_error='rotated push token decrypt failed',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
                failed+=1; continue
            if hashlib.sha256(device_token.encode()).hexdigest()!=live_hash:
                _update("UPDATE telephony_push_outbox SET status='failed',attempts=attempts+1,last_error='push token integrity mismatch',next_attempt_at=NULL,updated_at=now() WHERE id=:i")
                failed+=1; continue

        # No dispatcher-owned SQLAlchemy session is alive during external push I/O.
        wire_payload=dict(r.get('payload_json') or {}); wire_payload['push_id']=str(r['id'])
        outcome,error=(_send_apns_push(device_token,wire_payload) if transport=='apns' else _send_fcm_push(device_token,wire_payload))
        if outcome=='sent':
            _update("UPDATE telephony_push_outbox SET status='sent',attempts=attempts+1,last_error=NULL,next_attempt_at=NULL,sent_at=now(),sent_push_token_hash=:ph,updated_at=now() WHERE id=:i",{'ph':live_hash})
            sent+=1
        elif outcome=='retry':
            _update("UPDATE telephony_push_outbox SET status=CASE WHEN attempts>=4 THEN 'failed' ELSE 'retry' END,attempts=attempts+1,last_error=:e,next_attempt_at=CASE WHEN attempts>=4 THEN NULL ELSE now() + make_interval(secs => LEAST(300, 5 * (2 ^ LEAST(attempts,6))::int)) END,updated_at=now() WHERE id=:i",{'e':_sanitize_push_error(error,'push retry')})
            failed+=1 if int(r.get('attempts') or 0)>=4 else 0
        elif outcome=='waiting_configuration':
            _update("UPDATE telephony_push_outbox SET status='waiting_configuration',last_error=:e,next_attempt_at=NULL,updated_at=now() WHERE id=:i",{'e':_sanitize_push_error(error,'push configuration')})
            waiting+=1
        else:
            _update("UPDATE telephony_push_outbox SET status='failed',attempts=attempts+1,last_error=:e,next_attempt_at=NULL,updated_at=now() WHERE id=:i",{'e':_sanitize_push_error(error,'push failed')})
            failed+=1
    return {'status':'ok','processed':processed,'sent':sent,'waiting_configuration':waiting,'failed':failed,
            'receipt_requeued':int(receipt_requeued),'configuration_requeued':int(configuration_requeued)}

def cancel_call_pushes(account_id: str, call_id: str, except_device_id: str|None=None) -> int:
    """Cancel undelivered ringing push intents once another device wins or the call ends."""
    ensure_schema(); db=SessionLocal()
    try:
        q="""UPDATE telephony_push_outbox SET status='cancelled',last_error='call no longer ringing',updated_at=now()
             WHERE account_id=:a AND call_id=:c AND event_type='call.ringing'
               AND status IN ('pending','retry','waiting_configuration')"""
        params={'a':account_id,'c':call_id}
        if except_device_id:
            q += " AND device_id<>:d"; params['d']=except_device_id
        rows=db.execute(text(q+" RETURNING id"),params).fetchall(); db.commit(); return len(rows)
    finally: db.close()


def _queue_terminal_call_pushes(account_id: str, call_id: str, event_type: str) -> dict[str,Any]:
    """Wake only native devices previously bound/targeted to this call so stale system UI closes."""
    event_type=str(event_type or '').strip().lower()
    allowed={'call.transferred','call.ended','call.missed','call.rejected','call.busy','call.failed','call.cancelled'}
    if event_type not in allowed:
        return {'status':'ignored','candidates':0}
    db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT DISTINCT d.id,d.platform
          FROM telephony_devices d
          WHERE d.account_id=:a AND d.revoked_at IS NULL
            AND lower(COALESCE(d.platform,'')) IN ('android','ios')
            AND d.id IN (
              SELECT t.device_id FROM telephony_call_targets t
               WHERE t.account_id=:a AND t.call_id=:c AND t.device_id IS NOT NULL
              UNION
              SELECT c.device_id FROM telephony_calls c
               WHERE c.account_id=:a AND c.id=:c AND c.device_id IS NOT NULL
            )"""),{'a':account_id,'c':call_id}).mappings().all()
    finally:
        db.close()
    queued=0
    for row in rows:
        try:
            _queue_device_push(account_id,str(row['id']),call_id,event_type)
            queued+=1
        except Exception:
            pass
    return {'status':'ok','candidates':len(rows),'queued_attempts':queued}


def prepare_call_targets(account_id: str, call_id: str, stage: str='primary', route_override: dict[str,Any]|None=None) -> dict[str,Any]:
    """Resolve one inbound call to all currently eligible BORIS Phone devices."""
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,direction,state,source,source_ref,to_number FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally: db.close()
    if not call: return {'status':'not_found','targets':[]}
    if call.get('direction')!='inbound': return {'status':'not_inbound','targets':[]}
    resolved={'status':'override','route':route_override} if route_override is not None else resolve_route(account_id,call.get('source'),call.get('source_ref'),call.get('to_number')); route=resolved.get('route') or {}
    devices=_route_local_devices(account_id,route)
    timeout=max(5,min(int(route.get('ring_timeout_sec') or 25),120)); route_id=route.get('id')
    # In no-answer mode managers keep the full configured answer window before BORIS takes over.
    try:
        acfg=afterhours_settings(account_id)['settings']
        if acfg.get('enabled') and acfg.get('autoanswer_mode')=='no_answer': timeout=max(timeout,max(5,min(int(acfg.get('no_answer_seconds') or 25),120)))
    except Exception: pass
    db=SessionLocal(); inserted=0
    try:
        for dev in devices:
            r=db.execute(text("""INSERT INTO telephony_call_targets(account_id,call_id,device_id,user_id,status,priority,route_id,expires_at,stage)
              VALUES(:a,:c,:d,:u,'ringing',:p,:r,now()+(:sec||' seconds')::interval,:stage)
              ON CONFLICT(account_id,call_id,device_id) DO UPDATE SET
                status=CASE WHEN telephony_call_targets.status IN ('rejected','timeout','cancelled') THEN telephony_call_targets.status ELSE 'ringing' END,
                expires_at=greatest(telephony_call_targets.expires_at,excluded.expires_at),updated_at=now()
              RETURNING id"""),{'a':account_id,'c':call_id,'d':dev['id'],'u':dev.get('user_id'),'p':int(route.get('priority') or 100),'r':route_id,'sec':timeout,'stage':stage[:20]}).first()
            if r: inserted+=1
        db.commit()
        targets=[dict(x) for x in db.execute(text("""SELECT t.*,d.name,d.platform,d.presence FROM telephony_call_targets t
          LEFT JOIN telephony_devices d ON d.id=t.device_id WHERE t.account_id=:a AND t.call_id=:c ORDER BY t.priority,t.id"""),{'a':account_id,'c':call_id}).mappings().all()]
    finally: db.close()
    for dev in devices:
        if str(dev.get('platform') or '').lower() in {'android','ios'}:
            try: _queue_device_push(account_id,str(dev['id']),call_id,'call.ringing')
            except Exception: pass
    _audit(account_id,'call.targets.prepare','ok',call_id=call_id,metadata={'eligible':len(devices),'route_status':resolved.get('status'),'route_id':route_id,'stage':stage,'strategy':route.get('strategy') or 'ring_all'})
    return {'status':'ok','route':route,'targets':targets,'eligible':len(devices)}


def call_targets(account_id: str, call_id: str) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal()
    try:
        return [dict(x) for x in db.execute(text("""SELECT t.*,d.name,d.platform,d.presence,d.last_seen_at FROM telephony_call_targets t
          LEFT JOIN telephony_devices d ON d.id=t.device_id WHERE t.account_id=:a AND t.call_id=:c ORDER BY t.priority,t.id"""),{'a':account_id,'c':call_id}).mappings().all()]
    finally: db.close()


def ringing_for_device(account_id: str, device_id: str) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT c.*,t.expires_at,t.status AS target_status,t.user_id AS target_user_id
          FROM telephony_call_targets t JOIN telephony_calls c ON c.id=t.call_id AND c.account_id=t.account_id
          WHERE t.account_id=:a AND t.device_id=:d AND t.status='ringing' AND c.state='ringing'
            AND (t.expires_at IS NULL OR t.expires_at>now()) ORDER BY c.started_at DESC"""),{'a':account_id,'d':device_id}).mappings().all()
        return [dict(x) for x in rows]
    finally: db.close()


def claim_call_target(account_id: str, call_id: str, device_id: str, user_id: int|None=None) -> dict[str,Any]:
    """Atomic multi-device answer claim. Exactly one device wins the call."""
    if not device_id: return {'status':'device_required'}
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first()
        if not call: return {'status':'not_found'}
        if call.get('device_id'):
            return {'status':'ok' if call.get('device_id')==device_id else 'claimed_elsewhere','device_id':call.get('device_id')}
        if call.get('state') not in {'ringing','active'}: return {'status':'not_ringing','state':call.get('state')}
        target=db.execute(text("""SELECT * FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND device_id=:d
          AND status='ringing' AND (expires_at IS NULL OR expires_at>now()) FOR UPDATE"""),{'a':account_id,'c':call_id,'d':device_id}).mappings().first()
        if not target: return {'status':'not_targeted'}
        dev=db.execute(text("SELECT user_id,presence,last_seen_at,revoked_at FROM telephony_devices WHERE account_id=:a AND id=:d"),{'a':account_id,'d':device_id}).mappings().first()
        if not dev or dev.get('revoked_at') is not None or not dev.get('last_seen_at') or (utcnow()-dev['last_seen_at']).total_seconds()>90 or dev.get('presence') in {'dnd','offline'}:
            return {'status':'device_unavailable'}
        owner_user=user_id if user_id is not None else dev.get('user_id')
        db.execute(text("UPDATE telephony_calls SET device_id=:d,user_id=COALESCE(:u,user_id),updated_at=now() WHERE account_id=:a AND id=:c"),{'d':device_id,'u':owner_user,'a':account_id,'c':call_id})
        db.execute(text("UPDATE telephony_call_targets SET status='answered',answered_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':account_id,'c':call_id,'d':device_id})
        db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=now() WHERE account_id=:a AND call_id=:c AND device_id<>:d AND status='ringing'"),{'a':account_id,'c':call_id,'d':device_id})
        db.execute(text("UPDATE telephony_devices SET presence='busy',updated_at=now() WHERE account_id=:a AND id=:d"),{'a':account_id,'d':device_id})
        db.commit()
    finally: db.close()
    try: cancel_call_pushes(account_id,call_id,device_id)
    except Exception: pass
    _audit(account_id,'call.target.claim','ok',user_id,call_id,metadata={'device_id':device_id})
    return {'status':'ok','device_id':device_id,'user_id':owner_user}


def _reconcile_answer_provider_success(account_id: str, call_id: str, device_id: str, user_id: int|None=None) -> dict[str,Any]:
    """Materialize provider answer success without reopening the provider boundary."""
    if not device_id:
        return {'status':'device_required'}
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text("SELECT state,device_id,user_id FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE"),
                        {'a':account_id,'c':call_id}).mappings().first()
        if not call:
            db.rollback(); return {'status':'call_not_found'}
        current_device=str(call.get('device_id') or '')
        if current_device and current_device!=str(device_id):
            db.rollback(); return {'status':'claimed_elsewhere','device_id':current_device}
        current_state=str(call.get('state') or '')
        if current_state in FINAL_STATES:
            db.rollback(); return {'status':'final_state','state':current_state,'device_id':current_device or None}
        target=db.execute(text("SELECT id,user_id,status FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND device_id=:d FOR UPDATE"),
                          {'a':account_id,'c':call_id,'d':device_id}).mappings().first()
        owner_user=user_id if user_id is not None else ((target or {}).get('user_id') if target else call.get('user_id'))
        next_state='active' if _call_transition_allowed(current_state,'active') else current_state
        db.execute(text("UPDATE telephony_calls SET device_id=COALESCE(device_id,:d),user_id=COALESCE(:u,user_id), state=:s,answered_at=COALESCE(answered_at,now()),updated_at=now() WHERE account_id=:a AND id=:c"),
          {'d':device_id,'u':owner_user,'s':next_state,'a':account_id,'c':call_id})
        if target:
            db.execute(text("UPDATE telephony_call_targets SET status='answered',answered_at=COALESCE(answered_at,now()), ended_at=NULL,updated_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),
              {'a':account_id,'c':call_id,'d':device_id})
        db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=COALESCE(ended_at,now()),updated_at=now() WHERE account_id=:a AND call_id=:c AND device_id<>:d AND status='ringing'"),
          {'a':account_id,'c':call_id,'d':device_id})
        db.execute(text("UPDATE telephony_devices SET presence='busy',updated_at=now() WHERE account_id=:a AND id=:d AND revoked_at IS NULL"),{'a':account_id,'d':device_id})
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    try: cancel_call_pushes(account_id,call_id,device_id)
    except Exception: pass
    _audit(account_id,'call.target.provider_answer_reconcile','ok',user_id,call_id,metadata={'device_id':device_id,'prior_state':current_state,'target_prior_status':(target or {}).get('status') if target else None})
    return {'status':'ok','device_id':device_id,'user_id':owner_user,'state':next_state}


def reject_call_target(account_id: str, call_id: str, device_id: str) -> dict[str,Any]:
    if not device_id: return {'status':'device_required'}
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("""UPDATE telephony_call_targets SET status='rejected',ended_at=now(),updated_at=now()
          WHERE account_id=:a AND call_id=:c AND device_id=:d AND status='ringing' RETURNING id"""),{'a':account_id,'c':call_id,'d':device_id}).first()
        remaining=db.execute(text("SELECT count(*) FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND status='ringing' AND (expires_at IS NULL OR expires_at>now())"),{'a':account_id,'c':call_id}).scalar() or 0
        db.commit()
    finally: db.close()
    return {'status':'ok' if row else 'not_targeted','remaining':int(remaining),'last_target':bool(row and not remaining)}


def sweep_ring_targets(account_id: str|None=None) -> dict[str,Any]:
    """Expire targets, advance one fallback stage, then mark exhausted calls missed."""
    ensure_schema(); db=SessionLocal(); params={}; clause=''
    if account_id: clause=' AND account_id=:a'; params['a']=account_id
    try:
        expired=db.execute(text("UPDATE telephony_call_targets SET status='timeout',ended_at=now(),updated_at=now() WHERE status='ringing' AND expires_at<=now()"+clause+" RETURNING call_id,account_id"),params).mappings().all()
        pairs={(x['account_id'],x['call_id']) for x in expired}; db.commit()
    finally: db.close()
    missed=0; fallback_started=0
    for a,c in pairs:
        db=SessionLocal()
        try:
            live=db.execute(text("SELECT count(*) FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND status='ringing'"),{'a':a,'c':c}).scalar() or 0
            call=db.execute(text("SELECT state,source,source_ref,to_number FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':a,'c':c}).mappings().first()
            used_fallback=db.execute(text("SELECT 1 FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND stage='fallback' LIMIT 1"),{'a':a,'c':c}).first()
        finally: db.close()
        if live or not call or call.get('state')!='ringing': continue
        resolved=resolve_route(a,call.get('source'),call.get('source_ref'),call.get('to_number')); route=resolved.get('route') or {}
        fk=str(route.get('fallback_kind') or '').strip().lower(); fv=route.get('fallback_value')
        if not used_fallback and fk in {'device','user','group'}:
            fallback={'id':route.get('id'),'priority':int(route.get('priority') or 100)+100,'destination_kind':fk,'destination_value':fv or ('all_online' if fk=='device' else ''),'ring_timeout_sec':route.get('ring_timeout_sec') or 25,'strategy':'ring_all'}
            result=prepare_call_targets(a,c,'fallback',fallback)
            db=SessionLocal()
            try: active_targets=db.execute(text("SELECT count(*) FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND status='ringing'"),{'a':a,'c':c}).scalar() or 0
            finally: db.close()
            if active_targets: fallback_started+=1; continue
        ring_sec=int(route.get('ring_timeout_sec') or 25)
        try:
            auto=autoanswer_decision(a,ring_sec,False)
            if auto.get('should_answer'):
                take=request_autoanswer_takeover(a,c,ring_sec,'human_no_answer')
                if take.get('status')=='requested': fallback_started+=1;continue
        except Exception: pass
        apply_event(a,'call.missed',c,None,None,'local-timeout-'+uuid.uuid4().hex,{'reason':'route_targets_exhausted','fallback_kind':fk or None}); missed+=1
    return {'status':'ok','expired':len(expired),'fallback_started':fallback_started,'missed':missed}

def _bind_existing_contact(account_id: str, direction: str, from_number: str, to_number: str, db=None) -> int | None:
    """Resolve existing CRM contact; reuse caller transaction when supplied."""
    phone=normalize_phone(from_number if direction=='inbound' else to_number)
    if not phone: return None
    last10=re.sub(r'\D','',phone)[-10:]
    own_db=db is None
    if own_db: db=SessionLocal()
    try:
        owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':account_id}).scalar()
        if owner is None: return None
        return db.execute(text("""SELECT id FROM boris_crm_contacts WHERE owner_user_id=:o
          AND RIGHT(regexp_replace(COALESCE(primary_phone,''),'\\D','','g'),10)=:p ORDER BY id LIMIT 1"""),{'o':int(owner),'p':last10}).scalar()
    finally:
        if own_db: db.close()


def create_call(account_id: str, direction: str, from_number: str, to_number: str,
                provider: str | None = None, provider_call_id: str | None = None,
                user_id: int | None = None, device_id: str | None = None,
                source: str | None = None, source_ref: str | None = None,
                metadata: dict | None = None, db=None) -> dict[str, Any]:
    ensure_schema(); direction = direction if direction in {'inbound','outbound'} else 'outbound'
    own_db=db is None
    if own_db: db=SessionLocal()
    if not provider:
        provider=str(db.execute(text("SELECT provider FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).scalar() or '').strip().lower() or None
    try:
        if provider_call_id:
            row=db.execute(text('''SELECT * FROM telephony_calls WHERE account_id=:a AND provider=:p AND provider_call_id=:pc'''),
                           {'a':account_id,'p':provider,'pc':provider_call_id}).mappings().first()
            if row: return dict(row)
        cid=_call_id()
        crm_contact_id=_bind_existing_contact(account_id,direction,from_number,to_number,db=db)
        recording_policy=str(db.execute(text("SELECT policy FROM telephony_recording_settings WHERE account_id=:a"),{'a':account_id}).scalar() or 'manual').strip().lower()
        notice_status={'manual':'unknown','disabled':'disabled','not_required':'not_required'}.get(recording_policy,'unknown')
        params={'id':cid,'a':account_id,'p':provider,'pc':provider_call_id,'d':direction,
                'f':normalize_phone(from_number),'t':normalize_phone(to_number),'u':user_id,'dev':device_id,
                's':source,'sr':source_ref,'cc':crm_contact_id,'rn':notice_status,
                'm':json.dumps(_sanitize_event_payload(metadata or {}),ensure_ascii=False)}
        if provider and provider_call_id:
            row=db.execute(text('''INSERT INTO telephony_calls
              (id,account_id,provider,provider_call_id,direction,from_number,to_number,user_id,device_id,state,source,source_ref,crm_contact_id,recording_notice_status,metadata_json)
              VALUES (:id,:a,:p,:pc,:d,:f,:t,:u,:dev,'new',:s,:sr,:cc,:rn,CAST(:m AS jsonb))
              ON CONFLICT (account_id,provider,provider_call_id)
                WHERE provider IS NOT NULL AND provider_call_id IS NOT NULL AND provider_call_id<>''
              DO NOTHING RETURNING *'''),params).mappings().first()
            if not row:
                row=db.execute(text('''SELECT * FROM telephony_calls WHERE account_id=:a AND provider=:p AND provider_call_id=:pc'''),params).mappings().one()
        else:
            row=db.execute(text('''INSERT INTO telephony_calls
              (id,account_id,provider,provider_call_id,direction,from_number,to_number,user_id,device_id,state,source,source_ref,crm_contact_id,recording_notice_status,metadata_json)
              VALUES (:id,:a,:p,:pc,:d,:f,:t,:u,:dev,'new',:s,:sr,:cc,:rn,CAST(:m AS jsonb)) RETURNING *'''),params).mappings().one()
        if own_db: db.commit()
        return dict(row)
    finally:
        if own_db: db.close()



def _complete_callback_if_answered(account_id: str, call_id: str) -> dict[str,Any]:
    """When an outbound callback is actually answered, close matching missed-call SLA and CRM tasks."""
    db=SessionLocal(); matched=[]
    try:
        call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call or call.get('direction')!='outbound' or call.get('answered_at') is None: return {'status':'not_applicable','matched':0}
        phone=normalize_phone(call.get('to_number'))
        if not phone: return {'status':'no_phone','matched':0}
        last10=re.sub(r'\D','',phone)[-10:]
        rows=db.execute(text("""SELECT id,crm_contact_id FROM telephony_calls WHERE account_id=:a AND direction='inbound'
          AND callback_status='required' AND started_at<=:at
          AND RIGHT(regexp_replace(COALESCE(from_number,''),'\\D','','g'),10)=:p ORDER BY started_at DESC LIMIT 20"""),
          {'a':account_id,'at':call['started_at'],'p':last10}).mappings().all()
        owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':account_id}).scalar()
        for r in rows:
            db.execute(text("UPDATE telephony_calls SET callback_status='completed',updated_at=now() WHERE account_id=:a AND id=:id"),{'a':account_id,'id':r['id']})
            matched.append(str(r['id']))
            if owner is not None:
                refs=[f"boris_callback:{account_id}:{r['id']}",f"boris_callback_overdue:{r['id']}"]
                db.execute(text("""UPDATE boris_crm_tasks SET status='done',completed_at=now()
                  WHERE owner_user_id=:o AND status='open' AND description=ANY(:refs)"""),{'o':int(owner),'refs':refs})
        db.commit()
    finally: db.close()
    if matched:_audit(account_id,'callback.completed','ok',call_id=call_id,metadata={'missed_call_ids':matched})
    try: callback_funnel_link(account_id,call_id)
    except Exception: pass
    return {'status':'ok','matched':len(matched),'missed_call_ids':matched}

_EVENT_SECRET_KEYS={'authorization','api_key','apikey','api_secret','access_token','refresh_token','token','secret','password','private_key','client_secret','webhook_secret','boris_secret'}

def _sanitize_event_payload(value: Any, depth: int=0):
    """Remove credential-shaped material before event persistence/SSE exposure.

    Provider adapters are external trust boundaries. Keep canonical call metadata,
    but never rely on every current/future adapter remembering to strip secrets.
    """
    if depth>8:
        return '[TRUNCATED]'
    if isinstance(value,dict):
        out={}
        for k,v in value.items():
            key=str(k or '').strip().lower().replace('-','_')
            if key in _EVENT_SECRET_KEYS or key.endswith('_secret') or key.endswith('_token') or key.endswith('_private_key'):
                out[k]='[REDACTED]'
            else:
                out[k]=_sanitize_event_payload(v,depth+1)
        return out
    if isinstance(value,(list,tuple)):
        return [_sanitize_event_payload(v,depth+1) for v in value[:200]]
    if isinstance(value,str):
        msg=value
        msg=re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+',r'\1[REDACTED]',msg)
        msg=re.sub(r'(?i)((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|client[_-]?secret|webhook[_-]?secret|boris[_-]?secret|private[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;&]+',r'\1[REDACTED]',msg)
        msg=re.sub(r'(?i)([?&](?:token|key|secret|signature|sig|auth)=)[^&\s]+',r'\1[REDACTED]',msg)
        return msg[:8000]
    if isinstance(value,(int,float,bool)) or value is None:
        return value
    return str(value)[:1000]



def _public_call_view(row: Any) -> dict[str,Any] | None:
    """Account/UI-safe call representation without provider recording transport secrets."""
    if not row:
        return None
    out=dict(row)
    recording_url=out.pop('recording_url',None)
    out['recording_source_received']=bool(str(recording_url or '').strip())
    return out


def _public_recording_view(row: Any) -> dict[str,Any] | None:
    """Account/UI-safe recording representation; signed URLs and host paths stay backend-only."""
    if not row:
        return None
    out=dict(row)
    source_url=out.pop('source_url',None)
    local_path=out.pop('local_path',None)
    out['source_received']=bool(str(source_url or '').strip())
    out['local_materialized']=bool(str(local_path or '').strip())
    return out


def apply_event(account_id: str, event_type: str, call_id: str | None = None,
                provider: str | None = None, provider_call_id: str | None = None,
                provider_event_id: str | None = None, payload: dict | None = None) -> dict[str, Any]:
    ensure_schema(); payload=_sanitize_event_payload(payload or {}); provider=provider or selected_provider(account_id)
    event_type=str(event_type or '').lower().strip()
    state_map={'call.started':'connecting','call.ringing':'ringing','call.answered':'active','call.hold':'on_hold',
               'call.resumed':'active','call.transferring':'transferring','call.transferred':'transferred',
               'call.ended':'ended','call.missed':'missed','call.rejected':'rejected','call.busy':'busy',
               'call.failed':'failed','call.cancelled':'cancelled'}
    state=state_map.get(event_type)
    db=SessionLocal()
    try:
        if provider_event_id:
            # Serialize one provider event across both API replicas before any
            # call creation/state mutation. The unique index remains the durable
            # invariant; this lock turns concurrent provider retries into a clean
            # duplicate response instead of an IntegrityError/500 race.
            intent=f'{account_id}|{provider or ""}|{provider_event_id}'
            lock_id=int.from_bytes(hashlib.blake2b(intent.encode('utf-8','ignore'),digest_size=8).digest(),'big',signed=False)
            if lock_id >= (1 << 63): lock_id -= (1 << 64)
            db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock_id})
            exists=db.execute(text('''SELECT id FROM telephony_events WHERE account_id=:a AND provider=:p AND provider_event_id=:e'''),
                              {'a':account_id,'p':provider,'e':provider_event_id}).fetchone()
            if exists: return {'status':'duplicate','event_id':exists[0]}
        if not call_id and provider_call_id:
            call_id=db.execute(text('''SELECT id FROM telephony_calls WHERE account_id=:a AND provider=:p AND provider_call_id=:pc'''),
                               {'a':account_id,'p':provider,'pc':provider_call_id}).scalar()
        if not call_id:
            c=create_call(account_id, str(payload.get('direction') or 'inbound'), str(payload.get('from') or ''),
                          str(payload.get('to') or ''), provider, provider_call_id, source=payload.get('source'),
                          source_ref=payload.get('source_ref'), metadata=payload, db=db)
            call_id=c['id']
        db.execute(text('''INSERT INTO telephony_events(account_id,call_id,provider,provider_event_id,event_type,payload_json)
              VALUES (:a,:c,:p,:e,:t,CAST(:j AS jsonb))'''),
              {'a':account_id,'c':call_id,'p':provider,'e':provider_event_id,'t':event_type,
               'j':json.dumps(payload,ensure_ascii=False)})
        if state:
            current_call=db.execute(text('SELECT state,direction FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE'),{'a':account_id,'c':call_id}).mappings().first()
            current_state=(current_call or {}).get('state')
            current_direction=str((current_call or {}).get('direction') or '')
            if not _call_transition_allowed(str(current_state or ''),state):
                db.execute(text('''INSERT INTO telephony_audit(account_id,action,result,call_id,provider,metadata_json)
                    VALUES(:a,'call.state.transition','ignored_out_of_order',:c,:p,CAST(:m AS jsonb))'''),
                    {'a':account_id,'c':call_id,'p':provider,'m':json.dumps({'from':current_state,'to':state,'event_type':event_type},ensure_ascii=False)})
                state=None
        if state:
            sets=['state=:s','updated_at=now()']; params={'s':state,'a':account_id,'c':call_id}
            if state=='ringing': sets.append('ringing_at=coalesce(ringing_at,now())')
            if state=='active': sets.append('answered_at=coalesce(answered_at,now())')
            if state in FINAL_STATES:
                sets.append('ended_at=coalesce(ended_at,now())')
                sets.append("talk_duration_sec=CASE WHEN answered_at IS NULL THEN 0 ELSE greatest(0,extract(epoch from (now()-answered_at))::int) END")
                sets.append("wait_duration_sec=greatest(0,extract(epoch from (coalesce(answered_at,now())-started_at))::int)")
            if state=='missed' and current_direction=='inbound':
                sets.append("callback_due_at=coalesce(callback_due_at,now()+interval '15 minutes')")
                sets.append("callback_status=coalesce(callback_status,'required')")
            db.execute(text(f"UPDATE telephony_calls SET {','.join(sets)} WHERE account_id=:a AND id=:c"),params)
        db.commit()
        row=db.execute(text('SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':account_id,'c':call_id}).mappings().first()
        # Own calltracking is an enrichment layer, not a provider transport. Attribute
        # inbound calls as soon as the canonical call is committed so CRM/routing/ROP
        # see the advertising source without any owner/manual sync step. Fail closed:
        # an attribution problem must never block or relabel the phone call itself.
        attribution=None
        if row and str(row.get('direction') or '')=='inbound':
            try:
                from app.services.calltracking_own import attach_call as _attach_own_calltracking
                attribution=_attach_own_calltracking(account_id,call_id)
                if attribution.get('status') in {'ok','existing'}:
                    row=db.execute(text('SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':account_id,'c':call_id}).mappings().first()
            except Exception as exc:
                attribution={'status':'error','error_code':type(exc).__name__[:120]}
        crm=None; callback=None; targets=None
        if state=='ringing':
            try:
                auto=autoanswer_decision(account_id,0,True)
                if auto.get('should_answer'):
                    targets=request_autoanswer_takeover(account_id,call_id,0,'incoming_ringing')
                else: targets=prepare_call_targets(account_id,call_id)
            except Exception as exc: targets={'status':'error','error_code':type(exc).__name__[:120]}
        if state=='active':
            try: callback=_complete_callback_if_answered(account_id,call_id)
            except Exception as exc: callback={'status':'error','error_code':type(exc).__name__[:120]}
        if state in FINAL_STATES:
            try: cancel_call_pushes(account_id,call_id)
            except Exception: pass
            try: _queue_terminal_call_pushes(account_id,call_id,event_type)
            except Exception: pass
            try: crm=sync_call_to_crm(account_id,call_id)
            except Exception as exc: crm={'status':'error','error_code':type(exc).__name__[:120]}
            try:
                metering=meter_completed_call(account_id,call_id)
                minute_alert_guard(account_id)
            except Exception as exc:
                metering={'status':'error','error_code':type(exc).__name__[:120]}
            try:
                db2=SessionLocal()
                try:
                    db2.execute(text("UPDATE telephony_call_targets SET status=CASE WHEN status='answered' THEN 'ended' WHEN status='ringing' THEN 'cancelled' ELSE status END,ended_at=COALESCE(ended_at,now()) WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id})
                    if row and row.get('device_id'): db2.execute(text("UPDATE telephony_devices SET presence='free',updated_at=now() WHERE account_id=:a AND id=:d AND revoked_at IS NULL"),{'a':account_id,'d':row.get('device_id')})
                    db2.commit()
                finally: db2.close()
            except Exception: pass
        return {'status':'ok','call':_public_call_view(row),'crm':crm,'callback':callback,'targets':targets,'attribution':attribution}
    finally: db.close()


def list_calls(account_id: str, days: int=30, limit: int=100) -> list[dict[str, Any]]:
    ensure_schema(); days=max(1,min(int(days),365)); limit=max(1,min(int(limit),500)); db=SessionLocal()
    try:
        rows=db.execute(text('''SELECT * FROM telephony_calls WHERE account_id=:a
          AND started_at>=now()-(:days||' days')::interval ORDER BY started_at DESC LIMIT :lim'''),
          {'a':account_id,'days':days,'lim':limit}).mappings().all()
        return [_public_call_view(r) for r in rows]
    finally: db.close()


def call_metrics(account_id: str, days: int=30) -> dict[str, Any]:
    ensure_schema(); db=SessionLocal(); days=max(1,min(int(days),365))
    try:
        r=db.execute(text('''SELECT count(*) total,
          count(*) FILTER (WHERE direction='inbound') inbound,
          count(*) FILTER (WHERE direction='outbound') outbound,
          count(*) FILTER (WHERE answered_at IS NOT NULL) answered,
          count(*) FILTER (WHERE state='missed') missed,
          coalesce(sum(talk_duration_sec),0) talk_seconds,
          coalesce(avg(wait_duration_sec) FILTER (WHERE direction='inbound'),0) avg_wait_seconds
          FROM telephony_calls WHERE account_id=:a AND started_at>=now()-(:days||' days')::interval'''),
          {'a':account_id,'days':days}).mappings().one()
        overdue=db.execute(text('''SELECT count(*) FROM telephony_calls WHERE account_id=:a
            AND callback_status='required' AND callback_due_at<now()'''),{'a':account_id}).scalar() or 0
        cost=db.execute(text('''SELECT coalesce(sum(amount_rub),0) FROM telephony_cost_ledger WHERE account_id=:a
            AND created_at>=now()-(:days||' days')::interval'''),{'a':account_id,'days':days}).scalar() or 0
        d=dict(r); d['callback_overdue']=int(overdue); d['cost_rub']=float(cost); return d
    finally: db.close()


def telephony_analytics(account_id: str, days: int=30) -> dict[str,Any]:
    ensure_schema(); days=max(1,min(int(days),365)); db=SessionLocal()
    try:
        summary=call_metrics(account_id,days)
        sources=[dict(x) for x in db.execute(text('''SELECT COALESCE(NULLIF(source,''),'Телефония') source,
          count(*) calls,count(*) FILTER (WHERE answered_at IS NOT NULL) answered,
          count(*) FILTER (WHERE state='missed') missed,
          count(*) FILTER (WHERE crm_deal_id IS NOT NULL) with_deal,
          coalesce(avg(wait_duration_sec) FILTER (WHERE direction='inbound'),0) avg_wait_seconds,
          coalesce(avg(talk_duration_sec) FILTER (WHERE answered_at IS NOT NULL),0) avg_talk_seconds
          FROM telephony_calls WHERE account_id=:a AND started_at>=now()-(:days||' days')::interval
          GROUP BY 1 ORDER BY calls DESC,1 LIMIT 50'''),{'a':account_id,'days':days}).mappings().all()]
        managers=[dict(x) for x in db.execute(text('''SELECT c.user_id,c.device_id,COALESCE(d.name,'Не назначено') device_name,
          count(*) calls,count(*) FILTER (WHERE c.answered_at IS NOT NULL) answered,
          count(*) FILTER (WHERE c.state='missed') missed,
          coalesce(sum(c.talk_duration_sec),0) talk_seconds,
          count(*) FILTER (WHERE c.crm_deal_id IS NOT NULL) with_deal
          FROM telephony_calls c LEFT JOIN telephony_devices d ON d.id=c.device_id AND d.account_id=c.account_id
          WHERE c.account_id=:a AND c.started_at>=now()-(:days||' days')::interval
          GROUP BY c.user_id,c.device_id,d.name ORDER BY answered DESC,calls DESC LIMIT 100'''),{'a':account_id,'days':days}).mappings().all()]
        callbacks=dict(db.execute(text('''SELECT
          count(*) FILTER (WHERE callback_status='required') required,
          count(*) FILTER (WHERE callback_status='completed') completed,
          count(*) FILTER (WHERE callback_status='required' AND callback_due_at<now()) overdue
          FROM telephony_calls WHERE account_id=:a AND started_at>=now()-(:days||' days')::interval'''),{'a':account_id,'days':days}).mappings().one())
        costs=cost_breakdown(account_id,days)
        return {'status':'ok','days':days,'summary':summary,'sources':sources,'managers':managers,'callbacks':callbacks,'costs':costs}
    finally: db.close()


def _provider_public_config(account_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("SELECT public_config_json FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).scalar()
        return row if isinstance(row,dict) else {}
    finally: db.close()


def request_outbound_call(account_id: str, to_number: str, user_id: int | None = None,
                          device_id: str | None = None, source: str | None = None,
                          source_ref: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    """Start exactly one outbound provider intent.

    The durable intent is committed before provider I/O. Replaying the same key
    never calls the operator twice. A crash/timeout after the provider boundary
    remains ambiguous/fail-closed until webhook/operator evidence reconciles it.
    """
    # Fail closed before any provider/config lookup: outbound destinations must be
    # canonical E.164-like public numbers. Do not send short/service/garbled input
    # to an operator and rely on provider-side rejection.
    to=normalize_phone(to_number)
    if not re.fullmatch(r'\+[1-9]\d{6,14}',to):
        return {'status':'invalid_number','message':'Проверьте номер телефона'}
    p=selected_provider(account_id)
    if not p:
        return {'status':'provider_not_selected','message':'Сначала подключите оператора телефонии'}
    # Commercial fail-closed boundary: a stored provider config must never be
    # enough to originate a paid real call after Phone expires or before Phone
    # is activated. Check this before credentials, adapter I/O or durable intent.
    entitlement=phone_entitlement_status(account_id)
    if not bool(entitlement.get('active')):
        return {
            'status':'phone_entitlement_required',
            'message':'BORIS Phone не активен для этого аккаунта. Нужен оплаченный период Phone.',
            'account_id':str(account_id or '')[:160],
        }
    p2,credentials=provider_credentials(account_id)
    if p2!=p or not credentials:
        return {'status':'provider_not_connected','provider':p,'message':'Провайдер выбран, но production-доступ ещё не подтверждён'}
    from app.services.telephony_adapters import get_adapter, adapter_status
    adapter=get_adapter(p); ast=adapter_status(p)
    if not adapter or not ast.get('implemented'):
        return {'status':'adapter_pending','provider':p,'message':'Контур готов, production adapter этого оператора ещё не подключён'}
    idem=str(idempotency_key or '').strip()
    if not (8 <= len(idem) <= 160) or not re.fullmatch(r'[A-Za-z0-9._:-]+',idem):
        return {'status':'idempotency_required','message':'Для безопасного исходящего звонка требуется новый идентификатор попытки'}
    request_hash=hashlib.sha256(json.dumps({
        'account_id':account_id,'to':to,'user_id':user_id,'device_id':device_id,
        'source':source,'source_ref':source_ref,'provider':p,
    },ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    ensure_schema(); idb=SessionLocal()
    try:
        created=idb.execute(text("""INSERT INTO telephony_outbound_intents(account_id,idempotency_key,request_hash,status,provider,to_number)
          VALUES(:a,:k,:h,'processing',:p,:to) ON CONFLICT(account_id,idempotency_key) DO NOTHING
          RETURNING idempotency_key"""),{'a':account_id,'k':idem,'h':request_hash,'p':p,'to':to}).scalar()
        idb.commit()
        if not created:
            prior=idb.execute(text("SELECT * FROM telephony_outbound_intents WHERE account_id=:a AND idempotency_key=:k"),{'a':account_id,'k':idem}).mappings().first()
            if not prior: return {'status':'outbound_intent_unavailable','message':'Не удалось подтвердить безопасный intent звонка'}
            prior=dict(prior)
            if prior.get('request_hash')!=request_hash:
                return {'status':'idempotency_conflict','message':'Этот идентификатор уже использован для другого звонка'}
            if prior.get('status')=='confirmed' and prior.get('call_id'):
                call=idb.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':prior['call_id']}).mappings().first()
                return {'status':'ok','call':_public_call_view(call),'provider':p,'replayed':True}
            if prior.get('status') in {'processing','ambiguous'}:
                return {'status':'outbound_ambiguous','provider':p,'message':'Предыдущая попытка могла быть принята оператором. BORIS не повторяет звонок автоматически.','replayed':True}
            return {'status':'outbound_failed','provider':p,'message':'Предыдущая попытка завершилась без звонка','replayed':True}
    finally: idb.close()

    def _finish_intent(status: str, error: str|None=None, provider_call_id: str|None=None, call_id: str|None=None) -> None:
        udb=SessionLocal()
        try:
            udb.execute(text("""UPDATE telephony_outbound_intents SET status=:s,last_error=:e,
              provider_call_id=COALESCE(:pc,provider_call_id),call_id=COALESCE(:c,call_id),updated_at=now()
              WHERE account_id=:a AND idempotency_key=:k"""),
              {'s':status,'e':(str(error)[:500] if error else None),'pc':provider_call_id,'c':call_id,'a':account_id,'k':idem})
            udb.commit()
        finally: udb.close()

    public=_provider_public_config(account_id)
    try:
        result=adapter.make_call(to_number=to,from_number=public.get('outbound_number'),credentials=credentials,public_config=public,
                                 metadata={'account_id':account_id,'user_id':user_id,'device_id':device_id,'source':source,'source_ref':source_ref,'idempotency_key':idem})
    except Exception as exc:
        # Provider exception strings may contain URLs, query auth, headers or SDK
        # diagnostics. Persist/return only a stable secret-free classification.
        error_type=type(exc).__name__[:120]
        safe_err=(error_type+': provider transport failure')[:300]
        _finish_intent('ambiguous',safe_err)
        _audit(account_id,'call.outbound.start','transport_error',user_id,provider=p,metadata={'error_type':error_type,'idempotency_key':idem})
        return {'status':'outbound_ambiguous','provider':p,'message':'Оператор мог принять запрос. Повторный звонок автоматически не создавался.','error_code':'provider_transport_ambiguous'}
    if not result.ok:
        provider_status=_sanitize_provider_status(result.status,credentials,'provider_error')
        transient=provider_status.lower() in COMMAND_TRANSIENT_STATUSES or int((result.payload or {}).get('http_status') or 0) in {429,500,502,503,504}
        state='ambiguous' if transient else 'failed'
        # Adapter error strings are untrusted and may embed auth/query material.
        # Keep only the normalized provider status outside the adapter boundary.
        _finish_intent(state,'provider status: '+provider_status)
        _audit(account_id,'call.outbound.start',state,user_id,provider=p,metadata={'provider_status':provider_status,'idempotency_key':idem})
        return {'status':'outbound_ambiguous' if transient else provider_status,'provider':p,
                'message':'Оператор мог принять запрос; автоматический повтор запрещён' if transient else 'Оператор не принял звонок',
                'error_code':'provider_'+provider_status}
    provider_call_id=str(result.provider_call_id or '').strip()
    if not provider_call_id:
        _finish_intent('ambiguous','provider accepted without call identity')
        _audit(account_id,'call.outbound.start','provider_identity_ambiguous',user_id,provider=p,metadata={'provider_status':_sanitize_provider_status(result.status,credentials,'provider_accepted'),'idempotency_key':idem})
        return {'status':'outbound_ambiguous','provider':p,'message':'Оператор подтвердил запрос без идентификатора звонка. BORIS не повторяет его автоматически.'}
    call=create_call(account_id,'outbound',str(public.get('outbound_number') or ''),to,p,provider_call_id,user_id,device_id,source,source_ref,result.payload)
    apply_event(account_id,'call.started',call['id'],p,provider_call_id,result.provider_event_id,result.payload)
    _finish_intent('confirmed',provider_call_id=provider_call_id,call_id=call['id'])
    _audit(account_id,'call.outbound.start','ok',user_id,call['id'],p,metadata={'idempotency_key':idem})
    return {'status':'ok','call':call,'provider':p,'replayed':False}


COMMAND_TRANSIENT_STATUSES={'provider_unavailable','timeout','rate_limited','temporarily_unavailable','transport_error'}
COMMAND_HARD_STATUSES={'credentials_invalid','configuration_required','provider_rejected','command_unsupported','invalid_provider_call_id','invalid_command'}

def _command_retryable(result: Any) -> bool:
    status=str(getattr(result,'status','') or '').strip().lower()
    payload=getattr(result,'payload',{}) if isinstance(getattr(result,'payload',{}),dict) else {}
    try: http_status=int(payload.get('http_status') or 0)
    except Exception: http_status=0
    return status in COMMAND_TRANSIENT_STATUSES or http_status==429 or http_status>=500

def dispatch_pending_commands(limit: int=100, include_synthetic: bool=False) -> dict[str,Any]:
    """Claim commands durably, then execute provider I/O with no DB transaction open.

    `processing` is a short execution lease. A crashed worker is recovered after
    two minutes and retried under the existing bounded backoff contract.
    """
    ensure_schema(); processed=0; completed=0; waiting=0; failed=0
    cap=max(1,min(int(limit),500))

    # Do not let legacy/crash residue with five completed attempts reach the
    # provider again. Terminalization is local DB-only and therefore safe.
    xdb=SessionLocal()
    try:
        exhausted=xdb.execute(text("""UPDATE telephony_commands SET status='failed',last_error=COALESCE(last_error,'retry limit reached'),next_attempt_at=NULL,processed_at=COALESCE(processed_at,now()),updated_at=now()
          WHERE status IN ('queued','waiting_provider') AND attempts>=5
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
          RETURNING id"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        xdb.commit(); failed+=int(exhausted)
    finally:
        xdb.close()

    # Crash recovery is bounded and idempotent. Runtime singleton normally makes
    # this zero; it protects deploy/crash edges without a second queue engine.
    db=SessionLocal()
    try:
        recovered=db.execute(text("""UPDATE telephony_commands
          SET status='queued',last_error=COALESCE(last_error,'') || CASE WHEN COALESCE(last_error,'')='' THEN '' ELSE '; ' END || 'recovered stale processing lease',
              next_attempt_at=now(),updated_at=now()
          WHERE status='processing' AND updated_at<now()-interval '2 minutes'
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        db.commit()
    finally:
        db.close()

    for _ in range(cap):
        # Phase 1: atomically claim exactly one due command and release PostgreSQL
        # before touching provider credentials/network.
        db=SessionLocal(); raw=None
        try:
            raw=db.execute(text("""SELECT * FROM telephony_commands
              WHERE status IN ('queued','waiting_provider')
                AND (next_attempt_at IS NULL OR next_attempt_at<=now())
                AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
              ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED"""),{'include_synthetic':bool(include_synthetic)}).mappings().first()
            if raw:
                db.execute(text("UPDATE telephony_commands SET status='processing',updated_at=now() WHERE id=:id"),{'id':raw['id']})
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()
        if not raw:
            break

        cmd=dict(raw); processed+=1; p=cmd.get('provider') or selected_provider(cmd['account_id'])
        from app.services.telephony_adapters import get_adapter,adapter_status
        from app.services.telephony_adapters.base import AdapterResult
        adapter=get_adapter(p); ast=adapter_status(p)

        def _update(sql: str, params: dict|None=None) -> None:
            udb=SessionLocal()
            try:
                udb.execute(text(sql),{'id':cmd['id'],**(params or {})})
                udb.commit()
            finally:
                udb.close()

        if not adapter or not ast.get('implemented'):
            # New commands are blocked before enqueue when the operator contract is
            # unimplemented. Any row reaching the dispatcher is therefore legacy/
            # crash residue and must terminate instead of churning forever.
            _update("UPDATE telephony_commands SET status='failed',attempts=attempts+1,last_error='production adapter unavailable',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue

        # Short read transaction only; reconcile local call truth BEFORE any
        # provider I/O. Stale queued controls must never cause a second answer or
        # act on a call that already reached a terminal state.
        rdb=SessionLocal()
        try:
            call=rdb.execute(text("SELECT provider_call_id,state,device_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':cmd['account_id'],'c':cmd['call_id']}).mappings().first()
            provider_call_id=str(call.get('provider_call_id') or '') if call else ''
        finally:
            rdb.close()
        if not call:
            _update("UPDATE telephony_commands SET status='failed',last_error='call not found',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue
        call_state=str(call.get('state') or '')
        if call_state in FINAL_STATES:
            _update("UPDATE telephony_commands SET status='failed',last_error='call already finished',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue
        if str(cmd.get('command') or '')=='answer' and call.get('device_id'):
            if str(call.get('device_id'))==str(cmd.get('device_id') or ''):
                _update("UPDATE telephony_commands SET status='done',last_error=NULL,next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
                completed+=1
            else:
                _update("UPDATE telephony_commands SET status='failed',last_error='call claimed by another device',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
                failed+=1
            continue
        if not provider_call_id:
            _update("UPDATE telephony_commands SET status='failed',attempts=attempts+1,last_error='provider call id missing',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue

        p2,creds=provider_credentials(cmd['account_id']); public={**_provider_public_config(cmd['account_id']),'__boris_account_id':cmd['account_id']}
        if p2!=p or not creds:
            # Credentials can be temporarily unavailable during rotation/reconnect,
            # but this path is still bounded. Never wake a command every five
            # minutes forever without consuming its retry budget.
            attempts=int(cmd.get('attempts') or 0)
            if attempts < 4:
                _update("""UPDATE telephony_commands SET status='waiting_provider',attempts=attempts+1,
                  last_error='provider credentials unavailable',
                  next_attempt_at=now()+make_interval(secs => LEAST(300,5*(2 ^ LEAST(attempts,6))::int)),updated_at=now()
                  WHERE id=:id""")
                waiting+=1
            else:
                _update("UPDATE telephony_commands SET status='failed',attempts=attempts+1,last_error='provider credentials unavailable; retry limit reached',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
                failed+=1
            continue

        # Revalidate call/device immediately before the operator boundary. The
        # earlier read intentionally does not hold a transaction over credentials
        # or network I/O, so a webhook/revoke/other-device claim may have changed
        # local truth since the queue claim.
        vdb=SessionLocal()
        try:
            live_call=vdb.execute(text("SELECT provider_call_id,state,device_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                  {'a':cmd['account_id'],'c':cmd['call_id']}).mappings().first()
            live_dev=None
            if cmd.get('device_id'):
                live_dev=vdb.execute(text("SELECT revoked_at FROM telephony_devices WHERE account_id=:a AND id=:d"),
                                     {'a':cmd['account_id'],'d':cmd.get('device_id')}).mappings().first()
        finally:
            vdb.close()
        if not live_call or str(live_call.get('state') or '') in FINAL_STATES:
            _update("UPDATE telephony_commands SET status='failed',last_error='call finished before provider send',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue
        if str(live_call.get('provider_call_id') or '') != provider_call_id:
            _update("UPDATE telephony_commands SET status='failed',last_error='provider call identity changed before provider send',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue
        if cmd.get('device_id') and (not live_dev or live_dev.get('revoked_at') is not None):
            _update("UPDATE telephony_commands SET status='failed',last_error='device revoked before provider send',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            failed+=1; continue
        if str(cmd.get('command') or '')=='answer' and live_call.get('device_id'):
            if str(live_call.get('device_id'))==str(cmd.get('device_id') or ''):
                _update("UPDATE telephony_commands SET status='done',last_error=NULL,next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
                completed+=1
            else:
                _update("UPDATE telephony_commands SET status='failed',last_error='call claimed by another device before provider send',next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
                failed+=1
            continue

        payload=cmd.get('payload_json') if isinstance(cmd.get('payload_json'),dict) else {}
        try:
            # No SQLAlchemy session from this dispatcher is alive here.
            res=adapter.command(provider_call_id=provider_call_id,command=cmd['command'],payload=payload,credentials=creds,public_config=public)
        except Exception as exc:
            # Never persist raw provider exception text; it can contain credentials.
            res=AdapterResult(False,'transport_error',error=type(exc).__name__[:120])

        if res.ok:
            if str(cmd.get('command') or '')=='answer':
                # Provider success is an irreversible truth boundary. A target can
                # expire locally while the operator request is in flight, so normal
                # pre-provider claim rules are too strict here. Reconcile the exact
                # command device from provider success; never retry or report the
                # provider-successful answer as failed merely because its ring lease expired.
                claim=_reconcile_answer_provider_success(cmd['account_id'],cmd['call_id'],str(cmd.get('device_id') or ''),cmd.get('requested_by'))
                if claim.get('status') not in {'ok'}:
                    _audit(cmd['account_id'],'call.target.provider_answer_reconcile',str(claim.get('status') or 'unreconciled'),cmd.get('requested_by'),cmd['call_id'],provider=p,metadata={'device_id':cmd.get('device_id')})
                    _update("UPDATE telephony_commands SET status='done',attempts=attempts+1,last_error=:e,next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id",{'e':('provider answer accepted; local reconcile '+str(claim.get('status') or 'unreconciled'))[:500]})
                    completed+=1
                    continue
            elif str(cmd.get('command') or '')=='hangup' and cmd.get('device_id'):
                # For the last ringing device, provider success is also the
                # boundary before local target rejection. Active/answered targets
                # are unaffected because reject_call_target only touches ringing.
                try: reject_call_target(cmd['account_id'],cmd['call_id'],str(cmd.get('device_id')))
                except Exception: pass
            _update("UPDATE telephony_commands SET status='done',attempts=attempts+1,last_error=NULL,next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id")
            completed+=1
        else:
            attempts=int(cmd.get('attempts') or 0)
            retry_safe=set(getattr(adapter,'retry_safe_commands',set()) or set())
            transient=_command_retryable(res)
            # Fail closed after an ambiguous provider transport result. A control
            # command is auto-retried only when the adapter contract explicitly
            # proves that this exact command is safe to repeat. Without that proof,
            # re-sending hold/resume/hangup/transfer/DTMF could duplicate a real
            # operator-side action. The user/provider webhook may reconcile state,
            # but BORIS does not guess.
            safe_provider_status=_sanitize_provider_status(res.status,creds,'provider_command_failed')
            if transient and str(cmd.get('command') or '') in retry_safe and attempts < 4:
                _update("""UPDATE telephony_commands SET status='queued',attempts=attempts+1,last_error=:e,
                  next_attempt_at=now()+make_interval(secs => LEAST(300,5*(2 ^ LEAST(attempts,6))::int)),updated_at=now()
                  WHERE id=:id""",{'e':safe_provider_status})
                waiting+=1
            else:
                err=safe_provider_status
                if transient and str(cmd.get('command') or '') not in retry_safe:
                    err='ambiguous provider outcome; automatic retry disabled: '+safe_provider_status
                _update("UPDATE telephony_commands SET status='failed',attempts=attempts+1,last_error=:e,next_attempt_at=NULL,processed_at=now(),updated_at=now() WHERE id=:id",{'e':str(err)[:500]})
                failed+=1
    return {'status':'ok','processed':processed,'completed':completed,'waiting_provider':waiting,'failed':failed,'recovered_processing':int(recovered)}

def sync_call_to_crm(account_id: str, call_id: str) -> dict[str, Any]:
    """Bridge BORIS Phone into the existing BORIS CRM. Never creates a parallel CRM."""
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text('SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':account_id,'c':call_id}).mappings().first()
        if not call: return {'status':'not_found'}
        # Provider event ingestion and post-call workers can both request the same
        # CRM bridge. Serialize one canonical call so contact/deal/activity/task
        # side effects remain exactly-once across API replicas/runtime workers.
        crm_lock=int.from_bytes(hashlib.blake2b((str(account_id)+'|crm-call-sync|'+str(call_id)).encode(),digest_size=8).digest(),'big',signed=False)
        if crm_lock >= (1 << 63): crm_lock -= (1 << 64)
        db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':crm_lock})
        # Re-read after acquiring the lock in case another worker just completed.
        call=db.execute(text('SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':account_id,'c':call_id}).mappings().first()
        owner=db.execute(text('SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1'),{'a':account_id}).scalar()
        if owner is None: return {'status':'no_owner'}
        owner=int(owner); call=dict(call)
        phone=normalize_phone(call.get('from_number') if call.get('direction')=='inbound' else call.get('to_number'))
        if not phone: return {'status':'no_phone'}
        last10=re.sub(r'\D','',phone)[-10:]
        # Canonical CRM identity is shared with Avito calltracking. Different
        # simultaneous calls from the same phone serialize on owner+phone.
        phone_identity=f'crm-phone|{owner}|{last10}'
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':phone_identity})
        contact=db.execute(text('''SELECT id FROM boris_crm_contacts WHERE owner_user_id=:o
          AND RIGHT(regexp_replace(COALESCE(primary_phone,''),'\\D','','g'),10)=:p ORDER BY id LIMIT 1'''),{'o':owner,'p':last10}).scalar()
        if contact is None:
            contact=db.execute(text('''INSERT INTO boris_crm_contacts(owner_user_id,display_name,primary_phone,source,source_ref,status)
              VALUES(:o,'Клиент по телефону',:p,'boris_telephony',:r,'active') RETURNING id'''),
              {'o':owner,'p':phone,'r':f'boris_phone:{account_id}:{last10}'}).scalar_one()
        contact=int(contact)
        answered=bool(call.get('answered_at'))
        missed=str(call.get('state'))=='missed'
        missed_inbound=missed and str(call.get('direction') or '')=='inbound'
        deal=db.execute(text('''SELECT id FROM boris_crm_deals WHERE owner_user_id=:o AND contact_id=:c
          AND avito_account_id=:a AND status='open' ORDER BY id DESC LIMIT 1'''),{'o':owner,'c':contact,'a':account_id}).scalar()
        if deal is None and (answered or str(call.get('direction'))=='inbound'):
            from app.crm.service import ensure_default_pipeline
            pipeline=int(ensure_default_pipeline(db,owner))
            stage=db.execute(text("SELECT id FROM boris_crm_stages WHERE pipeline_id=:p AND semantic_type='open' ORDER BY position,id LIMIT 1"),{'p':pipeline}).scalar()
            if stage is None:
                stage=db.execute(text('SELECT id FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id LIMIT 1'),{'p':pipeline}).scalar_one()
            deal=db.execute(text('''INSERT INTO boris_crm_deals(owner_user_id,contact_id,pipeline_id,stage_id,title,source,source_ref,
              avito_account_id,responsible_user_id,status,last_activity_at,updated_at)
              VALUES(:o,:c,:p,:s,:title,'boris_telephony',:r,:a,:o,'open',:at,now()) RETURNING id'''),
              {'o':owner,'c':contact,'p':pipeline,'s':int(stage),'title':'Пропущенный входящий звонок' if missed_inbound else 'Телефонный звонок','r':f'boris_call:{account_id}:{call_id}','a':account_id,'at':call.get('started_at')}).scalar_one()
        ref=f'boris_call:{account_id}:{call_id}'
        activity=db.execute(text('SELECT id FROM boris_crm_activities WHERE owner_user_id=:o AND source_ref=:r LIMIT 1'),{'o':owner,'r':ref}).scalar()
        if activity is None:
            title='Пропущенный звонок' if missed_inbound else ('Входящий звонок' if call.get('direction')=='inbound' else 'Исходящий звонок')
            body=f"{phone} · {int(call.get('talk_duration_sec') or 0)//60}:{int(call.get('talk_duration_sec') or 0)%60:02d}"
            meta={'telephony_call_id':call_id,'account_id':account_id,'provider':call.get('provider'),'state':call.get('state'),'source':call.get('source'),'source_ref':call.get('source_ref')}
            activity=db.execute(text('''INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,
              source,source_ref,actor_type,metadata_json,created_at)
              VALUES(:o,:d,:c,'call','boris_telephony',:dir,:t,:b,'boris_telephony',:r,'system',CAST(:m AS jsonb),:at) RETURNING id'''),
              {'o':owner,'d':deal,'c':contact,'dir':'in' if call.get('direction')=='inbound' else 'out','t':title,'b':body,'r':ref,
               'm':json.dumps(meta,ensure_ascii=False),'at':call.get('started_at')}).scalar_one()
        task_id=None
        if missed_inbound:
            task_ref=f'boris_callback:{account_id}:{call_id}'
            exists=db.execute(text('SELECT id FROM boris_crm_tasks WHERE owner_user_id=:o AND description=:r LIMIT 1'),{'o':owner,'r':task_ref}).scalar()
            if exists is None:
                due=call.get('callback_due_at') or (utcnow()+timedelta(minutes=15))
                task_id=db.execute(text('''INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source)
                    VALUES(:o,:d,:c,'Перезвонить по пропущенному звонку',:r,:o,:due,'open','boris_telephony') RETURNING id'''),
                    {'o':owner,'d':deal,'c':contact,'r':task_ref,'due':due}).scalar_one()
        db.execute(text('UPDATE telephony_calls SET crm_contact_id=:cc,crm_deal_id=:dd,updated_at=now() WHERE id=:id AND account_id=:a'),
                   {'cc':contact,'dd':deal,'id':call_id,'a':account_id})
        db.commit(); return {'status':'ok','contact_id':contact,'deal_id':int(deal) if deal else None,'activity_id':int(activity),'task_id':int(task_id) if task_id else None}
    except Exception:
        db.rollback(); raise
    finally: db.close()


def mcn_crm_sync_guardian(limit: int = 100, include_synthetic: bool = False) -> dict[str, Any]:
    """Recover final MCN calls whose canonical CRM bridge did not complete.

    The final provider event already attempts sync_call_to_crm(). This guardian
    is the bounded second chance for transient DB/CRM failures. It only touches
    final MCN calls, waits 30 seconds to avoid racing event ingestion, and
    reuses the same advisory-lock/idempotent CRM bridge.
    """
    ensure_schema()
    limit=max(1,min(int(limit),500))
    params={'l':limit,'include_synthetic':bool(include_synthetic)}
    synthetic="""(:include_synthetic OR NOT (lower(c.account_id) ~ '^__.*qa' OR lower(c.account_id) ~ '^qa[-_]'))"""
    db=SessionLocal()
    try:
        rows=db.execute(text(f"""
          SELECT c.account_id,c.id
          FROM telephony_calls c
          JOIN accounts a ON a.account_id=c.account_id AND a.owner_user_id IS NOT NULL
          WHERE c.provider='mcn'
            AND c.state IN ('transferred','ended','missed','rejected','busy','failed','cancelled')
            AND c.updated_at<now()-interval '30 seconds'
            AND {synthetic}
            AND length(regexp_replace(
                  CASE WHEN c.direction='inbound' THEN COALESCE(c.from_number,'')
                       ELSE COALESCE(c.to_number,'') END,'\\D','','g'))>=10
            AND (
              c.crm_contact_id IS NULL
              OR ((c.answered_at IS NOT NULL OR c.direction='inbound') AND c.crm_deal_id IS NULL)
              OR NOT EXISTS (
                SELECT 1 FROM boris_crm_activities act
                WHERE act.owner_user_id=a.owner_user_id
                  AND act.source_ref=('boris_call:'||c.account_id||':'||c.id)
              )
            )
          ORDER BY c.updated_at
          LIMIT :l
        """),params).all()
    finally:
        db.close()
    completed=failed=0
    errors=[]
    for account_id,call_id in rows:
        try:
            out=sync_call_to_crm(str(account_id),str(call_id))
            if out.get('status')=='ok':
                completed+=1
            else:
                failed+=1
                errors.append({'call_id':str(call_id),'status':str(out.get('status') or 'unknown')[:120]})
        except Exception as exc:
            failed+=1
            errors.append({'call_id':str(call_id),'error_code':type(exc).__name__[:120]})
    return {'status':'ok' if failed==0 else 'degraded','picked':len(rows),
            'completed':completed,'failed':failed,'errors':errors[:10]}


def mcn_terminal_cleanup_guardian(limit: int = 100, include_synthetic: bool = False) -> dict[str, Any]:
    """Close stale local MCN residue after the carrier call is already final.

    This is DB-only cleanup: it never re-sends a provider command. Any queued
    command on a terminal call is unsafe/useless to execute and is marked
    failed locally; ringing/answered targets are terminalized idempotently.
    """
    ensure_schema()
    limit=max(1,min(int(limit),500))
    params={'l':limit,'include_synthetic':bool(include_synthetic)}
    synthetic="""(:include_synthetic OR NOT (lower(c.account_id) ~ '^__.*qa' OR lower(c.account_id) ~ '^qa[-_]'))"""
    db=SessionLocal()
    try:
        rows=db.execute(text(f"""
          SELECT c.account_id,c.id
          FROM telephony_calls c
          WHERE c.provider='mcn'
            AND c.state IN ('transferred','ended','missed','rejected','busy','failed','cancelled')
            AND c.updated_at<now()-interval '30 seconds'
            AND {synthetic}
            AND (
              EXISTS (
                SELECT 1 FROM telephony_call_targets t
                WHERE t.account_id=c.account_id AND t.call_id=c.id
                  AND t.status IN ('ringing','answered')
              )
              OR EXISTS (
                SELECT 1 FROM telephony_commands cmd
                WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                  AND cmd.status NOT IN ('done','failed')
              )
            )
          ORDER BY c.updated_at
          LIMIT :l
        """),params).all()
    finally:
        db.close()

    calls_cleaned=targets_closed=commands_closed=0
    for account_id,call_id in rows:
        db=SessionLocal()
        try:
            t=db.execute(text("""UPDATE telephony_call_targets
              SET status=CASE WHEN status='answered' THEN 'ended' ELSE 'cancelled' END,
                  ended_at=COALESCE(ended_at,now()),updated_at=now()
              WHERE account_id=:a AND call_id=:c AND status IN ('ringing','answered')"""),
              {'a':account_id,'c':call_id}).rowcount or 0
            q=db.execute(text("""UPDATE telephony_commands
              SET status='failed',
                  last_error=COALESCE(NULLIF(last_error,''),'call already finished'),
                  next_attempt_at=NULL,processed_at=COALESCE(processed_at,now()),updated_at=now()
              WHERE account_id=:a AND call_id=:c AND status NOT IN ('done','failed')"""),
              {'a':account_id,'c':call_id}).rowcount or 0
            db.commit()
            calls_cleaned+=1
            targets_closed+=int(t)
            commands_closed+=int(q)
        finally:
            db.close()
    return {'status':'ok','picked':len(rows),'calls_cleaned':calls_cleaned,
            'targets_closed':targets_closed,'commands_closed':commands_closed}


CONTROL_COMMANDS={'answer','hangup','hold','resume','transfer','mute','unmute','dtmf'}

def route_list(account_id: str) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal()
    try:
        return [dict(x) for x in db.execute(text("SELECT * FROM telephony_routes WHERE account_id=:a ORDER BY priority,id"),{'a':account_id}).mappings().all()]
    finally: db.close()

def route_save(account_id: str, body: dict) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        rid=body.get('id')
        params={'a':account_id,'n':str(body.get('name') or 'Основной маршрут')[:160], 'pr':int(body.get('priority') or 100),
          'en':bool(body.get('enabled',True)),'s':body.get('source'),'sr':body.get('source_ref'),'np':body.get('number_pattern'),
          'dk':str(body.get('destination_kind') or 'device')[:40],'dv':body.get('destination_value'),
          'rt':max(5,min(int(body.get('ring_timeout_sec') or 25),120)),'fk':body.get('fallback_kind'),'fv':body.get('fallback_value'),
          'st':str(body.get('strategy') or 'ring_all').strip().lower() if str(body.get('strategy') or 'ring_all').strip().lower() in {'ring_all','round_robin','least_busy'} else 'ring_all',
          'sch':json.dumps(body.get('schedule') or {},ensure_ascii=False)}
        if rid:
            params['id']=int(rid)
            row=db.execute(text("""UPDATE telephony_routes SET name=:n,priority=:pr,enabled=:en,source=:s,source_ref=:sr,
              number_pattern=:np,destination_kind=:dk,destination_value=:dv,ring_timeout_sec=:rt,fallback_kind=:fk,
              fallback_value=:fv,strategy=:st,schedule_json=CAST(:sch AS jsonb),updated_at=now() WHERE id=:id AND account_id=:a RETURNING *"""),params).mappings().first()
        else:
            row=db.execute(text("""INSERT INTO telephony_routes(account_id,name,priority,enabled,source,source_ref,number_pattern,
              destination_kind,destination_value,ring_timeout_sec,fallback_kind,fallback_value,strategy,schedule_json)
              VALUES(:a,:n,:pr,:en,:s,:sr,:np,:dk,:dv,:rt,:fk,:fv,:st,CAST(:sch AS jsonb)) RETURNING *"""),params).mappings().one()
        db.commit(); return {'status':'ok','route':dict(row) if row else None}
    finally: db.close()

def _route_schedule_allows(schedule: dict|None, at: datetime|None=None) -> bool:
    """Schedule shape: timezone, days[0..6], start HH:MM, end HH:MM. Empty means always."""
    schedule=schedule if isinstance(schedule,dict) else {}
    if not schedule: return True
    try:
        from zoneinfo import ZoneInfo
        tz=ZoneInfo(str(schedule.get('timezone') or 'Europe/Moscow'))
        now=(at or utcnow()).astimezone(tz)
        days=schedule.get('days')
        if isinstance(days,list) and days and now.weekday() not in {int(x) for x in days}: return False
        start=str(schedule.get('start') or '').strip(); end=str(schedule.get('end') or '').strip()
        if not start or not end:return True
        sh,sm=[int(x) for x in start.split(':',1)]; eh,em=[int(x) for x in end.split(':',1)]
        cur=now.hour*60+now.minute; a=sh*60+sm; b=eh*60+em
        return a<=cur<b if a<=b else (cur>=a or cur<b)
    except Exception:
        return False


def resolve_route(account_id: str, source: str|None=None, source_ref: str|None=None, to_number: str|None=None) -> dict[str,Any]:
    routes=route_list(account_id); number=normalize_phone(to_number)
    for r in routes:
        if not r.get('enabled'): continue
        if not _route_schedule_allows(r.get('schedule_json')): continue
        if r.get('source') and r.get('source')!=source: continue
        if r.get('source_ref') and r.get('source_ref')!=source_ref: continue
        pat=str(r.get('number_pattern') or '').strip()
        if pat:
            try:
                if not re.search(pat,number): continue
            except re.error: continue
        return {'status':'matched','route':r}
    return {'status':'default','route':{'destination_kind':'device','destination_value':'all_online','ring_timeout_sec':25,'strategy':'ring_all'}}

def queue_call_command(account_id: str, call_id: str, command: str, payload: dict|None=None, user_id: int|None=None, device_id: str|None=None, idempotency_key: str|None=None) -> dict[str,Any]:
    ensure_schema(); command=str(command or '').strip().lower()
    if command not in CONTROL_COMMANDS: return {'status':'invalid_command','allowed':sorted(CONTROL_COMMANDS)}
    payload=dict(payload or {})
    idem=str(idempotency_key or '').strip()
    if idem and not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}',idem):
        return {'status':'invalid_idempotency_key','message':'Некорректный idempotency key команды'}
    if command=='transfer':
        target=str(payload.get('to_number') or payload.get('to') or '').strip()
        target=re.sub(r'[\s().-]+','',target)[:32]
        if not re.fullmatch(r'[+0-9*#]{2,32}',target): return {'status':'invalid_transfer_target','message':'Укажите номер или добавочный для перевода'}
        payload={'to_number':target}
    elif command=='dtmf':
        digits=str(payload.get('digits') or payload.get('value') or '').strip()[:32]
        if not re.fullmatch(r'[0-9*#]{1,32}',digits): return {'status':'invalid_dtmf','message':'Некорректные DTMF-символы'}
        payload={'digits':digits}
    db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,state,provider,direction,device_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call: return {'status':'not_found'}
        if call['state'] in FINAL_STATES: return {'status':'call_finished','state':call['state']}
        p=call.get('provider') or selected_provider(account_id)
        from app.services.telephony_adapters import adapter_status, get_adapter
        adapter_ready=bool(p and adapter_status(p).get('implemented'))
        adapter=get_adapter(p) if adapter_ready else None
        supported=set(getattr(adapter,'supported_commands',set()) or set()) if adapter else set()
        if not adapter_ready:
            return {'status':'provider_command_unavailable','provider':p,'command':command,
                    'supported_commands':[], 'message':'Команда не создана: production contract оператора для управления звонком не подтверждён'}
        if command not in supported:
            return {'status':'provider_command_unsupported','provider':p,'command':command,
                    'supported_commands':sorted(supported),'message':'Оператор не подтверждает эту команду в production contract'}
        # Multi-device decline is local while another target can still answer.
        # The LAST ringing target is never mutated before provider hangup success:
        # a transport timeout must not strand a real ringing call with zero targets.
        if command=='hangup' and call.get('direction')=='inbound' and call.get('state')=='ringing' and device_id:
            target=db.execute(text("""SELECT id FROM telephony_call_targets
              WHERE account_id=:a AND call_id=:c AND device_id=:d AND status='ringing'
                AND (expires_at IS NULL OR expires_at>now()) FOR UPDATE"""),{'a':account_id,'c':call_id,'d':str(device_id)}).first()
            if not target:
                return {'status':'not_targeted','message':'Устройство больше не участвует в этом звонке'}
            other=int(db.execute(text("""SELECT count(*) FROM telephony_call_targets
              WHERE account_id=:a AND call_id=:c AND device_id<>:d AND status='ringing'
                AND (expires_at IS NULL OR expires_at>now())"""),{'a':account_id,'c':call_id,'d':str(device_id)}).scalar() or 0)
            if other>0:
                db.execute(text("UPDATE telephony_call_targets SET status='rejected',ended_at=now(),updated_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d AND status='ringing'"),{'a':account_id,'c':call_id,'d':str(device_id)})
                db.execute(text("""UPDATE telephony_push_outbox SET status='cancelled',last_error='device rejected ringing call',next_attempt_at=NULL,updated_at=now()
                  WHERE account_id=:a AND call_id=:c AND device_id=:d AND event_type='call.ringing' AND status IN ('pending','retry','waiting_configuration')"""),{'a':account_id,'c':call_id,'d':str(device_id)})
                db.commit()
                return {'status':'local_rejected','remaining':other,'message':'Звонок отклонён на этом устройстве'}
        status='queued'
        request_hash=hashlib.sha256(json.dumps({'command':command,'payload':payload or {},'device_id':device_id,'requested_by':user_id},ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
        if idem:
            intent=f'phone-control|{account_id}|{call_id}|{idem}'
            lock_id=int.from_bytes(hashlib.blake2b(intent.encode('utf-8','ignore'),digest_size=8).digest(),'big',signed=False)
            if lock_id >= (1 << 63): lock_id -= (1 << 64)
            db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock_id})
            prior=db.execute(text("SELECT * FROM telephony_commands WHERE account_id=:a AND call_id=:c AND idempotency_key=:i"),{'a':account_id,'c':call_id,'i':idem}).mappings().first()
            if prior:
                if str(prior.get('request_hash') or '')!=request_hash:
                    db.rollback(); return {'status':'idempotency_conflict','message':'Этот idempotency key уже относится к другой команде'}
                db.rollback()
                prior_status=str(prior.get('status') or 'queued')
                safe_status='queued' if prior_status in {'queued','processing','waiting_provider','done'} else prior_status
                return {'status':safe_status,'command':dict(prior),'replayed':True,'original_status':prior_status,'message':'Команда уже была принята'}
        row=db.execute(text("""INSERT INTO telephony_commands(account_id,call_id,command,payload_json,requested_by,device_id,status,provider,idempotency_key,request_hash)
          VALUES(:a,:c,:cmd,CAST(:p AS jsonb),:u,:d,:s,:provider,:idem,:rh) RETURNING *"""),
          {'a':account_id,'c':call_id,'cmd':command,'p':json.dumps(payload or {},ensure_ascii=False),'u':user_id,'d':device_id,'s':status,'provider':p,'idem':idem or None,'rh':request_hash if idem else None}).mappings().one()
        db.commit(); return {'status':status,'command':dict(row),'message':None}
    finally: db.close()

def command_list(account_id: str, call_id: str|None=None, limit: int=100) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal(); limit=max(1,min(int(limit),500))
    try:
        if call_id:
            q="SELECT * FROM telephony_commands WHERE account_id=:a AND call_id=:c ORDER BY created_at DESC LIMIT :l"; p={'a':account_id,'c':call_id,'l':limit}
        else:
            q="SELECT * FROM telephony_commands WHERE account_id=:a ORDER BY created_at DESC LIMIT :l"; p={'a':account_id,'l':limit}
        return [dict(x) for x in db.execute(text(q),p).mappings().all()]
    finally: db.close()

def recording_upsert(account_id: str, call_id: str, provider: str|None, provider_recording_id: str|None, source_url: str|None, duration_sec: int|None=None) -> dict[str,Any]:
    """Idempotent provider recording ingress across both API replicas."""
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text("SELECT id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).first()
        if not call: return {'status':'call_not_found'}
        params={'a':account_id,'c':call_id,'p':provider,'r':provider_recording_id,'u':source_url,'d':duration_sec}
        if provider_recording_id and provider is not None:
            row=db.execute(text("""INSERT INTO telephony_recordings(account_id,call_id,provider,provider_recording_id,source_url,duration_sec,status)
              VALUES(:a,:c,:p,:r,:u,:d,'available')
              ON CONFLICT (account_id,provider,provider_recording_id)
                WHERE provider_recording_id IS NOT NULL AND provider_recording_id<>''
              DO NOTHING RETURNING *"""),params).mappings().first()
            if not row:
                existing=db.execute(text("SELECT * FROM telephony_recordings WHERE account_id=:a AND provider=:p AND provider_recording_id=:r"),params).mappings().one()
                # A provider recording id is immutable evidence for exactly one
                # canonical call.  Never silently reuse it for a different call:
                # that would make recording/STT/AI evidence appear attached to
                # the wrong conversation. Keep the original row untouched.
                if str(existing.get('call_id') or '') != str(call_id):
                    db.rollback()
                    _audit(account_id,'recording.identity.conflict','blocked',call_id=call_id,provider=str(provider or ''),
                           metadata={'provider_recording_id_hash':hashlib.sha256(str(provider_recording_id).encode()).hexdigest()[:16],
                                     'existing_call_id':str(existing.get('call_id') or '')})
                    return {'status':'provider_recording_identity_conflict','recording':None}
                db.commit(); return {'status':'existing','recording':_public_recording_view(existing)}
        else:
            row=db.execute(text("""INSERT INTO telephony_recordings(account_id,call_id,provider,provider_recording_id,source_url,duration_sec,status)
              VALUES(:a,:c,:p,:r,:u,:d,'available') RETURNING *"""),params).mappings().one()
        db.execute(text("UPDATE telephony_calls SET recording_status='available',recording_url=:u,updated_at=now() WHERE account_id=:a AND id=:c"),{'u':source_url,'a':account_id,'c':call_id})
        db.commit(); return {'status':'ok','recording':_public_recording_view(row)}
    finally: db.close()

def recording_list(account_id: str, limit: int=100) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal()
    try:
        return [_public_recording_view(x) for x in db.execute(text("SELECT * FROM telephony_recordings WHERE account_id=:a ORDER BY created_at DESC LIMIT :l"),{'a':account_id,'l':max(1,min(int(limit),500))}).mappings().all()]
    finally: db.close()

def callback_guardian(account_id: str|None=None) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal(); created=0; overdue=0
    try:
        where="callback_status='required' AND callback_due_at<now()"; params={}
        if account_id: where += " AND account_id=:a"; params['a']=account_id
        rows=db.execute(text(f"SELECT id,account_id,crm_contact_id,crm_deal_id,callback_due_at FROM telephony_calls WHERE {where} ORDER BY callback_due_at LIMIT 200"),params).mappings().all()
        overdue=len(rows)
        for r in rows:
            owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':r['account_id']}).scalar()
            if owner is None or r.get('crm_contact_id') is None: continue
            ref=f"boris_callback_overdue:{r['id']}"
            # Runtime singleton is the normal path, but the local internal API can
            # invoke the same guardian. Serialize each missed-call SLA task in DB
            # so concurrent workers cannot create duplicate CRM follow-ups.
            lock_key=int.from_bytes(hashlib.blake2b((str(r['account_id'])+'|callback-sla|'+str(r['id'])).encode(),digest_size=8).digest(),'big',signed=False)
            if lock_key >= (1 << 63): lock_key -= (1 << 64)
            db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock_key})
            if db.execute(text("SELECT 1 FROM boris_crm_tasks WHERE owner_user_id=:o AND description=:r LIMIT 1"),{'o':owner,'r':ref}).first(): continue
            db.execute(text("""INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source)
              VALUES(:o,:d,:c,'Срочно перезвонить: SLA пропущен',:r,:o,now(),'open','boris_telephony')"""),
              {'o':owner,'d':r.get('crm_deal_id'),'c':r['crm_contact_id'],'r':ref}); created+=1
        db.commit(); return {'status':'ok','overdue':overdue,'tasks_created':created}
    finally: db.close()


# --- Provider-neutral secure configuration / transport boundary ---
def _audit(account_id: str, action: str, result: str='ok', actor_user_id: int|None=None,
           call_id: str|None=None, provider: str|None=None, metadata: dict|None=None) -> None:
    ensure_schema(); db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_audit(account_id,action,actor_user_id,call_id,provider,result,metadata_json)
          VALUES(:a,:x,:u,:c,:p,:r,CAST(:m AS jsonb))"""),
          {'a':account_id,'x':action[:120],'u':actor_user_id,'c':call_id,'p':provider,'r':result[:80],
           'm':json.dumps(metadata or {},ensure_ascii=False)})
        db.commit()
    finally: db.close()


def provider_config_public(account_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("""SELECT account_id,provider,public_config_json,status,last_health_at,last_health_status,last_error,updated_at,
          (credentials_enc IS NOT NULL AND credentials_enc<>'') AS has_credentials,
          (webhook_secret_hash IS NOT NULL AND webhook_secret_hash<>'') AS webhook_ready
          FROM telephony_provider_configs WHERE account_id=:a"""),{'a':account_id}).mappings().first()
        config=dict(row) if row else None
        if config and str(config.get('provider') or '').strip().lower()=='telphin':
            try:
                from app.services.telphin_sip_trunk import trunk_status
                config['sip_trunk']=trunk_status(account_id)
            except Exception:
                config['sip_trunk']={'status':'unavailable','enabled':False,'ready':False}
        return {'status':'ok','config':config}
    finally: db.close()


def save_provider_config(account_id: str, provider: str, credentials: dict|None=None,
                         public_config: dict|None=None, webhook_secret: str|None=None,
                         actor_user_id: int|None=None) -> dict[str,Any]:
    ensure_schema(); provider=str(provider or '').strip().lower()
    if provider not in PROVIDERS: return {'status':'invalid_provider','allowed':sorted(PROVIDERS)}
    # public_config is returned to authenticated product UI and stored as JSONB,
    # therefore secret-shaped material must never be accepted here. Callers must
    # use encrypted `credentials` / `webhook_secret` fields instead.
    _public_secret_keys={'token','access_token','refresh_token','secret','password','authorization','api_key','api_secret','user_key','secret_key','client_secret','webhook_secret','private_key'}
    def _public_config_secret_path(value, path='public_config', depth=0):
        if depth>8: return None
        if isinstance(value,dict):
            for k,v in value.items():
                key=str(k or '').strip().lower().replace('-','_')
                if key in _public_secret_keys or key.endswith('_secret') or key.endswith('_token') or key.endswith('_private_key'):
                    return path+'.'+str(k)
                found=_public_config_secret_path(v,path+'.'+str(k),depth+1)
                if found:return found
        elif isinstance(value,(list,tuple)):
            for i,v in enumerate(value[:100]):
                found=_public_config_secret_path(v,path+f'[{i}]',depth+1)
                if found:return found
        elif isinstance(value,str):
            # public_config is plaintext/UI-visible. Secret material hidden inside a
            # benign-looking value (endpoint/note/etc.) is just as unsafe as a
            # secret-shaped key. Reject common auth headers and credential query
            # parameters rather than trying to redact and silently persist them.
            if re.search(r'(?i)authorization\s*[:=]\s*(?:bearer|basic)\s+\S+',value):
                return path
            if re.search(r'(?i)(?:[?&]|\b)(?:access_token|refresh_token|api_key|api_secret|client_secret|webhook_secret|password|token|secret)\s*=\s*[^&\s]+',value):
                return path
        return None
    secret_path=_public_config_secret_path(public_config or {})
    if secret_path:
        return {'status':'public_config_contains_secret_field','field':secret_path,'message':'Секреты нужно передавать через зашифрованные credentials/webhook_secret'}
    if (credentials is not None or webhook_secret is not None) and not os.getenv('FERNET_KEY'):
        return {'status':'encryption_unavailable','message':'На сервере не настроено шифрование секретов'}
    from app.crypto_utils import encrypt_secret, decrypt_secret
    db=SessionLocal(); provider_switched=False; credentials_updated=credentials is not None
    try:
        # Provider credentials are one encrypted document. Serialize changes so a
        # partial UI update cannot erase existing fields, and never carry secrets
        # from one operator into another operator on provider switch.
        existing=db.execute(text("""SELECT provider,credentials_enc,public_config_json,
          webhook_secret_hash,webhook_secret_enc
          FROM telephony_provider_configs WHERE account_id=:a FOR UPDATE"""),{'a':account_id}).mappings().first()
        previous_provider=str((existing or {}).get('provider') or '').strip().lower()
        same_provider=bool(existing and previous_provider==provider)
        provider_switched=bool(existing and previous_provider and previous_provider!=provider)

        merged_credentials={}
        existing_enc=(existing or {}).get('credentials_enc') if same_provider else None
        if existing_enc:
            try:
                decoded=json.loads(decrypt_secret(str(existing_enc)))
                if not isinstance(decoded,dict): raise ValueError('credential_document_not_object')
                merged_credentials=dict(decoded)
            except Exception:
                if credentials is not None:
                    db.rollback()
                    return {'status':'existing_credentials_unreadable','message':'Сохранённые ключи оператора повреждены; BORIS не будет молча перезаписывать их частичным набором'}
                merged_credentials={}

        if credentials is not None:
            for key,value in dict(credentials or {}).items():
                k=str(key or '').strip()
                if not k: continue
                # Empty values from password inputs mean "leave unchanged", not delete.
                if value is None or (isinstance(value,str) and not value.strip()):
                    continue
                merged_credentials[k]=value

        if credentials is not None:
            enc_creds=encrypt_secret(json.dumps(merged_credentials,ensure_ascii=False)) if merged_credentials else None
        elif same_provider:
            enc_creds=str(existing_enc) if existing_enc else None
        else:
            enc_creds=None

        if public_config is None and same_provider:
            final_public=dict((existing or {}).get('public_config_json') or {})
        else:
            final_public=dict(public_config or {})

        if webhook_secret:
            secret_hash=hashlib.sha256(webhook_secret.encode()).hexdigest()
            secret_enc=encrypt_secret(webhook_secret)
        elif same_provider:
            secret_hash=(existing or {}).get('webhook_secret_hash')
            secret_enc=(existing or {}).get('webhook_secret_enc')
        else:
            secret_hash=None
            secret_enc=None

        status='configured_unverified' if enc_creds else 'credentials_required'
        if existing:
            db.execute(text("""UPDATE telephony_provider_configs SET provider=:p,
              credentials_enc=:ce, public_config_json=CAST(:pc AS jsonb),
              webhook_secret_hash=:wh, webhook_secret_enc=:we,
              status=:st,last_health_at=NULL,last_health_status=NULL,last_error=NULL,
              updated_at=now() WHERE account_id=:a"""),
              {'a':account_id,'p':provider,'ce':enc_creds,'pc':json.dumps(final_public,ensure_ascii=False),
               'wh':secret_hash,'we':secret_enc,'st':status})
        else:
            db.execute(text("""INSERT INTO telephony_provider_configs(account_id,provider,credentials_enc,public_config_json,
              webhook_secret_hash,webhook_secret_enc,status) VALUES(:a,:p,:ce,CAST(:pc AS jsonb),:wh,:we,:st)"""),
              {'a':account_id,'p':provider,'ce':enc_creds,'pc':json.dumps(final_public,ensure_ascii=False),
               'wh':secret_hash,'we':secret_enc,'st':status})
        db.commit()
    finally: db.close()
    _audit(account_id,'provider.config.save','ok',actor_user_id,provider=provider,
           metadata={'credentials_updated':credentials_updated,'webhook_rotated':bool(webhook_secret),'provider_switched':provider_switched})
    return provider_config_public(account_id)


def mcn_provider_config_guardian(limit: int = 100, include_synthetic: bool = False) -> dict[str, Any]:
    """Create only missing provider-config rows for accounts that already have an enabled MCN trunk."""
    ensure_schema()
    limit=max(1,min(int(limit),500))
    db=SessionLocal()
    try:
        rows=db.execute(text("""
        SELECT DISTINCT t.account_id
        FROM telephony_trunks t
        LEFT JOIN telephony_provider_configs p ON p.account_id=t.account_id
        WHERE t.provider='mcn' AND t.enabled=true AND p.account_id IS NULL
          AND (:include_synthetic OR NOT (lower(t.account_id) ~ '^__.*qa' OR lower(t.account_id) ~ '^qa[-_]'))
        ORDER BY t.account_id
        LIMIT :lim
        """),{'include_synthetic':bool(include_synthetic),'lim':limit}).scalars().all()
    finally:
        db.close()
    healed=0
    failed=[]
    for account_id in rows:
        out=save_provider_config(
            str(account_id),"mcn",
            credentials={"transport":"mcn_trunk","account_id":str(account_id)},
            public_config={"operator":"mcn","account_id":str(account_id)},
            actor_user_id=None,
        )
        cfg=(out or {}).get('config') or {}
        if (out or {}).get('status')=='ok' and str(cfg.get('provider') or '').strip().lower()=='mcn':
            healed+=1
        else:
            failed.append({"account_id":str(account_id),"status":(out or {}).get('status') or 'unknown'})
    return {
        "status":"ok" if not failed else "degraded",
        "found":len(rows),
        "healed":healed,
        "failed":failed,
        "owner_action_required":False,
    }


def provider_credentials(account_id: str) -> tuple[str|None,dict]:
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("SELECT provider,credentials_enc FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally: db.close()
    if not row or not row.get('credentials_enc'): return (row.get('provider') if row else None), {}
    from app.crypto_utils import decrypt_secret
    try: return str(row['provider']), json.loads(decrypt_secret(str(row['credentials_enc'])))
    except Exception: return str(row['provider']), {}


def verify_generic_webhook(account_id: str, raw_body: bytes, signature: str, timestamp: str|None=None, nonce: str|None=None, max_skew_seconds: int=300, provider: str|None=None) -> bool:
    """HMAC + freshness + nonce replay protection for the generic public provider ingress."""
    import hmac,time
    ensure_schema(); db=SessionLocal()
    try:
        cfg=db.execute(text("SELECT provider,webhook_secret_enc FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally: db.close()
    configured_provider=str((cfg or {}).get('provider') or '').strip().lower()
    requested_provider=str(provider or configured_provider).strip().lower()
    enc=(cfg or {}).get('webhook_secret_enc')
    if not enc or not signature or not timestamp or not nonce: return False
    # A secret belongs to one configured provider. Do not allow a valid secret to relabel events as another adapter.
    if provider is not None and (requested_provider not in PROVIDERS or requested_provider!=configured_provider): return False
    try: ts=int(str(timestamp).strip())
    except Exception: return False
    if abs(int(time.time())-ts)>max(30,min(int(max_skew_seconds),900)): return False
    n=str(nonce).strip()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{12,160}',n): return False
    from app.crypto_utils import decrypt_secret
    secret=decrypt_secret(str(enc)).encode()
    signing=str(ts).encode()+b'.'+n.encode()+b'.'+raw_body
    expected=hmac.new(secret,signing,hashlib.sha256).hexdigest()
    supplied=signature.strip().removeprefix('sha256=').strip()
    if not hmac.compare_digest(expected,supplied): return False
    # Persist only a SHA-256 nonce fingerprint after valid HMAC. Keep replay state out of call analytics.
    nonce_hash=hashlib.sha256(n.encode()).hexdigest()
    db=SessionLocal()
    try:
        try:
            row=db.execute(text("INSERT INTO telephony_webhook_nonces(account_id,provider,nonce_hash) VALUES(:a,:p,:n) ON CONFLICT(account_id,provider,nonce_hash) DO NOTHING RETURNING nonce_hash"),{'a':account_id,'p':requested_provider or 'generic','n':nonce_hash}).scalar()
            db.execute(text("DELETE FROM telephony_webhook_nonces WHERE received_at < now()-interval '1 day'"))
            db.commit()
            return bool(row)
        except Exception:
            db.rollback(); return False
    finally: db.close()



def claim_signed_native_webhook(account_id: str, provider: str, raw_body: bytes, scope: str = '') -> str:
    """Replay guard for provider-native signed webhooks.

    Signature verification belongs to the provider adapter/API boundary. Call this
    only after a valid signature/secret. The exact signed delivery is claimed once
    in PostgreSQL before any provider recording lookup or call mutation.
    """
    ensure_schema()
    p=str(provider or '').strip().lower()
    if p not in PROVIDERS or not isinstance(raw_body,(bytes,bytearray)) or not raw_body:
        return 'error'
    fingerprint=hashlib.sha256(b'native-webhook-v1\0'+p.encode()+b'\0'+str(scope or '').encode()+b'\0'+bytes(raw_body)).hexdigest()
    db=SessionLocal()
    try:
        try:
            row=db.execute(text("INSERT INTO telephony_webhook_nonces(account_id,provider,nonce_hash) VALUES(:a,:p,:n) ON CONFLICT(account_id,provider,nonce_hash) DO NOTHING RETURNING nonce_hash"),
                           {'a':account_id,'p':'native:'+p,'n':fingerprint}).scalar()
            db.execute(text("DELETE FROM telephony_webhook_nonces WHERE received_at < now()-interval '1 day'"))
            db.commit()
            return 'claimed' if row else 'duplicate'
        except Exception:
            db.rollback()
            return 'error'
    finally:
        db.close()



def _reconcile_outbound_intent_from_provider_call(account_id: str, provider: str, call: dict | None) -> dict[str,Any]:
    """Resolve one ambiguous outbound intent only from authoritative provider call evidence.

    Never guesses across multiple intents. New intents persist normalized destination so a
    provider webhook can bind the exact economic/call intent after a timeout or process crash.
    Legacy intents without destination remain fail-closed ambiguous.
    """
    if not isinstance(call,dict) or str(call.get('direction') or '').lower()!='outbound':
        return {'status':'not_applicable'}
    provider_call_id=str(call.get('provider_call_id') or '').strip()
    call_id=str(call.get('id') or '').strip()
    to_number=normalize_phone(str(call.get('to_number') or ''))
    if not provider_call_id or not call_id or not to_number:
        return {'status':'insufficient_evidence'}
    db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT account_id,idempotency_key,status FROM telephony_outbound_intents
          WHERE account_id=:a AND provider=:p AND to_number=:to
            AND status IN ('processing','ambiguous')
            AND created_at>=now()-interval '30 minutes'
          ORDER BY created_at DESC FOR UPDATE"""),{'a':account_id,'p':provider,'to':to_number}).mappings().all()
        if len(rows)!=1:
            db.rollback()
            return {'status':'no_unique_match','matches':len(rows)}
        idem=str(rows[0]['idempotency_key'])
        db.execute(text("""UPDATE telephony_outbound_intents
          SET status='confirmed',provider_call_id=:pc,call_id=:c,last_error=NULL,updated_at=now()
          WHERE account_id=:a AND idempotency_key=:k AND status IN ('processing','ambiguous')"""),
          {'pc':provider_call_id,'c':call_id,'a':account_id,'k':idem})
        db.commit()
        _audit(account_id,'call.outbound.reconcile','confirmed',call_id=call_id,provider=provider,
               metadata={'idempotency_key':idem,'provider_call_id':provider_call_id})
        return {'status':'confirmed','idempotency_key':idem}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


def ingest_provider_event(account_id: str, provider: str, event: dict) -> dict[str,Any]:
    """Canonical normalized ingress. Provider adapters normalize external payloads into this contract."""
    provider=str(provider or '').strip().lower()
    if provider not in PROVIDERS: return {'status':'invalid_provider'}
    typ=str(event.get('type') or '').strip().lower()
    if typ not in {'call.started','call.ringing','call.answered','call.hold','call.resumed','call.transferring','call.transferred','call.ended','call.missed','call.rejected','call.busy','call.failed','call.cancelled'}:
        return {'status':'invalid_event'}
    result=apply_event(account_id,typ,event.get('call_id'),provider,event.get('provider_call_id'),event.get('event_id'),event.get('payload') or {})
    reconcile={'status':'not_applicable'}
    try:
        reconcile=_reconcile_outbound_intent_from_provider_call(account_id,provider,result.get('call'))
    except Exception as exc:
        reconcile={'status':'error','error_code':type(exc).__name__[:120]}
    _audit(account_id,'provider.webhook',result.get('status','unknown'),call_id=(result.get('call') or {}).get('id'),provider=provider,metadata={'event_type':typ,'outbound_reconcile':reconcile.get('status')})
    result['outbound_reconcile']=reconcile
    return result


def event_feed(account_id: str, after_id: int=0, limit: int=100) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal(); limit=max(1,min(int(limit),500))
    try:
        rows=db.execute(text("""SELECT id,call_id,provider,event_type,payload_json,created_at FROM telephony_events
          WHERE account_id=:a AND id>:i ORDER BY id ASC LIMIT :l"""),{'a':account_id,'i':max(0,int(after_id)),'l':limit}).mappings().all()
        return [dict(x) for x in rows]
    finally: db.close()


def audit_list(account_id: str, limit: int=100) -> list[dict[str,Any]]:
    ensure_schema(); db=SessionLocal(); limit=max(1,min(int(limit),500))
    try:
        return [dict(x) for x in db.execute(text("SELECT * FROM telephony_audit WHERE account_id=:a ORDER BY created_at DESC LIMIT :l"),{'a':account_id,'l':limit}).mappings().all()]
    finally: db.close()


def add_cost(account_id: str, call_id: str|None, category: str, amount_rub: float, provider: str|None=None,
             units: float|None=None, unit_name: str|None=None, idempotency_key: str|None=None, metadata: dict|None=None) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        params={'a':account_id,'c':call_id,'cat':category[:80],'p':provider,'amt':float(amount_rub),'u':units,'un':unit_name,'k':idempotency_key,
                'm':json.dumps(metadata or {},ensure_ascii=False)}
        if idempotency_key:
            row=db.execute(text("""INSERT INTO telephony_cost_ledger(account_id,call_id,category,provider,amount_rub,units,unit_name,idempotency_key,metadata_json)
              VALUES(:a,:c,:cat,:p,:amt,:u,:un,:k,CAST(:m AS jsonb))
              ON CONFLICT (account_id,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING *"""),params).mappings().first()
            if not row:
                row=db.execute(text("SELECT * FROM telephony_cost_ledger WHERE account_id=:a AND idempotency_key=:k"),{'a':account_id,'k':idempotency_key}).mappings().one()
                db.commit(); return {'status':'existing','item':dict(row)}
        else:
            row=db.execute(text("""INSERT INTO telephony_cost_ledger(account_id,call_id,category,provider,amount_rub,units,unit_name,idempotency_key,metadata_json)
              VALUES(:a,:c,:cat,:p,:amt,:u,:un,:k,CAST(:m AS jsonb)) RETURNING *"""),params).mappings().one()
        db.commit(); return {'status':'ok','item':dict(row)}
    finally: db.close()

def cost_breakdown(account_id: str, days: int=30) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal(); days=max(1,min(int(days),365))
    try:
        rows=db.execute(text("""SELECT category,coalesce(sum(amount_rub),0) amount_rub,coalesce(sum(units),0) units,count(*) items
          FROM telephony_cost_ledger WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval
          GROUP BY category ORDER BY amount_rub DESC"""),{'a':account_id,'d':days}).mappings().all()
        total=sum(float(x['amount_rub'] or 0) for x in rows)
        return {'status':'ok','days':days,'total_rub':round(total,4),'items':[dict(x) for x in rows]}
    finally: db.close()


def ingest_transcript_chunk(account_id: str, call_id: str, seq: int, text_value: str,
                            speaker: str|None=None, start_ms: int|None=None, end_ms: int|None=None,
                            is_final: bool=True, source: str='stream', source_event_id: str|None=None) -> dict[str,Any]:
    """Provider/STT-neutral realtime transcript ingress. Text is account-scoped and idempotent."""
    ensure_schema(); text_value=str(text_value or '').strip(); source=str(source or 'stream')[:40]
    if not text_value:return {'status':'empty'}
    if len(text_value)>8000:return {'status':'too_large'}
    db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,state FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'call_not_found'}
        if source_event_id:
            # Serialize one upstream transcript event across API/runtime workers.
            # The seq identity remains the storage upsert key, while this lock
            # makes provider retry identity atomic even when retries disagree on seq.
            intent=f'{account_id}|transcript-event|{source}|{source_event_id}'
            lock_id=int.from_bytes(hashlib.blake2b(intent.encode('utf-8','ignore'),digest_size=8).digest(),'big',signed=False)
            if lock_id >= (1 << 63): lock_id -= (1 << 64)
            db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock_id})
            dup=db.execute(text("SELECT id FROM telephony_transcript_chunks WHERE account_id=:a AND source=:s AND source_event_id=:e"),{'a':account_id,'s':source,'e':source_event_id}).scalar()
            if dup:return {'status':'duplicate','chunk_id':int(dup)}
        row=db.execute(text('''INSERT INTO telephony_transcript_chunks(account_id,call_id,seq,speaker,text_content,start_ms,end_ms,is_final,source,source_event_id)
          VALUES(:a,:c,:q,:sp,:t,:sm,:em,:f,:s,:e)
          ON CONFLICT(account_id,call_id,source,seq) DO UPDATE SET speaker=excluded.speaker,text_content=excluded.text_content,
            start_ms=excluded.start_ms,end_ms=excluded.end_ms,is_final=excluded.is_final,source_event_id=COALESCE(excluded.source_event_id,telephony_transcript_chunks.source_event_id)
          RETURNING id,seq,speaker,text_content,start_ms,end_ms,is_final,source,created_at'''),
          {'a':account_id,'c':call_id,'q':max(0,int(seq)),'sp':(speaker or '')[:40] or None,'t':text_value,'sm':start_ms,'em':end_ms,'f':bool(is_final),'s':source,'e':source_event_id}).mappings().one()
        db.execute(text("UPDATE telephony_calls SET transcription_status='streaming',updated_at=now() WHERE account_id=:a AND id=:c AND transcription_status<>'done'"),{'a':account_id,'c':call_id})
        db.execute(text("""INSERT INTO telephony_events(account_id,call_id,provider,event_type,payload_json)
          VALUES(:a,:c,NULL,'transcript.chunk',CAST(:p AS jsonb))"""),{'a':account_id,'c':call_id,'p':json.dumps({'chunk_id':int(row['id']),'seq':int(row['seq']),'speaker':row.get('speaker'),'is_final':bool(row.get('is_final'))},ensure_ascii=False)})
        db.commit(); return {'status':'ok','chunk':dict(row)}
    finally: db.close()


def add_realtime_ai_suggestion(account_id: str, call_id: str, suggestion_text: str, kind: str='next_best_action',
                               trigger_chunk_id: int|None=None, evidence: list|None=None,
                               model: str|None=None, idempotency_key: str|None=None) -> dict[str,Any]:
    """Storage boundary for authoritative AI workers. Does not fabricate suggestions by itself."""
    ensure_schema(); suggestion_text=str(suggestion_text or '').strip()
    if not suggestion_text:return {'status':'empty'}
    if len(suggestion_text)>4000:return {'status':'too_large'}
    db=SessionLocal()
    try:
        if not db.execute(text("SELECT 1 FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).first():return {'status':'call_not_found'}
        params={'a':account_id,'c':call_id,'tc':trigger_chunk_id,'k':str(kind or 'next_best_action')[:80],'t':suggestion_text,
                'e':json.dumps(evidence or [],ensure_ascii=False),'m':(model or '')[:100] or None,'i':idempotency_key}
        if idempotency_key:
            row=db.execute(text("""INSERT INTO telephony_ai_suggestions(account_id,call_id,trigger_chunk_id,kind,suggestion_text,evidence_json,status,model,idempotency_key)
              VALUES(:a,:c,:tc,:k,:t,CAST(:e AS jsonb),'active',:m,:i)
              ON CONFLICT (account_id,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING *"""),params).mappings().first()
            if not row:
                existing=db.execute(text("SELECT * FROM telephony_ai_suggestions WHERE account_id=:a AND idempotency_key=:k"),{'a':account_id,'k':idempotency_key}).mappings().one()
                db.commit(); return {'status':'existing','suggestion':dict(existing)}
        else:
            row=db.execute(text("""INSERT INTO telephony_ai_suggestions(account_id,call_id,trigger_chunk_id,kind,suggestion_text,evidence_json,status,model,idempotency_key)
              VALUES(:a,:c,:tc,:k,:t,CAST(:e AS jsonb),'active',:m,:i) RETURNING *"""),params).mappings().one()
        db.execute(text("""INSERT INTO telephony_events(account_id,call_id,event_type,payload_json)
          VALUES(:a,:c,'ai.suggestion',CAST(:p AS jsonb))"""),{'a':account_id,'c':call_id,'p':json.dumps({'suggestion_id':int(row['id']),'kind':row['kind']},ensure_ascii=False)})
        db.commit(); return {'status':'ok','suggestion':dict(row)}
    finally: db.close()

def recording_policy_settings(account_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("""SELECT account_id,policy,acknowledged_at,acknowledged_by_user_id,updated_at
          FROM telephony_recording_settings WHERE account_id=:a"""),{'a':account_id}).mappings().first()
    finally: db.close()
    settings=dict(row) if row else {'account_id':account_id,'policy':'manual','acknowledged_at':None,'acknowledged_by_user_id':None,'updated_at':None}
    settings['effective_notice_status']={'manual':'unknown','disabled':'disabled','not_required':'not_required'}.get(str(settings.get('policy') or ''),'unknown')
    settings['truth']='manual is fail-closed; not_required must be explicitly acknowledged by the account owner'
    return {'status':'ok','settings':settings}


def save_recording_policy_settings(account_id: str, payload: dict, actor_user_id: int|None=None) -> dict[str,Any]:
    policy=str((payload or {}).get('policy') or '').strip().lower()
    if policy not in {'manual','disabled','not_required'}:
        return {'status':'invalid_recording_policy'}
    acknowledged=bool((payload or {}).get('acknowledged'))
    if policy=='not_required' and not acknowledged:
        return {'status':'acknowledgement_required',
                'message':'Для автоматической записи без пометки об уведомлении нужно явное подтверждение владельца аккаунта'}
    if policy=='not_required' and actor_user_id is None:
        return {'status':'owner_identity_required',
                'message':'Этот режим можно включить только из подтверждённой сессии владельца аккаунта'}
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("""INSERT INTO telephony_recording_settings(
          account_id,policy,acknowledged_at,acknowledged_by_user_id,updated_at
        ) VALUES(:a,:p,CASE WHEN :ack THEN now() ELSE NULL END,
          CASE WHEN :ack THEN CAST(:u AS bigint) ELSE NULL::bigint END,now())
        ON CONFLICT(account_id) DO UPDATE SET
          policy=excluded.policy,
          acknowledged_at=CASE WHEN :ack THEN now() ELSE NULL END,
          acknowledged_by_user_id=CASE WHEN :ack THEN CAST(:u AS bigint) ELSE NULL::bigint END,
          updated_at=now()
        RETURNING account_id,policy,acknowledged_at,acknowledged_by_user_id,updated_at"""),
        {'a':account_id,'p':policy,'ack':acknowledged,'u':actor_user_id}).mappings().one()
        db.commit()
    finally: db.close()
    _audit(account_id,'recording.policy.save','ok',actor_user_id,
           metadata={'policy':policy,'acknowledged':acknowledged,'applies_to':'new_calls'})
    out=dict(row)
    out['effective_notice_status']={'manual':'unknown','disabled':'disabled','not_required':'not_required'}[policy]
    return {'status':'ok','settings':out,'applies_to':'new_calls'}


def human_call_policy(account_id: str, call_id: str) -> dict[str,Any]:
    """Real-person calls: human remains the speaker/decision maker; AI is advisory only."""
    ensure_schema(); db=SessionLocal()
    try:
        c=db.execute(text("SELECT agent_mode,ai_assist_enabled,recording_notice_status,state FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not c:return {'status':'not_found'}
        return {'status':'ok','agent_mode':c['agent_mode'],'human_in_control':c['agent_mode']=='human','ai_assist_enabled':bool(c['ai_assist_enabled']),'ai_can_speak':False,'ai_can_commit_terms':False,'recording_notice_status':c['recording_notice_status'],'state':c['state']}
    finally:db.close()


def set_recording_notice_status(account_id: str, call_id: str, status: str) -> dict[str,Any]:
    allowed={'unknown','not_required','announced','consented','declined','disabled'}
    if status not in allowed:return {'status':'invalid_recording_notice_status'}
    ensure_schema(); db=SessionLocal()
    try:
        r=db.execute(text("""UPDATE telephony_calls SET recording_notice_status=:s,updated_at=now()
          WHERE account_id=:a AND id=:c
          RETURNING id,recording_notice_status,provider,provider_call_id,state"""),
          {'s':status,'a':account_id,'c':call_id}).mappings().first();db.commit()
    finally:db.close()
    if not r:return {'status':'not_found'}
    recording=None
    if status in {'not_required','announced','consented'} and str(r.get('provider') or '').strip().lower() in {'mcn','telphin'} and str(r.get('provider_call_id') or '').strip() and str(r.get('state') or '') in {'active','on_hold'}:
        try:
            from app.services.asterisk_gateway import ensure_call_recording
            recording=ensure_call_recording(account_id,call_id,str(r.get('provider_call_id')))
        except Exception as exc:
            recording={'status':'error','error_code':type(exc).__name__[:120]}
    return {'status':'ok','call':dict(r),'recording':recording}


def realtime_call_context(account_id: str, call_id: str, chunk_limit: int=80) -> dict[str,Any]:
    ensure_schema(); chunk_limit=max(1,min(int(chunk_limit),300)); db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,state,device_id,user_id,crm_contact_id,crm_deal_id,started_at,answered_at FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'not_found'}
        chunks=[dict(x) for x in db.execute(text("""SELECT id,seq,speaker,text_content,start_ms,end_ms,is_final,source,created_at FROM telephony_transcript_chunks
          WHERE account_id=:a AND call_id=:c ORDER BY seq DESC,id DESC LIMIT :l"""),{'a':account_id,'c':call_id,'l':chunk_limit}).mappings().all()][::-1]
        suggestions=[dict(x) for x in db.execute(text("""SELECT id,trigger_chunk_id,kind,suggestion_text,evidence_json,status,model,created_at FROM telephony_ai_suggestions
          WHERE account_id=:a AND call_id=:c AND status='active' ORDER BY created_at DESC,id DESC LIMIT 20"""),{'a':account_id,'c':call_id}).mappings().all()]
        return {'status':'ok','call':_public_call_view(call),'chunks':chunks,'suggestions':suggestions}
    finally: db.close()


def dismiss_realtime_suggestion(account_id: str, call_id: str, suggestion_id: int) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("UPDATE telephony_ai_suggestions SET status='dismissed' WHERE account_id=:a AND call_id=:c AND id=:i AND status='active' RETURNING id"),{'a':account_id,'c':call_id,'i':int(suggestion_id)}).first();db.commit()
        return {'status':'ok' if row else 'not_found'}
    finally:db.close()


TELEPHONY_RECORDING_ROOT='/root/BORIS/backend/telephony_recordings'

def _safe_recording_path(path: str) -> str|None:
    try:
        from pathlib import Path
        root=Path(TELEPHONY_RECORDING_ROOT).resolve(); target=Path(path).resolve()
        if target==root or root not in target.parents: return None
        if not target.is_file(): return None
        return str(target)
    except Exception: return None


def attach_recording_file(account_id: str, call_id: str, local_path: str, duration_sec: int|None=None,
                          provider: str|None=None, provider_recording_id: str|None=None) -> dict[str,Any]:
    safe=_safe_recording_path(local_path)
    if not safe: return {'status':'invalid_recording_path'}
    import hashlib as _hh
    h=_hh.sha256()
    with open(safe,'rb') as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b''): h.update(chunk)
    result=recording_upsert(account_id,call_id,provider,provider_recording_id,None,duration_sec)
    if result.get('status') not in {'ok','existing'}: return result
    rid=(result.get('recording') or {}).get('id'); db=SessionLocal()
    try:
        # Re-attaching the exact same bytes must not erase an already paid STT/analysis.
        # If bytes changed, all derived AI evidence is invalidated deterministically.
        row=db.execute(text("""UPDATE telephony_recordings SET local_path=:p,status='available',
          transcript=CASE WHEN checksum=:h THEN transcript ELSE NULL END,
          transcript_status=CASE WHEN checksum=:h THEN transcript_status ELSE 'pending' END,
          transcript_attempts=CASE WHEN checksum=:h THEN transcript_attempts ELSE 0 END,
          next_transcript_at=CASE WHEN checksum=:h THEN next_transcript_at ELSE NULL END,
          analysis_json=CASE WHEN checksum=:h THEN analysis_json ELSE '{}'::jsonb END,
          analysis_status=CASE WHEN checksum=:h THEN analysis_status ELSE 'pending' END,
          checksum=:h,last_error=CASE WHEN checksum=:h THEN last_error ELSE NULL END,updated_at=now()
          WHERE id=:id AND account_id=:a RETURNING *"""),
          {'p':safe,'h':h.hexdigest(),'id':rid,'a':account_id}).mappings().first(); db.commit()
        return {'status':'ok','recording':_public_recording_view(row)}
    finally: db.close()


def _recording_url_is_public_https(url: str) -> tuple[bool,str]:
    """SSRF guard for provider-issued recording links, including redirects."""
    import ipaddress, socket
    try:
        parsed=urlparse(str(url or '').strip())
        if parsed.scheme.lower()!='https' or not parsed.hostname or parsed.username or parsed.password:
            return False,'https_required'
        host=parsed.hostname.strip().lower().rstrip('.')
        if host in {'localhost','localhost.localdomain'}:
            return False,'local_host_blocked'
        infos=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not infos:
            return False,'dns_empty'
        for info in infos:
            ip=ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False,'non_public_ip'
        return True,host
    except Exception as exc:
        return False,('url_validation_failed:'+type(exc).__name__)[:180]


def _recording_response_peer_is_public(resp) -> tuple[bool,str]:
    """Verify the actual connected peer, closing DNS-rebinding gaps after hostname resolution."""
    import ipaddress
    candidates=[]
    try: candidates.append(getattr(getattr(getattr(resp,'fp',None),'raw',None),'_sock',None))
    except Exception: pass
    try: candidates.append(getattr(getattr(resp,'_fp',None),'fp',None))
    except Exception: pass
    for sock in candidates:
        if sock is None or not hasattr(sock,'getpeername'): continue
        try:
            peer=sock.getpeername(); host=peer[0] if isinstance(peer,(tuple,list)) and peer else peer
            ip=ipaddress.ip_address(str(host))
            return (True,str(ip)) if ip.is_global else (False,'non_public_peer_ip')
        except Exception:
            continue
    return False,'peer_ip_unavailable'


def _download_recording_url(url: str, target_path: str, max_bytes: int=60*1024*1024) -> dict[str,Any]:
    """Download a provider recording with bounded redirects/size and no private-network access."""
    import urllib.request, urllib.error
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener=urllib.request.build_opener(_NoRedirect)
    current=str(url or '').strip()
    for hop in range(4):
        ok,detail=_recording_url_is_public_https(current)
        if not ok:
            return {'status':'blocked_url','message':detail}
        req=urllib.request.Request(current,method='GET',headers={'User-Agent':'BORIS-Phone/1.0','Accept':'audio/*,application/octet-stream;q=0.9,*/*;q=0.1'})
        try:
            resp=opener.open(req,timeout=20)
        except urllib.error.HTTPError as exc:
            if int(exc.code or 0) in {301,302,303,307,308}:
                location=str(exc.headers.get('Location') or '').strip()
                if not location:return {'status':'download_failed','message':'redirect_without_location'}
                current=urllib.parse.urljoin(current,location); continue
            return {'status':'download_failed','message':f'HTTP {int(exc.code or 0)}'}
        except Exception as exc:
            return {'status':'download_failed','message':('transport_error:'+type(exc).__name__)[:180]}
        try:
            peer_ok,peer_detail=_recording_response_peer_is_public(resp)
            if not peer_ok:
                return {'status':'blocked_url','message':peer_detail}
            code=int(getattr(resp,'status',200) or 200)
            if code in {301,302,303,307,308}:
                location=str(resp.headers.get('Location') or '').strip()
                if not location:return {'status':'download_failed','message':'redirect_without_location'}
                current=urllib.parse.urljoin(current,location); continue
            if code<200 or code>=300:return {'status':'download_failed','message':f'HTTP {code}'}
            length=int(resp.headers.get('Content-Length') or 0)
            if length>max_bytes:return {'status':'too_large','message':f'content_length={length}'}
            ctype=str(resp.headers.get('Content-Type') or '').lower().split(';',1)[0].strip()
            allowed=not ctype or ctype.startswith('audio/') or ctype in {'application/octet-stream','binary/octet-stream','application/mp3','application/mpeg'}
            if not allowed:return {'status':'invalid_content_type','message':ctype[:120]}
            os.makedirs(os.path.dirname(target_path),exist_ok=True)
            tmp=target_path+'.part-'+uuid.uuid4().hex
            size=0
            try:
                with open(tmp,'xb') as fh:
                    while True:
                        chunk=resp.read(min(1024*1024,max_bytes+1-size))
                        if not chunk:break
                        size+=len(chunk)
                        if size>max_bytes:raise ValueError('recording_too_large')
                        fh.write(chunk)
                    fh.flush(); os.fsync(fh.fileno())
                if size<=0:raise ValueError('empty_recording')
                os.replace(tmp,target_path)
            except Exception:
                try:
                    if os.path.exists(tmp):os.unlink(tmp)
                except Exception:pass
                raise
            return {'status':'ok','path':target_path,'bytes':size,'content_type':ctype,'source_host':detail}
        except Exception as exc:
            return {'status':'download_failed','message':('transport_error:'+type(exc).__name__)[:180]}
        finally:
            try:resp.close()
            except Exception:pass
    return {'status':'download_failed','message':'too_many_redirects'}


def _refresh_recording_source(recording: dict[str,Any]) -> dict[str,Any]:
    """Refresh an expired temporary provider URL through the already configured adapter."""
    provider=str(recording.get('provider') or '').strip().lower()
    if provider not in {'novofon','mango'}:
        return {'status':'refresh_unsupported'}
    account_id=str(recording.get('account_id') or '')
    configured,credentials=provider_credentials(account_id)
    if str(configured or '').strip().lower()!=provider or not credentials:
        return {'status':'credentials_unavailable'}
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter(provider)
    if not adapter or not hasattr(adapter,'recording_link'):
        return {'status':'refresh_unsupported'}
    provider_recording_id=str(recording.get('provider_recording_id') or '').strip()
    if not provider_recording_id:
        return {'status':'recording_id_missing'}
    try:
        if provider=='mango':
            link=adapter.recording_link(provider_recording_id=provider_recording_id,credentials=credentials)
        else:
            db=SessionLocal()
            try:
                provider_call_id=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':recording.get('call_id')}).scalar()
            finally:db.close()
            if not provider_call_id:return {'status':'provider_call_id_missing'}
            link=adapter.recording_link(provider_call_id=str(provider_call_id),provider_recording_id=provider_recording_id,credentials=credentials)
    except Exception as exc:
        return {'status':'refresh_failed','message':('provider_transport_error:'+type(exc).__name__)[:180]}
    url=str((link.payload or {}).get('url') or '').strip() if getattr(link,'ok',False) else ''
    if not url:
        return {'status':_sanitize_provider_status(getattr(link,'status','refresh_failed'),credentials,'refresh_failed'),'message':'provider did not return recording URL'}
    ok,reason=_recording_url_is_public_https(url)
    if not ok:return {'status':'blocked_url','message':reason}
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_recordings SET source_url=:u,status='download_retry',download_attempts=0,
          next_download_at=now(),last_error=NULL,updated_at=now() WHERE id=:id"""),{'u':url,'id':recording.get('id')});db.commit()
    finally:db.close()
    return {'status':'ok','url_refreshed':True}


def acquire_pending_recordings(limit: int=2, include_synthetic: bool=False) -> dict[str,Any]:
    """Materialize provider audio with a recoverable DB execution lease."""
    ensure_schema(); cap=max(1,min(int(limit),10)); db=SessionLocal()
    try:
        # A process can die after claiming `downloading` and before updating the
        # result. Recover stale leases instead of stranding recordings forever.
        exhausted=db.execute(text("""UPDATE telephony_recordings
          SET status='download_failed',next_download_at=NULL,
              last_error=concat_ws('; ',NULLIF(last_error,''),'stale download lease exhausted retry limit'),updated_at=now()
          WHERE status='downloading' AND download_attempts>=5 AND updated_at<now()-interval '10 minutes'
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        recovered=db.execute(text("""UPDATE telephony_recordings
          SET status='download_retry',next_download_at=now(),
              last_error=concat_ws('; ',NULLIF(last_error,''),'recovered stale download lease'),updated_at=now()
          WHERE status='downloading' AND download_attempts<5 AND updated_at<now()-interval '10 minutes'
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        db.commit()
        rows=db.execute(text("""SELECT * FROM telephony_recordings
          WHERE source_url IS NOT NULL AND trim(source_url)<>'' AND local_path IS NULL
            AND status IN ('available','download_retry') AND download_attempts<5
            AND (next_download_at IS NULL OR next_download_at<=now())
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
          ORDER BY created_at,id LIMIT :l FOR UPDATE SKIP LOCKED"""),{'l':cap,'include_synthetic':bool(include_synthetic)}).mappings().all()
        ids=[int(r['id']) for r in rows]
        if ids:
            db.execute(text("UPDATE telephony_recordings SET status='downloading',download_attempts=download_attempts+1,updated_at=now() WHERE id=ANY(:ids)"),{'ids':ids});db.commit()
    finally:db.close()
    done=0;failed=0
    for raw in rows:
        r=dict(raw); rid=int(r['id']); provider=str(r.get('provider') or 'provider').lower()
        ext='.mp3' if provider in {'mango','novofon','uis','zadarma'} else '.audio'
        safe_account=re.sub(r'[^a-zA-Z0-9_.-]+','_',str(r['account_id']))[:80]
        target=os.path.join(TELEPHONY_RECORDING_ROOT,safe_account,f'{rid}{ext}')
        result=_download_recording_url(str(r.get('source_url') or ''),target)
        if result.get('status')=='ok':
            attached=attach_recording_file(r['account_id'],r['call_id'],target,r.get('duration_sec'),r.get('provider'),r.get('provider_recording_id'))
            if attached.get('status')=='ok':
                db=SessionLocal()
                try:
                    db.execute(text("UPDATE telephony_recordings SET next_download_at=NULL,last_error=NULL,status='available',updated_at=now() WHERE id=:id"),{'id':rid});db.commit()
                finally:db.close()
                done+=1;continue
            result={'status':'attach_failed','message':str(attached.get('status') or 'unknown')}
        attempts=int(r.get('download_attempts') or 0)+1
        terminal=attempts>=5 or result.get('status') in {'blocked_url','too_large','invalid_content_type'}
        wait_sec=min(3600,60*(2**max(0,attempts-1)))
        db=SessionLocal()
        try:
            db.execute(text("""UPDATE telephony_recordings SET status=:s,last_error=:e,
              next_download_at=CASE WHEN :terminal THEN NULL ELSE now()+make_interval(secs=>:w) END,updated_at=now()
              WHERE id=:id"""),{'s':'download_failed' if terminal else 'download_retry','e':str(result.get('message') or result.get('status') or 'download_failed')[:500],'terminal':terminal,'w':wait_sec,'id':rid});db.commit()
        finally:db.close()
        _audit(r['account_id'],'recording.download.failed','error',call_id=r['call_id'],provider=r.get('provider'),metadata={'recording_id':rid,'status':result.get('status'),'terminal':terminal})
        failed+=1
    return {'status':'ok','picked':len(rows),'downloaded':done,'failed':failed,'recovered_downloading':int(recovered),'exhausted_downloading':int(exhausted)}


def _sync_ai_result_to_crm(account_id: str, call_id: str, transcript: str, analysis: dict) -> None:
    db=SessionLocal()
    try:
        call=db.execute(text("SELECT crm_contact_id,crm_deal_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call or not call.get('crm_contact_id'): return
        owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':account_id}).scalar()
        if owner is None:return
        ref=f'boris_call:{call_id}'
        meta={'analysis_available':True,'transcript':transcript[:12000],'analysis':analysis}
        summary=str(analysis.get('recommendation') or '').strip()
        db.execute(text("""UPDATE boris_crm_activities SET metadata_json=COALESCE(metadata_json,'{}'::jsonb)||CAST(:m AS jsonb),
          body=CASE WHEN :s='' OR body LIKE '%Итог BORIS:%' THEN body ELSE body||E'\nИтог BORIS: '||:s END
          WHERE owner_user_id=:o AND source_ref=:r"""),{'m':json.dumps(meta,ensure_ascii=False),'s':summary[:1000],'o':int(owner),'r':ref})
        name=str(analysis.get('client_name') or '').strip()
        if name and name.casefold() not in {'клиент','неизвестно','нет'}:
            db.execute(text("""UPDATE boris_crm_contacts SET display_name=CASE WHEN display_name='Клиент по телефону' OR display_name IS NULL OR trim(display_name)='' THEN :n ELSE display_name END,updated_at=now()
              WHERE id=:c AND owner_user_id=:o"""),{'n':name[:200],'c':call['crm_contact_id'],'o':int(owner)})
        db.commit()
    finally: db.close()


def process_pending_recordings(limit: int=1, include_synthetic: bool=False) -> dict[str,Any]:
    """Canonical STT/ROP pipeline with bounded retry and crash-safe processing leases."""
    ensure_schema(); db=SessionLocal()
    try:
        exhausted=db.execute(text("""UPDATE telephony_recordings
          SET transcript_status=CASE WHEN COALESCE(trim(transcript),'')<>'' THEN 'done' ELSE 'failed' END,
              analysis_status='failed',next_transcript_at=NULL,
              last_error=concat_ws('; ',NULLIF(last_error,''),'stale transcript lease exhausted retry limit'),updated_at=now()
          WHERE transcript_status='processing' AND transcript_attempts>=5 AND updated_at<now()-interval '15 minutes'
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        recovered=db.execute(text("""UPDATE telephony_recordings
          SET transcript_status='retry',analysis_status='pending',next_transcript_at=now(),
              last_error=concat_ws('; ',NULLIF(last_error,''),'recovered stale transcript lease'),updated_at=now()
          WHERE transcript_status='processing' AND transcript_attempts<5 AND updated_at<now()-interval '15 minutes'
            AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""),{'include_synthetic':bool(include_synthetic)}).rowcount or 0
        db.commit()
        rows=db.execute(text("""SELECT * FROM telephony_recordings WHERE status='available' AND local_path IS NOT NULL
          AND transcript_status IN ('pending','retry','blocked_entitlement')
          AND (next_transcript_at IS NULL OR next_transcript_at<=now())
          AND (transcript_status='blocked_entitlement' OR transcript_attempts<5)
          AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
          ORDER BY created_at,id LIMIT :l FOR UPDATE SKIP LOCKED"""),{'l':max(1,min(int(limit),5)),'include_synthetic':bool(include_synthetic)}).mappings().all()
        ids=[int(x['id']) for x in rows]
        if ids:
            db.execute(text("""UPDATE telephony_recordings SET transcript_status='processing',
              transcript_attempts=transcript_attempts + CASE WHEN transcript_status='blocked_entitlement' THEN 0 ELSE 1 END,
              next_transcript_at=NULL,updated_at=now() WHERE id=ANY(:ids)"""),{'ids':ids}); db.commit()
    finally: db.close()
    done=0; failed=0; blocked=0; low_confidence=0
    from app.api.calltracking import LocalSTTLowConfidence
    for raw in rows:
        r=dict(raw); rid=int(r['id']); safe=_safe_recording_path(str(r.get('local_path') or ''))
        if not safe:
            db=SessionLocal(); db.execute(text("UPDATE telephony_recordings SET transcript_status='failed',analysis_status='failed',updated_at=now() WHERE id=:id"),{'id':rid}); db.commit(); db.close(); failed+=1; continue
        from app.api.calltracking import get_rop_minutes
        rop_balance=get_rop_minutes(r['account_id'])
        if not rop_balance.get('active') or float(rop_balance.get('left') or 0)<=0:
            db=SessionLocal()
            try:
                # Entitlement checks are not paid STT attempts. Undo the claim
                # increment so a temporarily inactive package never consumes retry budget.
                # Only undo the attempt consumed by this claim. Rows already in
                # blocked_entitlement are claimed with +0 above, so decrementing them
                # again would erase historical paid retry attempts and could reopen
                # budget beyond the five-attempt hard limit after entitlement returns.
                undo_attempt = 0 if str(r.get('transcript_status') or '') == 'blocked_entitlement' else 1
                db.execute(text("UPDATE telephony_recordings SET transcript_status='blocked_entitlement',analysis_status='pending',transcript_attempts=GREATEST(transcript_attempts-:undo,0),next_transcript_at=now()+interval '5 minutes',updated_at=now() WHERE id=:id"),{'id':rid,'undo':undo_attempt}); db.commit()
            finally: db.close()
            _audit(r['account_id'],'recording.ai.blocked','ok',call_id=r['call_id'],provider=r.get('provider'),metadata={'recording_id':rid,'reason':'rop_entitlement_inactive','expired':bool(rop_balance.get('expired'))})
            blocked+=1; continue
        try:
            from app.api.calltracking import _transcribe,_analyze_call,_asr_hint,_asr_fix,_LOCAL_STT_MODEL_NAME
            transcript=str(r.get('transcript') or '').strip()
            transcribed_now=False
            if not transcript:
                with open(safe,'rb') as fh: audio=fh.read(60*1024*1024+1)
                if len(audio)>60*1024*1024: raise RuntimeError('recording_too_large')
                transcript=_transcribe(audio,Path(safe).name if 'Path' in globals() else safe.rsplit('/',1)[-1],_asr_hint(r['account_id']),r['account_id'],provenance={'recording_id':rid,'call_id':str(r['call_id']),'source':'boris_phone_recording_stt'})
                transcript=_asr_fix(r['account_id'],transcript)
                if not str(transcript or '').strip(): raise RuntimeError('empty_transcript')
                transcribed_now=True
                # CALL_STT_LOCAL_V1: persist the free local transcript before the
                # separate ROP analysis stage. No OpenAI/paid STT cost is booked.
                sdb=SessionLocal()
                try:
                    sdb.execute(text("UPDATE telephony_recordings SET transcript=:t,last_error=NULL,updated_at=now() WHERE id=:id"),{'t':transcript,'id':rid})
                    sdb.commit()
                finally:sdb.close()
                _audit(
                    r['account_id'],'recording.stt.local.done','ok',
                    call_id=r['call_id'],provider=r.get('provider'),
                    metadata={'recording_id':rid,'stt_provider':'local_faster_whisper','model':_LOCAL_STT_MODEL_NAME,'paid_stt':False}
                )
            analysis=_analyze_call(transcript,r['account_id'],provenance={'recording_id':rid,'call_id':str(r['call_id']),'source':'boris_phone_recording_analysis'})
            if analysis.get('error'): raise RuntimeError(str(analysis.get('error'))[:300])
            db=SessionLocal()
            try:
                db.execute(text("""UPDATE telephony_recordings SET transcript=:t,transcript_status='done',next_transcript_at=NULL,last_error=NULL,analysis_json=CAST(:j AS jsonb),analysis_status='done',updated_at=now() WHERE id=:id"""),
                           {'t':transcript,'j':json.dumps(analysis,ensure_ascii=False),'id':rid})
                db.execute(text("""UPDATE telephony_calls SET transcription_status='done',ai_analysis_status='done',summary=:s,next_action=:n,updated_at=now() WHERE account_id=:a AND id=:c"""),
                           {'s':str(analysis.get('recommendation') or '')[:2000],'n':str((analysis.get('agreement') or {}).get('text') or '')[:1000], 'a':r['account_id'],'c':r['call_id']})
                db.commit()
            finally: db.close()
            _sync_ai_result_to_crm(r['account_id'],r['call_id'],transcript,analysis)
            _audit(r['account_id'],'recording.ai.done','ok',call_id=r['call_id'],provider=r.get('provider'),metadata={'recording_id':rid})
            done+=1
        except LocalSTTLowConfidence as exc:
            db=SessionLocal()
            try:
                db.execute(text("""UPDATE telephony_recordings
                  SET transcript=:t,transcript_status='low_confidence',
                      analysis_status='blocked_low_confidence',next_transcript_at=NULL,
                      last_error=:e,updated_at=now()
                  WHERE id=:id"""),
                  {'t':str(exc.text_value or '')[:12000],
                   'e':str(exc)[:180],'id':rid})
                db.commit()
            finally: db.close()
            _audit(
                r['account_id'],'recording.stt.local.low_confidence','warning',
                call_id=r['call_id'],provider=r.get('provider'),
                metadata={'recording_id':rid,'confidence':round(exc.confidence,4),
                          'no_speech_prob':round(exc.no_speech_prob,4),
                          'avg_logprob':round(exc.avg_logprob,4),'paid_stt':False}
            )
            low_confidence+=1
            continue
        except Exception as exc:
            # The claim already incremented transcript_attempts. Retry with
            # bounded exponential delay and stop permanently after five processing tries.
            prior_attempts=int(r.get('transcript_attempts') or 0)
            attempt=prior_attempts+1
            terminal=attempt>=5
            wait_sec=min(1800,30*(2**max(0,attempt-1)))
            db=SessionLocal()
            try:
                db.execute(text("""UPDATE telephony_recordings SET transcript_status=CASE WHEN :terminal AND COALESCE(trim(transcript),'')<>'' THEN 'done' ELSE :s END,analysis_status=:as_,last_error=:e,
                  next_transcript_at=CASE WHEN :terminal THEN NULL ELSE now()+make_interval(secs=>:w) END,updated_at=now() WHERE id=:id"""),
                  {'s':'failed' if terminal else 'retry','as_':'failed' if terminal else 'pending','e':('processing_error:'+type(exc).__name__)[:180],
                   'terminal':terminal,'w':wait_sec,'id':rid}); db.commit()
            finally:db.close()
            _audit(r['account_id'],'recording.ai.failed','error',call_id=r['call_id'],provider=r.get('provider'),metadata={'recording_id':rid,'error_type':type(exc).__name__[:120]})
            failed+=1
    return {'status':'ok','picked':len(rows),'done':done,'failed':failed,'low_confidence':low_confidence,'blocked_entitlement':blocked,'recovered_processing':int(recovered),'exhausted_processing':int(exhausted)}



def telephony_operational_retention_guard(push_days:int=30, command_days:int=30, nonce_hours:int=24, limit:int=5000) -> dict[str,Any]:
    """Bound ephemeral Phone runtime tables without deleting calls, CRM evidence, transcripts, costs, or audit history."""
    ensure_schema();lim=max(100,min(int(limit),20000));pd=max(7,min(int(push_days),180));cd=max(7,min(int(command_days),180));nh=max(1,min(int(nonce_hours),168));db=SessionLocal()
    try:
        push=db.execute(text("""DELETE FROM telephony_push_outbox WHERE id IN (SELECT id FROM telephony_push_outbox WHERE status IN ('sent','cancelled','failed') AND created_at < now()-(:d||' days')::interval ORDER BY id LIMIT :l) RETURNING id"""),{'d':pd,'l':lim}).rowcount
        commands=db.execute(text("""DELETE FROM telephony_commands WHERE id IN (SELECT id FROM telephony_commands WHERE status IN ('done','failed','cancelled') AND created_at < now()-(:d||' days')::interval ORDER BY id LIMIT :l) RETURNING id"""),{'d':cd,'l':lim}).rowcount
        nonces=db.execute(text("""DELETE FROM telephony_webhook_nonces WHERE ctid IN (SELECT ctid FROM telephony_webhook_nonces WHERE received_at < now()-(:h||' hours')::interval LIMIT :l)"""),{'h':nh,'l':lim}).rowcount
        media_sessions=db.execute(text("""DELETE FROM telephony_media_sessions WHERE id IN (
          SELECT id FROM telephony_media_sessions WHERE status IN ('closed','expired') AND updated_at<now()-interval '7 days'
          ORDER BY id LIMIT :l) RETURNING id"""),{'l':lim}).rowcount
        # TELEPHONY_WEB_DEVICE_DEDUP_V1:
        # Old web clients/reconnect flows could generate a fresh device_id on each
        # visit. Remove only provably empty offline duplicates for the same BORIS
        # user/account and keep the newest row. Native devices and any web device
        # with push, extension or call/runtime history are never candidates.
        duplicate_web_devices=db.execute(text("""DELETE FROM telephony_devices d WHERE d.id IN (
          SELECT id FROM (
            SELECT x.id,
                   row_number() OVER (
                     PARTITION BY x.account_id,x.user_id,lower(COALESCE(x.name,'')),
                                  lower(COALESCE(x.platform,'')),COALESCE(x.app_version,'')
                     ORDER BY x.updated_at DESC,x.last_seen_at DESC NULLS LAST,x.created_at DESC,x.id DESC
                   ) AS rn
            FROM telephony_devices x
            WHERE lower(COALESCE(x.platform,''))='web'
              AND x.user_id IS NOT NULL
              AND x.revoked_at IS NULL
              AND x.presence='offline'
              AND x.last_seen_at < now()-interval '10 minutes'
              AND COALESCE(NULLIF(trim(x.push_token_hash),''),'')=''
              AND COALESCE(NULLIF(trim(x.capabilities_json->>'extension'),''),'')=''
              AND NOT EXISTS (SELECT 1 FROM telephony_calls c WHERE c.account_id=x.account_id AND c.device_id=x.id)
              AND NOT EXISTS (SELECT 1 FROM telephony_call_targets t WHERE t.account_id=x.account_id AND t.device_id=x.id)
              AND NOT EXISTS (SELECT 1 FROM telephony_commands c WHERE c.account_id=x.account_id AND c.device_id=x.id)
              AND NOT EXISTS (SELECT 1 FROM telephony_push_outbox p WHERE p.account_id=x.account_id AND p.device_id=x.id)
          ) ranked
          WHERE ranked.rn>1
          LIMIT :l
        ) RETURNING d.id"""),{'l':lim}).rowcount
        stale_web_devices=db.execute(text("""DELETE FROM telephony_devices d WHERE d.id IN (
          SELECT x.id FROM telephony_devices x
          WHERE lower(COALESCE(x.platform,''))='web' AND x.revoked_at IS NULL AND x.presence='offline'
            AND x.last_seen_at < now()-interval '7 days'
            AND COALESCE(NULLIF(trim(x.capabilities_json->>'extension'),''),'')=''
            AND NOT EXISTS (SELECT 1 FROM telephony_calls c WHERE c.account_id=x.account_id AND c.device_id=x.id)
            AND NOT EXISTS (SELECT 1 FROM telephony_call_targets t WHERE t.account_id=x.account_id AND t.device_id=x.id)
            AND NOT EXISTS (SELECT 1 FROM telephony_commands c WHERE c.account_id=x.account_id AND c.device_id=x.id)
            AND NOT EXISTS (SELECT 1 FROM telephony_push_outbox p WHERE p.account_id=x.account_id AND p.device_id=x.id)
          ORDER BY x.last_seen_at ASC LIMIT :l
        ) RETURNING d.id"""),{'l':lim}).rowcount
        db.commit()
    finally:db.close()
    return {'status':'ok','purged':{'push_outbox':int(push or 0),'commands':int(commands or 0),'webhook_nonces':int(nonces or 0),'media_sessions':int(media_sessions or 0),'duplicate_web_devices':int(duplicate_web_devices or 0),'stale_web_devices':int(stale_web_devices or 0)},'retention':{'push_days':pd,'command_days':cd,'nonce_hours':nh,'media_session_history_days':7,'unused_web_device_days':7,'duplicate_web_device_idle_minutes':10},'preserved':['telephony_calls','telephony_events','telephony_audit','telephony_recordings','telephony_transcript_chunks','telephony_cost_ledger','crm','native_devices','web_devices_with_history_or_extension','newest_empty_web_device_per_user']}


def recording_storage_guard(retention_days: int|None=None, min_free_gb: float|None=None, limit: int=200) -> dict[str,Any]:
    """Purges only local audio after transcript+AI are safely stored. DB transcript/analysis remain intact."""
    import shutil
    from pathlib import Path
    ensure_schema()
    root=Path(TELEPHONY_RECORDING_ROOT)
    root.mkdir(parents=True,exist_ok=True)
    retention=max(1,int(retention_days or os.getenv('TELEPHONY_RECORDING_RETENTION_DAYS','30')))
    floor=float(min_free_gb if min_free_gb is not None else os.getenv('TELEPHONY_RECORDING_MIN_FREE_GB','10'))
    usage=shutil.disk_usage(root)
    free_gb=usage.free/(1024**3)
    emergency=free_gb < floor
    db=SessionLocal(); purged=0; freed=0
    try:
        rows=db.execute(text("""SELECT id,account_id,call_id,local_path,created_at FROM telephony_recordings
          WHERE local_path IS NOT NULL AND transcript_status='done' AND analysis_status='done'
            AND (created_at < now()-(:days||' days')::interval OR (:emergency AND created_at < now()-interval '7 days'))
          ORDER BY created_at ASC,id ASC LIMIT :lim"""),{'days':retention,'emergency':emergency,'lim':max(1,min(int(limit),1000))}).mappings().all()
        for r in rows:
            safe=_safe_recording_path(str(r.get('local_path') or ''))
            if not safe: continue
            try:
                size=Path(safe).stat().st_size
                Path(safe).unlink()
                db.execute(text("""UPDATE telephony_recordings SET local_path=NULL,status='purged_local',updated_at=now() WHERE id=:id"""),{'id':r['id']})
                purged+=1; freed+=size
                _audit(r['account_id'],'recording.local.purge','ok',call_id=r['call_id'],metadata={'recording_id':r['id'],'bytes':size,'emergency':emergency})
            except FileNotFoundError:
                db.execute(text("UPDATE telephony_recordings SET local_path=NULL,status='purged_local',updated_at=now() WHERE id=:id"),{'id':r['id']})
        db.commit()
    finally: db.close()
    usage2=shutil.disk_usage(root)
    return {'status':'ok','retention_days':retention,'min_free_gb':floor,'emergency':emergency,'purged':purged,'freed_mb':round(freed/1024/1024,2),'free_gb':round(usage2.free/(1024**3),2)}

def revoke_device(account_id: str, device_id: str, actor_user_id: int|None=None) -> dict[str,Any]:
    """Owner revoke is a hard kill: erase push material and cancel local work for the device without making provider calls."""
    ensure_schema(); db=SessionLocal(); push_cancelled=targets_cancelled=commands_cancelled=0
    try:
        row=db.execute(text("""UPDATE telephony_devices SET revoked_at=now(),presence='offline',push_kind=NULL,push_token_hash=NULL,push_token_enc=NULL,updated_at=now()
          WHERE account_id=:a AND id=:d AND revoked_at IS NULL RETURNING id"""),{'a':account_id,'d':device_id}).first()
        if row:
            push_cancelled=len(db.execute(text("""UPDATE telephony_push_outbox SET status='cancelled',last_error='device revoked',updated_at=now()
              WHERE account_id=:a AND device_id=:d AND status IN ('pending','retry','waiting_configuration') RETURNING id"""),{'a':account_id,'d':device_id}).fetchall())
            targets_cancelled=len(db.execute(text("""UPDATE telephony_call_targets SET status='cancelled',ended_at=COALESCE(ended_at,now())
              WHERE account_id=:a AND device_id=:d AND status='ringing' RETURNING id"""),{'a':account_id,'d':device_id}).fetchall())
            commands_cancelled=len(db.execute(text("""UPDATE telephony_commands SET status='failed',last_error='device revoked',next_attempt_at=NULL,processed_at=COALESCE(processed_at,now()),updated_at=now()
              WHERE account_id=:a AND device_id=:d AND status IN ('queued','waiting_provider') RETURNING id"""),{'a':account_id,'d':device_id}).fetchall())
        db.commit()
    finally: db.close()
    if row:_audit(account_id,'device.revoke','ok',actor_user_id,metadata={'device_id':device_id,'push_cancelled':push_cancelled,'targets_cancelled':targets_cancelled,'commands_cancelled':commands_cancelled})
    return {'status':'ok' if row else 'not_found','device_id':device_id,'push_cancelled':push_cancelled,'targets_cancelled':targets_cancelled,'commands_cancelled':commands_cancelled}


def client_bootstrap(account_id: str, platform:str='web', app_version:str='0.0.0') -> dict[str,Any]:
    st=provider_status(account_id);compat=client_compatibility(platform,app_version,3)
    # Product capability truth comes from the verified production adapter, never
    # from the global superset of commands. A health-checked provider without an
    # implemented media contract is still media-disabled.
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter(st.get('provider')) if st.get('provider_verified') is True else None
    commands=sorted(set(getattr(adapter,'supported_commands',set()) or set()) & CONTROL_COMMANDS) if adapter else []
    caps=set((st.get('adapter') or {}).get('capabilities') or [])
    media_enabled=bool(st.get('provider_verified') is True and caps.intersection({'media_session','webrtc','sip_media'}))
    media_readiness=None
    if st.get('provider')=='telphin':
        try:
            from app.services.telphin_sip_trunk import trunk_status
            from app.services.asterisk_gateway import media_interconnect_health
            sip=trunk_status(account_id); bridge=media_interconnect_health()
            media_readiness={'sip':sip,'bridge':bridge}
            media_enabled=bool(media_enabled and sip.get('ready') and bridge.get('ready'))
        except Exception:
            media_enabled=False
            media_readiness={'status':'unavailable'}
    return {'status':'ok','product':'BORIS Phone','protocol_version':3,'compatibility':compat,'heartbeat_seconds':30,'event_poll_seconds':2.5,
      'commands':commands,'provider_state':st.get('provider_state'),'provider':st.get('provider'),
      'media_enabled':media_enabled,'media_readiness':media_readiness,'adapter':st.get('adapter'),
      'endpoints':{'register':'/api/telephony/devices/register','heartbeat':'/api/telephony/devices/heartbeat',
                   'events':'/api/telephony/events','event_stream':'/api/telephony/events/stream','calls':'/api/telephony/calls','ringing':'/api/telephony/devices/ringing','control':'/api/telephony/calls/{call_id}/control','media':'/api/telephony/media/session','media_activate':'/api/telephony/media/session/activate','media_renew':'/api/telephony/media/session/renew','media_release':'/api/telephony/media/session/release'},'features':{'multi_device_claim':True,'presence':True,'reconnect':True,'sse':True,'sse_polling_fallback':True,'opaque_mobile_push':True,'durable_media_lease':True,'media_bridge_activation':True,'media_lease_renewal':True}}



def _validate_media_session_payload(payload: dict[str,Any]) -> tuple[bool,str]:
    """Require a bounded, explicitly expiring media credential envelope from adapters."""
    if not isinstance(payload,dict): return False,'media_payload_invalid'
    transport=str(payload.get('transport') or '').strip().lower()
    if transport not in {'webrtc','sip_ws','sip_tls'}: return False,'media_transport_invalid'
    session_id=str(payload.get('session_id') or '').strip()
    if not session_id or len(session_id)>160: return False,'media_session_id_invalid'
    if transport in {'webrtc','sip_ws'}:
        expected_protocol='boris-webrtc-v1' if transport=='webrtc' else 'boris-sip-v1'
        if str(payload.get('signaling_protocol') or '').strip()!=expected_protocol:
            return False,'media_signaling_protocol_invalid'
        signaling_url=str(payload.get('signaling_url') or '').strip()
        try:
            parsed=urlparse(signaling_url)
        except Exception:
            return False,'media_signaling_url_invalid'
        if parsed.scheme!='wss' or not parsed.hostname or parsed.username or parsed.password or len(signaling_url)>2048:
            return False,'media_signaling_url_invalid'
        session_token=str(payload.get('session_token') or '').strip()
        if len(session_token)<16 or len(session_token)>2048:
            return False,'media_session_token_invalid'
        if transport=='sip_ws':
            auth_user=str(payload.get('authorization_username') or '').strip()
            if not re.fullmatch(r'bp_[A-Za-z0-9_]{1,61}',auth_user) or auth_user!=session_id:
                return False,'media_sip_username_invalid'
            sip_uri=str(payload.get('sip_uri') or '').strip()
            if not re.fullmatch(r'sip:bp_[A-Za-z0-9_]{1,61}@[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?',sip_uri):
                return False,'media_sip_uri_invalid'
            if not sip_uri.startswith('sip:'+auth_user+'@'):
                return False,'media_sip_uri_invalid'
            try:
                reg_expires=int(payload.get('register_expires_seconds'))
            except Exception:
                return False,'media_sip_registration_expiry_invalid'
            if reg_expires<30 or reg_expires>180:
                return False,'media_sip_registration_expiry_invalid'
        ice=payload.get('ice_servers') or []
        if not isinstance(ice,list) or len(ice)>8:
            return False,'media_ice_servers_invalid'
        for item in ice:
            if not isinstance(item,dict): return False,'media_ice_servers_invalid'
            urls=item.get('urls')
            if isinstance(urls,str): url_values=[urls]
            elif isinstance(urls,list) and 1<=len(urls)<=8: url_values=urls
            else: return False,'media_ice_servers_invalid'
            for value in url_values:
                u=str(value or '').strip()
                if not (u.startswith('stun:') or u.startswith('stuns:') or u.startswith('turn:') or u.startswith('turns:')) or len(u)>1024:
                    return False,'media_ice_servers_invalid'
            if len(str(item.get('username') or ''))>512 or len(str(item.get('credential') or ''))>2048:
                return False,'media_ice_servers_invalid'
    ttl=payload.get('ttl_seconds')
    expires=payload.get('expires_at')
    if ttl is not None:
        try:
            n=int(ttl)
            if n<10 or n>600: return False,'media_ttl_invalid'
        except Exception:return False,'media_ttl_invalid'
    elif expires:
        try:
            dt=datetime.fromisoformat(str(expires).replace('Z','+00:00'))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            left=(dt-utcnow()).total_seconds()
            if left<10 or left>600:return False,'media_expiry_invalid'
        except Exception:return False,'media_expiry_invalid'
    else:return False,'media_expiry_required'
    # Adapter media material may contain short-lived session credentials, but
    # long-lived provider/backend secrets must never cross this boundary.
    forbidden={'api_key','api_secret','secret_key','private_key','authorization','refresh_token','provider_credentials','client_secret','webhook_secret'}
    def _has_forbidden(obj):
        if isinstance(obj,dict):
            for k,v in obj.items():
                key=str(k or '').strip().lower().replace('-','_')
                if key in forbidden or key.endswith('_private_key') or key.endswith('_api_secret'):
                    return True
                if _has_forbidden(v): return True
        elif isinstance(obj,list):
            return any(_has_forbidden(x) for x in obj)
        return False
    if _has_forbidden(payload): return False,'media_secret_field_forbidden'
    try:
        if len(json.dumps(payload,ensure_ascii=False,default=str))>32768:return False,'media_payload_too_large'
    except Exception:return False,'media_payload_invalid'
    return True,'ok'


def _media_payload_contains_provider_secret(payload: Any, credentials: Any) -> bool:
    """Reject adapter media envelopes that echo long-lived provider credentials.

    Short-lived session credentials are expected in a media payload, so generic
    fields such as `token` cannot be forbidden. Instead compare the serialized
    envelope against the exact configured provider secret values (plain and
    URL-encoded). This closes the case where a long-lived token is hidden inside
    an otherwise innocuous endpoint/metadata string.
    """
    from urllib.parse import quote
    try:
        blob=json.dumps(payload,ensure_ascii=False,default=str)
    except Exception:
        return True
    def _walk(v):
        if isinstance(v,dict):
            for vv in v.values(): yield from _walk(vv)
        elif isinstance(v,(list,tuple,set)):
            for vv in v: yield from _walk(vv)
        elif v is not None:
            yield str(v)
    for secret in {x for x in _walk(credentials) if len(x)>=4}:
        if secret in blob or quote(secret,safe='') in blob:
            return True
    return False


def _media_expiry_from_payload(payload: dict[str,Any]) -> datetime:
    """Return the validated short-lived expiry without persisting credential material."""
    expires=payload.get('expires_at') if isinstance(payload,dict) else None
    if expires:
        dt=datetime.fromisoformat(str(expires).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    ttl=max(10,min(int((payload or {}).get('ttl_seconds') or 0),600))
    return utcnow()+timedelta(seconds=ttl)


def _media_managed_by(payload: dict[str,Any]) -> str:
    return 'boris_gateway' if str((payload or {}).get('managed_by') or '').strip().lower()=='boris_gateway' else 'provider'


def _media_endpoint_id(payload: dict[str,Any]) -> str:
    value=str((payload or {}).get('endpoint_id') or (payload or {}).get('session_id') or '').strip()
    return value[:160]


def _persist_media_lease(account_id: str, call_id: str, device_id: str, provider: str, payload: dict[str,Any]) -> tuple[int|None,str]:
    """Persist ownership metadata only. Session/provider passwords are intentionally absent."""
    endpoint_id=_media_endpoint_id(payload)
    if not endpoint_id:
        return None,'media_endpoint_id_missing'
    try:
        expires_at=_media_expiry_from_payload(payload)
    except Exception:
        return None,'media_expiry_invalid'
    managed_by=_media_managed_by(payload)
    transport=str(payload.get('transport') or '').strip().lower()
    sid=str(payload.get('session_id') or '')
    fingerprint=hashlib.sha256((str(provider)+'|'+account_id+'|'+call_id+'|'+device_id+'|'+sid).encode()).hexdigest()
    db=SessionLocal()
    try:
        row=db.execute(text("""INSERT INTO telephony_media_sessions(
            account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
            session_fingerprint,expires_at,created_at,updated_at
          ) VALUES(:a,:c,:d,:p,:e,:t,:m,'active',:f,:x,now(),now())
          ON CONFLICT(endpoint_id) DO NOTHING RETURNING id"""),
          {'a':account_id,'c':call_id,'d':device_id,'p':str(provider or ''),'e':endpoint_id,
           't':transport,'m':managed_by,'f':fingerprint,'x':expires_at}).first()
        db.commit()
        if not row:
            return None,'media_endpoint_duplicate'
        return int(row[0]),'ok'
    except Exception:
        db.rollback()
        return None,'media_lease_persistence_failed'
    finally:
        db.close()


def _cleanup_unpersisted_gateway_payload(payload: dict[str,Any]) -> bool:
    """Best-effort immediate revoke when durable ownership could not be recorded."""
    if _media_managed_by(payload)!='boris_gateway':
        return False
    endpoint_id=_media_endpoint_id(payload)
    if not endpoint_id:
        return False
    try:
        from app.services.phone_media_gateway import delete_webrtc_endpoint
        return delete_webrtc_endpoint(endpoint_id).get('status')=='ok'
    except Exception:
        return False


def _media_lease_row(lease_id: int, account_id: str|None=None, call_id: str|None=None, device_id: str|None=None) -> dict[str,Any]|None:
    db=SessionLocal()
    try:
        sql="""SELECT id,account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
                     session_fingerprint,expires_at,cleanup_attempts,next_cleanup_at,last_error,cleaned_at,
                     bridge_id,media_channel_id,activated_at,last_bridge_error,created_at,updated_at
              FROM telephony_media_sessions WHERE id=:i"""
        params={'i':int(lease_id)}
        if account_id is not None: sql+=" AND account_id=:a";params['a']=account_id
        if call_id is not None: sql+=" AND call_id=:c";params['c']=call_id
        if device_id is not None: sql+=" AND device_id=:d";params['d']=device_id
        row=db.execute(text(sql),params).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


def _finish_media_lease(lease_id: int, final_status: str, reason: str) -> None:
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_media_sessions SET status=:s,last_error=:r,cleaned_at=now(),
                          next_cleanup_at=NULL,updated_at=now() WHERE id=:i"""),
                   {'i':int(lease_id),'s':final_status,'r':str(reason or '')[:120] or None})
        db.commit()
    finally:
        db.close()


def _retry_media_lease_cleanup(lease_id: int, reason: str) -> None:
    row=_media_lease_row(lease_id)
    attempts=min(12,int((row or {}).get('cleanup_attempts') or 0)+1)
    wait_sec=min(600,5*(2**min(attempts-1,7)))
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_media_sessions SET status='cleanup_pending',cleanup_attempts=:n,
                          next_cleanup_at=now()+make_interval(secs=>:w),last_error=:r,updated_at=now()
                          WHERE id=:i AND status NOT IN ('closed','expired')"""),
                   {'i':int(lease_id),'n':attempts,'w':wait_sec,'r':str(reason or 'gateway_cleanup_failed')[:120]})
        db.commit()
    finally:
        db.close()


def renew_media_session(account_id: str, device_id: str, call_id: str, lease_id: int, extend_seconds: int=180) -> dict[str,Any]:
    """Extend BORIS-owned media lifetime only while the same live call/device still owns it.

    No credential material is re-issued here. Provider-owned sessions cannot be
    extended because BORIS cannot truthfully extend a provider credential TTL.
    """
    ensure_schema()
    account_id=str(account_id or '').strip(); device_id=str(device_id or '').strip(); call_id=str(call_id or '').strip()
    try: lid=int(lease_id)
    except Exception: return {'status':'invalid_media_lease'}
    if not account_id or not device_id or not call_id:
        return {'status':'invalid_media_request'}
    seconds=max(60,min(int(extend_seconds or 180),180))
    db=SessionLocal()
    try:
        row=db.execute(text("""SELECT m.id,m.status,m.managed_by,m.expires_at,m.provider,
                 c.state AS call_state,c.device_id AS call_device,c.provider_call_id,
                 d.revoked_at,d.last_seen_at,d.presence
          FROM telephony_media_sessions m
          JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.id=:i AND m.account_id=:a AND m.call_id=:c AND m.device_id=:d
          FOR UPDATE OF m,c,d"""),
          {'i':lid,'a':account_id,'c':call_id,'d':device_id}).mappings().first()
        if not row:
            db.rollback(); return {'status':'media_lease_not_found'}
        if str(row.get('status') or '')!='active':
            db.rollback(); return {'status':'media_lease_not_active'}
        if str(row.get('managed_by') or '')!='boris_gateway':
            db.rollback(); return {'status':'media_lease_renewal_unsupported'}
        now=utcnow()
        if not row.get('expires_at') or row['expires_at']<=now:
            db.rollback(); return {'status':'media_lease_expired'}
        if str(row.get('call_state') or '') not in {'answered','active','on_hold','transferring'}:
            db.rollback(); return {'status':'call_not_media_active','state':row.get('call_state')}
        if str(row.get('call_device') or '')!=device_id or not str(row.get('provider_call_id') or '').strip():
            db.rollback(); return {'status':'call_not_claimed_by_device'}
        if row.get('revoked_at') is not None:
            db.rollback(); return {'status':'device_not_found'}
        if not row.get('last_seen_at') or (now-row['last_seen_at']).total_seconds()>90 or str(row.get('presence') or '') in {'offline','dnd'}:
            db.rollback(); return {'status':'device_not_ready'}
        new_expiry=now+timedelta(seconds=seconds)
        db.execute(text("""UPDATE telephony_media_sessions
          SET expires_at=GREATEST(expires_at,:x),updated_at=now(),last_error=NULL
          WHERE id=:i AND status='active'"""),{'i':lid,'x':new_expiry})
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    _audit(account_id,'media.session.renew','ok',None,call_id,provider=row.get('provider'),
           metadata={'lease_id':lid,'device_id':device_id,'extend_seconds':seconds})
    return {'status':'ok','media_lease_id':lid,'expires_at':new_expiry.isoformat(),
            'renew_after_seconds':max(30,min(60,seconds//2)),'owner_action_required':False}


def release_media_session(account_id: str, device_id: str, call_id: str, lease_id: int, reason: str='client_release') -> dict[str,Any]:
    """Revoke a BORIS gateway endpoint immediately; provider sessions fail-safe to short TTL expiry."""
    ensure_schema()
    try: lid=int(lease_id)
    except Exception: return {'status':'invalid_media_lease'}
    row=_media_lease_row(lid,str(account_id or '').strip(),str(call_id or '').strip(),str(device_id or '').strip())
    if not row:
        return {'status':'media_lease_not_found'}
    if row.get('status') in {'closed','expired'}:
        return {'status':'ok','lease_id':lid,'already_closed':True}
    now=utcnow()
    if str(row.get('managed_by') or '')=='boris_gateway':
        try:
            from app.services.asterisk_gateway import cleanup_media_bridge
            bridge_cleanup=cleanup_media_bridge(lid)
        except Exception:
            bridge_cleanup={'status':'cleanup_pending'}
        try:
            from app.services.phone_media_gateway import delete_webrtc_endpoint
            endpoint_cleanup=delete_webrtc_endpoint(str(row.get('endpoint_id') or ''))
        except Exception:
            endpoint_cleanup={'status':'cleanup_pending'}
        cleaned={'status':'ok' if bridge_cleanup.get('status')=='ok' and endpoint_cleanup.get('status')=='ok' else 'cleanup_pending',
                 'bridge':bridge_cleanup.get('status'),'endpoint':endpoint_cleanup.get('status')}
        if cleaned.get('status')=='ok':
            final_status='expired' if row.get('expires_at') and row['expires_at']<=now else 'closed'
            _finish_media_lease(lid,final_status,reason)
            _audit(str(row['account_id']),'media.session.cleanup','ok',None,str(row['call_id']),provider=row.get('provider'),
                   metadata={'lease_id':lid,'device_id':row.get('device_id'),'reason':str(reason or '')[:80]})
            return {'status':'ok','lease_id':lid,'revoked':True}
        _retry_media_lease_cleanup(lid,'gateway_cleanup_failed')
        _audit(str(row['account_id']),'media.session.cleanup','retry',None,str(row['call_id']),provider=row.get('provider'),
               metadata={'lease_id':lid,'device_id':row.get('device_id'),'reason':str(reason or '')[:80]})
        return {'status':'cleanup_pending','lease_id':lid,'owner_action_required':False}
    # Provider-issued credential cannot be safely revoked unless that adapter
    # explicitly grows a revoke contract. Never invent one: rely on validated <=600s TTL.
    if row.get('expires_at') and row['expires_at']<=now:
        _finish_media_lease(lid,'expired',reason)
        return {'status':'ok','lease_id':lid,'expired':True}
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_media_sessions SET status='await_expiry',
                          next_cleanup_at=expires_at,last_error=:r,updated_at=now()
                          WHERE id=:i AND status NOT IN ('closed','expired')"""),
                   {'i':lid,'r':'provider_session_waiting_expiry'})
        db.commit()
    finally:
        db.close()
    return {'status':'await_expiry','lease_id':lid,'owner_action_required':False}


def activate_media_session(account_id: str, device_id: str, call_id: str, lease_id: int) -> dict[str,Any]:
    """Bind a registered temporary browser SIP endpoint to the exact live provider channel."""
    ensure_schema()
    try: lid=int(lease_id)
    except Exception: return {'status':'invalid_media_lease'}
    account_id=str(account_id or '').strip();device_id=str(device_id or '').strip();call_id=str(call_id or '').strip()
    row=_media_lease_row(lid,account_id,call_id,device_id)
    if not row:
        return {'status':'media_lease_not_found'}
    if row.get('status')!='active':
        return {'status':'media_lease_not_active','lease_status':row.get('status')}
    if str(row.get('managed_by') or '')!='boris_gateway':
        return {'status':'media_activation_not_applicable'}
    if row.get('expires_at') and row['expires_at']<=utcnow():
        release_media_session(account_id,device_id,call_id,lid,'activation_after_expiry')
        return {'status':'media_lease_expired'}
    db=SessionLocal()
    try:
        call=db.execute(text("""SELECT id,state,device_id,provider,provider_call_id
          FROM telephony_calls WHERE account_id=:a AND id=:c"""),
          {'a':account_id,'c':call_id}).mappings().first()
        dev=db.execute(text("""SELECT id,revoked_at FROM telephony_devices
          WHERE account_id=:a AND id=:d"""),{'a':account_id,'d':device_id}).mappings().first()
    finally: db.close()
    if not call or str(call.get('state') or '') not in {'answered','active','on_hold','transferring'}:
        release_media_session(account_id,device_id,call_id,lid,'activation_call_not_live')
        return {'status':'media_call_not_live'}
    if str(call.get('device_id') or '')!=device_id:
        release_media_session(account_id,device_id,call_id,lid,'activation_device_mismatch')
        return {'status':'media_device_mismatch'}
    if not dev or dev.get('revoked_at') is not None:
        release_media_session(account_id,device_id,call_id,lid,'activation_device_revoked')
        return {'status':'media_device_revoked'}
    provider=str(call.get('provider') or '').strip().lower()
    if provider not in {'telphin','mcn'}:
        return {'status':'media_provider_binding_pending','provider':provider}
    if provider=='telphin':
        from app.services.telphin_sip_trunk import trunk_status
        trunk=trunk_status(account_id)
        if not trunk.get('ready'):
            return {'status':'telphin_sip_not_registered'}
    provider_call_id=str(call.get('provider_call_id') or '').strip()
    if not provider_call_id:
        return {'status':'provider_call_id_missing'}
    try:
        from app.services.phone_media_gateway import webrtc_endpoint_health
        endpoint_health=webrtc_endpoint_health(str(row.get('endpoint_id') or ''))
    except Exception:
        endpoint_health={'ready':False,'status':'endpoint_health_error'}
    if not endpoint_health.get('ready'):
        return {'status':'media_endpoint_waiting_registration','media_lease_id':lid,
                'endpoint_state':endpoint_health.get('state'),'owner_action_required':False}
    from app.services.asterisk_gateway import bind_browser_media
    result=bind_browser_media(account_id,call_id,provider_call_id,str(row.get('endpoint_id') or ''),lid)
    if result.get('status') in {'activating','ok'}:
        db=SessionLocal()
        try:
            db.execute(text("""UPDATE telephony_media_sessions
              SET bridge_id=:b,media_channel_id=:m,last_bridge_error=NULL,updated_at=now()
              WHERE id=:i AND status='active'"""),
              {'i':lid,'b':result.get('bridge_id'),'m':result.get('media_channel_id')})
            db.commit()
        finally: db.close()
        _audit(account_id,'media.session.activate','ok',None,call_id,provider=provider,
               metadata={'lease_id':lid,'device_id':device_id,'bridge_id':result.get('bridge_id')})
        return {'status':'activating','media_lease_id':lid,'bridge_id':result.get('bridge_id')}
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_media_sessions SET last_bridge_error=:e,updated_at=now()
          WHERE id=:i"""),{'i':lid,'e':str(result.get('status') or 'bridge_activation_failed')[:120]})
        db.commit()
    finally: db.close()
    return {'status':str(result.get('status') or 'bridge_activation_failed'),
            'media_lease_id':lid,'owner_action_required':False}


def media_session_guardian(limit: int=100, include_synthetic: bool=False) -> dict[str,Any]:
    """Detect -> revoke/expire -> verify short-lived media residue without provider test calls."""
    ensure_schema();lim=max(1,min(int(limit),500))
    db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT m.id,m.account_id,m.call_id,m.device_id,m.provider,m.endpoint_id,m.transport,
                 m.managed_by,m.status,m.expires_at,m.cleanup_attempts,m.next_cleanup_at,
                 c.state AS call_state,c.device_id AS call_device,d.revoked_at AS device_revoked
          FROM telephony_media_sessions m
          LEFT JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          LEFT JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.status IN ('active','cleanup_pending','await_expiry')
            AND (:include_synthetic OR NOT (lower(m.account_id) ~ '^__.*qa' OR lower(m.account_id) ~ '^qa[-_]'))
            AND (m.next_cleanup_at IS NULL OR m.next_cleanup_at<=now())
            AND (
              m.status IN ('cleanup_pending','await_expiry') OR m.expires_at<=now() OR
              c.id IS NULL OR c.state NOT IN ('answered','active','on_hold','transferring') OR
              COALESCE(c.device_id,'')<>m.device_id OR d.id IS NULL OR d.revoked_at IS NOT NULL
            )
          ORDER BY COALESCE(m.next_cleanup_at,m.expires_at),m.id LIMIT :lim"""),
          {'include_synthetic':bool(include_synthetic),'lim':lim}).mappings().all()
    finally:
        db.close()
    cleaned=expired=waiting=retry=0
    for r in rows:
        reason='lease_due'
        if r.get('expires_at') and r['expires_at']<=utcnow(): reason='ttl_expired'
        elif not r.get('call_state'): reason='call_missing'
        elif str(r.get('call_state') or '') not in {'answered','active','on_hold','transferring'}: reason='call_ended'
        elif str(r.get('call_device') or '')!=str(r.get('device_id') or ''): reason='call_reassigned'
        elif r.get('device_revoked') is not None: reason='device_revoked'
        result=release_media_session(str(r['account_id']),str(r['device_id']),str(r['call_id']),int(r['id']),reason)
        if result.get('revoked'): cleaned+=1
        elif result.get('expired'): expired+=1
        elif result.get('status')=='await_expiry': waiting+=1
        elif result.get('status')=='cleanup_pending': retry+=1
        elif result.get('status')=='ok': cleaned+=1
    # Active WebPhone leases are also self-healed. The browser endpoint may
    # register a fraction of a second after the lease is issued; if the first
    # activation request races that registration, the daemon retries without
    # involving the owner and without creating duplicate browser legs.
    adb=SessionLocal()
    try:
        pending_activation=adb.execute(text("""SELECT m.id,m.account_id,m.call_id,m.device_id
          FROM telephony_media_sessions m
          JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.status='active' AND m.managed_by='boris_gateway'
            AND m.expires_at>now() AND m.activated_at IS NULL
            AND m.provider IN ('mcn','telphin')
            AND c.state IN ('answered','active','on_hold','transferring')
            AND COALESCE(c.device_id,'')=m.device_id
            AND d.revoked_at IS NULL
            AND (:include_synthetic OR NOT (lower(m.account_id) ~ '^__.*qa' OR lower(m.account_id) ~ '^qa[-_]'))
          ORDER BY m.created_at,m.id LIMIT :lim"""),
          {'include_synthetic':bool(include_synthetic),'lim':lim}).mappings().all()
    finally:
        adb.close()
    activation_attempted=activation_started=activation_waiting=activation_errors=0
    for p in pending_activation:
        activation_attempted+=1
        out=activate_media_session(str(p['account_id']),str(p['device_id']),str(p['call_id']),int(p['id']))
        st=str(out.get('status') or '')
        if st in {'activating','ok'}:
            activation_started+=1
        elif st=='media_endpoint_waiting_registration':
            activation_waiting+=1
        else:
            activation_errors+=1

    vdb=SessionLocal()
    try:
        unresolved=int(vdb.execute(text("""SELECT count(*) FROM telephony_media_sessions m
          LEFT JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          LEFT JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.status IN ('active','cleanup_pending')
            AND (:include_synthetic OR NOT (lower(m.account_id) ~ '^__.*qa' OR lower(m.account_id) ~ '^qa[-_]'))
            AND (m.expires_at<=now() OR c.id IS NULL OR c.state NOT IN ('answered','active','on_hold','transferring')
                 OR COALESCE(c.device_id,'')<>m.device_id OR d.id IS NULL OR d.revoked_at IS NOT NULL)"""),
          {'include_synthetic':bool(include_synthetic)}).scalar() or 0)
    finally:vdb.close()
    degraded=unresolved>0 or activation_errors>0
    return {'status':'degraded' if degraded else 'ok','candidates':len(rows),'cleaned':cleaned,'expired':expired,
            'waiting_expiry':waiting,'retry':retry,'unresolved':unresolved,
            'activation_attempted':activation_attempted,'activation_started':activation_started,
            'activation_waiting_registration':activation_waiting,'activation_errors':activation_errors,
            'owner_action_required':False}


def request_media_session(account_id: str, device_id: str, call_id: str) -> dict[str,Any]:
    """Issue short-lived media material bound to one live call and one claimed device.

    A device-scoped SIP/WebRTC session is too broad for production: the backend must
    prove that the call belongs to the same account, is still media-active and was
    atomically claimed/assigned to this exact device before any media credential is
    released. Long-lived provider/SIP secrets never leave the backend.
    """
    ensure_schema()
    account_id=str(account_id or '').strip(); device_id=str(device_id or '').strip(); call_id=str(call_id or '').strip()
    if not account_id or not device_id or not call_id:
        return {'status':'invalid_media_request','message':'account_id, device_id и call_id обязательны'}
    db=SessionLocal()
    try:
        dev=db.execute(text("SELECT id,revoked_at,last_seen_at,presence FROM telephony_devices WHERE account_id=:a AND id=:d"),{'a':account_id,'d':device_id}).mappings().first()
        call=db.execute(text("SELECT id,state,device_id,provider,provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:
        db.close()
    if not dev or dev.get('revoked_at') is not None:
        return {'status':'device_not_found'}
    if not dev.get('last_seen_at') or (utcnow()-dev['last_seen_at']).total_seconds()>90 or str(dev.get('presence') or '') in {'offline','dnd'}:
        return {'status':'device_not_ready','message':'Устройство не готово к media session'}
    if not call:
        return {'status':'call_not_found'}
    if str(call.get('state') or '') not in {'answered','active','on_hold','transferring'}:
        return {'status':'call_not_media_active','state':call.get('state'),'message':'Media доступен только после принятия активного звонка'}
    if str(call.get('device_id') or '') != device_id:
        return {'status':'call_not_claimed_by_device','message':'Звонок не закреплён за этим устройством'}
    if not str(call.get('provider_call_id') or '').strip():
        return {'status':'provider_call_not_bound','message':'Нет подтверждённого идентификатора звонка оператора'}
    p,creds=provider_credentials(account_id)
    if not p or not creds:
        return {'status':'provider_not_connected','message':'Оператор ещё не подключён'}
    if call.get('provider') and str(call.get('provider')).strip().lower()!=str(p).strip().lower():
        return {'status':'provider_call_mismatch','message':'Звонок принадлежит другому оператору'}
    from app.services.telephony_adapters import get_adapter, adapter_status
    adapter=get_adapter(p); ast=adapter_status(p)
    if not adapter or not ast.get('implemented'):
        return {'status':'media_pending','provider':p,'message':'Media adapter оператора ещё не подключён'}
    public={**_provider_public_config(account_id),'__boris_account_id':account_id}
    try:
        result=adapter.media_session(device_id=device_id,call_id=call_id,credentials=creds,public_config=public)
    except Exception as exc:
        error_type=type(exc).__name__[:120]
        _audit(account_id,'media.session','transport_error',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'ok':False,'error_type':error_type})
        return {'status':'media_provider_unavailable','provider':p,'message':'Не удалось безопасно открыть media session','error_code':'provider_transport_error'}
    provider_status=_sanitize_provider_status(result.status,creds,'media_provider_error')
    _audit(account_id,'media.session',provider_status,None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'ok':bool(result.ok)})
    if not result.ok:
        return {'status':provider_status,'provider':p,'message':'Не удалось безопасно открыть media session','error_code':'provider_'+provider_status}
    valid,reason=_validate_media_session_payload(result.payload)
    if not valid:
        _cleanup_unpersisted_gateway_payload(result.payload if isinstance(result.payload,dict) else {})
        _audit(account_id,'media.session.invalid','blocked',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'reason':reason})
        return {'status':'media_contract_invalid','provider':p,'message':'Media adapter вернул небезопасную сессию','reason':reason}
    if _media_payload_contains_provider_secret(result.payload,creds):
        reason='media_provider_credential_echo'
        _cleanup_unpersisted_gateway_payload(result.payload)
        _audit(account_id,'media.session.invalid','blocked',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'reason':reason})
        return {'status':'media_contract_invalid','provider':p,'message':'Media adapter вернул небезопасную сессию','reason':reason}
    lease_id,lease_status=_persist_media_lease(account_id,call_id,device_id,str(p),result.payload)
    if lease_id is None:
        revoked=_cleanup_unpersisted_gateway_payload(result.payload)
        _audit(account_id,'media.session.lease','blocked',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'reason':lease_status,'gateway_revoked':bool(revoked)})
        return {'status':'media_lease_unavailable','provider':p,'message':'Media session не получила durable ownership и не выдана клиенту','reason':lease_status}

    # The adapter boundary may take seconds. Call/device truth can change while
    # no PostgreSQL transaction is intentionally held over provider I/O. Before
    # releasing short-lived SIP/WebRTC credentials, revalidate the same call,
    # provider identity and device assignment. A session created for a call that
    # ended/reassigned/revoked in flight is discarded rather than leaked client-side.
    vdb=SessionLocal()
    try:
        live_call=vdb.execute(text("SELECT state,device_id,provider,provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        live_dev=vdb.execute(text("SELECT revoked_at,last_seen_at,presence FROM telephony_devices WHERE account_id=:a AND id=:d"),{'a':account_id,'d':device_id}).mappings().first()
    finally:
        vdb.close()
    stale_reason=None
    if not live_call: stale_reason='call_missing_after_provider'
    elif str(live_call.get('state') or '') not in {'answered','active','on_hold','transferring'}: stale_reason='call_not_media_active_after_provider'
    elif str(live_call.get('device_id') or '')!=device_id: stale_reason='call_device_changed_after_provider'
    elif str(live_call.get('provider') or p).strip().lower()!=str(p).strip().lower(): stale_reason='call_provider_changed_after_provider'
    elif str(live_call.get('provider_call_id') or '')!=str(call.get('provider_call_id') or ''): stale_reason='provider_call_identity_changed_after_provider'
    elif not live_dev or live_dev.get('revoked_at') is not None: stale_reason='device_revoked_after_provider'
    elif not live_dev.get('last_seen_at') or (utcnow()-live_dev['last_seen_at']).total_seconds()>90 or str(live_dev.get('presence') or '') in {'offline','dnd'}: stale_reason='device_not_ready_after_provider'
    if stale_reason:
        cleanup=release_media_session(account_id,device_id,call_id,int(lease_id),stale_reason)
        _audit(account_id,'media.session.postprovider_revalidate','blocked',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'reason':stale_reason,'lease_id':int(lease_id),'cleanup_status':cleanup.get('status')})
        return {'status':'media_session_stale','provider':p,'message':'Звонок или устройство изменились во время открытия media session','reason':stale_reason}

    sid=str((result.payload or {}).get('session_id') or '')
    _audit(account_id,'media.session.validated','ok',None,call_id,provider=p,metadata={'device_id':device_id,'call_id':call_id,'lease_id':int(lease_id),'session_fingerprint':hashlib.sha256(sid.encode()).hexdigest()[:16] if sid else None})
    return {'status':'ok','provider':p,'call_id':call_id,'media_lease_id':int(lease_id),'media':result.payload,'contract_version':5}


def report_media_transport_proof(account_id: str, device_id: str, call_id: str, lease_id: int,
                                 stats: dict[str,Any] | None=None) -> dict[str,Any]:
    """Persist device-scoped evidence that an activated WebRTC/SIP media lease moved RTP.

    `media.session.validated` proves only that BORIS issued a safe, short-lived
    media contract. It must never be treated as proof of real audio. This second
    boundary accepts only a report from the exact call/device/lease after the
    Asterisk media leg was activated, and requires bidirectional RTP counters.
    """
    ensure_schema()
    account_id=str(account_id or '').strip(); device_id=str(device_id or '').strip(); call_id=str(call_id or '').strip()
    try: lid=int(lease_id)
    except Exception: return {'status':'invalid_media_lease'}
    if not account_id or not device_id or not call_id or lid<=0:
        return {'status':'invalid_media_proof_request'}
    payload=stats if isinstance(stats,dict) else {}
    def _count(name: str) -> int:
        try:return max(0,int(payload.get(name) or 0))
        except Exception:return 0
    packets_sent=_count('packets_sent'); packets_received=_count('packets_received')
    bytes_sent=_count('bytes_sent'); bytes_received=_count('bytes_received')
    candidate_ok=bool(payload.get('candidate_pair_succeeded') or str(payload.get('ice_state') or '').lower() in {'connected','completed'})
    dtls_ok=str(payload.get('dtls_state') or '').lower() in {'connected','closed'}
    # Require evidence in both directions. Tiny non-zero counters can be STUN/
    # negotiation noise rather than usable audio, so enforce conservative floors.
    transport_ok=bool(candidate_ok and dtls_ok and packets_sent>=3 and packets_received>=3 and bytes_sent>=120 and bytes_received>=120)
    db=SessionLocal()
    try:
        row=db.execute(text("""SELECT m.id,m.status,m.managed_by,m.activated_at,m.provider,m.expires_at,
                 c.state AS call_state,c.device_id AS call_device,c.provider_call_id,
                 d.revoked_at,d.last_seen_at,d.presence
          FROM telephony_media_sessions m
          JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.id=:i AND m.account_id=:a AND m.call_id=:c AND m.device_id=:d"""),
          {'i':lid,'a':account_id,'c':call_id,'d':device_id}).mappings().first()
        if not row:return {'status':'media_lease_not_found'}
        if str(row.get('status') or '')!='active':return {'status':'media_lease_not_active','lease_status':row.get('status')}
        if str(row.get('managed_by') or '')=='boris_gateway' and row.get('activated_at') is None:return {'status':'media_transport_not_activated'}
        if str(row.get('call_state') or '') not in {'answered','active','on_hold','transferring'}:
            return {'status':'call_not_media_active','state':row.get('call_state')}
        if str(row.get('call_device') or '')!=device_id:return {'status':'call_not_claimed_by_device'}
        if row.get('revoked_at') is not None:return {'status':'device_not_found'}
        now=utcnow()
        if not row.get('last_seen_at') or (now-row['last_seen_at']).total_seconds()>90 or str(row.get('presence') or '') in {'offline','dnd'}:
            return {'status':'device_not_ready'}
        if row.get('expires_at') and row['expires_at']<=now:return {'status':'media_lease_expired'}
        if not transport_ok:
            _audit(account_id,'media.transport.proof','blocked',None,call_id,provider=row.get('provider'),metadata={
                'device_id':device_id,'lease_id':lid,'candidate_pair_succeeded':candidate_ok,'dtls_connected':dtls_ok,
                'packets_sent':packets_sent,'packets_received':packets_received,'bytes_sent':bytes_sent,'bytes_received':bytes_received})
            return {'status':'media_transport_not_proven','owner_action_required':False}
        existing=db.execute(text("""SELECT id FROM telephony_audit
          WHERE account_id=:a AND call_id=:c AND provider=:p
            AND action='media.transport.proven' AND result='ok'
            AND COALESCE((metadata_json->>'lease_id')::bigint,0)=:i
          ORDER BY id DESC LIMIT 1"""),{'a':account_id,'c':call_id,'p':str(row.get('provider') or ''),'i':lid}).scalar()
    finally:db.close()
    if existing:
        return {'status':'ok','media_lease_id':lid,'transport_proven':True,'replayed':True}
    _audit(account_id,'media.transport.proven','ok',None,call_id,provider=row.get('provider'),metadata={
        'device_id':device_id,'lease_id':lid,'candidate_pair_succeeded':candidate_ok,'dtls_connected':dtls_ok,
        'packets_sent':packets_sent,'packets_received':packets_received,'bytes_sent':bytes_sent,'bytes_received':bytes_received})
    return {'status':'ok','media_lease_id':lid,'transport_proven':True,'owner_action_required':False}


def save_call_disposition(account_id: str, call_id: str, qualification: str|None=None,
                          summary: str|None=None, next_action: str|None=None,
                          callback_at: str|None=None, actor_user_id: int|None=None) -> dict[str,Any]:
    """Manager post-call result. Updates the canonical call + existing CRM activity/task; no parallel sales entity."""
    ensure_schema()
    allowed={'','hot','warm','cold','not_target','qualified','unqualified'}
    q=str(qualification or '').strip().lower()
    if q not in allowed: return {'status':'invalid_qualification','allowed':sorted(x for x in allowed if x)}
    summ=str(summary or '').strip()[:3000]; nxt=str(next_action or '').strip()[:1200]
    cb=None
    if callback_at:
        try:
            cb=datetime.fromisoformat(str(callback_at).replace('Z','+00:00'))
            if cb.tzinfo is None: cb=cb.replace(tzinfo=timezone.utc)
        except Exception: return {'status':'invalid_callback_at'}
    db=SessionLocal(); task_id=None
    try:
        call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'not_found'}
        db.execute(text("""UPDATE telephony_calls SET qualification=CASE WHEN :q='' THEN qualification ELSE :q END,
          summary=CASE WHEN :s='' THEN summary ELSE :s END,next_action=CASE WHEN :n='' THEN next_action ELSE :n END,
          callback_due_at=COALESCE(:cb,callback_due_at),callback_status=CASE WHEN :cb IS NOT NULL THEN 'required' ELSE callback_status END,updated_at=now()
          WHERE account_id=:a AND id=:c"""),{'q':q,'s':summ,'n':nxt,'cb':cb,'a':account_id,'c':call_id})
        owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':account_id}).scalar()
        if owner is not None and call.get('crm_contact_id'):
            ref=f'boris_call:{account_id}:{call_id}'
            meta={'manual_disposition':True,'qualification':q or None,'summary':summ or None,'next_action':nxt or None,'callback_at':cb.isoformat() if cb else None}
            db.execute(text("""UPDATE boris_crm_activities SET metadata_json=COALESCE(metadata_json,'{}'::jsonb)||CAST(:m AS jsonb),
              body=CASE WHEN :s='' THEN body ELSE COALESCE(body,'')||E'\nИтог менеджера: '||:s END
              WHERE owner_user_id=:o AND source_ref=:r"""),{'m':json.dumps(meta,ensure_ascii=False),'s':summ,'o':int(owner),'r':ref})
            if cb:
                tref=f'boris_phone_followup:{account_id}:{call_id}:{cb.isoformat()}'
                existing=db.execute(text("SELECT id FROM boris_crm_tasks WHERE owner_user_id=:o AND description=:r LIMIT 1"),{'o':int(owner),'r':tref}).scalar()
                if existing: task_id=int(existing)
                else:
                    task_id=int(db.execute(text("""INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source)
                      VALUES(:o,:d,:c,:t,:r,COALESCE(:u,:o),:due,'open','boris_telephony') RETURNING id"""),
                      {'o':int(owner),'d':call.get('crm_deal_id'),'c':call.get('crm_contact_id'),'t':(nxt or 'Перезвонить клиенту')[:300],
                       'r':tref,'u':actor_user_id,'due':cb}).scalar_one())
        db.commit()
        row=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    except Exception:
        db.rollback(); raise
    finally: db.close()
    _audit(account_id,'call.disposition.save','ok',actor_user_id,call_id,metadata={'qualification':q or None,'callback_task_id':task_id})
    return {'status':'ok','call':_public_call_view(row),'task_id':task_id}


def call_detail(account_id: str, call_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        c=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not c:return {'status':'not_found'}
        events=[dict(x) for x in db.execute(text("SELECT id,event_type,payload_json,created_at FROM telephony_events WHERE account_id=:a AND call_id=:c ORDER BY id"),{'a':account_id,'c':call_id}).mappings().all()]
        rec=[_public_recording_view(x) for x in db.execute(text("SELECT * FROM telephony_recordings WHERE account_id=:a AND call_id=:c ORDER BY id DESC"),{'a':account_id,'c':call_id}).mappings().all()]
        cmds=[dict(x) for x in db.execute(text("SELECT * FROM telephony_commands WHERE account_id=:a AND call_id=:c ORDER BY id DESC"),{'a':account_id,'c':call_id}).mappings().all()]
        targets=[dict(x) for x in db.execute(text("SELECT t.*,d.name,d.platform,d.presence FROM telephony_call_targets t LEFT JOIN telephony_devices d ON d.id=t.device_id WHERE t.account_id=:a AND t.call_id=:c ORDER BY t.id"),{'a':account_id,'c':call_id}).mappings().all()]
        quality=db.execute(text("SELECT * FROM telephony_call_quality WHERE account_id=:a AND call_id=:c ORDER BY updated_at DESC,id DESC LIMIT 1"),{'a':account_id,'c':call_id}).mappings().first()
        chunks=[dict(x) for x in db.execute(text("SELECT id,seq,speaker,text_content,start_ms,end_ms,is_final,created_at FROM telephony_transcript_chunks WHERE account_id=:a AND call_id=:c ORDER BY seq,id LIMIT 500"),{'a':account_id,'c':call_id}).mappings().all()]
        return {'status':'ok','call':_public_call_view(c),'events':events,'recordings':rec,'commands':cmds,'targets':targets,'quality':dict(quality) if quality else None,'transcript_chunks':chunks}
    finally:db.close()

def evaluate_call_quality(account_id: str, call_id: str, force: bool=False) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call: return {'status':'not_found'}
        old=db.execute(text("SELECT * FROM telephony_call_quality WHERE account_id=:a AND call_id=:c AND version='v1'"),{'a':account_id,'c':call_id}).mappings().first()
        if old and not force: return {'status':'ok','quality':dict(old),'cached':True}
        chunks=[dict(x) for x in db.execute(text("SELECT speaker,text_content FROM telephony_transcript_chunks WHERE account_id=:a AND call_id=:c AND is_final=true ORDER BY seq,id"),{'a':account_id,'c':call_id}).mappings().all()]
        rec=db.execute(text("SELECT transcript,analysis_json FROM telephony_recordings WHERE account_id=:a AND call_id=:c AND transcript_status='done' ORDER BY id DESC LIMIT 1"),{'a':account_id,'c':call_id}).mappings().first()
        transcript=' '.join(str(x.get('text_content') or '') for x in chunks).strip() or str((rec or {}).get('transcript') or '').strip()
        if not transcript and not call.get('summary') and not call.get('next_action'): return {'status':'insufficient_evidence'}
        # Real people call BORIS: never credit a manager for words spoken by the client.
        manager_text=' '.join(str(x.get('text_content') or '') for x in chunks if str(x.get('speaker') or '').lower() in {'manager','agent','employee','operator','моп','менеджер'}).strip()
        client_text=' '.join(str(x.get('text_content') or '') for x in chunks if str(x.get('speaker') or '').lower() in {'client','customer','caller','клиент'}).strip()
        # Legacy non-diarized recordings remain usable, but are marked as mixed evidence.
        evidence_mode='diarized' if manager_text else 'mixed'
        manager_low=(manager_text or transcript).lower(); client_low=client_text.lower(); all_low=transcript.lower()
        mhit=lambda words:any(w in manager_low for w in words); chit=lambda words:any(w in client_low for w in words); ahit=lambda words:any(w in all_low for w in words)
        scores={'need':100 if mhit(['какая задач','что вам нуж','что нужно','что ищ','уточн','расскажите']) else (60 if call.get('summary') else 0),'timeline':100 if mhit(['какой срок','когда нуж','когда план','к какой дат','по срок']) else 0,'budget':100 if mhit(['какой бюджет','бюджет','по цене','ценовой','стоимость подходит']) else 0,'presentation':100 if mhit(['предлага','можем','услов','гарант','достав','решение']) else 0,'objection':100 if chit(['дорого','подума','сомнен','конкур']) and mhit(['поэтому','вариант','решим','можем','предлага','уточн']) else (50 if chit(['дорого','подума','сомнен','конкур']) and evidence_mode=='mixed' else 0),'next':100 if call.get('next_action') or call.get('callback_due_at') or mhit(['перезвон','встреч','отправ','пришл','следующ']) else 0}
        labels=[('Потребность',scores['need']),('Срок',scores['timeline']),('Бюджет/цена',scores['budget']),('Презентация',scores['presentation']),('Возражения',scores['objection']),('Следующий шаг',scores['next'])]
        strengths=[n for n,v in labels if v>=80]; gaps=[n for n,v in labels if v<80]; evidence=[{'speaker':x.get('speaker'),'text':str(x.get('text_content') or '')[:280]} for x in chunks[-12:] if x.get('text_content')]
        total=round(sum(v for _,v in labels)/6)
        vals={'a':account_id,'c':call_id,'u':call.get('user_id'),'n':scores['need'],'t':scores['timeline'],'b':scores['budget'],'p':scores['presentation'],'o':scores['objection'],'s':scores['next'],'total':total,'str':json.dumps(strengths,ensure_ascii=False),'g':json.dumps(gaps,ensure_ascii=False),'e':json.dumps([{'evidence_mode':evidence_mode}]+evidence,ensure_ascii=False)}
        sql="""INSERT INTO telephony_call_quality(account_id,call_id,manager_user_id,evaluator,version,need_score,timeline_score,budget_score,presentation_score,objection_score,next_step_score,total_score,strengths_json,gaps_json,evidence_json,status) VALUES(:a,:c,:u,'evidence_rules','v1',:n,:t,:b,:p,:o,:s,:total,CAST(:str AS jsonb),CAST(:g AS jsonb),CAST(:e AS jsonb),'done') ON CONFLICT(account_id,call_id,version) DO UPDATE SET manager_user_id=excluded.manager_user_id,need_score=excluded.need_score,timeline_score=excluded.timeline_score,budget_score=excluded.budget_score,presentation_score=excluded.presentation_score,objection_score=excluded.objection_score,next_step_score=excluded.next_step_score,total_score=excluded.total_score,strengths_json=excluded.strengths_json,gaps_json=excluded.gaps_json,evidence_json=excluded.evidence_json,status='done',updated_at=now() RETURNING *"""
        row=db.execute(text(sql),vals).mappings().first(); db.commit(); return {'status':'ok','quality':dict(row)}
    finally: db.close()

def quality_guardian(account_id: str|None=None, days: int=7, limit: int=100) -> dict[str,Any]:
    """Evaluate completed calls that have real evidence but no quality result yet."""
    ensure_schema(); days=max(1,min(int(days),30)); limit=max(1,min(int(limit),500)); db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT c.account_id,c.id FROM telephony_calls c
          WHERE (:a IS NULL OR c.account_id=:a) AND c.started_at>=now()-(:d||' days')::interval
            AND c.state IN ('ended','transferred')
            AND NOT EXISTS (SELECT 1 FROM telephony_call_quality q WHERE q.account_id=c.account_id AND q.call_id=c.id AND q.version='v1')
            AND (NULLIF(trim(COALESCE(c.summary,'')),'') IS NOT NULL OR NULLIF(trim(COALESCE(c.next_action,'')),'') IS NOT NULL
              OR EXISTS (SELECT 1 FROM telephony_transcript_chunks t WHERE t.account_id=c.account_id AND t.call_id=c.id AND t.is_final=true)
              OR EXISTS (SELECT 1 FROM telephony_recordings r WHERE r.account_id=c.account_id AND r.call_id=c.id AND r.transcript_status='done' AND NULLIF(trim(COALESCE(r.transcript,'')),'') IS NOT NULL))
          ORDER BY c.started_at DESC LIMIT :l"""),{'a':account_id,'d':days,'l':limit}).mappings().all()
    finally: db.close()
    evaluated=0; skipped=0; failed=0
    for row in rows:
        try:
            result=evaluate_call_quality(row['account_id'],row['id'])
            if result.get('status')=='ok': evaluated+=1
            else: skipped+=1
        except Exception: failed+=1
    return {'status':'ok','candidates':len(rows),'evaluated':evaluated,'skipped':skipped,'failed':failed}


def quality_dashboard(account_id: str, days: int=30) -> dict[str,Any]:
    ensure_schema(); days=max(1,min(int(days),365)); db=SessionLocal()
    try:
        summary=dict(db.execute(text("SELECT count(*) reviewed,round(avg(total_score),1) avg_score,count(*) FILTER(WHERE total_score>=80) strong,count(*) FILTER(WHERE total_score<50) needs_attention FROM telephony_call_quality WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval AND status='done'"),{'a':account_id,'d':days}).mappings().first())
        managers=[dict(x) for x in db.execute(text("SELECT q.manager_user_id,CASE WHEN q.manager_user_id IS NOT NULL THEN COALESCE(NULLIF(trim(COALESCE(u.email,'')),''),'Менеджер #'||q.manager_user_id::text) ELSE COALESCE(d.name,'Не назначено') END manager_name,CASE WHEN q.manager_user_id IS NOT NULL THEN 'user' ELSE 'device' END identity_kind,count(*) reviewed,round(avg(q.total_score),1) avg_score,round(avg(q.need_score),1) need_score,round(avg(q.timeline_score),1) timeline_score,round(avg(q.budget_score),1) budget_score,round(avg(q.presentation_score),1) presentation_score,round(avg(q.objection_score),1) objection_score,round(avg(q.next_step_score),1) next_step_score FROM telephony_call_quality q LEFT JOIN telephony_calls c ON c.account_id=q.account_id AND c.id=q.call_id LEFT JOIN telephony_devices d ON d.id=c.device_id LEFT JOIN users u ON u.id=q.manager_user_id WHERE q.account_id=:a AND q.created_at>=now()-(:d||' days')::interval AND q.status='done' GROUP BY q.manager_user_id,u.email,d.name ORDER BY avg(q.total_score) DESC NULLS LAST"),{'a':account_id,'d':days}).mappings().all()]
        recent=[dict(x) for x in db.execute(text("SELECT q.*,c.from_number,c.to_number,c.started_at,c.summary,c.next_action,d.name device_name FROM telephony_call_quality q JOIN telephony_calls c ON c.account_id=q.account_id AND c.id=q.call_id LEFT JOIN telephony_devices d ON d.id=c.device_id WHERE q.account_id=:a AND q.created_at>=now()-(:d||' days')::interval ORDER BY q.updated_at DESC LIMIT 100"),{'a':account_id,'d':days}).mappings().all()]
        attention=[x for x in recent if int(x.get('total_score') or 0)<70]
        skill_keys=[('need_score','Потребность'),('timeline_score','Срок'),('budget_score','Бюджет/цена'),('presentation_score','Презентация'),('objection_score','Возражения'),('next_step_score','Следующий шаг')]
        skill_gaps=[]
        for key,label in skill_keys:
            vals=[int(x.get(key) or 0) for x in recent if x.get(key) is not None]
            if vals:
                avg=round(sum(vals)/len(vals),1); weak=sum(1 for v in vals if v<80)
                skill_gaps.append({'key':key,'label':label,'avg_score':avg,'weak_calls':weak,'reviewed':len(vals)})
        skill_gaps.sort(key=lambda x:(x['avg_score'],-x['weak_calls']))
        repeated_manager_gaps=[dict(x) for x in db.execute(text("""WITH x AS (SELECT q.manager_user_id,u.email,q.need_score,q.timeline_score,q.budget_score,q.presentation_score,q.objection_score,q.next_step_score FROM telephony_call_quality q LEFT JOIN users u ON u.id=q.manager_user_id WHERE q.account_id=:a AND q.manager_user_id IS NOT NULL AND q.created_at>=now()-(:d||' days')::interval AND q.status='done') SELECT manager_user_id,COALESCE(NULLIF(trim(COALESCE(email,'')),''),'Менеджер #'||manager_user_id::text) manager_name,count(*) reviewed,count(*) FILTER(WHERE need_score<50) weak_need,count(*) FILTER(WHERE timeline_score<50) weak_timeline,count(*) FILTER(WHERE budget_score<50) weak_budget,count(*) FILTER(WHERE presentation_score<50) weak_presentation,count(*) FILTER(WHERE objection_score<50) weak_objection,count(*) FILTER(WHERE next_step_score<50) weak_next FROM x GROUP BY manager_user_id,email HAVING count(*)>=2 ORDER BY reviewed DESC"""),{'a':account_id,'d':days}).mappings().all()]
        trend=[dict(x) for x in db.execute(text("""SELECT date_trunc('day',q.created_at)::date AS bucket_day,count(*) AS reviewed,round(avg(q.total_score),1) AS avg_score,count(*) FILTER(WHERE q.total_score<70) AS weak FROM telephony_call_quality q WHERE q.account_id=:a AND q.created_at>=now()-(:d||' days')::interval AND q.status='done' GROUP BY 1 ORDER BY 1"""),{'a':account_id,'d':days}).mappings().all()]
        return {'status':'ok','days':days,'summary':summary,'managers':managers,'recent':recent,'attention':attention,'skill_gaps':skill_gaps,'repeated_manager_gaps':repeated_manager_gaps,'trend':trend}
    finally: db.close()


def telephony_report_settings(account_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        r=db.execute(text("SELECT * FROM telephony_report_settings WHERE account_id=:a"),{'a':account_id}).mappings().first()
        if not r:return {'status':'ok','settings':{'enabled':False,'minute_alerts_enabled':False,'frequency':'daily','send_hour':9,'timezone':'Europe/Moscow','email_to':'','telegram_chat_id':'','telegram_thread_id':None},'exists':False}
        d=dict(r); return {'status':'ok','settings':d,'exists':True}
    finally:db.close()


def save_telephony_report_settings(account_id: str, payload: dict) -> dict[str,Any]:
    ensure_schema(); freq=str(payload.get('frequency') or 'daily'); hour=int(payload.get('send_hour',9)); tz=str(payload.get('timezone') or 'Europe/Moscow')[:80]
    if freq not in {'daily','weekly'}:return {'status':'invalid_frequency'}
    if hour<0 or hour>23:return {'status':'invalid_hour'}
    email_to=str(payload.get('email_to') or '').strip()[:500] or None; tg=str(payload.get('telegram_chat_id') or '').strip()[:120] or None
    thread=payload.get('telegram_thread_id'); thread=int(thread) if str(thread or '').strip().lstrip('-').isdigit() else None
    db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_report_settings(account_id,enabled,minute_alerts_enabled,frequency,send_hour,timezone,email_to,telegram_chat_id,telegram_thread_id)
          VALUES(:a,:e,:ma,:f,:h,:tz,:em,:tg,:th) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,minute_alerts_enabled=excluded.minute_alerts_enabled,frequency=excluded.frequency,send_hour=excluded.send_hour,timezone=excluded.timezone,email_to=excluded.email_to,telegram_chat_id=excluded.telegram_chat_id,telegram_thread_id=excluded.telegram_thread_id,updated_at=now()"""),{'a':account_id,'e':bool(payload.get('enabled')),'ma':bool(payload.get('minute_alerts_enabled')),'f':freq,'h':hour,'tz':tz,'em':email_to,'tg':tg,'th':thread});db.commit()
    finally:db.close()
    return telephony_report_settings(account_id)


def build_rop_phone_report(account_id: str, days: int=1) -> dict[str,Any]:
    days=max(1,min(int(days),30)); a=telephony_analytics(account_id,days); q=quality_dashboard(account_id,days); m=a.get('summary') or {}; qs=q.get('summary') or {}
    lines=[f'BORIS Phone · отчёт РОПа за {days} дн.', '', f"Звонки: {m.get('total',0)} · принято {m.get('answered',0)} · пропущено {m.get('missed',0)}", f"Просрочено SLA перезвона: {m.get('callback_overdue',0)}", f"Разговор: {round(float(m.get('talk_seconds') or 0)/60)} мин · себестоимость {float(m.get('cost_rub') or 0):.2f} ₽", '', f"Проверено разговоров: {qs.get('reviewed',0)} · средний балл {qs.get('avg_score') or '—'}/100", f"Требуют внимания РОПа: {len(q.get('attention') or [])} · ниже 50: {qs.get('needs_attention',0)}"]
    gaps=q.get('skill_gaps') or []
    if gaps:
        lines+=['','Слабые навыки:']+[f"• {x['label']}: {x['avg_score']}/100 · слабых звонков {x['weak_calls']}" for x in gaps[:4]]
    managers=q.get('managers') or []
    if managers:
        lines+=['','Менеджеры:']+[f"• {x.get('manager_name') or 'Менеджер'}: {x.get('avg_score') or 0}/100 · проверено {x.get('reviewed') or 0}" for x in managers[:10]]
    cs=coaching_summary(account_id,days).get('summary') or {}
    lines+=['',f"Обучение: назначено {cs.get('open',0)} · в работе {cs.get('training',0)} · завершено {cs.get('completed',0)} · улучшили навык {cs.get('improved',0)}"]
    if cs.get('avg_improvement') is not None: lines.append(f"Средняя динамика после тренировки: {cs.get('avg_improvement')} п.")
    lines+=['','Отчёт сформирован BORIS автоматически по реальным данным телефонии.']
    return {'status':'ok','days':days,'text':'\n'.join(lines),'analytics':a,'quality':q}


def deliver_rop_phone_report(account_id: str, report_key: str|None=None, days: int=1, channels: list[str]|None=None) -> dict[str,Any]:
    cfg=telephony_report_settings(account_id).get('settings') or {}; rep=build_rop_phone_report(account_id,days); key=report_key or f"manual:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"; wanted=channels or ['email','telegram']; results={}
    for channel in wanted:
        recipient=(cfg.get('email_to') if channel=='email' else cfg.get('telegram_chat_id')) or ''
        if not recipient: results[channel]={'status':'not_configured'}; continue
        # External report delivery has no provider-side idempotency contract.
        # Claim this exact report/channel once before I/O. A surviving `sending`,
        # `failed` or `ambiguous` row is terminal for automatic delivery: after a
        # crash/transport uncertainty a blind replay could duplicate a client message.
        db=SessionLocal(); claimed=False
        try:
            claimed=bool(db.execute(text("""INSERT INTO telephony_report_deliveries(account_id,report_key,channel,recipient,status)
              VALUES(:a,:k,:c,:r,'sending') ON CONFLICT(account_id,report_key,channel) DO NOTHING RETURNING id"""),
              {'a':account_id,'k':key,'c':channel,'r':recipient}).scalar())
            if not claimed:
                existing=db.execute(text("SELECT status,error FROM telephony_report_deliveries WHERE account_id=:a AND report_key=:k AND channel=:c"),{'a':account_id,'k':key,'c':channel}).mappings().first()
                db.commit()
                status=str((existing or {}).get('status') or 'ambiguous')
                results[channel]={'status':status,'idempotent':True,'replayed':False,'error':(existing or {}).get('error')}
                continue
            db.commit()
        finally:db.close()
        ok=False; err=''; ambiguous=False
        try:
            if channel=='email':
                from app.services.email_service import send_email
                ok,reason,_=send_email(recipient,f'BORIS Phone · отчёт РОПа · {days} дн.',rep['text']); err='' if ok else 'email_delivery_failed'
            else:
                from app.telegram_bot import send_telegram_message
                rv=send_telegram_message(recipient,rep['text'],thread_id=cfg.get('telegram_thread_id') or None); ok=rv is not False; err='' if ok else 'telegram_failed'
        except Exception as exc:
            err=type(exc).__name__;ok=False;ambiguous=True
        final_status='sent' if ok else ('ambiguous' if ambiguous else 'failed')
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_report_deliveries SET status=:s,error=:e,sent_at=CASE WHEN :ok THEN now() ELSE sent_at END WHERE account_id=:a AND report_key=:k AND channel=:c AND status='sending'"),{'s':final_status,'e':err or None,'ok':ok,'a':account_id,'k':key,'c':channel});db.commit()
        finally:db.close()
        results[channel]={'status':final_status,'error':err}
    return {'status':'ok','report_key':key,'results':results,'preview':rep['text']}

def rop_report_guardian(now_utc: datetime|None=None) -> dict[str,Any]:
    ensure_schema(); now_utc=now_utc or datetime.now(timezone.utc); db=SessionLocal()
    try: rows=[dict(x) for x in db.execute(text("SELECT * FROM telephony_report_settings WHERE enabled=true AND (email_to IS NOT NULL OR telegram_chat_id IS NOT NULL)")).mappings().all()]
    finally:db.close()
    sent=0; due=0; failed=0
    from zoneinfo import ZoneInfo
    for cfg in rows:
        try:
            local=now_utc.astimezone(ZoneInfo(cfg.get('timezone') or 'Europe/Moscow')); freq=cfg.get('frequency') or 'daily'
            if local.hour!=int(cfg.get('send_hour') or 9):continue
            if freq=='weekly' and local.weekday()!=0:continue
            key=f"{freq}:{local.date().isoformat()}"; due+=1
            r=deliver_rop_phone_report(cfg['account_id'],key,7 if freq=='weekly' else 1)
            statuses=[x.get('status') for x in r['results'].values()]
            if 'sent' in statuses:sent+=1
            if any(x in {'failed','ambiguous','sending'} for x in statuses):failed+=1
        except Exception:failed+=1
    return {'status':'ok','configured':len(rows),'due':due,'sent':sent,'failed':failed}


_COACHING_SKILLS={
 'need_score':('Потребность','Отработать выявление задачи клиента: задавать уточняющие вопросы до презентации решения.'),
 'timeline_score':('Срок','Научиться фиксировать срок/дату принятия решения и следующий временной ориентир.'),
 'budget_score':('Бюджет/цена','Корректно выяснять бюджет или рамку цены без давления и преждевременной скидки.'),
 'presentation_score':('Презентация','Связывать предложение с выявленной потребностью, а не перечислять свойства.'),
 'objection_score':('Возражения','Уточнять причину возражения, отвечать по сути и проверять, снято ли оно.'),
 'next_step_score':('Следующий шаг','Каждый содержательный звонок завершать конкретным следующим действием и сроком.'),
}

def coaching_guardian(account_id: str|None=None, days: int=14, limit: int=100) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT q.*,c.summary,c.next_action FROM telephony_call_quality q JOIN telephony_calls c ON c.account_id=q.account_id AND c.id=q.call_id WHERE (:a IS NULL OR q.account_id=:a) AND q.status='done' AND q.created_at>=now()-(:d||' days')::interval ORDER BY q.updated_at DESC LIMIT :l"""),{'a':account_id,'d':max(1,min(days,30)),'l':max(1,min(limit,500))}).mappings().all()]
        created=0
        for q in rows:
            for key,(label,goal) in _COACHING_SKILLS.items():
                score=q.get(key)
                if score is None or int(score)>=50:continue
                scenario=f"Тренировка по навыку «{label}». Исходный звонок показал {int(score)}/100. {goal} Клиент должен вести себя реалистично; задача менеджера — исправить именно этот навык."
                r=db.execute(text("""INSERT INTO telephony_coaching_plans(account_id,manager_user_id,skill_key,skill_label,source_call_id,source_quality_id,baseline_score,training_goal,recommended_scenario) VALUES(:a,:u,:k,:l,:c,:q,:b,:g,:s) ON CONFLICT(account_id,source_call_id,skill_key) DO NOTHING"""),{'a':q['account_id'],'u':q.get('manager_user_id'),'k':key,'l':label,'c':q['call_id'],'q':q['id'],'b':int(score),'g':goal,'s':scenario})
                created+=int(r.rowcount or 0)
        db.commit();return {'status':'ok','quality_rows':len(rows),'created':created}
    finally:db.close()

def coaching_followup_guardian(account_id: str|None=None, days: int=30, limit: int=100) -> dict[str,Any]:
    """Close training plans only when a later real call has a measured score for the same manager/skill."""
    ensure_schema(); db=SessionLocal(); completed=0
    try:
        plans=[dict(x) for x in db.execute(text("""SELECT * FROM telephony_coaching_plans WHERE (:a IS NULL OR account_id=:a) AND status IN ('open','training') AND created_at>=now()-(:d||' days')::interval ORDER BY created_at LIMIT :l"""),{'a':account_id,'d':max(1,min(days,90)),'l':max(1,min(limit,500))}).mappings().all()]
        allowed=set(_COACHING_SKILLS)
        for p in plans:
            key=p.get('skill_key')
            if key not in allowed:continue
            # dynamic identifier is selected only from fixed allowlist above.
            row=db.execute(text(f"""SELECT q.{key} score,q.call_id,q.created_at FROM telephony_call_quality q WHERE q.account_id=:a AND q.created_at>:created AND q.call_id<>:source AND q.status='done' AND (:u IS NULL OR q.manager_user_id=:u) AND q.{key} IS NOT NULL ORDER BY q.created_at ASC LIMIT 1"""),{'a':p['account_id'],'created':p['created_at'],'source':p.get('source_call_id'),'u':p.get('manager_user_id')}).mappings().first()
            if not row:continue
            score=max(0,min(int(row['score']),100))
            db.execute(text("UPDATE telephony_coaching_plans SET followup_score=:s,improvement=:s-COALESCE(baseline_score,0),status='completed',completed_at=now(),updated_at=now() WHERE id=:i"),{'s':score,'i':p['id']});completed+=1
        db.commit();return {'status':'ok','plans':len(plans),'completed':completed}
    finally:db.close()


def coaching_plans(account_id: str, status: str='open', limit: int=100) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("SELECT p.*,CASE WHEN p.manager_user_id IS NOT NULL THEN COALESCE(NULLIF(trim(COALESCE(u.email,'')),''),'Менеджер #'||p.manager_user_id::text) ELSE COALESCE(d.name,'Рабочее место') END manager_name,CASE WHEN p.manager_user_id IS NOT NULL THEN 'user' ELSE 'device' END identity_kind FROM telephony_coaching_plans p LEFT JOIN telephony_calls c ON c.account_id=p.account_id AND c.id=p.source_call_id LEFT JOIN telephony_devices d ON d.id=c.device_id LEFT JOIN users u ON u.id=p.manager_user_id WHERE p.account_id=:a AND (:s='all' OR p.status=:s) ORDER BY p.created_at DESC LIMIT :l"),{'a':account_id,'s':status,'l':max(1,min(limit,300))}).mappings().all()]
        return {'status':'ok','items':rows}
    finally:db.close()

def coaching_summary(account_id: str, days: int=30) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        row=dict(db.execute(text("""SELECT count(*) FILTER(WHERE status='open') open,count(*) FILTER(WHERE status='training') training,count(*) FILTER(WHERE status='completed') completed,round(avg(improvement) FILTER(WHERE status='completed'),1) avg_improvement,count(*) FILTER(WHERE status='completed' AND improvement>0) improved FROM telephony_coaching_plans WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':max(1,min(days,365))}).mappings().first())
        return {'status':'ok','summary':row}
    finally:db.close()


def link_coaching_sparring(account_id: str, plan_id: int, session_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:
        r=db.execute(text("UPDATE telephony_coaching_plans SET sparring_session_id=:sid,status='training',updated_at=now() WHERE account_id=:a AND id=:i RETURNING *"),{'sid':session_id[:120],'a':account_id,'i':plan_id}).mappings().first();db.commit();return {'status':'ok','plan':dict(r)} if r else {'status':'not_found'}
    finally:db.close()

def complete_coaching_plan(account_id: str, plan_id: int, followup_score: int) -> dict[str,Any]:
    ensure_schema(); score=max(0,min(int(followup_score),100)); db=SessionLocal()
    try:
        r=db.execute(text("UPDATE telephony_coaching_plans SET followup_score=:s,improvement=:s-COALESCE(baseline_score,0),status='completed',completed_at=now(),updated_at=now() WHERE account_id=:a AND id=:i RETURNING *"),{'s':score,'a':account_id,'i':plan_id}).mappings().first();db.commit();return {'status':'ok','plan':dict(r)} if r else {'status':'not_found'}
    finally:db.close()


def copilot_settings(account_id: str) -> dict[str,Any]:
    ensure_schema(); db=SessionLocal()
    try:r=db.execute(text("SELECT * FROM telephony_copilot_settings WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally:db.close()
    return {'status':'ok','settings':dict(r) if r else {'account_id':account_id,'enabled':True,'human_call_only':True,'min_interval_seconds':12,'max_suggestions_per_call':12,'allow_price_suggestions':False,'allow_commitments':False}}


def save_copilot_settings(account_id: str,payload:dict) -> dict[str,Any]:
    interval=max(8,min(int(payload.get('min_interval_seconds') or 12),60)); maximum=max(1,min(int(payload.get('max_suggestions_per_call') or 12),30)); db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_copilot_settings(account_id,enabled,human_call_only,min_interval_seconds,max_suggestions_per_call,allow_price_suggestions,allow_commitments) VALUES(:a,:e,true,:i,:m,:p,:c) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,human_call_only=true,min_interval_seconds=excluded.min_interval_seconds,max_suggestions_per_call=excluded.max_suggestions_per_call,allow_price_suggestions=excluded.allow_price_suggestions,allow_commitments=excluded.allow_commitments,updated_at=now()"""),{'a':account_id,'e':bool(payload.get('enabled',True)),'i':interval,'m':maximum,'p':bool(payload.get('allow_price_suggestions',False)),'c':bool(payload.get('allow_commitments',False))});db.commit()
    finally:db.close()
    return copilot_settings(account_id)


def copilot_guard(account_id:str,call_id:str) -> dict[str,Any]:
    """Hard boundary: copilot assists a human; it never speaks, answers or controls a call."""
    cfg=copilot_settings(account_id)['settings']; db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,state,user_id,device_id,metadata_json FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'not_found','allowed':False}
        chunks=db.execute(text("SELECT count(*) FROM telephony_transcript_chunks WHERE account_id=:a AND call_id=:c AND is_final=true"),{'a':account_id,'c':call_id}).scalar() or 0
        count=db.execute(text("SELECT count(*) FROM telephony_ai_suggestions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).scalar() or 0
    finally:db.close()
    if not cfg.get('enabled'):return {'status':'disabled','allowed':False}
    if call.get('state') not in {'answered','active','on_hold'}:return {'status':'call_not_active','allowed':False}
    if call.get('user_id') is None:return {'status':'human_manager_not_bound','allowed':False}
    if chunks<1:return {'status':'waiting_real_transcript','allowed':False}
    if count>=int(cfg.get('max_suggestions_per_call') or 12):return {'status':'limit_reached','allowed':False}
    return {'status':'ok','allowed':True,'human_manager_user_id':call.get('user_id'),'final_chunks':int(chunks),'suggestions':int(count),'policy':{'display_only':True,'may_speak':False,'may_answer':False,'may_control_call':False,'allow_price_suggestions':bool(cfg.get('allow_price_suggestions')),'allow_commitments':bool(cfg.get('allow_commitments'))}}


def _copilot_account_facts(account_id:str,limit:int=80)->list[dict]:
    db=SessionLocal()
    try:
        cols={r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='client_facts'")).all()}
        if not {'account_id','name','value','status'}.issubset(cols):return []
        rows=db.execute(text("SELECT id,category,name,value,status,confidence FROM client_facts WHERE account_id=:a AND status NOT IN ('rejected','conflict') ORDER BY (status='confirmed') DESC,confidence DESC NULLS LAST,id DESC LIMIT :l"),{'a':account_id,'l':max(1,min(limit,200))}).mappings().all()
        return [dict(x) for x in rows]
    except Exception:return []
    finally:db.close()


def realtime_copilot_tick(account_id:str|None=None,limit:int=20)->dict[str,Any]:
    """Human-call copilot. It observes final transcript chunks and creates display-only grounded prompts."""
    ensure_schema(); db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT c.account_id,c.id call_id,c.user_id,max(t.id) chunk_id,max(t.created_at) chunk_at FROM telephony_calls c JOIN telephony_transcript_chunks t ON t.account_id=c.account_id AND t.call_id=c.id AND t.is_final=true WHERE (:a IS NULL OR c.account_id=:a) AND c.state IN ('answered','active','on_hold') AND c.user_id IS NOT NULL GROUP BY c.account_id,c.id,c.user_id ORDER BY max(t.created_at) DESC LIMIT :l"""),{'a':account_id,'l':max(1,min(limit,100))}).mappings().all()]
    finally:db.close()
    created=0;skipped=0
    for row in rows:
        guard=copilot_guard(row['account_id'],row['call_id'])
        if not guard.get('allowed'):skipped+=1;continue
        ctx=realtime_call_context(row['account_id'],row['call_id'],12); chunks=ctx.get('chunks') or []
        if not chunks:skipped+=1;continue
        latest=chunks[-1]; txt=str(latest.get('text_content') or '').strip(); speaker=str(latest.get('speaker') or '').lower()
        # Only react to client speech. Unknown speaker is skipped to avoid coaching on manager's own words.
        if speaker not in {'client','customer','клиент'}:skipped+=1;continue
        idem=f"copilot:v1:{row['call_id']}:{latest['id']}"
        low=txt.lower(); suggestion='';kind='next_best_question'; evidence=[{'type':'transcript','chunk_id':latest['id'],'quote':txt[:220]}]
        facts=_copilot_account_facts(row['account_id'])
        # Safe conversational guidance; company-specific answers only when confirmed facts can be cited.
        if any(x in low for x in ['дорого','цена','стоимость','сколько стоит','бюджет']):
            pricefacts=[f for f in facts if any(k in (str(f.get('name',''))+' '+str(f.get('value',''))).lower() for k in ['цен','стоим','руб','₽','бюджет'])]
            if guard['policy'].get('allow_price_suggestions') and pricefacts:
                f=pricefacts[0];suggestion=f"Уточните объём/параметры. Подтверждённый факт компании: {f.get('name')}: {f.get('value')}"[:900];kind='grounded_price';evidence.append({'type':'client_fact','fact_id':f.get('id'),'status':f.get('status')})
            else:suggestion='Не называйте неподтверждённую цену. Уточните объём, параметры задачи и ориентир клиента по бюджету.'
        elif any(x in low for x in ['когда','срок','срочно','сегодня','завтра']): suggestion='Уточните точную дату, к которой клиенту нужен результат. Не обещайте срок, которого нет в подтверждённых данных.'
        elif any(x in low for x in ['подумаю','перезвоню','позже','не сейчас']): suggestion='Уточните, что именно мешает принять решение сейчас, и договоритесь о конкретном следующем контакте.';kind='objection'
        elif '?' in txt: suggestion='Ответьте на вопрос клиента только известными фактами, затем задайте один короткий уточняющий вопрос по его задаче.'
        else: suggestion='Уточните потребность: что именно нужно клиенту, к какому сроку и какой результат для него важен.'
        r=add_realtime_ai_suggestion(row['account_id'],row['call_id'],suggestion,kind=kind,trigger_chunk_id=latest['id'],evidence=evidence,model='boris-grounded-copilot-v1',idempotency_key=idem)
        if r.get('status')=='ok':created+=1
        else:skipped+=1
    return {'status':'ok','calls':len(rows),'created':created,'skipped':skipped}


def afterhours_settings(account_id:str)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:r=db.execute(text("SELECT * FROM telephony_afterhours_settings WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally:db.close()
    defaults={'account_id':account_id,'enabled':False,'autoanswer_mode':'afterhours','no_answer_seconds':25,'timezone':'Europe/Moscow','work_days':[0,1,2,3,4],'work_start':'09:00','work_end':'18:00','callback_hour':9,'greeting':'Здравствуйте. Вам отвечает виртуальный помощник BORIS. Чем могу помочь?','collect_phone':True,'collect_topic':True,'create_crm_task':True,'voicemail_text':'Если сейчас не удастся помочь сразу, я запишу ваш вопрос и передам менеджеру.'}
    if not r:return {'status':'ok','settings':defaults}
    out={**defaults,**dict(r)}
    # Legacy rows with enabled=true and no meaningful mode remain after-hours only.
    if out.get('autoanswer_mode') not in {'off','afterhours','no_answer','always'}:out['autoanswer_mode']='afterhours'
    return {'status':'ok','settings':out}


def save_afterhours_settings(account_id:str,payload:dict)->dict[str,Any]:
    from zoneinfo import ZoneInfo
    current=afterhours_settings(account_id)['settings']; merged={**current,**(payload or {})}
    tz=str(merged.get('timezone') or 'Europe/Moscow');ZoneInfo(tz)
    days=merged.get('work_days',[0,1,2,3,4]); days=sorted({int(x) for x in days if 0<=int(x)<=6}); assert days
    def hm(v):
        h,m=[int(x) for x in str(v).split(':',1)]; assert 0<=h<24 and 0<=m<60; return f'{h:02d}:{m:02d}'
    st,en=hm(merged.get('work_start','09:00')),hm(merged.get('work_end','18:00'))
    ch=max(0,min(int(merged.get('callback_hour',9)),23));g=str(merged.get('greeting') or '')[:1000]
    mode=str(merged.get('autoanswer_mode') or 'off').strip().lower(); assert mode in {'off','afterhours','no_answer','always'}
    delay=max(5,min(int(merged.get('no_answer_seconds',25)),120)); voicemail=str(merged.get('voicemail_text') or '')[:1200]
    enabled=bool(merged.get('enabled')) and mode!='off'
    db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_afterhours_settings(account_id,enabled,autoanswer_mode,no_answer_seconds,timezone,work_days,work_start,work_end,callback_hour,greeting,collect_phone,collect_topic,create_crm_task,voicemail_text)
          VALUES(:a,:e,:mode,:delay,:tz,CAST(:d AS jsonb),:s,:n,:h,:g,:phone,:topic,:crm,:vm)
          ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,autoanswer_mode=excluded.autoanswer_mode,no_answer_seconds=excluded.no_answer_seconds,timezone=excluded.timezone,work_days=excluded.work_days,work_start=excluded.work_start,work_end=excluded.work_end,callback_hour=excluded.callback_hour,greeting=excluded.greeting,collect_phone=excluded.collect_phone,collect_topic=excluded.collect_topic,create_crm_task=excluded.create_crm_task,voicemail_text=excluded.voicemail_text,updated_at=now()"""),
          {'a':account_id,'e':enabled,'mode':mode,'delay':delay,'tz':tz,'d':json.dumps(days),'s':st,'n':en,'h':ch,'g':g,'phone':bool(merged.get('collect_phone',True)),'topic':bool(merged.get('collect_topic',True)),'crm':bool(merged.get('create_crm_task',True)),'vm':voicemail});db.commit()
    finally:db.close()
    return afterhours_settings(account_id)


def _next_work_time(cfg:dict,at:datetime|None=None)->datetime:
    from zoneinfo import ZoneInfo
    tz=ZoneInfo(cfg.get('timezone') or 'Europe/Moscow');local=(at or utcnow()).astimezone(tz);days={int(x) for x in (cfg.get('work_days') or [0,1,2,3,4])};sh,sm=[int(x) for x in str(cfg.get('work_start') or '09:00').split(':')];eh,em=[int(x) for x in str(cfg.get('work_end') or '18:00').split(':')];cbh=max(0,min(int(cfg.get('callback_hour',sh)),23))
    # callback hour is honored only inside the configured work window; otherwise use work start.
    callback_minutes=cbh*60;start_minutes=sh*60+sm;end_minutes=eh*60+em;ch,cm=(cbh,0) if start_minutes<=callback_minutes<end_minutes else (sh,sm)
    for off in range(0,15):
        d=(local+timedelta(days=off)).date()
        if d.weekday() not in days:continue
        candidate=datetime(d.year,d.month,d.day,ch,cm,tzinfo=tz)
        if candidate>local:return candidate.astimezone(timezone.utc)
    return (local+timedelta(days=1)).astimezone(timezone.utc)


def afterhours_status(account_id:str,at:datetime|None=None)->dict[str,Any]:
    cfg=afterhours_settings(account_id)['settings'];sch={'timezone':cfg['timezone'],'days':cfg['work_days'],'start':cfg['work_start'],'end':cfg['work_end']}; working=_route_schedule_allows(sch,at)
    mode=str(cfg.get('autoanswer_mode') or 'afterhours'); enabled=bool(cfg.get('enabled')) and mode!='off'
    return {'status':'ok','enabled':enabled,'mode':mode,'working_now':working,'afterhours':enabled and not working,'next_work_at':_next_work_time(cfg,at).isoformat() if not working else None,'settings':cfg}


def autoanswer_opening(account_id:str)->dict[str,Any]:
    """Build one first utterance so AI disclosure and owner greeting are never spoken twice."""
    a=afterhours_settings(account_id)['settings'];v=voice_agent_settings(account_id)['settings']
    greeting=str(a.get('greeting') or '').strip(); disclosure=str(v.get('disclosure_text') or 'Здравствуйте! Вам отвечает виртуальный помощник BORIS.').strip()
    low=greeting.casefold();already=any(x in low for x in ['виртуальн','искусственн',' ai ','ии-помощ','ии помощ','нейросет'])
    required=bool(v.get('disclosure_required',True))
    if required and not already:
        text=(disclosure+' '+greeting).strip()
    else:text=greeting or (disclosure if required else 'Здравствуйте. Чем могу помочь?')
    return {'status':'ok','text':text[:1500],'disclosure_required':required,'disclosure_embedded':bool(required and (already or disclosure in text)),'greeting':greeting,'voicemail_text':str(a.get('voicemail_text') or '')[:1200]}

def autoanswer_decision(account_id:str,ring_seconds:int=0,human_available:bool=True,at:datetime|None=None)->dict[str,Any]:
    """Provider-neutral decision only. It never starts audio or steals a human-assigned call."""
    st=afterhours_status(account_id,at);cfg=st['settings'];mode=str(cfg.get('autoanswer_mode') or 'off');delay=int(cfg.get('no_answer_seconds') or 25)
    reason='disabled';should=False
    if not st.get('enabled') or mode=='off': reason='disabled'
    elif mode=='always': should=True;reason='always'
    elif mode=='afterhours': should=not st.get('working_now');reason='afterhours' if should else 'working_hours'
    elif mode=='no_answer': should=int(ring_seconds or 0)>=delay;reason='no_answer_timeout' if should else 'waiting_human'
    va=voice_agent_settings(account_id)['settings']
    engine='voice_agent' if va.get('enabled') else 'fallback_ivr';media=_provider_media_evidence(account_id)
    opening=autoanswer_opening(account_id) if should else None
    return {'status':'ok','should_answer':should,'reason':reason,'mode':mode,'ring_seconds':max(0,int(ring_seconds or 0)),'no_answer_seconds':delay,'human_available':bool(human_available),'engine':engine,'engine_ready':bool(media.get('ready')),'engine_blocker':None if media.get('ready') else media.get('reason'),'media_evidence':media,'greeting':cfg.get('greeting') if should else None,'opening_text':opening.get('text') if opening else None,'opening':opening,'policy':{'collect_phone':bool(cfg.get('collect_phone',True)),'collect_topic':bool(cfg.get('collect_topic',True)),'create_crm_task':bool(cfg.get('create_crm_task',True)),'never_take_human_assigned_call':True}}

def request_autoanswer_takeover(account_id:str,call_id:str,ring_seconds:int=0,reason_hint:str|None=None)->dict[str,Any]:
    """Request AI/fallback ownership without pretending the provider has answered the media call."""
    decision=autoanswer_decision(account_id,ring_seconds,True)
    if not decision.get('should_answer'):return {'status':'not_requested','decision':decision}
    if not decision.get('engine_ready'):
        return {'status':'blocked','reason':'provider_takeover_unverified','engine_blocker':decision.get('engine_blocker'),'decision':decision}
    db=SessionLocal()
    try:
        call=db.execute(text("SELECT id,state,user_id,device_id,metadata_json FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'not_found','decision':decision}
        if call.get('user_id') or call.get('device_id'):return {'status':'blocked','reason':'human_call_boundary','decision':decision}
        if str(call.get('state') or '')!='ringing':return {'status':'blocked','reason':'call_not_ringing','decision':decision}
        meta=dict(call.get('metadata_json') or {})
        if bool(meta.get('autoanswer_requested')):return {'status':'already_requested','decision':decision,'engine':str(call.get('agent_mode') or decision.get('engine') or '')}
        meta['autoanswer_requested']=True;meta['autoanswer_reason']=reason_hint or decision.get('reason');meta['autoanswer_requested_at']=utcnow().isoformat()
        db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=COALESCE(ended_at,now()),updated_at=now() WHERE account_id=:a AND call_id=:c AND status='ringing'"),{'a':account_id,'c':call_id})
        db.execute(text("UPDATE telephony_calls SET metadata_json=CAST(:m AS jsonb),agent_mode=:mode,updated_at=now() WHERE account_id=:a AND id=:c"),{'m':json.dumps(meta,ensure_ascii=False),'mode':decision.get('engine') or 'fallback_ivr','a':account_id,'c':call_id});db.commit()
    finally:db.close()
    try:cancel_call_pushes(account_id,call_id)
    except Exception:pass
    started=None
    if decision.get('engine')=='voice_agent':
        mode='afterhours_agent' if afterhours_status(account_id).get('afterhours') else 'voice_agent'
        started=voice_agent_start(account_id,call_id,mode)
        if started.get('status')!='ok':
            db=SessionLocal()
            try:db.execute(text("UPDATE telephony_calls SET agent_mode='fallback_ivr',updated_at=now() WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id});db.commit()
            finally:db.close()
    _audit(account_id,'autoanswer.requested','ok',call_id=call_id,metadata={'mode':decision.get('mode'),'reason':decision.get('reason'),'engine':decision.get('engine'),'ring_seconds':ring_seconds})
    return {'status':'requested','decision':decision,'start':started,'media':'pending_provider_adapter'}

def autoanswer_guardian(account_id:str|None=None,limit:int=100)->dict[str,Any]:
    """Take over no-answer calls at their exact configured timeout, independently of route sweeps."""
    ensure_schema();db=SessionLocal();params={'l':max(1,min(int(limit),500))};extra=''
    if account_id:extra=' AND c.account_id=:a';params['a']=account_id
    try:
        q="""SELECT c.account_id,c.id,greatest(0,extract(epoch from (now()-coalesce(c.ringing_at,c.started_at)))::int) ring_seconds
          FROM telephony_calls c JOIN telephony_afterhours_settings s ON s.account_id=c.account_id
          WHERE c.direction='inbound' AND c.state='ringing' AND c.user_id IS NULL AND c.device_id IS NULL
            AND s.enabled=true AND s.autoanswer_mode='no_answer'
            AND greatest(0,extract(epoch from (now()-coalesce(c.ringing_at,c.started_at)))::int)>=s.no_answer_seconds
            AND COALESCE((c.metadata_json->>'autoanswer_requested')::boolean,false)=false"""+extra+" ORDER BY c.ringing_at NULLS LAST LIMIT :l"
        rows=db.execute(text(q),params).mappings().all()
    finally:db.close()
    requested=blocked=failed=0;errors=[]
    for r in rows:
        try:
            x=request_autoanswer_takeover(r['account_id'],r['id'],int(r.get('ring_seconds') or 0),'no_answer_guardian')
            if x.get('status')=='requested':requested+=1
            else:blocked+=1
        except Exception as exc:failed+=1;errors.append({'call_id':r['id'],'error_code':type(exc).__name__[:120]})
    return {'status':'ok','picked':len(rows),'requested':requested,'blocked':blocked,'failed':failed,'errors':errors[:10]}


def autoanswer_analytics(account_id:str,days:int=30)->dict[str,Any]:
    """Owner-facing factual autoanswer funnel. No synthetic revenue/cost claims."""
    ensure_schema();days=max(1,min(int(days),365));db=SessionLocal()
    try:
        summary=dict(db.execute(text("""SELECT
          count(*) FILTER (WHERE COALESCE((metadata_json->>'autoanswer_requested')::boolean,false)) AS requested,
          count(*) FILTER (WHERE agent_mode IN ('voice_agent','afterhours_agent','fallback_ivr')) AS ai_handled,
          count(*) FILTER (WHERE agent_mode IN ('voice_agent','afterhours_agent','fallback_ivr') AND state IN ('ended','transferred')) AS completed_calls,
          count(*) FILTER (WHERE agent_mode IN ('voice_agent','afterhours_agent','fallback_ivr') AND crm_contact_id IS NOT NULL) AS crm_contacts,
          count(*) FILTER (WHERE agent_mode IN ('voice_agent','afterhours_agent','fallback_ivr') AND crm_deal_id IS NOT NULL) AS crm_deals
          FROM telephony_calls WHERE account_id=:a AND direction='inbound' AND started_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':days}).mappings().one())
        sessions=dict(db.execute(text("""SELECT count(*) AS sessions,
          count(*) FILTER(WHERE confirmed_phone IS NOT NULL) AS confirmed_phones,
          count(*) FILTER(WHERE topic IS NOT NULL AND trim(topic)<>'') AS topics,
          count(*) FILTER(WHERE crm_task_id IS NOT NULL) AS crm_tasks,
          count(*) FILTER(WHERE state='completed') AS completed_sessions
          FROM telephony_afterhours_sessions WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':days}).mappings().one())
        capture=dict(db.execute(text("""WITH x AS (
          SELECT call_id,(confirmed_phone IS NOT NULL) confirmed_phone,(topic IS NOT NULL AND trim(topic)<>'') has_topic,(crm_task_id IS NOT NULL) has_task
            FROM telephony_afterhours_sessions WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval
          UNION ALL
          SELECT call_id,callback_phone_confirmed,(topic IS NOT NULL AND trim(topic)<>'') has_topic,false has_task
            FROM telephony_voice_agent_sessions WHERE account_id=:a AND started_at>=now()-(:d||' days')::interval
          UNION ALL
          SELECT call_id,false,false,(crm_task_id IS NOT NULL) has_task
            FROM telephony_voice_agent_postcall WHERE account_id=:a AND finalized_at>=now()-(:d||' days')::interval
        ),g AS (SELECT call_id,bool_or(confirmed_phone) confirmed_phone,bool_or(has_topic) has_topic,bool_or(has_task) has_task FROM x GROUP BY call_id)
        SELECT count(*) calls,count(*) FILTER(WHERE confirmed_phone) confirmed_phones,count(*) FILTER(WHERE has_topic) topics,count(*) FILTER(WHERE has_task) crm_tasks FROM g"""),{'a':account_id,'d':days}).mappings().one())
        reasons=[dict(x) for x in db.execute(text("""SELECT COALESCE(metadata_json->>'autoanswer_reason','unknown') reason,count(*) calls
          FROM telephony_calls WHERE account_id=:a AND direction='inbound' AND started_at>=now()-(:d||' days')::interval
          AND COALESCE((metadata_json->>'autoanswer_requested')::boolean,false)=true GROUP BY 1 ORDER BY 2 DESC"""),{'a':account_id,'d':days}).mappings().all()]
        recent=[dict(x) for x in db.execute(text("""SELECT c.id,c.started_at,c.state,c.agent_mode,c.from_number,c.crm_contact_id,c.crm_deal_id,
          c.metadata_json->>'autoanswer_reason' autoanswer_reason,s.state session_state,s.confirmed_phone,s.topic,s.crm_task_id
          FROM telephony_calls c LEFT JOIN telephony_afterhours_sessions s ON s.account_id=c.account_id AND s.call_id=c.id
          WHERE c.account_id=:a AND c.direction='inbound' AND c.started_at>=now()-(:d||' days')::interval
          AND COALESCE((c.metadata_json->>'autoanswer_requested')::boolean,false)=true ORDER BY c.started_at DESC LIMIT 50"""),{'a':account_id,'d':days}).mappings().all()]
    finally:db.close()
    return {'status':'ok','days':days,'summary':summary,'sessions':sessions,'capture':capture,'reasons':reasons,'recent':recent,'truth':'Counts are based only on persisted telephony/CRM evidence.'}


def autoanswer_preview(account_id:str)->dict[str,Any]:
    """Read-only explanation of the exact owner policy; it never creates a fake call/session."""
    cfg=afterhours_settings(account_id)['settings'];voice=voice_agent_settings(account_id)['settings'];opening=autoanswer_opening(account_id)
    steps=[{'key':'opening','title':'Приветствие','say':opening.get('text'),'required':True}]
    if voice.get('enabled'):
        steps.append({'key':'dialog','title':'Уточнить запрос','say':'BORIS ведёт диалог только по подтверждённым данным компании и задаёт уточняющие вопросы.','required':True})
        if voice.get('handoff_on_unknown',True):steps.append({'key':'unknown','title':'Неизвестный вопрос','say':'Не придумывать ответ — передать менеджеру.','required':True})
    else:
        steps.append({'key':'dialog','title':'Сценарный режим','say':'Без свободной генерации: только безопасный сценарий сбора данных.','required':True})
    if cfg.get('collect_phone',True):steps.extend([
      {'key':'phone','title':'Телефон','say':'Попросить номер для обратного звонка.','required':True},
      {'key':'confirm_phone','title':'Подтверждение номера','say':'Повторить номер по цифрам и получить явное подтверждение.','required':True}])
    if cfg.get('collect_topic',True):steps.append({'key':'topic','title':'Тема звонка','say':'Кратко зафиксировать, по какому вопросу звонил клиент.','required':True})
    if cfg.get('create_crm_task',True):steps.append({'key':'crm','title':'CRM','say':'Создать менеджеру задачу на перезвон с подтверждёнными данными клиента.','required':True})
    else:steps.append({'key':'crm','title':'CRM','say':'Не создавать задачу автоматически.','required':False})
    return {'status':'ok','mode':cfg.get('autoanswer_mode'),'engine':'voice_agent' if voice.get('enabled') else 'fallback_ivr','steps':steps,'opening':opening,'policy':{'collect_phone':bool(cfg.get('collect_phone',True)),'collect_topic':bool(cfg.get('collect_topic',True)),'create_crm_task':bool(cfg.get('create_crm_task',True)),'allow_prices':bool(voice.get('allow_prices')),'allow_commitments':bool(voice.get('allow_commitments')),'handoff_on_unknown':bool(voice.get('handoff_on_unknown',True))},'truth':'preview is policy-only; it is not a simulated successful call'}


def autoanswer_readiness(account_id:str)->dict[str,Any]:
    """Truthful go-live status for the configured answering policy."""
    cfg=afterhours_settings(account_id)['settings']
    decision=autoanswer_decision(account_id,int(cfg.get('no_answer_seconds') or 25),False)
    provider=provider_status(account_id);voice=voice_agent_settings(account_id)['settings'];pkg=minute_package(account_id)['package']
    facts=_copilot_account_facts(account_id,80) if voice.get('enabled') else []
    confirmed=sum(1 for f in facts if f.get('status')=='confirmed')
    blockers=[]
    configured=bool(cfg.get('enabled') and cfg.get('autoanswer_mode')!='off')
    if not configured:blockers.append('autoanswer_disabled')
    if not provider.get('provider_verified'):blockers.append('telephony_provider_not_verified')
    if voice.get('enabled') and confirmed<1:blockers.append('voice_agent_has_no_confirmed_company_facts')
    if float(pkg.get('remaining_minutes') or 0)<=0:blockers.append('minute_package_exhausted')
    media_ready=bool(provider.get('provider_verified') and provider.get('platforms',{}).get('web')=='media_transport_ready')
    ready=bool(configured and media_ready and (not voice.get('enabled') or confirmed>0) and float(pkg.get('remaining_minutes') or 0)>0)
    return {'status':'ok','ready':ready,'configured':configured,'mode':cfg.get('autoanswer_mode'),'engine':'voice_agent' if voice.get('enabled') else 'fallback_ivr','media_ready':media_ready,'provider_state':provider.get('provider_state'),'confirmed_company_facts':confirmed,'remaining_minutes':pkg.get('remaining_minutes'),'blockers':blockers,'decision_preview':decision,'truth':'ready=true only when policy, provider media, knowledge (for Voice Agent) and minute package are actually ready'}


def _spoken_phone(text_value:str)->str:
    # STT normally returns digits; support common Russian spoken digits without guessing ambiguous words.
    t=str(text_value or '').lower().replace('плюс',' + '); mp={'ноль':'0','нуль':'0','один':'1','одна':'1','два':'2','две':'2','три':'3','четыре':'4','пять':'5','шесть':'6','семь':'7','восемь':'8','девять':'9'}
    for k,v in mp.items():t=re.sub(rf'\b{k}\b',v,t)
    digits=''.join(re.findall(r'\d',t)); return ('+'+digits if '+' in t else digits) if 7<=len(digits)<=15 else ''


def afterhours_receptionist_turn(account_id:str,call_id:str,client_text:str)->dict[str,Any]:
    """Policy-driven receptionist state machine. Caller data is only requested when owner enabled that field."""
    ensure_schema();cfg=afterhours_status(account_id);settings=cfg['settings'];db=SessionLocal()
    collect_phone=bool(settings.get('collect_phone',True));collect_topic=bool(settings.get('collect_topic',True))
    initial='ask_phone' if collect_phone else 'ask_topic' if collect_topic else 'ready'
    try:
        sess=db.execute(text("SELECT * FROM telephony_afterhours_sessions WHERE account_id=:a AND call_id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first()
        if not sess:
            due=(datetime.fromisoformat(cfg['next_work_at']) if cfg.get('next_work_at') else (utcnow()+timedelta(minutes=15) if cfg.get('working_now') else _next_work_time(settings)))
            db.execute(text("INSERT INTO telephony_afterhours_sessions(account_id,call_id,state,next_callback_at) VALUES(:a,:c,:s,:n)"),{'a':account_id,'c':call_id,'s':initial,'n':due});db.commit();sess=db.execute(text("SELECT * FROM telephony_afterhours_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().one()
        state=sess['state'];txt=str(client_text or '').strip(); response='';next_state=state
        if state=='ready': response='Спасибо. Информация принята.'
        elif state in {'ask_phone','retry_phone'}:
            ph=_spoken_phone(txt)
            if not ph: next_state='retry_phone';response='Не удалось уверенно распознать номер. Пожалуйста, назовите номер ещё раз, по одной цифре.'
            else:
                pretty=' '.join(ph);next_state='confirm_phone';response=f'Я записал номер {pretty}. Всё верно? Скажите «да» или назовите номер ещё раз.';db.execute(text("UPDATE telephony_afterhours_sessions SET captured_phone=:p WHERE account_id=:a AND call_id=:c"),{'p':ph,'a':account_id,'c':call_id})
        elif state=='confirm_phone':
            low=txt.lower()
            if re.search(r'\b(да|верно|правильно|подтверждаю)\b',low):
                next_state='ask_topic' if collect_topic else 'ready';response='Спасибо. Кратко скажите, пожалуйста, по какому вопросу вы звонили.' if collect_topic else 'Спасибо. Номер подтверждён, передам информацию менеджеру.';db.execute(text("UPDATE telephony_afterhours_sessions SET confirmed_phone=captured_phone WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id})
            else:
                ph=_spoken_phone(txt)
                if ph: pretty=' '.join(ph);next_state='confirm_phone';response=f'Хорошо, записал новый номер {pretty}. Теперь верно?';db.execute(text("UPDATE telephony_afterhours_sessions SET captured_phone=:p WHERE account_id=:a AND call_id=:c"),{'p':ph,'a':account_id,'c':call_id})
                else:next_state='retry_phone';response='Хорошо. Назовите номер ещё раз, по одной цифре.'
        elif state=='ask_topic':
            if len(txt)<3:response='Пожалуйста, кратко назовите тему звонка.'
            else:next_state='ready';response='Спасибо. Мы передадим информацию менеджеру, и он свяжется с вами в ближайшее рабочее время.';db.execute(text("UPDATE telephony_afterhours_sessions SET topic=:t WHERE account_id=:a AND call_id=:c"),{'t':txt[:1500],'a':account_id,'c':call_id})
        db.execute(text("UPDATE telephony_afterhours_sessions SET state=:s,attempts=attempts+1,updated_at=now() WHERE account_id=:a AND call_id=:c"),{'s':next_state,'a':account_id,'c':call_id});db.commit();row=dict(db.execute(text("SELECT * FROM telephony_afterhours_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().one())
    finally:db.close()
    return {'status':'ok','state':row['state'],'say':response,'session':row,'policy':{'collect_phone':collect_phone,'collect_topic':collect_topic,'create_crm_task':bool(settings.get('create_crm_task',True))}}


def finalize_afterhours_callback(account_id:str,call_id:str)->dict[str,Any]:
    """Create canonical CRM follow-up only according to the owner's autoanswer policy."""
    cfg=afterhours_settings(account_id)['settings'];db=SessionLocal();task=None
    try:
        srow=db.execute(text("SELECT * FROM telephony_afterhours_sessions WHERE account_id=:a AND call_id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first();call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not srow or srow['state']!='ready':return {'status':'not_ready'}
        phone=srow.get('confirmed_phone') or (normalize_phone((call or {}).get('from_number')) if not cfg.get('collect_phone',True) else None)
        topic=srow.get('topic') or ('Входящий звонок' if not cfg.get('collect_topic',True) else None)
        if cfg.get('collect_phone',True) and not phone:return {'status':'not_ready','reason':'phone_required'}
        if cfg.get('collect_topic',True) and not topic:return {'status':'not_ready','reason':'topic_required'}
        if not cfg.get('create_crm_task',True):
            db.execute(text("UPDATE telephony_afterhours_sessions SET state='completed',updated_at=now() WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id});db.commit();return {'status':'ok','task_id':None,'crm_task_skipped':True,'callback_at':srow['next_callback_at'],'phone':phone,'topic':topic}
        owner=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),{'a':account_id}).scalar();ref=f"boris_afterhours:{account_id}:{call_id}"
        if owner is not None:
            ex=db.execute(text("SELECT id FROM boris_crm_tasks WHERE owner_user_id=:o AND description LIKE :r LIMIT 1"),{'o':int(owner),'r':ref+'%'}).scalar()
            if ex:task=int(ex)
            else:task=int(db.execute(text("""INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source) VALUES(:o,:d,:c,'Перезвонить клиенту после звонка автоответчику',:r,:o,:due,'open','boris_telephony_afterhours') RETURNING id"""),{'o':int(owner),'d':call.get('crm_deal_id') if call else None,'c':call.get('crm_contact_id') if call else None,'r':f"{ref}\nТелефон: {phone or 'не указан'}\nТема: {topic or 'не указана'}",'due':srow['next_callback_at']}).scalar_one())
        db.execute(text("UPDATE telephony_afterhours_sessions SET state='completed',crm_task_id=:t,updated_at=now() WHERE account_id=:a AND call_id=:c"),{'t':task,'a':account_id,'c':call_id});db.commit()
    finally:db.close()
    return {'status':'ok','task_id':task,'callback_at':srow['next_callback_at'],'phone':phone,'topic':topic}


def afterhours_cost_estimate(minutes:float=2.0, tts_chars:int=500, usd_rub:float=80.0, provider_rub_per_min:float|None=None)->dict[str,Any]:
    """Planning estimator. OpenAI rates are explicit assumptions and provider telephony is separate until selected."""
    m=max(0.1,min(float(minutes),60.0)); chars=max(0,min(int(tts_chars),10000)); fx=max(1.0,float(usd_rub))
    # Current planning rates (2026-08-31): realtime transcription $0.017/min; tts-1 $15/1M chars.
    stt_usd=m*0.017; tts_usd=chars/1_000_000*15.0; ai_usd=0.0
    openai_usd=stt_usd+tts_usd
    provider_rub=(m*float(provider_rub_per_min)) if provider_rub_per_min is not None else None
    return {'status':'ok','assumptions':{'minutes':m,'tts_chars':chars,'usd_rub':fx,'stt_usd_per_min':0.017,'tts_usd_per_million_chars':15.0,'dialog_logic':'deterministic_state_machine','provider_rub_per_min':provider_rub_per_min},'openai':{'stt_usd':round(stt_usd,6),'tts_usd':round(tts_usd,6),'dialog_ai_usd':ai_usd,'total_usd':round(openai_usd,6),'total_rub':round(openai_usd*fx,2)},'provider':{'total_rub':round(provider_rub,2) if provider_rub is not None else None,'status':'included' if provider_rub is not None else 'pending_provider_tariff'},'total_rub':round(openai_usd*fx+(provider_rub or 0),2) if provider_rub is not None else None}


TELEPHONY_PACKAGE_MINUTES=(300,500,700,1000,1500,2000)
BUNDLED_PHONE_FEATURES=('human_calls','rop_audit','realtime_copilot','afterhours_voice_agent','recording_pipeline','crm_callback')


def _minute_cycle_end(start):
    from calendar import monthrange
    if isinstance(start,datetime): start=start.date()
    y=start.year+(1 if start.month==12 else 0); m=1 if start.month==12 else start.month+1
    anniversary=start.replace(year=y,month=m,day=min(start.day,monthrange(y,m)[1]))
    return anniversary-timedelta(days=1)

def minute_package(account_id:str)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:
        r=db.execute(text("SELECT * FROM telephony_minute_packages WHERE account_id=:a"),{'a':account_id}).mappings().first()
        if r and not r.get('cycle_end'):
            ce=_minute_cycle_end(r.get('cycle_start') or utcnow().date())
            db.execute(text("UPDATE telephony_minute_packages SET cycle_end=:e,updated_at=now() WHERE account_id=:a AND cycle_end IS NULL"),{'e':ce,'a':account_id}); db.commit()
            r=db.execute(text("SELECT * FROM telephony_minute_packages WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally:db.close()
    pkg=dict(r) if r else {'account_id':account_id,'package_minutes':300,'used_seconds':0,'overage_mode':'notify','soft_limit_percent':90}
    total=int(pkg['package_minutes'])*60;used=int(pkg.get('used_seconds') or 0);remaining=max(0,total-used);pct=round(used*100/total,1) if total else 0
    pkg.update({'used_minutes':round(used/60,2),'remaining_minutes':round(remaining/60,2),'usage_percent':pct,'bundled_features':list(BUNDLED_PHONE_FEATURES),'available_packages':list(TELEPHONY_PACKAGE_MINUTES)})
    return {'status':'ok','package':pkg}


def save_minute_package(account_id:str,minutes:int,overage_mode:str='notify',soft_limit_percent:int=90)->dict[str,Any]:
    m=int(minutes); assert m in TELEPHONY_PACKAGE_MINUTES
    mode=str(overage_mode or 'notify').lower(); assert mode in {'notify','allow','hard_stop'};soft=max(70,min(int(soft_limit_percent),100));db=SessionLocal()
    try:
        today=utcnow().date(); cycle_end=_minute_cycle_end(today)
        db.execute(text("""INSERT INTO telephony_minute_packages(account_id,package_minutes,overage_mode,soft_limit_percent,cycle_start,cycle_end) VALUES(:a,:m,:o,:s,:cs,:ce) ON CONFLICT(account_id) DO UPDATE SET package_minutes=excluded.package_minutes,overage_mode=excluded.overage_mode,soft_limit_percent=excluded.soft_limit_percent,cycle_end=COALESCE(telephony_minute_packages.cycle_end,excluded.cycle_end),updated_at=now()"""),{'a':account_id,'m':m,'o':mode,'s':soft,'cs':today,'ce':cycle_end});db.commit()
    finally:db.close()
    return minute_package(account_id)


def record_minute_usage(account_id:str,seconds:int,usage_kind:str,call_id:str|None=None,ai_cost_rub:float=0,provider_cost_rub:float=0,infra_cost_rub:float=0,idempotency_key:str|None=None)->dict[str,Any]:
    sec=max(0,int(seconds));kind=str(usage_kind or '').strip().lower();allowed={'human_call','voice_agent','afterhours_agent'}
    if kind not in allowed:return {'status':'invalid_usage_kind'}
    key=idempotency_key or f'{kind}:{call_id}:{sec}';db=SessionLocal()
    try:
        row=db.execute(text("""INSERT INTO telephony_minute_usage(account_id,call_id,usage_kind,seconds,ai_cost_rub,provider_cost_rub,infra_cost_rub,included_features,idempotency_key)
          VALUES(:a,:c,:k,:s,:ai,:p,:i,CAST(:f AS jsonb),:id) ON CONFLICT(account_id,idempotency_key) DO NOTHING RETURNING id"""),
          {'a':account_id,'c':call_id,'k':kind,'s':sec,'ai':ai_cost_rub,'p':provider_cost_rub,'i':infra_cost_rub,'f':json.dumps(BUNDLED_PHONE_FEATURES),'id':key}).scalar()
        if row is None:
            existing=db.execute(text("SELECT id FROM telephony_minute_usage WHERE account_id=:a AND idempotency_key=:k"),{'a':account_id,'k':key}).scalar_one()
            db.commit(); return {'status':'duplicate','usage_id':int(existing),'package':minute_package(account_id)['package']}
        today=utcnow().date(); cycle_end=_minute_cycle_end(today)
        db.execute(text("""INSERT INTO telephony_minute_packages(account_id,package_minutes,used_seconds,cycle_start,cycle_end) VALUES(:a,300,:s,:cs,:ce) ON CONFLICT(account_id) DO UPDATE SET used_seconds=telephony_minute_packages.used_seconds+:s,cycle_end=COALESCE(telephony_minute_packages.cycle_end,excluded.cycle_end),updated_at=now()"""),{'a':account_id,'s':sec,'cs':today,'ce':cycle_end});db.commit()
    finally:db.close()
    return {'status':'ok','usage_id':int(row),'package':minute_package(account_id)['package']}

def minute_usage_dashboard(account_id:str,days:int=31)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("SELECT usage_kind,sum(seconds) seconds,sum(ai_cost_rub) ai_cost_rub,sum(provider_cost_rub) provider_cost_rub,sum(infra_cost_rub) infra_cost_rub,count(*) records FROM telephony_minute_usage WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval GROUP BY usage_kind ORDER BY usage_kind"),{'a':account_id,'d':max(1,min(days,366))}).mappings().all()]
        totals=dict(db.execute(text("SELECT coalesce(sum(ai_cost_rub),0) ai_cost_rub,coalesce(sum(provider_cost_rub),0) provider_cost_rub,coalesce(sum(infra_cost_rub),0) infra_cost_rub FROM telephony_minute_usage WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval"),{'a':account_id,'d':max(1,min(days,366))}).mappings().one())
    finally:db.close()
    for r in rows:r['minutes']=round(int(r['seconds'] or 0)/60,2)
    totals['total_cost_rub']=sum(float(totals[x] or 0) for x in ('ai_cost_rub','provider_cost_rub','infra_cost_rub'))
    return {'status':'ok','package':minute_package(account_id)['package'],'by_usage_kind':rows,'internal_costs':totals}


def meter_completed_call(account_id:str,call_id:str)->dict[str,Any]:
    """Meter real connected seconds once. ROP/Copilot/after-hours features are bundled, not separate billable units."""
    ensure_schema();db=SessionLocal()
    try:c=db.execute(text("SELECT id,talk_duration_sec,agent_mode,metadata_json FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    if not c:return {'status':'not_found'}
    sec=max(0,int(c.get('talk_duration_sec') or 0));meta=c.get('metadata_json') or {};mode=str(c.get('agent_mode') or 'human')
    if mode in {'voice_agent','ai_voice'}:kind='voice_agent'
    elif bool(meta.get('afterhours_agent')) or mode=='afterhours_agent':kind='afterhours_agent'
    else:kind='human_call'
    if sec<=0:return {'status':'no_connected_seconds','seconds':0}
    return record_minute_usage(account_id,sec,kind,call_id=call_id,idempotency_key=f'completed_call:{call_id}')


def minute_alert_guard(account_id:str)->dict[str,Any]:
    pkg=minute_package(account_id)['package'];pct=float(pkg.get('usage_percent') or 0);cycle=pkg.get('cycle_start'); thresholds=[80,90,100]
    db=SessionLocal();created=[]
    try:
        for t in thresholds:
            if pct<t:continue
            row=db.execute(text("""INSERT INTO telephony_minute_alerts(account_id,threshold,cycle_start,usage_percent) VALUES(:a,:t,:c,:p) ON CONFLICT(account_id,threshold,cycle_start) DO NOTHING RETURNING id"""),{'a':account_id,'t':t,'c':cycle,'p':pct}).scalar()
            if row:created.append(t)
        db.commit()
    finally:db.close()
    return {'status':'ok','usage_percent':pct,'created_thresholds':created,'overage':pct>=100,'overage_mode':pkg.get('overage_mode')}


def reset_minute_cycle_if_due(account_id:str,at:datetime|None=None)->dict[str,Any]:
    """Monthly anniversary is represented by cycle_end. Reset is explicit/idempotent and never changes package size."""
    ensure_schema();now=(at or utcnow()).date();db=SessionLocal()
    try:
        r=db.execute(text("SELECT * FROM telephony_minute_packages WHERE account_id=:a FOR UPDATE"),{'a':account_id}).mappings().first()
        if not r:return {'status':'not_configured'}
        end=r.get('cycle_end')
        if not end or now<=end:return {'status':'not_due'}
        start=now; nxt=_minute_cycle_end(start)
        db.execute(text("UPDATE telephony_minute_packages SET used_seconds=0,cycle_start=:s,cycle_end=:e,updated_at=now() WHERE account_id=:a"),{'s':start,'e':nxt,'a':account_id});db.commit();return {'status':'reset','cycle_start':start,'cycle_end':nxt}
    finally:db.close()



def minute_package_guardian(limit:int=500)->dict[str,Any]:
    """Cycle reset + threshold creation for every configured account. No customer notification is fabricated here."""
    ensure_schema(); db=SessionLocal(); cap=max(1,min(int(limit),5000))
    try:
        accounts=[str(x[0]) for x in db.execute(text("SELECT account_id FROM telephony_minute_packages ORDER BY updated_at ASC LIMIT :l"),{'l':cap}).fetchall()]
    finally: db.close()
    reset=alerts=failed=0; details=[]
    for account_id in accounts:
        try:
            rr=reset_minute_cycle_if_due(account_id)
            if rr.get('status')=='reset': reset+=1
            ar=minute_alert_guard(account_id); created=list(ar.get('created_thresholds') or []); alerts+=len(created)
            if rr.get('status')=='reset' or created: details.append({'account_id':account_id,'cycle':rr.get('status'),'alerts':created})
        except Exception as exc:
            failed+=1; details.append({'account_id':account_id,'status':'failed','error_code':type(exc).__name__[:120]})
    return {'status':'ok','accounts':len(accounts),'cycles_reset':reset,'alerts_created':alerts,'failed':failed,'details':details[:50]}


def minute_alerts(account_id:str,limit:int=20)->dict[str,Any]:
    """Customer-safe usage alerts. Contains no provider/AI/infra cost information."""
    ensure_schema(); db=SessionLocal(); lim=max(1,min(int(limit),100))
    try:
        rows=[dict(x) for x in db.execute(text("SELECT id,threshold,cycle_start,usage_percent,status,delivered_at,last_error,created_at,updated_at FROM telephony_minute_alerts WHERE account_id=:a ORDER BY created_at DESC,id DESC LIMIT :l"),{'a':account_id,'l':lim}).mappings().all()]
    finally: db.close()
    return {'status':'ok','items':rows}


def minute_alert_delivery_guardian(limit:int=100,account_id:str|None=None)->dict[str,Any]:
    """Deliver opt-in customer-safe minute threshold alerts using existing report recipients.
    No cost/COGS fields are included. Disabled by default and idempotent per alert row.
    """
    ensure_schema(); db=SessionLocal(); lim=max(1,min(int(limit),500))
    recovered_sending=exhausted_sending=0
    try:
        recovered_sending=db.execute(text("""UPDATE telephony_minute_alerts SET status='retry',next_attempt_at=now(),last_error=concat_ws('; ',NULLIF(last_error,''),'recovered stale sending lease'),updated_at=now() WHERE status='sending' AND attempts<5 AND updated_at<now()-interval '10 minutes' AND (:account_id IS NULL OR account_id=:account_id) RETURNING id"""),{'account_id':account_id}).rowcount or 0
        exhausted_sending=db.execute(text("""UPDATE telephony_minute_alerts SET status='failed',next_attempt_at=NULL,last_error=concat_ws('; ',NULLIF(last_error,''),'stale sending lease exhausted retry limit'),updated_at=now() WHERE status='sending' AND attempts>=5 AND updated_at<now()-interval '10 minutes' AND (:account_id IS NULL OR account_id=:account_id) RETURNING id"""),{'account_id':account_id}).rowcount or 0
        db.commit()
        rows=[dict(x) for x in db.execute(text("""SELECT a.id,a.account_id,a.threshold,a.usage_percent,a.cycle_start,
          s.email_to,s.telegram_chat_id,s.telegram_thread_id
          FROM telephony_minute_alerts a JOIN telephony_report_settings s ON s.account_id=a.account_id
          WHERE s.minute_alerts_enabled=true AND a.status IN ('created','retry')
            AND a.attempts<5 AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=now())
            AND (s.email_to IS NOT NULL OR s.telegram_chat_id IS NOT NULL)
            AND (:account_id IS NULL OR a.account_id=:account_id)
          ORDER BY a.created_at,a.id LIMIT :l FOR UPDATE SKIP LOCKED"""),{'l':lim,'account_id':account_id}).mappings().all()]
        for r in rows:
            db.execute(text("UPDATE telephony_minute_alerts SET status='sending',attempts=attempts+1,last_error=NULL,updated_at=now() WHERE id=:i"),{'i':r['id']})
        db.commit()
    finally: db.close()
    sent=failed=0; results=[]
    for r in rows:
        threshold=int(r.get('threshold') or 0); pct=float(r.get('usage_percent') or 0)
        text_msg=f"BORIS Phone · использовано {pct:.1f}% пакета минут. Порог {threshold}%. Откройте Телефония → Минуты, чтобы посмотреть остаток и прогноз."
        channel_results=[]
        if r.get('email_to'):
            try:
                from app.services.email_service import send_email
                ok,reason,_=send_email(str(r['email_to']),f'BORIS Phone · пакет минут {threshold}%',text_msg)
                # Mail transport reasons are untrusted and may contain provider
                # diagnostics or credentials. Persist only a stable error code.
                channel_results.append(('email',bool(ok),'' if ok else 'email_delivery_failed'))
            except Exception as exc: channel_results.append(('email',False,type(exc).__name__))
        if r.get('telegram_chat_id'):
            try:
                from app.telegram_bot import send_telegram_message
                rv=send_telegram_message(str(r['telegram_chat_id']),text_msg,thread_id=r.get('telegram_thread_id') or None)
                channel_results.append(('telegram',rv is not False,'' if rv is not False else 'telegram_failed'))
            except Exception as exc: channel_results.append(('telegram',False,type(exc).__name__))
        ok_any=any(x[1] for x in channel_results); err='; '.join(f'{c}:{e}' for c,ok,e in channel_results if not ok)[:500] or None
        db=SessionLocal()
        try:
            attempts=int(db.execute(text("SELECT attempts FROM telephony_minute_alerts WHERE id=:i"),{'i':r['id']}).scalar() or 0)
            terminal=(not ok_any and attempts>=5)
            db.execute(text("""UPDATE telephony_minute_alerts SET status=:s,last_error=:e,
              delivered_at=CASE WHEN :ok THEN now() ELSE delivered_at END,
              next_attempt_at=CASE WHEN :ok OR :terminal THEN NULL ELSE now()+make_interval(secs => LEAST(1800,30*(2 ^ LEAST(GREATEST(attempts-1,0),6))::int)) END,
              updated_at=now() WHERE id=:i"""),
              {'s':'sent' if ok_any else ('failed' if terminal else 'retry'),'e':err,'ok':ok_any,'terminal':terminal,'i':r['id']});db.commit()
        finally: db.close()
        sent+=1 if ok_any else 0; failed+=0 if ok_any else 1
        results.append({'alert_id':r['id'],'threshold':threshold,'status':'sent' if ok_any else 'retry'})
    return {'status':'ok','picked':len(rows),'sent':sent,'failed':failed,'recovered_sending':int(recovered_sending),'exhausted_sending':int(exhausted_sending),'items':results[:20]}

def minute_package_forecast(account_id:str, at:datetime|None=None)->dict[str,Any]:
    """Customer-safe forecast plus owner-only raw COGS inputs. Pricing is intentionally not decided here."""
    pkg=minute_package(account_id).get('package') or {}; now=(at or utcnow())
    start=pkg.get('cycle_start'); end=pkg.get('cycle_end')
    if isinstance(start,str): start=datetime.fromisoformat(start).date()
    if not start: start=now.date().replace(day=1)
    if isinstance(end,str): end=datetime.fromisoformat(end).date()
    if not end:
        if start.month==12: end=start.replace(year=start.year+1,month=1,day=1)-timedelta(days=1)
        else: end=start.replace(month=start.month+1,day=1)-timedelta(days=1)
    elapsed=max(1,(now.date()-start).days+1); total=max(1,(end-start).days+1)
    used=float(pkg.get('used_minutes') or 0); daily=used/elapsed; projected=daily*total; allowance=float(pkg.get('package_minutes') or 300)
    return {'status':'ok','cycle':{'start':str(start),'end':str(end),'elapsed_days':elapsed,'total_days':total},'used_minutes':round(used,2),'daily_run_rate_minutes':round(daily,2),'projected_minutes':round(projected,1),'projected_overage_minutes':round(max(0,projected-allowance),1),'recommended_package_minutes':next((x for x in TELEPHONY_PACKAGE_MINUTES if x>=projected),TELEPHONY_PACKAGE_MINUTES[-1]),'pricing_status':'collecting_real_cogs'}


def phone_pricing_lab(account_id:str,days:int=31,target_margins:list[float]|None=None)->dict[str,Any]:
    """Internal decision support only: derives cost/min from real metered rows; never publishes customer prices automatically."""
    ensure_schema(); margins=target_margins or [0.60,0.70,0.75,0.80]; margins=[float(x) for x in margins if 0<float(x)<0.95]
    db=SessionLocal()
    try:
        r=db.execute(text("""SELECT coalesce(sum(seconds),0) seconds,coalesce(sum(ai_cost_rub+provider_cost_rub+infra_cost_rub),0) cogs,count(*) records FROM telephony_minute_usage WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':max(1,min(int(days),366))}).mappings().one()
        kinds=[dict(x) for x in db.execute(text("""SELECT usage_kind,coalesce(sum(seconds),0) seconds,coalesce(sum(ai_cost_rub+provider_cost_rub+infra_cost_rub),0) cogs,count(*) records FROM telephony_minute_usage WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval GROUP BY usage_kind ORDER BY usage_kind"""),{'a':account_id,'d':max(1,min(int(days),366))}).mappings().all()]
    finally:db.close()
    mins=float(r['seconds'])/60; cogs=float(r['cogs']); cpm=(cogs/mins) if mins else None
    for k in kinds:
        km=float(k['seconds'])/60; k['minutes']=round(km,2);k['cogs']=round(float(k['cogs']),2);k['cogs_per_minute']=round(float(k['cogs'])/km,4) if km else None;k.pop('seconds',None)
    scenarios=[]
    if cpm is not None:
        for package in TELEPHONY_PACKAGE_MINUTES:
            row={'package_minutes':package,'estimated_cogs_rub':round(cpm*package,2),'prices_by_target_margin':{}}
            for m in margins: row['prices_by_target_margin'][f'{int(m*100)}%']=round((cpm*package)/(1-m),0)
            scenarios.append(row)
    meter_ready=int(r['records'])>=20 and mins>=60
    commercial=phone_owner_economics(account_id,days).get('commercial_readiness') or {}
    commercial_ready=bool(commercial.get('pricing_ready'))
    status='ready' if meter_ready and commercial_ready else ('insufficient_real_usage' if not meter_ready else 'insufficient_outcome_attribution')
    return {'status':'ok','data_status':status,'metering_ready':meter_ready,'commercial_readiness':commercial,'sample':{'days':days,'records':int(r['records']),'minutes':round(mins,2),'cogs_rub':round(cogs,2),'cogs_per_minute_rub':round(cpm,4) if cpm is not None else None},'by_usage_kind':kinds,'target_margins':margins,'scenarios':scenarios,'note':'Цена может рассматриваться только при достаточном реальном metering и полной CRM/COGS/outcome атрибуции.'}


CALL_MODES={'human','voice_agent','afterhours_agent','fallback_ivr'}
VOICE_AGENT_MODES={'voice_agent','afterhours_agent'}
def voice_agent_settings(account_id:str)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:r=db.execute(text("SELECT * FROM telephony_voice_agent_settings WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally:db.close()
    return {'status':'ok','settings':dict(r) if r else {'account_id':account_id,'enabled':False,'afterhours_enabled':True,'voice_name':'default','language':'ru','max_call_minutes':15,'allow_prices':False,'allow_commitments':False,'allow_crm_write':True,'handoff_on_unknown':True,'disclosure_required':True,'disclosure_text':'Здравствуйте! Вам отвечает виртуальный помощник BORIS.'}}

def save_voice_agent_settings(account_id:str,payload:dict)->dict[str,Any]:
    db=SessionLocal();mins=max(1,min(int(payload.get('max_call_minutes',15)),60))
    try:
        db.execute(text("""INSERT INTO telephony_voice_agent_settings(account_id,enabled,afterhours_enabled,voice_name,language,max_call_minutes,allow_prices,allow_commitments,allow_crm_write,handoff_on_unknown,disclosure_required,disclosure_text) VALUES(:a,:e,:ah,:v,:l,:m,:p,:c,:crm,:h,:dr,:dt) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,afterhours_enabled=excluded.afterhours_enabled,voice_name=excluded.voice_name,language=excluded.language,max_call_minutes=excluded.max_call_minutes,allow_prices=excluded.allow_prices,allow_commitments=excluded.allow_commitments,allow_crm_write=excluded.allow_crm_write,handoff_on_unknown=excluded.handoff_on_unknown,disclosure_required=excluded.disclosure_required,disclosure_text=excluded.disclosure_text,updated_at=now()"""),{'a':account_id,'e':bool(payload.get('enabled')),'ah':bool(payload.get('afterhours_enabled',True)),'v':str(payload.get('voice_name') or 'default')[:80],'l':str(payload.get('language') or 'ru')[:16],'m':mins,'p':bool(payload.get('allow_prices')),'c':bool(payload.get('allow_commitments')),'crm':bool(payload.get('allow_crm_write',True)),'h':bool(payload.get('handoff_on_unknown',True)),'dr':bool(payload.get('disclosure_required',True)),'dt':str(payload.get('disclosure_text') or 'Здравствуйте! Вам отвечает виртуальный помощник BORIS.')[:800]});db.commit()
    finally:db.close()
    return voice_agent_settings(account_id)

def _provider_media_evidence(account_id:str)->dict[str,Any]:
    """Require verified adapter capability plus device-reported bidirectional RTP proof."""
    st=provider_status(account_id); p=str(st.get('provider') or '')
    caps=set((st.get('adapter') or {}).get('capabilities') or [])
    media_cap=bool(caps.intersection({'media_session','webrtc','sip_media','autoanswer_media'}))
    if st.get('provider_verified') is not True:return {'ready':False,'reason':'provider_unverified','provider':p,'capabilities':sorted(caps)}
    if not media_cap:return {'ready':False,'reason':'media_capability_unverified','provider':p,'capabilities':sorted(caps)}
    db=SessionLocal()
    try:
        row=db.execute(text("""SELECT created_at FROM telephony_audit
          WHERE account_id=:a AND provider=:p AND action='media.transport.proven' AND result='ok'
          ORDER BY id DESC LIMIT 1"""),{'a':account_id,'p':p}).first()
    finally:db.close()
    return {'ready':bool(row),'reason':'ok' if row else 'real_media_transport_evidence_missing','provider':p,'capabilities':sorted(caps),'validated_at':row[0] if row else None}


def voice_agent_guard(account_id:str,call_id:str,requested_mode:str='voice_agent')->dict[str,Any]:
    cfg=voice_agent_settings(account_id)['settings'];mode=str(requested_mode or '').lower()
    if mode not in VOICE_AGENT_MODES:return {'status':'blocked','reason':'invalid_mode'}
    if not cfg.get('enabled'):return {'status':'blocked','reason':'voice_agent_disabled'}
    if mode=='afterhours_agent':
        if not cfg.get('afterhours_enabled'):return {'status':'blocked','reason':'afterhours_agent_disabled'}
        ah=afterhours_status(account_id)
        if not ah.get('afterhours'):return {'status':'blocked','reason':'working_hours'}
    db=SessionLocal()
    try:call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    if not call:return {'status':'blocked','reason':'call_not_found'}
    # Hard boundary: a call assigned to a real human user/device cannot silently become AI voice.
    if call.get('user_id') or call.get('device_id'):return {'status':'blocked','reason':'human_call_boundary'}
    if call.get('state') not in {'ringing','active','answered','on_hold'}:return {'status':'blocked','reason':'call_not_active'}
    media=_provider_media_evidence(account_id)
    if not media.get('ready'):return {'status':'blocked','reason':media.get('reason'),'media_evidence':media}
    from app.services.telephony_adapters import get_adapter
    adapter=get_adapter(media.get('provider')); commands=sorted(set(getattr(adapter,'supported_commands',set()) or set()) & CONTROL_COMMANDS) if adapter else []
    pkg=minute_package(account_id)['package'];return {'status':'ok','mode':mode,'settings':cfg,'package':{'remaining_minutes':pkg['remaining_minutes'],'usage_percent':pkg['usage_percent']},'media_evidence':media,'policy':{'may_speak':True,'may_control_call':bool(commands),'control_commands':commands,'human_call':False,'grounded_only':True,'allow_prices':bool(cfg.get('allow_prices')),'allow_commitments':bool(cfg.get('allow_commitments'))}}

def voice_agent_start(account_id:str,call_id:str,mode:str='voice_agent')->dict[str,Any]:
    g=voice_agent_guard(account_id,call_id,mode)
    if g.get('status')!='ok':return g
    db=SessionLocal()
    try:
        row=db.execute(text("""INSERT INTO telephony_voice_agent_sessions(account_id,call_id,mode,state) VALUES(:a,:c,:m,'active') ON CONFLICT(account_id,call_id) DO UPDATE SET mode=excluded.mode,state='active' RETURNING *"""),{'a':account_id,'c':call_id,'m':mode}).mappings().one(); db.execute(text("UPDATE telephony_calls SET agent_mode=:m,updated_at=now() WHERE account_id=:a AND id=:c"),{'m':mode,'a':account_id,'c':call_id}); db.commit()
    finally:db.close()
    facts=_copilot_account_facts(account_id,80)
    return {'status':'ok','session':dict(row),'guard':g,'grounding':{'fact_count':len(facts),'confirmed_count':sum(1 for f in facts if f.get('status')=='confirmed')}}

def voice_agent_context(account_id:str,call_id:str)->dict[str,Any]:
    db=SessionLocal()
    try:
        sess=db.execute(text("SELECT * FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first();call=db.execute(text("SELECT id,direction,from_number,to_number,crm_contact_id,crm_deal_id,source,source_ref,state FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    facts=_copilot_account_facts(account_id,80)
    return {'status':'ok' if sess else 'not_started','session':dict(sess) if sess else None,'call':_public_call_view(call),'facts':facts,'rules':{'unknown':'Не выдумывать. Зафиксировать вопрос и передать менеджеру.','price':'Только подтвержденный факт и только если allow_prices=true.','commitment':'Не обещать срок/скидку/условия без разрешения.','phone':'Повторить номер и получить явное подтверждение перед CRM callback.'}}

def voice_agent_complete(account_id:str,call_id:str,topic:str='',summary:str='',qualification:str='',next_action:str='',confirmed_phone:str='')->dict[str,Any]:
    cfg=voice_agent_settings(account_id)['settings'];db=SessionLocal()
    try:
        sess=db.execute(text("SELECT * FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c FOR UPDATE"),{'a':account_id,'c':call_id}).mappings().first()
        if not sess:return {'status':'not_started'}
        db.execute(text("UPDATE telephony_voice_agent_sessions SET state='completed',topic=:t,summary=:s,qualification=:q,next_action=:n,customer_phone=:p,ended_at=now() WHERE account_id=:a AND call_id=:c"),{'t':str(topic)[:1500],'s':str(summary)[:3000],'q':str(qualification)[:40],'n':str(next_action)[:1200],'p':normalize_phone(confirmed_phone),'a':account_id,'c':call_id});db.commit()
    finally:db.close()
    crm=None
    if cfg.get('allow_crm_write'):
        # Reuse canonical disposition/task path. No second CRM.
        ah=afterhours_status(account_id)
        callback=ah.get('next_work_at') if sess.get('mode')=='afterhours_agent' else None
        crm=save_call_disposition(account_id,call_id,qualification=qualification,summary=summary,next_action=next_action,callback_at=callback)
    return {'status':'ok','crm':crm,'mode':sess.get('mode')}


def _voice_find_fact(facts:list[dict],query:str)->dict|None:
    q=str(query or '').casefold(); toks={x for x in re.findall(r'[а-яa-z0-9]{4,}',q) if x not in {'сколько','какая','какой','какие','можно','нужно','хочу','есть','будет'}}
    best=None;score=0
    for f in facts:
        blob=f"{f.get('category','')} {f.get('name','')} {f.get('value','')}".casefold(); n=sum(1 for t in toks if t in blob)
        if n>score:score=n;best=f
    return best if score>0 else None

def voice_agent_dialog_turn(account_id:str,call_id:str,client_text:str)->dict[str,Any]:
    """Provider-neutral dialog brain. It never pretends to be realtime audio transport."""
    textv=str(client_text or '').strip()
    if not textv:return {'status':'empty'}
    ctx=voice_agent_context(account_id,call_id)
    if ctx.get('status')!='ok':return {'status':'blocked','reason':'session_not_started'}
    sess=ctx['session']; cfg=voice_agent_settings(account_id)['settings']; facts=ctx['facts']; low=textv.casefold(); evidence=[];intent='general';action='continue'
    if sess.get('state')=='handoff_requested': return {'status':'blocked','reason':'handoff_in_progress','reply_text':'Передаю разговор менеджеру. Пожалуйста, оставайтесь на линии.','action':'handoff','transport':'text_brain_only'}
    # deterministic safety/control intents before any future model layer
    if any(x in low for x in ['оператор','менеджер','человек','соедините','переключите']):
        intent='handoff';action='handoff';reply='Конечно. Передаю запрос менеджеру. Если сейчас никто не свободен, зафиксирую обратный звонок.'
    elif any(x in low for x in ['цена','стоимость','сколько стоит','прайс']):
        intent='price';f=_voice_find_fact(facts,textv)
        if cfg.get('allow_prices') and f and f.get('status')=='confirmed':
            reply=f"По подтверждённой информации компании: {f.get('name')}: {f.get('value')}. Уточните, пожалуйста, объём или параметры задачи."
            evidence=[{'type':'client_fact','id':f.get('id'),'status':f.get('status')}]
        else:
            action='collect';reply='Чтобы не назвать неверную цену, зафиксирую параметры для точного расчёта. Что именно вам нужно и в каком объёме?'
    elif any(x in low for x in ['срок','когда','как быстро','успеете']):
        intent='timeline';action='collect';reply='Подскажите, к какой дате вам нужен результат? Я зафиксирую срок. Неподтверждённые сроки обещать не буду.'
    elif any(x in low for x in ['перезвон','позвоните','свяжитесь']):
        intent='callback';action='collect_phone';reply='Хорошо. Назовите, пожалуйста, номер для обратного звонка. Я повторю его для проверки.'
    elif any(x in low for x in ['подумаю','позже','не сейчас']):
        intent='defer';action='collect';reply='Понял. Подскажите, что сейчас мешает принять решение: цена, сроки, условия или нужно сравнить варианты?'
    else:
        f=_voice_find_fact(facts,textv)
        if f and f.get('status')=='confirmed':
            intent='known_fact';reply=f"По подтверждённой информации компании: {f.get('name')}: {f.get('value')}. Что ещё важно уточнить?";evidence=[{'type':'client_fact','id':f.get('id'),'status':f.get('status')}]
        else:
            intent='unknown';action='collect';reply='Этот момент я не буду придумывать. Зафиксирую вопрос для менеджера. Подскажите ещё, пожалуйста, что именно вам требуется и насколько это срочно?'
    db=SessionLocal()
    try:
        n=int(db.execute(text("SELECT coalesce(max(turn_no),0)+1 FROM telephony_voice_agent_turns WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).scalar() or 1)
        for sp,tx in [('client',textv),('agent',reply)]:
            db.execute(text("""INSERT INTO telephony_voice_agent_turns(account_id,call_id,turn_no,speaker,text_content,intent,action,evidence_json) VALUES(:a,:c,:n,:sp,:t,:i,:ac,CAST(:e AS jsonb)) ON CONFLICT DO NOTHING"""),{'a':account_id,'c':call_id,'n':n,'sp':sp,'t':tx,'i':intent,'ac':action,'e':json.dumps(evidence,ensure_ascii=False)})
        if action=='handoff':db.execute(text("UPDATE telephony_voice_agent_sessions SET state='handoff_requested',handoff_reason=:r WHERE account_id=:a AND call_id=:c"),{'r':textv[:1000],'a':account_id,'c':call_id})
        db.commit()
    finally:db.close()
    return {'status':'ok','reply_text':reply,'intent':intent,'action':action,'evidence':evidence,'transport':'text_brain_only'}

def voice_agent_handoff(account_id:str,call_id:str,reason:str='client_requested')->dict[str,Any]:
    """Creates durable handoff state. Actual SIP transfer remains provider adapter responsibility."""
    db=SessionLocal()
    try:
        sess=db.execute(text("SELECT * FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not sess:return {'status':'not_started'}
        db.execute(text("UPDATE telephony_voice_agent_sessions SET state='handoff_requested',handoff_reason=:r WHERE account_id=:a AND call_id=:c"),{'r':str(reason)[:1000],'a':account_id,'c':call_id});db.commit()
    finally:db.close()
    # Do not fake transfer without provider. Routing layer can pick eligible human devices now.
    try:targets=prepare_call_targets(account_id,call_id)
    except Exception as exc:targets={'status':'pending_provider','error_code':type(exc).__name__[:120]}
    return {'status':'ok','handoff_state':'requested','targets':targets,'provider_transfer':'pending_provider_adapter'}

def voice_agent_transcript(account_id:str,call_id:str)->dict[str,Any]:
    db=SessionLocal()
    try:rows=[dict(x) for x in db.execute(text("SELECT turn_no,speaker,text_content,intent,action,evidence_json,created_at FROM telephony_voice_agent_turns WHERE account_id=:a AND call_id=:c ORDER BY turn_no,id"),{'a':account_id,'c':call_id}).mappings().all()]
    finally:db.close()
    return {'status':'ok','turns':rows}


def voice_agent_qualification(account_id:str,call_id:str)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:r=db.execute(text("SELECT * FROM telephony_voice_agent_qualification WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    fields=['need','product','parameters','geography','timeline','budget','urgency','next_step']; d=dict(r) if r else {'account_id':account_id,'call_id':call_id}
    d['missing']=[x for x in fields if not d.get(x)];d['completeness']=round(100*(len(fields)-len(d['missing']))/len(fields))
    return {'status':'ok','qualification':d}

def update_voice_agent_qualification(account_id:str,call_id:str,payload:dict)->dict[str,Any]:
    allowed=['need','product','parameters','geography','timeline','budget','urgency','qualification','next_step']; vals={k:(str(payload.get(k)).strip()[:1500] if payload.get(k) is not None else None) for k in allowed}
    # Do not invent lead temperature: explicit only, or derive conservatively from collected evidence.
    q=vals.get('qualification')
    if q not in {None,'hot','warm','cold','not_target','qualified','unqualified'}:return {'status':'invalid_qualification'}
    db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_voice_agent_qualification(account_id,call_id,need,product,parameters,geography,timeline,budget,urgency,qualification,next_step) VALUES(:a,:c,:need,:product,:parameters,:geography,:timeline,:budget,:urgency,:qualification,:next_step) ON CONFLICT(account_id,call_id) DO UPDATE SET need=COALESCE(excluded.need,telephony_voice_agent_qualification.need),product=COALESCE(excluded.product,telephony_voice_agent_qualification.product),parameters=COALESCE(excluded.parameters,telephony_voice_agent_qualification.parameters),geography=COALESCE(excluded.geography,telephony_voice_agent_qualification.geography),timeline=COALESCE(excluded.timeline,telephony_voice_agent_qualification.timeline),budget=COALESCE(excluded.budget,telephony_voice_agent_qualification.budget),urgency=COALESCE(excluded.urgency,telephony_voice_agent_qualification.urgency),qualification=COALESCE(excluded.qualification,telephony_voice_agent_qualification.qualification),next_step=COALESCE(excluded.next_step,telephony_voice_agent_qualification.next_step),updated_at=now()"""),{'a':account_id,'c':call_id,**vals});db.commit()
    finally:db.close()
    return voice_agent_qualification(account_id,call_id)

def voice_agent_next_question(account_id:str,call_id:str)->dict[str,Any]:
    q=voice_agent_qualification(account_id,call_id)['qualification'];missing=q.get('missing') or []
    prompts={'need':'Расскажите, пожалуйста, какую задачу хотите решить?','product':'Какой именно товар или услуга вас интересует?','parameters':'Какие основные параметры или объём нужны?','geography':'В каком городе или районе это требуется?','timeline':'К какой дате нужен результат?','budget':'Есть ли ориентир по бюджету?','urgency':'Насколько срочно нужно решить задачу?','next_step':'Как вам удобнее продолжить: расчёт, консультация, замер, встреча или звонок менеджера?'}
    # Budget is deliberately late, after need/product/parameters/timeline.
    order=['need','product','parameters','geography','timeline','urgency','budget','next_step']
    field=next((x for x in order if x in missing),None)
    return {'status':'complete','question':None,'qualification':q} if not field else {'status':'ask','field':field,'question':prompts[field],'qualification':q}

def voice_agent_finalize_qualification(account_id:str,call_id:str)->dict[str,Any]:
    q=voice_agent_qualification(account_id,call_id)['qualification']; present=sum(bool(q.get(x)) for x in ['need','product','parameters','geography','timeline','urgency','next_step'])
    qual=q.get('qualification')
    if not qual:
        # conservative business classification, visible evidence only
        if q.get('need') and q.get('timeline') and q.get('next_step') and str(q.get('urgency') or '').casefold() in {'срочно','высокая','сегодня','как можно скорее'}:qual='hot'
        elif present>=4:qual='warm'
        elif present>=2:qual='cold'
        else:qual='unqualified'
        update_voice_agent_qualification(account_id,call_id,{'qualification':qual});q=voice_agent_qualification(account_id,call_id)['qualification']
    summary='; '.join(f"{k}: {q.get(k)}" for k in ['need','product','parameters','geography','timeline','budget','urgency'] if q.get(k))
    result=voice_agent_complete(account_id,call_id,topic=str(q.get('need') or q.get('product') or ''),summary=summary,qualification=qual,next_action=str(q.get('next_step') or 'Передать менеджеру'))
    return {'status':'ok','qualification':q,'completion':result}


def _voice_extract_qualification(text_value:str)->dict[str,str]:
    """Conservative deterministic extractor; only explicit customer evidence is stored."""
    t=str(text_value or '').strip(); low=t.casefold(); out={}
    # Geography: explicit common prepositions + place phrase, intentionally narrow.
    m=re.search(r'\b(?:в|город|г\.)\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]{2,}(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]{2,})?)',t)
    if m: out['geography']=m.group(1).strip()
    # Timeline/urgency from explicit language only.
    if any(x in low for x in ['сегодня','срочно','как можно скорее','как можно быстрее']):out['urgency']='срочно'
    elif any(x in low for x in ['не срочно','можно не спешить']):out['urgency']='не срочно'
    tm=re.search(r'(?:к|до)\s+(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|(?:понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресень[ея]))',low)
    if tm:out['timeline']=tm.group(0)
    else:
        tm2=re.search(r'(?:через|в течение)\s+\d+\s+(?:дн(?:я|ей)|недел(?:ю|и|ь)|месяц(?:а|ев)?)',low)
        if tm2:out['timeline']=tm2.group(0)
    # Budget only when customer explicitly gives money/limit.
    bm=re.search(r'(?:бюджет|до|около|примерно)\s*(\d[\d\s]{2,})(?:\s*(?:₽|руб|тыс|тысяч))',low)
    if bm:out['budget']=bm.group(0).strip()
    # Next target step.
    for key,words in [('Замер',['замер']),('Расчёт',['расчет','расчёт','посчитать']),('Консультация',['консультац']),('Встреча',['встреч']),('Звонок менеджера',['перезвон','позвонит','звонок менеджер'])]:
        if any(w in low for w in words):out['next_step']=key;break
    # Need/product: keep explicit first substantive request, no semantic invention.
    if any(x in low for x in ['нужен ','нужна ','нужно ','хочу ','интересует ']):
        out['need']=t[:500]
    return out

def voice_agent_ingest_customer_turn(account_id:str,call_id:str,text_value:str)->dict[str,Any]:
    extracted=_voice_extract_qualification(text_value)
    if extracted:update_voice_agent_qualification(account_id,call_id,extracted)
    dialog=voice_agent_dialog_turn(account_id,call_id,text_value)
    q=voice_agent_qualification(account_id,call_id)['qualification']; nxt=voice_agent_next_question(account_id,call_id)
    # Hot evidence triggers durable handoff request; actual provider transfer remains external.
    auto_handoff=None
    if q.get('need') and q.get('timeline') and q.get('next_step') and q.get('urgency')=='срочно':
        update_voice_agent_qualification(account_id,call_id,{'qualification':'hot'})
        auto_handoff=voice_agent_handoff(account_id,call_id,'hot_lead_detected')
    return {'status':'ok','extracted':extracted,'dialog':dialog,'qualification':voice_agent_qualification(account_id,call_id)['qualification'],'next_question':nxt,'auto_handoff':auto_handoff}

def voice_agent_objection_response(account_id:str,call_id:str,text_value:str)->dict[str,Any]:
    low=str(text_value or '').casefold();facts=_copilot_account_facts(account_id,80);e=[]
    if any(x in low for x in ['дорого','цена высокая','дешевле']):
        kind='price';reply='Понимаю. Чтобы сравнение было корректным, уточним объём и важные параметры. Я не буду обещать скидку без подтверждения.'
    elif any(x in low for x in ['подумаю','надо подумать']):
        kind='defer';reply='Конечно. Что именно хочется обдумать: цену, сроки, характеристики или условия? Я зафиксирую это для следующего контакта.'
    elif any(x in low for x in ['не доверя','гарант','отзыв']):
        kind='trust';f=_voice_find_fact(facts,text_value)
        if f and f.get('status')=='confirmed':reply=f"Могу опираться только на подтверждённые данные компании: {f.get('name')}: {f.get('value')}.";e=[{'type':'client_fact','id':f.get('id')}]
        else:reply='Я не буду придумывать гарантии или отзывы. Зафиксирую этот вопрос, чтобы менеджер дал подтверждённую информацию.'
    else:kind='other';reply='Понял. Уточните, пожалуйста, что именно вызывает сомнение — цена, сроки, условия или сам вариант решения?'
    return {'status':'ok','kind':kind,'reply_text':reply,'evidence':e}


def _extract_voice_qualification(text_value:str)->dict[str,str]:
    t=str(text_value or '').strip(); low=t.casefold(); out={}
    if not t:return out
    # Explicit signals only; no invented values.
    geo=re.search(r'(?:в|город|район)\s+([А-ЯЁA-Z][а-яёa-z-]{2,}(?:\s+[А-ЯЁA-Z][а-яёa-z-]{2,})?)',t)
    if geo:out['geography']=geo.group(1).strip()
    money=re.search(r'(?:(?:до|около|примерно|бюджет)\s*)?(\d[\d\s]{2,})\s*(?:₽|руб|рублей)',low)
    if money:out['budget']=re.sub(r'\s+','',money.group(1))+' руб.'
    if any(x in low for x in ['сегодня','срочно','как можно скорее','очень срочно']):out['urgency']='срочно'
    elif any(x in low for x in ['не срочно','можно не спешить']):out['urgency']='низкая'
    tm=re.search(r'(?:к|до)\s+(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)',low)
    if tm:out['timeline']='до '+tm.group(1)
    elif 'в течение недели' in low:out['timeline']='в течение недели'
    elif 'в течение месяца' in low:out['timeline']='в течение месяца'
    elif 'на этой неделе' in low:out['timeline']='на этой неделе'
    # next-step signals are explicit customer intent
    for key,words in [('Замер',['замер','замерщик']),('Расчёт',['расчет','расчёт','посчитать']),('Консультация',['консультац']),('Встреча',['встреч']),('Обратный звонок',['перезвон','позвоните','обратн'])]:
        if any(w in low for w in words):out['next_step']=key;break
    return out

def voice_agent_objection(text_value:str)->dict[str,str]|None:
    low=str(text_value or '').casefold()
    rules=[('price','дорого','Понимаю. Чтобы сравнение было корректным, уточню объём и важные параметры — тогда менеджер сможет предложить подходящий вариант без выдуманной скидки.'),('competitor','у конкур','Понял. Что именно у другого предложения для вас сильнее: цена, срок, комплектация или условия?'),('think','подума','Конечно. Что именно нужно обдумать: цену, сроки, условия или сам вариант решения?'),('trust','не довер','Понимаю. Скажите, что вызывает сомнение — гарантия, опыт, договор, отзывы или результат?')]
    for kind,token,reply in rules:
        if token in low:return {'kind':kind,'reply':reply}
    return None

def voice_agent_ingest_client_turn(account_id:str,call_id:str,client_text:str)->dict[str,Any]:
    """One authoritative client-turn entry: extract explicit facts -> save qualification -> dialog -> next question."""
    extracted=_extract_voice_qualification(client_text)
    if extracted:update_voice_agent_qualification(account_id,call_id,extracted)
    objection=voice_agent_objection(client_text)
    dialog=voice_agent_dialog_turn(account_id,call_id,client_text)
    q=voice_agent_qualification(account_id,call_id)['qualification']
    nxt=voice_agent_next_question(account_id,call_id)
    # Objection response takes priority, but still grounded/no discount promises.
    if objection and dialog.get('action') not in {'handoff'}:
        dialog['reply_text']=objection['reply'];dialog['intent']='objection_'+objection['kind'];dialog['action']='handle_objection'
    return {'status':'ok','extracted':extracted,'objection':objection,'dialog':dialog,'qualification':q,'next_question':nxt}

def voice_agent_hot_handoff_decision(account_id:str,call_id:str)->dict[str,Any]:
    q=voice_agent_qualification(account_id,call_id)['qualification']
    # finalize classification on evidence, then request handoff only for hot + concrete next step.
    if not q.get('qualification'):
        voice_agent_finalize_qualification(account_id,call_id);q=voice_agent_qualification(account_id,call_id)['qualification']
    if q.get('qualification')=='hot' and q.get('next_step'):
        return {'status':'handoff_recommended','reason':'hot_lead_with_next_step','qualification':q,'handoff':voice_agent_handoff(account_id,call_id,'hot_lead')}
    return {'status':'continue','qualification':q}

def voice_agent_semantic_extract(account_id:str,call_id:str,text_value:str,model:str='gpt-5-mini')->dict[str,Any]:
    import os
    raw=str(text_value or '').strip()
    if not raw:return {'status':'empty','extracted':{}}
    base=_extract_voice_qualification(raw); key=os.getenv('OPENAI_API_KEY')
    if not key:return {'status':'fallback_no_ai','extracted':base,'source':'deterministic'}
    facts=_copilot_account_facts(account_id,50); fact_text='\n'.join(f"- {f.get('name')}: {f.get('value')} [{f.get('status')}]" for f in facts[:50])
    prompt=("Ты semantic extractor BORIS Phone. Извлеки ТОЛЬКО явно сказанное клиентом. Ничего не додумывай.\nРеплика: "+raw+"\nПодтвержденные факты компании (только контекст, НЕ данные клиента):\n"+fact_text+"\nВерни строго JSON object с ключами need,product,parameters,geography,timeline,budget,urgency,next_step. Неизвестные поля null. next_step только Замер|Расчёт|Консультация|Встреча|Обратный звонок|null.")
    try:
        import hashlib as _hashlib
        from app.api.campaigns import _ff_guarded_openai_response
        _intent='voice-semantic:%s:%s:%s' % (str(account_id),str(call_id),_hashlib.sha256(raw.encode('utf-8')).hexdigest())
        paid=_ff_guarded_openai_response(str(account_id),'voice_semantic_extract',model,prompt,500,_intent,timeout=60)
        out=str((paid or {}).get('text') or '').strip(); m=re.search(r'\{.*\}',out,re.S)
        if not m:raise ValueError('no_json')
        data=json.loads(m.group(0)); allowed={'need','product','parameters','geography','timeline','budget','urgency','next_step'}
        clean={k:str(v).strip()[:1500] for k,v in data.items() if k in allowed and v not in (None,'',[]) };clean.update(base)
        if clean:update_voice_agent_qualification(account_id,call_id,clean)
        usage=(paid or {}).get('usage') or {};meta={}
        for src,dst in [('input_tokens','input_tokens'),('output_tokens','output_tokens'),('total_tokens','total_tokens')]:
            if usage.get(src) is not None:meta[dst]=int(usage.get(src) or 0)
        return {'status':'ok','extracted':clean,'source':'openai_semantic','model':model,'usage':meta,'cache_hit':bool((paid or {}).get('cache_hit'))}
    except Exception as exc:return {'status':'fallback_ai_error','extracted':base,'source':'deterministic','error_code':type(exc).__name__[:120]}

def voice_agent_sales_state(account_id:str,call_id:str)->dict[str,Any]:
    q=voice_agent_qualification(account_id,call_id)['qualification'];tr=voice_agent_transcript(account_id,call_id)['turns'];ob=[]
    for x in tr:
        if str(x.get('intent') or '').startswith('objection_'):ob.append(str(x['intent']).replace('objection_',''))
    stage='discovery'
    if q.get('need') and q.get('product'):stage='qualification'
    if q.get('next_step'):stage='conversion'
    if q.get('qualification')=='hot' and q.get('next_step'):stage='handoff'
    return {'status':'ok','stage':stage,'qualification':q,'objections':list(dict.fromkeys(ob)),'turns':len(tr),'goal':q.get('next_step') or 'Выяснить следующий целевой шаг'}

def voice_agent_rop_evaluation(account_id:str,call_id:str)->dict[str,Any]:
    state=voice_agent_sales_state(account_id,call_id);q=state['qualification'];tr=voice_agent_transcript(account_id,call_id)['turns'];clients=[x for x in tr if x.get('speaker')=='client'];agents=[x for x in tr if x.get('speaker')=='agent']
    fabricated=sum(1 for x in agents if not x.get('evidence_json') and any(w in str(x.get('text_content') or '').casefold() for w in ['гарантируем','точная цена','точно сделаем']))
    score=min(35,int(q.get('completeness') or 0)*35//100)+(20 if q.get('next_step') else 0)+(15 if q.get('timeline') else 0)+(10 if q.get('geography') else 0)+(10 if clients and agents else 0)+(10 if fabricated==0 else 0)
    gaps=q.get('missing') or [];rec=[]
    if gaps:rec.append('Добрать: '+', '.join(gaps))
    if not q.get('next_step'):rec.append('Закрыть разговор на конкретный следующий шаг')
    if fabricated:rec.append('Убрать неподтвержденные обещания')
    return {'status':'ok','score':max(0,min(score,100)),'stage':state['stage'],'qualification':q.get('qualification'),'objections':state['objections'],'gaps':gaps,'recommendations':rec,'client_turns':len(clients),'agent_turns':len(agents),'fabrication_flags':fabricated}

def voice_agent_afterhours_start(account_id:str,call_id:str)->dict[str,Any]:
    """Explicit after-hours AI entry. Never auto-converts a human-owned call."""
    ah=afterhours_status(account_id)
    if not ah.get('afterhours'):return {'status':'blocked','reason':'working_hours'}
    return voice_agent_start(account_id,call_id,'afterhours_agent')

def voice_agent_account_analytics(account_id:str,days:int=30)->dict[str,Any]:
    ensure_schema();days=max(1,min(int(days),365));db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT c.id,c.agent_mode,c.state,c.talk_duration_sec,c.qualification,c.summary,c.next_action,c.created_at,
          q.completeness,q.need,q.product,q.geography,q.timeline,q.budget,q.urgency,q.next_step,q.qualification AS va_qualification,
          s.mode AS voice_mode,s.state AS voice_state
          FROM telephony_calls c LEFT JOIN telephony_voice_agent_qualification q ON q.account_id=c.account_id AND q.call_id=c.id
          LEFT JOIN telephony_voice_agent_sessions s ON s.account_id=c.account_id AND s.call_id=c.id
          WHERE c.account_id=:a AND c.created_at>=now()-(:d||' days')::interval ORDER BY c.created_at DESC"""),{'a':account_id,'d':str(days)}).mappings().all()
        costs=db.execute(text("""SELECT usage_kind,coalesce(sum(seconds),0) sec,coalesce(sum(ai_cost_rub),0) ai,coalesce(sum(provider_cost_rub),0) provider,coalesce(sum(infra_cost_rub),0) infra FROM telephony_minute_usage WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval GROUP BY usage_kind"""),{'a':account_id,'d':str(days)}).mappings().all()
    finally:db.close()
    calls=[dict(x) for x in rows]; voice=[x for x in calls if x.get('voice_mode') in VOICE_AGENT_MODES]; human=[x for x in calls if not x.get('voice_mode')]
    def agg(items):
        n=len(items);connected=[x for x in items if int(x.get('talk_duration_sec') or 0)>0];hot=sum(1 for x in items if (x.get('va_qualification') or x.get('qualification'))=='hot');nexts=sum(1 for x in items if x.get('next_step') or x.get('next_action'));return {'calls':n,'connected':len(connected),'talk_minutes':round(sum(int(x.get('talk_duration_sec') or 0) for x in items)/60,2),'hot':hot,'with_next_step':nexts,'next_step_rate_pct':round(nexts*100/n,1) if n else 0}
    return {'status':'ok','days':days,'voice_agent':agg(voice),'human':agg(human),'costs':[dict(x) for x in costs],'cost_data_complete':bool(costs),'note':'Conversion here means captured next step, not revenue/deal win.'}

def voice_agent_rop_account_report(account_id:str,days:int=30)->dict[str,Any]:
    an=voice_agent_account_analytics(account_id,days);db=SessionLocal()
    try:ids=[r[0] for r in db.execute(text("SELECT call_id FROM telephony_voice_agent_sessions WHERE account_id=:a AND started_at>=now()-(:d||' days')::interval ORDER BY started_at DESC LIMIT 100"),{'a':account_id,'d':str(max(1,min(days,365)))}).all()]
    finally:db.close()
    evals=[]
    for cid in ids:
        try:evals.append({'call_id':cid,**voice_agent_rop_evaluation(account_id,cid)})
        except Exception:pass
    avg=round(sum(x.get('score',0) for x in evals)/len(evals),1) if evals else None
    gaps={}
    for e in evals:
        for g in e.get('gaps',[]):gaps[g]=gaps.get(g,0)+1
    return {'status':'ok','analytics':an,'evaluated_calls':len(evals),'average_rop_score':avg,'top_gaps':sorted(({'gap':k,'count':v} for k,v in gaps.items()),key=lambda x:-x['count'])[:10],'calls':evals[:30]}

def telephony_conversion_economics(account_id:str,days:int=30)->dict[str,Any]:
    """Real CRM outcomes joined to calls. Never treats next_step as revenue."""
    ensure_schema();days=max(1,min(int(days),365));db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT c.id,c.talk_duration_sec,c.qualification,c.crm_deal_id,c.created_at,s.mode voice_mode,
          d.status deal_status,d.amount_kopeks,d.currency,st.semantic_type,st.name stage_name,
          coalesce(u.ai_cost_rub,0) ai_cost_rub,coalesce(u.provider_cost_rub,0) provider_cost_rub,coalesce(u.infra_cost_rub,0) infra_cost_rub
          FROM telephony_calls c LEFT JOIN telephony_voice_agent_sessions s ON s.account_id=c.account_id AND s.call_id=c.id
          LEFT JOIN boris_crm_deals d ON d.id=c.crm_deal_id LEFT JOIN boris_crm_stages st ON st.id=d.stage_id
          LEFT JOIN (SELECT call_id,sum(ai_cost_rub) ai_cost_rub,sum(provider_cost_rub) provider_cost_rub,sum(infra_cost_rub) infra_cost_rub FROM telephony_minute_usage WHERE account_id=:a GROUP BY call_id) u ON u.call_id=c.id
          WHERE c.account_id=:a AND c.created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':str(days)}).mappings().all()]
    finally:db.close()
    def group(items):
        won=[x for x in items if str(x.get('semantic_type') or '').lower() in {'won','success'} or str(x.get('deal_status') or '').lower() in {'won','success'}];lost=[x for x in items if str(x.get('semantic_type') or '').lower() in {'lost','failed'} or str(x.get('deal_status') or '').lower() in {'lost','failed'}]
        revenue=sum((int(x.get('amount_kopeks') or 0)/100) for x in won if str(x.get('currency') or 'RUB')=='RUB');cost=sum(float(x.get(k) or 0) for x in items for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub']);n=len(items)
        return {'calls':n,'crm_deals':sum(1 for x in items if x.get('crm_deal_id')),'won_deals':len(won),'lost_deals':len(lost),'won_rate_pct':round(len(won)*100/n,1) if n else 0,'won_revenue_rub':round(revenue,2),'tracked_cogs_rub':round(cost,2),'cogs_per_won_rub':round(cost/len(won),2) if won else None}
    voice=[x for x in rows if x.get('voice_mode') in VOICE_AGENT_MODES];human=[x for x in rows if not x.get('voice_mode')]
    return {'status':'ok','days':days,'voice_agent':group(voice),'human':group(human),'data_quality':{'calls':len(rows),'calls_with_crm_deal':sum(1 for x in rows if x.get('crm_deal_id')),'calls_with_cost':sum(1 for x in rows if any(float(x.get(k) or 0)>0 for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub']))},'warning':'Revenue counts only CRM deals explicitly in won/success semantic/status states.'}

def afterhours_recovery_analytics(account_id:str,days:int=30)->dict[str,Any]:
    ensure_schema();db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT s.call_id,s.state,s.confirmed_phone,s.topic,s.next_callback_at,c.crm_deal_id,d.status deal_status,st.semantic_type
          FROM telephony_afterhours_sessions s LEFT JOIN telephony_calls c ON c.id=s.call_id AND c.account_id=s.account_id
          LEFT JOIN boris_crm_deals d ON d.id=c.crm_deal_id LEFT JOIN boris_crm_stages st ON st.id=d.stage_id
          WHERE s.account_id=:a AND s.created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':str(max(1,min(days,365)))}).mappings().all()]
    finally:db.close()
    won=sum(1 for x in rows if str(x.get('semantic_type') or '').lower() in {'won','success'} or str(x.get('deal_status') or '').lower() in {'won','success'})
    captured=sum(1 for x in rows if x.get('confirmed_phone') and x.get('topic'));return {'status':'ok','sessions':len(rows),'captured_callbacks':captured,'crm_deals':sum(1 for x in rows if x.get('crm_deal_id')),'won_deals':won,'recovery_rate_pct':round(captured*100/len(rows),1) if rows else 0,'sale_rate_pct':round(won*100/len(rows),1) if rows else 0}

def phone_owner_economics(account_id:str,days:int=30)->dict[str,Any]:
    conv=telephony_conversion_economics(account_id,days);ah=afterhours_recovery_analytics(account_id,days);ops=voice_agent_account_analytics(account_id,days)
    return {'status':'ok','days':days,'operations':ops,'crm_conversion':conv,'afterhours_recovery':ah,'commercial_readiness':{'crm_attribution_pct':round(conv['data_quality']['calls_with_crm_deal']*100/conv['data_quality']['calls'],1) if conv['data_quality']['calls'] else 0,'cost_attribution_pct':round(conv['data_quality']['calls_with_cost']*100/conv['data_quality']['calls'],1) if conv['data_quality']['calls'] else 0,'pricing_ready':bool(conv['data_quality']['calls']>=30 and conv['data_quality']['calls_with_cost']>=24)}}

def voice_agent_disclosure(account_id:str,call_id:str)->dict[str,Any]:
    cfg=voice_agent_settings(account_id)['settings'];db=SessionLocal()
    try:
        sess=db.execute(text("SELECT disclosure_played_at,state FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not sess:return {'status':'not_started'}
        if not cfg.get('disclosure_required',True):return {'status':'not_required','text':''}
        if sess.get('disclosure_played_at'):return {'status':'already_played','text':''}
        txt=str(cfg.get('disclosure_text') or 'Здравствуйте! Вам отвечает виртуальный помощник BORIS.')[:500]
        db.execute(text("UPDATE telephony_voice_agent_sessions SET disclosure_played_at=now() WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id});db.commit();return {'status':'required','text':txt}
    finally:db.close()

def voice_agent_callback_phone(account_id:str,call_id:str,text_value:str)->dict[str,Any]:
    raw=str(text_value or '');digits=re.sub(r'\D','',raw);phone=normalize_phone(digits) if len(digits)>=10 else ''
    db=SessionLocal()
    try:
        sess=db.execute(text("SELECT callback_phone,callback_phone_confirmed FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not sess:return {'status':'not_started'}
        low=raw.casefold().strip()
        if sess.get('callback_phone') and any(x==low or x in low for x in ['да','верно','правильно','подтверждаю']):
            db.execute(text("UPDATE telephony_voice_agent_sessions SET callback_phone_confirmed=true WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id});db.commit();return {'status':'confirmed','phone':sess['callback_phone'],'confirmed':True}
        if phone:
            db.execute(text("UPDATE telephony_voice_agent_sessions SET callback_phone=:p,callback_phone_confirmed=false WHERE account_id=:a AND call_id=:c"),{'p':phone,'a':account_id,'c':call_id});db.commit();return {'status':'confirm_required','phone':phone,'confirmed':False,'reply_text':f'Проверяю номер: {_spoken_phone(phone)}. Всё верно?'}
        return {'status':'need_phone','confirmed':bool(sess.get('callback_phone_confirmed')),'phone':sess.get('callback_phone')}
    finally:db.close()

def voice_agent_safe_complete(account_id:str,call_id:str,topic:str='',summary:str='',qualification:str='',next_action:str='')->dict[str,Any]:
    db=SessionLocal()
    try:sess=db.execute(text("SELECT callback_phone,callback_phone_confirmed FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    confirmed=(sess or {}).get('callback_phone') if (sess or {}).get('callback_phone_confirmed') else ''
    return voice_agent_complete(account_id,call_id,topic,summary,qualification,next_action,confirmed)

def _voice_persist_exact_turn(account_id:str,call_id:str,client_text:str,reply:str,intent:str,action:str,evidence:list|None=None)->int:
    db=SessionLocal(); evidence=evidence or []
    try:
        n=int(db.execute(text("SELECT coalesce(max(turn_no),0)+1 FROM telephony_voice_agent_turns WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).scalar() or 1)
        for sp,tx in [('client',client_text),('agent',reply)]: db.execute(text("INSERT INTO telephony_voice_agent_turns(account_id,call_id,turn_no,speaker,text_content,intent,action,evidence_json) VALUES(:a,:c,:n,:sp,:t,:i,:ac,CAST(:e AS jsonb))"),{'a':account_id,'c':call_id,'n':n,'sp':sp,'t':tx,'i':intent,'ac':action,'e':json.dumps(evidence,ensure_ascii=False)})
        db.commit();return n
    finally:db.close()

def voice_agent_fallback_ivr(account_id:str,call_id:str,client_text:str)->dict[str,Any]:
    """Provider-neutral deterministic fallback using the same owner-configured autoanswer policy."""
    ah=afterhours_status(account_id);cfg=ah.get('settings') or {};mode=str(cfg.get('autoanswer_mode') or 'off')
    if ah.get('enabled') and mode in {'afterhours','no_answer','always'}:
        r=afterhours_receptionist_turn(account_id,call_id,client_text)
        return {'status':'ok','mode':'fallback_ivr','source':'autoanswer_receptionist','result':r,'greeting':cfg.get('greeting'),'voicemail_text':cfg.get('voicemail_text')}
    low=str(client_text or '').casefold()
    if any(x in low for x in ['оператор','менеджер','человек']):return {'status':'ok','mode':'fallback_ivr','action':'handoff','reply_text':'Передаю запрос менеджеру. Пожалуйста, оставайтесь на линии.'}
    return {'status':'ok','mode':'fallback_ivr','action':'collect_callback','reply_text':'Сейчас интеллектуальный помощник временно недоступен. Назовите номер для обратного звонка, и я передам запрос менеджеру.'}

def voice_agent_lifecycle_turn(account_id:str,call_id:str,client_text:str)->dict[str,Any]:
    """Single authoritative text brain: exact persisted reply == returned/spoken reply."""
    txt=str(client_text or '').strip()
    if not txt:return {'status':'empty'}
    ctx=voice_agent_context(account_id,call_id)
    if ctx.get('status')!='ok':return {'status':'blocked','reason':'session_not_started'}
    sess=ctx['session']
    if sess.get('state')=='handoff_requested':return {'status':'blocked','reason':'handoff_in_progress','reply_text':'Передаю разговор менеджеру. Пожалуйста, оставайтесь на линии.','action':'handoff'}
    # Callback confirmation has priority once a number was requested/captured.
    db=SessionLocal()
    try:ps=db.execute(text("SELECT callback_phone,callback_phone_confirmed FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    digits=re.sub(r'\D','',txt)
    if len(digits)>=10 or ((ps or {}).get('callback_phone') and not (ps or {}).get('callback_phone_confirmed')):
        ph=voice_agent_callback_phone(account_id,call_id,txt)
        if ph.get('status')=='confirm_required': reply=ph['reply_text'];intent='callback_phone';action='confirm_phone'
        elif ph.get('status')=='confirmed': reply='Спасибо, номер подтверждён. Что именно нужно передать менеджеру?';intent='callback_phone';action='collect_topic'
        else: reply='Назовите, пожалуйста, номер для обратного звонка.';intent='callback_phone';action='collect_phone'
        n=_voice_persist_exact_turn(account_id,call_id,txt,reply,intent,action);return {'status':'ok','reply_text':reply,'intent':intent,'action':action,'turn_no':n,'phone':ph,'qualification':voice_agent_qualification(account_id,call_id)['qualification']}
    extracted=_extract_voice_qualification(txt)
    if extracted:update_voice_agent_qualification(account_id,call_id,extracted)
    objection=voice_agent_objection(txt);low=txt.casefold();facts=ctx['facts'];cfg=voice_agent_settings(account_id)['settings'];e=[];handoff_reason=None
    if any(x in low for x in ['оператор','менеджер','человек','соедините','переключите']):intent='handoff';action='handoff';handoff_reason='client_requested';reply='Конечно. Передаю разговор менеджеру. Пожалуйста, оставайтесь на линии.'
    elif objection:intent='objection_'+objection['kind'];action='handle_objection';reply=objection['reply']
    elif any(x in low for x in ['цена','стоимость','сколько стоит','прайс']):
        intent='price';f=_voice_find_fact(facts,txt)
        if cfg.get('allow_prices') and f and f.get('status')=='confirmed':reply=f"По подтверждённой информации компании: {f.get('name')}: {f.get('value')}.";e=[{'type':'client_fact','id':f.get('id'),'status':'confirmed'}];action='continue'
        else:reply='Чтобы не назвать неверную цену, уточню параметры для точного расчёта.';action='collect'
    elif ('?' in txt or any(x in low for x in ['можете','есть ли','делаете','работаете','какие','какой','какая','где','когда','сколько'])) and not _voice_find_fact(facts,txt):
        intent='unknown_question'
        if cfg.get('handoff_on_unknown',True):action='handoff';handoff_reason='unknown_question';reply='Чтобы не дать неверный ответ, передаю этот вопрос менеджеру. Пожалуйста, оставайтесь на линии.'
        else:action='collect';reply='Этого факта нет в подтверждённых данных компании, поэтому я не буду придумывать ответ. Зафиксирую вопрос для менеджера.'
    else:
        nxt=voice_agent_next_question(account_id,call_id);reply=str(nxt.get('question') or 'Что для вас будет следующим удобным шагом: расчёт, консультация, встреча или звонок менеджера?');intent='qualification';action='collect'
    n=_voice_persist_exact_turn(account_id,call_id,txt,reply,intent,action,e)
    if action=='handoff':handoff=voice_agent_handoff(account_id,call_id,handoff_reason or 'policy_handoff')
    else:handoff=None
    q=voice_agent_qualification(account_id,call_id)['qualification'];hot=None
    if action!='handoff' and q.get('need') and q.get('timeline') and q.get('next_step') and q.get('urgency')=='срочно':
        update_voice_agent_qualification(account_id,call_id,{'qualification':'hot'});hot=voice_agent_handoff(account_id,call_id,'hot_lead_detected')
    return {'status':'ok','reply_text':reply,'intent':intent,'action':action,'turn_no':n,'evidence':e,'extracted':extracted,'qualification':voice_agent_qualification(account_id,call_id)['qualification'],'handoff':handoff or hot,'transport':'text_brain_only'}

def voice_agent_inbound_lifecycle_start(account_id:str,call_id:str)->dict[str,Any]:
    """Entry decision for an inbound call before media provider is attached."""
    ah=afterhours_status(account_id);cfg=voice_agent_settings(account_id)['settings']
    mode='afterhours_agent' if ah.get('afterhours') and cfg.get('afterhours_enabled') else 'voice_agent'
    started=voice_agent_start(account_id,call_id,mode)
    if started.get('status')!='ok':return {'status':'fallback','reason':started.get('reason'),'mode':'fallback_ivr'}
    disclosure=voice_agent_disclosure(account_id,call_id)
    return {'status':'ok','mode':mode,'start':started,'disclosure':disclosure,'media':'pending_provider_adapter'}

def _voice_postcall_bridge_afterhours(account_id:str,call_id:str,topic:str,callback_phone:str|None,confirmed:bool,task_id:int|None=None)->dict[str,Any]:
    """Unifies smart after-hours Voice Agent with existing after-hours recovery analytics without creating a second task engine."""
    if not callback_phone or not confirmed:return {'status':'not_confirmed'}
    ah=afterhours_status(account_id); due=ah.get('next_work_at')
    db=SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_afterhours_sessions(account_id,call_id,state,captured_phone,confirmed_phone,topic,next_callback_at,crm_task_id)
          VALUES(:a,:c,'ready',:p,:p,:t,:due,:task) ON CONFLICT(account_id,call_id) DO UPDATE SET state='ready',captured_phone=excluded.captured_phone,
          confirmed_phone=excluded.confirmed_phone,topic=excluded.topic,next_callback_at=excluded.next_callback_at,crm_task_id=COALESCE(excluded.crm_task_id,telephony_afterhours_sessions.crm_task_id),updated_at=now()"""),
          {'a':account_id,'c':call_id,'p':callback_phone,'t':str(topic or '')[:1500],'due':due,'task':task_id});db.commit()
    finally:db.close()
    return {'status':'ok','next_callback_at':due,'task_id':task_id}

def voice_agent_post_call_finalize(account_id:str,call_id:str,force:bool=False)->dict[str,Any]:
    """Idempotent post-call orchestrator: CRM -> safe disposition -> metering -> SLA -> ROP -> economics ledger."""
    ensure_schema();db=SessionLocal()
    try:
        call=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if not call:return {'status':'not_found'}
        if str(call.get('agent_mode') or 'human') not in VOICE_AGENT_MODES:return {'status':'not_voice_agent'}
        if str(call.get('state') or '') not in {'ended','missed','failed','rejected','busy','cancelled','transferred'}:return {'status':'call_not_final','state':call.get('state')}
        old=db.execute(text("SELECT * FROM telephony_voice_agent_postcall WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
        if old and old.get('status')=='completed' and not force:return {'status':'ok','cached':True,'postcall':dict(old)}
        sess=db.execute(text("SELECT * FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    q=voice_agent_qualification(account_id,call_id)['qualification']
    if not q.get('qualification'):
        present=sum(bool(q.get(x)) for x in ['need','product','parameters','geography','timeline','urgency','next_step'])
        qual='hot' if q.get('need') and q.get('timeline') and q.get('next_step') and str(q.get('urgency') or '').casefold() in {'срочно','высокая','сегодня','как можно скорее'} else ('warm' if present>=4 else 'cold' if present>=2 else 'unqualified')
        update_voice_agent_qualification(account_id,call_id,{'qualification':qual});q=voice_agent_qualification(account_id,call_id)['qualification']
    summary='; '.join(f"{k}: {q.get(k)}" for k in ['need','product','parameters','geography','timeline','budget','urgency'] if q.get(k))
    topic=str(q.get('need') or q.get('product') or (sess or {}).get('topic') or '')
    nxt=str(q.get('next_step') or (sess or {}).get('next_action') or 'Передать менеджеру')
    crm_sync=sync_call_to_crm(account_id,call_id)
    completion=voice_agent_safe_complete(account_id,call_id,topic,summary,str(q.get('qualification') or ''),nxt)
    metering=meter_completed_call(account_id,call_id);alerts=minute_alert_guard(account_id)
    rop=voice_agent_rop_evaluation(account_id,call_id)
    db=SessionLocal()
    try:
        usage=db.execute(text("SELECT id,ai_cost_rub,provider_cost_rub,infra_cost_rub FROM telephony_minute_usage WHERE account_id=:a AND call_id=:c ORDER BY id DESC LIMIT 1"),{'a':account_id,'c':call_id}).mappings().first()
        sess2=db.execute(text("SELECT mode,callback_phone,callback_phone_confirmed FROM telephony_voice_agent_sessions WHERE account_id=:a AND call_id=:c"),{'a':account_id,'c':call_id}).mappings().first()
    finally:db.close()
    task_id=(completion.get('crm') or {}).get('task_id') if isinstance(completion.get('crm'),dict) else None
    bridge=None
    if (sess2 or {}).get('mode')=='afterhours_agent': bridge=_voice_postcall_bridge_afterhours(account_id,call_id,topic,(sess2 or {}).get('callback_phone'),bool((sess2 or {}).get('callback_phone_confirmed')),task_id)
    cost_recorded=bool(usage);cost_nonzero=bool(usage and sum(float(usage.get(k) or 0) for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub'])>0)
    outcome={'qualification':q.get('qualification'),'next_step':q.get('next_step'),'crm_sync':crm_sync.get('status'),'completion':completion.get('status'),'metering':metering.get('status'),'alerts':alerts.get('created_thresholds',[]),'afterhours_bridge':bridge.get('status') if isinstance(bridge,dict) else None}
    db=SessionLocal()
    try:
        row=db.execute(text("""INSERT INTO telephony_voice_agent_postcall(account_id,call_id,status,crm_status,crm_task_id,metering_status,usage_id,rop_score,cost_recorded,cost_nonzero,outcome_json,finalized_at)
          VALUES(:a,:c,'completed',:crm,:task,:meter,:uid,:rop,:cr,:cn,CAST(:o AS jsonb),now()) ON CONFLICT(account_id,call_id) DO UPDATE SET status='completed',crm_status=excluded.crm_status,
          crm_task_id=excluded.crm_task_id,metering_status=excluded.metering_status,usage_id=COALESCE(excluded.usage_id,telephony_voice_agent_postcall.usage_id),rop_score=excluded.rop_score,
          cost_recorded=excluded.cost_recorded,cost_nonzero=excluded.cost_nonzero,outcome_json=excluded.outcome_json,finalized_at=now(),updated_at=now() RETURNING *"""),
          {'a':account_id,'c':call_id,'crm':completion.get('status'),'task':task_id,'meter':metering.get('status'),'uid':usage.get('id') if usage else None,'rop':rop.get('score'),'cr':cost_recorded,'cn':cost_nonzero,'o':json.dumps(outcome,ensure_ascii=False)}).mappings().one();db.commit()
    finally:db.close()
    return {'status':'ok','postcall':dict(row),'qualification':q,'crm_sync':crm_sync,'completion':completion,'metering':metering,'alerts':alerts,'rop':rop,'afterhours_bridge':bridge,'cost_status':'recorded_nonzero' if cost_nonzero else 'usage_recorded_cost_pending' if cost_recorded else 'usage_not_recorded'}

def voice_agent_post_call_guardian(account_id:str|None=None,limit:int=50)->dict[str,Any]:
    ensure_schema();db=SessionLocal();params={'l':max(1,min(int(limit),200))};where="c.agent_mode IN ('voice_agent','afterhours_agent') AND c.state IN ('ended','missed','failed','rejected','busy','cancelled','transferred') AND (p.call_id IS NULL OR p.status<>'completed')"
    if account_id:where+=' AND c.account_id=:a';params['a']=account_id
    try:rows=db.execute(text(f"SELECT c.account_id,c.id FROM telephony_calls c LEFT JOIN telephony_voice_agent_postcall p ON p.account_id=c.account_id AND p.call_id=c.id WHERE {where} ORDER BY c.updated_at LIMIT :l"),params).all()
    finally:db.close()
    done=failed=0;errors=[]
    for a,c in rows:
        try:
            r=voice_agent_post_call_finalize(a,c);done+=1 if r.get('status')=='ok' else 0
        except Exception as exc:failed+=1;errors.append({'call_id':c,'error_code':type(exc).__name__[:120]})
    return {'status':'ok','picked':len(rows),'completed':done,'failed':failed,'errors':errors[:10]}

def callback_funnel_link(account_id:str,outbound_call_id:str)->dict[str,Any]:
    """Durably link an answered manager callback to the latest pending inbound call for the same customer."""
    ensure_schema();db=SessionLocal()
    try:
        out=db.execute(text("SELECT * FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':account_id,'c':outbound_call_id}).mappings().first()
        if not out or out.get('direction')!='outbound' or not out.get('answered_at'):return {'status':'not_applicable'}
        phone=normalize_phone(out.get('to_number'));last10=re.sub(r'\D','',phone or '')[-10:]
        if len(last10)<10:return {'status':'no_phone'}
        src=db.execute(text("""SELECT c.id,c.agent_mode,c.crm_deal_id,c.callback_due_at,c.started_at,c.source,c.source_ref FROM telephony_calls c
          WHERE c.account_id=:a AND c.direction='inbound' AND c.id<>:o AND c.started_at<=:at
          AND RIGHT(regexp_replace(COALESCE(c.from_number,''),'\\D','','g'),10)=:p
          AND (c.callback_status IN ('required','completed') OR c.agent_mode IN ('voice_agent','afterhours_agent'))
          ORDER BY (c.callback_status='required') DESC,c.started_at DESC LIMIT 1"""),{'a':account_id,'o':outbound_call_id,'at':out['started_at'],'p':last10}).mappings().first()
        if not src:return {'status':'no_source'}
        row=db.execute(text("""INSERT INTO telephony_callback_links(account_id,source_call_id,callback_call_id,status,callback_answered_at,within_sla,crm_deal_id)
          VALUES(:a,:s,:o,'answered',:ans,CASE WHEN :due IS NULL THEN NULL ELSE :ans<=:due END,COALESCE(CAST(:deal AS bigint),CAST(:odeal AS bigint)))
          ON CONFLICT(account_id,callback_call_id) DO UPDATE SET source_call_id=excluded.source_call_id,status='answered',callback_answered_at=excluded.callback_answered_at,within_sla=excluded.within_sla,crm_deal_id=COALESCE(excluded.crm_deal_id,telephony_callback_links.crm_deal_id),updated_at=now() RETURNING *"""),
          {'a':account_id,'s':src['id'],'o':outbound_call_id,'ans':out['answered_at'],'due':src.get('callback_due_at'),'deal':src.get('crm_deal_id'),'odeal':out.get('crm_deal_id')}).mappings().one()
        db.execute(text("UPDATE telephony_calls SET callback_status='completed',updated_at=now() WHERE account_id=:a AND id=:s"),{'a':account_id,'s':src['id']})
        # Preserve original acquisition attribution across the callback leg, but never overwrite an explicit outbound source.
        db.execute(text("""UPDATE telephony_calls SET source=COALESCE(NULLIF(source,''),:src),source_ref=COALESCE(NULLIF(source_ref,''),:ref),updated_at=now()
          WHERE account_id=:a AND id=:o"""),{'src':src.get('source'),'ref':src.get('source_ref'),'a':account_id,'o':outbound_call_id})
        db.commit()
        return {'status':'ok','source_call_id':src['id'],'callback_call_id':outbound_call_id,'within_sla':row['within_sla'],'source_inherited':bool(src.get('source') or src.get('source_ref'))}
    finally:db.close()

def callback_funnel_analytics(account_id:str,days:int=30)->dict[str,Any]:
    ensure_schema();d=max(1,min(int(days),365));db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT l.*,s.agent_mode source_mode,s.state source_state,s.callback_due_at,d.status deal_status,st.semantic_type,d.amount_kopeks,d.currency
          FROM telephony_callback_links l JOIN telephony_calls s ON s.account_id=l.account_id AND s.id=l.source_call_id
          LEFT JOIN boris_crm_deals d ON d.id=COALESCE(l.crm_deal_id,s.crm_deal_id) LEFT JOIN boris_crm_stages st ON st.id=d.stage_id
          WHERE l.account_id=:a AND l.created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':str(d)}).mappings().all()]
        pending=int(db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a AND callback_status='required' AND started_at>=now()-(:d||' days')::interval"),{'a':account_id,'d':str(d)}).scalar() or 0)
        overdue=int(db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a AND callback_status='required' AND callback_due_at<now() AND started_at>=now()-(:d||' days')::interval"),{'a':account_id,'d':str(d)}).scalar() or 0)
    finally:db.close()
    answered=len(rows);sla=sum(1 for x in rows if x.get('within_sla') is True);won=[x for x in rows if str(x.get('semantic_type') or '').lower() in {'won','success'} or str(x.get('deal_status') or '').lower() in {'won','success'}];rev=sum(int(x.get('amount_kopeks') or 0)/100 for x in won if str(x.get('currency') or 'RUB')=='RUB')
    voice=[x for x in rows if x.get('source_mode') in VOICE_AGENT_MODES]
    return {'status':'ok','days':d,'answered_callbacks':answered,'within_sla':sla,'sla_rate_pct':round(sla*100/answered,1) if answered else 0,'pending_callbacks':pending,'overdue_callbacks':overdue,'voice_agent_callbacks_answered':len(voice),'won_deals_after_callback':len(won),'won_revenue_rub':round(rev,2),'note':'Sale is counted only from canonical CRM won/success state.'}

def phone_funnel_rop_report(account_id:str,days:int=30)->dict[str,Any]:
    calls=voice_agent_account_analytics(account_id,days);cb=callback_funnel_analytics(account_id,days);conv=telephony_conversion_economics(account_id,days);ah=afterhours_recovery_analytics(account_id,days)
    issues=[]
    if cb['overdue_callbacks']:issues.append(f"Просрочено callback SLA: {cb['overdue_callbacks']}")
    if cb['answered_callbacks'] and cb['sla_rate_pct']<80:issues.append(f"Callback в SLA только {cb['sla_rate_pct']}%")
    if calls['voice_agent']['calls'] and calls['voice_agent']['next_step_rate_pct']<60:issues.append('Voice Agent редко фиксирует следующий целевой шаг')
    if conv['data_quality']['calls'] and conv['data_quality']['calls_with_crm_deal']*100/conv['data_quality']['calls']<70:issues.append('Недостаточная привязка телефонных звонков к CRM-сделкам')
    return {'status':'ok','days':days,'calls':calls,'callbacks':cb,'conversion':conv,'afterhours':ah,'attention':issues,'attention_required':bool(issues)}

def phone_outcome_economics(account_id:str,days:int=30)->dict[str,Any]:
    """Outcome economics from formal call mode + canonical CRM. Missing cost/outcome data stays unknown, never zero-success."""
    ensure_schema();d=max(1,min(int(days),365));db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT c.id,c.agent_mode,c.source,c.source_ref,c.crm_deal_id,c.talk_duration_sec,c.qualification call_qualification,
          q.qualification va_qualification,q.next_step,
          d.status deal_status,d.amount_kopeks,d.currency,st.semantic_type,
          coalesce(u.seconds,0) metered_seconds,u.cost_records,u.ai_cost_rub,u.provider_cost_rub,u.infra_cost_rub,
          CASE WHEN cl.callback_call_id IS NOT NULL THEN 1 ELSE 0 END callback_answered,cl.within_sla
          FROM telephony_calls c
          LEFT JOIN telephony_voice_agent_qualification q ON q.account_id=c.account_id AND q.call_id=c.id
          LEFT JOIN boris_crm_deals d ON d.id=c.crm_deal_id LEFT JOIN boris_crm_stages st ON st.id=d.stage_id
          LEFT JOIN (SELECT call_id,sum(seconds) seconds,count(*) cost_records,sum(ai_cost_rub) ai_cost_rub,sum(provider_cost_rub) provider_cost_rub,sum(infra_cost_rub) infra_cost_rub FROM telephony_minute_usage WHERE account_id=:a GROUP BY call_id) u ON u.call_id=c.id
          LEFT JOIN telephony_callback_links cl ON cl.account_id=c.account_id AND cl.source_call_id=c.id
          WHERE c.account_id=:a AND c.created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':str(d)}).mappings().all()]
    finally:db.close()
    def summarize(items):
        n=len(items);costed=[x for x in items if x.get('cost_records')];nonzero=[x for x in costed if sum(float(x.get(k) or 0) for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub'])>0]
        dealed=[x for x in items if x.get('crm_deal_id')];closed=[x for x in dealed if str(x.get('semantic_type') or '').lower() in {'won','success','lost','failed'} or str(x.get('deal_status') or '').lower() in {'won','success','lost','failed'}]
        hot=[x for x in items if (x.get('va_qualification') or x.get('call_qualification'))=='hot'];callbacks=[x for x in items if x.get('callback_answered')];won=[x for x in items if str(x.get('semantic_type') or '').lower() in {'won','success'} or str(x.get('deal_status') or '').lower() in {'won','success'}]
        cost=sum(sum(float(x.get(k) or 0) for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub']) for x in items);revenue=sum(int(x.get('amount_kopeks') or 0)/100 for x in won if str(x.get('currency') or 'RUB')=='RUB')
        cost_cov=round(len(costed)*100/n,1) if n else None;crm_cov=round(len(dealed)*100/n,1) if n else None;outcome_cov=round(len(closed)*100/len(dealed),1) if dealed else None
        trustworthy_cost=n>=10 and len(costed)*100/n>=80
        return {'calls':n,'hot':len(hot),'callbacks_answered':len(callbacks),'crm_deals':len(dealed),'closed_deals':len(closed),'won_deals':len(won),'won_revenue_rub':round(revenue,2),
          'tracked_cogs_rub':round(cost,2) if costed else None,'cost_coverage_pct':cost_cov,'crm_attribution_pct':crm_cov,'closed_outcome_pct':outcome_cov,
          'cost_per_hot_rub':round(cost/len(hot),2) if trustworthy_cost and hot else None,'cost_per_callback_rub':round(cost/len(callbacks),2) if trustworthy_cost and callbacks else None,
          'cost_per_won_rub':round(cost/len(won),2) if trustworthy_cost and won else None,'revenue_to_cogs_ratio':round(revenue/cost,2) if trustworthy_cost and cost>0 and revenue>0 else None,
          'data_status':'ready' if n>=30 and (cost_cov or 0)>=80 and (crm_cov or 0)>=70 and (outcome_cov or 0)>=60 else 'insufficient_data'}
    voice=[x for x in rows if str(x.get('agent_mode') or 'human') in {'voice_agent','afterhours_agent'}];human=[x for x in rows if str(x.get('agent_mode') or 'human')=='human']
    by_source={}
    for x in rows:
        key=str(x.get('source') or 'unknown');by_source.setdefault(key,[]).append(x)
    return {'status':'ok','days':d,'all':summarize(rows),'voice_agent':summarize(voice),'human':summarize(human),'by_source':[{'source':k,**summarize(v)} for k,v in sorted(by_source.items(),key=lambda kv:-len(kv[1]))[:20]],'note':'Unit outcome costs are hidden until cost coverage is sufficient. Revenue requires CRM won/success.'}

def phone_owner_economics(account_id:str,days:int=30)->dict[str,Any]:
    conv=telephony_conversion_economics(account_id,days);ah=afterhours_recovery_analytics(account_id,days);ops=voice_agent_account_analytics(account_id,days);out=phone_outcome_economics(account_id,days);cb=callback_funnel_analytics(account_id,days)
    allq=out['all'];crm=allq.get('crm_attribution_pct');cost=allq.get('cost_coverage_pct');closed=allq.get('closed_outcome_pct')
    ready=bool(allq['calls']>=30 and (cost or 0)>=80 and (crm or 0)>=70 and (closed or 0)>=60)
    blockers=[]
    if allq['calls']<30:blockers.append(f"Нужно минимум 30 звонков, сейчас {allq['calls']}")
    if (cost or 0)<80:blockers.append(f"Покрытие себестоимостью {cost if cost is not None else 'нет данных'}%, нужно ≥80%")
    if (crm or 0)<70:blockers.append(f"CRM-атрибуция {crm if crm is not None else 'нет данных'}%, нужно ≥70%")
    if (closed or 0)<60:blockers.append(f"Закрытые CRM-исходы: {str(closed)+'%' if closed is not None else 'нет данных'}, нужно ≥60%")
    return {'status':'ok','days':days,'operations':ops,'crm_conversion':conv,'afterhours_recovery':ah,'callback_funnel':cb,'outcome_economics':out,
      'commercial_readiness':{'crm_attribution_pct':crm,'cost_attribution_pct':cost,'closed_outcome_pct':closed,'pricing_ready':ready,'data_status':'ready' if ready else 'insufficient_data','blockers':blockers}}


def _verified_phone_channel_spend(account_id:str,days:int)->dict[str,Any]:
    """Reuse the same verified sources as BORIS Revenue Analytics: committed Avito actual ledger + Direct daily facts."""
    ensure_schema(); d=max(1,min(int(days),365)); db=SessionLocal(); avito=None; direct=None
    try:
        raws=db.execute(text("""SELECT value FROM storage WHERE account_id=:a AND key LIKE 'kpi_budget_ledger:%'
          AND substring(key from 'kpi_budget_ledger:(.*)$')::date >= current_date-(CAST(:d AS int)-1)"""),{'a':account_id,'d':d}).scalars().all()
        total=0.0; found=False
        for raw in raws:
            try:
                obj=json.loads(raw or '{}') if isinstance(raw,str) else (raw or {})
                for op in obj.get('operations',[]):
                    if op.get('status')=='committed' and op.get('actual_amount_rub') is not None:
                        total+=float(op.get('actual_amount_rub') or 0); found=True
            except Exception: continue
        avito=round(total,2) if found else None
        try:
            v=db.execute(text("SELECT sum(cost) FROM direct_stats_daily WHERE account_id=:a AND day::date>=current_date-(CAST(:d AS int)-1)"),{'a':account_id,'d':d}).scalar()
            direct=round(float(v),2) if v is not None else None
        except Exception:
            db.rollback(); direct=None
    finally: db.close()
    return {'avito':avito,'yandex_direct':direct,'quality':{'avito':'committed_actual_ledger' if avito is not None else 'no_verified_spend','yandex_direct':'direct_stats_daily' if direct is not None else 'no_synced_spend'}}

def _phone_source_channel(source:str)->str|None:
    x=str(source or '').strip().lower().replace(' ','_')
    if x.startswith('avito') or x.startswith('авито'): return 'avito'
    if x in {'direct','yandex_direct','yandex-direct','яндекс_директ'} or 'yandex' in x and 'direct' in x: return 'yandex_direct'
    return None

def phone_source_roi(account_id:str,days:int=30)->dict[str,Any]:
    """Source→call→CRM→won attribution. ROI is shown only when attributable acquisition spend exists."""
    ensure_schema();d=max(1,min(int(days),365));db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""SELECT c.id,c.source,c.source_ref,c.agent_mode,c.crm_deal_id,
          q.qualification va_qualification,d.status deal_status,d.amount_kopeks,d.currency,st.semantic_type,
          u.cost_records,u.ai_cost_rub,u.provider_cost_rub,u.infra_cost_rub,
          CASE WHEN cl.callback_call_id IS NULL THEN 0 ELSE 1 END callback_answered
          FROM telephony_calls c LEFT JOIN telephony_voice_agent_qualification q ON q.account_id=c.account_id AND q.call_id=c.id
          LEFT JOIN boris_crm_deals d ON d.id=c.crm_deal_id LEFT JOIN boris_crm_stages st ON st.id=d.stage_id
          LEFT JOIN (SELECT call_id,count(*) cost_records,sum(ai_cost_rub) ai_cost_rub,sum(provider_cost_rub) provider_cost_rub,sum(infra_cost_rub) infra_cost_rub FROM telephony_minute_usage WHERE account_id=:a GROUP BY call_id) u ON u.call_id=c.id
          LEFT JOIN telephony_callback_links cl ON cl.account_id=c.account_id AND cl.source_call_id=c.id
          WHERE c.account_id=:a AND c.created_at>=now()-(:d||' days')::interval"""),{'a':account_id,'d':str(d)}).mappings().all()]
    finally:db.close()
    groups={}
    for x in rows:groups.setdefault((str(x.get('source') or 'unknown'),str(x.get('source_ref') or '')),[]).append(x)
    result=[]
    for (source,ref),items in groups.items():
        won=[x for x in items if str(x.get('semantic_type') or '').lower() in {'won','success'} or str(x.get('deal_status') or '').lower() in {'won','success'}]
        hot=[x for x in items if x.get('va_qualification')=='hot'];cb=[x for x in items if x.get('callback_answered')]
        revenue=sum(int(x.get('amount_kopeks') or 0)/100 for x in won if str(x.get('currency') or 'RUB')=='RUB');costed=[x for x in items if x.get('cost_records')]
        phone_cogs=sum(sum(float(x.get(k) or 0) for k in ['ai_cost_rub','provider_cost_rub','infra_cost_rub']) for x in items)
        crm=sum(1 for x in items if x.get('crm_deal_id'));cost_cov=round(len(costed)*100/len(items),1) if items else None;crm_cov=round(crm*100/len(items),1) if items else None
        result.append({'source':source,'source_ref':ref or None,'calls':len(items),'hot':len(hot),'callbacks_answered':len(cb),'crm_deals':crm,'won_deals':len(won),'won_revenue_rub':round(revenue,2),'phone_cogs_rub':round(phone_cogs,2) if costed else None,'cost_coverage_pct':cost_cov,'crm_attribution_pct':crm_cov,'roi_pct':None,'roi_status':'acquisition_spend_not_connected','note':'ROI requires attributable advertising/acquisition spend; phone COGS alone is not ad spend.'})
    result.sort(key=lambda x:(-x['won_revenue_rub'],-x['calls']))
    verified_spend=_verified_phone_channel_spend(account_id,d)
    summaries=[]
    by_source={}
    for x in result:
        key=str(x.get('source') or 'unknown'); acc=by_source.setdefault(key,{'source':key,'calls':0,'hot':0,'won_deals':0,'won_revenue_rub':0.0,'crm_deals':0})
        for k in ('calls','hot','won_deals','crm_deals'): acc[k]+=int(x.get(k) or 0)
        acc['won_revenue_rub']+=float(x.get('won_revenue_rub') or 0)
    for source,acc in by_source.items():
        channel=_phone_source_channel(source); spend=verified_spend.get(channel) if channel else None; revenue=float(acc['won_revenue_rub'])
        acc['won_revenue_rub']=round(revenue,2);acc['ad_spend_rub']=round(float(spend),2) if spend is not None else None
        acc['roi_pct']=round((revenue-float(spend))/float(spend)*100,1) if spend not in (None,0) else None
        acc['roi_status']='ready_verified_channel_spend' if acc['roi_pct'] is not None else ('verified_spend_zero' if spend==0 else 'acquisition_spend_not_connected')
        acc['spend_quality']=(verified_spend.get('quality') or {}).get(channel) if channel else 'unmapped_source'
        summaries.append(acc)
    summaries.sort(key=lambda x:(-float(x.get('won_revenue_rub') or 0),-int(x.get('calls') or 0)))
    return {'status':'ok','days':d,'sources':result,'source_summary':summaries,'verified_spend':verified_spend,'data_status':'ready' if len(rows)>=30 else 'insufficient_data','note':'Per-source_ref ROI stays unknown unless spend is attributable to that ref. Source-level ROI uses only verified Avito/Direct spend.'}

def phone_package_recommendation(account_id:str,days:int=30)->dict[str,Any]:
    """Usage recommendation only. Does not publish/change commercial price."""
    f=minute_package_forecast(account_id);owner=phone_owner_economics(account_id,days);lab=phone_pricing_lab(account_id,days)
    projected=float(f.get('projected_minutes') or 0);recommended=int(f.get('recommended_package_minutes') or TELEPHONY_PACKAGE_MINUTES[0]);ready=bool(owner['commercial_readiness']['pricing_ready'] and lab.get('data_status')=='ready')
    reason=f"Прогноз {projected:.0f} мин за цикл; ближайший пакет {recommended} мин."
    if not ready:reason+=' Цена не рассчитывается автоматически: недостаточно подтверждённых данных экономики.'
    return {'status':'ok','recommended_package_minutes':recommended,'projected_minutes':projected,'pricing_ready':ready,'price_recommendation_rub':None,'reason':reason,'available_packages':list(TELEPHONY_PACKAGE_MINUTES),'commercial_action':'collect_data' if not ready else 'owner_review_required'}

def phone_owner_dashboard(account_id:str)->dict[str,Any]:
    """One owner payload for 30/60/90 comparisons; avoids mixing windows client-side."""
    windows={}
    for d in (30,60,90):
        econ=phone_owner_economics(account_id,d);windows[str(d)]={'economics':econ,'sources':phone_source_roi(account_id,d),'package':phone_package_recommendation(account_id,d)}
    return {'status':'ok','windows':windows,'truth_rules':['Продажа = только CRM won/success','ROI не считается без расходов источника','Цена пакета не публикуется автоматически','Недостаточные данные показываются как insufficient_data']}

CLIENT_MIN_VERSIONS={'web':'0.0.0','windows':'0.1.0','macos':'0.1.0','linux':'0.1.0','android':'0.1.0','ios':'0.1.0'}

CLIENT_RELEASE_CHANNEL={
  'web':{'current_version':'web','install_mode':'pwa','signed':None,'notarized':None},
  'windows':{'current_version':'0.1.0','install_mode':'native','signed':False,'notarized':None},
  'macos':{'current_version':'0.1.0','install_mode':'pwa_or_native','signed':False,'notarized':False},
  'linux':{'current_version':'0.1.0','install_mode':'native','signed':False,'notarized':None},
  'android':{'current_version':'0.1.0','install_mode':'pwa_or_native','signed':False,'notarized':None},
  'ios':{'current_version':'0.1.0','install_mode':'pwa_or_native','signed':False,'notarized':False},
}

def native_release_evidence(platform: str, build_root: Path | None = None) -> dict[str, Any]:
    """Validate durable native release evidence against the current artifact bytes."""
    p=str(platform or '').strip().lower()
    root=(build_root or Path('/root/BORIS/clients/boris-phone')).resolve()
    base={'status':'missing','platform':p,'signed_verified':False,'device_qa_verified':False,'release_verified':False,'artifact_path':None,'sha256':None}
    evidence_path=root/'native-release-evidence.json'
    if not evidence_path.is_file():
        return base
    try:
        doc=json.loads(evidence_path.read_text())
        if int(doc.get('schema') or 0)!=1:
            return {**base,'status':'invalid_schema'}
        entry=(doc.get('platforms') or {}).get(p)
        if not isinstance(entry,dict):
            return base
        rel=str(entry.get('artifact_path') or '').strip()
        expected=str(entry.get('sha256') or '').strip().lower()
        if not rel or not re.fullmatch(r'[0-9a-f]{64}',expected):
            return {**base,'status':'invalid_fields'}
        artifact=(root/rel).resolve()
        try: artifact.relative_to(root)
        except Exception: return {**base,'status':'artifact_path_escape'}
        if not artifact.is_file():
            return {**base,'status':'artifact_missing','artifact_path':rel,'sha256':expected}
        h=hashlib.sha256()
        with artifact.open('rb') as fh:
            for chunk in iter(lambda:fh.read(1024*1024),b''): h.update(chunk)
        actual=h.hexdigest()
        if actual!=expected:
            return {**base,'status':'artifact_sha256_mismatch','artifact_path':rel,'sha256':actual}
        signed=entry.get('signed_verified') is True
        device_qa=entry.get('device_qa_verified') is True
        return {
          'status':'ok','platform':p,'artifact_path':rel,'sha256':actual,
          'signed_verified':signed,'device_qa_verified':device_qa,'release_verified':bool(signed and device_qa),
          'signing_verified_at':entry.get('signing_verified_at'),'device_qa_verified_at':entry.get('device_qa_verified_at'),
          'device_qa_checks':entry.get('device_qa_checks') if isinstance(entry.get('device_qa_checks'),dict) else {},
          'truth':'release_verified requires signing and real-device QA evidence bound to the same current artifact SHA-256',
        }
    except Exception as exc:
        return {**base,'status':'invalid','error_type':type(exc).__name__[:80]}


def client_release_status(platform:str,app_version:str='0.0.0')->dict[str,Any]:
    p=str(platform or 'web').strip().lower()
    known=p in CLIENT_RELEASE_CHANNEL
    item=dict(CLIENT_RELEASE_CHANNEL.get(p,CLIENT_RELEASE_CHANNEL['web']))
    current=str(item.get('current_version') or '0.0.0'); installed=str(app_version or '0.0.0')
    update_available=False if current=='web' else _version_tuple(installed)<_version_tuple(current)
    release_evidence=native_release_evidence(p) if p in {'android','ios'} else None
    blocked_reason=None
    if release_evidence is not None:
        item['signed']=bool(release_evidence.get('signed_verified'))
        item['device_qa_verified']=bool(release_evidence.get('device_qa_verified'))
        item['release_verified']=bool(release_evidence.get('release_verified'))
        download_ready=bool(release_evidence.get('release_verified'))
        if not download_ready:
            ev_status=str(release_evidence.get('status') or 'missing')
            if ev_status!='ok':
                blocked_reason='release_evidence_'+re.sub(r'[^a-z0-9_]+','_',ev_status.lower())[:80]
            elif not item['signed']:
                blocked_reason='signed_artifact_not_verified'
            elif not item['device_qa_verified']:
                blocked_reason='real_device_qa_not_verified'
            else:
                blocked_reason='native_release_not_verified'
    else:
        download_ready=bool(item.get('signed') is True and (p!='macos' or item.get('notarized') is True))
        if p!='web' and not download_ready:
            if item.get('signed') is not True:
                blocked_reason='signed_artifact_not_verified'
            elif p=='macos' and item.get('notarized') is not True:
                blocked_reason='notarization_not_verified'
    download_url=None
    sha256=None
    if download_ready:
        key={'windows':'BORIS_PHONE_WINDOWS_DOWNLOAD_URL','macos':'BORIS_PHONE_MACOS_DOWNLOAD_URL','linux':'BORIS_PHONE_LINUX_DOWNLOAD_URL','android':'BORIS_PHONE_ANDROID_DOWNLOAD_URL','ios':'BORIS_PHONE_IOS_DOWNLOAD_URL'}.get(p)
        hash_key={'windows':'BORIS_PHONE_WINDOWS_SHA256','macos':'BORIS_PHONE_MACOS_SHA256','linux':'BORIS_PHONE_LINUX_SHA256','android':'BORIS_PHONE_ANDROID_SHA256','ios':'BORIS_PHONE_IOS_SHA256'}.get(p)
        candidate=str(os.getenv(key or '') or '').strip() if key else ''
        digest=str(os.getenv(hash_key or '') or '').strip().lower() if hash_key else ''
        allowed_hosts={'boris-ai.pro'} | {x.strip().lower() for x in str(os.getenv('BORIS_PHONE_UPDATE_ALLOWED_HOSTS') or '').split(',') if x.strip()}
        try: parsed=urlparse(candidate); host=(parsed.hostname or '').lower()
        except Exception: parsed=None; host=''
        evidence_digest_ok=bool(release_evidence is None or digest==str(release_evidence.get('sha256') or '').lower())
        if candidate.startswith('https://') and host in allowed_hosts and re.fullmatch(r'[0-9a-f]{64}',digest or '') and evidence_digest_ok:
            download_url=candidate;sha256=digest
        else:
            download_ready=False
            blocked_reason='download_url_or_sha_not_verified'
    if not known:
        blocked_reason='unsupported_platform'
    item.update({'status':'ok','platform':p,'installed_version':installed,'update_available':update_available,
                 'minimum_supported_version':CLIENT_MIN_VERSIONS.get(p,'0.1.0'),
                 'download_ready':download_ready,'download_url':download_url,'sha256':sha256,
                 'blocked_reason':blocked_reason,'release_evidence':release_evidence,
                 'update_policy':'signed_and_device_qa_verified_only' if p in {'android','ios'} else ('signed_verified_only' if p!='web' else 'web_atomic_release'),
                 'pwa_available': p in {'web','windows','macos','linux','android','ios'},
                 'pwa_start_url':'/phone-app?source=pwa'})
    return item


def phone_media_release_diagnostics(provider: str | None, provider_connected: bool, adapter_info: dict[str, Any] | None, media_transport_proven: int = 0) -> dict[str, Any]:
    """Explain why real call audio is or is not production-proven.

    This diagnostic never creates media credentials and never treats a local
    SIP daemon or an adapter capability declaration as proof of real audio.
    """
    p=str(provider or '').strip().lower()
    info=adapter_info if isinstance(adapter_info,dict) else {}
    implemented=bool(info.get('implemented'))
    caps={str(x).strip().lower() for x in (info.get('capabilities') or []) if str(x).strip()}
    adapter_media=bool(caps.intersection({'media_session','webrtc','sip_media'}))
    local_bins=('turnserver','asterisk','freeswitch','janus-gateway','rtpengine','kamailio','opensips')
    local_gateway_tools=[name for name in local_bins if shutil.which(name)]
    try:
        from app.services.phone_media_gateway import gateway_health, turn_runtime_status
        media_gateway=gateway_health()
        turn_runtime=turn_runtime_status(probe=True)
    except Exception:
        media_gateway={'status':'diagnostic_error','runtime_ready':False,'configured':False}
        turn_runtime={'status':'diagnostic_error','configured':False,'runtime_ready':False}
    proven=int(media_transport_proven or 0)>0
    blockers=[]
    if not p: blockers.append('provider_not_selected')
    elif not provider_connected: blockers.append('provider_not_verified')
    elif not implemented: blockers.append('provider_adapter_unimplemented')
    elif not adapter_media: blockers.append('adapter_media_contract_unimplemented')
    if not media_gateway.get('runtime_ready'):
        blockers.append('boris_media_gateway_not_ready')
    if turn_runtime.get('configured') and not turn_runtime.get('runtime_ready'):
        blockers.append('boris_turn_not_ready')
    if not proven:
        blockers.append('real_media_transport_not_proven')
    labels={
      'provider_not_selected':'не выбран оператор телефонии',
      'provider_not_verified':'оператор не прошёл production health-check',
      'provider_adapter_unimplemented':'production adapter оператора не реализован',
      'adapter_media_contract_unimplemented':'adapter не умеет выдавать безопасную короткоживущую media-session',
      'boris_media_gateway_not_ready':'BORIS SIP/WebRTC media gateway не запущен или не прошёл ARI health-check',
      'boris_turn_not_ready':'TURN для сложных мобильных сетей настроен, но runtime не готов',
      'real_media_transport_not_proven':'нет подтверждённого двустороннего RTP на реальном звонке',
    }
    return {
      'status':'ready' if proven and not blockers else 'blocked',
      'provider':p or None,'provider_connected':bool(provider_connected),
      'adapter_implemented':implemented,'adapter_media_contract':adapter_media,
      'adapter_capabilities':sorted(caps),'local_media_gateway_tools':local_gateway_tools,'media_gateway':media_gateway,
      'turn_runtime':turn_runtime,
      'media_transport_proven':proven,
      # Backward-compatible field for existing UI readers; semantics are now stricter.
      'media_session_proven':proven,'blocker_codes':blockers,
      'blockers_human':[labels.get(x,x) for x in blockers],
      'truth':'audio is ready only after bidirectional RTP is reported for the exact authenticated call/device/lease after media activation',
    }


def phone_native_release_environment(build_root: Path | None = None) -> dict[str, Any]:
    """Detect native release blockers on the current BORIS build host.

    This is diagnostic evidence only: presence of credentials/tooling never
    promotes a native release to PASS without signed artifacts and real-device QA.
    Secret values are never returned.
    """
    root=(build_root or Path('/root/BORIS/clients/boris-phone')).resolve()
    boris_root=root.parents[1] if len(root.parents)>1 else Path('/root/BORIS')
    android_app=root/'android/app'
    sdk_raw=str(os.getenv('ANDROID_SDK_ROOT') or os.getenv('ANDROID_HOME') or '').strip()
    sdk=Path(sdk_raw) if sdk_raw else boris_root/'.tools/android-sdk'
    firebase_env=str(os.getenv('BORIS_ANDROID_FIREBASE_JSON') or '').strip()
    firebase_present=bool((android_app/'google-services.json').is_file() or (firebase_env and Path(firebase_env).is_file()))
    keystore=str(os.getenv('BORIS_ANDROID_KEYSTORE') or '').strip()
    android_signing={
      'keystore_file':bool(keystore and Path(keystore).is_file()),
      'store_password':bool(os.getenv('BORIS_ANDROID_STORE_PASSWORD')),
      'key_alias':bool(os.getenv('BORIS_ANDROID_KEY_ALIAS')),
      'key_password':bool(os.getenv('BORIS_ANDROID_KEY_PASSWORD')),
    }
    android_toolchain=bool(shutil.which('java') and (sdk/'platforms/android-35').is_dir() and (sdk/'build-tools/35.0.0').is_dir())
    local_adb=sdk/'platform-tools/adb'
    adb_path=shutil.which('adb') or (str(local_adb) if local_adb.is_file() and os.access(local_adb,os.X_OK) else None)
    android_missing=[]
    if not android_toolchain: android_missing.append('android_sdk_or_java_17_missing')
    if not firebase_present: android_missing.append('firebase_google_services_json')
    if not android_signing['keystore_file']: android_missing.append('release_keystore')
    if not android_signing['store_password']: android_missing.append('release_store_password')
    if not android_signing['key_alias']: android_missing.append('release_key_alias')
    if not android_signing['key_password']: android_missing.append('release_key_password')
    if not adb_path: android_missing.append('adb_unavailable')
    android_missing.append('real_device_qa_not_proven')

    xcodebuild=bool(shutil.which('xcodebuild')); xcodegen=bool(shutil.which('xcodegen'))
    apple_team=bool(os.getenv('BORIS_APPLE_TEAM_ID'))
    ios_missing=[]
    if not xcodebuild: ios_missing.append('xcodebuild')
    if not xcodegen: ios_missing.append('xcodegen')
    if not apple_team: ios_missing.append('apple_team_id')
    # A signing identity must be proven by the macOS preflight; never infer it
    # merely from environment variables on this server.
    ios_missing.append('apple_codesigning_identity_not_proven')
    ios_missing.append('real_device_qa_not_proven')
    return {
      'status':'ok',
      'truth':'host diagnostics only; native release PASS still requires signed artifact plus real-device QA',
      'android':{
        'toolchain_ready':android_toolchain,'firebase_config_present':firebase_present,
        'signing_material_present':all(android_signing.values()),'signing_fields':android_signing,
        'adb_available':bool(adb_path),'adb_source':'path' if shutil.which('adb') else ('local_sdk' if adb_path else None),
        'release_preflight_inputs_ready':bool(android_toolchain and firebase_present and all(android_signing.values())),
        'real_device_qa_proven':False,'missing':android_missing,
      },
      'ios':{
        'toolchain_ready':bool(xcodebuild and xcodegen),'xcodebuild_available':xcodebuild,
        'xcodegen_available':xcodegen,'apple_team_id_present':apple_team,
        'codesigning_identity_proven':False,'archive_preflight_inputs_ready':bool(xcodebuild and xcodegen and apple_team),
        'real_device_qa_proven':False,'missing':ios_missing,
      },
    }


def _version_tuple(value:str)->tuple[int,int,int]:
    nums=[int(x) for x in re.findall(r'\d+',str(value or '0'))[:3]]
    return tuple((nums+[0,0,0])[:3])

def client_compatibility(platform:str,app_version:str,protocol_version:int=3)->dict[str,Any]:
    p=str(platform or 'web').lower();v=str(app_version or '0.0.0');minimum=CLIENT_MIN_VERSIONS.get(p,'0.1.0');supported=_version_tuple(v)>=_version_tuple(minimum);protocol_ok=int(protocol_version or 0)==3
    return {'status':'ok','platform':p,'app_version':v,'minimum_supported_version':minimum,'protocol_version':3,'supported':bool(supported and protocol_ok),'upgrade_required':not supported,'protocol_compatible':protocol_ok,'media_contract':'short_lived_backend_session_only'}

def _manager_action_completion_proven(action_code: str, gates: dict[str, Any]) -> bool:
    """Return True only when runtime evidence proves the manager step happened."""
    code=str(action_code or '').strip()
    checks={
      'open_phone_device': bool(gates.get('device_online')),
      'real_inbound_test': bool(gates.get('real_inbound_seen')),
      'answer_real_call': bool(gates.get('real_answer_seen') and gates.get('real_media_provider')),
      'real_hold_test': bool(gates.get('real_hold_completed')),
      'real_resume_test': bool(gates.get('real_resume_completed')),
      'finish_real_inbound': bool(gates.get('real_call_completed')),
      'real_outbound_test': bool(gates.get('real_outbound_seen') and gates.get('real_outbound_lifecycle')),
    }
    return bool(checks.get(code,False))


def _manager_action_title(action_code: str) -> str:
    return {
      'open_phone_device':'Открыть BORIS Phone для проверки линии',
      'real_inbound_test':'Проверить реальный входящий звонок',
      'answer_real_call':'Ответить на тестовый входящий звонок',
      'real_hold_test':'Проверить удержание звонка',
      'real_resume_test':'Вернуть звонок с удержания',
      'finish_real_inbound':'Завершить тестовый входящий звонок',
      'real_outbound_test':'Проверить реальный исходящий звонок',
    }.get(str(action_code or '').strip(),'Следующий шаг BORIS Phone')


def _manager_action_assignee(db, account_id: str) -> int|None:
    """Assign only when exactly one active manager/employee is proven on this account's device."""
    rows=db.execute(text("""
      SELECT DISTINCT d.user_id
      FROM telephony_devices d
      JOIN users u ON u.id=d.user_id
      WHERE d.account_id=:a
        AND d.user_id IS NOT NULL
        AND d.revoked_at IS NULL
        AND d.last_seen_at IS NOT NULL
        AND d.last_seen_at>=now()-interval '30 days'
        AND lower(COALESCE(u.role,'')) IN ('manager','employee')
        AND COALESCE(u.is_active,true)=true
      ORDER BY d.user_id
      LIMIT 3
    """),{'a':account_id}).scalars().all()
    ids=[int(x) for x in rows if x is not None]
    return ids[0] if len(ids)==1 else None


def phone_manager_action(account_id: str) -> dict[str,Any]|None:
    """Current durable human action for BORIS Phone, if one exists."""
    ensure_schema();db=SessionLocal()
    try:
        row=db.execute(text("""
          SELECT id,account_id,action_code,actor,title,action_text,assigned_user_id,
                 due_at,status,resolution,first_seen_at,last_seen_at,completed_at,metadata_json
          FROM telephony_manager_actions
          WHERE account_id=:a AND status='open'
          ORDER BY id DESC LIMIT 1
        """),{'a':account_id}).mappings().first()
        return dict(row) if row else None
    finally:db.close()


def reconcile_phone_manager_action(account_id: str, readiness: dict[str,Any],
                                   include_synthetic: bool=False) -> dict[str,Any]:
    """Persist exactly one manager step and resolve it only from real readiness evidence.

    This is a technical Phone acceptance action, not a fake CRM deal/contact.
    Repeated minute ticks are idempotent under an advisory lock.
    """
    ensure_schema()
    aid=str(account_id or '').strip()
    if not aid:return {'status':'invalid_account'}
    if not include_synthetic and (re.match(r'^__.*qa',aid,re.I) or re.match(r'^qa[-_]',aid,re.I)):
        return {'status':'synthetic_skipped','account_id':aid}
    ready=readiness if isinstance(readiness,dict) else {}
    primary=ready.get('primary_next_action') if isinstance(ready.get('primary_next_action'),dict) else {}
    actor=str(primary.get('actor') or '').strip().lower()
    code=str(primary.get('code') or '').strip()[:120]
    action_text=str(primary.get('text') or '').strip()[:2000]
    gates=ready.get('telephony_gates') if isinstance(ready.get('telephony_gates'),dict) else {}
    db=SessionLocal()
    try:
        lock_key=f'phone-manager-action|{aid}'
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':lock_key})
        current=db.execute(text("""
          SELECT * FROM telephony_manager_actions
          WHERE account_id=:a AND status='open'
          ORDER BY id DESC LIMIT 1 FOR UPDATE
        """),{'a':aid}).mappings().first()
        closed=None
        if current and (actor!='manager' or str(current.get('action_code') or '')!=code):
            proven=_manager_action_completion_proven(str(current.get('action_code') or ''),gates)
            new_status='completed' if proven else 'superseded'
            resolution='runtime_evidence_proven' if proven else f'primary_action_changed:{code or actor or "none"}'
            closed=int(db.execute(text("""
              UPDATE telephony_manager_actions
              SET status=:s,resolution=:r,completed_at=now(),last_seen_at=now()
              WHERE id=:i AND status='open' RETURNING id
            """),{'s':new_status,'r':resolution[:300],'i':int(current['id'])}).scalar_one())
            current=None
        if actor!='manager' or not code:
            db.commit()
            return {'status':'closed' if closed else 'no_manager_action','account_id':aid,
                    'closed_action_id':closed,'primary_actor':actor or 'none','primary_code':code or None}
        assignee=_manager_action_assignee(db,aid)
        title=_manager_action_title(code)
        fresh_meta={
          'source':'mcn_acceptance_watcher',
          'assignment':'unique_account_device_user' if assignee is not None else 'unassigned',
          'owner_action_required':False,
        }
        metadata=json.dumps(fresh_meta,ensure_ascii=False)
        if current and str(current.get('action_code') or '')==code:
            # Preserve guardian-owned reminder/escalation evidence across the
            # minute-by-minute readiness refresh. Reconcile owns identity and
            # assignment; the overdue guardian owns escalation counters.
            current_meta=dict(current.get('metadata_json') or {})
            current_meta['source']='mcn_acceptance_watcher'
            current_meta['assignment']=fresh_meta['assignment']
            current_meta.setdefault('owner_action_required',False)
            metadata=json.dumps(current_meta,ensure_ascii=False)
            row=db.execute(text("""
              UPDATE telephony_manager_actions
              SET title=:t,action_text=:x,assigned_user_id=:u,last_seen_at=now(),
                  metadata_json=CAST(:m AS jsonb)
              WHERE id=:i
              RETURNING id,account_id,action_code,actor,title,action_text,assigned_user_id,
                        due_at,status,resolution,first_seen_at,last_seen_at,completed_at,metadata_json
            """),{'t':title,'x':action_text,'u':assignee,'m':metadata,'i':int(current['id'])}).mappings().one()
            db.commit()
            return {'status':'existing','action':dict(row),'closed_action_id':closed}
        try:
            row=db.execute(text("""
              INSERT INTO telephony_manager_actions(
                account_id,action_code,actor,title,action_text,assigned_user_id,due_at,status,metadata_json
              ) VALUES(:a,:c,'manager',:t,:x,:u,now()+interval '15 minutes','open',CAST(:m AS jsonb))
              RETURNING id,account_id,action_code,actor,title,action_text,assigned_user_id,
                        due_at,status,resolution,first_seen_at,last_seen_at,completed_at,metadata_json
            """),{'a':aid,'c':code,'t':title,'x':action_text,'u':assignee,'m':metadata}).mappings().one()
        except Exception:
            # The partial unique index is the final cross-process exactly-one boundary.
            db.rollback()
            db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':lock_key})
            row=db.execute(text("""
              SELECT id,account_id,action_code,actor,title,action_text,assigned_user_id,
                     due_at,status,resolution,first_seen_at,last_seen_at,completed_at,metadata_json
              FROM telephony_manager_actions
              WHERE account_id=:a AND status='open'
              ORDER BY id DESC LIMIT 1
            """),{'a':aid}).mappings().first()
            db.commit()
            if row and str(row.get('action_code') or '')==code:
                return {'status':'existing_after_race','action':dict(row),'closed_action_id':closed}
            raise
        db.commit()
        return {'status':'created','action':dict(row),'closed_action_id':closed}
    except Exception:
        db.rollback();raise
    finally:db.close()



def phone_manager_action_guardian(limit: int=100, include_synthetic: bool=False) -> dict[str,Any]:
    """Re-verify overdue Phone manager steps, then notify account without minute-by-minute spam.

    due_at is the next verification/reminder deadline. The first deadline is 15
    minutes after creation; unresolved actions are rechecked at most hourly.
    Real readiness evidence always gets a chance to close/supersede the action
    before any human notification is emitted.
    """
    ensure_schema();limit=max(1,min(int(limit),500));db=SessionLocal()
    try:
        rows=[dict(x) for x in db.execute(text("""
          SELECT id,account_id,action_code,title,action_text,assigned_user_id,due_at,metadata_json
          FROM telephony_manager_actions
          WHERE status='open' AND due_at IS NOT NULL AND due_at<=now()
            AND (:include_synthetic OR NOT (
              lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'
            ))
          ORDER BY due_at,id LIMIT :l
        """),{'include_synthetic':bool(include_synthetic),'l':limit}).mappings().all()]
    finally:db.close()
    if not rows:
        return {'status':'ok','candidates':0,'resolved_by_evidence':0,'notified':0,
                'owner_action_required':False,'owner_action_code':None,'errors':0}
    resolved=0;notified=0;errors=0;owner_required=False
    details=[]
    for candidate in rows:
        aid=str(candidate.get('account_id') or '').strip()
        action_id=int(candidate.get('id') or 0)
        if not aid or action_id<=0:
            errors+=1;continue
        try:
            readiness=phone_client_readiness(aid)
            reconcile=reconcile_phone_manager_action(
                aid,readiness,include_synthetic=include_synthetic
            )
            current=phone_manager_action(aid)
            if not current or int(current.get('id') or 0)!=action_id:
                resolved+=1
                details.append({'account_id':aid,'action_id':action_id,'status':'resolved_by_evidence'})
                continue
            db=SessionLocal()
            try:
                db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),
                           {'k':f'phone-manager-action|{aid}'})
                live=db.execute(text("""
                  SELECT id,action_code,title,action_text,assigned_user_id,due_at,metadata_json
                  FROM telephony_manager_actions
                  WHERE id=:i AND account_id=:a AND status='open'
                  FOR UPDATE
                """),{'i':action_id,'a':aid}).mappings().first()
                if not live or not live.get('due_at') or live.get('due_at')>utcnow():
                    db.rollback();continue
                meta=dict(live.get('metadata_json') or {})
                count=int(meta.get('overdue_notification_count') or 0)+1
                assigned=live.get('assigned_user_id')
                if count==1:
                    prefix='BORIS Phone: менеджеру нужно завершить проверку.'
                elif count==2:
                    prefix='BORIS Phone: проверка всё ещё не завершена.'
                else:
                    prefix='BORIS Phone: проверка просрочена повторно — нужен ответственный сотрудник.'
                text_msg=(f"{prefix} {str(live.get('action_text') or '').strip()}").strip()[:1800]
                from app.services.notification_store import append_notification
                item={
                  'ts':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                  'text':text_msg,
                  'related_results':[{
                    'type':'telephony_manager_action',
                    'action_id':action_id,
                    'action_code':str(live.get('action_code') or ''),
                    'assigned_user_id':int(assigned) if assigned is not None else None,
                    'reminder_no':count,
                  }],
                  'read':False,
                  'severity':'warning' if count<3 else 'action_required',
                }
                append_notification(
                    db,aid,item,
                    idempotency_key=f'telephony_manager_action_overdue:{action_id}:{count}',
                    commit=False,
                )
                meta['overdue_notification_count']=count
                meta['last_overdue_notified_at']=item['ts']
                if count>=3:
                    meta['owner_action_required']=True
                    meta['escalation']='manager_action_unhandled'
                    owner_required=True
                db.execute(text("""
                  UPDATE telephony_manager_actions
                  SET due_at=now()+interval '1 hour',
                      metadata_json=CAST(:m AS jsonb),last_seen_at=now()
                  WHERE id=:i AND account_id=:a AND status='open'
                """),{'i':action_id,'a':aid,'m':json.dumps(meta,ensure_ascii=False)})
                db.commit();notified+=1
                details.append({
                  'account_id':aid,'action_id':action_id,'status':'notified',
                  'reminder_no':count,'assigned':assigned is not None,
                  'owner_action_required':count>=3,
                })
            except Exception:
                db.rollback();raise
            finally:db.close()
        except Exception as exc:
            errors+=1
            details.append({'account_id':aid,'action_id':action_id,'status':'error',
                            'error_type':type(exc).__name__[:120]})
    status='degraded' if errors else 'ok'
    return {'status':status,'candidates':len(rows),'resolved_by_evidence':resolved,
            'notified':notified,'owner_action_required':owner_required,
            'owner_action_code':'manager_action_unhandled' if owner_required else None,
            'errors':errors,'details':details[:100]}


def retire_phone_manager_action(account_id: str, reason: str='telephony_contour_retired') -> dict[str,Any]:
    """Cancel a no-longer-applicable manager action without calling external systems."""
    ensure_schema();aid=str(account_id or '').strip();db=SessionLocal()
    try:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':f'phone-manager-action|{aid}'})
        ids=[int(x) for x in db.execute(text("""
          UPDATE telephony_manager_actions
          SET status='cancelled',resolution=:r,completed_at=now(),last_seen_at=now()
          WHERE account_id=:a AND status='open' RETURNING id
        """),{'a':aid,'r':str(reason or 'telephony_contour_retired')[:300]}).scalars().all()]
        db.commit();return {'status':'ok','account_id':aid,'cancelled':len(ids),'action_ids':ids}
    except Exception:
        db.rollback();raise
    finally:db.close()


def _phone_primary_next_action(*, provider_key: str, provider_connected: bool, provider_has_credentials: bool,
                               telphin_sip: dict[str,Any]|None, online_devices: int,
                               gates: dict[str,bool], recording_policy: str='manual',
                               recording_ai_entitlement: dict[str,Any]|None=None,
                               mcn_readiness: dict[str,Any]|None=None,
                               mcn_transport: dict[str,Any]|None=None,
                               mcn_network: dict[str,Any]|None=None) -> dict[str,Any]:
    """One owner-facing next step. Internal recovery stays assigned to BORIS."""
    p=str(provider_key or '').strip().lower()
    sip=telphin_sip if isinstance(telphin_sip,dict) else {}
    mready=mcn_readiness if isinstance(mcn_readiness,dict) else {}
    mtransport=mcn_transport if isinstance(mcn_transport,dict) else {}
    mnetwork=mcn_network if isinstance(mcn_network,dict) else {}
    if not p:
        return {'code':'connect_telephony','actor':'owner','owner_action_required':True,
                'text':'Подключите телефонный номер и данные линии в настройках телефонии. Остальное BORIS применит и проверит сам.'}
    if p=='mcn':
        blockers={str(x) for x in (mready.get('blockers') or [])}
        if 'mcn_trunk_missing' in blockers:
            return {'code':'mcn_trunk_credentials','actor':'owner','owner_action_required':True,
                    'text':'Добавьте данные SIP-линии: сервер, логин и пароль. BORIS сам сохранит, применит настройки и проверит регистрацию.'}
        if mnetwork and not mnetwork.get('ready'):
            return {'code':'mcn_network_self_heal','actor':'boris','owner_action_required':False,
                    'text':'BORIS автоматически применяет и проверяет сетевые правила SIP/RTP для телефонной линии. Вмешательство владельца не требуется.'}
        if bool(mtransport.get('owner_action_required')):
            return {'code':'mcn_sip_rejected','actor':'owner','owner_action_required':True,
                    'text':'Оператор отклонил SIP-регистрацию. Проверьте только выданные логин и пароль линии; BORIS не будет бесконечно повторять неверные данные.'}
        if 'mcn_trunk_not_verified' in blockers:
            return {'code':'mcn_registration_self_heal','actor':'boris','owner_action_required':False,
                    'text':'BORIS сам проверяет регистрацию телефонной линии и повторяет безопасную регистрацию при временном сбое.'}
        if 'mcn_active_did_missing' in blockers:
            return {'code':'mcn_did_required','actor':'owner','owner_action_required':True,
                    'text':'SIP-линия готова. Добавьте выданный телефонный номер, чтобы BORIS включил реальные входящие звонки.'}
        if not provider_connected:
            return {'code':'mcn_provider_health_check','actor':'boris','owner_action_required':False,
                    'text':'Телефонная линия настроена. BORIS выполняет финальную проверку подключения и сам повторит её при временном сбое.'}
    if not provider_connected:
        if not provider_has_credentials:
            return {'code':'provider_credentials','actor':'owner','owner_action_required':True,
                    'text':'Введите production-доступы оператора. BORIS после сохранения сам выполнит безопасный health-check.'}
        return {'code':'provider_health_check','actor':'boris','owner_action_required':False,
                'text':'BORIS проверяет подключение оператора и сам повторит безопасную проверку при временном сбое.'}
    if p=='telphin':
        sip_status=str(sip.get('status') or 'not_configured')
        if not sip.get('enabled') or sip_status in {'not_configured','waiting_credentials'}:
            return {'code':'telphin_sip_credentials','actor':'owner','owner_action_required':True,
                    'text':'Подключение оператора уже настроено. Добавьте логин и пароль SIP-линии для реального звука.'}
        if sip_status=='rejected':
            return {'code':'telphin_sip_rejected','actor':'owner','owner_action_required':True,
                    'text':'Оператор отклонил SIP-регистрацию. Проверьте логин и пароль линии; BORIS не будет бесконечно повторять неверные данные.'}
        if sip_status!='registered':
            return {'code':'telphin_sip_self_heal','actor':'boris','owner_action_required':False,
                    'text':'BORIS сам применяет и проверяет SIP-конфигурацию телефонной линии. Вмешательство владельца сейчас не требуется.'}
    if online_devices<=0:
        return {'code':'open_phone_device','actor':'manager','owner_action_required':False,
                'text':'Откройте BORIS Phone на устройстве менеджера и оставьте его онлайн для реального тестового звонка.'}
    if not gates.get('real_inbound_seen'):
        return {'code':'real_inbound_test','actor':'manager','owner_action_required':False,
                'text':'Сделайте один реальный входящий звонок на подключённый номер с телефона менеджера/сотрудника. BORIS сам соберёт доказательства дальше.'}
    if not gates.get('real_answer_seen') or not gates.get('real_media_provider'):
        return {'code':'answer_real_call','actor':'manager','owner_action_required':False,
                'text':'Ответьте на реальный звонок в BORIS Phone. Система сама проверит звук и media-session.'}
    if p=='mcn' and not gates.get('mcn_carrier_linked'):
        return {'code':'mcn_carrier_link_reconcile','actor':'boris','owner_action_required':False,
                'text':'BORIS сверяет реальный звонок MCN с Asterisk/carrier identity. Вмешательство владельца не требуется.'}
    if not gates.get('real_hold_completed'):
        return {'code':'real_hold_test','actor':'manager','owner_action_required':False,
                'text':'На этом реальном звонке нажмите «Удержать». BORIS проверит фактическое состояние канала.'}
    if not gates.get('real_resume_completed'):
        return {'code':'real_resume_test','actor':'manager','owner_action_required':False,
                'text':'Верните тот же звонок с удержания. BORIS должен подтвердить resume на реальном канале.'}
    if not gates.get('real_call_completed'):
        return {'code':'finish_real_inbound','actor':'manager','owner_action_required':False,
                'text':'Завершите этот реальный входящий звонок. Дальше BORIS сам проверит запись, CRM и очистку ресурсов.'}
    if not gates.get('recording_received'):
        if recording_policy=='manual':
            return {'code':'recording_policy','actor':'owner','owner_action_required':True,
                    'text':'Для полного E2E выберите политику записи аккаунта или подтвердите уведомление для тестового звонка.'}
        return {'code':'recording_wait','actor':'boris','owner_action_required':False,
                'text':'BORIS ждёт завершение разрешённой записи и сам передаст её в общий контур обработки.'}
    if not gates.get('transcription_completed') or not gates.get('ai_analysis_completed'):
        entitlement=recording_ai_entitlement if isinstance(recording_ai_entitlement,dict) else None
        if entitlement is None:
            return {'code':'recording_ai_entitlement_check','actor':'boris','owner_action_required':False,
                    'text':'BORIS проверяет доступность пакета РОП перед расшифровкой и AI-разбором.'}
        if not bool(entitlement.get('active')):
            return {'code':'rop_entitlement_required','actor':'owner','owner_action_required':True,
                    'text':'Запись получена, но пакет РОП для расшифровки и AI-разбора неактивен или минуты закончились.'}
        return {'code':'recording_ai_processing','actor':'boris','owner_action_required':False,
                'text':'BORIS обрабатывает запись: расшифровка и AI-разбор выполняются автоматически.'}
    if not gates.get('real_crm_linked'):
        return {'code':'real_crm_reconcile','actor':'boris','owner_action_required':False,
                'text':'BORIS связывает завершённый звонок с контактом и сделкой CRM и сам повторит idempotent sync при сбое.'}
    if not gates.get('real_cleanup_completed'):
        return {'code':'real_call_cleanup','actor':'boris','owner_action_required':False,
                'text':'BORIS очищает media-session, цели дозвона и очередь команд после звонка. Владелец ничего не перезапускает.'}
    if not gates.get('real_outbound_seen') or not gates.get('real_outbound_lifecycle'):
        return {'code':'real_outbound_test','actor':'manager','owner_action_required':False,
                'text':'Сделайте один реальный исходящий звонок из BORIS Phone и завершите его после ответа.'}
    if not gates.get('real_inbound_e2e_chain'):
        return {'code':'inbound_full_chain_retry','actor':'boris','owner_action_required':False,
                'text':'BORIS собирает недостающее доказательство полного входящего E2E на одном реальном звонке.'}
    return {'code':'ready','actor':'none','owner_action_required':False,
            'text':'Полный телефонный E2E подтверждён реальными событиями.'}


def phone_client_readiness(account_id:str)->dict[str,Any]:
    """Truthful readiness for both the app shell and the real call contour.

    `readiness_pct` remains the provider-independent application score for
    compatibility. `telephony_readiness_pct` is the owner-facing production
    score and never treats UI/PWA readiness as proof that real calls work.
    """
    ensure_schema();db=SessionLocal()
    try:
        devices=[dict(x) for x in db.execute(text("SELECT id,name,platform,app_version,presence,last_seen_at,revoked_at,capabilities_json,push_kind,push_token_hash FROM telephony_devices WHERE account_id=:a ORDER BY updated_at DESC LIMIT 100"),{'a':account_id}).mappings().all()]
        push_rows=[dict(x) for x in db.execute(text("SELECT status,count(*) n FROM telephony_push_outbox WHERE account_id=:a GROUP BY status"),{'a':account_id}).mappings().all()]
        provider_row=db.execute(text("SELECT provider,status,last_health_at,last_health_status,(credentials_enc IS NOT NULL AND credentials_enc<>'') AS has_credentials FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).mappings().first()
        # Real-E2E proof must cross an authenticated provider boundary. Merely
        # inserting provider/provider_call_id (including local synthetic QA hooks)
        # is not evidence of a real operator call. Inbound requires a normalized
        # provider event with a durable provider event id; outbound requires the
        # successful adapter-origin audit written only after make_call acceptance.
        proof=db.execute(text("""WITH real_calls AS (
          SELECT c.* FROM telephony_calls c
          WHERE c.account_id=:a AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider) AND COALESCE(c.provider_call_id,'')<>''
            AND (
              (c.direction='inbound' AND EXISTS (
                SELECT 1 FROM telephony_events e WHERE e.account_id=c.account_id AND e.call_id=c.id
                  AND e.provider=c.provider AND COALESCE(e.provider_event_id,'')<>''
              ))
              OR
              (c.direction='outbound' AND EXISTS (
                SELECT 1 FROM telephony_audit au WHERE au.account_id=c.account_id AND au.call_id=c.id
                  AND au.provider=c.provider AND au.action='call.outbound.start' AND au.result='ok'
              ))
            )
        ) SELECT
          count(*) AS provider_calls,
          count(*) FILTER (WHERE direction='inbound') AS provider_inbound,
          count(*) FILTER (WHERE direction='outbound') AS provider_outbound,
          count(*) FILTER (WHERE answered_at IS NOT NULL) AS answered,
          count(*) FILTER (WHERE ended_at IS NOT NULL) AS completed
          FROM real_calls"""),{'a':account_id}).mappings().first() or {}
        recording_proof=db.execute(text("""WITH real_calls AS (
          SELECT c.id FROM telephony_calls c
          WHERE c.account_id=:a AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider) AND COALESCE(c.provider_call_id,'')<>''
            AND (
              (c.direction='inbound' AND EXISTS (SELECT 1 FROM telephony_events e WHERE e.account_id=c.account_id AND e.call_id=c.id AND e.provider=c.provider AND COALESCE(e.provider_event_id,'')<>''))
              OR
              (c.direction='outbound' AND EXISTS (SELECT 1 FROM telephony_audit au WHERE au.account_id=c.account_id AND au.call_id=c.id AND au.provider=c.provider AND au.action='call.outbound.start' AND au.result='ok'))
            )
        ) SELECT
          count(*) FILTER (WHERE COALESCE(trim(r.source_url),'')<>'') AS recording_ingress,
          count(*) FILTER (WHERE COALESCE(trim(r.local_path),'')<>'' AND COALESCE(trim(r.checksum),'')<>'') AS recordings,
          count(*) FILTER (WHERE r.transcript_status='done' AND COALESCE(trim(r.transcript),'')<>'') AS transcripts,
          count(*) FILTER (WHERE r.analysis_status='done') AS analyzed
          FROM telephony_recordings r JOIN real_calls c ON c.id=r.call_id
          WHERE r.account_id=:a"""),{'a':account_id}).mappings().first() or {}
        transport_proof=db.execute(text("""WITH real_calls AS (
          SELECT c.id,c.provider FROM telephony_calls c
          WHERE c.account_id=:a AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider) AND COALESCE(c.provider_call_id,'')<>''
            AND (
              (c.direction='inbound' AND EXISTS (SELECT 1 FROM telephony_events e WHERE e.account_id=c.account_id AND e.call_id=c.id AND e.provider=c.provider AND COALESCE(e.provider_event_id,'')<>''))
              OR
              (c.direction='outbound' AND EXISTS (SELECT 1 FROM telephony_audit au WHERE au.account_id=c.account_id AND au.call_id=c.id AND au.provider=c.provider AND au.action='call.outbound.start' AND au.result='ok'))
            )
        ) SELECT
          count(*) FILTER (WHERE au.action='media.session.validated' AND au.result='ok') AS media_sessions_ok,
          count(*) FILTER (WHERE au.action='media.transport.proven' AND au.result='ok') AS media_transport_proven,
          count(*) FILTER (WHERE au.action='call.outbound.start' AND au.result='ok') AS outbound_started_ok
          FROM telephony_audit au JOIN real_calls c ON c.id=au.call_id AND c.provider=au.provider
          WHERE au.account_id=:a"""),{'a':account_id}).mappings().first() or {}
        push_proof=db.execute(text("""WITH real_calls AS (
          SELECT c.id FROM telephony_calls c
          WHERE c.account_id=:a AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider) AND COALESCE(c.provider_call_id,'')<>''
            AND (
              (c.direction='inbound' AND EXISTS (SELECT 1 FROM telephony_events e WHERE e.account_id=c.account_id AND e.call_id=c.id AND e.provider=c.provider AND COALESCE(e.provider_event_id,'')<>''))
              OR
              (c.direction='outbound' AND EXISTS (SELECT 1 FROM telephony_audit au WHERE au.account_id=c.account_id AND au.call_id=c.id AND au.provider=c.provider AND au.action='call.outbound.start' AND au.result='ok'))
            )
        ) SELECT
          count(*) FILTER (WHERE o.status='sent') AS provider_accepted,
          count(*) FILTER (WHERE o.status='sent' AND o.device_received_at IS NOT NULL) AS device_received
          FROM telephony_push_outbox o
          JOIN real_calls c ON c.id=o.call_id
          JOIN telephony_devices d ON d.account_id=o.account_id AND d.id=o.device_id
          WHERE o.account_id=:a AND d.revoked_at IS NULL AND o.sent_push_token_hash=d.push_token_hash
            AND ((lower(d.platform)='ios' AND lower(COALESCE(d.push_kind,''))='apns_voip') OR (lower(d.platform)='android' AND lower(COALESCE(d.push_kind,''))='fcm'))
        """),{'a':account_id}).mappings().first() or {}
        # Full E2E acceptance is one-call evidence, not a sum of unrelated
        # counters. For MCN the same inbound call must additionally be linked
        # to the canonical Asterisk/carrier identity. The acceptance call must
        # carry native receipt, answer/end, validated media, hold -> resume,
        # CRM binding, recording -> STT -> AI and terminal resource cleanup.
        e2e_proof=db.execute(text("""WITH real_inbound AS (
          SELECT c.* FROM telephony_calls c
          WHERE c.account_id=:a
            AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider)
            AND c.direction='inbound' AND c.provider IS NOT NULL
            AND COALESCE(c.provider_call_id,'')<>''
            AND EXISTS (
              SELECT 1 FROM telephony_events e
              WHERE e.account_id=c.account_id AND e.call_id=c.id AND e.provider=c.provider
                AND COALESCE(e.provider_event_id,'')<>''
            )
        ), real_outbound AS (
          SELECT c.* FROM telephony_calls c
          WHERE c.account_id=:a
            AND c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider)
            AND c.direction='outbound' AND c.provider IS NOT NULL
            AND COALESCE(c.provider_call_id,'')<>''
            AND EXISTS (
              SELECT 1 FROM telephony_audit au
              WHERE au.account_id=c.account_id AND au.call_id=c.id AND au.provider=c.provider
                AND au.action='call.outbound.start' AND au.result='ok'
            )
        ) SELECT
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE c.answered_at IS NOT NULL AND c.ended_at IS NOT NULL
              AND (c.provider<>'mcn' OR EXISTS (
                SELECT 1 FROM telephony_call_carrier_links l
                WHERE l.account_id=c.account_id AND l.call_id=c.id AND l.provider='mcn'
                  AND (COALESCE(l.asterisk_uniqueid,'')<>'' OR COALESCE(l.asterisk_linkedid,'')<>'' OR COALESCE(l.carrier_call_id,'')<>'')
              ))
              AND EXISTS (
                SELECT 1 FROM telephony_push_outbox o
                JOIN telephony_devices d ON d.account_id=o.account_id AND d.id=o.device_id
                WHERE o.account_id=c.account_id AND o.call_id=c.id AND o.status='sent'
                  AND o.device_received_at IS NOT NULL AND d.revoked_at IS NULL
                  AND o.sent_push_token_hash=d.push_token_hash
                  AND ((lower(d.platform)='ios' AND lower(COALESCE(d.push_kind,''))='apns_voip')
                    OR (lower(d.platform)='android' AND lower(COALESCE(d.push_kind,''))='fcm'))
              )
              AND EXISTS (
                SELECT 1 FROM telephony_recordings r
                WHERE r.account_id=c.account_id AND r.call_id=c.id
                  AND COALESCE(trim(r.local_path),'')<>'' AND COALESCE(trim(r.checksum),'')<>''
                  AND r.transcript_status='done' AND COALESCE(trim(r.transcript),'')<>''
                  AND r.analysis_status='done'
              )
              AND EXISTS (
                SELECT 1 FROM telephony_audit au
                WHERE au.account_id=c.account_id AND au.call_id=c.id AND au.provider=c.provider
                  AND au.action='media.transport.proven' AND au.result='ok'
              )
              AND EXISTS (
                SELECT 1 FROM telephony_commands cmd
                WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                  AND cmd.command='hold' AND cmd.status='done'
              )
              AND EXISTS (
                SELECT 1 FROM telephony_commands cmd
                WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                  AND cmd.command='resume' AND cmd.status='done'
              )
              AND c.crm_contact_id IS NOT NULL AND c.crm_deal_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM telephony_media_sessions m
                WHERE m.account_id=c.account_id AND m.call_id=c.id
                  AND m.status IN ('closed','expired') AND m.cleaned_at IS NOT NULL
              )
              AND NOT EXISTS (
                SELECT 1 FROM telephony_media_sessions m
                WHERE m.account_id=c.account_id AND m.call_id=c.id
                  AND m.status IN ('active','cleanup_pending','await_expiry')
              )
              AND NOT EXISTS (
                SELECT 1 FROM telephony_call_targets t
                WHERE t.account_id=c.account_id AND t.call_id=c.id
                  AND t.status IN ('ringing','answered')
              )
              AND NOT EXISTS (
                SELECT 1 FROM telephony_commands cmd
                WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                  AND cmd.status NOT IN ('done','failed')
              )
          ) AS inbound_full_chain,
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE EXISTS (SELECT 1 FROM telephony_commands cmd
                          WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                            AND cmd.command='hold' AND cmd.status='done')
          ) AS inbound_hold_completed,
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE EXISTS (SELECT 1 FROM telephony_commands cmd
                          WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                            AND cmd.command='resume' AND cmd.status='done')
          ) AS inbound_resume_completed,
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE c.crm_contact_id IS NOT NULL AND c.crm_deal_id IS NOT NULL
          ) AS inbound_crm_linked,
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE c.provider='mcn' AND EXISTS (
              SELECT 1 FROM telephony_call_carrier_links l
              WHERE l.account_id=c.account_id AND l.call_id=c.id AND l.provider='mcn'
                AND (COALESCE(l.asterisk_uniqueid,'')<>'' OR COALESCE(l.asterisk_linkedid,'')<>'' OR COALESCE(l.carrier_call_id,'')<>'')
            )
          ) AS mcn_carrier_linked,
          EXISTS (
            SELECT 1 FROM real_inbound c
            WHERE c.ended_at IS NOT NULL
              AND EXISTS (SELECT 1 FROM telephony_media_sessions m
                          WHERE m.account_id=c.account_id AND m.call_id=c.id
                            AND m.status IN ('closed','expired') AND m.cleaned_at IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM telephony_media_sessions m
                              WHERE m.account_id=c.account_id AND m.call_id=c.id
                                AND m.status IN ('active','cleanup_pending','await_expiry'))
              AND NOT EXISTS (SELECT 1 FROM telephony_call_targets t
                              WHERE t.account_id=c.account_id AND t.call_id=c.id
                                AND t.status IN ('ringing','answered'))
              AND NOT EXISTS (SELECT 1 FROM telephony_commands cmd
                              WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                                AND cmd.status NOT IN ('done','failed'))
          ) AS inbound_cleanup_completed,
          EXISTS (
            SELECT 1 FROM real_outbound c
            WHERE c.answered_at IS NOT NULL AND c.ended_at IS NOT NULL
          ) AS outbound_full_lifecycle
        """),{'a':account_id}).mappings().first() or {}
        push_sent=int((push_proof or {}).get('provider_accepted') or 0)
        push_received=int((push_proof or {}).get('device_received') or 0)
    finally:db.close()
    build_root=Path('/root/BORIS/clients/boris-phone')
    win_qa=build_root/'desktop/release-win-qa/BORIS-Phone-0.1.0-win-x64.exe'
    android_aab=build_root/'android/app/build/outputs/bundle/release/app-release.aab'
    android_debug=build_root/'android/app/build/outputs/apk/debug/app-debug.apk'
    def _latest_source_mtime(root:Path, excluded_parts:set[str]|None=None)->float:
        excluded_parts=set(excluded_parts or set())
        allowed={'.ts','.tsx','.js','.json','.kt','.java','.xml','.gradle','.kts','.swift','.plist','.sh','.properties'}
        special={'package.json','package-lock.json','settings.gradle','gradlew'}
        latest=0.0
        if not root.is_dir(): return latest
        for base,dirs,files in os.walk(root):
            dirs[:]=[d for d in dirs if d not in excluded_parts]
            for name in files:
                src=Path(base)/name
                try:
                    if src.suffix.lower() not in allowed and name not in special: continue
                    st=src.stat()
                    latest=max(latest,max(st.st_mtime,st.st_ctime))
                except Exception:
                    continue
        return latest
    def _artifact_evidence(path:Path, source_root:Path|None=None, excluded_parts:set[str]|None=None, **extra):
        exists=path.is_file()
        artifact_mtime=path.stat().st_mtime if exists else 0.0
        source_mtime=_latest_source_mtime(source_root,excluded_parts) if source_root is not None else 0.0
        fresh=bool(exists and (source_mtime<=0.0 or artifact_mtime+0.001>=source_mtime))
        item={
          'built':exists,'bytes':path.stat().st_size if exists else 0,'sha256':None,
          'artifact_mtime':artifact_mtime or None,'source_latest_mtime':source_mtime or None,
          'fresh_against_source':fresh,'stale':bool(exists and not fresh),
        }
        if exists:
            import hashlib
            h=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
            item['sha256']=h.hexdigest()
        item.update(extra); return item
    desktop_source_excludes={'node_modules','build','dist','release','release-win','release-win-qa','.cache'}
    android_source_excludes={'build','.gradle'}
    linux_appimage=build_root/'desktop/release/BORIS-Phone-0.1.0-linux-x86_64.AppImage'
    build_evidence={
      'linux_appimage':_artifact_evidence(linux_appimage,build_root/'desktop',desktop_source_excludes,signed=False,qa_only=False),
      'windows_unsigned_nsis':_artifact_evidence(win_qa,build_root/'desktop',desktop_source_excludes,signed=False,qa_only=True),
      'android_debug_apk':_artifact_evidence(android_debug,build_root/'android',android_source_excludes,qa_only=True,device_qa=False),
      'android_release_aab':_artifact_evidence(android_aab,build_root/'android',android_source_excludes,signed=False,device_qa=False),
      'ios_source_release_contract':{'validated':(build_root/'ios/scripts/source-release-qa.sh').is_file(),'signed':False,'archive_built':False,'device_qa':False},
    }
    release_integrity={'status':'missing','artifacts':0,'generated_at':None}
    manifest=build_root/'release-artifacts.json'
    if manifest.is_file():
        try:
            import hashlib as _hashlib_release
            m=json.loads(manifest.read_text()); arts=m.get('artifacts') or []
            verified=[]; invalid=[]
            root_resolved=build_root.resolve()
            for art in arts:
                rel=str((art or {}).get('path') or '').strip()
                expected_sha=str((art or {}).get('sha256') or '').strip().lower()
                expected_bytes=(art or {}).get('bytes')
                reason=''
                if not rel or not re.fullmatch(r'[0-9a-f]{64}',expected_sha):
                    reason='manifest_fields_invalid'
                else:
                    candidate=(build_root/rel).resolve()
                    try: candidate.relative_to(root_resolved)
                    except Exception: reason='artifact_path_escape'
                    if not reason and not candidate.is_file(): reason='artifact_missing'
                    if not reason and expected_bytes is not None and int(expected_bytes)!=candidate.stat().st_size: reason='artifact_size_mismatch'
                    if not reason:
                        h=_hashlib_release.sha256()
                        with candidate.open('rb') as fh:
                            for chunk in iter(lambda:fh.read(1024*1024),b''): h.update(chunk)
                        if h.hexdigest()!=expected_sha: reason='artifact_sha256_mismatch'
                if reason: invalid.append({'path':rel,'reason':reason})
                else: verified.append(rel)
            release_integrity={'status':'ok' if bool(arts) and not invalid and len(verified)==len(arts) else 'invalid',
                'artifacts':len(arts),'sha256_verified_entries':len(verified),'generated_at':m.get('generated_at'),
                'truth':m.get('truth') or {},'invalid_entries':invalid}
        except Exception as exc:
            release_integrity={'status':'invalid','artifacts':0,'generated_at':None,'invalid_entries':[{'path':'release-artifacts.json','reason':type(exc).__name__}]}
    def _device_live(d:dict[str,Any])->bool:
        last=d.get('last_seen_at')
        return bool(last and not d.get('revoked_at') and str(d.get('presence') or '') not in {'offline',''} and (utcnow()-last).total_seconds()<90)
    platforms={p:{'registered':0,'compatible':0,'online':0} for p in ['web','windows','macos','android','ios']}
    for d in devices:
        p=str(d.get('platform') or 'web').lower(); platforms.setdefault(p,{'registered':0,'compatible':0,'online':0}); platforms[p]['registered']+=1
        comp=client_compatibility(p,str(d.get('app_version') or '0.0.0')); platforms[p]['compatible']+=1 if comp['supported'] else 0
        platforms[p]['online']+=1 if _device_live(d) else 0
    # Application readiness is evidence-backed, not aspirational. Source/build
    # gates must fall back to false if files/artifacts disappear or integrity is
    # invalid; real telephony remains a separate hard-gated score below.
    frontend_root=Path('/root/BORIS/frontend')
    desktop_root=build_root/'desktop'
    android_root=build_root/'android'
    ios_root=build_root/'ios'
    web_source=(frontend_root/'app/phone-app/page.tsx').is_file() and (frontend_root/'app/phone-app/phone-app.css').is_file()
    desktop_source=(desktop_root/'src/main.ts').is_file() and (desktop_root/'src/preload.ts').is_file()
    android_source=(android_root/'app/build.gradle.kts').is_file() and (android_root/'scripts/source-build-qa.sh').is_file()
    ios_source=(ios_root/'BORISPhone/BORISPhoneApp.swift').is_file() and (ios_root/'scripts/source-release-qa.sh').is_file()
    linux_pkg=bool(build_evidence['linux_appimage']['built'] and build_evidence['linux_appimage']['fresh_against_source'])
    shared_protocol=(build_root/'shared/protocol.ts').is_file()
    gates={
      'backend_core':Path('/root/BORIS/backend/app/api/telephony.py').is_file() and Path('/root/BORIS/backend/app/services/telephony_core.py').is_file(),
      'web_phone':web_source,'desktop_source':desktop_source,'desktop_linux_package':linux_pkg,
      'windows_source':desktop_source,'macos_source':desktop_source,'android_source':android_source,'ios_source':ios_source,
      'windows_unsigned_nsis_built':bool(build_evidence['windows_unsigned_nsis']['built'] and build_evidence['windows_unsigned_nsis']['fresh_against_source']),
      'android_debug_apk_built':bool(build_evidence['android_debug_apk']['built'] and build_evidence['android_debug_apk']['fresh_against_source']),
      'android_release_source_aab_built':bool(build_evidence['android_release_aab']['built'] and build_evidence['android_release_aab']['fresh_against_source']),
      'account_isolation':True,'version_gate':bool(CLIENT_MIN_VERSIONS),'native_call_control_contract':shared_protocol,
      'release_checksum_manifest':release_integrity.get('status')=='ok' and int(release_integrity.get('artifacts') or 0)>0,
      'apple_pwa_installable':web_source,'pwa_standalone_launch':web_source,'pwa_network_authoritative':web_source,'pwa_update_lifecycle':web_source,
      'device_reconnect_flow':shared_protocol,'audio_device_preferences':web_source,
      'windows_signed_installer':False,'macos_native_signed_notarized':False,'android_release_aab_device_qa':False,'ios_native_signed_archive_device_qa':False,
      'real_media_provider':False
    }
    # Application readiness deliberately excludes real_media_provider, as requested by owner.
    app_keys=[k for k in gates if k!='real_media_provider'];passed=sum(1 for k in app_keys if gates[k]);pct=round(passed*100/len(app_keys),1)
    remaining=[k for k in app_keys if not gates[k]]
    push_status={str(x.get('status') or 'unknown'):int(x.get('n') or 0) for x in push_rows}
    push_tokens={'ios_apns_voip':sum(1 for x in devices if x.get('push_token_hash') and str(x.get('push_kind') or '').lower()=='apns_voip' and not x.get('revoked_at')),
                 'android_fcm':sum(1 for x in devices if x.get('push_token_hash') and str(x.get('push_kind') or '').lower()=='fcm' and not x.get('revoked_at'))}
    push_health={'tokens':push_tokens,'outbox':push_status,
                 'credentials_configured':{'apns':bool(os.getenv('BORIS_APNS_TEAM_ID') and os.getenv('BORIS_APNS_KEY_ID') and os.getenv('BORIS_APNS_PRIVATE_KEY') and os.getenv('BORIS_APNS_BUNDLE_ID')),
                                           'fcm':bool(os.getenv('BORIS_FCM_PROJECT_ID') and os.getenv('BORIS_FCM_SERVICE_ACCOUNT_JSON'))},
                 'truth':'configured means credentials exist; it does not mean a real device delivery was proven'}
    distribution={
      'web':{'mode':'web','usable_without_provider':True,'native_release_ready':False},
      'windows':{'mode':'pwa_or_native','pwa_ready':True,'native_release_ready':False,'install_hint':'Chrome/Edge → Установить приложение'},
      'macos':{'mode':'pwa_or_native','pwa_ready':True,'native_release_ready':False,'install_hint':'Safari → Поделиться → Добавить в Dock'},
      'android':{'mode':'pwa_or_native','pwa_ready':True,'native_release_ready':False,'install_hint':'Chrome → Установить приложение'},
      'ios':{'mode':'pwa_or_native','pwa_ready':True,'native_release_ready':False,'install_hint':'Safari → Поделиться → На экран Домой'},
    }
    provider_status_value=str((provider_row or {}).get('status') or '')
    provider_key=str((provider_row or {}).get('provider') or '').strip().lower()
    from app.services.telephony_adapters import adapter_status as _phone_adapter_status
    adapter_info=_phone_adapter_status(provider_key) if provider_key else {'available':False,'implemented':False,'capabilities':[]}
    adapter_ready=bool(provider_key and adapter_info.get('implemented'))
    health_at=(provider_row or {}).get('last_health_at')
    health_fresh=bool(health_at and (utcnow()-health_at).total_seconds()<=24*3600)
    provider_connected=bool(provider_status_value=='connected' and str((provider_row or {}).get('last_health_status') or '')=='ok' and (provider_row or {}).get('has_credentials') and adapter_ready and health_fresh)
    online_devices=sum(1 for d in devices if _device_live(d))
    native_push_tokens=int(push_tokens['ios_apns_voip'] or 0)+int(push_tokens['android_fcm'] or 0)
    call_proof={k:int((proof or {}).get(k) or 0) for k in ['provider_calls','provider_inbound','provider_outbound','answered','completed']}
    rec_proof={k:int((recording_proof or {}).get(k) or 0) for k in ['recording_ingress','recordings','transcripts','analyzed']}
    transport_evidence={k:int((transport_proof or {}).get(k) or 0) for k in ['media_sessions_ok','media_transport_proven','outbound_started_ok']}
    acceptance_evidence={k:bool((e2e_proof or {}).get(k)) for k in [
      'inbound_full_chain','inbound_hold_completed','inbound_resume_completed',
      'inbound_crm_linked','mcn_carrier_linked','inbound_cleanup_completed','outbound_full_lifecycle'
    ]}
    media_diagnostics=phone_media_release_diagnostics(provider_key,provider_connected,adapter_info,transport_evidence['media_transport_proven'])
    production_gates={
      'provider_selected':bool((provider_row or {}).get('provider')),
      'provider_connected':provider_connected,
      'device_online':online_devices>0,
      'native_push_registered':native_push_tokens>0,
      'native_push_provider_accepted':int(push_sent or 0)>0,
      'native_push_delivered':int(push_received or 0)>0,
      'real_provider_call_seen':call_proof['provider_calls']>0,
      'real_inbound_seen':call_proof['provider_inbound']>0,
      'real_outbound_seen':call_proof['provider_outbound']>0,
      'real_answer_seen':call_proof['answered']>0,
      'real_call_completed':call_proof['completed']>0,
      'recording_received':rec_proof['recordings']>0,
      'transcription_completed':rec_proof['transcripts']>0,
      'ai_analysis_completed':rec_proof['analyzed']>0,
      # Real media requires bidirectional RTP evidence from the exact authenticated
      # call/device/lease after Asterisk activation. Issuing a safe media contract alone is not proof.
      'real_media_provider':transport_evidence['media_transport_proven']>0,
      'real_hold_completed':acceptance_evidence['inbound_hold_completed'],
      'real_resume_completed':acceptance_evidence['inbound_resume_completed'],
      'real_crm_linked':acceptance_evidence['inbound_crm_linked'],
      'real_cleanup_completed':acceptance_evidence['inbound_cleanup_completed'],
      'real_inbound_e2e_chain':acceptance_evidence['inbound_full_chain'],
      'real_outbound_lifecycle':acceptance_evidence['outbound_full_lifecycle'],
    }
    if provider_key=='mcn':
        production_gates['mcn_carrier_linked']=acceptance_evidence['mcn_carrier_linked']
    production_passed=sum(1 for v in production_gates.values() if v)
    production_pct=round(production_passed*100/len(production_gates),1)
    production_remaining=[k for k,v in production_gates.items() if not v]
    _gate_labels={
      'provider_selected':'не выбран оператор телефонии',
      'provider_connected':'не подтверждено подключение оператора',
      'device_online':'нет активного устройства BORIS Phone',
      'native_push_registered':'не зарегистрирован push-токен реального телефона',
      'native_push_provider_accepted':'APNs/FCM ещё не подтвердил приём push',
      'native_push_delivered':'приложение на реальном телефоне ещё не подтвердило получение push',
      'real_provider_call_seen':'нет подтверждённого звонка через подтверждённый контур оператора',
      'real_inbound_seen':'не проверен реальный входящий звонок',
      'real_outbound_seen':'не проверен реальный исходящий звонок',
      'real_answer_seen':'не доказан ответ на реальный звонок',
      'real_call_completed':'не доказано корректное завершение реального звонка',
      'recording_received':'не получена реальная запись разговора',
      'transcription_completed':'не завершена расшифровка реального звонка',
      'ai_analysis_completed':'не завершён AI-разбор реального звонка',
      'real_media_provider':'не подтверждён реальный SIP/WebRTC media transport',
      'real_hold_completed':'на реальном входящем звонке не подтверждено удержание',
      'real_resume_completed':'на том же реальном звонке не подтверждён возврат с удержания',
      'real_crm_linked':'реальный звонок не привязан к контакту и сделке CRM',
      'real_cleanup_completed':'после реального звонка не подтверждена очистка media/targets/commands',
      'mcn_carrier_linked':'реальный звонок не связан с Asterisk и линией оператора',
      'real_inbound_e2e_chain':'нет одного реального входящего звонка с полной цепочкой: линия → приложение → ответ/звук → удержание/возврат → завершение → запись → расшифровка → AI → CRM → очистка',
      'real_outbound_lifecycle':'нет одного реального исходящего звонка с подтверждённым start → answer → end',
    }
    production_remaining_human=[_gate_labels.get(k,k) for k in production_remaining]
    try:
        recording_policy=str((recording_policy_settings(account_id).get('settings') or {}).get('policy') or 'manual')
    except Exception:
        recording_policy='manual'
    telphin_sip_readiness=None
    if provider_key=='telphin':
        try:
            from app.services.telphin_sip_trunk import trunk_status as _telphin_trunk_status
            telphin_sip_readiness=_telphin_trunk_status(account_id)
        except Exception:
            telphin_sip_readiness={'status':'unavailable','enabled':False,'ready':False}
    mcn_readiness_evidence=None
    mcn_transport_evidence=None
    mcn_network_evidence=None
    if provider_key=='mcn':
        try:
            from app.services.mcn_core import readiness as _mcn_readiness
            mcn_readiness_evidence=_mcn_readiness(account_id)
        except Exception as exc:
            mcn_readiness_evidence={'status':'unavailable','ready':False,'blockers':['mcn_readiness_unavailable'],'error_type':type(exc).__name__[:120]}
        try:
            from app.services.asterisk_gateway import mcn_account_transport_health as _mcn_transport_health
            mcn_transport_evidence=_mcn_transport_health(account_id,False)
        except Exception as exc:
            mcn_transport_evidence={'status':'unavailable','ready':False,'owner_action_required':False,'error_type':type(exc).__name__[:120]}
        try:
            from app.services.mcn_network_guard import mcn_firewall_health as _mcn_firewall_health
            mcn_network_evidence=_mcn_firewall_health()
        except Exception as exc:
            mcn_network_evidence={'status':'unavailable','ready':False,'owner_action_required':False,'error_type':type(exc).__name__[:120]}
    recording_ai_entitlement=None
    if production_gates.get('recording_received') and (
        not production_gates.get('transcription_completed') or not production_gates.get('ai_analysis_completed')
    ):
        try:
            from app.api.calltracking import get_rop_minutes
            recording_ai_entitlement=get_rop_minutes(account_id)
        except Exception:
            recording_ai_entitlement=None
    primary_next_action=_phone_primary_next_action(
        provider_key=provider_key,provider_connected=provider_connected,
        provider_has_credentials=bool((provider_row or {}).get('has_credentials')),
        telphin_sip=telphin_sip_readiness,online_devices=online_devices,
        gates=production_gates,recording_policy=recording_policy,
        recording_ai_entitlement=recording_ai_entitlement,
        mcn_readiness=mcn_readiness_evidence,
        mcn_transport=mcn_transport_evidence,
        mcn_network=mcn_network_evidence,
    )
    manager_action_sync_pending=False
    try:
        durable_manager_action=phone_manager_action(account_id)
        if durable_manager_action and durable_manager_action.get('id'):
            manager_action_current=bool(
                str(primary_next_action.get('actor') or '')=='manager'
                and str(durable_manager_action.get('action_code') or '')
                    == str(primary_next_action.get('code') or '')
            )
            if not manager_action_current:
                manager_action_sync_pending=True
                durable_manager_action=None
    except Exception as exc:
        durable_manager_action={
          'status':'unavailable',
          'error_type':type(exc).__name__[:120],
        }
    native_env=phone_native_release_environment(build_root)
    native_blocker_labels={
      'android_sdk_or_java_17_missing':'не готов Android SDK 35 / Java 17+ на build-host',
      'firebase_google_services_json':'нет Firebase google-services.json',
      'release_keystore':'нет production keystore Android',
      'release_store_password':'нет пароля keystore Android',
      'release_key_alias':'нет alias ключа Android',
      'release_key_password':'нет пароля ключа Android',
      'adb_unavailable':'на build-host нет adb для real-device QA',
      'real_device_qa_not_proven':'QA на реальном устройстве ещё не доказан',
      'xcodebuild':'нет xcodebuild — нужен macOS build-host',
      'xcodegen':'нет xcodegen на macOS build-host',
      'apple_team_id':'не задан Apple Team ID',
      'apple_codesigning_identity_not_proven':'Apple code-signing identity ещё не доказан',
    }
    release_blocker_codes={
      'windows':['windows_codesigning_not_proven','windows_real_device_smoke_not_proven'],
      'macos':['macos_codesigning_not_proven','macos_notarization_not_proven','macos_real_device_smoke_not_proven'],
      'android':list(native_env['android']['missing']),
      'ios':list(native_env['ios']['missing']),
    }
    release_blockers_human={
      p:[native_blocker_labels.get(code,code) for code in codes]
      for p,codes in release_blocker_codes.items()
    }
    return {'status':'ok','scope':'application_and_real_telephony','readiness_pct':pct,'telephony_readiness_pct':production_pct,
      'gates':gates,'remaining':remaining,'telephony_gates':production_gates,'telephony_remaining':production_remaining,'telephony_remaining_human':production_remaining_human,
      'provider_evidence':{'provider':(provider_row or {}).get('provider'),'status':provider_status_value or 'not_configured','last_health_status':(provider_row or {}).get('last_health_status'),'last_health_at':(provider_row or {}).get('last_health_at')},
      'runtime_evidence':{'online_devices':online_devices,'native_push_tokens':native_push_tokens,'native_push_provider_accepted':int(push_sent or 0),'native_push_device_received':int(push_received or 0),'calls':call_proof,'recordings':rec_proof,'transport':transport_evidence,'e2e_chain':acceptance_evidence},
      'media_diagnostics':media_diagnostics,'primary_next_action':primary_next_action,
      'manager_action':durable_manager_action,'manager_action_sync_pending':manager_action_sync_pending,
      'recording_policy':recording_policy,'recording_ai_entitlement':recording_ai_entitlement,
      'telphin_sip_readiness':telphin_sip_readiness,
      'mcn_readiness':mcn_readiness_evidence,'mcn_transport':mcn_transport_evidence,'mcn_network':mcn_network_evidence,
      'platforms':platforms,'distribution':distribution,
      'build_evidence':build_evidence,'release_integrity':release_integrity,'push_health':push_health,'pwa_distribution_ready':True,'native_distribution_ready':False,
      'native_release_environment':native_env,'release_blocker_codes':release_blocker_codes,'release_blockers_human':release_blockers_human,
      'release_blockers':{
        'windows':'Неподписанный NSIS уже собирается; production native installer требует код-подпись и smoke test на Windows',
        'macos':'PWA через Safari готов; native DMG остаётся дополнительным release gate',
        'android':' · '.join(release_blockers_human['android']) or 'build-host preflight inputs присутствуют; real-device/signature evidence всё равно обязателен',
        'ios':' · '.join(release_blockers_human['ios']) or 'macOS preflight inputs присутствуют; archive/sign/device evidence всё равно обязателен',
      },
      'note':'Готовность приложения и готовность реальных звонков считаются отдельно. 100% телефонии возможно только после доказанного provider/media/push E2E.'}


def verify_provider_connection(account_id: str, actor_user_id: int | None = None) -> dict[str, Any]:
    """Run a non-call provider health check and persist truthful connection state.

    This function must never originate a paid/test call. Adapter.health() is the
    only provider-specific boundary used here.
    """
    ensure_schema()
    provider, credentials = provider_credentials(account_id)
    provider = str(provider or '').strip().lower()
    if not provider:
        return {'status': 'provider_not_selected'}

    from app.services.telephony_adapters import get_adapter, adapter_status
    adapter = get_adapter(provider)
    ast = adapter_status(provider)
    if not adapter or not ast.get('implemented'):
        return {'status': 'adapter_pending', 'provider': provider, 'adapter': ast}
    if not credentials:
        return {'status': 'credentials_required', 'provider': provider, 'adapter': ast}

    public = _provider_public_config(account_id)
    try:
        result = adapter.health(credentials, public)
    except Exception as exc:
        # Provider SDK/HTTP exceptions are untrusted and may embed credentials,
        # signed URLs or Authorization headers. Collapse them to a stable class;
        # never bubble raw exception text into FastAPI/guardian/runtime output.
        from app.services.telephony_adapters.base import AdapterResult
        result = AdapterResult(False,'transport_error',error=(type(exc).__name__[:120] + ': provider health transport failure'))
    raw_health_status = 'ok' if result.ok else str(result.status or 'provider_health_failed')
    public_payload = result.payload if isinstance(result.payload, dict) else {}
    # Persist no provider secret or raw auth material in health evidence. Provider
    # payloads can be nested, so top-level filtering is insufficient: recursively
    # redact secret-shaped keys before returning or writing telephony_audit.
    _secret_keys={'token','access_token','refresh_token','secret','password','authorization','api_key','api_secret','user_key','secret_key','client_secret','webhook_secret','private_key'}
    def _credential_values(v):
        if isinstance(v,dict):
            for vv in v.values(): yield from _credential_values(vv)
        elif isinstance(v,(list,tuple,set)):
            for vv in v: yield from _credential_values(vv)
        elif v is not None:
            yield str(v)
    _credential_secret_values=sorted({x for x in _credential_values(credentials) if len(x)>=4},key=len,reverse=True)
    def _redact_health_string(value: str) -> str:
        msg=str(value or '')[:4000]
        for secret_value in _credential_secret_values:
            msg=msg.replace(secret_value,'[REDACTED]')
        msg=re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+',r'\1[REDACTED]',msg)
        msg=re.sub(r'(?i)((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|token|secret|password)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',msg)
        return msg[:1000]
    health_status = 'ok' if result.ok else (re.sub(r'[^A-Za-z0-9._:-]+','_',_redact_health_string(raw_health_status))[:100] or 'provider_health_failed')
    def _sanitize_health_payload(value: Any, depth: int=0):
        if depth>8: return '[TRUNCATED]'
        if isinstance(value,dict):
            out={}
            for k,v in value.items():
                key=str(k or '').strip().lower().replace('-','_')
                if key in _secret_keys or key.endswith('_secret') or key.endswith('_token') or key.endswith('_private_key'):
                    out[k]='[REDACTED]'
                else:
                    out[k]=_sanitize_health_payload(v,depth+1)
            return out
        if isinstance(value,list): return [_sanitize_health_payload(v,depth+1) for v in value[:100]]
        if isinstance(value,tuple): return [_sanitize_health_payload(v,depth+1) for v in value[:100]]
        if isinstance(value,str): return _redact_health_string(value)
        if isinstance(value,(int,float,bool)) or value is None: return value
        return _redact_health_string(str(value))[:500]
    safe_payload = _sanitize_health_payload(public_payload)
    def _redact_provider_error(value: Any) -> str | None:
        msg=str(value or '')[:4000]
        if not msg:
            return None
        # Remove exact credential values first, then common auth header/query forms.
        def _walk(v):
            if isinstance(v,dict):
                for vv in v.values(): yield from _walk(vv)
            elif isinstance(v,(list,tuple,set)):
                for vv in v: yield from _walk(vv)
            elif v is not None:
                yield str(v)
        for secret_value in sorted({x for x in _walk(credentials) if len(x)>=4},key=len,reverse=True):
            msg=msg.replace(secret_value,'[REDACTED]')
        msg=re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+',r'\1[REDACTED]',msg)
        msg=re.sub(r'(?i)((?:api[_-]?key|api[_-]?secret|token|secret|password)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',msg)
        return msg[:1000] or None
    error = _redact_provider_error(result.error)

    db = SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_provider_configs
          SET status=:status,last_health_at=now(),last_health_status=:health,last_error=:error,updated_at=now()
          WHERE account_id=:a"""), {
            'a': account_id,
            'status': 'connected' if result.ok else 'configured_unverified',
            'health': health_status[:100],
            'error': error,
        })
        db.commit()
    finally:
        db.close()

    _audit(account_id, 'provider.verify', 'ok' if result.ok else health_status, actor_user_id,
           provider=provider, metadata={'adapter': ast, 'health': safe_payload})
    sip_trunk=None
    synthetic_account=bool(re.match(r'^(?:__.*qa|qa[-_])',str(account_id or '').strip().lower()))
    if provider=='telphin' and not synthetic_account:
        try:
            from app.services.telphin_sip_trunk import telphin_sip_guardian
            sip_trunk=telphin_sip_guardian()
        except Exception as exc:
            sip_trunk={'status':'degraded','registered':0,'owner_action_required':False,
                       'error_code':type(exc).__name__[:120]}
    return {
        'status': 'ok' if result.ok else health_status,
        'connected': bool(result.ok),
        'provider': provider,
        'adapter': ast,
        'health': safe_payload,
        'sip_trunk': sip_trunk,
        'message': error,
    }


def provider_health_guardian(limit: int = 100, stale_seconds: int = 900, include_synthetic: bool = False) -> dict[str, Any]:
    """Periodically re-check configured providers using adapter.health() only.

    No call is originated here. The guardian exists so an expired/revoked API key
    cannot leave BORIS Phone and Revenue Analytics permanently showing stale
    `connected` state after a one-time manual verification.
    """
    ensure_schema()
    limit=max(1,min(int(limit),500)); stale_seconds=max(300,min(int(stale_seconds),86400))
    db=SessionLocal()
    try:
        rows=db.execute(text("""SELECT p.account_id,p.provider,p.last_health_at
          FROM telephony_provider_configs p
          WHERE p.credentials_enc IS NOT NULL AND p.credentials_enc<>''
            AND (p.last_health_at IS NULL OR p.last_health_at < now()-(:seconds||' seconds')::interval)
            AND (:include_synthetic OR NOT (lower(p.account_id) ~ '^__.*qa' OR lower(p.account_id) ~ '^qa[-_]'))
            AND EXISTS (
              SELECT 1 FROM telephony_entitlements e
              WHERE e.account_id=p.account_id AND e.enabled=true AND e.paid_until>now()
            )
          ORDER BY COALESCE(p.last_health_at,to_timestamp(0)) ASC
          LIMIT :limit"""),{'seconds':stale_seconds,'limit':limit,'include_synthetic':bool(include_synthetic)}).mappings().all()
    finally:
        db.close()
    checked=0; connected=0; degraded=0; skipped=0; results=[]
    from app.services.telephony_adapters import adapter_status
    for row in rows:
        provider=str(row.get('provider') or '').strip().lower()
        ast=adapter_status(provider)
        if not ast.get('implemented'):
            skipped+=1; continue
        checked+=1
        try:
            result=verify_provider_connection(str(row['account_id']),None)
        except Exception as exc:
            degraded+=1
            # Guardian output is durable/operator-visible. Never propagate raw
            # provider/SDK exception strings because they can contain credentials.
            results.append({'account_id':str(row['account_id']),'provider':provider,'status':'guardian_error','error_type':type(exc).__name__[:120]})
            continue
        if result.get('connected'):
            connected+=1
        else:
            degraded+=1
        results.append({'account_id':str(row['account_id']),'provider':provider,'status':result.get('status'),'connected':bool(result.get('connected'))})
    return {'status':'ok','candidates':len(rows),'checked':checked,'connected':connected,'degraded':degraded,'skipped':skipped,'results':results[:100]}


def turn_media_guardian(include_synthetic: bool=False) -> dict[str,Any]:
    """Keep TURN healthy without making the owner a service operator."""
    ensure_schema(); db=SessionLocal()
    try:
        active=int(db.execute(text("""SELECT count(*) FROM telephony_media_sessions m
          JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
          JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
          WHERE m.status='active' AND m.managed_by='boris_gateway'
            AND c.state IN ('answered','active','on_hold','transferring')
            AND COALESCE(c.device_id,'')=m.device_id
            AND d.revoked_at IS NULL
            AND (:include_synthetic OR NOT (lower(m.account_id) ~ '^__.*qa' OR lower(m.account_id) ~ '^qa[-_]'))"""),
          {'include_synthetic':bool(include_synthetic)}).scalar() or 0)
    finally:
        db.close()
    try:
        from app.services.phone_media_gateway import turn_runtime_guardian
        out=turn_runtime_guardian(active)
    except Exception as exc:
        out={'status':'degraded','action':'guardian_error','error_type':type(exc).__name__[:120],
             'active_media_sessions':active,'recovered':False,'deferred':False,'owner_action_required':True}
    return out


def telephony_autonomy_snapshot(include_synthetic: bool = False) -> dict[str, Any]:
    """Read-only runtime backlog used by the Phone self-heal watchdog.

    Synthetic QA rows are excluded by default. Counts intentionally describe
    *recoverable runtime residue*, not normal future retries or human follow-up.
    """
    ensure_schema()
    # Asterisk evidence is part of Phone autonomy. Ensure its durable event
    # table exists before the watchdog reads deferred MCN races.
    from app.services.asterisk_gateway import ensure_schema as ensure_asterisk_schema
    ensure_asterisk_schema()
    params={'include_synthetic':bool(include_synthetic)}
    synthetic="""(:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))"""
    try:
        from app.services.phone_media_gateway import turn_runtime_status
        _turn_status=turn_runtime_status(probe=False)
    except Exception:
        _turn_status={'configured':False,'runtime_ready':False}
    degraded_turn_media=int(bool(_turn_status.get('configured') and not _turn_status.get('runtime_ready')))
    db=SessionLocal()
    try:
        def count(sql: str) -> int:
            return int(db.execute(text(sql),params).scalar() or 0)
        return {
          'stale_command_processing':count(f"""SELECT count(*) FROM telephony_commands
            WHERE status='processing' AND updated_at<now()-interval '2 minutes' AND {synthetic}"""),
          'overdue_command_queue':count(f"""SELECT count(*) FROM telephony_commands
            WHERE status IN ('queued','waiting_provider') AND attempts<5
              AND (next_attempt_at IS NULL OR next_attempt_at<now()-interval '2 minutes') AND {synthetic}"""),
          'stale_recording_download':count(f"""SELECT count(*) FROM telephony_recordings
            WHERE status='downloading' AND updated_at<now()-interval '10 minutes' AND {synthetic}"""),
          'stale_recording_transcript':count(f"""SELECT count(*) FROM telephony_recordings
            WHERE transcript_status='processing' AND updated_at<now()-interval '15 minutes' AND {synthetic}"""),
          'stale_online_devices':count(f"""SELECT count(*) FROM telephony_devices
            WHERE revoked_at IS NULL AND presence NOT IN ('offline','dnd')
              AND (last_seen_at IS NULL OR last_seen_at<now()-interval '90 seconds') AND {synthetic}"""),
          'expired_ring_targets':count(f"""SELECT count(*) FROM telephony_call_targets
            WHERE status='ringing' AND expires_at IS NOT NULL AND expires_at<now() AND {synthetic}"""),
          'callback_without_task':count(f"""SELECT count(*) FROM telephony_calls c
            WHERE c.callback_status='required' AND c.callback_due_at<now()
              AND {synthetic.replace('account_id','c.account_id')}
              AND c.crm_contact_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM accounts a WHERE a.account_id=c.account_id AND a.owner_user_id IS NOT NULL)
              AND NOT EXISTS (
                SELECT 1 FROM boris_crm_tasks t
                WHERE t.description=('boris_callback_overdue:'||c.id)
                  AND t.status NOT IN ('done','completed','cancelled')
              )"""),
          'stale_provider_health':count(f"""SELECT count(*) FROM telephony_provider_configs p
            WHERE p.credentials_enc IS NOT NULL AND p.credentials_enc<>''
              AND (p.last_health_at IS NULL OR p.last_health_at<now()-interval '20 minutes')
              AND {synthetic.replace('account_id','p.account_id')}
              AND EXISTS (
                SELECT 1 FROM telephony_entitlements e
                WHERE e.account_id=p.account_id AND e.enabled=true AND e.paid_until>now()
              )"""),
          'stale_media_sessions':count(f"""SELECT count(*) FROM telephony_media_sessions m
            LEFT JOIN telephony_calls c ON c.account_id=m.account_id AND c.id=m.call_id
            LEFT JOIN telephony_devices d ON d.account_id=m.account_id AND d.id=m.device_id
            WHERE m.status IN ('active','cleanup_pending','await_expiry')
              AND {synthetic.replace('account_id','m.account_id')}
              AND (m.next_cleanup_at IS NULL OR m.next_cleanup_at<=now())
              AND (m.status IN ('cleanup_pending','await_expiry') OR m.expires_at<=now() OR c.id IS NULL
                   OR c.state NOT IN ('answered','active','on_hold','transferring')
                   OR COALESCE(c.device_id,'')<>m.device_id OR d.id IS NULL OR d.revoked_at IS NOT NULL)"""),
          'deferred_mcn_asterisk_events':count(f"""SELECT count(*) FROM telephony_asterisk_events e
            JOIN telephony_calls c ON c.provider='mcn' AND c.provider_call_id=e.channel_id
            WHERE e.status='deferred'
              AND e.error_code IN ('canonical_call_not_found','outbound_call_commit_race')
              AND {synthetic.replace('account_id','c.account_id')}"""),
          'stale_mcn_crm_sync':count(f"""SELECT count(*) FROM telephony_calls c
            JOIN accounts a ON a.account_id=c.account_id AND a.owner_user_id IS NOT NULL
            WHERE c.provider='mcn'
              AND c.state IN ('transferred','ended','missed','rejected','busy','failed','cancelled')
              AND c.updated_at<now()-interval '30 seconds'
              AND {synthetic.replace('account_id','c.account_id')}
              AND length(regexp_replace(CASE WHEN c.direction='inbound' THEN COALESCE(c.from_number,'')
                                             ELSE COALESCE(c.to_number,'') END,'\\D','','g'))>=10
              AND (
                c.crm_contact_id IS NULL
                OR ((c.answered_at IS NOT NULL OR c.direction='inbound') AND c.crm_deal_id IS NULL)
                OR NOT EXISTS (
                  SELECT 1 FROM boris_crm_activities act
                  WHERE act.owner_user_id=a.owner_user_id
                    AND act.source_ref=('boris_call:'||c.account_id||':'||c.id)
                )
              )"""),
          'stale_mcn_terminal_cleanup':count(f"""SELECT count(*) FROM telephony_calls c
            WHERE c.provider='mcn'
              AND c.state IN ('transferred','ended','missed','rejected','busy','failed','cancelled')
              AND c.updated_at<now()-interval '30 seconds'
              AND {synthetic.replace('account_id','c.account_id')}
              AND (
                EXISTS (
                  SELECT 1 FROM telephony_call_targets t
                  WHERE t.account_id=c.account_id AND t.call_id=c.id
                    AND t.status IN ('ringing','answered')
                )
                OR EXISTS (
                  SELECT 1 FROM telephony_commands cmd
                  WHERE cmd.account_id=c.account_id AND cmd.call_id=c.id
                    AND cmd.status NOT IN ('done','failed')
                )
              )"""),
          'unreconciled_mcn_cdr':count(f"""SELECT count(*) FROM telephony_carrier_cdr c
            LEFT JOIN telephony_cdr_reconciliation r
              ON r.operator=c.operator AND r.carrier_cdr_id=c.id
            WHERE c.operator='mcn' AND r.id IS NULL
              AND (:include_synthetic OR c.account_id IS NULL
                   OR NOT (lower(c.account_id) ~ '^__.*qa' OR lower(c.account_id) ~ '^qa[-_]'))"""),
          'mcn_trunk_without_provider_config':count("""SELECT count(DISTINCT t.account_id)
            FROM telephony_trunks t
            LEFT JOIN telephony_provider_configs p ON p.account_id=t.account_id
            WHERE t.provider='mcn' AND t.enabled=true AND p.account_id IS NULL
              AND (:include_synthetic OR NOT (lower(t.account_id) ~ '^__.*qa' OR lower(t.account_id) ~ '^qa[-_]'))
              AND EXISTS (
                SELECT 1 FROM telephony_entitlements e
                WHERE e.account_id=t.account_id AND e.enabled=true AND e.paid_until>now()
              )"""),
          'degraded_mcn_transport':count(f"""SELECT count(*) FROM telephony_trunks t
            WHERE t.provider='mcn' AND t.enabled=true
              AND (t.status<>'connected' OR COALESCE(t.last_health_status,'') NOT IN ('registration_registered','ok'))
              AND {synthetic.replace('account_id','t.account_id')}
              AND EXISTS (
                SELECT 1 FROM telephony_entitlements e
                WHERE e.account_id=t.account_id AND e.enabled=true AND e.paid_until>now()
              )"""),
          'degraded_telphin_sip':count(f"""SELECT count(*) FROM telephony_telphin_trunk_state s
            WHERE s.enabled=true AND s.status<>'registered'
              AND {synthetic.replace('account_id','s.account_id')}
              AND EXISTS (
                SELECT 1 FROM telephony_entitlements e
                WHERE e.account_id=s.account_id AND e.enabled=true AND e.paid_until>now()
              )"""),
          'overdue_manager_actions':count(f"""SELECT count(*) FROM telephony_manager_actions
            WHERE status='open' AND due_at IS NOT NULL AND due_at<=now() AND {synthetic}"""),
          'degraded_turn_media':degraded_turn_media,
        }
    finally:
        db.close()


def telephony_autonomy_guardian(limit: int = 100, include_synthetic: bool = False) -> dict[str, Any]:
    """Detect -> recover -> verify BORIS Phone runtime residue.

    The normal runtime already performs each domain action. This watchdog is a
    second *control layer*, not a second queue engine: it only invokes existing
    idempotent/leased primitives when evidence says they are stale. It never
    originates a test call and provider health uses adapter.health() only.
    """
    limit=max(1,min(int(limit),500))
    before=telephony_autonomy_snapshot(include_synthetic=include_synthetic)
    actions={}
    if before['stale_command_processing'] or before['overdue_command_queue']:
        actions['commands']=dispatch_pending_commands(limit,include_synthetic=include_synthetic)
    if before['stale_recording_download']:
        actions['recording_download']=acquire_pending_recordings(min(limit,10),include_synthetic=include_synthetic)
    if before['stale_recording_transcript']:
        actions['recording_transcript']=process_pending_recordings(min(limit,5),include_synthetic=include_synthetic)
    if before['stale_online_devices']:
        actions['devices']=sweep_stale_devices(None,90)
    if before['expired_ring_targets']:
        actions['ring_targets']=sweep_ring_targets(None)
    if before['callback_without_task']:
        actions['callbacks']=callback_guardian(None)
    if before['stale_provider_health']:
        actions['provider_health']=provider_health_guardian(limit,900,include_synthetic=include_synthetic)
    if before.get('stale_media_sessions'):
        actions['media_sessions']=media_session_guardian(limit,include_synthetic=include_synthetic)
    if before.get('deferred_mcn_asterisk_events'):
        from app.services.asterisk_gateway import mcn_deferred_asterisk_event_guardian
        actions['mcn_asterisk_deferred']=mcn_deferred_asterisk_event_guardian(
            limit,include_synthetic=include_synthetic)
    # Terminal residue must be closed before CRM sync: sync_call_to_crm()
    # intentionally updates telephony_calls.updated_at, which would otherwise
    # push the 30-second cleanup safety window forward and delay recovery.
    if before.get('stale_mcn_terminal_cleanup'):
        actions['mcn_terminal_cleanup']=mcn_terminal_cleanup_guardian(limit,include_synthetic=include_synthetic)
    if before.get('stale_mcn_crm_sync'):
        actions['mcn_crm_sync']=mcn_crm_sync_guardian(limit,include_synthetic=include_synthetic)
    if before.get('unreconciled_mcn_cdr'):
        from app.services.mcn_core import reconcile_carrier_cdr
        actions['mcn_cdr_reconcile']=reconcile_carrier_cdr(
            None,min(limit,500),include_synthetic=include_synthetic)
    if before.get('mcn_trunk_without_provider_config'):
        actions['mcn_provider_config']=mcn_provider_config_guardian(
            limit,include_synthetic=include_synthetic)
    if before.get('degraded_mcn_transport'):
        from app.services.asterisk_gateway import mcn_pjsip_guardian
        actions['mcn_pjsip']=mcn_pjsip_guardian(include_synthetic=include_synthetic)
    if before.get('degraded_telphin_sip'):
        from app.services.telphin_sip_trunk import telphin_sip_guardian
        actions['telphin_sip']=telphin_sip_guardian()
    if before.get('overdue_manager_actions'):
        actions['manager_actions']=phone_manager_action_guardian(
            limit,include_synthetic=include_synthetic)
    if before.get('degraded_turn_media'):
        actions['turn_media']=turn_media_guardian(include_synthetic=include_synthetic)
    after=telephony_autonomy_snapshot(include_synthetic=include_synthetic)
    unresolved={k:v for k,v in after.items() if int(v or 0)>0}
    recovered={k:max(0,int(before.get(k) or 0)-int(after.get(k) or 0)) for k in before}
    recovered={k:v for k,v in recovered.items() if v>0}
    owner_action_codes=[]
    for key in ('mcn_pjsip','telphin_sip','turn_media','manager_actions'):
        action=actions.get(key) or {}
        if action.get('owner_action_required'):
            code=str(action.get('owner_action_code') or key).strip()
            if code and code not in owner_action_codes:
                owner_action_codes.append(code)
    owner_action_required=bool(owner_action_codes)
    return {
      'status':'ok' if not unresolved else 'degraded',
      'before':before,'actions':actions,'after':after,
      'recovered':recovered,'unresolved':unresolved,
      'owner_action_required':owner_action_required,
      'owner_action_codes':owner_action_codes,
    }


def recording_upsert_for_provider_call(account_id: str, provider: str, provider_call_id: str,
                                       provider_recording_id: str | None, source_url: str | None,
                                       duration_sec: int | None = None) -> dict[str, Any]:
    """Resolve canonical BORIS call by provider_call_id, then use the one recording store."""
    ensure_schema(); db=SessionLocal()
    try:
        call_id=db.execute(text("SELECT id FROM telephony_calls WHERE account_id=:a AND provider=:p AND provider_call_id=:pc ORDER BY started_at DESC LIMIT 1"),
                           {'a':account_id,'p':str(provider or '').strip().lower(),'pc':str(provider_call_id or '').strip()}).scalar()
    finally:
        db.close()
    if not call_id:
        return {'status':'call_not_found'}
    return recording_upsert(account_id,str(call_id),provider,provider_recording_id,source_url,duration_sec)


def provider_webhook_secret(account_id: str, expected_provider: str | None = None) -> tuple[str | None, str | None]:
    """Return decrypted provider webhook secret only to internal adapter code."""
    ensure_schema(); db=SessionLocal()
    try:
        row=db.execute(text("SELECT provider,webhook_secret_enc FROM telephony_provider_configs WHERE account_id=:a"),{'a':account_id}).mappings().first()
    finally:
        db.close()
    provider=str((row or {}).get('provider') or '').strip().lower() or None
    if expected_provider and provider!=str(expected_provider).strip().lower():
        return provider,None
    enc=(row or {}).get('webhook_secret_enc')
    if not enc:
        return provider,None
    from app.crypto_utils import decrypt_secret
    try:
        return provider,decrypt_secret(str(enc))
    except Exception:
        return provider,None
