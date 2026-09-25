BEGIN;

-- CONTROL_MODULE_CONTRACT_V2_DB_GUARD_V1
-- Restore contracts downgraded by the legacy guardian, then make such a
-- downgrade impossible at the database boundary.

WITH contracts(module, writer, source_of_truth, input_contract, output_contract, dependencies, mandatory_tests, stale_seconds) AS (
  VALUES
  ('analytics','production_watch_global','canonical stats/events storage','events + provider/account stats','metrics snapshot + evidence',ARRAY[]::text[],ARRAY['production_contract']::text[],3600),
  ('billing','billing_payments_core','billing ledger + provider receipt','tariff/payment intent','entitlement/receipt + ledger proof',ARRAY[]::text[],ARRAY['billing_usage_idempotency','campaign_banner_billing_idempotency']::text[],3600),
  ('crm','crm_integration_core','CRM DB entities + exact external evidence','lead/call/message event','tenant-scoped CRM entity/task + evidence',ARRAY['messages','telephony']::text[],ARRAY['crm_inbound','crm_next_action_unanswered_only']::text[],1800),
  ('direct','direct_autopilot_runner','Yandex Direct provider readback + control requirement','campaign/KPI/owner mandate','provider-confirmed campaign state',ARRAY['analytics','billing']::text[],ARRAY['control_plane_acceptance']::text[],1800),
  ('email','email_outreach_core','email_queue + provider message identity','lead/template/campaign intent','provider receipt + durable delivery state',ARRAY['prospecting','billing']::text[],ARRAY['mailbox_sent_copy_retry_lease','email_open_tracking']::text[],1800),
  ('feed_factory','content_engine_core','campaign/feed storage + submitted artifact','catalog/brief/category contract','validated immutable feed artifact + publication state',ARRAY['marketing']::text[],ARRAY['feed_quantity_e2e','feed_publication_item_status_commit']::text[],1800),
  ('marketing','avito_money_core','Control Plane KPI + Avito provider readback + CPX receipt ledger','KPI/account/budget/provider state','one canonical operation result + provider evidence',ARRAY['analytics','crm','billing']::text[],ARRAY['marketer_tenant_isolation','marketer_action_outcome_contract','marketer_rollout_guard_block_not_failure']::text[],900),
  ('messages','sales_conversation_pipeline','messenger thread + provider message identity','new inbound state or explicit outbound intent','one main reply/delivery state + provider evidence',ARRAY['crm']::text[],ARRAY['mop_no_duplicate_reply_guard','messages_to_mop_dispatch_truth']::text[],900),
  ('mop','sales_conversation_pipeline','messenger dialogue + CRM context','one new client dialogue state','one canonical reply/task state + evidence',ARRAY['messages','crm']::text[],ARRAY['mop_first_turn_all_entrypoints','mop_no_duplicate_reply_guard']::text[],900),
  ('reactivation','reactivation_core','reactivation state machine + message receipt ledger','eligible inactive dialogue + entitlement','ready/sent/replied/converted or explicit block',ARRAY['messages','crm']::text[],ARRAY['reactivation_auto_authority_epoch','reactivation_avito_transport']::text[],1800),
  ('rop','sales_conversation_pipeline','calls/messages/KPI audit window','calls/messages/KPI evidence','persisted score/recommendation with source evidence',ARRAY['crm','messages','telephony']::text[],ARRAY['client_report_delivery_receipt']::text[],3600),
  ('sitebuild','autonomous_dev_execution','workspace execution + immutable artifact','site specification','versioned site artifact + reachable smoke proof',ARRAY['control_plane']::text[],ARRAY['workspace_runtime_auto_sync']::text[],3600),
  ('social','social_reliability_core','posting queue + immutable Social authority + provider post identity','content plan + owner policy','provider post id/url or explicit block/wait',ARRAY['billing']::text[],ARRAY['social_text_stable_idempotency','social_paid_period_canonical']::text[],1800),
  ('telegram','telegram_command_core','telegram dialogue queue + provider message identity','lead/dialogue/command intent','provider message identity + delivery evidence',ARRAY['crm']::text[],ARRAY['command_center_execution_exactly_once']::text[],1800),
  ('telephony','telephony_core','telephony call/session ledger + provider event identity','provider call/media event or outbound intent','call/session state + CRM/CDR/recording evidence',ARRAY['crm','billing']::text[],ARRAY['phone_provider_call_identity_exactly_once','phone_crm_call_sync_exactly_once']::text[],900),
  ('calltracking','telephony_core','telephony/CDR + CRM phone identity','calltracking event','tenant-scoped call + attribution/CRM evidence',ARRAY['telephony','crm']::text[],ARRAY['calltracking_crm_phone_identity_lock','mcn_calltracking_own_runtime']::text[],1800),
  ('prospecting','prospecting_discovery_core','prospect registry + source provenance','target segment/source family','deduped prospect + provenance + next action',ARRAY['email']::text[],ARRAY['prospecting_live_source_coverage_20_20','prospecting_provider_rate_guard']::text[],3600),
  ('crowd_seo','prospecting_discovery_core','forum/platform registry + placement evidence','offer + target platform policy','placement/draft/wait state + URL/evidence',ARRAY['prospecting']::text[],ARRAY['crowd_seo_guard_singleton_lock','crowd_seo_captcha_failclosed']::text[],3600),
  ('control_plane','control_plane_core','control_requirements + versions + executions + evidence','owner/system rule + actual state','versioned desired state + execution/evidence ledger',ARRAY[]::text[],ARRAY['control_plane_single_scheduler_runtime_scan','programming_journal_no_fake_progress']::text[],900),
  ('development','autonomous_dev_execution','A2A orders + reviewed patches + immutable release pipeline','scoped dev order','reviewed patch/test proof/release artifact',ARRAY['control_plane']::text[],ARRAY['workspace_execution_scope_dedupe','deploy_single_backend_rollout_owner']::text[],1800)
)
INSERT INTO control_module_contracts(module,enabled,contract_json,capabilities_json,verification_stale_seconds)
SELECT module,true,
       jsonb_build_object(
         'contract_version',2,'writer',writer,'source_of_truth',source_of_truth,
         'input',input_contract,'output',output_contract,
         'states',jsonb_build_array('OK','BLOCKED_POLICY','WAIT_EXTERNAL','RETRY','DEGRADED','FAILED_INTERNAL'),
         'idempotency','required for every state mutation or external side effect',
         'retry','bounded; RETRY only; ambiguous external responses are reconciled before replay',
         'dependencies',to_jsonb(dependencies),
         'done_proof','required durable readback/evidence before OK',
         'mandatory_tests',to_jsonb(mandatory_tests),
         'tenant_isolation','required for account/client scoped actions'
       ),
       jsonb_build_object(
         'contract_enforced',true,
         'runtime_ledger','control_executions/control_execution_steps/control_evidence',
         'rule_registry','control_requirements/control_requirement_versions',
         'external_actions_gated',true
       ), stale_seconds
