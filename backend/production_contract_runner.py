"""BORIS production contract reconciler.

One shared policy version for every connected paid account. The contract verifies
both source/runtime invariants and stamps account-scoped rollout state. It does
not clone business logic per account.
"""
import datetime
import json
import pathlib
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.storage import Storage

POLICY_VERSION = "2026-09-04.iron-v102-programming-journal-truth"
ROOT = pathlib.Path("/root/BORIS")
POLICY_REQUIRED_ACCOUNTS = {x.strip() for x in __import__('os').getenv("BORIS_POLICY_REQUIRED_ACCOUNTS", "").split(",") if x.strip()}
POLICY_EXCLUDED_ACCOUNTS = {x.strip() for x in __import__('os').getenv("BORIS_POLICY_EXCLUDED_ACCOUNTS", "grant_kaluzhskaya_oblast_beton_plity_77392").split(",") if x.strip()}

def _is_real_rollout_account(account_id: str) -> bool:
    aid=(account_id or "").strip().lower()
    if not aid or aid in {x.lower() for x in POLICY_EXCLUDED_ACCOUNTS}:
        return False
    return not aid.startswith(("qa_","qa-","__qa","test_","test-","testref_","demo_","sandbox_"))
REQUIRED_MARKERS = {
    "global_acceptance_state_machine": (ROOT / "backend/global_production_acceptance.py", "GLOBAL_PRODUCTION_ACCEPTANCE_V1"),
    "global_acceptance_guardian_wire": (ROOT / "backend/production_guardian_tick.sh", "GLOBAL_PRODUCTION_ACCEPTANCE_V1"),
    "action_log_rollback_exactly_once": (ROOT / "backend/app/api/action_log_api.py", "ACTION_LOG_ROLLBACK_EXACTLY_ONCE_V1"),
    "command_center_execution_exactly_once": (ROOT / "backend/app/services/command_flow.py", "EXECUTION_CLAIM_EXACTLY_ONCE_V1"),
    "command_center_terminal_cancel_guard": (ROOT / "backend/app/services/command_flow.py", "TERMINAL_CANCEL_GUARD_V1"),
    "command_center_idempotent_create": (ROOT / "backend/app/services/command_store.py", "business idempotency key for Command Center"),
    "command_center_terminal_db_guard_sql": (ROOT / "backend/scripts/sql/command_center_terminal_guard_20260903.sql", "COMMAND_CENTER_TERMINAL_DB_GUARD_V1"),
    "command_center_zombie_recovery": (ROOT / "backend/app/services/command_audit.py", "COMMAND_CENTER_ZOMBIE_RECOVERY_V1"),
    "command_center_maintenance_runner": (ROOT / "backend/scripts/command_center_maintenance.py", "reconcile_lifecycle"),
    "billing_usage_idempotency": (ROOT / "backend/app/api/billing.py", "BILLING_USAGE_IDEMPOTENCY_V1"),
    "billing_usage_idempotency_selfheal": (ROOT / "backend/app/api/billing.py", "BILLING_USAGE_IDEMPOTENCY_SELF_HEAL_V1"),
    "campaign_banner_billing_idempotency": (ROOT / "backend/app/api/campaigns.py", "billing_pending_banners_key"),
    "campaign_banner_child_tariff_before_mutation": (ROOT / "backend/app/api/campaigns.py", "CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1"),
    "avito_batch_banner_billing_idempotency": (ROOT / "backend/app/api/avito.py", "BILLING_CHILD_INTENT_EXACTLY_ONCE_V1"),
    "avito_batch_banner_billing_before_draft_mutation": (ROOT / "backend/app/api/avito.py", "if _billing_pending:"),
    "draft_storage_atomic_mutation": (ROOT / "backend/app/api/avito.py", "DRAFT_STORAGE_ATOMIC_MUTATION_V1"),
    "draft_publish_remove_atomic": (ROOT / "backend/app/api/avito.py", "DRAFT_PUBLISH_REMOVE_ATOMIC_V1"),
    "draft_banner_merge_atomic": (ROOT / "backend/app/api/avito.py", "DRAFT_BATCH_BANNER_ATOMIC_MERGE_V1"),
    "draft_replace_revision_cas": (ROOT / "backend/app/api/avito.py", "draft_revision_conflict"),
    "plan_draft_atomic_append": (ROOT / "backend/app/api/plan_items.py", "PLAN_DRAFT_APPEND_ATOMIC_V2"),
    "pipeline_draft_exactly_once": (ROOT / "backend/app/api/tasks.py", "PIPELINE_DRAFT_TARIFF_EXACTLY_ONCE_V1"),
    "pipeline_text_stable_intent": (ROOT / "backend/app/api/tasks.py", "PIPELINE_TEXT_ECONOMIC_INTENT_V1"),
    "parsed_draft_exactly_once": (ROOT / "backend/app/api/parser.py", "PARSED_DRAFT_TARIFF_EXACTLY_ONCE_V1"),
    "parsed_draft_atomic_append": (ROOT / "backend/app/api/parser.py", "PARSED_DRAFT_APPEND_ATOMIC_V1"),
    "feed_storage_atomic_mutation": (ROOT / "backend/app/api/avito.py", "FEED_STORAGE_ATOMIC_MUTATION_V1"),
    "feed_publish_atomic_upsert": (ROOT / "backend/app/api/avito.py", "FEED_PUBLISH_UPSERT_ATOMIC_V1"),
    "feed_item_edit_atomic": (ROOT / "backend/app/api/avito.py", "FEED_ITEM_EDIT_ATOMIC_V1"),
    "campaign_activation_feed_atomic": (ROOT / "backend/app/api/campaigns.py", "CAMPAIGN_ACTIVATION_FEED_ATOMIC_V1"),
    "auto_duplicate_free_preview": (ROOT / "backend/app/api/avito.py", "AUTO_DUPLICATE_FREE_PREVIEW_V1"),
    "auto_duplicate_tariff_exactly_once": (ROOT / "backend/app/api/avito.py", "AUTO_DUPLICATE_LISTING_TARIFF_EXACTLY_ONCE_V1"),
    "auto_duplicate_manifest_exactly_once": (ROOT / "backend/app/api/avito.py", "AUTO_DUPLICATE_MANIFEST_EXACTLY_ONCE_V1"),
    "scenario_duplicate_run_idempotency": (ROOT / "backend/app/api/home.py", "SCENARIO_DUPLICATE_RUN_IDEMPOTENCY_V1"),
    "storage_browser_singleton_atomic": (ROOT / "backend/app/api/browser_gateway.py", "STORAGE_SINGLETON_ATOMIC_PUT_V1"),
    "storage_mass_editor_singleton_atomic": (ROOT / "backend/app/api/mass_editor.py", "STORAGE_SINGLETON_ATOMIC_PUT_V1"),
    "storage_kpi_history_atomic": (ROOT / "backend/app/api/avito.py", "KPI_HISTORY_ATOMIC_SINGLETON_V1"),
    "storage_singleton_reconciler": (ROOT / "backend/scripts/storage_singleton_reconcile.py", "Known list/object/cache keys have deterministic merge policies"),
    "storage_singleton_timer": (ROOT / "backend/scripts/storage_singleton_guard.sh", "STORAGE_SINGLETON_GUARD_PASS"),
    "programming_journal_truth_api": (ROOT / "backend/app/api/control_plane.py", "workspace_programming_journal"),
    "programming_journal_no_fake_progress": (ROOT / "backend/app/api/control_plane.py", "no invented progress"),
    "development_queue_truth_snapshot": (ROOT / "backend/app/ext_api/recover.py", "QUEUE_TRUTH_SNAPSHOT_V1"),
    "workspace_runtime_auto_sync": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_RUNTIME_AUTO_SYNC_V1"),
    "workspace_execution_scope_dedupe": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_EXECUTION_SCOPE_DEDUPE_V1"),
    "workspace_execution_runtime_not_task": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_EXECUTION_RUNTIME_NOT_TASK_V1"),
    "workspace_canonical_scope_alias": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_CANONICAL_SCOPE_ALIAS_V1"),
    "workspace_restore_canonical_scope": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_RESTORE_CANONICAL_SCOPE_V1"),
    "workspace_scope_dedupe_selfheal": (ROOT / "backend/app/ext_api/workspace_sync.py", "WORKSPACE_SCOPE_DEDUPE_SELF_HEAL_V1"),
    "workspace_no_owner_fake_progress": (ROOT / "backend/app/api/control_plane.py", "WORKSPACE_NO_OWNER_FAKE_PROGRESS_V1"),
    "workspace_truth_guard": (ROOT / "backend/scripts/workspace_truth_guard.sh", "WORKSPACE_TRUTH_GUARD=PASS"),
    "workspace_event_created_at_truth": (ROOT / "backend/app/api/control_plane.py", "e.created_at.isoformat() if e.created_at else None"),
    "messages_non_dialogue_event_scope": (ROOT / "backend/app/services/control_plane_adapters.py", "MESSAGE_NON_DIALOGUE_EVENT_SCOPE_V1"),
    "mop_autosend_policy_explicit": (ROOT / "backend/app/services/control_plane_adapters.py", "MOP_AUTOSEND_POLICY_EXPLICIT_V1"),
    "crm_next_action_mop_scope_parity": (ROOT / "backend/app/services/brain_recovery.py", "CRM_NEXT_ACTION_MOP_SCOPE_PARITY_V1"),
    "crm_non_dialogue_scope_cleanup": (ROOT / "backend/app/services/brain_recovery.py", "CRM_NON_DIALOGUE_AND_MOP_SCOPE_TASK_CLEANUP_V1"),
    "crm_mop_owned_obligation_no_human_task": (ROOT / "backend/app/services/brain_recovery.py", "CRM_MOP_OWNED_OBLIGATION_NO_HUMAN_TASK_V1"),
    "crm_diag_mop_owned_obligation": (ROOT / "backend/app/services/control_plane_adapters.py", "CRM_DIAG_MOP_OWNED_OBLIGATION_V1"),
    "reactivation_402_exact_chat_cooldown": (ROOT / "backend/app/reactivation_runtime.py", "REACTIVATION_402_EXACT_CHAT_COOLDOWN_V1"),
    "storage_core_singleton_db_guard_sql": (ROOT / "backend/scripts/sql/storage_core_singleton_unique_20260903.sql", "STORAGE_CORE_SINGLETON_DB_GUARD_V1"),
    "notification_storage_atomic": (ROOT / "backend/app/services/notification_store.py", "NOTIFICATION_STORAGE_ATOMIC_V1"),
    "notification_chat_atomic_read": (ROOT / "backend/app/api/chat.py", "notification store"),
    "notification_tasks_atomic_append": (ROOT / "backend/app/api/tasks.py", "параллельные workers не теряют сообщения"),
    "notification_support_atomic_append": (ROOT / "backend/app/api/support.py", "атомарно в кабинет клиента"),
    "notification_onboarding_atomic_append": (ROOT / "backend/app/onboarding.py", "без потери параллельных сообщений"),
    "command_ui_ambiguous_retry_idempotency": (ROOT / "frontend/app/command/page.tsx", "COMMAND_UI_AMBIGUOUS_RETRY_IDEMPOTENCY_V1"),
    "command_client_forwards_business_id": (ROOT / "frontend/app/lib/commandClient.ts", "command_id: opts?.commandId"),
    "marketer_waiting_stage_truth": (ROOT / "backend/kpi_goal_runner.py", "MARKETER_WAITING_STAGE_TRUTH_V2"),
    "marketer_health_latest_evidence": (ROOT / "backend/app/api/reliability.py", "MARKETER_HEALTH_LATEST_EVIDENCE_V1"),
    "feed_source_text": (ROOT / "backend/app/api/campaigns.py", "РЕЖИМ РЕДАКТОРА"),
    "feed_search_reco": (ROOT / "backend/app/api/campaigns.py", "workflow/search_recommendations/apply"),
    "copywriter": (ROOT / "backend/app/api/campaigns.py", "РЕЖИМ СИЛЬНОГО КОПИРАЙТЕРА BORIS"),
    "copy_quality_gate": (ROOT / "backend/app/api/campaigns.py", "copy_quality_contract"),
    "copy_quality_single_paid_attempt": (ROOT / "backend/app/api/campaigns.py", "single_paid_attempt_fail_closed"),
    "copy_description_unique": (ROOT / "backend/app/api/campaigns.py", "duplicate_description"),
    "campaign_text_single_provider": (ROOT / "backend/app/api/campaigns.py", "single_paid_attempt=True"),
    "campaign_text_budget_guard": (ROOT / "backend/gigachat_pool.py", 'module="campaign_text"'),
    "campaign_text_ambiguous_id": (ROOT / "backend/gigachat_pool.py", "ambiguous-text:"),
    "campaign_paid_durable_cache": (ROOT / "backend/app/api/campaigns.py", "campaign_paid_exactly_once_v2"),
    "campaign_paid_provider_idempotency": (ROOT / "backend/app/api/campaigns.py", "Idempotency-Key"),
    "campaign_paid_ambiguous_tombstone": (ROOT / "backend/app/api/campaigns.py", "ambiguous-text:campaign:"),
    "social_paid_auto_explicit_optin": (ROOT / "backend/posting_runner.py", "PAID_AI_AUTO_DISABLED:"),
    "social_text_canonical_paid_contour": (ROOT / "backend/posting_runner.py", "_ff_guarded_openai_response"),
    "social_text_stable_idempotency": (ROOT / "backend/posting_runner.py", "social-post-text:"),
    "social_image_stable_artifact_intent": (ROOT / "backend/posting_runner.py", "_social_"),
    "stock_vision_canonical_guard": (ROOT / "backend/app/api/media.py", "_vision_chat_json"),
    "stock_vision_remote_url": (ROOT / "backend/app/api/media.py", "image_url = str(c.get(\"image\")"),
    "banner_v4_copy_canonical_guard": (ROOT / "backend/app/api/banners.py", "Reuse the existing canonical paid Responses contour"),
    "banner_reference_vision_single_intent": (ROOT / "backend/app/api/banners.py", "One paid vision intent per reference image"),
    "banner_reference_multi_single_intent": (ROOT / "backend/app/api/banners.py", "One paid vision intent for the selected reference set"),
    "banner_classify_canonical_guard": (ROOT / "backend/app/api/banners.py", "banner-classify:"),
    "campaign_banner_normal_intent_deterministic": (ROOT / "backend/app/api/campaigns.py", "_artifact_intent=\"normal:\""),
    "campaign_paid_batch_explicit_confirmation": (ROOT / "backend/app/api/campaigns.py", "BORIS_PAID_IMAGE_CONFIRM_THRESHOLD"),
    "image_provider_idempotency_key": (ROOT / "backend/app/services/openai_image_transport.py", "Idempotency-Key"),
    "image_edit_provider_idempotency_key": (ROOT / "backend/app/services/image_service.py", "boris-image-edit:"),
    "image_artifact_atomic_persist": (ROOT / "backend/app/services/image_service.py", "_atomic_write_bytes"),
    "image_artifact_decode_guard": (ROOT / "backend/app/services/image_service.py", "PAID_IMAGE_ARTIFACT_INVALID"),
    "visual_watchdog_explicit_fail_closed": (ROOT / "backend/scripts/avito_visual_guard_watchdog.sh", "STEP $name FAIL"),
    "image_generation_preprovider_barrier": (ROOT / "backend/app/services/image_service.py", "provider_in_progress"),
    "image_edit_preprovider_barrier": (ROOT / "backend/app/services/image_service.py", "_edit_provider_idem"),
    "feed_vision_paid_cross_process_lock": (ROOT / "backend/app/services/media_semantic_validator.py", "pg_advisory_lock"),
    "feed_vision_paid_durable_cache": (ROOT / "backend/app/services/media_semantic_validator.py", "feed_vision_exactly_once_v1"),
    "feed_vision_exact_provider_id": (ROOT / "backend/app/services/media_semantic_validator.py", "x-request-id"),
    "feed_vision_ambiguous_id": (ROOT / "backend/app/services/media_semantic_validator.py", "ambiguous-vision:"),
    "feed_vision_policy_version": (ROOT / "backend/app/services/media_semantic_validator.py", "SEMANTIC_POLICY_VERSION"),
    "feed_media_invalid_provenance_guard": (ROOT / "backend/app/services/media_semantic_validator.py", "media_provenance_invalid"),
    "feed_media_legacy_family_cache": (ROOT / "backend/app/services/media_semantic_validator.py", "LEGACY_SEMANTIC_CLASS_FAMILY_CACHE_V1"),
    "feed_factory_current_scope_reconcile": (ROOT / "backend/app/api/campaigns.py", "FEED_FACTORY_CURRENT_SCOPE_RECONCILE_V1"),
    "feed_media_sha_quarantine_guard": (ROOT / "backend/app/services/media_service.py", "source-laundered Avito visual bytes are quarantined"),
    "feed_scoped_contract_overlay": (ROOT / "backend/app/api/campaigns.py", "_ff_contract_with_payload_overlay"),
    "feed_schema_drift_readonly_docs": (ROOT / "backend/app/services/category_resolver.py", "official_account_docs_readonly"),
    "jobs_no_progress_guard": (ROOT / "backend/app/services/jobs.py", "_same_continuation_count"),
    "feed_publication_mutation_lock": (ROOT / "backend/app/api/campaigns.py", "campaign_publication_locked"),
    "feed_terminal_delivery_exactly_once": (ROOT / "backend/app/api/campaigns.py", "campaign_already_submitted"),
    "feed_cross_campaign_active_duplicate_preflight": (ROOT / "backend/app/api/campaigns.py", "CROSS_CAMPAIGN_ACTIVE_DUPLICATE_PREFLIGHT_V1"),
    "feed_publication_autoresume": (ROOT / "backend/app/services/jobs.py", "_publication_lock_released_at"),
    "feed_submitted_snapshot": (ROOT / "backend/app/api/campaigns.py", "submitted_snapshot"),
    "feed_review_page_auto": (ROOT / "backend/app/services/jobs.py", "generate_campaign_review_page"),
    "feed_prepare_ready_is_terminal": (ROOT / "backend/app/services/jobs.py", "READY_FOR_REVIEW_IS_TERMINAL_PREP_V1"),
    "waiting_job_terminal_reconcile": (ROOT / "backend/app/services/jobs.py", "WAITING_JOB_TERMINAL_RECONCILE_V1"),
    "feed_review_page_atomic": (ROOT / "backend/app/services/campaign_review_page.py", "os.replace(tmp, path)"),
    "feed_review_page_current_scope": (ROOT / "backend/app/services/campaign_review_page.py", "REVIEW_PAGE_CURRENT_SCOPE_V1"),
    "feed_review_page_submitted_xml_source": (ROOT / "backend/app/services/campaign_review_page.py", "REVIEW_PAGE_SUBMITTED_XML_SOURCE_V2"),
    "feed_review_page_browser_url": (ROOT / "backend/app/services/campaign_review_page.py", "def _browser_url"),
    "feed_review_page_local_media_guard": (ROOT / "backend/app/services/campaign_review_page.py", "def _assert_local_review_media"),
    "feed_review_page_mobile_overflow_guard": (ROOT / "backend/app/services/campaign_review_page.py", "overflow-wrap:anywhere"),
    "frontend_review_page_link": (ROOT / "frontend/app/dashboard/campaign/[id]/page.tsx", "Открыть результат без входа"),
    "frontend_publication_lock_aligns_backend": (ROOT / "frontend/app/dashboard/campaign/[id]/page.tsx", "FRONTEND_PUBLICATION_LOCK_ALIGNS_BACKEND_V2"),
    "feed_publication_item_status_commit": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_ITEM_STATUS_COMMIT_V1"),
    "feed_publication_item_status_selfheal": (ROOT / "backend/app/services/campaign_post_publish_watch.py", "PUBLICATION_ITEM_STATUS_SELF_HEAL_V1"),
    "feed_media_link_payload_binding": (ROOT / "backend/app/services/media_semantic_validator.py", "MEDIA_LINK_PAYLOAD_BINDING_V1"),
    "feed_media_link_payload_reconcile": (ROOT / "backend/app/services/jobs.py", "MEDIA_LINK_PAYLOAD_RECONCILE_V1"),
    "feed_dateend_stuck_old_autoload_cleanup": (ROOT / "backend/app/services/campaign_post_publish_watch.py", "DATEEND_STUCK_OLD_AUTOLOAD_CLEANUP_V1"),
    "feed_publication_identity_reconcile_helper": (ROOT / "backend/app/services/campaign_identity.py", "def reconcile_published_status"),
    "frontend_deploy_postlock_source_guard": (ROOT / "frontend/scripts/boris-frontend-deploy.sh", "FRONTEND_DEPLOY_POSTLOCK_SOURCE_GUARD_V1"),
    "frontend_deploy_final_source_guard": (ROOT / "frontend/scripts/boris-frontend-deploy.sh", "FRONTEND_DEPLOY_FINAL_SOURCE_GUARD_V1"),
    "frontend_retention_immutable_cleanup": (ROOT / "frontend/scripts/frontend-artifact-retention.py", "_remove_immutable_tree"),
    "frontend_retention_privileged_fallback": (ROOT / "frontend/scripts/boris-frontend-deploy.sh", "FRONTEND_RETENTION_PRIVILEGED_FALLBACK_V1"),
    "frontend_build_diagnostic_no_build": (ROOT / "frontend/scripts/boris-frontend-build.sh", "FRONTEND_BUILD_DIAGNOSTIC_NO_BUILD_V1"),
    "frontend_build_ready_dedupe": (ROOT / "frontend/scripts/boris-frontend-build.sh", "FRONTEND_BUILD_READY_DEDUPE_V1"),
    "frontend_generation_convergence": (ROOT / "backend/app/ext_api/deploy.py", "FRONTEND_GENERATION_CONVERGENCE_V2"),
    "frontend_postcontract_selfheal": (ROOT / "backend/app/ext_api/deploy.py", "FRONTEND_POSTCONTRACT_SELFHEAL_V1"),
    "feed_banner_mobile_two_line_contract": (ROOT / "backend/app/services/avito_premium_compositor.py", "complete_title_prefix_max4_two_lines_min58"),
    "feed_banner_mobile_qa_cache_v4": (ROOT / "backend/app/services/media_semantic_validator.py", "campaign_banner_vision_qa_v8_content_sha_bottom_caption"),
    "campaign_launcher_curated_niche_first": (ROOT / "backend/app/api/campaigns.py", "Saved niche is curated product truth"),
    "campaign_launcher_hygiene_all_sources": (ROOT / "backend/app/api/campaigns.py", "LAUNCHER_HYGIENE_ALL_SOURCES_V1"),
    "frontend_launcher_curated_niche_first": (ROOT / "frontend/app/dashboard/campaign/page.tsx", "CLIENT_LAUNCHER_CURATED_NICHE_FIRST_V1"),
    "frontend_launcher_no_foreign_niche_fallback": (ROOT / "frontend/app/dashboard/campaign/page.tsx", "нужны данные клиента"),
    "feed_media_autocuration": (ROOT / "backend/app/services/jobs.py", "media_autocuration"),
    "feed_vision_budget_backpressure": (ROOT / "backend/app/services/jobs.py", "openai_vision_budget"),
    "feed_vision_advisory_nullpool": (ROOT / "backend/app/services/media_semantic_validator.py", "boris-vision-advisory-lock"),
    "feed_mortgage_subject_guard": (ROOT / "backend/app/services/media_semantic_validator.py", "media_semantic_subject_context_missing"),
    "category_paid_guard": (ROOT / "backend/app/services/category_resolver.py", "category_paid_exactly_once_v1"),
    "category_paid_account_scope": (ROOT / "backend/app/services/category_resolver.py", "CATEGORY_PAID_ACCOUNT_REQUIRED"),
    "category_paid_exact_provider_id": (ROOT / "backend/app/services/category_resolver.py", "x-request-id"),
    "category_paid_ambiguous_id": (ROOT / "backend/app/services/category_resolver.py", "ambiguous-text:category:"),
    "category_paid_advisory_nullpool": (ROOT / "backend/app/services/category_resolver.py", "boris-category-paid-lock"),
    "ai_budget_dedicated_nullpool": (ROOT / "backend/app/services/ai_budget.py", "boris-ai-budget-guard"),
    "usage_ledger_dedicated_nullpool": (ROOT / "backend/app/usage.py", "boris-api-usage-ledger"),
    "usage_ledger_qa_isolation": (ROOT / "backend/app/usage.py", "UNIT/QA ISOLATION V1"),
    "image_artifact_advisory_nullpool": (ROOT / "backend/app/services/image_service.py", "boris-paid-image-artifact-lock"),
    "image_account_advisory_nullpool": (ROOT / "backend/app/services/image_service.py", "boris-paid-image-account-lock"),
    "audio_paid_advisory_nullpool": (ROOT / "backend/app/services/openai_audio_guard.py", "boris-openai-audio-lock"),
    "video_tts_cross_process_lock": (ROOT / "backend/app/services/openai_audio_guard.py", "pg_advisory_lock"),
    "paid_api_boundary_regression": (ROOT / "backend/tests/test_no_legacy_direct_paid_api.py", "NoLegacyDirectPaidApiTests"),
    "prospect_paid_canonical_guard": (ROOT / "backend/app/services/prospect_campaigns.py", "_ff_guarded_openai_response"),
    "prospect_paid_stable_idempotency": (ROOT / "backend/app/services/prospect_campaigns.py", "idempotency_key=key"),
    "voice_semantic_canonical_guard": (ROOT / "backend/app/services/telephony_core.py", "voice-semantic:"),
    "phone_command_retry_bounded": (ROOT / "backend/app/services/telephony_core.py", "provider credentials unavailable; retry limit reached"),
    "phone_outbound_webhook_reconcile_fail_closed": (ROOT / "backend/app/services/telephony_core.py", "no_unique_match"),
    "phone_outbound_intent_destination_bound": (ROOT / "backend/app/services/telephony_core.py", "ix_telephony_outbound_intents_reconcile"),
    "phone_provider_call_identity_exactly_once": (ROOT / "backend/app/services/telephony_core.py", "ux_telephony_calls_provider_identity"),
    "phone_provider_recording_identity_exactly_once": (ROOT / "backend/app/services/telephony_core.py", "ux_telephony_recordings_provider_identity"),
    "phone_provider_recording_call_binding": (ROOT / "backend/app/services/telephony_core.py", "provider_recording_identity_conflict"),
    "phone_provider_event_identity_exactly_once": (ROOT / "backend/app/services/telephony_core.py", "ux_telephony_events_provider_identity"),
    "phone_connected_e2e_readiness_truth": (ROOT / "backend/app/services/telephony_core.py", "real_inbound_e2e_chain"),
    "phone_callback_sla_task_exactly_once": (ROOT / "backend/app/services/telephony_core.py", "callback-sla"),
    "phone_crm_call_sync_exactly_once": (ROOT / "backend/app/services/telephony_core.py", "crm-call-sync"),
    "phone_crm_phone_identity_lock": (ROOT / "backend/app/services/telephony_core.py", "crm-phone|"),
    "calltracking_crm_phone_identity_lock": (ROOT / "backend/app/crm/calltracking_sync.py", "crm-phone|"),
    "monitor_paid_durable_intent_claim": (ROOT / "backend/app/monitoring/llm.py", "monitor_llm_paid:v1:"),
    "monitor_paid_ambiguous_fail_closed": (ROOT / "backend/app/monitoring/llm.py", "MONITOR_LLM_INTENT_ALREADY_CLAIMED"),
    "phone_callback_task_completion_alignment": (ROOT / "backend/app/services/telephony_core.py", "boris_callback:{account_id}"),
    "phone_provider_event_migration": (ROOT / "backend/migrations/028_telephony_provider_event_identity.sql", "ux_telephony_events_provider_identity"),
    "phone_autonomous_qa_fail_truth": (ROOT / "scripts/phone-autonomous-qa-cycle.sh", "exit \"$FAIL\""),
    "phone_autonomous_qa_singleton": (ROOT / "scripts/phone-autonomous-qa-cycle.sh", "PHONE_AUTONOMOUS_QA_SINGLETON=PASS"),
    "phone_native_dynamic_version_android": (ROOT / "clients/boris-phone/android/app/src/main/java/ai/boris/phone/MainActivity.kt", "BORISPhone/$appVersion Android"),
    "phone_native_dynamic_version_ios": (ROOT / "clients/boris-phone/ios/BORISPhone/PhoneRootView.swift", "BORISPhone/\\(appVersion) iOS"),
    "phone_push_active_intent_exactly_once": (ROOT / "backend/migrations/030_telephony_push_active_intent_identity.sql", "ux_telephony_push_active_intent"),
    "phone_push_receipt_device_bound": (ROOT / "backend/app/services/telephony_core.py", "sent_push_token_hash=:ph"),
    "phone_push_receipt_current_token_bound": (ROOT / "backend/app/services/telephony_core.py", "td.push_token_hash=:ph"),
    "phone_push_receipt_native_proof": (ROOT / "frontend/app/phone-app/page.tsx", "receipt_token:nativePushProofRef.current"),
    "phone_push_waiting_configuration_sleep": (ROOT / "backend/app/services/telephony_core.py", "`waiting_configuration` is a sleeping state"),
    "phone_push_preprovider_device_revalidation": (ROOT / "backend/app/services/telephony_core.py", "Revalidate the device immediately before the external provider boundary"),
    "phone_recording_readiness_materialized": (ROOT / "backend/app/services/telephony_core.py", "recording_ingress"),
    "phone_recording_connected_peer_ssrf_guard": (ROOT / "backend/app/services/telephony_core.py", "Verify the actual connected peer"),
    "phone_command_preprovider_revalidation": (ROOT / "backend/app/services/telephony_core.py", "Revalidate call/device immediately before the operator boundary"),
    "phone_answer_provider_success_reconcile": (ROOT / "backend/app/services/telephony_core.py", "Provider success is an irreversible truth boundary"),
    "phone_media_postprovider_revalidation": (ROOT / "backend/app/services/telephony_core.py", "revalidate the same call"),
    "phone_control_request_idempotency": (ROOT / "backend/app/services/telephony_core.py", "ux_telephony_commands_idempotency"),
    "phone_control_ui_idempotency": (ROOT / "frontend/app/phone-app/page.tsx", "idempotency_key:requestId"),
    "phone_provider_online_presence_truth": (ROOT / "backend/app/services/telephony_core.py", "presence<>'offline' AND last_seen_at"),
    "phone_provider_health_recursive_secret_redaction": (ROOT / "backend/app/services/telephony_core.py", "_sanitize_health_payload"),
    "phone_provider_public_config_secret_guard": (ROOT / "backend/app/services/telephony_core.py", "public_config_contains_secret_field"),
    "phone_provider_health_exception_redaction": (ROOT / "backend/app/services/telephony_core.py", "provider health transport failure"),
    "phone_provider_health_synthetic_isolation": (ROOT / "backend/app/services/telephony_core.py", "include_synthetic: bool = False"),
    "phone_stt_entitlement_retry_budget_preserved": (ROOT / "backend/app/services/telephony_core.py", "undo_attempt = 0 if str(r.get('transcript_status') or '') == 'blocked_entitlement' else 1"),
    "phone_recording_webhook_error_redaction": (ROOT / "backend/app/api/telephony.py", "_safe_recording_link_failure"),
    "phone_revoke_pending_command_cancel": (ROOT / "backend/app/services/telephony_core.py", "commands_cancelled"),
    "phone_postprocessing_error_redaction": (ROOT / "backend/app/services/telephony_core.py", "processing_error:"),
    "phone_recording_refresh_error_redaction": (ROOT / "backend/app/services/telephony_core.py", "provider_transport_error:"),
    "phone_sse_event_batch_access_revalidation": (ROOT / "backend/app/api/telephony.py", "event arrival"),
    "phone_autonomous_qa_access_suite": (ROOT / "scripts/phone-autonomous-qa-cycle.sh", "tests.test_telephony_api_access"),
    "phone_webhook_parse_error_redaction": (ROOT / "backend/app/api/telephony.py", "invalid provider webhook payload"),
    "phone_event_payload_secret_redaction": (ROOT / "backend/app/services/telephony_core.py", "_sanitize_event_payload"),
    "marketer_rollback_media_quality_fail_closed": (ROOT / "backend/app/api/avito.py", "rollback_media_quality_blocked"),
    "reactivation_avito_receive_preflight": (ROOT / "backend/app/reactivation_runtime.py", "REACTIVATION_AVITO_RECEIVE_PREFLIGHT_V1"),
    "reactivation_transport_block_state": (ROOT / "backend/app/reactivation_core.py", '"blocked":          ("ready", "cancelled")'),
    "reactivation_transport_selfheal": (ROOT / "backend/app/reactivation_runtime.py", "def reconcile_transport_unavailable"),
    "reactivation_transport_recovery_stale_guard": (ROOT / "backend/app/reactivation_runtime.py", "fresh_history_and_dialog_unchanged"),
    "reactivation_partial_402_external_truth": (ROOT / "backend/app/services/control_plane_adapters.py", "REACTIVATION_PARTIAL_402_EXTERNAL_TRUTH_V1"),
    "reactivation_avito_only_transport_regression": (ROOT / "backend/tests/test_reactivation_avito_transport.py", "test_reactivation_transport_graph_has_no_sms_email_send_calls"),
    "feed_terminal_partial_queue_truth": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PARTIAL_REPAIR_STATE_V1"),
    "publication_rejected_item_truth": (ROOT / "backend/app/services/campaign_identity.py", "PUBLICATION_REJECTED_ITEM_TRUTH_V1"),
    "publication_full_failure_item_truth": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_FULL_FAILURE_ITEM_TRUTH_V1"),
    "publication_replacement_duplicate_proof": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_REPLACEMENT_DUPLICATE_TRANSITION_PROOF_V2"),
    "feed_failed_durable_rollback_proof": (ROOT / "backend/app/services/control_plane_adapters.py", "FEED_FAILED_DURABLE_ROLLBACK_PROOF_V1"),
    "autoload_rejected_not_published": (ROOT / "backend/app/services/campaign_identity.py", "AUTOLOAD_REJECTED_NOT_PUBLISHED_V1"),
    "autoload_rejection_sticky_until_new_submission": (ROOT / "backend/app/services/campaign_identity.py", "AUTOLOAD_REJECTION_STICKY_UNTIL_NEW_SUBMISSION_V1"),
    "targeted_description_repair_prep": (ROOT / "backend/app/services/jobs.py", "TARGETED_DESCRIPTION_REPAIR_PREP_V1"),
    "ff_xml_multivalue_option": (ROOT / "backend/app/api/campaigns.py", "FF_XML_MULTIVALUE_OPTION_V1"),
    "client_supervisor_overdue_actions": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_OVERDUE_ACTIONS_V1"),
    "client_supervisor_overdue_human_work": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_OVERDUE_HUMAN_WORK_V3"),
    "crm_task_outcome_evidence_reconcile": (ROOT / "backend/app/services/brain_recovery.py", "CRM_TASK_OUTCOME_EVIDENCE_RECONCILE_V2"),
    "crm_task_existing_analysis_enrich": (ROOT / "backend/app/services/brain_recovery.py", "CRM_TASK_EXISTING_ANALYSIS_ENRICH_V1"),
    "crm_task_autoanswer_reschedule": (ROOT / "backend/app/services/brain_recovery.py", "CRM_TASK_AUTOANSWER_RESCHEDULE_V1"),
    "crm_reactivation_internal_handoff": (ROOT / "backend/app/services/brain_recovery.py", "CRM_REACTIVATION_INTERNAL_HANDOFF_V1"),
    "crm_next_action_live_inquiry_evidence": (ROOT / "backend/app/services/brain_recovery.py", "CRM_NEXT_ACTION_LIVE_INQUIRY_EVIDENCE_V1"),
    "crm_next_action_unanswered_only": (ROOT / "backend/app/services/brain_recovery.py", "CRM_NEXT_ACTION_UNANSWERED_ONLY_V1"),
    "crm_next_action_diagnosis_live_unanswered": (ROOT / "backend/app/services/control_plane_adapters.py", "CRM_NEXT_ACTION_DIAGNOSIS_LIVE_UNANSWERED_V1"),
    "crm_already_answered_task_cleanup": (ROOT / "backend/app/services/brain_recovery.py", "CRM_ALREADY_ANSWERED_AUTO_TASK_CLEANUP_V1"),
    "crm_stale_import_task_cleanup": (ROOT / "backend/app/services/brain_recovery.py", "CRM_STALE_IMPORTED_INQUIRY_TASK_CLEANUP_V1"),
    "failed_campaign_autonomous_retry_failclosed": (ROOT / "backend/app/services/jobs.py", "FAILED_CAMPAIGN_AUTONOMOUS_RETRY_FAILCLOSED_V1"),
    "crm_task_existing_analysis_autoanswer_enrich": (ROOT / "backend/app/services/brain_recovery.py", "CRM_TASK_EXISTING_ANALYSIS_ENRICH_V2"),
    "crm_promised_worktime_next_action": (ROOT / "backend/app/services/brain_recovery.py", "CRM_PROMISED_WORKTIME_NEXT_ACTION_V1"),
    "crm_safe_recovery_profile": (ROOT / "backend/app/services/control_plane_adapters.py", "CRM_SAFE_RECOVERY_PROFILE_V1"),
    "frontend_client_human_work": (ROOT / "frontend/app/dashboard/command-center/page.tsx", "client-human-work-v1"),
    "crm_missed_call_task_contact_coalesce": (ROOT / "backend/app/crm/calltracking_sync.py", "MISSED_CALL_TASK_CONTACT_COALESCE_V1"),
    "feed_terminal_partial_counter_evidence": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PARTIAL_COUNTER_EVIDENCE_V1"),
    "feed_pending_review_child_truth": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PENDING_REVIEW_CHILD_TRUTH_V1"),
    "feed_targeted_partial_repair_plan": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_TARGETED_REPAIR_PLAN_V1"),
    "feed_brief_negative_fact_guard": (ROOT / "backend/app/api/campaigns.py", "FEED_BRIEF_NEGATIVE_FACT_GUARD_V1"),
    "feed_brief_negative_fact_autosanitize": (ROOT / "backend/app/api/campaigns.py", "FEED_BRIEF_NEGATIVE_FACT_AUTOSANITIZE_V1"),
    "autoload_error_detail_evidence": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_ERROR_DETAIL_EVIDENCE_V1"),
    "autoload_delivery_truth_v2": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_DELIVERY_TRUTH_V2"),
    "autoload_unresolved_detail": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_UNRESOLVED_DETAIL_V1"),
    "autoload_duplicate_2010_terminal": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_DUPLICATE_2010_TERMINAL_V1"),
    "publication_explicit_replacement_finalize": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_EXPLICIT_REPLACEMENT_FINALIZE_V1"),
    "autoload_manual_hourly_limit_scheduled_fallback": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_MANUAL_HOURLY_LIMIT_SCHEDULED_FALLBACK_V1"),
    "cpx_measure_inactive_terminal": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_MEASURE_INACTIVE_TERMINAL_V1"),
    "feed_newer_upload_supersedes_pending": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PENDING_REVIEW_NEWER_UPLOAD_TRUTH_V1"),
    "feed_approved_omission_selfheal": (ROOT / "backend/app/services/jobs.py", "APPROVED_FEED_OMISSION_SELFHEAL_V1"),
    "feed_low_cadence_processing_reentry": (ROOT / "backend/app/services/jobs.py", "LOW_CADENCE_PROCESSING_REENTRY_V1"),
    "marketer_interrupt_recovery_truth": (ROOT / "backend/ai_marketer_hourly.sh", "AI_MARKETER_INTERRUPT_TRUTH_V1"),
    "stats_tenant_deferred_selfheal": (ROOT / "backend/daily_stats_collector.py", "STATS_TENANT_DEFERRED_SELFHEAL_V1"),
    "canonical_replacement_authorization_gap": (ROOT / "backend/kpi_goal_runner.py", "CANONICAL_REPLACEMENT_AUTHORIZATION_GAP_V1"),
    "marketer_non_money_fleet_scope": (ROOT / "backend/kpi_goal_runner.py", "NON_MONEY_FLEET_SCOPE_V1"),
    "marketer_non_money_active_client_only": (ROOT / "backend/kpi_goal_runner.py", "NON_MONEY_OBSERVATION_ACTIVE_CLIENT_ONLY_V1"),
    "kpi_provider_stats_day_truth": (ROOT / "backend/app/api/avito.py", "KPI_PROVIDER_STATS_DAY_TRUTH_V1"),
    "kpi_provider_stats_new_work_fence": (ROOT / "backend/app/api/avito.py", "KPI_PROVIDER_STATS_NEW_WORK_FENCE_V1"),
    "mop_paid_durable_cache": (ROOT / "backend/app/api/messenger.py", "mop_paid_response:"),
    "mop_paid_cross_process_lock": (ROOT / "backend/app/api/messenger.py", "pg_advisory_lock"),
    "mop_paid_exact_provider_id": (ROOT / "backend/app/services/sales_ai_router.py", '"x-request-id"'),
    "mop_paid_ambiguous_id": (ROOT / "backend/app/services/sales_ai_router.py", "ambiguous-openai:sales:"),
    "mop_paid_advisory_nullpool": (ROOT / "backend/app/api/messenger.py", "boris-mop-paid-lock"),
    "mop_paid_budget_guard": (ROOT / "backend/app/services/sales_ai_router.py", "ai_budget.reserve("),
    "mop_sales_ai_router_policy": (ROOT / "backend/app/api/messenger.py", "MOP_SALES_AI_ROUTER_V1"),
    "mop_training_sales_ai_router": (ROOT / "backend/app/api/mop_training.py", "MOP_TRAINING_SALES_AI_ROUTER_V1"),
    "rop_chat_sales_ai_router": (ROOT / "backend/app/api/calltracking.py", "rop_chat_analysis"),
    "sales_gemini_daily_reset_probe_latch": (ROOT / "backend/app/services/sales_ai_router.py", "GEMINI_DAILY_RESET_PROBE_LATCH_V1"),
    "sales_router_no_gigachat_fallback": (ROOT / "backend/app/services/sales_ai_router.py", "SALES_ROUTER_NO_GIGACHAT_FALLBACK_V1"),
    "sales_router_exact_provider_policy": (ROOT / "backend/app/services/sales_ai_router.py", "openai_if_usable_then_deepseek_then_free_gemini_then_local"),
    "sales_local_emergency_model": (ROOT / "backend/app/services/sales_ai_router.py", "SALES_LOCAL_EMERGENCY_MODEL_V1"),
    "sales_local_pressure_recovery_floor": (ROOT / "backend/app/services/sales_ai_router.py", "LOCAL_AI_HISTORICAL_LOAD_RECOVERY_FLOOR_V1"),
    "gemini_daily_reset_proactive_probe": (ROOT / "backend/.boris_ops/chat_coordination/development_provider_watch.py", "GEMINI_DAILY_RESET_PROACTIVE_PROBE_V1"),
    "enhance_long_copy": (ROOT / "backend/app/services/jobs.py", "length=\"long\""),
    "feed_exact_quantity": (ROOT / "backend/app/api/campaigns.py", "quantity"),
    "feed_publication_campaign_scope": (ROOT / "backend/app/services/jobs.py", "expected_ad_ids=expected_ad_ids"),
    "feed_publication_item_pagination": (ROOT / "backend/app/api/avito.py", 'params={"perPage":100,"page":_page}'),
    "feed_scheduled_url_watcher": (ROOT / "backend/app/services/jobs.py", "awaiting_scheduled_url_result"),
    "feed_scheduled_url_time_backoff": (ROOT / "backend/app/services/jobs.py", "SCHEDULED_URL_TIME_BASED_BACKOFF_V1"),
    "feed_direct_upload_time_boundary": (ROOT / "backend/app/services/jobs.py", "DIRECT_UPLOAD_TIME_BOUNDARY_V1"),
    "feed_direct_upload_submission_time_boundary": (ROOT / "backend/app/services/jobs.py", "DIRECT_UPLOAD_SUBMISSION_TIME_BOUNDARY_V1"),
    "publication_terminal_schedule_clean": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_TERMINAL_SCHEDULE_CLEAN_V1"),
    "publication_pending_review_no_resurrect": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PENDING_REVIEW_NO_RESURRECT_V1"),
    "low_cadence_root_parent_guard": (ROOT / "backend/app/services/jobs.py", "LOW_CADENCE_ROOT_PARENT_GUARD_V2"),
    "canonical_replacement_prepare": (ROOT / "backend/app/services/initial_portfolio.py", "CANONICAL_REPLACEMENT_PREPARE_V1"),
    "canonical_replacement_autoprepare": (ROOT / "backend/kpi_goal_runner.py", "CANONICAL_REPLACEMENT_AUTOPREPARE_V1"),
    "publication_pending_review_terminal": (ROOT / "backend/app/services/jobs.py", "PUBLICATION_PENDING_REVIEW_TERMINAL_V1"),
    "capacity_queue_age_self_reference_guard": (ROOT / "backend/app/services/reliability.py", "CAPACITY_QUEUE_AGE_SELF_REFERENCE_GUARD_V1"),
    "brain_acceptance_readonly_tx_release": (ROOT / "backend/scripts/brain_acceptance_guard.py", "BRAIN_ACCEPTANCE_READONLY_TX_RELEASE_V1"),
    "brain_acceptance_dynamic_module_count": (ROOT / "backend/app/services/brain_selftest.py", "BRAIN_SELFTEST_DYNAMIC_MODULE_COUNT_V1"),
    "brain_e2e_dynamic_module_count": (ROOT / "backend/app/services/brain_e2e_acceptance.py", "BRAIN_E2E_DYNAMIC_MODULE_COUNT_V1"),
    "brain_world_registry_reconcile": (ROOT / "backend/app/services/brain_world_state.py", "BRAIN_WORLD_REGISTRY_RECONCILE_V1"),
    "owner_wait_visibility": (ROOT / "backend/app/services/jobs.py", "OWNER_WAIT_VISIBILITY_V1"),
    "client_supervisor_brain_composite": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_BRAIN_COMPOSITE_V1"),
    "client_supervisor_working_degraded": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_WORKING_DEGRADED_V1"),
    "client_supervisor_operational_signals": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_OPERATIONAL_SIGNALS_V1"),
    "client_supervisor_expected_work_scope": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_EXPECTED_WORK_SCOPE_V1"),
    "client_supervisor_social_paid_scope": (ROOT / "backend/app/services/client_supervisor.py", "CLIENT_SUPERVISOR_SOCIAL_PAID_SCOPE_V1"),
    "social_paid_period_canonical": (ROOT / "backend/app/services/social_entitlement.py", "SOCIAL_PAID_PERIOD_CANONICAL_V1"),
    "social_paid_project_gap_owner_action": (ROOT / "backend/app/services/brain_world_state.py", "SOCIAL_PAID_PROJECT_GAP_OWNER_ACTION_V1"),
    "client_supervisor_fleet_wire": (ROOT / "backend/control_plane_guardian_runner.py", "CLIENT_SUPERVISOR_FLEET_WIRE_V1"),
    "frontend_client_supervisor_operational_facts": (ROOT / "frontend/app/dashboard/command-center/page.tsx", "client-operational-facts"),
    "client_plan_exact_systemd_schedule": (ROOT / "backend/app/services/brain_action_planner.py", "_systemd_timer_next_run"),
    "client_plan_owner_money_concrete_text": (ROOT / "backend/app/services/brain_action_planner.py", "Подтвердите дневной рекламный лимит для аккаунта"),
    "client_plan_owner_monitor_exact_time": (ROOT / "backend/app/services/brain_action_planner.py", '"monitor_planned_for"'),
    "client_plan_account_scoped_reactivation_proof": (ROOT / "backend/app/services/brain_action_planner.py", "_reactivation_execution_schedule"),
    "client_plan_mop_external_recheck_schedule": (ROOT / "backend/app/services/brain_action_planner.py", "_mop_execution_schedule"),
    "client_plan_routine_real_schedule": (ROOT / "backend/app/services/brain_action_planner.py", "_build_routine_plan"),
    "client_plan_timer_running_now_truth": (ROOT / "backend/app/services/brain_action_planner.py", "SYSTEMD_TIMER_RUNNING_NOW_TRUTH_V1"),
    "client_plan_timer_postrun_retry": (ROOT / "backend/app/services/brain_action_planner.py", "SYSTEMD_TIMER_POSTRUN_RETRY_V1"),
    "frontend_client_plan_running_now_truth": (ROOT / "frontend/app/dashboard/clients-control/page.tsx", "выполняется сейчас"),
    "client_plan_routine_expected_scope": (ROOT / "backend/app/api/control_plane.py", 'plan["routine_plan"]'),
    "prospecting_control_adapter": (ROOT / "backend/app/services/control_plane_adapters.py", "LEAD_RADAR_CONTROL_ADAPTER_V2"),
    "prospecting_mandatory_brain_module": (ROOT / "backend/app/services/brain_modules.py", '"prospecting"'),
    "prospecting_exact_timer_schedule": (ROOT / "backend/app/services/brain_action_planner.py", '"prospecting":("boris-lead-radar.timer",1)'),
    "prospecting_timer_selfheal": (ROOT / "backend/app/services/brain_recovery.py", "PROSPECTING_TIMER_SELFHEAL_V1"),
    "prospecting_structured_dependencies": (ROOT / "backend/app/services/lead_radar/brain_seed.py", "runtime_preflight"),
    "prospecting_open_web_source_groups": (ROOT / "backend/app/services/lead_radar/open_web.py", "OPEN_WEB_SOURCE_GROUPS_V1"),
    "prospecting_open_web_registry": (ROOT / "backend/app/services/lead_radar/registry.py", "OPEN_WEB_MISSING_GROUPS_REGISTRY_V1"),
    "prospecting_preflight_all_source_families": (ROOT / "backend/lead_radar_config_preflight.py", "LEAD_RADAR_PREFLIGHT_ALL_SOURCE_FAMILIES_V1"),
    "prospecting_provider_rate_guard": (ROOT / "backend/app/services/lead_radar/source_policy.py", "OPEN_WEB_PROVIDER_RATE_GUARD_V1"),
    "crowd_seo_offer_type_goods_services": (ROOT / "backend/app/services/crowd_seo.py", "CROWD_SEO_OFFER_TYPE_V1"),
    "crowd_seo_offer_aware_forum_discovery": (ROOT / "backend/app/services/forum_discovery.py", "FORUM_DISCOVERY_OFFER_AWARE_INTENTS_V1"),
    "crowd_seo_discovery_real_progress": (ROOT / "backend/app/services/forum_discovery.py", "FORUM_DISCOVERY_REAL_PROGRESS_V1"),
    "crowd_seo_discovery_real_progress_backoff": (ROOT / "backend/tools/forum_acquisition_guard.py", "DISCOVERY_REAL_PROGRESS_BACKOFF_V1"),
    "crowd_seo_guard_singleton_lock": (ROOT / "backend/tools/forum_acquisition_guard.py", "FORUM_ACQUISITION_SINGLETON_LOCK_V1"),
    "crowd_seo_multi_client_copy": (ROOT / "backend/app/services/crowd_seo.py", "CROWD_SEO_MULTI_CLIENT_SURFACE_COPY_V1"),
    "crowd_seo_reusable_warmup": (ROOT / "backend/app/services/service_marketplace.py", "CROWD_SEO_REUSABLE_WARMUP_15D_V1"),
    "crowd_seo_captcha_failclosed": (ROOT / "backend/tools/marketplace_browser_assistant.py", "CAPTCHA_HUMAN_CHECKPOINT_FAIL_CLOSED_V1"),
    "frontend_crowd_goods_services": (ROOT / "frontend/app/dashboard/prospecting/acquisition/page.tsx", "товары и услуги · вход только сайт"),
    "frontend_crowd_warmup_queue": (ROOT / "frontend/app/dashboard/prospecting/acquisition/page.tsx", "Прогрев аккаунтов:"),
    "frontend_crowd_bootstrap_verify_now": (ROOT / "frontend/app/dashboard/prospecting/acquisition/page.tsx", "CROWD_BOOTSTRAP_VERIFY_NOW_V1"),
    "module_adapter_missing_autoreconcile": (ROOT / "backend/app/services/incident_reconciler.py", "MODULE_ADAPTER_MISSING"),
    "frontend_client_plan_routine_schedule": (ROOT / "frontend/app/dashboard/clients-control/page.tsx", "постоянная работа по оплаченным модулям"),
    "claude_mcp_config_unique_temp": (ROOT / "backend/app/ext_api/lead.py", "CLAUDE_MCP_CONFIG_UNIQUE_TEMP_V1"),
    "mop_provider_wait_autoretry": (ROOT / "backend/app/mop_core.py", "MOP_PROVIDER_WAIT_AUTORETRY_V1"),
    "mop_human_first_turn_sentence_guard": (ROOT / "backend/app/api/messenger.py", "HUMAN_FIRST_TURN_SENTENCE_GUARD_V1"),
    "mop_local_provider_retry_backoff": (ROOT / "backend/app/mop_core.py", "MOP_LOCAL_PROVIDER_RETRY_BACKOFF_V1"),
    "mop_provider_deferred_wait_v2": (ROOT / "backend/app/mop_core.py", "MOP_PROVIDER_DEFERRED_WAIT_V2"),
    "mop_technical_handoff_selfheal": (ROOT / "backend/app/mop_core.py", "MOP_TECHNICAL_HANDOFF_SELFHEAL_V1"),
    "mop_nonterminal_card_spam_guard": (ROOT / "backend/app/api/messenger.py", "MOP_NONTERMINAL_CARD_SPAM_GUARD_V2"),
    "mop_send_guard_history_fix": (ROOT / "backend/app/api/messenger.py", "_guard_sql_text"),
    "mop_local_inference_lock": (ROOT / "backend/app/services/sales_ai_router.py", "SALES_AI_LOCAL_INFERENCE_LOCK_V1"),
    "mop_stale_nonterminal_reconcile": (ROOT / "backend/app/services/brain_recovery.py", "MOP_STALE_NONTERMINAL_RECONCILE_V1"),
    "mop_system_event_draft_guard": (ROOT / "backend/app/api/messenger.py", "MOP_SYSTEM_EVENT_DRAFT_GUARD_V1"),
    "mop_entitlement_before_draft": (ROOT / "backend/app/api/messenger.py", "MOP_ENTITLEMENT_BEFORE_DRAFT_V1"),
    "mop_system_event_selfheal": (ROOT / "backend/app/services/brain_recovery.py", "MOP_SYSTEM_EVENT_SELFHEAL_V1"),
    "mop_handoff_ack_not_manager_completion": (ROOT / "backend/app/services/brain_recovery.py", "MOP_HANDOFF_ACK_IS_NOT_MANAGER_COMPLETION_V1"),
    "owner_outreach_current_row_excluded": (ROOT / "backend/app/services/owner_outreach_volume_safety.py", "OWNER_OUTREACH_CURRENT_ROW_EXCLUDED_V1"),
    "owner_outreach_service_day_by_execution": (ROOT / "backend/app/services/owner_outreach_volume_safety.py", "OWNER_OUTREACH_SERVICE_DAY_BY_EXECUTION_V1"),
    "owner_email_shortfall_not_owner_action": (ROOT / "backend/app/services/prospect_campaigns.py", "OWNER_NOT_OPERATOR_EMAIL_SHORTFALL_V1"),
    "frontend_client_plan_auto_recheck_time": (ROOT / "frontend/app/dashboard/clients-control/page.tsx", "Следующая автоматическая проверка"),
    "frontend_client_owner_recheck_exact_time": (ROOT / "frontend/app/dashboard/clients-control/page.tsx", "после подтверждения BORIS перепроверит решение на ближайшем цикле"),
    "frontend_manager_due_time": (ROOT / "frontend/app/dashboard/clients-control/page.tsx", "Срок менеджера:"),
    "money_guard_paid_period_scope": (ROOT / "backend/control_plane_guardian_runner.py", "MONEY_GUARD_PAID_PERIOD_SCOPE_V1"),
    "frontend_client_supervisor_control_center": (ROOT / "frontend/app/dashboard/command-center/page.tsx", "CLIENT_SUPERVISOR_CONTROL_CENTER_UX_V1"),
    "frontend_owner_avito_next_check": (ROOT / "frontend/app/dashboard/campaign/[id]/page.tsx", "OWNER_AVITO_NEXT_CHECK_UX_V1"),
    "feed_superseded_scope": (ROOT / "backend/app/api/campaigns.py", 'CampaignItem.status != "superseded"'),
    "feed_published_scope_lock": (ROOT / "backend/app/api/campaigns.py", "published_campaign_scope_locked"),
    "feed_deleted_media_lifecycle_guard": (ROOT / "backend/app/api/avito.py", "Deleted historical MediaAsset rows must not poison"),
    "feed_publication_follow_latest": (ROOT / "backend/app/api/avito.py", "followed_latest_from"),
    "feed_transient_edit_limit_truth": (ROOT / "backend/app/api/avito.py", "edit-rate limit"),
    "feed_publication_processing_truth": (ROOT / "backend/app/services/jobs.py", '"unresolved":len'),
    "feed_publication_status_persist": (ROOT / "backend/app/services/campaign_identity.py", 'status = "published"'),
    "avito_stats_single_retry_layer": (ROOT / "backend/app/api/avito.py", "Do not wrap it in a second retry loop"),
    "stats_retry_after_bounded": (ROOT / "backend/app/api/avito.py", "STATS_RETRY_AFTER_BOUNDED_V2"),
    "stats_account_throttle_stop": (ROOT / "backend/app/api/avito.py", "AVITO_ACCOUNT_THROTTLE_STOP_V2"),
    "stats_last_good_spend_grace": (ROOT / "backend/app/api/avito.py", "STATS_LAST_GOOD_SPEND_GRACE_V1"),
    "marketer_mutation_spend_fresh_15m": (ROOT / "backend/app/api/cpx_advisor.py", "MARKETER_MUTATION_SPEND_FRESH_15M_V1"),
    "guardian_disabled_account_scope": (ROOT / "backend/boris_guardian.py", "reliability_kill_switches"),
    "storage_pressure_artifact_gc": (ROOT / "frontend/scripts/boris-storage-guardian.sh", "min_age=120"),
    "storage_pressure_dynamic_rollback_depth": (ROOT / "frontend/scripts/boris-storage-guardian.sh", "[ \"$used\" -ge 88 ] && keep_count=20"),
    "frontend_retention_root_timer": (ROOT / "frontend/scripts/boris-frontend-deploy.sh", "FRONTEND_RETENTION_ROOT_TIMER_V1"),
    "ops_guardian_reload_state_safe": (ROOT / "boris_ops/guardian.py", "OPS_GUARDIAN_RELOADING_HEALTHY_V1"),
    "browser_gateway_zip_sync": (ROOT / "frontend/scripts/build-browser-gateway.sh", "GATEWAY_ZIP_SYNC_V1"),
    "frontend_hash_excludes_generated_gateway_zip": (ROOT / "frontend/scripts/frontend-source-hash.py", "FRONTEND_SOURCE_HASH_EXCLUDES_GENERATED_GATEWAY_ZIP_V1"),
    "frontend_hash_excludes_nonruntime_qa": (ROOT / "frontend/scripts/frontend-source-hash.py", "FRONTEND_SOURCE_HASH_EXCLUDES_NONRUNTIME_QA_V1"),
"frontend_hash_runtime_only": (ROOT / "frontend/scripts/frontend-source-hash.py", "FRONTEND_SOURCE_HASH_RUNTIME_ONLY_V1"),
    "browser_gateway_public_atomic_sync": (ROOT / "frontend/scripts/deploy-browser-gateway.sh", "GATEWAY_PUBLIC_ATOMIC_SYNC_V1"),
    "browser_gateway_public_path_unit": (pathlib.Path("/etc/systemd/system/boris-browser-gateway-sync.path"), "PathChanged=/root/BORIS/frontend/public/boris-browser-gateway.zip"),
    "crm_inbound": (ROOT / "backend/app/crm/bridge.py", "CRM capture invariant"),
    "marketer_90_10": (ROOT / "backend/cpx_advisor_runner.py", "HIGH_BID_SHARE = 0.10"),
    "marketer_proof_gate": (ROOT / "backend/cpx_advisor_runner.py", "MIN_PROVEN_VIEWS_7D = 20"),
    "marketer_spend_fail_closed": (ROOT / "backend/app/services/intraday.py", "advisor_spend_unknown"),
    "marketer_budget_90_guard": (ROOT / "backend/app/services/intraday.py", "BUDGET_NEAR_LIMIT"),
    "marketer_bid_cap_reconcile": (ROOT / "backend/cpx_cap_reconciler.py", "remaining_over_cap"),
    "marketer_bid_cap_snapshot_fallback": (ROOT / "backend/cpx_cap_reconciler.py", "stored_snapshot_fallback"),
    "marketer_bid_cap_coverage_gate": (ROOT / "backend/cpx_cap_reconciler.py", "inventory_complete"),
    "marketer_global_bid_cap": (ROOT / "backend/app/services/marketing_money_policy.py", "GLOBAL_HARD_MAX_BID_RUB = 150.0"),
    "marketer_global_bid_step": (ROOT / "backend/app/services/marketing_money_policy.py", "GLOBAL_MAX_BID_DELTA_PCT = 10"),
    "marketer_global_auto_action_run_cap": (ROOT / "backend/app/services/marketing_money_policy.py", "GLOBAL_MAX_AUTO_ACTIONS_RUN = 5"),
    "marketer_global_auto_action_day_cap": (ROOT / "backend/app/services/marketing_money_policy.py", "GLOBAL_MAX_AUTO_ACTIONS_DAY = 120"),
    "marketer_run_cap_account_scoped": (ROOT / "backend/app/services/autonomy.py", "count_actions_run(db, account_id, rid)"),
    "marketer_daily_budget_policy": (ROOT / "backend/app/services/marketing_money_policy.py", "effective_daily_budget_limit"),
    "marketer_spend_signal_policy": (ROOT / "backend/app/services/marketing_money_policy.py", "latest_confirmed_spend"),
    "marketer_spend_independent_inventory": (ROOT / "backend/app/api/avito.py", "MONEY_SIGNAL_INDEPENDENT_OF_INVENTORY_V1"),
    "marketer_spend_separate_storage": (ROOT / "backend/app/api/avito.py", "daily_spending:"),
    "marketer_budget_brake": (ROOT / "backend/cpx_budget_brake.py", "paid_promotions_after"),
    "marketer_budget_brake_early_cutoff": (ROOT / "backend/cpx_budget_brake.py", "CPX_BUDGET_BRAKE_ADAPTIVE_RESERVE_V1"),
    # CPX_MONEY_IDENTITY_CONTRACT_V2: content/feed mapping is not the money-write
    # authority. Money planning may use an exact account-scoped provider item
    # identity, but every actual write is re-proven against getBids/{itemID}.
    "marketer_cpx_money_identity_v2": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_MONEY_IDENTITY_V2"),
    "marketer_cpx_provider_probe_identity": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_PROVIDER_PROBE_MONEY_WRITABILITY_V1"),
    "marketer_cpx_final_provider_item_proof": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_FINAL_PROVIDER_ITEM_PROOF_V1"),
    "marketer_cpx_advisor_service_entitlement_gate": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_SERVICE_ENTITLEMENT_GATE_V1"),
    "marketer_cpx_advisor_early_safe_snapshot": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_EARLY_SAFE_SNAPSHOT_V1"),
    "marketer_cpx_money_writability_summary": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_MONEY_WRITABILITY_SUMMARY_V1"),
"marketer_cpx_owner_action_minimal": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_OWNER_ACTION_MINIMAL_V1"),
    "marketer_cpx_budget_brake_owner_action": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ADVISOR_BUDGET_BRAKE_OWNER_ACTION_V1"),
    "marketer_adaptive_budget_policy": (ROOT / "backend/app/services/marketing_money_policy.py", "adaptive_budget_brake_policy"),
    "marketer_adaptive_cutoff_observability": (ROOT / "backend/cpx_budget_brake.py", "CPX_BUDGET_BRAKE_CUTOFF_OBSERVABILITY_V1"),
    "marketer_adaptive_budget_final_raise_guard": (ROOT / "backend/app/api/cpx_advisor.py", "MARKETER_ADAPTIVE_BUDGET_RESERVE_V1"),
    "marketer_budget_brake_transport_deferred": (ROOT / "backend/cpx_budget_brake.py", "CPX_BUDGET_BRAKE_TRANSPORT_DEFERRED_V1"),
    "marketer_budget_brake_db_session_isolation": (ROOT / "backend/cpx_budget_brake.py", "connection_invalidated"),
    "marketer_budget_brake_before_kpi": (ROOT / "backend/ai_marketer_hourly.sh", "cpx_budget_brake.py --apply"),
    "marketer_kpi_survives_collector_failure": (ROOT / "backend/ai_marketer_hourly.sh", "KPI runner owns per-account freshness checks"),
    "marketer_advisor_canonical_spend": (ROOT / "backend/app/api/cpx_advisor.py", "latest_confirmed_spend"),
    "marketer_advisor_step_10": (ROOT / "backend/app/api/cpx_advisor.py", "BID_STEP_PCT = 10"),
    "marketer_feed_ramp_step_10": (ROOT / "backend/app/api/cpx_advisor.py", "NEW_FEED_HOURLY_RAISE_PCT = 10"),
    "marketer_mandate_dynamic_step": (ROOT / "backend/app/api/cpx_advisor.py", "account_max_step_pct"),
    "marketer_advisor_action_history": (ROOT / "backend/app/api/cpx_advisor.py", "boris_action_text"),
    "marketer_historical_action_current_truth": (ROOT / "backend/app/api/cpx_advisor.py", "HISTORICAL_CPX_ACTION_TRUTH_V2"),
    "frontend_bid_floor_ui": (ROOT / "frontend/app/dashboard/page.tsx", "Минимум Avito"),
    "frontend_bid_action_ui": (ROOT / "frontend/app/dashboard/page.tsx", "BORIS сделал:"),
    "frontend_bid_next_check_ui": (ROOT / "frontend/app/dashboard/page.tsx", "Следующая проверка:"),
    "marketer_advisor_spend_signal_ui": (ROOT / "backend/app/api/cpx_advisor.py", '"spend_signal": {'),
    "marketer_advisor_position_status": (ROOT / "backend/app/api/cpx_advisor.py", '"position_monitor": _position_monitor'),
    "frontend_advisor_spend_status": (ROOT / "frontend/app/dashboard/page.tsx", "Расходы Avito"),
    "frontend_advisor_position_status": (ROOT / "frontend/app/dashboard/page.tsx", "Позиции в Avito"),
    "frontend_advisor_account_race_guard": (ROOT / "frontend/app/dashboard/page.tsx", "advisorLoadSeq.current"),
    "frontend_advisor_zero_budget_hydration": (ROOT / "frontend/app/dashboard/page.tsx", "daily_budget_limit_rub != null"),
    "marketer_advisor_owner_action": (ROOT / "backend/app/api/cpx_advisor.py", "owner_action"),
    "frontend_advisor_owner_action": (ROOT / "frontend/app/dashboard/page.tsx", "Нужно ли ваше действие:"),
    "frontend_advisor_no_default_cpl": (ROOT / "frontend/app/dashboard/page.tsx", "max_cost_per_lead_rub: advisorData?.target?.max_cpl_rub ??"),
    "frontend_advisor_no_default_target": (ROOT / "frontend/app/dashboard/page.tsx", "target_leads_per_day: advisorData?.target?.target_leads_per_day ?? undefined"),
    "kpi_partial_update_preserves_owner_target": (ROOT / "backend/app/api/avito.py", "KPI_PARTIAL_UPDATE_PRESERVES_OWNER_TARGET_V1"),
    "frontend_real_feed_schedule": (ROOT / "frontend/app/dashboard/page.tsx", "AVITO_REAL_DATEBEGIN_SCHEDULE_V1"),
    "frontend_zero_budget_raise_state": (ROOT / "frontend/app/dashboard/page.tsx", "Повышение ставок остановлено: суточный бюджет не задан"),
    "frontend_marketing_daily_budget_input": (ROOT / "frontend/app/dashboard/marketing/page.tsx", "Суточный рекламный бюджет, ₽"),
    "frontend_marketing_hard_bid_cap_input": (ROOT / "frontend/app/dashboard/marketing/page.tsx", "Максимальная ставка за просмотр, ₽"),
    "frontend_marketing_zero_budget_truth": (ROOT / "frontend/app/dashboard/marketing/page.tsx", "денежные действия остановлены: бюджет не задан"),
    "marketer_budget_guardian": (ROOT / "backend/boris_guardian.py", "budget_brake_health"),
    "marketer_timer_self_heal": (ROOT / "backend/boris_guardian.py", "KPI_TIMER_SELF_HEAL_V1_TEST_CONTRACT"),
    "marketer_balance_funding_owner_escalation": (ROOT / "backend/boris_guardian.py", "BALANCE_FUNDING_OWNER_ESCALATION_V1"),
    "kpi_budget_exhausted_money_false": (ROOT / "backend/app/api/avito.py", "KPI_BUDGET_EXHAUSTED_MONEY_FALSE_V1"),
    "marketer_stats_money_scope": (ROOT / "backend/daily_stats_collector.py", "Money-safety coverage is wider"),
    "marketer_all_connected_stats_scope": (ROOT / "backend/daily_stats_collector.py", "ALL_CONNECTED_STATS_SCOPE_V1"),
    "marketer_late_day_spend_catchup": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_SPEND_CATCHUP_V1"),
    "marketer_late_day_spend_time_pacing": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_SPEND_TIME_PACING_V1"),
    "marketer_late_day_spend_effect_learning": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_SPEND_EFFECT_LEARNING_V1"),
    "marketer_late_day_negative_feedback_fence": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_NEGATIVE_FEEDBACK_FENCE_V1"),
    "marketer_late_day_spend_fresh_money_proof": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_SPEND_FRESH_MONEY_PROOF_V1"),
    "marketer_late_day_measurement_binding": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_MEASUREMENT_BINDING_V3"),
    "marketer_late_day_one_step_then_measure": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_ONE_STEP_THEN_MEASURE_V2"),
    "marketer_late_day_live_converter": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_LIVE_CONVERTER_FALLBACK_V1"),
    "marketer_budget_brake_monotonic_overlimit": (ROOT / "backend/cpx_budget_brake.py", "CPX_BUDGET_BRAKE_MONOTONIC_OVERLIMIT_V2"),
    "marketer_late_day_spend_baseline_durable": (ROOT / "backend/app/api/cpx_advisor.py", "LATE_DAY_SPEND_BASELINE_DURABLE_V1"),
    "marketer_late_day_spend_exact_reset": (ROOT / "backend/app/services/autonomy.py", "LATE_DAY_SPEND_RESET_COMPENSATION_V1"),
    "marketer_late_day_spend_content_isolation": (ROOT / "backend/app/api/avito.py", "LATE_DAY_SPEND_CONTENT_ISOLATION_V1"),
    "marketer_late_day_spend_hourly_wire": (ROOT / "backend/ai_marketer_hourly.sh", "LATE_DAY_SPEND_CATCHUP_WIRE_V1"),
    "marketer_late_day_spend_midnight_wire": (ROOT / "backend/ai_marketer_hourly.sh", "LATE_DAY_SPEND_RESET_WIRE_V1"),
    "marketer_late_day_canonical_entitlement": (ROOT / "backend/late_day_spend_controller.py", "LATE_DAY_CANONICAL_SERVICE_ENTITLEMENT_V1"),
    "marketer_bid_cap_safety_lower_lane": (ROOT / "backend/cpx_cap_reconciler.py", "CPX_CAP_SAFETY_LOWER_LANE_V1"),
    "marketer_budget_brake_safety_lower_lane": (ROOT / "backend/cpx_budget_brake.py", "CPX_BUDGET_BRAKE_SAFETY_LOWER_LANE_V1"),
    "marketer_daily_experiment_journal": (ROOT / "backend/ai_marketer_hourly.sh", "DAILY_EXPERIMENT_JOURNAL_V1"),
    "marketer_daily_experiment_journal_dynamic_scope": (ROOT / "backend/app/services/marketing_experiment_journal.py", "DAILY_EXPERIMENT_JOURNAL_DYNAMIC_SCOPE_V1"),
    "marketer_daily_money_experiment_portfolio": (ROOT / "backend/app/services/marketing_experiment_journal.py", "DAILY_EXPERIMENT_PORTFOLIO_MONEY_V1"),
    "marketer_workstream_money_journal_scope": (ROOT / "backend/app/services/marketing_experiment_journal.py", "WS_AVITO_MONEY_JOURNAL_SCOPE_V1"),
    "marketer_profitable_day_continue_after_target": (ROOT / "backend/app/api/cpx_advisor.py", "PROFITABLE_DAY_CONTINUE_AFTER_TARGET_OWNER_RULE_V1"),
    "marketer_title_winner_memory": (ROOT / "backend/app/api/avito.py", "KPI_TITLE_WINNER_MEMORY_V1"),
    "marketer_stats_before_cpx_cap": (ROOT / "backend/ai_marketer_hourly.sh", "STATS_BEFORE_CPX_CAP_V1"),
    "marketer_direct_write_guard": (ROOT / "backend/app/api/cpxpromo.py", "auto_promotion_incompatible_with_hard_bid_cap"),
    "marketer_direct_budget_guard": (ROOT / "backend/app/api/cpxpromo.py", "blocked_daily_budget_exhausted"),
    "marketer_autonomy_last_guard": (ROOT / "backend/app/services/autonomy.py", "effective_hard_max_bid_rub"),
    "marketer_rollback_guard": (ROOT / "backend/app/api/action_log_api.py", "effective_hard_bid_cap"),
    "marketer_mandate_money_policy": (ROOT / "backend/app/api/cpx_advisor.py", "effective_hard_bid_cap"),
    "marketer_bid_cap_before_kpi": (ROOT / "backend/ai_marketer_hourly.sh", "bid_cap_reconcile"),
    "marketer_bid_cap_guardian": (ROOT / "backend/boris_guardian.py", "bid_cap_health"),
    "marketer_tenant_isolation": (ROOT / "backend/kpi_goal_runner.py", "ACCOUNT_INCIDENTS="),
    "marketer_lifecycle_obligation_scope": (ROOT / "backend/kpi_goal_runner.py", "LIFECYCLE_SCOPE_V1"),
    "marketer_lifecycle_wait_not_green": (ROOT / "backend/kpi_goal_runner.py", "LIFECYCLE_WAIT_NOT_GREEN_V1"),
    "marketer_lifecycle_priority": (ROOT / "backend/kpi_goal_runner.py", "LIFECYCLE_PRIORITY_V1"),
    "marketer_lifecycle_priority_wait_truth": (ROOT / "backend/kpi_goal_runner.py", "LIFECYCLE_PRIORITY_WAIT_TRUTH_V1"),
    "marketer_zero_budget_nonmoney_continuation": (ROOT / "backend/kpi_goal_runner.py", "ZERO_BUDGET_NONMONEY_CONTINUATION_V1"),
    "marketer_ownerless_nonmoney_execution": (ROOT / "backend/kpi_goal_runner.py", "OWNERLESS_NON_MONEY_EXECUTION_V1"),
    "marketer_ownerless_nonmoney_hourly_wire": (ROOT / "backend/ai_marketer_hourly.sh", "kpi_goal_runner.py --non-money-apply"),
    "marketer_kpi_apply_service_caller_guard": (ROOT / "backend/kpi_goal_runner.py", "KPI_APPLY_SERVICE_CALLER_GUARD_V1"),
    "marketer_ownerless_soak_service_guard": (ROOT / "backend/scripts/ownerless_marketer_75m_v3.py", "RETIRED_OWNERLESS_MARKETER_V1"),
    "deploy_kpi_lock": (ROOT / "backend/rolling_restart_backend.sh", "DEPLOY_KPI_LOCK_V1"),
    "deploy_nginx_root_atomic": (ROOT / "backend/rolling_restart_backend.sh", "ROLLING_NGINX_ROOT_ATOMIC_V2"),
    "deploy_restart_observed": (ROOT / "backend/rolling_restart_backend.sh", "restart not observed"),
    "deploy_instance_contract_retry": (ROOT / "backend/rolling_restart_backend.sh", "DEPLOY_INSTANCE_CONTRACT_RETRY_V1"),
    "deploy_source_quiescence": (ROOT / "backend/rolling_restart_backend.sh", "SOURCE_QUIESCENCE_BEFORE_ROLL_V1"),
    "backend_generation_content_certificate": (ROOT / "backend/production_contract_runner.py", "BACKEND_CONTENT_GENERATION_V1"),
    "backend_generation_rollout_certificate": (ROOT / "backend/rolling_restart_backend.sh", "BACKEND_CONTENT_CERTIFY_V1"),
    "deploy_public_5xx_strict": (ROOT / "backend/rolling_restart_backend.sh", "PUBLIC_5XX_STRICT_V1"),
    "deploy_nginx_drain_generation_fence": (ROOT / "backend/rolling_restart_backend.sh", "NGINX_DRAIN_GENERATION_FENCE_V1"),
    "runtime_heal_rolling": (ROOT / "backend/production_contract_runner.py", "RUNTIME_HEAL_ROLLING_V1"),
    "runtime_heal_settle_recheck": (ROOT / "backend/production_contract_runner.py", "RUNTIME_HEAL_SETTLE_RECHECK_V1"),
    "runtime_heal_deploy_lock_backoff": (ROOT / "backend/production_contract_runner.py", "RUNTIME_HEAL_DEPLOY_LOCK_BACKOFF_V1"),
    "deploy_coordinator_fail_closed": (ROOT / "backend/rolling_restart_backend.sh", "DEPLOY_COORDINATOR_FAIL_CLOSED_V1"),
    "guardian_nonbackend_source_quiet": (ROOT / "backend/production_guardian_tick.sh", "GUARDIAN_NONBACKEND_SOURCE_QUIET_V1"),
    "social_heartbeat_zero_age_truth": (ROOT / "backend/app/services/control_plane_adapters.py", "SOCIAL_HEARTBEAT_ZERO_AGE_TRUTH_V1"),
    "messages_owner_disabled_scope_truth": (ROOT / "backend/app/services/control_plane_adapters.py", "MESSAGE_OWNER_SCOPE_SEMANTICS_V1"),
    "crm_owner_disabled_source_wait_truth": (ROOT / "backend/app/services/control_plane_adapters.py", "CRM_OWNER_DISABLED_SOURCE_WAIT_V1"),
    "deploy_single_backend_rollout_owner": (ROOT / "backend/app/ext_api/deploy.py", "DEPLOY_SINGLE_BACKEND_ROLLOUT_OWNER_V1"),
    "runtime_heal_backend_delegated_to_deploy": (ROOT / "backend/production_contract_runner.py", "RUNTIME_HEAL_BACKEND_DELEGATED_TO_DEPLOY_V1"),
    "marketer_canonical_service_entitlement_scope": (ROOT / "backend/kpi_goal_runner.py", "MARKETER_CANONICAL_SERVICE_ENTITLEMENT_SCOPE_V1"),
    "marketer_unlimited_not_money_entitlement": (ROOT / "backend/app/api/cpx_advisor.py", "MARKETER_UNLIMITED_NOT_ENTITLEMENT_V1"),
    "marketer_rollout_canonical_entitlement": (ROOT / "backend/marketer_rollout_runner.py", "MARKETER_ROLLOUT_CANONICAL_ENTITLEMENT_V1"),
    "marketer_stale_runtime_source_union": (ROOT / "backend/marketer_rollout_runner.py", "MARKETER_STALE_RUNTIME_SOURCE_UNION_V2"),
    "marketer_stale_runtime_pre_global_guard": (ROOT / "backend/marketer_rollout_runner.py", "MARKETER_STALE_RUNTIME_PRE_GLOBAL_GUARD_V1"),
    "marketer_stale_runtime_brain_selfheal": (ROOT / "backend/app/services/brain_recovery.py", "MARKETER_STALE_RUNTIME_BRAIN_SELFHEAL_V1"),
    "marketer_paid_period_expiry_guard": (ROOT / "backend/kpi_goal_runner.py", "MARKETER_PAID_PERIOD_EXPIRY_GUARD_V1"),
    "marketer_kpi_config_gap_money_fail_closed": (ROOT / "backend/app/api/avito.py", "KPI_CONFIG_GAP_MONEY_FAIL_CLOSED_V1"),
    "marketer_kpi_config_gap_control_state": (ROOT / "backend/app/api/avito.py", "KPI_CONFIG_GAP_CONTROL_STATE_V1"),
    "marketer_zero_active_inventory_precedence": (ROOT / "backend/app/api/avito.py", "KPI_ZERO_ACTIVE_INVENTORY_PRECEDENCE_V1"),
    "marketer_zero_inventory_publishable_source_truth": (ROOT / "backend/app/api/avito.py", "KPI_ZERO_INVENTORY_PUBLISHABLE_SOURCE_TRUTH_V1"),
    "marketer_canonical_entitlement_guard": (ROOT / "backend/app/api/cpx_advisor.py", "MARKETER_CANONICAL_ENTITLEMENT_GUARD_V1"),
    "marketer_safe_deferred": (ROOT / "backend/kpi_goal_runner.py", "SAFE_DEFERRED="),
    "marketer_provider_deferred_safe": (ROOT / "backend/kpi_goal_runner.py", "except ProviderDeferred"),
    "marketer_ambiguous_http_no_replay": (ROOT / "backend/kpi_goal_runner.py", "except httpx.RemoteProtocolError"),
    "marketer_reach_blocked_diagnostic_fallback": (ROOT / "backend/app/api/avito.py", "reach_blocked_diagnostic"),
    "marketer_inventory_gap_non_money_fallback": (ROOT / "backend/kpi_goal_runner.py", "KPI_INVENTORY_GAP_NON_MONEY_FALLBACK_V1"),
    "marketer_nonmoney_prebootstrap_barrier": (ROOT / "backend/app/api/avito.py", "NEW_ITEM_NO_PROMO_BOOTSTRAP_V2"),
    "marketer_plan_diagnostics": (ROOT / "backend/cpx_advisor_runner.py", "plan_diagnostics"),
    "marketer_daily_kpi_control_formula": (ROOT / "backend/app/api/avito.py", "KPI_DAILY_CONTROL_FORMULA_V1"),
    "marketer_dynamic_conversion_diagnosis": (ROOT / "backend/app/api/avito.py", "KPI_DYNAMIC_CONVERSION_DIAGNOSIS_V1"),
    "marketer_owner_workspace_formula": (ROOT / "backend/app/api/avito.py", "KPI_OWNER_WORKSPACE_FORMULA_V1"),
    "marketer_kpi_urgency_from_lead_gap": (ROOT / "backend/app/api/avito.py", "KPI_URGENCY_FROM_LEAD_GAP_V1"),
    "marketer_kpi_recovery_multiplier": (ROOT / "backend/app/api/avito.py", "KPI_RECOVERY_MULTIPLIER_V1"),
    "marketer_kpi_control_state_machine": (ROOT / "backend/app/api/avito.py", "KPI_CONTROL_STATE_MACHINE_V1"),
    "marketer_single_hypothesis_per_item": (ROOT / "backend/app/api/avito.py", "KPI_SINGLE_HYPOTHESIS_PER_ITEM_V1"),
    "marketer_active_experiment_plan_exclusion": (ROOT / "backend/app/api/avito.py", "KPI_ACTIVE_EXPERIMENT_PLAN_EXCLUSION_V1"),
    "marketer_action_outcome_contract": (ROOT / "backend/app/api/avito.py", "KPI_ACTION_OUTCOME_CONTRACT_V1"),
    "marketer_daily_kpi_pool_isolation": (ROOT / "backend/app/api/avito.py", "KPI_DAILY_CONTROL_POOL_ISOLATION_V1"),
    "marketer_kpi_economics_first": (ROOT / "backend/app/api/avito.py", "KPI_ECONOMICS_FIRST_V1"),
    "marketer_reduce_cpl_executor": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_EXECUTOR_V1"),
    "marketer_reduce_cpl_hourly_idempotency": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_HOURLY_IDEMPOTENCY_V1"),
    "marketer_reduce_cpl_feedback_guard": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_FEEDBACK_GUARD_V1"),
    "marketer_winner_scale_step_10": (ROOT / "backend/app/api/avito.py", "KPI_WINNER_SCALE_STEP_10_V1"),
    "marketer_winner_scale_hourly_idempotency": (ROOT / "backend/app/api/avito.py", "KPI_WINNER_SCALE_HOURLY_IDEMPOTENCY_V1"),
    "marketer_winner_scale_positive_feedback_only": (ROOT / "backend/app/api/avito.py", "KPI_WINNER_SCALE_POSITIVE_FEEDBACK_ONLY_V1"),
    "marketer_mutation_budget_per_cycle": (ROOT / "backend/app/api/avito.py", "KPI_MUTATION_BUDGET_BY_URGENCY_V1"),
    "marketer_recovery_capacity_truth": (ROOT / "backend/app/api/avito.py", "KPI_RECOVERY_CAPACITY_TRUTH_V1"),
    "marketer_critical_recovery_cohort": (ROOT / "backend/app/api/avito.py", "KPI_CRITICAL_RECOVERY_COHORT_V2"),
    "marketer_global_autonomous_raise_stop": (ROOT / "backend/app/api/cpx_advisor.py", "KPI_GLOBAL_AUTONOMOUS_RAISE_STOP_V1"),
    "marketer_profitable_day_push": (ROOT / "backend/app/api/cpx_advisor.py", "PROFITABLE_DAY_PUSH_V1"),
    "marketer_profitable_day_push_wire": (ROOT / "backend/marketer_rollout_runner.py", "OWNER_PROFITABLE_DAY_SQUEEZE_V1"),
    "marketer_profitable_day_push_skip_blocked": (ROOT / "backend/app/api/cpx_advisor.py", "PROFITABLE_DAY_PUSH_SKIP_BLOCKED_CANDIDATES_V1"),
    "marketer_fact_guard_rejection_not_ai_failure": (ROOT / "backend/app/api/avito.py", "KPI_FACT_GUARD_REJECTION_NOT_AI_FAILURE_V1"),
    "marketer_prepare_terminal_stale_demotion": (ROOT / "backend/app/api/avito.py", "KPI_PREPARE_TERMINAL_STALE_DEMOTION_V1"),
    "marketer_prepare_same_input_durable_dedupe": (ROOT / "backend/app/api/avito.py", "KPI_PREPARE_SAME_INPUT_DURABLE_DEDUPE_V1"),
    "marketer_goal_paid_period_expiry_guard": (ROOT / "backend/app/api/avito.py", "KPI_GOAL_SERVICE_PERIOD_FAIL_CLOSED_V2"),
    "marketer_reduce_cpl_goal_wire": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_GOAL_WIRE_V1"),
    "marketer_reduce_cpl_blocked_diagnostic_fallback": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_BLOCKED_DIAGNOSTIC_FALLBACK_V1"),
    "marketer_reduce_cpl_guard": (ROOT / "backend/app/api/avito.py", "KPI_REDUCE_CPL_GUARD_V1"),
    "marketer_money_lane_block_reason": (ROOT / "backend/app/api/avito.py", "KPI_MONEY_LANE_BLOCK_REASON_V1"),
    "marketer_intraday_cpl_canonical_spend": (ROOT / "backend/app/services/intraday.py", "KPI_INTRADAY_CPL_CANONICAL_SPEND_V1"),
    "marketer_intraday_cpl_redline_block": (ROOT / "backend/app/services/intraday.py", "KPI_INTRADAY_CPL_REDLINE_BLOCK_V1"),
    "marketer_owner_action_only_if_required": (ROOT / "backend/app/api/avito.py", "KPI_OWNER_ACTION_ONLY_IF_REQUIRED_V1"),
    "marketer_current_work_action_truth": (ROOT / "backend/app/api/avito.py", "KPI_CURRENT_WORK_ACTION_TRUTH_V1"),
    "marketer_idle_mode_truth": (ROOT / "backend/cpx_advisor_runner.py", "MARKETER_IDLE_MODE_TRUTH_V1"),
    "marketer_staged_money_rollout": (ROOT / "backend/marketer_rollout_runner.py", "MAX_ACCOUNTS_PER_CYCLE=8"),
    "marketer_rollout_service_caller_guard": (ROOT / "backend/marketer_rollout_runner.py", "MARKETER_ROLLOUT_SELF_CGROUP_IMPORT_V1"),
    "marketer_money_contour_autodeploy_protected": (ROOT / "backend/app/ext_api/repo.py", "MONEY_CONTOUR_PROTECTED_V1"),
    "marketer_kpi_storage_money_reconcile_db": (ROOT / "backend/sql/marketer_kpi_money_reconcile_v1.sql", "BORIS_KPI_MONEY_RECONCILE_DB_V2_MAX150"),
    "marketer_control_plane_hard_cap_db_guard": (ROOT / "backend/sql/control_plane_kpi_hard_cap_guard_v1.sql", "CONTROL_PLANE_KPI_HARD_CAP_DB_GUARD_V1"),
    "control_plane_no_github_dependency": (ROOT / "backend/app/services/control_plane.py", "system.no_github_dependency"),
    "marketer_staged_canary_action_cap": (ROOT / "backend/marketer_rollout_runner.py", "planned==0 or (applied>0 and blocked==0)"),
    "marketer_staged_live_proof_required": (ROOT / "backend/marketer_rollout_runner.py", "ROLLOUT_LIVE_PROOF_REQUIRED_V1"),
    "marketer_rollout_guard_block_not_failure": (ROOT / "backend/marketer_rollout_runner.py", "ROLLOUT_GUARD_BLOCK_NOT_FAILURE_V1"),
    "messages_to_mop_dispatch_truth": (ROOT / "backend/app/services/control_plane_adapters.py", "MESSAGE_TO_MOP_DISPATCH_TRUTH_V1"),
    "marketer_reach_intraday_no_contact_stop": (ROOT / "backend/app/services/intraday.py", "REACH_RESCUE_MAX_VIEWS_TODAY_NO_CONTACT = 10"),
    "marketer_negative_feedback_planner_block": (ROOT / "backend/app/services/intraday.py", "_negative_raise_measurement"),
    "marketer_new_ad_watchdog": (ROOT / "backend/new_ad_bid_watch_runner.py", "GLOBAL_MUTATION_CAP=3"),
    "marketer_new_ad_watchdog_worker": (ROOT / "backend/boris_background_worker.py", "_new_ad_bid_watch_loop"),
    "marketer_uid_self_heal": (ROOT / "backend/app/api/cpx_advisor.py", "AVITO_UID_SELF_HEAL_V1"),
    "marketer_measure_no_signal_elapsed_timeout": (ROOT / "backend/app/api/cpx_advisor.py", "MEASURE_NO_SIGNAL_ELAPSED_TIMEOUT_V2"),
    "marketer_measurement_backlog_guard": (ROOT / "backend/app/api/cpx_advisor.py", "MEASUREMENT_BACKLOG_GUARD_V2"),
    "marketer_lowviews_zero_bid_handoff": (ROOT / "backend/marketer_rollout_runner.py", "LOWVIEWS_ZERO_BID_HANDOFF_V1"),
    "marketer_owner_priority_fair_rotation": (ROOT / "backend/marketer_rollout_runner.py", "OWNER_PRIORITY_FAIR_ROTATION_V2"),
    "marketer_title_active_cohort_20pct": (ROOT / "backend/app/api/avito.py", "KPI_TITLE_ACTIVE_COHORT_EXACT20_V2"),
    "marketer_title_cohort_rotation": (ROOT / "backend/app/api/avito.py", "KPI_TITLE_COHORT_BATCH_ROTATION_V1"),
    "marketer_parallel_title_disjoint": (ROOT / "backend/app/api/avito.py", "KPI_PARALLEL_TITLE_DISJOINT_REFILL_V1"),
    "marketer_cpl_parallel_content_recovery": (ROOT / "backend/app/api/avito.py", "KPI_CPL_RECOVERY_PARALLEL_TITLE_V1"),
    "marketer_nonmoney_parallel_content_budget": (ROOT / "backend/app/api/avito.py", "KPI_NON_MONEY_PARALLEL_CONTENT_BUDGET_V1"),
    "marketer_exact_avito_feed_snapshot_recovery": (ROOT / "backend/app/api/avito.py", "official_autoload_feed_snapshot"),
    "marketer_cpx_capability_cooldown": (ROOT / "backend/app/api/cpx_advisor.py", "CPX_ITEM_CAPABILITY_COOLDOWN_V1"),
    "marketer_lowviews_capability_rotation": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V2"),
    "marketer_lowviews_capability_retry_24h": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V3"),
    "marketer_lowviews_run_id_scope": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_RUN_ID_SCOPE_V1"),
    "marketer_lowviews_provider_alias_hourly": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_PROVIDER_ALIAS_HOURLY_V1"),
    "marketer_lowviews_negative_feedback_guard": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_NEGATIVE_FEEDBACK_GUARD_V1"),
    "marketer_lowviews_account_signal_cooldown": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_ACCOUNT_SIGNAL_COOLDOWN_V1"),
    "marketer_reach_rescue_cohort_v2": (ROOT / "backend/app/services/intraday.py", "REACH_RESCUE_COHORT_DURABLE_V2"),
    "marketer_kpi_first_bid_bounded": (ROOT / "backend/kpi_goal_runner.py", "FIRST_BID_CAUSAL_SINGLE_STEP_V2"),
    "marketer_kpi_feed_ramp_bounded": (ROOT / "backend/kpi_goal_runner.py", "hourly_new_feed_low_views_ramp(account_id, max_items=1)"),
    "marketer_account_low_views_persistent": (ROOT / "backend/marketer_rollout_runner.py", "STAGED_LOW_VIEWS_10_20_30_V1"),
    "marketer_lowviews_service_caller_guard": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_SERVICE_CALLER_GUARD_V2"),
    "marketer_staged_low_views_controlled_lane": (ROOT / "backend/marketer_rollout_runner.py", "STAGED_LOW_VIEWS_CONTROLLED_LANE_V1"),
    "marketer_unified_staged_all_accounts": (ROOT / "backend/marketer_rollout_runner.py", "UNIFIED_STAGED_ALL_MARKETER_ACCOUNTS_V1"),
    "marketer_account_low_views_fresh_signal": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_FRESH_SIGNAL_V2"),
    "marketer_account_low_views_short_measure": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_SHORT_MEASURE_V2"),
    "marketer_account_low_views_blocked_rotation": (ROOT / "backend/app/api/cpx_advisor.py", "ACCOUNT_LOW_VIEWS_BLOCKED_ROTATION_V1"),
    "bid_cap_provider_deferred_safe": (ROOT / "backend/cpx_cap_reconciler.py", "provider_deferred"),
    "stats_tenant_isolation": (ROOT / "backend/daily_stats_collector.py", "DAILY_STATS_PARTIAL"),
    "stats_systemic_fail_closed": (ROOT / "backend/daily_stats_collector.py", "DAILY_STATS_SYSTEMIC_FAIL"),
    "marketer_description_executor": (ROOT / "backend/app/api/avito.py", "_kpi_build_descriptions_prompt"),
    "marketer_description_sequential_guard": (ROOT / "backend/app/api/avito.py", "active_after_title_failure"),
    "marketer_first_image_executor": (ROOT / "backend/app/api/avito.py", "prepare_first_image_test"),
    "marketer_first_image_reorder_only": (ROOT / "backend/app/api/avito.py", "First-image test может только менять порядок уже существующей галереи"),
    "marketer_first_image_sequential_guard": (ROOT / "backend/app/api/avito.py", "title AND description"),
    "marketer_rollback_live_confirmation": (ROOT / "backend/app/api/avito.py", "AUTOLOAD_ROLLBACK_CONFIRMATION_V1"),
    "marketer_template_title_live_proof": (ROOT / "backend/app/api/avito.py", "KPI_TEMPLATE_TITLE_LIVE_PROOF_V1"),
    "marketer_publish_stale_autoretry": (ROOT / "backend/app/api/avito.py", "STALE_PUBLISH_AUTORETRY_V1"),
    "marketer_confirm_session_isolation": (ROOT / "backend/app/api/avito.py", "KPI_CONFIRM_SESSION_ISOLATION_V1"),
    "marketer_goal_session_boundaries": (ROOT / "backend/app/api/avito.py", "KPI_GOAL_SESSION_BOUNDARY_V1"),
    "marketer_mapping_bounded": (ROOT / "backend/app/api/avito.py", "KPI_MAPPING_BOUNDED_V2"),
    "marketer_prepare_publishable_mapping_preflight": (ROOT / "backend/app/api/avito.py", "KPI_PREPARE_PUBLISHABLE_MAPPING_PREFLIGHT_V1"),
    "marketer_field_scoped_rollback": (ROOT / "backend/app/api/avito.py", "KPI_FIELD_SCOPED_ROLLBACK_V2"),
    "marketer_rollback_media_scope": (ROOT / "backend/app/api/avito.py", "ROLLBACK_MEDIA_SCOPE_V2"),
    "marketer_rollback_stale_autoretry": (ROOT / "backend/app/api/avito.py", "STALE_ROLLBACK_AUTORETRY_V1"),
    "marketer_rollback_snapshot_selfheal": (ROOT / "backend/app/api/avito.py", "ROLLBACK_SNAPSHOT_SELFHEAL_V1"),
    "marketer_rollback_provider_stalled": (ROOT / "backend/app/api/avito.py", "avito_autoload_not_applying_after_bounded_retries"),
    "marketer_provider_stalled_incident": (ROOT / "backend/kpi_goal_runner.py", "LIFECYCLE_PROVIDER_STALLED"),
    "marketer_bootstrap_canonical_identity_only": (ROOT / "backend/app/api/cpx_advisor.py", "CANONICAL_FEED_BID_SCOPE_V1"),
    "marketer_first_activation_only": (ROOT / "backend/app/api/cpx_advisor.py", "FIRST_ACTIVATION_ONLY_V1"),
    "marketer_first_bid_item_block_isolation": (ROOT / "backend/app/api/cpx_advisor.py", "FIRST_BID_ITEM_BLOCK_ISOLATION_V1"),
    "marketer_first_activation_scope": (ROOT / "backend/app/api/cpx_advisor.py", 'strip() in current_feed_ids'),
    "marketer_first_bid_launch_mandate": (ROOT / "backend/app/api/cpx_advisor.py", "new_feed_launch"),
    "marketer_first_bid_standing_owner_rule": (ROOT / "backend/app/api/cpx_advisor.py", "NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3"),
    "marketer_money_db_guard_standing_launch": (ROOT / "backend/sql/marketer_money_mandate_guard_v1.sql", "BORIS_MARKETER_MONEY_MANDATE_DB_GUARD_V5_MAX150"),
    "marketer_first_bid_budget_separation": (ROOT / "backend/app/services/autonomy.py", "MONEY_GUARD_ALL_RAISES_V3"),
    "marketer_first_bid_later_raises_full_money_guard": (ROOT / "backend/app/services/autonomy.py", "check_raise_allowed as _check_raise_allowed"),
    "marketer_first_bid_live_balance_required": (ROOT / "backend/app/services/autonomy.py", "check_raise_allowed as _check_raise_allowed"),
    "marketer_first_bid_zero_budget_separation": (ROOT / "backend/production_contract_runner.py", "FIRST_BID_ZERO_BUDGET_SEPARATION_V2"),
    "marketer_first_bid_absolute_hard_cap": (ROOT / "backend/app/services/autonomy.py", "NEW_FEED_FIRST_BID_ABSOLUTE_HARD_CAP_V3"),
    "marketer_first_bid_hard_cap_clamp": (ROOT / "backend/app/api/cpx_advisor.py", "NEW_FEED_FIRST_BID_HARD_CAP_CLAMP_V3"),
    "marketer_money_guard_session_isolation": (ROOT / "backend/app/services/autonomy.py", "MONEY_GUARD_ALL_RAISES_V3"),
    "marketer_historical_demand_pacing": (ROOT / "backend/app/services/intraday.py", "messenger_leads_same_weekday_60d"),
    "marketer_spend_pacing_gate": (ROOT / "backend/app/services/intraday.py", "blocked_spend_pacing"),
    "marketer_intraday_budget_fail_closed": (ROOT / "backend/app/services/intraday.py", "advisor_daily_budget_missing"),
    "marketer_explicit_budget_mandate": (ROOT / "backend/app/api/cpx_advisor.py", "EXPLICIT_DAILY_BUDGET_MANDATE_V1"),
    "marketer_budget_owner_provenance_guard": (ROOT / "backend/app/api/cpx_advisor.py", "MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1"),
    "kpi_budget_owner_provenance_write": (ROOT / "backend/app/api/avito.py", "MONEY_BUDGET_OWNER_PROVENANCE_V1"),
    "feed_pilot_scale_replacement_cleanup": (ROOT / "backend/app/services/campaign_post_publish_watch.py", "PILOT_SCALE_OLD_CAMPAIGN_CLEANUP_V1"),
    "marketer_readiness_explicit_budget_truth": (ROOT / "backend/app/api/avito.py", "KPI_MONEY_READINESS_TRUTH_V1"),
    "marketer_autonomous_mode_unified": (ROOT / "backend/app/api/avito.py", "AUTONOMOUS_MARKETING_MODE_UNIFIED_V1"),
    "frontend_autonomous_mode_unified": (ROOT / "frontend/app/dashboard/marketing/page.tsx", "AUTONOMOUS_MARKETING_MODE_UNIFIED_V1"),
    "marketer_zero_budget_raise_revocation": (ROOT / "backend/production_contract_runner.py", "money_raise_disabled_missing_explicit_budget"),
    "marketer_position_monitor": (ROOT / "backend/app/services/avito_position_monitor.py", "browser_gateway_pro_items"),
    "marketer_position_harvest": (ROOT / "backend/app/services/avito_position_monitor.py", "def harvest_latest_position_job"),
    "browser_position_harvest_payload": (ROOT / "backend/app/api/browser_gateway.py", "POSITION_HARVEST_PAYLOAD_V1"),
    "marketer_position_identity_guard": (ROOT / "backend/app/services/avito_position_monitor.py", "identity-verified Browser Gateway"),
    "marketer_position_money_guard": (ROOT / "backend/app/api/avito.py", "existing_kpi_budget_and_cpl_guards_only"),
    "marketer_position_kpi_signal": (ROOT / "backend/app/api/avito.py", "low_search_position"),
    "frontend_position_monitor_ui": (ROOT / "frontend/app/dashboard/marketing/page.tsx", "Позиция в поиске Avito"),
}


