#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
py=backend/venv/bin/python
"$py" -m py_compile backend/app/services/telephony_core.py backend/app/api/telephony.py
# Approved mobile dialer contract.
grep -q 'aria-label="Удалить последнюю цифру"' frontend/app/phone-app/page.tsx
grep -q 'Очистить' frontend/app/phone-app/page.tsx
grep -q 'pasteNumber' frontend/app/phone-app/page.tsx
grep -q '+ Код страны' frontend/app/phone-app/page.tsx
grep -q "tab==='settings'" frontend/app/phone-app/page.tsx
grep -q 'Автоответчик' frontend/app/phone-app/page.tsx
grep -q 'safe-area-inset-bottom' frontend/app/phone-app/phone-app.css
grep -Eq 'BORIS_SW_VERSION = "boris-pwa-v[0-9][A-Za-z0-9._-]*"' frontend/public/sw.js
grep -q 'OFFLINE_URL' frontend/public/sw.js
grep -q 'Данные звонков и CRM не показываются из старого кэша' frontend/public/phone-offline.html
grep -q 'retry=setInterval' frontend/app/phone-app/page.tsx
# Autoanswer owner product contract.
grep -q "'off','Выключен'" frontend/app/dashboard/phone/page.tsx
grep -q "'afterhours','Вне рабочего времени'" frontend/app/dashboard/phone/page.tsx
grep -q "'no_answer','Если не ответили'" frontend/app/dashboard/phone/page.tsx
grep -q "'always','Всегда'" frontend/app/dashboard/phone/page.tsx
grep -q 'Фраза о виртуальном помощнике' frontend/app/dashboard/phone/page.tsx
grep -q 'Передавать неизвестный вопрос менеджеру' frontend/app/dashboard/phone/page.tsx
grep -q "@router.get('/autoanswer/readiness')" backend/app/api/telephony.py
grep -q "@router.get('/autoanswer/preview')" backend/app/api/telephony.py
grep -q 'never_take_human_assigned_call' backend/app/services/telephony_core.py
grep -q 'already_requested' backend/app/services/telephony_core.py
grep -q 'unknown_question' backend/app/services/telephony_core.py
test -f backend/migrations/007_telephony_autoanswer_policy.sql
test -f backend/migrations/008_telephony_voice_agent_policy.sql
test -f clients/boris-phone/UI_REFERENCE.md
printf '%s\n' PHONE_PRODUCT_BASE_QA=PASS MOBILE_DIALER_CONTRACT=PASS AUTOANSWER_PRODUCT_CONTRACT=PASS UI_REFERENCE_CONTRACT=PASS

# Public provider ingress must be signed, fresh, bounded, and replay-safe.
grep -q "x_boris_timestamp" backend/app/api/telephony.py
grep -q "x_boris_nonce" backend/app/api/telephony.py
grep -q "payload too large" backend/app/api/telephony.py
grep -q "telephony_webhook_nonces" backend/app/services/telephony_core.py
test -f backend/migrations/009_telephony_core_schema.sql
grep -q "hmac.compare_digest" backend/app/services/telephony_core.py
echo WEBHOOK_SECURITY_CONTRACT=PASS

# Native push is a wake-up hint only; no caller PII is trusted from FCM/PushKit.
grep -q 'event!="call.ringing"' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisFirebaseMessagingService.kt
grep -q 'event == "call.ringing"' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
grep -q 'BORIS Phone' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
echo NATIVE_PUSH_MINIMAL_TRUST_CONTRACT=PASS

# Successful native build paths must refresh integrity evidence automatically.
grep -q 'artifact-manifest.py' clients/boris-phone/android/scripts/source-build-qa.sh
grep -q 'artifact-manifest.py' clients/boris-phone/android/scripts/release-build.sh
grep -q 'npm run manifest' clients/boris-phone/desktop/package.json
echo RELEASE_INTEGRITY_AUTOMATION=PASS

# Native update UI must explain the authoritative backend release blocker instead of
# presenting every unavailable release as the same generic "not published" state.
grep -q 'nativeUpdateBlockedText' frontend/app/phone-app/page.tsx
grep -q 'blocked_reason' frontend/app/phone-app/page.tsx
grep -q 'signed_artifact_not_verified' frontend/app/phone-app/page.tsx
grep -q 'real_device_qa_not_verified' frontend/app/phone-app/page.tsx
grep -q 'setNotice(nativeUpdateBlockedText(releaseInfo))' frontend/app/phone-app/page.tsx
echo PHONE_NATIVE_UPDATE_BLOCK_REASON_UI=PASS

# Durable push retry/backoff must not hot-loop external transports.
grep -q 'next_attempt_at' backend/app/services/telephony_core.py
grep -q "interval '5 minutes'" backend/app/services/telephony_core.py
test -f backend/migrations/010_telephony_push_retry_backoff.sql
echo MOBILE_PUSH_BACKOFF_CONTRACT=PASS

# Runtime load/control resilience.
grep -q "boris_phone_presence" frontend/app/phone-app/page.tsx
grep -q "ownDeviceBusy=useMemo" frontend/app/phone-app/page.tsx
grep -q "scheduleRefresh" frontend/app/phone-app/page.tsx
grep -q "'mute','unmute'" frontend/app/phone-app/page.tsx
grep -q '"ended","transferred"' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisConnectionService.kt
grep -q '"ended","transferred"' clients/boris-phone/ios/BORISPhone/CallManager.swift
echo PHONE_RUNTIME_LOAD_SHAPING=PASS
echo NATIVE_TRANSFER_TERMINAL_SYNC=PASS
echo IOS_NATIVE_MUTE_CONTROL_BRIDGE=PASS

# Signed webhook secret is bound to the configured provider.
grep -q 'requested_provider!=configured_provider' backend/app/services/telephony_core.py
grep -q 'provider=provider' backend/app/api/telephony.py
echo WEBHOOK_PROVIDER_BINDING=PASS

# Mobile Phone shows authoritative autoanswer state and can change safe quick modes directly.
grep -q 'afterhours/status' frontend/app/phone-app/page.tsx
grep -q 'saveAutoQuick' frontend/app/phone-app/page.tsx
grep -q 'mobileAutoModes' frontend/app/phone-app/page.tsx
grep -q 'Все настройки автоответчика' frontend/app/phone-app/page.tsx
echo MOBILE_AUTOANSWER_QUICK_CONTROL=PASS

