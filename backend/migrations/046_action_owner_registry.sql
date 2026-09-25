BEGIN;

-- BORIS_ACTION_OWNER_REGISTRY_V1
-- One externally visible/state-mutating action has one canonical owner.
-- This registry is distinct from workstream file ownership: it governs runtime side effects.

CREATE TABLE IF NOT EXISTS control_action_owners (
    action_key varchar(96) PRIMARY KEY,
    title text NOT NULL,
    owner_module varchar(64) NOT NULL REFERENCES control_module_contracts(module),
    owner_workstream varchar(96) NOT NULL,
    owner_service varchar(160) NOT NULL,
    canonical_entrypoint text NOT NULL,
    allowed_callers_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_of_truth text NOT NULL,
    terminal_states_json jsonb NOT NULL,
    idempotency_required boolean NOT NULL DEFAULT true,
    runtime_ledger_required boolean NOT NULL DEFAULT true,
    tenant_scope_required boolean NOT NULL DEFAULT true,
    proof_required boolean NOT NULL DEFAULT true,
    external_side_effect boolean NOT NULL DEFAULT true,
    money_side_effect boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    source_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_control_action_owner_key CHECK (action_key ~ '^[A-Z0-9_]+$'),
    CONSTRAINT ck_control_action_owner_version CHECK (version >= 1),
    CONSTRAINT ck_control_action_owner_states CHECK (jsonb_typeof(terminal_states_json)='array' AND jsonb_array_length(terminal_states_json) > 0),
    CONSTRAINT ck_control_action_owner_callers CHECK (jsonb_typeof(allowed_callers_json)='array')
);

CREATE TABLE IF NOT EXISTS control_action_owner_versions (
    id bigserial PRIMARY KEY,
    action_key varchar(96) NOT NULL REFERENCES control_action_owners(action_key) ON DELETE CASCADE,
    version integer NOT NULL,
    snapshot_json jsonb NOT NULL,
    change_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_control_action_owner_version UNIQUE(action_key, version)
);