def _source_checks() -> dict:
    out = {}
    for key, (path, marker) in REQUIRED_MARKERS.items():
        try:
            out[key] = marker in path.read_text(encoding="utf-8")
        except Exception:
            out[key] = False
    active = ROOT / "frontend/.boris-builds/current/server"
    try:
        blobs = []
        candidates=[p for p in active.rglob("*.js") if "campaign_[id]" in p.name] + list(active.rglob("*.js"))
        seen=set()
        for path in candidates:
            if path in seen: continue
            seen.add(path)
            try:
                txt = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            blobs.append(txt)
            probe="\n".join(blobs)
            if "Сколько объявлений нужно" in probe and "Добавить выбранные названия" in probe and "Использовать свой текст или примеры" in probe:
                break
            if len(blobs) >= 180:
                break
        joined = "\n".join(blobs)
        out["frontend_source_text_ui"] = "Использовать свой текст или примеры" in joined
        out["frontend_search_reco_ui"] = "Добавить выбранные названия" in joined
        out["frontend_exact_quantity_ui"] = "Сколько объявлений нужно" in joined
        out["frontend_text_upload_ui"] = "Или загрузить свой текст" in joined
        out["frontend_no_server_jargon"] = "мощность сервера" not in joined and "работает на сервере" not in joined
        # Generic deployment invariant: current frontend source must be exactly
        # the snapshot from which the active immutable artifact was built.
        import subprocess
        source_hash=subprocess.check_output(["python3",str(ROOT/"frontend/scripts/frontend-source-hash.py")],text=True,timeout=15).strip()
        meta_path=ROOT/"frontend/.boris-builds/current/BORIS_ARTIFACT.json"
        meta=json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        out["frontend_active_artifact_ready"]=str(meta.get("status") or "")=="READY" and bool(meta.get("immutable"))
        out["frontend_source_deployed"]=bool(source_hash) and source_hash==str(meta.get("snapshot_source_hash") or "")

        # MOP_TRAINING_END_TO_END_CONTRACT_V1:
        # keep combat sparring UI, persistence and production learning on one
        # compatible contract. This guards the exact class of regression where
        # frontend created a combat session but saved feedback to the legacy
        # conversational-training store.
        mop_front=(ROOT/"frontend/app/messages/mop-training/page.tsx").read_text(encoding="utf-8")
        mop_back=(ROOT/"backend/app/api/mop_combat_training.py").read_text(encoding="utf-8")
        mop_msg=(ROOT/"backend/app/api/messenger.py").read_text(encoding="utf-8")
        _save_start=mop_front.find("async function saveHumanFeedback")
        _save_end=mop_front.find("async function updateLearnedRule", _save_start)
        _save_block=mop_front[_save_start:_save_end] if _save_start >= 0 and _save_end > _save_start else ""
        _finish_start=mop_front.find("async function finishTraining")
        _finish_end=mop_front.find("async function saveHumanFeedback", _finish_start)
        _finish_block=mop_front[_finish_start:_finish_end] if _finish_start >= 0 and _finish_end > _finish_start else ""
        out["mop_training_combat_feedback_endpoint"] = (
            'training.mode === "mop_combat_sparring"' in _save_block
            and '"/api/mop-combat/sparring/feedback"' in _save_block
        )
        out["mop_training_combat_learning_endpoint"] = (
            '"/api/mop-combat/sparring/learning"' in _save_block
            and 'action: "remember"' in _save_block
        )
        out["mop_training_combat_finish_endpoint"] = (
            'training.mode === "mop_combat_sparring"' in _finish_block
            and '"/api/mop-combat/sparring/finish"' in _finish_block
        )
        out["mop_training_score_scale_10"] = "score: int = Field(ge=1, le=10)" in mop_back
        out["mop_training_rule_persistence"] = (
            '@router.post("/sparring/learning")' in mop_back
            and "client_facts" in mop_back
            and '"proposal_status":"remembered"' in mop_back
        )
        out["mop_training_stale_session_selfheal"] = (
            "def _heal_stale_sparring_sessions" in mop_back
            and '"stale_session_auto_heal"' in mop_back
            and "stale_sessions_healed=_heal_stale_sparring_sessions" in mop_back
        )
        out["mop_training_learning_relevance_gate"] = (
            "def _mop_confirmed_learning_relevant" in mop_msg
            and "_learning_relevant_now" in mop_msg
        )
        out["mop_training_learning_priority"] = (
            "ПОДТВЕРЖДЁННОЕ ОБУЧЕНИЕ — ОБЯЗАТЕЛЬНО К ПРИМЕНЕНИЮ" in mop_msg
            and "+ learning_priority_block" in mop_msg
            and "learning_priority_block[:1200]" in mop_msg
            and 'BORIS_MOP_LOCAL_NUM_CTX", "3072"' in mop_msg
            and 'BORIS_MOP_LOCAL_NUM_PREDICT", "160"' in mop_msg
        )
        out["mop_training_no_crm_pollution"] = (
            "MOP_TRAINING_NO_CRM_POLLUTION_V1" in mop_msg
            and 'str(chat_id).startswith("training:")' in mop_msg
        )
        out["mop_training_delivery_disabled"] = (
            '"avito_send":False' in mop_back
            and '"delivery":"disabled"' in mop_back
        )

        # Browser Gateway download is production code. Verify the ZIP offered to
        # clients is exactly the same four-file extension as the current source.
        import zipfile
        gw_dir=ROOT/"frontend/public/boris-browser-gateway"
        gw_zip=ROOT/"frontend/public/boris-browser-gateway.zip"
        gw_files=["manifest.json","background.js","popup.html","popup.js"]
        gw_ok=gw_zip.exists() and all((gw_dir/f).exists() for f in gw_files)
        if gw_ok:
            with zipfile.ZipFile(gw_zip) as z:
                expected=sorted("boris-browser-gateway/"+f for f in gw_files)
                actual=sorted(n for n in z.namelist() if not n.endswith("/"))
                gw_ok = actual == expected and all(z.read("boris-browser-gateway/"+f)==(gw_dir/f).read_bytes() for f in gw_files)
        out["browser_gateway_zip_current"]=bool(gw_ok)
        public_gw=pathlib.Path("/var/www/boris-browser-gateway.zip")
        out["browser_gateway_public_zip_current"] = bool(gw_ok and public_gw.exists() and public_gw.read_bytes()==gw_zip.read_bytes())
        out["browser_gateway_public_path_active"] = subprocess.run(["systemctl","is-active","--quiet","boris-browser-gateway-sync.path"],timeout=5).returncode == 0
    except Exception:
        out["frontend_source_text_ui"] = False
        out["frontend_search_reco_ui"] = False
        out["frontend_exact_quantity_ui"] = False
        out["frontend_text_upload_ui"] = False
        out["frontend_no_server_jargon"] = False
        out["frontend_active_artifact_ready"] = False
        out["frontend_source_deployed"] = False
        out["mop_training_combat_feedback_endpoint"] = False
        out["mop_training_combat_learning_endpoint"] = False
        out["mop_training_combat_finish_endpoint"] = False
        out["mop_training_score_scale_10"] = False
        out["mop_training_rule_persistence"] = False
        out["mop_training_stale_session_selfheal"] = False
        out["mop_training_learning_relevance_gate"] = False
        out["mop_training_learning_priority"] = False
        out["mop_training_no_crm_pollution"] = False
        out["mop_training_delivery_disabled"] = False
        out["browser_gateway_zip_current"] = False
        out["browser_gateway_public_zip_current"] = False
        out["browser_gateway_public_path_active"] = False
    # GLOBAL_HA_SINGLE_ROLLOUT_OWNER_SCAN_V1: one-off helper scripts are a real
    # production risk if they can restart both APIs outside canonical rolling.
    import re as _re
    unsafe=[]
    for _base in (ROOT/"backend/run", ROOT/"backend/scripts"):
        if not _base.exists(): continue
        for _p in _base.rglob("*"):
            if not _p.is_file() or _p.suffix not in {".py",".sh"} or ".bak" in _p.name: continue
            if _p.name == "rolling_restart_backend.sh": continue
            try: _txt=_p.read_text(encoding="utf-8",errors="ignore")
            except Exception: continue
            for _line in _txt.splitlines():
                if "systemctl" in _line and "boris-backend" in _line and _re.search(r"(restart|try-restart|stop|start)",_line):
                    unsafe.append(str(_p.relative_to(ROOT)))
                    break
    out["single_backend_rollout_owner_runtime_scan"] = not unsafe
    # BACKEND_MANUAL_RESTART_REFUSAL_CONTRACT_V1:
    # An ad-hoc root/SentinelX restart once stopped both nginx upstreams in the
    # same second. Both API units must refuse ordinary manual stop/restart.
    try:
        _manual_guard=[]
        for _u in ("boris-backend.service","boris-backend-replica.service"):
            _v=subprocess.check_output(
                ["systemctl","show",_u,"-p","RefuseManualStop","--value"],
                text=True,timeout=5
            ).strip().lower()
            _manual_guard.append(_v=="yes")
        out["backend_manual_restart_refused"] = all(_manual_guard)
    except Exception:
        out["backend_manual_restart_refused"] = False
    return out