# Phone uses the shared BORIS icon system instead of ad-hoc glyphs for primary navigation and call controls.
grep -q "import {Icon} from '../ui'" frontend/app/phone-app/page.tsx
grep -q 'phoneOff' frontend/app/ui/index.tsx
grep -q 'keypad' frontend/app/ui/index.tsx
grep -q 'transfer' frontend/app/ui/index.tsx
echo PHONE_ETALON_ICON_SYSTEM=PASS

# Native shells fail closed to a no-data offline screen and do not auto-launch arbitrary external redirects.
grep -q 'onReceivedError' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'Данные звонков не показываются из старого кэша' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'didFailProvisionalNavigation' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift
grep -q 'navigationType == .linkActivated' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift
echo NATIVE_OFFLINE_FAILSAFE=PASS

# Ephemeral operational queues are bounded; business evidence is explicitly preserved.
grep -q 'def telephony_operational_retention_guard' backend/app/services/telephony_core.py
grep -q 'telephony_retention_guard' backend/telephony_runtime_runner.py
grep -q "'telephony_calls','telephony_events','telephony_audit'" backend/app/services/telephony_core.py
echo PHONE_OPERATIONAL_RETENTION=PASS

# Mobile design/combat-control contract.
grep -q 'phoneIntentGrid' frontend/app/phone-app/page.tsx
grep -q 'historyGroups' frontend/app/phone-app/page.tsx
grep -q "muted?'unmute':'mute'" frontend/app/phone-app/page.tsx
grep -q 'transferOpen' frontend/app/phone-app/page.tsx
grep -q 'to_number:to' frontend/app/phone-app/page.tsx
grep -q 'DESIGN COMPLETION V5' frontend/app/phone-app/phone-app.css
grep -q 'QUICK INTENT CARDS' frontend/app/phone-app/phone-app.css
grep -q 'LIVE/IDLE CALL CARD' frontend/app/phone-app/phone-app.css
grep -q "invalid_transfer_target" backend/app/services/telephony_core.py
grep -q "invalid_dtmf" backend/app/services/telephony_core.py
echo PHONE_DESIGN_COMBAT_CONTRACT=PASS
# Runtime scale contract: hot event/device/routing queries must remain indexed.
grep -q "ix_telephony_events_account_id" backend/migrations/011_telephony_runtime_hot_indexes.sql
grep -q "ix_telephony_devices_online" backend/migrations/011_telephony_runtime_hot_indexes.sql
grep -q "ix_telephony_targets_device_call" backend/migrations/011_telephony_runtime_hot_indexes.sql
grep -q "ix_telephony_calls_device_state" backend/migrations/011_telephony_runtime_hot_indexes.sql
echo "PHONE_RUNTIME_DB_INDEX_CONTRACT=PASS"
grep -q 'ck_telephony_calls_state' backend/migrations/012_telephony_state_constraints.sql
grep -q 'ck_telephony_devices_presence' backend/migrations/012_telephony_state_constraints.sql
grep -q 'ck_telephony_targets_status' backend/migrations/012_telephony_state_constraints.sql
echo PHONE_RUNTIME_STATE_CONSTRAINTS=PASS
grep -q 'pg_try_advisory_lock' backend/telephony_runtime_runner.py
grep -q 'ix_telephony_commands_queued' backend/migrations/013_telephony_runtime_singleton_and_queue_indexes.sql
grep -q 'ck_telephony_push_status' backend/migrations/013_telephony_runtime_singleton_and_queue_indexes.sql
echo PHONE_RUNTIME_SINGLETON_CONTRACT=PASS
echo PHONE_QUEUE_STATE_CONTRACT=PASS
grep -q "next_attempt_at IS NULL OR next_attempt_at<=now()" backend/app/services/telephony_core.py
grep -q "production adapter unavailable" backend/app/services/telephony_core.py
grep -q "provider credentials unavailable; retry limit reached" backend/app/services/telephony_core.py
grep -q "waiting_configuration','sent'" backend/migrations/014_telephony_command_backoff_and_state_fix.sql
echo PHONE_COMMAND_BACKOFF_CONTRACT=PASS
echo PHONE_QUEUE_REAL_STATE_CONTRACT=PASS
grep -q 'transferDevices' frontend/app/phone-app/page.tsx
grep -q 'Доступные устройства' frontend/app/phone-app/page.tsx
grep -q 'transferDeviceList' frontend/app/phone-app/phone-app.css
echo PHONE_TRANSFER_DESTINATION_PICKER=PASS
grep -q "capabilities_json?.extension" frontend/app/phone-app/page.tsx
grep -q "добавочный не задан" frontend/app/phone-app/page.tsx
grep -q "ext=re.sub" backend/app/services/telephony_core.py
echo PHONE_TRANSFER_SAFE_EMPLOYEE_PICKER=PASS
grep -q 'dtmfOpen' frontend/app/phone-app/page.tsx
grep -q "control('dtmf',{digits:k})" frontend/app/phone-app/page.tsx
grep -q 'dtmfKeys' frontend/app/phone-app/phone-app.css
echo PHONE_LIVE_DTMF_KEYPAD=PASS
grep -q 'def set_device_extension' backend/app/services/telephony_core.py
grep -q "devices/{device_id}/extension" backend/app/api/telephony.py
grep -q "capabilities_json=(COALESCE(telephony_devices.capabilities_json,'{}'::jsonb)" backend/app/services/telephony_core.py
grep -q -- "- 'calls' - 'crm' - 'notifications' - 'media' - 'background' - 'native_call_ui'" backend/app/services/telephony_core.py
grep -q 'bp-extension' frontend/app/dashboard/phone/page.tsx
echo PHONE_DEVICE_EXTENSION_ADMIN=PASS
echo PHONE_DEVICE_CAPABILITY_MERGE=PASS
grep -q 'CALL_STATE_TRANSITIONS' backend/app/services/telephony_core.py
grep -q 'ignored_out_of_order' backend/app/services/telephony_core.py
grep -q '015_telephony_call_state_alignment.sql' <(find backend/migrations -maxdepth 1 -type f -printf '%f\n')
echo PHONE_MONOTONIC_STATE_MACHINE=PASS
echo PHONE_DB_APP_STATE_ALIGNMENT=PASS
grep -q "'ringing','answered','rejected','timeout','cancelled','ended'" backend/migrations/012_telephony_state_constraints.sql
grep -q 'ix_telephony_targets_due' backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q 'ix_telephony_targets_due' backend/migrations/016_telephony_target_state_alignment.sql
echo PHONE_TARGET_STATE_ALIGNMENT=PASS
echo PHONE_TARGET_DUE_SWEEP_INDEX=PASS
grep -q 'COMMAND_TRANSIENT_STATUSES' backend/app/services/telephony_core.py
grep -q '_command_retryable' backend/app/services/telephony_core.py
grep -Eq "attempts[[:space:]]*<[[:space:]]*5" backend/app/services/telephony_core.py
grep -q 'LEAST(300,5' backend/app/services/telephony_core.py
echo PHONE_COMMAND_TRANSIENT_RETRY=PASS
echo PHONE_COMMAND_RETRY_BOUNDED=PASS
grep -q "telephony_commands(created_at,id,next_attempt_at) WHERE status IN ('queued','waiting_provider')" backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q "telephony_push_outbox(created_at,id,next_attempt_at) WHERE status IN ('pending','retry','waiting_configuration')" backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q 'ix_telephony_targets_due' backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q 'expires_at,id,account_id,call_id' backend/migrations/025_telephony_runtime_columns_indexes.sql
echo PHONE_DUE_QUEUE_INDEX_SHAPE=PASS
grep -q "telephony_audit(account_id,action,result,call_id,provider,metadata_json)" backend/app/services/telephony_core.py
echo PHONE_OUT_OF_ORDER_AUDIT_SCHEMA_ALIGNMENT=PASS
grep -q "status='processing'" backend/app/services/telephony_core.py
grep -q 'recovered stale processing lease' backend/app/services/telephony_core.py
grep -q 'No SQLAlchemy session from this dispatcher is alive here' backend/app/services/telephony_core.py
echo PHONE_COMMAND_EXTERNAL_TX_BOUNDARY=PASS
echo PHONE_COMMAND_PROCESSING_RECOVERY=PASS
grep -q "interval '2 minutes'" backend/app/services/telephony_core.py
grep -q 'FOR UPDATE OF o SKIP LOCKED' backend/app/services/telephony_core.py
grep -q 'No dispatcher-owned SQLAlchemy session is alive during external push I/O' backend/app/services/telephony_core.py
echo PHONE_PUSH_EXTERNAL_TX_BOUNDARY=PASS
echo PHONE_PUSH_EXECUTION_LEASE=PASS
grep -q "telephony_push_outbox(created_at,id,next_attempt_at) WHERE status IN ('pending','retry','waiting_configuration')" backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q "waiting_configuration'" backend/migrations/018_telephony_push_waiting_due_index.sql
echo PHONE_PUSH_WAITING_INDEX=PASS
grep -q 'waiting_configuration.*sleeping state' backend/app/services/telephony_core.py
echo PHONE_PUSH_WAITING_CONFIGURATION_SLEEP=PASS
test -f clients/boris-phone/scripts/runtime-db-qa.py
grep -q 'PHONE_STATE_MACHINE_DB_INTEGRATION=PASS' clients/boris-phone/scripts/runtime-db-qa.py
grep -q 'PHONE_TARGET_TIMEOUT_DB_INTEGRATION=PASS' clients/boris-phone/scripts/runtime-db-qa.py
grep -q 'PHONE_COMMAND_CONCURRENT_CLAIM=PASS' clients/boris-phone/scripts/runtime-db-qa.py
grep -q 'PHONE_PUSH_CONCURRENT_CLAIM=PASS' clients/boris-phone/scripts/runtime-db-qa.py
echo PHONE_RUNTIME_DB_QA_SUITE=PASS
grep -q "'outbound_ambiguous'" backend/app/services/telephony_core.py
grep -q 'Повторный звонок автоматически не создавался' backend/app/services/telephony_core.py
echo PHONE_OUTBOUND_AMBIGUITY_FAIL_CLOSED=PASS
grep -q 'transcript_attempts integer NOT NULL DEFAULT 0' backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q 'recovered stale transcript lease' backend/app/services/telephony_core.py
grep -q "transcript_attempts<5" backend/app/services/telephony_core.py
grep -q 'next_transcript_at=now()+interval' backend/app/services/telephony_core.py
grep -q '019_telephony_recording_retry_lease.sql' <(find backend/migrations -maxdepth 1 -type f -printf '%f\n')
echo PHONE_RECORDING_RETRY_LEASE=PASS
grep -q 'ix_telephony_recording_download_lease' backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q 'ix_telephony_recording_transcript_lease' backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q "telephony_recordings(created_at,id,next_download_at)" backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q "telephony_recordings(created_at,id,next_transcript_at)" backend/migrations/025_telephony_runtime_columns_indexes.sql
grep -q '020_telephony_recording_queue_indexes.sql' <(find backend/migrations -maxdepth 1 -type f -printf '%f\n')
echo PHONE_RECORDING_QUEUE_INDEX_SHAPE=PASS

