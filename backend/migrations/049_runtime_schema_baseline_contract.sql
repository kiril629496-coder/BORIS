BEGIN;

-- BORIS_RUNTIME_SCHEMA_BASELINE_V1
-- Legacy runtime-created schema becomes migration-owned. This migration
-- is intentionally verification-only: production already contains the
-- baseline; missing objects fail closed instead of being recreated by app code.
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['public.ai_guard_event_log','public.auth_login_email_challenges','public.auth_trusted_devices','public.avito_position_snapshots','public.boris_billing_usage_intents','public.boris_crm_activities','public.boris_crm_audit_log','public.boris_crm_companies','public.boris_crm_connections','public.boris_crm_contacts','public.boris_crm_conversations','public.boris_crm_deals','public.boris_crm_external_entities','public.boris_crm_messages','public.boris_crm_pipelines','public.boris_crm_stages','public.boris_crm_sync_conflicts','public.boris_crm_sync_events','public.boris_crm_sync_outbox','public.boris_crm_tasks','public.calltracking_attributions','public.calltracking_pool_numbers','public.calltracking_pools','public.calltracking_sessions','public.calltracking_sites','public.calltracking_static_bindings','public.calltracking_touchpoints','public.client_config','public.client_mailboxes','public.crm_external_connections','public.crm_external_entities','public.crm_sync_attempts','public.crm_sync_audit','public.crm_sync_conflicts','public.crm_sync_outbox','public.crm_sync_state','public.crm_webhook_events','public.development_lead_shortlist','public.email_open_events','public.email_open_trackers','public.email_tracking_policy','public.ext_order_snapshots','public.factory_attention_events','public.factory_attention_state','public.factory_auto_execution_events','public.factory_auto_execution_state','public.factory_bootstrap_events','public.factory_bootstrap_state','public.factory_execution_events','public.factory_execution_state','public.factory_fact_intake_events','public.factory_fact_intake_state','public.factory_launch_health','public.factory_launch_health_events','public.factory_launch_lifecycle_state','public.factory_missing_fact_events','public.factory_missing_fact_state','public.factory_outcome_bridge_events','public.factory_outcome_bridge_state','public.factory_outcome_events','public.factory_outcome_state','public.factory_recovery_events','public.factory_recovery_state','public.factory_remediation_guard_state','public.factory_remediation_policy','public.factory_remediation_policy_events','public.factory_rule_runtime','public.factory_rule_runtime_slots','public.factory_supervisor_events','public.factory_supervisor_state','public.lead_notification_deliveries','public.lead_notification_max_owner_routes','public.lead_notification_outbox','public.lead_notification_settings','public.lead_push_subscriptions','public.lead_radar_leads','public.mailbox_sent_copy_queue','public.manager_leads','public.max_boris_chat_routes','public.max_boris_reminders','public.monitor_runtime_state','public.mop_account_dialogue_brain','public.mop_knowledge_gaps','public.mop_niche_dialogue_brain','public.owner_daily_report_delivery_slots','public.owner_daily_report_snapshots','public.owner_mailbox_transport_drift_events','public.prospect_campaign_members','public.prospect_campaigns','public.prospect_daily_reports','public.prospect_inbound_replies','public.prospect_mailbox_action_alerts','public.prospect_outreach_bootstrap','public.prospect_owner_daily_limits','public.prospect_repair_state','public.prospect_replenish_runs','public.prospect_reply_alerts','public.prospect_send_reports','public.rop_account_brain','public.rop_global_brain','public.telegram_source_scan_stats','public.telephony_afterhours_sessions','public.telephony_afterhours_settings','public.telephony_ai_suggestions','public.telephony_asterisk_config_state','public.telephony_asterisk_events','public.telephony_asterisk_recordings','public.telephony_asterisk_runtime_health','public.telephony_audit','public.telephony_call_carrier_links','public.telephony_call_quality','public.telephony_call_targets','public.telephony_callback_links','public.telephony_calls','public.telephony_carrier_cdr','public.telephony_carrier_cost_ledger','public.telephony_cdr_reconciliation','public.telephony_coaching_plans','public.telephony_commands','public.telephony_copilot_settings','public.telephony_cost_ledger','public.telephony_devices','public.telephony_dids','public.telephony_entitlements','public.telephony_events','public.telephony_manager_actions','public.telephony_media_sessions','public.telephony_minute_alerts','public.telephony_minute_packages','public.telephony_minute_usage','public.telephony_outbound_intents','public.telephony_provider_configs','public.telephony_push_outbox','public.telephony_recording_settings','public.telephony_recordings','public.telephony_report_deliveries','public.telephony_report_settings','public.telephony_routes','public.telephony_tariff_rates','public.telephony_tariff_versions','public.telephony_transcript_chunks','public.telephony_trunks','public.telephony_voice_agent_postcall','public.telephony_voice_agent_qualification','public.telephony_voice_agent_sessions','public.telephony_voice_agent_settings','public.telephony_voice_agent_turns','public.telephony_webhook_nonces']::text[]) x
   WHERE to_regclass(x) IS NULL;
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing relations: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['public.ix_ai_guard_event_log_account_created','public.ix_auth_login_challenge_user_created','public.ix_auth_trusted_device_live','public.ix_avito_position_account_item_time','public.ix_avito_position_account_time','public.ix_boris_billing_usage_intents_created','public.ix_boris_crm_activities_deal','public.ix_boris_crm_companies_owner','public.ix_boris_crm_connections_owner','public.ix_boris_crm_contacts_avito_chat','public.ix_boris_crm_contacts_email','public.ix_boris_crm_contacts_owner','public.ix_boris_crm_contacts_phone','public.ix_boris_crm_conversations_owner_last','public.ix_boris_crm_deals_avito_chat','public.ix_boris_crm_deals_contact','public.ix_boris_crm_deals_owner','public.ix_boris_crm_deals_stage','public.ix_boris_crm_messages_conversation','public.ix_boris_crm_pipelines_owner','public.ix_boris_crm_stages_pipeline','public.ix_boris_crm_tasks_owner_due','public.ix_calltracking_attribution_source','public.ix_calltracking_pool_available','public.ix_calltracking_sessions_did_window','public.ix_calltracking_sessions_visitor_active','public.ix_calltracking_static_active','public.ix_cc_lookup','public.ix_crm_audit_owner_created','public.ix_crm_external_connections_owner','public.ix_crm_external_entities_lookup','public.ix_crm_sync_attempts_outbox','public.ix_crm_sync_audit_owner_time','public.ix_crm_sync_conflicts_pending','public.ix_crm_sync_outbox_owner','public.ix_crm_sync_outbox_ready','public.ix_crm_webhook_events_provider_status','public.ix_development_lead_shortlist_day','public.ix_email_open_events_tracker_opened','public.ix_email_open_trackers_first_opened','public.ix_factory_bootstrap_events_account','public.ix_factory_bootstrap_owner','public.ix_factory_bootstrap_status','public.ix_factory_fact_intake_status','public.ix_factory_missing_fact_events_account','public.ix_factory_missing_fact_status','public.ix_factory_rule_runtime_slots_day','public.ix_lead_notification_outbox_due','public.ix_lead_push_account_active','public.ix_max_boris_reminders_due','public.ix_mop_knowledge_gaps_account_status','public.ix_owner_daily_report_date','public.ix_pcm_campaign_status','public.ix_pcm_domain','public.ix_prospect_replies_member_human_received','public.ix_rop_global_brain_generated','public.ix_telephony_asterisk_events_channel','public.ix_telephony_asterisk_linkedid','public.ix_telephony_asterisk_recordings_call','public.ix_telephony_dids_account','public.ix_telephony_entitlements_active','public.ix_telephony_manager_actions_history','public.ix_telephony_media_call','public.ix_telephony_media_due','public.ix_telephony_outbound_intents_reconcile','public.ix_telephony_outbound_intents_status','public.ix_telephony_tariff_rates_lookup','public.uq_boris_crm_deals_lead_radar_owner_source_ref','public.uq_cc_active','public.uq_cc_collector_hash','public.uq_cc_version','public.ux_pcm_campaign_email_active','public.ux_prospect_suppression_kind_value','public.ux_telephony_calls_provider_identity','public.ux_telephony_carrier_call_id','public.ux_telephony_commands_idempotency','public.ux_telephony_dids_active_number','public.ux_telephony_events_provider_identity','public.ux_telephony_manager_actions_one_open','public.ux_telephony_media_endpoint','public.ux_telephony_push_active_intent','public.ux_telephony_recordings_provider_identity']::text[]) x
   WHERE to_regclass(x) IS NULL;
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing indexes: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','created_at','account_id','source','operation','event_code','status','user_message','intent_key','details_json']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='ai_guard_event_log' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on ai_guard_event_log: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['challenge_id','user_id','email','code_hash','device_hash','expires_at','attempts','max_attempts','used_at','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='auth_login_email_challenges' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on auth_login_email_challenges: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','user_id','device_hash','user_agent','trusted_until','last_seen_at','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='auth_trusted_devices' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on auth_trusted_devices: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','item_id','title','position','delta','trend','visibility','views','contacts','conversion','source','gateway_job_id','measured_at','raw_excerpt']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='avito_position_snapshots' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on avito_position_snapshots: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','unit','idempotency_key','amount','result_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_billing_usage_intents' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_billing_usage_intents: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','deal_id','contact_id','activity_type','channel','direction','title','body','source','source_ref','actor_type','actor_id','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_activities' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_activities: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','actor_type','actor_id','action','entity_type','entity_id','before_json','after_json','reason','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_audit_log' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_audit_log: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','name','phone','email','website','source','source_ref','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_companies' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_companies: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','mode','status','external_account_id','external_domain','settings_json','created_at','updated_at','webhook_secret','token_expires_at','last_sync_at','last_error','external_writes_enabled']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_connections' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_connections: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','display_name','first_name','last_name','primary_phone','primary_email','company_name','source','source_ref','avito_user_id','avito_chat_id','telegram_id','vk_id','status','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_contacts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_contacts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','contact_id','deal_id','channel','account_id','external_chat_id','title','unread_count','last_message_at','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_conversations' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_conversations: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','contact_id','company_id','pipeline_id','stage_id','title','amount_kopeks','currency','source','source_ref','avito_account_id','avito_item_id','avito_chat_id','responsible_user_id','status','next_action_at','last_activity_at','lost_reason','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_deals' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_deals: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','entity_type','boris_entity_id','external_entity_id','external_version','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_external_entities' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_external_entities: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','conversation_id','external_message_id','direction','sender_type','body','sent_at','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_messages' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_messages: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','name','code','is_default','is_active','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_pipelines' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_pipelines: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','pipeline_id','name','code','position','semantic_type','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_stages' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_stages: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','entity_type','entity_id','field_name','boris_value','external_value','resolution','status','created_at','resolved_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_sync_conflicts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_sync_conflicts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','external_event_id','event_type','entity_type','external_entity_id','payload_json','status','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_sync_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_sync_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','event_id','idempotency_key','entity_type','entity_id','operation','payload_json','status','attempts','next_attempt_at','created_at','completed_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_sync_outbox' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_sync_outbox: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','deal_id','contact_id','title','description','assigned_user_id','due_at','status','source','created_at','completed_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='boris_crm_tasks' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on boris_crm_tasks: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','did_id','session_id','static_binding_id','attribution_mode','confidence','source','source_ref','first_touch_json','last_touch_json','evidence_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_attributions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_attributions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['pool_id','did_id','status','current_session_id','leased_until','cooldown_until','last_assigned_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_pool_numbers' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_pool_numbers: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','name','default_did_id','lease_ttl_sec','cooloff_sec','enabled','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_pools' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_pools: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','site_id','account_id','visitor_id','browser_session_id','state','first_seen_at','last_seen_at','landing_url','referrer','utm_source','utm_medium','utm_campaign','utm_content','utm_term','yclid','gclid','vk_click_id','assigned_did_id','allocation_mode','expires_at','released_at','attribution_until','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_sessions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_sessions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','name','public_key','allowed_domains','default_pool_id','enabled','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_sites' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_sites: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','did_id','source','source_ref','campaign_id','ad_id','location','active_from','active_to','enabled','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_static_bindings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_static_bindings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','session_id','occurred_at','channel','campaign','referrer','landing_url','metadata_json']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='calltracking_touchpoints' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on calltracking_touchpoints: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','billing_owner_kind','billing_owner_id','account_id','version','status','schema_version','source','config','content_hash','validation_status','validation_report','validated_at','created_at','created_by_kind','created_by_ref','activated_at','notes']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='client_config' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on client_config: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','account_id','email_address','display_name','smtp_host','smtp_port','smtp_ssl','imap_host','imap_port','imap_ssl','username','secret_encrypted','status','last_imap_uid','last_checked_at','last_error','created_at','updated_at','smtp_last_checked_at','smtp_last_error','imap_last_checked_at','imap_last_error']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='client_mailboxes' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on client_mailboxes: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','status','external_account_id','external_account_name','token_ref','scopes_json','metadata_json','last_sync_at','last_error','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_external_connections' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_external_connections: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','entity_type','boris_entity_id','external_entity_id','external_parent_id','external_url','payload_hash','last_external_updated_at','last_synced_at','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_external_entities' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_external_entities: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','outbox_id','attempt_no','status','http_status','response_hash','error_code','error_message','started_at','finished_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_sync_attempts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_sync_attempts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','action','entity_type','boris_entity_id','external_entity_id','origin','before_json','after_json','reason','correlation_id','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_sync_audit' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_sync_audit: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','entity_type','boris_entity_id','external_entity_id','conflict_type','boris_payload_json','external_payload_json','resolution','resolved_by','resolved_at','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_sync_conflicts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_sync_conflicts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','operation','entity_type','boris_entity_id','external_entity_id','origin','idempotency_key','payload_json','payload_hash','status','attempts','available_at','locked_at','completed_at','last_error','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_sync_outbox' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_sync_outbox: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','provider','entity_type','cursor_value','last_success_at','last_attempt_at','last_error','imported_count','exported_count','metadata_json','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_sync_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_sync_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','provider','external_event_id','event_type','payload_hash','payload_json','status','processed_at','error_message','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='crm_webhook_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on crm_webhook_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','service_date','kind','lead_id','company_id','rank','fit_score','brief_json','contact_email','campaign_id','campaign_member_id','outreach_status','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='development_lead_shortlist' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on development_lead_shortlist: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','tracker_id','opened_at','user_agent','proxy_hint']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='email_open_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on email_open_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','email_queue_id','token','created_at','first_opened_at','last_opened_at','open_count','proxy_hint_count','first_user_agent','last_user_agent']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='email_open_trackers' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on email_open_trackers: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','activated_at','policy_version']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='email_tracking_policy' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on email_tracking_policy: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','tag','order_id','dev_job_id','status','priority','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='ext_order_snapshots' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on ext_order_snapshots: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','attention_key','account_id','before_status','after_status','route','priority','attention_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_attention_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_attention_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['attention_key','account_id','source','issue_type','route','priority','status','title','detail','source_health','source_fingerprint','attention_fingerprint','created_at','updated_at','resolved_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_attention_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_attention_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','attention_key','account_id','issue_type','action_type','before_status','after_status','execution_fingerprint','result','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_auto_execution_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_auto_execution_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['attention_key','account_id','issue_type','action_type','status','source_attention_fingerprint','result','execution_fingerprint','executed_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_auto_execution_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_auto_execution_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','event','from_fingerprint','to_fingerprint','payload','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_bootstrap_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_bootstrap_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','owner_user_id','source_fingerprint','bootstrap_fingerprint','status','action_count','executable_internal','blocked_external','package','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_bootstrap_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_bootstrap_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','field','event','before_value','after_value','source_bootstrap_fingerprint','execution_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_execution_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_execution_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','field','source_bootstrap_fingerprint','target_key','target_value','status','execution_fingerprint','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_execution_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_execution_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','fact_kind','event_type','before_status','after_status','before_value','after_value','source_type','source_ref','actor','confidence','fact_fingerprint','payload','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_fact_intake_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_fact_intake_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','fact_kind','target_field','value','status','source_type','source_ref','actor','confidence','provenance','fact_fingerprint','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_fact_intake_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_fact_intake_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','launch_status','health_status','schedule_required','schedule_present','unresolved_internal','waiting_facts','pending_confirmations','provisioning_status','scheduler_stale','hard_blockers','reasons','health_fingerprint','checked_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_launch_health' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_launch_health: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','before_status','after_status','reasons','health_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_launch_health_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_launch_health_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','status','current_step','steps_total','steps_done','working_now','completed_steps','waiting_reason','owner_action_required','result','next_action','last_error','retry_count','next_retry_at','started_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_launch_lifecycle_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_launch_lifecycle_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','field','event_type','before_status','after_status','fact_kind','source_fact_fingerprint','payload','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_missing_fact_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_missing_fact_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','field','fact_kind','status','reason','question_text','current_value','resolved_value','source_execution_fingerprint','source_fact_fingerprint','evidence','first_seen_at','updated_at','resolved_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_missing_fact_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_missing_fact_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','bridge_key','account_id','issue_type','outcome','bridge_action','before_status','after_status','bridge_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_outcome_bridge_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_outcome_bridge_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['bridge_key','source_attention_key','account_id','issue_type','outcome','bridge_action','status','target_attention_key','source_fingerprint','bridge_fingerprint','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_outcome_bridge_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_outcome_bridge_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','issue_type','outcome','reason','source_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_outcome_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_outcome_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','issue_type','route','attention_status','outcome','reason','recovery_rows','attempts','source_fingerprint','first_seen_at','last_seen_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_outcome_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_outcome_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','attention_key','account_id','issue_type','recovery_status','target_route','reason','fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_recovery_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_recovery_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['attention_key','account_id','issue_type','source_route','recovery_status','target_route','reason','attempts','external_effect','fingerprint','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_recovery_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_recovery_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['attention_key','account_id','issue_type','action_type','route','decision','attempts','first_seen_at','last_attempt_at','next_allowed_at','expires_at','rollback_required','external_effects_allowed','reason','source_attention_fingerprint','guard_fingerprint','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_remediation_guard_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_remediation_guard_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['issue_type','action_type','enabled','max_attempts','cooldown_seconds','ttl_seconds','rollback_required','external_effects_allowed','escalate_after_exhaustion','policy_fingerprint','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_remediation_policy' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_remediation_policy: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','issue_type','action_type','event_type','policy_fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_remediation_policy_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_remediation_policy_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','rule_key','account_id','title','rule_mode','daily_target','timezone','window_start','window_end','executor_key','params','enabled','status','version','content_hash','blocked_reason','last_evaluated_at','last_status','last_error','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_rule_runtime' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_rule_runtime: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','rule_key','local_day','slot_no','status','attempts','execution_key','started_at','finished_at','next_retry_at','result','error_text','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_rule_runtime_slots' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_rule_runtime_slots: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','before_status','after_status','priority','reason','source','fingerprint','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_supervisor_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_supervisor_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','operational_status','priority','reason','source','health_status','open_owner_items','open_auto_items','waiting_facts','active_recovery','escalations','fingerprint','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='factory_supervisor_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on factory_supervisor_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','outbox_id','channel','target_key','status','error','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_notification_deliveries' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_notification_deliveries: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','chat_id','chat_title','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_notification_max_owner_routes' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_notification_max_owner_routes: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','event_key','event_type','severity','title','body','url','payload_json','status','attempts','next_attempt_at','last_error','created_at','sent_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_notification_outbox' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_notification_outbox: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','web_push_enabled','max_enabled','max_chat_id','max_bot_token_enc','email_enabled','email_to','email_mode','created_at','updated_at','max_webhook_secret_hash','max_leadership_chat_id']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_notification_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_notification_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','user_id','endpoint_hash','subscription_enc','user_agent','active','last_success_at','last_error','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_push_subscriptions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_push_subscriptions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_user_id','fingerprint','dedupe_key','source_key','external_id','url','title','body','author','company_name','company_domain','contact_hint','location','published_at','discovered_at','signal_family','service_ids','score','reasons','budget_min_rub','budget_max_rub','urgency','decision_maker_hint','status','contact_mode','contact_reason','reply_text','crm_deal_id','first_contact_at','last_contact_at','next_followup_at','metadata_json','created_at','updated_at','quality_version']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='lead_radar_leads' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on lead_radar_leads: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','mailbox_id','message_id','mime_bytes','status','attempts','last_error','created_at','updated_at','next_attempt_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='mailbox_sent_copy_queue' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on mailbox_sent_copy_queue: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','manager_email','name','surname','position','phone','email','company','site','niche','comment','status','account_id','created_at','updated_at','telegram_peer_id','telegram_username','lead_source']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='manager_leads' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on manager_leads: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['chat_id','transport_account_id','route_type','boris_account_id','chat_title','enabled','created_by_max_user_id','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='max_boris_chat_routes' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on max_boris_chat_routes: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','chat_id','transport_account_id','boris_account_id','deal_id','client_label','reminder_text','due_date','repeat_times','sent_times','status','created_by_max_user_id','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='max_boris_reminders' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on max_boris_reminders: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['key','value_text','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='monitor_runtime_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on monitor_runtime_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','generated_at','message_count','chat_count','payload']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='mop_account_dialogue_brain' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on mop_account_dialogue_brain: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','gap_key','account_id','avito_chat_id','reason','question','status','occurrences','first_seen_at','last_seen_at','last_notified_at','resolved_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='mop_knowledge_gaps' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on mop_knowledge_gaps: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['niche_key','generated_at','account_count','payload']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='mop_niche_dialogue_brain' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on mop_niche_dialogue_brain: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['report_date','slot','chat_id','thread_id','status','message_count','verification_passes','sent_at','error','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='owner_daily_report_delivery_slots' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on owner_daily_report_delivery_slots: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['report_date','account_id','account_name','owner_user_id','payload_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='owner_daily_report_snapshots' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on owner_daily_report_snapshots: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','mailbox_id','attempted_at','actor','application_name','attempted_email','attempted_smtp_host','attempted_imap_host','attempted_username']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='owner_mailbox_transport_drift_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on owner_mailbox_transport_drift_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','campaign_id','company_id','contact_id','email','email_domain','quality_score','status','skip_reason','email_queue_id','queued_at','sent_at','reply_status','replied_at','created_at','updated_at','ab_variant','copy_version']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_campaign_members' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_campaign_members: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','owner_id','name','niche','regions','status','daily_limit','per_domain_daily_limit','min_quality_score','subject_template','body_template','attachment_path','discovered_count','ready_count','created_at','updated_at','activated_at','paused_at','account_id','mailbox_id','ab_variants','copy_revision_status','copy_revision_version','copy_revision_needed_at','desired_status','status_reason','status_source']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_campaigns' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_campaigns: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['report_date','reported_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_daily_reports' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_daily_reports: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','mailbox_id','campaign_id','member_id','message_uid','message_id','in_reply_to','from_email','subject','body_preview','crm_lead_id','alert_status','received_at','created_at','message_kind']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_inbound_replies' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_inbound_replies: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['mailbox_id','error_kind','first_seen_at','notified_at','resolved_at','last_error','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_mailbox_action_alerts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_mailbox_action_alerts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['owner_id','mailbox_id','email_address','state','test_queue_id','test_message_id','last_error','updated_at','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_outreach_bootstrap' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_outreach_bootstrap: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['owner_id','service_date','daily_cap','timezone','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_owner_daily_limits' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_owner_daily_limits: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['campaign_id','company_id','attempts','last_attempt_at','last_success_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_repair_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_repair_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['campaign_id','last_run_at','last_inserted','last_parsed','last_error','updated_at','last_discovery_at','last_repair_at','discovery_cursor','last_search_attempt_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_replenish_runs' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_replenish_runs: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','reply_id','account_id','telegram_chat_id','status','error','created_at','sent_at','attempted_at','email_queue_id']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_reply_alerts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_reply_alerts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['email_queue_id','reported_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='prospect_send_reports' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on prospect_send_reports: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','generated_at','payload']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='rop_account_brain' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on rop_account_brain: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','version','generated_at','call_count','account_count','payload']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='rop_global_brain' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on rop_global_brain: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['source_id','scans','raw_messages','processed_messages','matched_messages','new_messages','errors','last_scan_at','last_match_at','last_error','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telegram_source_scan_stats' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telegram_source_scan_stats: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','state','captured_phone','confirmed_phone','topic','attempts','next_callback_at','crm_task_id','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_afterhours_sessions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_afterhours_sessions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','enabled','timezone','work_days','work_start','work_end','callback_hour','greeting','updated_at','autoanswer_mode','no_answer_seconds','collect_phone','collect_topic','create_crm_task','voicemail_text']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_afterhours_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_afterhours_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','trigger_chunk_id','kind','suggestion_text','evidence_json','status','model','idempotency_key','created_at','updated_at','grounding_json','confidence']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_ai_suggestions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_ai_suggestions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['singleton_key','rendered_sha256','applied_sha256','rendered_at','applied_at','status','last_error','metadata_json','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_asterisk_config_state' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_asterisk_config_state: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','event_fingerprint','event_type','channel_id','account_id','call_id','status','error_code','metadata_json','received_at','processed_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_asterisk_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_asterisk_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','recording_name','account_id','call_id','channel_id','format','status','duration_sec','source_path','attached_recording_id','last_error','created_at','started_at','finished_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_asterisk_recordings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_asterisk_recordings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['singleton_key','state','websocket_connected','connected_at','last_event_at','last_health_at','last_error','reconnect_count','event_count','pid','metadata_json','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_asterisk_runtime_health' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_asterisk_runtime_health: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','action','actor_user_id','call_id','provider','result','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_audit' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_audit: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','provider','trunk_id','did_id','carrier_call_id','asterisk_uniqueid','asterisk_linkedid','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_call_carrier_links' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_call_carrier_links: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','manager_user_id','evaluator','version','need_score','timeline_score','budget_score','presentation_score','objection_score','next_step_score','total_score','strengths_json','gaps_json','evidence_json','status','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_call_quality' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_call_quality: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','device_id','user_id','status','priority','route_id','expires_at','answered_at','ended_at','created_at','updated_at','stage']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_call_targets' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_call_targets: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','source_call_id','callback_call_id','status','callback_answered_at','within_sla','crm_deal_id','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_callback_links' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_callback_links: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','provider','provider_call_id','direction','from_number','to_number','extension','user_id','device_id','state','termination_reason','started_at','ringing_at','answered_at','ended_at','talk_duration_sec','wait_duration_sec','crm_contact_id','crm_deal_id','source','source_ref','recording_status','recording_url','transcription_status','ai_analysis_status','qualification','summary','next_action','callback_due_at','callback_status','metadata_json','created_at','updated_at','agent_mode','recording_notice_status','ai_assist_enabled']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_calls' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_calls: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','operator','account_id','carrier_call_id','direction','from_number','to_number','started_at','answered_at','ended_at','actual_duration_sec','billed_duration_sec','carrier_amount','currency','tariff_version_id','raw_json','imported_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_carrier_cdr' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_carrier_cdr: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','operator','carrier_cdr_id','carrier_net_rub','carrier_vat_rub','carrier_gross_rub','retail_rub','margin_rub','idempotency_key','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_carrier_cost_ledger' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_carrier_cost_ledger: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','operator','account_id','call_id','carrier_cdr_id','status','duration_delta_sec','amount_delta','details_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_cdr_reconciliation' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_cdr_reconciliation: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','manager_user_id','skill_key','skill_label','source_call_id','source_quality_id','baseline_score','status','training_goal','recommended_scenario','sparring_session_id','completed_at','followup_score','improvement','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_coaching_plans' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_coaching_plans: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','command','payload_json','requested_by','device_id','status','provider','provider_command_id','error','created_at','executed_at','last_error','processed_at','updated_at','attempts','next_attempt_at','idempotency_key','request_hash']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_commands' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_commands: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','enabled','human_call_only','min_interval_seconds','max_suggestions_per_call','allow_price_suggestions','allow_commitments','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_copilot_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_copilot_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','category','provider','amount_rub','units','unit_name','idempotency_key','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_cost_ledger' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_cost_ledger: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','user_id','name','platform','app_version','push_kind','push_token_hash','capabilities_json','presence','last_seen_at','revoked_at','created_at','updated_at','push_token_enc']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_devices' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_devices: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','provider','number_e164','region','number_type','account_id','trunk_id','purpose','inbound_enabled','outbound_cli_enabled','monthly_cost','connection_cost','vat_mode','status','activated_at','released_at','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_dids' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_dids: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','enabled','period_start','paid_until','price_rub','commercial_ref','source','actor_user_id','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_entitlements' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_entitlements: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','provider','provider_event_id','event_type','payload_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_events' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_events: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','action_code','actor','title','action_text','assigned_user_id','due_at','status','resolution','first_seen_at','last_seen_at','completed_at','metadata_json']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_manager_actions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_manager_actions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','device_id','provider','endpoint_id','transport','managed_by','status','session_fingerprint','expires_at','cleanup_attempts','next_cleanup_at','last_error','cleaned_at','created_at','updated_at','bridge_id','media_channel_id','activated_at','last_bridge_error']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_media_sessions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_media_sessions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','threshold','cycle_start','usage_percent','status','created_at','delivered_at','last_error','updated_at','attempts','next_attempt_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_minute_alerts' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_minute_alerts: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','package_minutes','used_seconds','cycle_start','cycle_end','overage_mode','soft_limit_percent','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_minute_packages' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_minute_packages: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','usage_kind','seconds','ai_cost_rub','provider_cost_rub','infra_cost_rub','included_features','idempotency_key','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_minute_usage' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_minute_usage: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','idempotency_key','request_hash','status','provider','provider_call_id','call_id','last_error','created_at','updated_at','to_number']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_outbound_intents' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_outbound_intents: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','provider','credentials_enc','public_config_json','webhook_secret_hash','webhook_secret_enc','status','last_health_at','last_health_status','last_error','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_provider_configs' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_provider_configs: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','device_id','call_id','event_type','payload_json','status','attempts','last_error','created_at','sent_at','updated_at','next_attempt_at','device_received_at','device_received_via','sent_push_token_hash']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_push_outbox' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_push_outbox: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','policy','acknowledged_at','acknowledged_by_user_id','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_recording_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_recording_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','provider','provider_recording_id','source_url','local_path','duration_sec','status','checksum','transcript','transcript_status','analysis_json','analysis_status','created_at','updated_at','download_attempts','next_download_at','last_error','transcript_attempts','next_transcript_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_recordings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_recordings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','report_key','channel','recipient','status','error','created_at','sent_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_report_deliveries' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_report_deliveries: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','enabled','frequency','send_hour','timezone','email_to','telegram_chat_id','telegram_thread_id','last_sent_key','last_sent_at','last_error','created_at','updated_at','minute_alerts_enabled']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_report_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_report_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','name','priority','enabled','source','source_ref','number_pattern','destination_kind','destination_value','ring_timeout_sec','fallback_kind','fallback_value','schedule_json','created_at','updated_at','strategy']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_routes' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_routes: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','tariff_version_id','direction_type','label','prefix','range_from','range_to','price','currency','destination_country','destination_region','metadata_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_tariff_rates' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_tariff_rates: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','operator','name','effective_from','vat_mode','billing_increment_sec','minimum_billable_sec','source_sha256','source_name','status','imported_at','activated_at','metadata_json']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_tariff_versions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_tariff_versions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','seq','speaker','text_content','start_ms','end_ms','is_final','source','source_event_id','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_transcript_chunks' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_transcript_chunks: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','provider','name','auth_mode','registrar','outbound_proxy','username_enc','password_enc','source_ips','codecs','dtmf_mode','max_channels','max_cps','allowed_cli','priority','enabled','status','last_health_at','last_health_status','last_error','metadata_json','created_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_trunks' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_trunks: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','call_id','status','crm_status','crm_task_id','metering_status','usage_id','rop_score','cost_recorded','cost_nonzero','outcome_json','finalized_at','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_voice_agent_postcall' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_voice_agent_postcall: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','call_id','need','product','parameters','geography','timeline','budget','urgency','qualification','next_step','completeness','updated_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_voice_agent_qualification' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_voice_agent_qualification: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','mode','state','customer_phone','qualification','topic','summary','next_action','handoff_reason','started_at','ended_at','metadata_json','disclosure_played_at','callback_phone','callback_phone_confirmed','preferred_contact_time']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_voice_agent_sessions' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_voice_agent_sessions: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','enabled','afterhours_enabled','voice_name','language','max_call_minutes','allow_prices','allow_commitments','allow_crm_write','handoff_on_unknown','updated_at','disclosure_required','disclosure_text']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_voice_agent_settings' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_voice_agent_settings: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','call_id','turn_no','speaker','text_content','intent','action','evidence_json','created_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_voice_agent_turns' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_voice_agent_turns: %', missing; END IF;
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['account_id','provider','nonce_hash','received_at']::text[]) x
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name='telephony_webhook_nonces' AND c.column_name=x);
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on telephony_webhook_nonces: %', missing; END IF;
END $$;

COMMIT;