def _service_started_epoch(unit: str) -> float:
    """Convert systemd monotonic activation time to wall-clock epoch."""
    import subprocess, time
    try:
        raw=subprocess.check_output([
            "systemctl","show",unit,"-p","ActiveEnterTimestampMonotonic","--value"
        ],text=True,timeout=5).strip()
        mono=float(raw or 0)/1_000_000.0
        if mono <= 0:
            return 0.0
        with open("/proc/uptime","r",encoding="utf-8") as fh:
            uptime=float(fh.read().split()[0])
        boot_epoch=time.time()-uptime
        return boot_epoch+mono
    except Exception:
        return 0.0


# Runtime generation must follow what each long-lived process actually imports.
# Treating every file under app/ as a dependency of every service caused unrelated
# parallel edits (for example a route used only by the API) to mark the background
# worker stale. API replicas intentionally keep the broad app/ scope; the dedicated
# worker gets its own import-root scope below.
BACKEND_RUNTIME_PATHS=[p for p in (ROOT/'backend/app').rglob('*.py') if '__pycache__' not in p.parts]+[ROOT/'backend/gigachat_pool.py']
BACKGROUND_RUNTIME_PATHS=[
    ROOT/'backend/boris_background_worker.py',
    ROOT/'backend/app/worker_runtime.py',
    ROOT/'backend/app/services/jobs.py',
    # Direct import roots of boris_background_worker.py. These long-lived
    # modules must invalidate the worker generation when their bytes change;
    # otherwise Telegram/MOP/background loops can keep stale code until an
    # owner notices and restarts the worker manually.
    ROOT/'backend/app/telegram_bot.py',
    ROOT/'backend/app/api/messenger.py',
    ROOT/'backend/app/mop_core.py',
    ROOT/'backend/app/api/client_memory.py',
    ROOT/'backend/app/crm/bridge.py',
    ROOT/'backend/app/api/mass_editor.py',
    ROOT/'backend/app/api/cpx_advisor.py',
    ROOT/'backend/app/services/reliability.py',
    ROOT/'backend/app/services/sales_ai_router.py',
    ROOT/'backend/app/db/session.py',
    ROOT/'backend/app/db/base.py',
    ROOT/'backend/app/main.py',
    # Feed Factory jobs dynamically import these modules. They are part of the
    # worker runtime generation even though they are not direct top-level imports.
    ROOT/'backend/app/api/campaigns.py',
    ROOT/'backend/app/services/media_semantic_validator.py',
    ROOT/'backend/app/services/media_service.py',
    ROOT/'backend/app/services/category_resolver.py',
    ROOT/'backend/app/api/feed_contract.py',
    # Autonomous transient-incident reconciliation is part of the worker runtime.
    ROOT/'backend/app/services/incident_reconciler.py',
    # Post-publication campaign cleanup/measurement is executed by the same
    # background worker. A changed watchdog must invalidate the worker generation
    # certificate so ownerless recovery cannot keep running stale imported code.
    ROOT/'backend/app/services/campaign_post_publish_watch.py',
]