# Truthful readiness: app/PWA readiness must never masquerade as real telephony E2E.
grep -q 'telephony_readiness_pct' backend/app/services/telephony_core.py
grep -q 'native_push_delivered' backend/app/services/telephony_core.py
grep -Eq "(au\.)?action='media\.session\.validated' AND (au\.)?result='ok'" backend/app/services/telephony_core.py
grep -q 'telephony_remaining_human' backend/app/services/telephony_core.py
grep -q 'Боевые звонки' frontend/app/dashboard/phone/page.tsx
grep -q 'Звонки {clientReady?.telephony_readiness_pct' frontend/app/phone-app/page.tsx
echo PHONE_REAL_E2E_READINESS_TRUTH=PASS

# Native push delivery proof must be stronger than APNs/FCM HTTP acceptance.
grep -q 'def ack_mobile_push' backend/app/services/telephony_core.py
grep -q 'receipt_token' backend/app/services/telephony_core.py
grep -q 'receipt_token:nativePushProofRef.current' frontend/app/phone-app/page.tsx
grep -q "@router.post('/devices/push-receipt')" backend/app/api/telephony.py
grep -q "wire_payload\['push_id'\]" backend/app/services/telephony_core.py
grep -q 'device_received_at' backend/migrations/026_telephony_push_device_receipt.sql
grep -q 'sent_push_token_hash' backend/migrations/029_telephony_push_sent_token_binding.sql
grep -q 'sent_push_token_hash=:ph' backend/app/services/telephony_core.py
grep -q 'boris-phone-native-push-receipt' frontend/app/phone-app/page.tsx
grep -q 'NativePushReceiptBus.publish' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisFirebaseMessagingService.kt
grep -q 'pushReceiptEvent' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
echo PHONE_NATIVE_PUSH_DEVICE_RECEIPT=PASS

