BEGIN;

-- BORIS_ACTION_OWNER_LIVE_ENTRYPOINTS_V2
UPDATE control_action_owners SET canonical_entrypoint='app.crm.bridge:sync_dialog_for_account', version=version+1, updated_at=now(), source_json=source_json || '{"entrypoint_verified":"migration_050"}'::jsonb WHERE action_key IN ('CRM_DEAL_CREATE','CRM_STATE_WRITE');
UPDATE control_action_owners SET canonical_entrypoint='app.services.telephony_core:dispatch_mobile_pushes', version=version+1, updated_at=now(), source_json=source_json || '{"entrypoint_verified":"migration_050"}'::jsonb WHERE action_key='PHONE_PUSH_SEND';
UPDATE control_action_owners SET canonical_entrypoint='app.services.telephony_core:process_pending_recordings', version=version+1, updated_at=now(), source_json=source_json || '{"entrypoint_verified":"migration_050"}'::jsonb WHERE action_key='PHONE_RECORDING_FETCH';

INSERT INTO control_action_owner_versions(action_key,version,snapshot_json,change_reason)
SELECT a.action_key,a.version,
       jsonb_build_object(
         'action_key',a.action_key,'title',a.title,'owner_module',a.owner_module,
         'owner_workstream',a.owner_workstream,'owner_service',a.owner_service,
         'canonical_entrypoint',a.canonical_entrypoint,'allowed_callers_json',a.allowed_callers_json,
         'source_of_truth',a.source_of_truth,'terminal_states_json',a.terminal_states_json,
         'idempotency_required',a.idempotency_required,'runtime_ledger_required',a.runtime_ledger_required,
         'tenant_scope_required',a.tenant_scope_required,'proof_required',a.proof_required,
         'external_side_effect',a.external_side_effect,'money_side_effect',a.money_side_effect,
         'enabled',a.enabled,'version',a.version,'source_json',a.source_json),
       'BORIS Action Owner live entrypoint verification v2'
FROM control_action_owners a
WHERE a.action_key IN ('CRM_DEAL_CREATE','CRM_STATE_WRITE','PHONE_PUSH_SEND','PHONE_RECORDING_FETCH')
ON CONFLICT (action_key,version) DO NOTHING;

COMMIT;