def _generation_fingerprint() -> str:
    """Stable fingerprint of the production source generation, without secrets."""
    import hashlib
    h=hashlib.sha256()
    paths=list(BACKEND_RUNTIME_PATHS)+[
        ROOT/'backend/production_contract_runner.py',
        ROOT/'backend/boris_guardian.py',
        ROOT/'backend/ai_marketer_hourly.sh',
        ROOT/'backend/kpi_goal_runner.py',
        ROOT/'backend/daily_stats_collector.py',
        ROOT/'backend/cpx_advisor_runner.py',
        ROOT/'backend/portfolio_bootstrap_runner.py',
    ]
    for p in sorted({x for x in paths if x.exists()},key=lambda x:str(x)):
        try:
            st=p.stat(); rel=str(p.relative_to(ROOT))
            h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        except Exception:
            continue
    meta=ROOT/'frontend/.boris-builds/current/BORIS_ARTIFACT.json'
    try:
        m=json.loads(meta.read_text(encoding='utf-8'))
        h.update(str(m.get('build_id') or '').encode())
        h.update(str(m.get('snapshot_source_hash') or '').encode())
    except Exception:
        h.update(b'frontend-meta-missing')
    return h.hexdigest()


BACKEND_GENERATION_CERT=ROOT/'backend/run/backend_runtime_content.sha256'