# Media credentials are issued only for an active call already claimed by the exact device.
grep -q 'def request_media_session(account_id: str, device_id: str, call_id: str)' backend/app/services/telephony_core.py
grep -q "call.get('state') or '') not in {'answered','active','on_hold','transferring'}" backend/app/services/telephony_core.py
grep -q "call_not_claimed_by_device" backend/app/services/telephony_core.py
grep -q "provider_call_not_bound" backend/app/services/telephony_core.py
grep -q "body.get('call_id')" backend/app/api/telephony.py
grep -q 'mediaSession(accountId:string,deviceId:string,callId:string)' clients/boris-phone/shared/protocol.ts
grep -q 'mediaSession(accountId:String,deviceId:String,callId:String)' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneApi.kt
grep -q 'mediaSession(accountId:String,deviceId:String,callId:String)' clients/boris-phone/ios/BORISPhone/BorisPhoneAPI.swift
echo PHONE_MEDIA_CALL_DEVICE_BINDING=PASS

# Real Phone E2E must be one connected evidence chain, not unrelated counters.
grep -q 'inbound_full_chain' backend/app/services/telephony_core.py
grep -q 'outbound_full_lifecycle' backend/app/services/telephony_core.py
grep -q 'real_inbound_e2e_chain' backend/app/services/telephony_core.py
grep -q 'real_outbound_lifecycle' backend/app/services/telephony_core.py
echo PHONE_CONNECTED_E2E_CHAIN_TRUTH=PASS

# Provider event idempotency is enforced durably in PostgreSQL as well as by advisory locking.
grep -q 'ux_telephony_events_provider_identity' backend/app/services/telephony_core.py
echo PHONE_PROVIDER_EVENT_DB_IDEMPOTENCY=PASS

# One provider recording id must never be silently rebound to another call.
grep -q 'provider_recording_identity_conflict' backend/app/services/telephony_core.py
grep -q 'recording.identity.conflict' backend/app/services/telephony_core.py
echo PHONE_PROVIDER_RECORDING_CALL_BINDING=PASS

# Callback SLA task creation is exactly-once even if internal/runtime guardians overlap.
grep -q "callback-sla" backend/app/services/telephony_core.py
grep -q "pg_advisory_xact_lock" backend/app/services/telephony_core.py
echo PHONE_CALLBACK_SLA_TASK_EXACTLY_ONCE=PASS

# Provider event durable identity is represented in migration history too.
test -f backend/migrations/028_telephony_provider_event_identity.sql
grep -q 'ux_telephony_events_provider_identity' backend/migrations/028_telephony_provider_event_identity.sql
echo PHONE_PROVIDER_EVENT_MIGRATION=PASS

# Phone -> CRM sync and missed-call callback task closure are cross-process safe and reference-aligned.
grep -q 'crm-call-sync|' backend/app/services/telephony_core.py
grep -Fq "task_ref=f'boris_callback:{account_id}:{call_id}'" backend/app/services/telephony_core.py
echo PHONE_CRM_CALL_SYNC_EXACTLY_ONCE=PASS
grep -q 'crm-phone|' backend/app/services/telephony_core.py
grep -q 'crm-phone|' backend/app/crm/calltracking_sync.py
echo PHONE_CRM_PHONE_IDENTITY_LOCK=PASS
echo PHONE_CALLBACK_TASK_COMPLETION_ALIGNMENT=PASS


# Native values crossing into the WebView must be serialized as JSON string literals,
# never interpolated by deleting one quote character and trusting provider token format.
grep -q 'JSONObject.quote' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'borisJSLiteral' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift
if grep -q 'replacingOccurrences(of:"'"'"'",with:"")' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift; then
  echo NATIVE_WEBVIEW_JSON_ESCAPE=FAIL; exit 1
fi
echo NATIVE_WEBVIEW_JSON_ESCAPE=PASS

# Phone runtime has an independent detect -> recover -> verify watchdog; callback SLA is not degraded after a manager task exists.
grep -q 'def telephony_autonomy_snapshot' backend/app/services/telephony_core.py
grep -q 'def telephony_autonomy_guardian' backend/app/services/telephony_core.py
grep -q 'telephony_autonomy_guardian(100)' backend/telephony_runtime_runner.py
grep -q 'callback_without_task' backend/app/services/telephony_core.py
grep -q 'overdue_callbacks_without_task' backend/app/services/control_plane_adapters.py
grep -q 'MISSED_CALLBACK_WAITING_MANAGER' backend/app/services/control_plane_adapters.py
grep -q 'TELEPHONY_RUNTIME_STALLED' backend/app/services/control_plane_adapters.py
grep -q 'TELEPHONY_PROVIDER_UNHEALTHY' backend/app/services/control_plane_adapters.py
grep -q 'stale_runtime_commands' backend/app/services/control_plane_adapters.py
echo PHONE_AUTONOMY_SELF_HEAL_CONTRACT=PASS

# Cold/background native controls must not depend on a warm WebView.
grep -q 'bridgeReady' frontend/app/phone-app/page.tsx
grep -q 'fun bridgeReady()' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'NativeCallControlBus.install' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'pending=ConcurrentHashMap' clients/boris-phone/android/app/src/main/java/ai/boris/phone/NativeCallControlBus.kt
grep -q 'wakePhoneUi' clients/boris-phone/android/app/src/main/java/ai/boris/phone/NativeCallControlBus.kt
grep -q 'onShowIncomingCallUi' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisConnectionService.kt
grep -q 'webBridgeReady' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
grep -q 'type=="bridgeReady"' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift
grep -q 'didStartProvisionalNavigation' clients/boris-phone/ios/BORISPhone/PhoneRootView.swift
echo PHONE_NATIVE_COLD_START_CONTROL_BRIDGE=PASS
echo ANDROID_SELF_MANAGED_INCOMING_UI_CONTRACT=PASS

# Timer fallback must execute the same full autonomy guardian as the long-running runtime.
grep -q 'telephony_autonomy_guardian(100)' backend/telephony_guardian_runner.py
grep -q 'run_guardian_once' backend/telephony_guardian_runner.py
echo PHONE_FALLBACK_GUARDIAN_FULL_AUTONOMY=PASS

# Android incoming-call readiness must reflect the real POST_NOTIFICATIONS permission.
grep -q 'notificationPermissionGranted' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'boris-phone-native-notification-permission' clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt
grep -q 'boris-phone-native-notification-permission' frontend/app/phone-app/page.tsx
grep -q "notifications:platform==='android'?nativeNotifications:true" frontend/app/phone-app/page.tsx
grep -q 'notifications_capable' backend/app/services/telephony_core.py
grep -q "capabilities_json->>'notifications'" backend/app/services/telephony_core.py
echo ANDROID_NOTIFICATION_PERMISSION_TRUTH=PASS
echo PHONE_ANDROID_ROUTING_PERMISSION_GATE=PASS