FROM contracts
ON CONFLICT (module) DO UPDATE SET
  enabled=true,
  contract_json=control_module_contracts.contract_json || EXCLUDED.contract_json,
  capabilities_json=control_module_contracts.capabilities_json || EXCLUDED.capabilities_json,
  verification_stale_seconds=EXCLUDED.verification_stale_seconds,
  updated_at=now();

ALTER TABLE control_module_contracts
  DROP CONSTRAINT IF EXISTS ck_control_module_contract_v2;
ALTER TABLE control_module_contracts
  ADD CONSTRAINT ck_control_module_contract_v2 CHECK (
    NOT enabled OR (
      COALESCE(contract_json->>'contract_version','0') = '2'
      AND contract_json ?& ARRAY[
        'writer','source_of_truth','input','output','states','idempotency','retry',
        'dependencies','done_proof','mandatory_tests','tenant_isolation'
      ]::text[]
      AND contract_json->'states' = '["OK","BLOCKED_POLICY","WAIT_EXTERNAL","RETRY","DEGRADED","FAILED_INTERNAL"]'::jsonb
      AND jsonb_typeof(contract_json->'dependencies') = 'array'
      AND jsonb_typeof(contract_json->'mandatory_tests') = 'array'
      AND jsonb_array_length(contract_json->'mandatory_tests') > 0
    )
  );

COMMIT;
