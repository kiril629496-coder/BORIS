BEGIN;

-- BORIS_RUNTIME_LEDGER_OBLIGATIONS_V1
-- Required plan->attempt->fact modules reuse control_executions/steps/evidence.

WITH obligations(key,title,module) AS (
VALUES
 ('runtime_ledger.marketing.hourly','Marketing plan/fact snapshot','marketing'),
 ('runtime_ledger.mop.hourly','MOP plan/fact snapshot','mop'),
 ('runtime_ledger.email.hourly','Email plan/fact snapshot','email'),
 ('runtime_ledger.telephony.hourly','Phone plan/fact snapshot','telephony'),
 ('runtime_ledger.feed_factory.hourly','Feed Factory plan/fact snapshot','feed_factory'),
 ('runtime_ledger.social.hourly','Social plan/fact snapshot','social'),
 ('runtime_ledger.prospecting.hourly','Prospecting plan/fact snapshot','prospecting')
)
INSERT INTO control_requirements(
 id,key,title,normalized_rule,requirement_type,scope_type,scope_id,module,priority,status,
 desired_state_json,conditions_json,actions_json,constraints_json,verification_json,
 dependencies_json,source_json,version
)
SELECT gen_random_uuid(),o.key,o.title,
       'Every hour materialize plan, attempts, fact, waits, next retry and L5 evidence in the canonical Runtime Ledger.',
       'operational_rule','global','*',o.module,'critical','active',
       '{}'::jsonb,'[]'::jsonb,'[]'::jsonb,
       jsonb_build_array(
         jsonb_build_object('timer_is_not_completion',true),
         jsonb_build_object('evidence_required',true),
         jsonb_build_object('tenant_scoped_when_account_present',true)
       ),
       jsonb_build_array(jsonb_build_object('type','runtime_ledger_snapshot','required',true)),
       '[]'::jsonb,
       jsonb_build_object(
         'source_of_truth','control_plane',
         'catalog','runtime_ledger_obligation_v1',
         'owner',(SELECT c.contract_json->>'writer' FROM control_module_contracts c WHERE c.module=o.module),
         'ledger_tables',jsonb_build_array('control_executions','control_execution_steps','control_execution_attempts','control_evidence'),
         'snapshot_period','hourly',
         'plan_fact_required',true
       ),
       1
FROM obligations o
ON CONFLICT (key) DO NOTHING;

INSERT INTO control_requirement_versions(requirement_id,version,snapshot_json,change_reason)
SELECT q.id,q.version,
       jsonb_build_object(
         'key',q.key,'title',q.title,'normalized_rule',q.normalized_rule,'requirement_type',q.requirement_type,
         'scope_type',q.scope_type,'scope_id',q.scope_id,'module',q.module,'priority',q.priority,'status',q.status,
         'desired_state_json',q.desired_state_json,'conditions_json',q.conditions_json,'actions_json',q.actions_json,
         'constraints_json',q.constraints_json,'verification_json',q.verification_json,
         'dependencies_json',q.dependencies_json,'source_json',q.source_json,'version',q.version
       ),
       'Runtime Ledger obligation catalog v1'
FROM control_requirements q
WHERE q.status='active'
  AND q.source_json->>'catalog'='runtime_ledger_obligation_v1'
  AND NOT EXISTS (
    SELECT 1 FROM control_requirement_versions v
     WHERE v.requirement_id=q.id AND v.version=q.version
  );

COMMIT;