def _backend_runtime_content_fingerprint() -> str:
    """Hash API runtime bytes; metadata-only chmod/chattr must not create a fake generation."""
    import hashlib
    h=hashlib.sha256()
    for path in sorted((x for x in BACKEND_RUNTIME_PATHS if x.exists()), key=lambda x:str(x)):
        try:
            h.update(str(path.relative_to(ROOT)).encode()+b'\0')
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):
                    h.update(chunk)
            h.update(b'\0')
        except Exception:
            h.update((str(path)+'|unreadable').encode())
    return h.hexdigest()


RUNTIME_GROUPS={
    "backend_runtime_generation": (
        ["boris-backend.service","boris-backend-replica.service"],
        BACKEND_RUNTIME_PATHS
    ),
    "background_runtime_generation": (
        ["boris-background-worker.service"],
        BACKGROUND_RUNTIME_PATHS
    ),
    "crm_runtime_generation": (
        ["boris-crm-sync.service"],
        [ROOT/"backend/app/crm/bridge.py",ROOT/"backend/app/services/calltracking_sync.py"]
    ),
    "telephony_runtime_generation": (
        ["boris-telephony-runtime.service"],
        [ROOT/"backend/app/services/telephony_core.py",ROOT/"backend/app/api/telephony.py"]
    ),
    "deploy_runtime_generation": (
        ["boris-deploy.service"],
        [ROOT/"backend/app/ext_api/deploy.py"]
    ),
}