WITH actions(action_key,title,owner_module,owner_workstream,owner_service,canonical_entrypoint,source_of_truth,tenant_scope,money_side_effect) AS (
VALUES
 ('AVITO_BID_WRITE','Изменение ставки продвижения Avito','marketing','avito_money_core','boris-ai-marketer.service','app.api.cpx_advisor:apply_one','Avito provider readback + CPX receipt ledger',true,true),
 ('AVITO_BUDGET_WRITE','Изменение суточного рекламного бюджета Avito','marketing','avito_money_core','boris-backend.service','app.api.avito:set_kpi_settings','authenticated owner KPI settings + Control Plane',true,true),
 ('AVITO_PROMOTION_WRITE','Изменение платного продвижения Avito','marketing','avito_money_core','boris-ai-marketer.service','app.api.cpx_advisor:apply_one','Avito provider readback + CPX receipt ledger',true,true),
 ('AVITO_PUBLISH','Публикация/отправка объявлений Avito','feed_factory','content_engine_core','boris-background-worker.service','app.api.avito:kpi_publish_changes','submitted feed artifact + Avito provider readback',true,false),
 ('AVITO_UNPUBLISH','Откат/снятие публикационного изменения Avito','feed_factory','content_engine_core','boris-background-worker.service','app.api.avito:kpi_publish_rollback','publication receipt + exact rollback evidence',true,false),
 ('MESSAGE_SEND','Отправка сообщения клиенту','messages','sales_conversation_pipeline','boris-background-worker.service','app.api.messenger:send_message','messenger thread + provider message identity',true,false),
 ('EMAIL_SEND','Отправка email','email','email_outreach_core','boris-background-worker.service','app.services.email_queue:process_one','email_queue + provider message identity',true,false),
 ('PHONE_PROVIDER_COMMAND','Команда провайдеру телефонии','telephony','telephony_core','boris-telephony-runtime.service','app.services.telephony_core:dispatch_pending_commands','telephony command ledger + provider event identity',true,false),
 ('PHONE_RECORDING_FETCH','Получение записи звонка','telephony','telephony_core','boris-telephony-runtime.service','app.services.telephony_core:recording_worker_tick','telephony recording ledger + provider recording identity',true,false),
 ('PHONE_PUSH_SEND','Отправка push по звонку','telephony','telephony_core','boris-telephony-runtime.service','app.services.telephony_core:push_worker_tick','telephony push intent + device receipt',true,false),
 ('CRM_DEAL_CREATE','Создание сделки CRM','crm','crm_integration_core','boris-background-worker.service','app.services.crm_integration:sync_dialogue_to_crm','CRM DB entity + exact source evidence',true,false),
 ('CRM_STATE_WRITE','Изменение состояния CRM','crm','crm_integration_core','boris-background-worker.service','app.services.crm_integration:sync_dialogue_to_crm','CRM DB entity + exact source evidence',true,false),
 ('CRM_TASK_WRITE','Создание/изменение задачи CRM','crm','crm_integration_core','boris-backend.service','app.services.crm_service:create_task','CRM task row + account/chat identity',true,false),
 ('FEED_PUBLISH','Публикация Feed Factory artifact','feed_factory','content_engine_core','boris-background-worker.service','app.api.avito:feed_send_to_avito','validated submitted artifact + publication receipt',true,false),
 ('SOCIAL_PUBLISH','Публикация поста TG/VK','social','social_reliability_core','boris-background-worker.service','posting_runner:autopost_projects','posting queue + immutable Social policy + provider post identity',true,false),
 ('SOCIAL_DELETE','Удаление поста TG/VK','social','social_reliability_core','boris-background-worker.service','posting_runner:delete_telegram_post','published post identity + provider confirmation',true,false),
 ('BILLING_CHARGE','Списание платного usage/единиц','billing','billing_payments_core','boris-backend.service','app.api.billing:check_and_consume','billing usage ledger + idempotency intent',true,true),
 ('BILLING_USAGE_WRITE','Запись биллингового usage','billing','billing_payments_core','boris-backend.service','app.api.billing:check_and_consume','billing usage ledger + idempotency intent',true,true),
 ('DEPLOY_RELEASE','Активация immutable release','development','workstream_coordination','boris-deploy.service','scripts.build_backend_release:main','immutable artifact manifest + current pointer',false,false),
 ('DB_SCHEMA_CHANGE','Изменение production schema','development','workstream_coordination','boris-deploy.service','migrations.run_all:main','numeric migration + migration ledger',false,false)
)
INSERT INTO control_action_owners(
 action_key,title,owner_module,owner_workstream,owner_service,canonical_entrypoint,
 allowed_callers_json,source_of_truth,terminal_states_json,idempotency_required,
 runtime_ledger_required,tenant_scope_required,proof_required,external_side_effect,
 money_side_effect,enabled,version,source_json
)
SELECT action_key,title,owner_module,owner_workstream,owner_service,canonical_entrypoint,
       '[]'::jsonb,source_of_truth,
       '["OK","BLOCKED_POLICY","WAIT_EXTERNAL","RETRY","DEGRADED","FAILED_INTERNAL"]'::jsonb,
       true,true,tenant_scope,true,true,money_side_effect,true,1,
       jsonb_build_object('source_of_truth','control_plane','registry_version',1,'seed','migration_046')
FROM actions
ON CONFLICT (action_key) DO UPDATE SET
 title=EXCLUDED.title,
 owner_module=EXCLUDED.owner_module,
 owner_workstream=EXCLUDED.owner_workstream,
 owner_service=EXCLUDED.owner_service,
 canonical_entrypoint=EXCLUDED.canonical_entrypoint,
 source_of_truth=EXCLUDED.source_of_truth,
 terminal_states_json=EXCLUDED.terminal_states_json,
 idempotency_required=EXCLUDED.idempotency_required,
 runtime_ledger_required=EXCLUDED.runtime_ledger_required,
 tenant_scope_required=EXCLUDED.tenant_scope_required,
 proof_required=EXCLUDED.proof_required,
 external_side_effect=EXCLUDED.external_side_effect,
 money_side_effect=EXCLUDED.money_side_effect,
 enabled=true,
 updated_at=now();

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
       'BORIS Action Owner Registry v1 canonical seed'
FROM control_action_owners a
WHERE a.enabled
ON CONFLICT (action_key,version) DO NOTHING;

COMMIT;