# Android self-managed incoming notification exposes native answer/reject controls.
grep -q 'ACTION_ANSWER' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
grep -q 'ACTION_REJECT' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
grep -q 'Ответить' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
grep -q 'Отклонить' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
grep -q 'NativeCallControlBus.dispatch' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
echo ANDROID_NATIVE_INCOMING_ACTIONS=PASS

# Native ringing UI is dismissed by an opaque terminal push when the call finishes elsewhere.
grep -q 'def _queue_terminal_call_pushes' backend/app/services/telephony_core.py
grep -q '_queue_terminal_call_pushes(account_id,call_id,event_type)' backend/app/services/telephony_core.py
grep -q '"call.ended" to "ended"' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisFirebaseMessagingService.kt
grep -q 'BorisPhoneService.cancelIncoming' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisFirebaseMessagingService.kt
grep -q '"call.ended":"ended"' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
grep -q 'syncBackendState(callId:callId,state:state)' clients/boris-phone/ios/BORISPhone/PhoneRuntime.swift
echo PHONE_NATIVE_TERMINAL_PUSH_DISMISS=PASS

# Provider acceptance is not enough for terminal dismiss: missing device receipt retries the same bounded durable intent.
grep -q 'terminal_receipt_requeued' backend/app/services/telephony_core.py
grep -q 'provider accepted terminal event; device receipt missing' backend/app/services/telephony_core.py
grep -q "o.event_type IN ('call.transferred','call.ended','call.missed','call.rejected','call.busy','call.failed','call.cancelled')" backend/app/services/telephony_core.py
echo PHONE_TERMINAL_PUSH_RECEIPT_RETRY=PASS

# Readiness evidence must be bound to the currently configured provider.
grep -q "c.provider=COALESCE((SELECT NULLIF(provider,'') FROM telephony_provider_configs WHERE account_id=:a),c.provider)" backend/app/services/telephony_core.py
grep -q 'test_old_provider_evidence_is_not_reused_after_provider_switch' backend/tests/test_telephony_db_integration.py
echo PHONE_CURRENT_PROVIDER_EVIDENCE_BINDING=PASS

# Owner readiness must expose exact native/media blockers instead of requiring manual server inspection.
grep -q 'def phone_native_release_environment' backend/app/services/telephony_core.py
grep -q 'def phone_media_release_diagnostics' backend/app/services/telephony_core.py
grep -q "'native_release_environment':native_env" backend/app/services/telephony_core.py
grep -q "'media_diagnostics':media_diagnostics" backend/app/services/telephony_core.py
echo PHONE_RELEASE_BLOCKER_DIAGNOSTICS=PASS

# Owner Phone UI must show exact media blockers instead of a generic red state.
grep -q 'Передача звука' frontend/app/dashboard/phone/page.tsx
grep -q 'clientReadiness.media_diagnostics' frontend/app/dashboard/phone/page.tsx
echo PHONE_OWNER_MEDIA_DIAGNOSTICS_UI=PASS

# Telecom onSilence and failed native controls must not restart ringtone.
grep -q 'setSilent(silent)' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisPhoneService.kt
grep -q 'override fun onSilence(){ BorisPhoneService.showIncomingNotification(context,callId,true) }' clients/boris-phone/android/app/src/main/java/ai/boris/phone/BorisConnectionService.kt
echo ANDROID_INCOMING_SILENCE_CONTRACT=PASS

# Hardware preflights must be fail-closed and distinguish tooling from missing devices.
grep -q "local_adb=sdk/'platform-tools/adb'" backend/app/services/telephony_core.py
grep -q 'ANDROID_DEVICE_PREFLIGHT=PASS' clients/boris-phone/android/scripts/device-preflight.sh
grep -q 'BLOCKER=device_not_connected' clients/boris-phone/android/scripts/device-preflight.sh
grep -q 'REAL_LOCKED_BACKGROUND_CALL_E2E=NOT_PROVEN' clients/boris-phone/android/scripts/device-preflight.sh
grep -q 'IOS_DEVICE_PREFLIGHT=PASS' clients/boris-phone/ios/scripts/device-preflight.sh
grep -q 'CALLKIT_LOCKED_BACKGROUND_E2E=NOT_PROVEN' clients/boris-phone/ios/scripts/device-preflight.sh
echo PHONE_REAL_DEVICE_PREFLIGHT_CONTRACT=PASS

# Web/native device registration must allocate a stable ID before the first API call.
# This prevents multi-tab races from creating duplicate device rows and prevents
# dashboard event polling from bypassing an owner revoke by auto-rotating IDs.
grep -q "localStorage.setItem(key,did)" frontend/app/phone-app/page.tsx
grep -q "device_id:did" frontend/app/phone-app/page.tsx
grep -q "localStorage.setItem(deviceKey,id)" frontend/app/dashboard/phone/page.tsx
grep -q "device_id:id" frontend/app/dashboard/phone/page.tsx
grep -q "},\[accountId\]);" frontend/app/dashboard/phone/page.tsx
if grep -q "localStorage.removeItem(deviceKey);id='';setDeviceId('')" frontend/app/dashboard/phone/page.tsx; then
  echo PHONE_DEVICE_REGISTRATION_STABILITY=FAIL
  exit 1
fi
echo PHONE_DEVICE_REGISTRATION_STABILITY=PASS