def _runtime_generation_state() -> tuple[dict,dict]:
    """Return PASS/FAIL per runtime group plus exact stale units."""
    out={}; stale={}
    for key,(units,paths) in RUNTIME_GROUPS.items():
        # Safe atomic editors can preserve mtime while ctime still records the
        # replacement/inode metadata change. Runtime freshness must conservatively
        # use the newest observable source timestamp or a pre-edit process can be
        # falsely certified as current.
        source_times=[max(p.stat().st_mtime,p.stat().st_ctime) for p in paths if p.exists()]
        required=max(source_times) if source_times else 0.0
        bad=[]
        # BACKEND_CONTENT_GENERATION_V1: API source files are frequently protected
        # with chmod/chattr/atomic metadata operations. ctime changes in that case
        # while Python bytes do not. A successful canonical HA rollout writes a
        # content certificate; when bytes still match it, metadata-only churn is
        # not a stale runtime generation. If bytes differ, conservative timestamp
        # freshness remains the fallback until rolling_restart_backend certifies it.
        certified_content=False
        if key == "backend_runtime_generation":
            try:
                certified_content=(BACKEND_GENERATION_CERT.read_text().strip()==_backend_runtime_content_fingerprint())
            except Exception:
                certified_content=False
        for unit in units:
            started=_service_started_epoch(unit)
            if certified_content:
                if started <= 0:
                    bad.append(unit)
            elif not required or started + 0.25 < required:
                bad.append(unit)
        out[key]=not bad
        if bad: stale[key]=bad
    return out,stale


