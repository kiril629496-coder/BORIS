# DATA_MODEL.md

## Evidence status legend

- **[CODE]** — подтверждено текущим кодом/файлами, прочитанными на production-host.
- **[TEST]** — подтверждено фактическим запуском теста; если запуск был раньше текущего аудита, это явно отмечено.
- **[LOG]** — подтверждено текущими журналами/systemd status.
- **[PROD]** — подтверждено текущим production runtime/service state.
- **[PLAN]** — запланировано/ожидает выполнения.
- **[HYPOTHESIS]** — предположение, требующее проверки.
- **[UNKNOWN]** — доступа или свежего доказательства недостаточно.


## Database technology

- **[CODE]** PostgreSQL is the production DB target.
- **[CODE]** SQLAlchemy engine/session: `backend/app/db/session.py`.
- **[CODE]** The current dirty workspace adds runtime-role-aware connection pools and resilient close handling.
- **[UNKNOWN]** Exact PostgreSQL server version and current live connection counts were not queried.

## Core identity / tenant objects

- **[CODE]** `users` / model `app/models/user.py`.
- **[CODE]** `accounts` / model `app/models/account.py`.
- **[CODE]** Tenant/business scope is predominantly `account_id`.
- **[CODE]** Account ownership uses `owner_user_id`; shared access uses `user_account_access` in current auth/query code.
- **[CODE]** Several modules use an account-scoped `storage` key/value table for configuration/runtime facts.
- **[CODE]** Constitution requires account-scoped caches, queues, locks and audit where needed.
- **[TEST]** Repository contains explicit tenant-isolation tests such as `test_distributed_tenant_bulkhead.py`, `test_tenant_circuit_isolation.py`, `test_browser_gateway_isolation.py`, `test_ai_privacy.py`, `test_external_ai_privacy_boundaries.py`.
- **[UNKNOWN]** Full live RLS/database-role policy was not inspected; do not claim PostgreSQL RLS unless verified.

## Major ORM domains visible

- **[CODE]** Accounts/users/account slots.
- **[CODE]** Campaigns, campaign items, campaign operations.
- **[CODE]** Catalog products/variants/facts.
- **[CODE]** Media assets/folders/links; photo sessions/shots; banners.
- **[CODE]** Messenger messages/prompts and MOP drafts.
- **[CODE]** Reactivation models.
- **[CODE]** Control-plane models.
- **[CODE]** Background jobs/tasks.
- **[CODE]** Revenue/usage/monitoring related entities.
- **[CODE]** Prompt templates and saved prompts.

## Migration chain present on disk

- **[CODE]** Sequential SQL migrations present: `001` through `058`.
- **[CODE]** Additional date-named outreach migrations:
  `20260905_owner_outreach_daily_cap.sql`,
  `20260905_owner_outreach_daily_cap_30.sql`.
- **[CODE]** Control-plane follow-up migrations `901`, `902`.
- **[CODE]** `migrations/run_all.py` is the migration runner.
- **[CODE]** Migration 049 is a large runtime schema baseline contract.
- **[CODE]** Migration 055 moves core runtime schema ownership away from runtime DDL; dirty source comments reference `BORIS_SCHEMA_MIGRATION_055_OWNED`.
- **[UNKNOWN]** Fresh applied migration ledger/status was not read in this handoff. “Files exist” does not mean “migration applied”.

## Tables explicitly created by inspected migrations

- **[CODE]** auth/email: `email_verifications`, `auth_rate_events`, `auth_blocks`, `email_queue`, `email_delivery_events`.
- **[CODE]** telephony: `telephony_afterhours_settings`, `telephony_voice_agent_settings`, `telephony_voice_agent_sessions`, `telephony_webhook_nonces`, `telephony_media_sessions`, `telephony_telphin_trunk_state`, `telephony_recording_settings`, `telephony_entitlements`.
- **[CODE]** money/CPX: `cpx_execution_receipts`.
- **[CODE]** control plane: `control_requirements`, `control_requirement_versions`, `control_executions`, `control_evidence`, `control_incidents`, `control_module_contracts`, `control_action_owners`, `control_action_owner_versions`, `control_owner_instructions`, `control_execution_steps`, `control_execution_attempts`, `control_rule_conflicts`.
- **[CODE]** email tracking: `email_open_trackers`, `email_open_events`, `email_tracking_policy`.

## Tenant-isolation rules for next agent

- **[CODE]** Never infer tenant from UI state alone; use authenticated account scope.
- **[CODE]** Any query/action touching client data must be scoped by `account_id` or canonical owner/access relationship.
- **[CODE]** Campaign-level ownership is item-scoped where documented.
- **[CODE]** Shared platform/owner accounts need explicit distinction from client accounts.
- **[PLAN]** When modifying data access, add at least a two-tenant negative isolation test.
- **[UNKNOWN]** There may be older code paths that still use global/non-account-scoped storage; audit before refactor.