# Telphin has two deliberately separate production boundaries:
# OAuth REST for API/records and SIP registration for real BORIS Phone audio.
# SIP/media readiness is valid only after the exact rendered config is applied
# and Asterisk reports the registration as Registered.
grep -q "'telphin': {'name':'Телфин'" backend/app/services/telephony_core.py
grep -q "'telphin': TelphinAdapter()" backend/app/services/telephony_adapters/registry.py
grep -q 'supported_commands = {"answer", "hold", "resume", "hangup", "transfer"}' backend/app/services/telephony_adapters/telphin.py
grep -q 'capabilities = {"api", "outbound_callback", "call_control", "media_session", "sip_media", "webrtc"}' backend/app/services/telephony_adapters/telphin.py
grep -q 'grant_type.*client_credentials' backend/app/services/telephony_adapters/telphin.py
grep -q 'def telphin_sip_guardian' backend/app/services/telphin_sip_trunk.py
grep -q 'def registration_health' backend/app/services/telphin_sip_trunk.py
grep -q 'def originate_telphin' backend/app/services/asterisk_gateway.py
grep -q 'def command_telphin' backend/app/services/asterisk_gateway.py
grep -q 'mode == "telphin-inbound"' backend/app/services/asterisk_gateway.py
grep -q 'def bind_browser_media' backend/app/services/asterisk_gateway.py
grep -q 'def activate_media_session' backend/app/services/telephony_core.py
grep -q "@router.post('/media/session/activate')" backend/app/api/telephony.py
grep -q 'telphin_sip_guardian()' backend/telephony_runtime_runner.py
grep -q 'degraded_telphin_sip' backend/app/services/telephony_core.py
grep -q "lower(account_id) ~ '^__.*qa'" backend/app/services/telphin_sip_trunk.py
test -f backend/migrations/037_telphin_sip_trunk_state.sql
test -f backend/migrations/038_telephony_media_bridge_binding.sql
grep -q 'providerSecrets.sip_line' frontend/app/dashboard/phone/page.tsx
grep -q 'providerSecrets.sip_password' frontend/app/dashboard/phone/page.tsx
grep -q 'SIP-линия для реального звука' frontend/app/dashboard/phone/page.tsx
if grep -q '"-rx", "pjsip reload"' backend/app/services/asterisk_gateway.py; then
  echo PHONE_TELPHIN_CONTRACT=FAIL
  exit 1
fi
echo PHONE_TELPHIN_CONTRACT=PASS


# Telphin/MCN Asterisk recordings must enter the shared recording -> STT -> AI
# pipeline with the real provider identity. Recording policy is account-scoped,
# fail-closed by default and not inferred by BORIS.
grep -q 'provider not in {"mcn", "telphin"}' backend/app/services/asterisk_gateway.py
grep -q 'call_provider' backend/app/services/asterisk_gateway.py
grep -q "provider=str(row.get(\"call_provider\")" backend/app/services/asterisk_gateway.py
grep -q 'def recording_policy_settings' backend/app/services/telephony_core.py
grep -q 'def save_recording_policy_settings' backend/app/services/telephony_core.py
grep -q "policy IN ('manual','disabled','not_required')" backend/migrations/039_telephony_recording_policy.sql
grep -q "@router.get('/recording/settings')" backend/app/api/telephony.py
grep -q "@router.post('/recording/settings')" backend/app/api/telephony.py
grep -q "recordingCfg.policy==='not_required'" frontend/app/dashboard/phone/page.tsx
grep -q 'Подтверждаю правило аккаунта' frontend/app/dashboard/phone/page.tsx
grep -q 'owner_identity_required' backend/app/services/telephony_core.py
echo PHONE_RECORDING_POLICY_AUTONOMY=PASS

grep -q 'def _phone_primary_next_action' backend/app/services/telephony_core.py
grep -q "'primary_next_action':primary_next_action" backend/app/services/telephony_core.py
grep -q 'Что сейчас делать:' frontend/app/dashboard/phone/page.tsx
grep -q 'Исполнитель:' frontend/app/dashboard/phone/page.tsx
echo PHONE_PRIMARY_NEXT_ACTION=PASS

grep -q 'rop_entitlement_required' backend/app/services/telephony_core.py
grep -q 'recording_ai_entitlement' backend/app/services/telephony_core.py
grep -q 'recording_ai_entitlement_check' backend/app/services/telephony_core.py
echo PHONE_RECORDING_AI_ENTITLEMENT_TRUTH=PASS

# BORIS WebRTC v1 client contract: media is requested only after backend marks
# the verified provider media-capable; signaling is WSS and short-lived.
grep -q "boris-webrtc-v1" backend/app/services/telephony_core.py
grep -q "new RTCPeerConnection" frontend/app/phone-app/page.tsx
grep -q "new WebSocket(m.signaling_url)" frontend/app/phone-app/page.tsx
grep -q "/api/telephony/media/session" frontend/app/phone-app/page.tsx
grep -q "mediaStreamRef.current" frontend/app/phone-app/page.tsx
grep -q "signaling_protocol?:'boris-webrtc-v1'" clients/boris-phone/shared/protocol.ts
grep -q "media_signaling_url_invalid" backend/app/services/telephony_core.py
echo PHONE_WEBRTC_MEDIA_CONSUMER=PASS

# A safe media-session contract or active bridge is NOT proof of real audio.
# Production media readiness requires bidirectional RTP counters reported by the
# exact authenticated call/device/lease after activation.
grep -q 'def report_media_transport_proof' backend/app/services/telephony_core.py
grep -q "action='media.transport.proven'" backend/app/services/telephony_core.py
grep -q "@router.post('/media/session/proof')" backend/app/api/telephony.py
grep -q 'mediaTransportProof' clients/boris-phone/shared/protocol.ts
grep -q "'real_media_provider':transport_evidence\['media_transport_proven'\]>0" backend/app/services/telephony_core.py
if grep -q "'real_media_provider':transport_evidence\['media_sessions_ok'\]>0" backend/app/services/telephony_core.py; then
  echo PHONE_REAL_MEDIA_TRUTH=FAIL
  exit 1
fi
# The browser/WebView media engine is the component that can observe actual
# RTCPeerConnection counters. A server endpoint alone is not enough: the
# release gate must also prove that the client collects stats and posts them
# back to the authenticated lease-scoped proof endpoint.
if ! grep -q '/api/telephony/media/session/proof' frontend/app/phone-app/page.tsx; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_missing_proof_post
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Eq 'getStats\(' frontend/app/phone-app/page.tsx frontend/app/phone-app/sipMedia.ts; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_missing_webrtc_stats
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Eq 'packetsSent|packets_sent' frontend/app/phone-app/page.tsx frontend/app/phone-app/sipMedia.ts || ! grep -Eq 'packetsReceived|packets_received' frontend/app/phone-app/page.tsx frontend/app/phone-app/sipMedia.ts; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_missing_bidirectional_counters
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
proof_arm_count="$(grep -o 'armProof(' frontend/app/phone-app/page.tsx | wc -l | tr -d ' ')"
if [ "${proof_arm_count:-0}" -lt 2 ]; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_proof_not_wired_all_media_branches
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_ARM_COUNT="${proof_arm_count:-0}"
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Eq 'armProof\([^;]*transportStats\(' frontend/app/phone-app/page.tsx; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_sip_media_proof_not_armed
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Eq 'armProof\([^;]*readBorisMediaTransportStats\(pc\)' frontend/app/phone-app/page.tsx; then
  echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=FAIL_webrtc_media_proof_not_armed
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
echo PHONE_REAL_MEDIA_CLIENT_PROOF_PIPELINE=PASS
echo PHONE_REAL_MEDIA_TRUTH=PASS