def _runtime_generation_checks() -> dict:
    """Detect partial rollouts: long-lived services must be newer than code they import."""
    return _runtime_generation_state()[0]


def _self_heal_runtime_generation() -> dict:
    """Restart only stale authoritative runtimes once, then verify again.

    This is intentionally bounded: one restart attempt per stale unit per contract
    run. If a unit still fails generation/health, the contract remains red.
    """
    import os, subprocess, time
    checks,stale=_runtime_generation_state()
    if not stale or str(os.getenv("BORIS_POLICY_SELF_HEAL","1")).lower() in {"0","false","off","no"}:
        return {"attempted":False,"restarted":[],"failed":[],"checks":checks}
    restarted=[]; failed=[]
    # RUNTIME_HEAL_ROLLING_V1: API replicas are a single HA generation. Never
    # restart them independently here: use the canonical drain/health/contract
    # rollout, which also takes the KPI runner lock before touching either API.
    backend_stale=list(stale.get("backend_runtime_generation") or [])
    # RUNTIME_HEAL_BACKEND_DELEGATED_TO_DEPLOY_V1: contract never rolls API replicas.
    # The single canonical owner is boris-deploy.service; contract only reports drift.
    if backend_stale:
        backend_stale = []
    if backend_stale:
        # RUNTIME_HEAL_DEPLOY_LOCK_BACKOFF_V1: parallel autonomous work can hold
        # the canonical rolling lock for a legitimate deployment. A single rc=19
        # must not turn every account red. Retry the *deployment*, never any paid
        # business operation, with a bounded backoff. rc=33 means source changed
        # throughout the three-pass rollout and is equally safe to retry later.
        _backend_healed=False
        _permission_denied=False
        for _heal_attempt in range(1, 5):
            try:
                p=subprocess.run([str(ROOT/"backend/rolling_restart_backend.sh")],capture_output=True,text=True,timeout=240,env={**os.environ,"BORIS_ROLLING_LANE":"runtime_selfheal_v1"})
                if p.returncode==0:
                    restarted.extend(backend_stale)
                    _backend_healed=True
                    break
                denied=((p.stderr or "")+"\n"+(p.stdout or "")).lower()
                if "authentication" in denied or "access denied" in denied or "permission" in denied:
                    _permission_denied=True
                    break
                if p.returncode in (19,33) and _heal_attempt < 4:
                    time.sleep(3.0 * _heal_attempt)
                    continue
                break
            except subprocess.TimeoutExpired:
                if _heal_attempt < 4:
                    time.sleep(3.0 * _heal_attempt)
                    continue
                break
            except Exception:
                break
        if not _backend_healed and not _permission_denied:
            # RUNTIME_HEAL_POSTLOCK_RECHECK_V1: rc=19 may only mean another
            # legitimate rolling release owned the lock. Before turning the whole
            # fleet red, re-read runtime generation: if that external rollout has
            # already converged, there is no runtime failure to report.
            _post_lock_checks, _post_lock_stale = _runtime_generation_state()
            _still_backend_stale = list(_post_lock_stale.get("backend_runtime_generation") or [])
            if _still_backend_stale:
                failed.extend(_still_backend_stale)
    # Non-HA workers may still be restarted individually when stale.
    ordered=[]
    for key in ("background_runtime_generation","crm_runtime_generation","telephony_runtime_generation","deploy_runtime_generation"):
        ordered.extend(stale.get(key) or [])
    seen=set()
    for unit in ordered:
        if unit in seen: continue
        seen.add(unit)
        try:
            p=subprocess.run(["systemctl","restart",unit],capture_output=True,text=True,timeout=20)
            if p.returncode!=0:
                # The production contract also runs under restricted service/users
                # that may inspect but not restart systemd. Permission denial is
                # observability, not proof that a healthy runtime failed. Keep the
                # generation check red, but do not mislabel the unit as restart-failed.
                denied=(p.stderr or "").lower()
                if "authentication" in denied or "access denied" in denied or "permission" in denied:
                    continue
                failed.append(unit); continue
            time.sleep(1.0)
            a=subprocess.run(["systemctl","is-active",unit],capture_output=True,text=True,timeout=5)
            if a.returncode==0 and a.stdout.strip()=="active": restarted.append(unit)
            else: failed.append(unit)
        except Exception:
            failed.append(unit)
    # RUNTIME_HEAL_SETTLE_RECHECK_V1: systemd may acknowledge a rolling restart
    # just before ActiveEnterTimestampMonotonic becomes visible to the next read.
    # Recheck for a few bounded seconds so the contract does not report a false
    # red generation while its own successful heal is still settling.
    post={}
    for _settle in range(6):
        post,post_stale=_runtime_generation_state()
        if not post_stale:
            break
        if _settle < 5:
            time.sleep(1.0)
    return {"attempted":True,"restarted":restarted,"failed":failed,"checks":post}


def _feed_factory_logic_checks() -> dict:
    """Pure E2E invariants: exact quantity expansion and copy gate, no DB writes or paid calls."""
    try:
        from types import SimpleNamespace
        from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody, _ff_copy_quality_errors
        import app.api.campaigns as C
        original=C._ff_category_candidates
        original_ctx=C._ff_account_context
        C._ff_category_candidates=lambda db,direction,limit=8:[{"template_id":"qa-template","path":"Услуги > QA"}]
        C._ff_account_context=lambda db,account_id:{}
        try:
            campaign=SimpleNamespace(default_account_id="qa_contract")
            body=FFWorkflowBriefBody(brief="Ремонт фар",directions=["Ремонт фар"],cities=["Москва"],variants=1,quantity=37)
            plan=_ff_extract_plan(None,campaign,body)
        finally:
            C._ff_category_candidates=original
            C._ff_account_context=original_ctx
        exact=(int(plan.get("quantity") or 0)==37 and len(plan.get("matrix") or [])==37 and len({int(x.get("variant") or 0) for x in plan.get("matrix") or []})==37)
        bad={"copy_quality_contract":"sales_v2","title":"Ремонт фар","description":"Коротко. Напишите.","city":"Москва","direction":"Ремонт фар"}
        good={"copy_quality_contract":"sales_v2","title":"Ремонт фар в Москве","city":"Москва","direction":"Ремонт фар","description":"Ремонт фар · Москва — напишите, чтобы начать с вашей задачи.\n\nСначала уточним исходные данные и то, какой результат для вас важен. Разберём запрос по существу и зафиксируем параметры, которые влияют на дальнейший выбор.\n\nДальше сопоставим вводные с доступным вариантом решения. В тексте используются только подтверждённые сведения без выдуманных сроков, гарантий, опыта или характеристик, которых нет в исходных данных.\n\nДо решения можно задать вопросы по важным для вас условиям. Если часть информации пока неизвестна, начните с того, что уже есть: дополнительные детали можно уточнить последовательно.\n\nНапишите в сообщения Avito и опишите задачу — начнём с конкретных вводных и следующего понятного шага."}
        return {"feed_quantity_e2e":bool(exact),"copy_quality_negative_gate":bool(_ff_copy_quality_errors(bad)),"copy_quality_positive_gate":not bool(_ff_copy_quality_errors(good))}
    except Exception:
        return {"feed_quantity_e2e":False,"copy_quality_negative_gate":False,"copy_quality_positive_gate":False}


def _runtime_shared_checks(db) -> dict:
    checks = {}
    real_chat_gap = db.execute(text("""
        SELECT COUNT(*)
          FROM messenger_leads l
         WHERE EXISTS (
             SELECT 1 FROM messenger_messages m
              WHERE m.account_id=l.account_id AND m.avito_chat_id=l.avito_chat_id
                AND lower(coalesce(m.direction,'')) IN ('in','incoming')
                AND coalesce(m.text,'') NOT LIKE '[Системное сообщение]%'
         )
           AND NOT EXISTS (
             SELECT 1 FROM boris_crm_deals d
              WHERE d.avito_account_id=l.account_id AND d.avito_chat_id=l.avito_chat_id
         )
    """)).scalar() or 0
    real_call_gap = db.execute(text("""
        SELECT COUNT(*)
          FROM boris_crm_activities a
         WHERE a.activity_type='call' AND a.direction='in'
           AND coalesce(a.source,'') IN ('avito_calltracking','boris_telephony')
           AND a.deal_id IS NULL
           AND coalesce(a.metadata_json->>'source','') <> 'qa'
           AND coalesce(a.source_ref,'') NOT LIKE '%qa_%'
    """)).scalar() or 0
    checks["crm_real_inbound_chat_gap_zero"] = int(real_chat_gap) == 0
    checks["crm_real_inbound_call_gap_zero"] = int(real_call_gap) == 0

    # MOP_TRAINING_QA_RULE_LEAK_GUARD_V1:
    # Synthetic sparring/QA instructions must never remain as confirmed
    # account rules after a production QA run. A leaked QA rule silently
    # changes real client replies, so make it a live DB production invariant.
    mop_training_qa_rule_leaks = db.execute(text("""
        SELECT COUNT(*)
          FROM client_facts
         WHERE category='rule'
           AND coalesce(source_ref,'') LIKE 'mop_sparring:%'
           AND value LIKE 'Правило для МОПа: QA:%'
    """)).scalar() or 0
    checks["mop_training_qa_rule_leaks_zero"] = int(mop_training_qa_rule_leaks) == 0
    # CLIENT_ARTIFACT_TENANT_SCOPE_V1: the historical Vasily Camry pack is a
    # uniquely attributable client artifact. It must never be active under any
    # other account; superseded rows are retained only under the canonical client.
    vasily_pack_cross = db.execute(text("""
        SELECT COUNT(*) FROM campaign_items
         WHERE source='pack:vasily_camry' AND status<>'superseded'
           AND account_id<>'vasyailchenko_82650'
    """)).scalar() or 0
    checks["client_artifact_vasily_pack_tenant_scope"] = int(vasily_pack_cross) == 0
    # Command Center DB last-line guard must exist in the live database, not
    # merely as a migration file in source.
    command_guard = db.execute(text(
        "select count(*) from pg_trigger where tgrelid='boris_commands'::regclass"
        " and tgname='trg_boris_command_terminal_status' and not tgisinternal"
    )).scalar() or 0
    checks["command_center_terminal_db_guard_live"] = int(command_guard) == 1
    storage_core_guard = db.execute(text(
        "select count(*) from pg_indexes where schemaname='public' and tablename='storage' "
        "and indexname='uq_storage_core_singletons' and indexdef ilike '%UNIQUE INDEX%'"
    )).scalar() or 0
    checks["storage_core_singleton_db_guard_live"] = int(storage_core_guard) == 1
    # The lifecycle reconciler must actually be scheduled, otherwise stale
    # confirmation/executing rows can accumulate forever despite correct code.
    import subprocess as _sp
    try:
        _en=_sp.run(["systemctl","is-enabled","boris-command-center-maintenance.timer"],capture_output=True,text=True,timeout=5)
        _ac=_sp.run(["systemctl","is-active","boris-command-center-maintenance.timer"],capture_output=True,text=True,timeout=5)
        checks["command_center_maintenance_timer_live"] = (_en.stdout.strip()=="enabled" and _ac.stdout.strip()=="active")
    except Exception:
        checks["command_center_maintenance_timer_live"] = False
    live_zombies = db.execute(text(
        "select count(*) from boris_commands where "
        " (status='awaiting_confirm' and coalesce(expires_at,created_at+interval '15 minutes')<=now())"
        " or (status='executing' and coalesce(confirmed_at,created_at)<now()-interval '10 minutes')"
        " or (status in ('parsed','confirmed') and created_at<now()-interval '10 minutes')"
    )).scalar() or 0
    checks["command_center_stale_live_rows_zero"] = int(live_zombies) == 0

    # MARKETER_STALE_RUNTIME_LIVE_CONTRACT_V1: owner-facing runtime may never
    # claim CPX money authority when canonical marketer entitlement is not
    # active. This is a live DB invariant, not merely a source-marker check.
    try:
        from app.services.control_plane_adapters_ext import marketing_service_entitlement as _marketing_entitlement_pc
        stale_runtime_authority=[]
        runtime_rows=db.query(Storage).filter(Storage.key=="virtual_marketer_runtime").all()
        for runtime_row in runtime_rows:
            try:
                runtime_data=json.loads(runtime_row.value or "{}")
            except Exception:
                continue
            if not isinstance(runtime_data,dict):
                continue
            runtime_ops={str(x) for x in (runtime_data.get("allowed_operations") or [])}
            claims=bool(runtime_data.get("mandate_active")) or bool(
                runtime_ops & {"cpx.raise_bid","cpx.lower_bid"}
            )
            if not claims:
                continue
            entitlement=_marketing_entitlement_pc(db,str(runtime_row.account_id)) or {}
            if entitlement.get("state") != "active":
                stale_runtime_authority.append(str(runtime_row.account_id))
        checks["marketer_stale_runtime_authority_zero"] = not bool(stale_runtime_authority)
    except Exception:
        checks["marketer_stale_runtime_authority_zero"] = False

    # PROSPECTING_LIVE_SOURCE_COVERAGE_20_V1: source-code markers are not enough.
    # A production PASS requires the currently loaded Lead Radar configuration
    # to expose every declared source group without network calls.
    try:
        from app.services.lead_radar.owner_scope import configured_owner as _lr_owner_pc
        from app.services.lead_radar.registry import build_connectors as _lr_build_pc
        from app.services.lead_radar.coverage import source_coverage as _lr_cov_pc
        _lr_cov=_lr_cov_pc(_lr_build_pc(_lr_owner_pc()))
        checks["prospecting_live_source_coverage_20_20"] = bool(
            _lr_cov.get("coverage_ready")
            and int(_lr_cov.get("configured_groups") or 0)==int(_lr_cov.get("target_groups") or 0)==20
            and not (_lr_cov.get("missing_group_keys") or [])
            and not (_lr_cov.get("unmapped_connector_keys") or [])
        )
    except Exception:
        checks["prospecting_live_source_coverage_20_20"] = False
    return checks


def _json_value(row):
    try:
        value = json.loads(row.value or "{}") if row else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _reconcile_account_state(db, account_id: str, kpi: dict) -> tuple[list[str],list[str]]:
    """Repair only safety-positive account drift; never enable products or raise spend."""
    changes=[]; issues=[]
    # Existing MOP sales settings must use the global CRM capture invariant.
    # If there is no settings row, application defaults already use first_inquiry,
    # so do not create configuration that could accidentally toggle other options.
    srow=(db.query(Storage).filter(Storage.account_id==account_id,Storage.key=='mop_crm_sales_settings')
          .order_by(Storage.id.desc()).first())
    if srow:
        try:
            data=json.loads(srow.value or '{}')
            if not isinstance(data,dict): raise ValueError('not_object')
            if str(data.get('deal_trigger') or 'first_inquiry')!='first_inquiry':
                data['deal_trigger']='first_inquiry'
                srow.value=json.dumps(data,ensure_ascii=False)
                changes.append('crm_deal_trigger_first_inquiry')
            # An explicitly enabled MOP without its canonical binding is a
            # silent sales outage: inbound messages are stored/CRM-captured,
            # but `_mop_incoming()` deliberately refuses AI generation. Detect
            # this drift, but never auto-enable a product here. Entitlement
            # alone is not proof of owner intent.
            mop_explicit = bool(data.get('mop_enabled', data.get('enabled', False)))
            if mop_explicit:
                bound = db.execute(text(
                    "SELECT 1 FROM ai_bindings WHERE product='mop' AND account_id=:a LIMIT 1"
                ), {'a':account_id}).first()
                if not bound:
                    issues.append('mop_enabled_without_binding')
        except Exception:
            issues.append('mop_sales_settings_invalid_json')
    # Money reconciliation is monotonic-safe: the contract may only REDUCE an
    # already granted ceiling to the account's stricter KPI setting. It never
    # creates a mandate and never increases bid/budget authority.
    try:
        cap=float(kpi.get('hard_max_bid_rub') or 0)
    except Exception: cap=0.0
    try:
        daily=float(kpi.get('daily_budget_limit_rub') or 0)
    except Exception: daily=0.0
    if cap>0:
        res=db.execute(text("""
            UPDATE money_mandates
               SET max_bid_rub=:cap
             WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
               AND (valid_until IS NULL OR valid_until>now())
               AND 'cpx.raise_bid'=ANY(allowed_operations)
               AND max_bid_rub IS NOT NULL AND max_bid_rub>:cap
        """),{'a':account_id,'cap':cap})
        if int(res.rowcount or 0)>0: changes.append('money_max_bid_lowered')
    if daily>0:
        res=db.execute(text("""
            UPDATE money_mandates
               SET daily_budget_rub=:lim
             WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
               AND (valid_until IS NULL OR valid_until>now())
               AND daily_budget_rub IS NOT NULL AND daily_budget_rub>:lim
        """),{'a':account_id,'lim':daily})
        if int(res.rowcount or 0)>0: changes.append('money_daily_budget_lowered')
    else:
        # Safety-positive reconciliation: historical paid_tariff_kpi mandates
        # must not retain an inferred raise budget after explicit-budget policy.
        res=db.execute(text("""
            UPDATE money_mandates
               SET allowed_operations=array_remove(allowed_operations,'cpx.raise_bid'),
                   daily_budget_rub=NULL,
                   confirmation_version='expbud_v1',
                   version=version+1
             WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
               AND source='paid_tariff_kpi'
               AND 'cpx.raise_bid'=ANY(allowed_operations)
        """),{'a':account_id})
        if int(res.rowcount or 0)>0: changes.append('money_raise_disabled_missing_explicit_budget')
        # ZERO_BUDGET_ALL_RAISES_REVOKED_V3: explicit zero budget disables every
        # autonomous raise lane, including new_feed_launch. First activation is a
        # paid action and therefore never bypasses the account daily budget.
        res=db.execute(text("""
            UPDATE money_mandates
               SET status='revoked', revoked_at=now(), version=version+1
             WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
               AND 'cpx.raise_bid'=ANY(allowed_operations)
        """),{'a':account_id})
        if int(res.rowcount or 0)>0: changes.append('all_raise_authority_revoked_zero_budget')
    return changes,issues



# PRODUCTION_CONTRACT_LIVE_HEALTH_HARD_GATE_V1: a fleet-wide PASS requires
# both local API replicas and public health to be live at certification time.
# PRODUCTION_CONTRACT_CANONICAL_ROLL_DETECT_V1: only the canonical rolling
# process may make a one-replica health gap a convergence state.
def _canonical_backend_roll_active() -> bool:
    import subprocess
    try:
        p=subprocess.run(["pgrep","-f","^/bin/bash /root/BORIS/backend/rolling_restart_backend.sh$"],capture_output=True,text=True,timeout=3)
        return p.returncode == 0 and bool((p.stdout or "").strip())
    except Exception:
        return False


def _live_runtime_health_state() -> dict:
    import urllib.request
    out={}
    for key,url in (("api1","http://127.0.0.1:8000/health"),("api2","http://127.0.0.1:8001/health"),("public","https://boris-ai.pro/health")):
        try:
            with urllib.request.urlopen(url,timeout=4.0) as r:
                code=int(getattr(r,"status",0) or 0)
            out[key]={"ok":200 <= code < 300,"status":code}
        except Exception as exc:
            code=getattr(exc,"code",None)
            out[key]={"ok":False,"status":int(code) if isinstance(code,int) else None,"error_type":type(exc).__name__}
    out["all_ok"]=all(bool((out.get(k) or {}).get("ok")) for k in ("api1","api2","public"))
    return out

def main():
    # CONTRACT_SOURCE_STABILITY_V1: never stamp every client red from a source
    # generation that changed while this contract was still evaluating. A live
    # deploy/edit is a convergence state, not proof that 19 client contours broke.
    source_generation_start = _backend_runtime_content_fingerprint()
    checks = _source_checks()
    checks.update(_feed_factory_logic_checks())
    generation_fingerprint=_generation_fingerprint()
    runtime_heal = _self_heal_runtime_generation()
    checks.update(runtime_heal.get("checks") or {})
    checks["runtime_self_heal_ok"] = not bool(runtime_heal.get("failed"))
    db = SessionLocal()
    failures = []
    try:
        checks.update(_runtime_shared_checks(db))
        # Rollout scope is the UNION of every currently connected/paid account
        # plus any account that is still operationally configured in BORIS.
        # This prevents a new client from silently missing a global correction
        # merely because one registry/table was not populated yet.
        accounts = [r[0] for r in db.execute(text("""
            WITH configured AS (
                SELECT account_id FROM account_slots WHERE status='connected'
                UNION SELECT account_id FROM ai_bindings WHERE account_id IS NOT NULL
                UNION SELECT account_id FROM mop_modes WHERE account_id IS NOT NULL
                UNION SELECT account_id FROM storage WHERE key IN ('kpi_settings','virtual_marketer_runtime','mop_crm_sales_settings')
                UNION SELECT default_account_id AS account_id FROM campaigns WHERE default_account_id IS NOT NULL
            )
            SELECT DISTINCT a.account_id
              FROM accounts a
             WHERE a.account_id IS NOT NULL AND btrim(a.account_id)<>''
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','background')
               )
               AND (
                    EXISTS (SELECT 1 FROM configured c WHERE c.account_id=a.account_id)
                    OR NULLIF(btrim(coalesce(a.avito_client_id,'')),'') IS NOT NULL
                    OR a.avito_user_id IS NOT NULL
                    OR NULLIF(btrim(coalesce(a.avito_login,'')),'') IS NOT NULL
               )
             ORDER BY a.account_id
        """))]
        # Global rollout means every real client account, not QA/demo fixtures.
        # Explicitly retired accounts stay out even if stale credentials/config rows remain.
        accounts=sorted({a for a in accounts if _is_real_rollout_account(a)})
        if POLICY_REQUIRED_ACCOUNTS:
            accounts=sorted(set(accounts) | {a for a in POLICY_REQUIRED_ACCOUNTS if _is_real_rollout_account(a)})
        # Freeze an auditable discovery snapshot. A global correction is only
        # green if every real account discovered at the end of the run is also
        # present in this stamped scope. This closes the race where a client is
        # connected while rollout is already iterating older accounts.
        discovery_started_accounts=set(accounts)
        marketer_scope_ok = True
        marketer_accounts = set()
        marketer_scope_error = ""
        from kpi_goal_runner import get_goal_auto_accounts
        # Release the contract session while billing discovery opens its own
        # short sessions. Discovery is retried because a brief pool handoff must
        # not turn a complete global rollout into a false fleet-wide failure.
        db.close()
        import time as _time
        for _attempt in range(3):
            try:
                marketer_accounts = {str(x.get("account_id")) for x in get_goal_auto_accounts()}
                marketer_scope_error = ""
                break
            except Exception as exc:
                marketer_scope_error = f"{type(exc).__name__}:{exc}"[:300]
                if _attempt < 2:
                    _time.sleep(0.35 * (_attempt + 1))
        db = SessionLocal()
        if marketer_scope_error:
            marketer_scope_ok = False
            checks["marketer_scope_resolved"] = False
        else:
            checks["marketer_scope_resolved"] = True
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        live_health=_live_runtime_health_state()
        checks["live_api1_health"]=bool((live_health.get("api1") or {}).get("ok"))
        checks["live_api2_health"]=bool((live_health.get("api2") or {}).get("ok"))
        checks["live_public_health"]=bool((live_health.get("public") or {}).get("ok"))
        if not live_health.get("all_ok"):
            db.rollback()
            health_fail=[k for k in ("api1","api2","public") if not bool((live_health.get(k) or {}).get("ok"))]
            public_ok=bool((live_health.get("public") or {}).get("ok"))
            roll_active=_canonical_backend_roll_active()
            is_roll_convergence=public_ok and roll_active and any(x in health_fail for x in ("api1","api2"))
            result={"policy_version":POLICY_VERSION,"generation_fingerprint":generation_fingerprint,"status":"converging" if is_roll_convergence else "fail","checked_at":now,"accounts":len(accounts),"passed_accounts":0,"account_ids":list(accounts),"shared_checks":checks,"live_health":live_health,"runtime_self_heal":{"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},"reconciled_accounts":{},"failures":[["__live_runtime_convergence__" if is_roll_convergence else "__live_runtime_health__",[f"unhealthy:{x}" for x in health_fail]]]}
            print(json.dumps(result,ensure_ascii=False))
            return 75 if is_roll_convergence else 1
        # BACKEND_DEPLOY_CONVERGENCE_GRACE_V1: fresh backend drift while the canonical
        # deploy owner is alive is convergence, not proof that every client broke.
        if checks.get("backend_runtime_generation") is False:
            import subprocess as _sp_conv, time as _time_conv
            try:
                _deploy_active = (_sp_conv.run(["systemctl","is-active","--quiet","boris-deploy.service"],timeout=3).returncode == 0)
            except Exception:
                _deploy_active = False
            try:
                _latest_backend_change=max((max(x.stat().st_mtime,x.stat().st_ctime) for x in BACKEND_RUNTIME_PATHS if x.exists()),default=0.0)
                _backend_drift_age=max(0.0,_time_conv.time()-_latest_backend_change) if _latest_backend_change else 10**9
            except Exception:
                _backend_drift_age=10**9
            if _deploy_active and _backend_drift_age <= 300.0:
                db.rollback()
                transient={"policy_version":POLICY_VERSION,"generation_fingerprint":generation_fingerprint,"status":"converging","checked_at":now,"accounts":len(accounts),"passed_accounts":0,"account_ids":list(accounts),"shared_checks":checks,"runtime_self_heal":{"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},"reconciled_accounts":{},"convergence":{"owner":"boris-deploy.service","backend_drift_age_sec":round(_backend_drift_age,3)},"failures":[["__runtime_convergence__",["backend_generation_waiting_canonical_deploy"]]]}
                print(json.dumps(transient,ensure_ascii=False))
                return 75
        # Re-check immediately before account policy rows are touched. If backend
        # bytes changed during discovery/runtime QA, leave the previous known-good
        # per-account policy state intact and ask the canonical deploy owner to
        # converge the new generation first.
        source_generation_pre_stamp = _backend_runtime_content_fingerprint()
        if source_generation_pre_stamp != source_generation_start:
            db.rollback()
            transient={
                "policy_version": POLICY_VERSION,
                "generation_fingerprint": generation_fingerprint,
                "status": "converging",
                "checked_at": now,
                "accounts": len(accounts),
                "passed_accounts": 0,
                "account_ids": list(accounts),
                "shared_checks": checks,
                "runtime_self_heal": {"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},
                "reconciled_accounts": {},
                "failures": [["__source_generation__",["backend_source_changed_during_contract"]]],
            }
            print(json.dumps(transient,ensure_ascii=False))
            return 75
        reconcile_events={}
        for acc in accounts:
            issues = [k for k, v in checks.items() if not v]
            krow = db.query(Storage).filter(Storage.account_id==acc, Storage.key=='kpi_settings').first()
            kpi = _json_value(krow)
            reconciled,reconcile_issues=_reconcile_account_state(db,acc,kpi)
            issues.extend(reconcile_issues)
            if reconciled:
                reconcile_events[acc]=list(reconciled)
            target = float(kpi.get('target_leads_per_day') or 0)
            daily_limit = float(kpi.get('daily_budget_limit_rub') or 0)
            configured_bid_cap = float(kpi.get('hard_max_bid_rub') or 0)
            if target > 0 and acc in marketer_accounts:
                # A positive KPI without a daily money ceiling is fail-closed
                # by the money guard. This is an account configuration state,
                # not a failed global rollout, so keep it observable separately.
                if daily_limit <= 0:
                    pass
                rtrow = db.query(Storage).filter(Storage.account_id==acc, Storage.key=='virtual_marketer_runtime').first()
                if not rtrow:
                    issues.append('marketer_runtime_missing')
                statsrow = db.query(Storage).filter(
                    Storage.account_id==acc,
                    Storage.key==f"daily_stats:{datetime.date.today().isoformat()}"
                ).first()
                stats = _json_value(statsrow)
                spending = stats.get('spending') if isinstance(stats.get('spending'), dict) else {}
                raw_spent = spending.get('all_spend_rub')
                if raw_spent is None:
                    raw_spent = stats.get('spent_rub') or stats.get('spent_today_rub') or stats.get('spent')
                try:
                    spent = float(raw_spent) if raw_spent is not None else None
                except Exception:
                    spent = None
                # Overspend is an operational incident, not proof that rollout
                # failed. The money engine is fail-closed at >=90%; record the
                # condition in account policy state without turning global code
                # deployment red after historical spend already occurred.
                budget_state = ('overrun' if daily_limit > 0 and spent is not None and spent > daily_limit * 1.02 else
                                'near_limit' if daily_limit > 0 and spent is not None and spent >= daily_limit * 0.90 else
                                'ok' if spent is not None else 'unknown')
                if daily_limit <= 0:
                    # Zero/missing explicit daily budget is healthy only when
                    # automatic paid_tariff_kpi raise authority is absent.
                    auto_raise = db.execute(text("""
                        SELECT 1 FROM money_mandates
                         WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                           AND (valid_until IS NULL OR valid_until>now())
                           AND source='paid_tariff_kpi'
                           AND 'cpx.raise_bid'=ANY(allowed_operations)
                         LIMIT 1
                    """), {'a':acc}).first()
                    if auto_raise:
                        issues.append('marketer_missing_budget_not_fail_closed')
                elif configured_bid_cap > 0:
                    mandate = db.execute(text("""
                        SELECT max_bid_rub
                          FROM money_mandates
                         WHERE :a=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                           AND (valid_until IS NULL OR valid_until>now())
                           AND 'cpx.raise_bid'=ANY(allowed_operations)
                         ORDER BY id DESC LIMIT 1
                    """), {'a': acc}).scalar()
                    # No active raise mandate is a safe fail-closed state for
                    # expired/unentitled tenants; cap equality matters only when
                    # autonomous raise authority actually exists.
                    if mandate is not None and float(mandate) > configured_bid_cap + 1e-9:
                        issues.append('marketer_bid_cap_mismatch')
            payload = {
                "policy_version": POLICY_VERSION,
                "generation_fingerprint": generation_fingerprint,
                "checked_at": now,
                "status": "pass" if not issues else "fail",
                "issues": sorted(set(issues)),
                "shared_checks": checks,
                "runtime_self_heal": {"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},
                "reconciled": reconciled,
                "marketer_budget_state": (budget_state if target > 0 and acc in marketer_accounts else None),
                "marketer_spent_today_rub": (spent if target > 0 and acc in marketer_accounts else None),
                "marketer_daily_limit_rub": (daily_limit if target > 0 and acc in marketer_accounts else None),
            }
            row = db.query(Storage).filter(Storage.account_id==acc, Storage.key=='global_policy_state').first()
            if row:
                row.value = json.dumps(payload, ensure_ascii=False)
            else:
                db.add(Storage(account_id=acc, key='global_policy_state', value=json.dumps(payload, ensure_ascii=False)))
            if issues:
                failures.append((acc, sorted(set(issues))))
        # CONTRACT_SOURCE_STABILITY_POSTSTAMP_V1: all policy-state updates above
        # are still in this transaction. If source changed while accounts were
        # evaluated, roll them back atomically rather than publishing a mixed
        # generation as authoritative fleet truth.
        source_generation_pre_commit = _backend_runtime_content_fingerprint()
        if source_generation_pre_commit != source_generation_start:
            db.rollback()
            transient={
                "policy_version": POLICY_VERSION,
                "generation_fingerprint": generation_fingerprint,
                "status": "converging",
                "checked_at": now,
                "accounts": len(accounts),
                "passed_accounts": 0,
                "account_ids": list(accounts),
                "shared_checks": checks,
                "runtime_self_heal": {"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},
                "reconciled_accounts": {},
                "failures": [["__source_generation__",["backend_source_changed_before_contract_commit"]]],
            }
            print(json.dumps(transient,ensure_ascii=False))
            return 75
        # Coverage proof: after commit every account in the rollout scope must
        # have exactly the current policy version and a passing state. Re-discover
        # at the END as well: a newly connected real client must make this run red
        # instead of silently waiting for the next guardian cycle.
        db.commit()
        end_accounts=[r[0] for r in db.execute(text("""
            WITH configured AS (
                SELECT account_id FROM account_slots WHERE status='connected'
                UNION SELECT account_id FROM ai_bindings WHERE account_id IS NOT NULL
                UNION SELECT account_id FROM mop_modes WHERE account_id IS NOT NULL
                UNION SELECT account_id FROM storage WHERE key IN ('kpi_settings','virtual_marketer_runtime','mop_crm_sales_settings')
                UNION SELECT default_account_id AS account_id FROM campaigns WHERE default_account_id IS NOT NULL
            )
            SELECT DISTINCT a.account_id
              FROM accounts a
             WHERE a.account_id IS NOT NULL AND btrim(a.account_id)<>''
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','background')
               )
               AND (
                    EXISTS (SELECT 1 FROM configured c WHERE c.account_id=a.account_id)
                    OR NULLIF(btrim(coalesce(a.avito_client_id,'')),'') IS NOT NULL
                    OR a.avito_user_id IS NOT NULL
                    OR NULLIF(btrim(coalesce(a.avito_login,'')),'') IS NOT NULL
               )
        """))]
        end_accounts={a for a in end_accounts if _is_real_rollout_account(a)}
        newly_discovered=sorted(end_accounts-discovery_started_accounts)
        if newly_discovered:
            failures.append(('__rollout_scope_race__',[f'new_account_during_rollout:{x}' for x in newly_discovered]))
        stamped={str(r[0]):_json_value(r[1]) for r in db.query(Storage.account_id,Storage).filter(Storage.key=='global_policy_state',Storage.account_id.in_(accounts)).all()}
        coverage_missing=[acc for acc in accounts if str((stamped.get(acc) or {}).get('policy_version') or '') != POLICY_VERSION]
        coverage_generation=[acc for acc in accounts if str((stamped.get(acc) or {}).get('generation_fingerprint') or '') != generation_fingerprint]
        coverage_failed=[acc for acc in accounts if str((stamped.get(acc) or {}).get('status') or '') != 'pass']
        if coverage_missing:
            failures.append(('__rollout_coverage__',[f'missing_current_policy:{x}' for x in coverage_missing]))
        if coverage_generation:
            failures.append(('__rollout_generation__',[f'stale_generation:{x}' for x in coverage_generation]))
        if coverage_failed:
            failures.append(('__rollout_status__',[f'not_pass:{x}' for x in coverage_failed]))
        live_health_final=_live_runtime_health_state()
        checks["live_api1_health"]=bool((live_health_final.get("api1") or {}).get("ok"))
        checks["live_api2_health"]=bool((live_health_final.get("api2") or {}).get("ok"))
        checks["live_public_health"]=bool((live_health_final.get("public") or {}).get("ok"))
        if not live_health_final.get("all_ok"):
            health_fail=[k for k in ("api1","api2","public") if not bool((live_health_final.get(k) or {}).get("ok"))]
            failures.append(('__live_runtime_health__',[f'unhealthy:{x}' for x in health_fail]))
        broken_accounts=sorted({str(x[0]) for x in failures if not str(x[0]).startswith('__')})
        result={
            "policy_version": POLICY_VERSION,
            "generation_fingerprint": generation_fingerprint,
            "status": "pass" if not failures else "fail",
            "checked_at": now,
            "accounts": len(accounts),
            "passed_accounts": max(0,len(accounts)-len(broken_accounts)),
            "account_ids": list(accounts),
            "shared_checks": checks,
            "runtime_self_heal": {"attempted":bool(runtime_heal.get("attempted")),"restarted":list(runtime_heal.get("restarted") or []),"failed":list(runtime_heal.get("failed") or [])},
            "reconciled_accounts": reconcile_events,
            "failures": failures,
        }
        # One authoritative rollout registry for Monitoring/Home/Guardian. Keep
        # latest plus one row per policy version; repeated runs update in place.
        summary_json=json.dumps(result,ensure_ascii=False)
        for summary_key in ('global_policy_rollout_latest',f'global_policy_rollout:{POLICY_VERSION}',f'global_policy_generation:{generation_fingerprint[:16]}'):
            sr=(db.query(Storage).filter(Storage.account_id=='__platform__',Storage.key==summary_key)
                .order_by(Storage.id.desc()).first())
            if sr: sr.value=summary_json
            else: db.add(Storage(account_id='__platform__',key=summary_key,value=summary_json))
        db.commit()
        print(summary_json)
        # Owner is contacted only for a real rollout breach. PASS stays silent.
        # Guardian invokes the same authoritative contract with notifications off
        # so one incident cannot generate duplicate alerts from two schedulers.
        if failures and str(__import__('os').getenv("BORIS_POLICY_NOTIFY","1")).lower() not in {"0","false","off","no"}:
            try:
                from app.ext_api.notify import send as _notify_owner
                broken_accounts=[str(x[0]) for x in failures if not str(x[0]).startswith('__')]
                shared_broken=sorted(k for k,v in checks.items() if not v)
                _notify_owner("BORIS: глобальная корректировка применена не полностью.\n"
                              f"Политика: {POLICY_VERSION}\n"
                              f"Проблемных аккаунтов: {len(broken_accounts)} из {len(accounts)}\n"
                              f"Контроль: {', '.join(shared_broken) if shared_broken else 'account-specific'}\n"
                              "BORIS оставил цикл в ошибке до устранения рассинхронизации.")
            except Exception:
                pass
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