# Telphin event/recording translation is implemented, but public unauthenticated
# Telphin ingress stays absent until a transport-authentication contract is proven.
grep -q "def recording_link" backend/app/services/telephony_adapters/telphin.py
grep -q "CallAPIID" backend/app/services/telephony_adapters/telphin.py
grep -q "storage_url" backend/app/services/telephony_adapters/telphin.py
if grep -q "/native/telphin/" backend/app/api/telephony.py; then
  echo PHONE_TELPHIN_INGRESS_AUTH_GATE=FAIL
  exit 1
fi
echo PHONE_TELPHIN_INGRESS_AUTH_GATE=PASS

# Durable media lease: no short-lived credential leaves the backend unless ownership is recorded,
# happy-path clients release it, and the autonomy guardian cleans orphaned/expired sessions.
test -f backend/migrations/036_telephony_media_session_lifecycle.sql
grep -q 'CREATE TABLE IF NOT EXISTS telephony_media_sessions' backend/migrations/036_telephony_media_session_lifecycle.sql
grep -q 'def media_session_guardian' backend/app/services/telephony_core.py
grep -q 'def release_media_session' backend/app/services/telephony_core.py
grep -q 'media_lease_unavailable' backend/app/services/telephony_core.py
grep -q "media_lease_id':int(lease_id)" backend/app/services/telephony_core.py
grep -q "@router.post('/media/session/release')" backend/app/api/telephony.py
grep -q 'media_session_guardian(100)' backend/telephony_runtime_runner.py
grep -q 'media_lease_id:lease.id' frontend/app/phone-app/page.tsx
grep -q 'durable_media_lease' backend/app/services/telephony_core.py
echo PHONE_DURABLE_MEDIA_LEASE=PASS

# Long BORIS calls must renew durable media ownership without reissuing provider secrets.
# The client rotates the temporary BORIS endpoint before TURN REST credentials expire.
grep -q 'def renew_media_session' backend/app/services/telephony_core.py
grep -q "@router.post('/media/session/renew')" backend/app/api/telephony.py
grep -q "/api/telephony/media/session/renew" frontend/app/phone-app/page.tsx
grep -q 'armRenewal' frontend/app/phone-app/page.tsx
grep -q '45\*60\*1000' frontend/app/phone-app/page.tsx
grep -q 'media_lease_renewal' backend/app/services/telephony_core.py
echo PHONE_MEDIA_LEASE_RENEWAL=PASS

# TURN is a BORIS-managed production dependency: runtime truth is probed, degraded
# state enters autonomy, and the telephony runtime continuously self-heals it.
grep -q 'def turn_runtime_guardian' backend/app/services/phone_media_gateway.py
grep -q 'def turn_media_guardian' backend/app/services/telephony_core.py
grep -q 'degraded_turn_media' backend/app/services/telephony_core.py
grep -q 'turn_media_guardian(False)' backend/telephony_runtime_runner.py
grep -q 'boris_turn_not_ready' backend/app/services/telephony_core.py
grep -q 'TURN REST credential' backend/app/services/phone_media_gateway.py
echo PHONE_TURN_AUTONOMY=PASS

# BORIS SIP/WebRTC gateway consumer is explicit and separate from provider readiness.
test -f frontend/app/phone-app/sipMedia.ts
grep -q "import { Web } from 'sip.js'" frontend/app/phone-app/sipMedia.ts
grep -q 'startBorisSipMedia' frontend/app/phone-app/page.tsx
grep -Fq '"signaling_protocol": "boris-sip-v1"' backend/app/services/phone_media_gateway.py
grep -Fq '"managed_by": "boris_gateway"' backend/app/services/phone_media_gateway.py
grep -q "boris-sip-v1" clients/boris-phone/shared/protocol.ts
echo PHONE_SIP_WSS_MEDIA_CONSUMER=PASS

# Real-call evidence must be checkable without originating calls or reading secrets.
test -f backend/scripts/phone-real-e2e-check.py
grep -q 'read-only evidence report' backend/scripts/phone-real-e2e-check.py
grep -q '127.0.0.1:8000/api/telephony/client/readiness' backend/scripts/phone-real-e2e-check.py
if grep -Eq 'originate_telphin|outbound_call\(|create_call\(' backend/scripts/phone-real-e2e-check.py; then
  echo PHONE_REAL_E2E_EVIDENCE_CLI=FAIL
  exit 1
fi
echo PHONE_REAL_E2E_EVIDENCE_CLI=PASS

# The MCN company-card approval is a real owner action. Backend safety is not
# enough: the product UI must expose the protected owner-only send route so the
# owner is never forced to operate scripts/CLI manually.
grep -q "@router.post('/mcn/company-card/send')" backend/app/api/telephony.py
grep -q 'mcn_company_card_send_approval' backend/app/api/telephony.py
grep -q 'approval_supported' backend/app/api/telephony.py
grep -q 'expected_card_fingerprint' backend/app/api/telephony.py
if ! grep -Rqs '/api/telephony/mcn/company-card/send' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_OWNER_APPROVAL_UI=FAIL_missing_company_card_send_action
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs 'confirm_share_banking' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_OWNER_APPROVAL_UI=FAIL_missing_explicit_banking_confirmation
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs 'expected_card_fingerprint' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_OWNER_APPROVAL_UI=FAIL_missing_card_fingerprint_guard
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs 'window.confirm' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_OWNER_APPROVAL_UI=FAIL_missing_owner_confirmation_dialog
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
echo PHONE_MCN_OWNER_APPROVAL_UI=PASS

# A real carrier line must never be attached merely because an account/Inbox
# exists. Phone has its own paid entitlement because legacy account_slots has a
# unique(account_id) shape and cannot safely represent Inbox + Phone together.
PHONE_COMMERCIAL_FAIL=0
if grep -q 'CREATE TABLE IF NOT EXISTS telephony_entitlements' backend/app/services/telephony_core.py   && grep -q 'def provision_paid_phone_entitlement' backend/app/services/telephony_core.py   && grep -q 'def active_phone_entitlements' backend/app/services/telephony_core.py   && grep -q "/platform/phone-entitlement/activate" backend/app/api/telephony.py   && grep -q 'confirm_paid_phone' backend/app/api/telephony.py   && grep -q '_require_private_platform_owner' backend/app/api/telephony.py   && grep -q 'active_phone_entitlements' backend/telephony_guardian_runner.py   && grep -q 'active_phone_entitlements' backend/scripts/phone-mcn-mailbox-onboard.py; then
  echo PHONE_COMMERCIAL_ACTIVATION_PATH=PASS
else
  echo PHONE_COMMERCIAL_ACTIVATION_PATH=FAIL_missing_paid_phone_entitlement_contract
  PHONE_COMMERCIAL_FAIL=1
fi

# Once MCN has supplied a complete SIP/DID letter, zero or multiple paid Phone
# targets are different owner decisions. The UI must surface both outcomes
# instead of leaving the owner to discover a silent backend wait.
grep -q 'mcn_phone_entitlement_required' backend/telephony_guardian_runner.py
grep -q 'mcn_phone_account_binding_required' backend/telephony_guardian_runner.py
if ! grep -Rqs 'mcn_phone_entitlement_required' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_ACCOUNT_BINDING_UI=FAIL_missing_phone_entitlement_action
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs 'mcn_phone_account_binding_required' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_ACCOUNT_BINDING_UI=FAIL_missing_account_binding_action
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs '/api/telephony/platform/phone-entitlement/activate' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_ACCOUNT_BINDING_UI=FAIL_entitlement_action_has_no_activation_api
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
if ! grep -Rqs 'confirm_paid_phone' frontend/app/phone-app frontend/app/dashboard/phone   || ! grep -Rqs 'commercial_ref' frontend/app/phone-app frontend/app/dashboard/phone   || ! grep -Rqs 'paid_until' frontend/app/phone-app frontend/app/dashboard/phone; then
  echo PHONE_MCN_ACCOUNT_BINDING_UI=FAIL_entitlement_activation_missing_commercial_evidence
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi
echo PHONE_MCN_ACCOUNT_BINDING_UI=PASS

# Paid Phone must also be reachable from the normal commercial pipeline.
# Manual owner activation is a safe fallback, not the primary client payment path.
if grep -q 'phone_subscription' backend/app/api/payments.py   && grep -q 'provision_paid_phone_entitlement' backend/app/api/payments.py   && grep -q 'BORIS_PHONE_MONTHLY_PRICE_RUB' backend/app/api/payments.py   && grep -q '@router.post("/purchase")' backend/app/api/wallet.py   && grep -q '/api/wallet/purchase' frontend/app/dashboard/billing/page.tsx   && grep -q 'phone_monthly' frontend/app/dashboard/billing/page.tsx; then
  echo PHONE_COMMERCIAL_PAYMENT_PIPELINE=PASS
else
  echo PHONE_COMMERCIAL_PAYMENT_PIPELINE=FAIL
  PHONE_COMMERCIAL_FAIL=1
fi

if PYTHONPATH=backend "$py" - <<'PY'
from app.api.payments import PACKAGES
raise SystemExit(0 if "phone_monthly" in PACKAGES else 1)
PY
then
  echo PHONE_COMMERCIAL_PRICE_CONFIG=PASS
else
  echo PHONE_COMMERCIAL_PRICE_CONFIG=WAITING_REAL_PRICE
fi

if [ "$PHONE_COMMERCIAL_FAIL" -ne 0 ]; then
  echo PHONE_PRODUCT_QA_FINAL=FAIL
  exit 2
fi

# External MCN waiting must not turn the owner into a mailbox operator. BORIS
# follows up only after a long delay, without attachments/secrets, and stops
# after a bounded number of confirmed reminders or any ambiguous delivery.
grep -q 'def _mcn_company_card_followup' backend/telephony_guardian_runner.py
grep -q '_MCN_FOLLOWUP_FIRST_DELAY_SECONDS = 48 \* 3600' backend/telephony_guardian_runner.py
grep -q '_MCN_FOLLOWUP_MAX_ATTEMPTS = 2' backend/telephony_guardian_runner.py
grep -q 'X-BORIS-Auto-Followup' backend/telephony_guardian_runner.py
grep -q 'sender_email' backend/scripts/phone-mcn-mailbox-onboard.py
grep -q 'mcn_company_card_response_sla' backend/telephony_guardian_runner.py
echo PHONE_MCN_BOUNDED_EXTERNAL_FOLLOWUP=PASS

# Any new owner-required telephony state must stay visible even before a
# dedicated UI card exists, otherwise backend self-diagnosis can be hidden.
grep -q 'mcnOnboarding?.owner_action_required' frontend/app/dashboard/phone/page.tsx
grep -q 'Нужно решение владельца' frontend/app/dashboard/phone/page.tsx
grep -q 'не повторяет опасные внешние действия автоматически' frontend/app/dashboard/phone/page.tsx
echo PHONE_OWNER_ACTION_FALLBACK_UI=PASS

# Commercial runtime gate: saved carrier credentials are not a substitute for
# a currently paid BORIS Phone entitlement. New provider mutations and outbound
# provider I/O must fail closed before touching credentials/operator transport.
grep -q "entitlement=phone_entitlement_status(account_id)" backend/app/services/telephony_core.py
grep -q "'status':'phone_entitlement_required'" backend/app/services/telephony_core.py
grep -q 'def _require_active_phone_entitlement' backend/app/api/telephony.py
test "$(grep -c '_require_active_phone_entitlement(account_id)' backend/app/api/telephony.py)" -ge 2
grep -q 'def _assert_phone_entitlement' backend/app/api/mcn_phone.py
test "$(grep -c '_assert_phone_entitlement(account_id)' backend/app/api/mcn_phone.py)" -ge 3
grep -q 'test_unpaid_phone_stops_before_provider_credentials_or_adapter_io' backend/tests/test_telephony_db_integration.py
grep -q 'test_unpaid_phone_blocks_provider_verify_before_external_probe' backend/tests/test_telephony_api_access.py
grep -q 'test_unpaid_phone_blocks_atomic_mcn_onboard_before_storage' backend/tests/test_mcn_api_onboarding.py
grep -q 'telephony_entitlements e' backend/app/services/asterisk_gateway.py
grep -q 'e.paid_until>now()' backend/app/services/asterisk_gateway.py
grep -q "return {'status':'phone_entitlement_required','owner_action_required':False}" backend/app/services/asterisk_gateway.py
grep -q 'test_unpaid_phone_blocks_direct_mcn_origination_before_asterisk_probe' backend/tests/test_mcn_deferred_asterisk_recovery.py
echo PHONE_COMMERCIAL_RUNTIME_GATE=PASS

echo PHONE_PRODUCT_QA_FINAL=PASS
