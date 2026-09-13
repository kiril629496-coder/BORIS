# BORIS AI — DATA MODEL

This is a code-derived inventory, not a generated database dump. Secrets and production rows were intentionally not inspected.

## Evidence labels

`[подтверждено кодом]` · `[подтверждено тестом]` · `[подтверждено логами]` · `[подтверждено production]` · `[предположение]` · `[неизвестно]`

## 1. Persistence technology

BORIS uses PostgreSQL through SQLAlchemy and `psycopg2`. **[подтверждено кодом]**

`backend/app/db/session.py` builds SQLAlchemy sessions and configures pool sizing by runtime role. **[подтверждено кодом]**

A separate legacy/top-level `database/` directory also exists, but current application models under `backend/app/models/` are the better code-level inventory for the active backend. **[подтверждено кодом]** / **[предположение]**

## 2. Tenant model

`account_id` is a central tenant/scoping identifier across runtime storage and many services. The Social/A2A code inspected during this handoff explicitly scopes `Storage` reads/writes by `account_id`. **[подтверждено кодом]**

The Constitution requires tenant/account scope to be preserved through queries, caches, queues, locks and audit. **[подтверждено кодом]**

The complete database was not introspected for every table/constraint, so it is **not** proven that every current table has a DB-level FK or unique constraint enforcing tenant isolation. **[неизвестно]**

## 3. Core identity and account tables

| Table | Model area | Role | Evidence |
|---|---|---|---|
| `users` | `user.py` | users/roles/identity | **[подтверждено кодом]** |
| `accounts` | `account.py` | client/business accounts | **[подтверждено кодом]** |
| `account_slots` | `account_slot.py` | account slot/runtime association | **[подтверждено кодом]** |
| `messenger_dialog_state` | `account_slot.py` | per-dialog state | **[подтверждено кодом]** |
| `storage` | `storage.py` | tenant-scoped key/value runtime/config state | **[подтверждено кодом]** |

`storage` is extensively used by Social and other runtime code as a durable key/value state surface, including project configuration and recovery/idempotency state. **[подтверждено кодом]**

Treating `storage` as a general dumping ground rather than an owned state surface would increase source-of-truth conflicts; new keys need a clear owner/version/lifecycle. **[предположение]**

## 4. Campaign / Avito / content model

| Table | Purpose | Evidence |
|---|---|---|
| `campaigns` | campaign aggregate | **[подтверждено кодом]** |
| `campaign_items` | campaign/listing items | **[подтверждено кодом]** |
| `campaign_ops` | campaign operations/state transitions | **[подтверждено кодом]** |
| `catalog_products` | catalog products | **[подтверждено кодом]** |
| `catalog_facts` | normalized/verified catalog facts | **[подтверждено кодом]** |
| `product_variants` | product variants | **[подтверждено кодом]** |
| `category_templates` | category templates | **[подтверждено кодом]** |
| `category_tree_leaves` | category tree leaves | **[подтверждено кодом]** |
| `avito_categories` | Avito category records | **[подтверждено кодом]** |
| `avito_category_params` | Avito category parameters | **[подтверждено кодом]** |
| `business_scenarios` | reusable business scenarios | **[подтверждено кодом]** |
| `plan_items` | plan/action items | **[подтверждено кодом]** |

The logical relation `campaign → campaign_items / campaign_ops` is strongly implied by model naming and domain code; exact FK/cardinality should be read from the models before schema changes. **[предположение]**

## 5. Messaging / CRM / MOP / reactivation

| Table | Purpose | Evidence |
|---|---|---|
| `messenger_messages` | persisted messages | **[подтверждено кодом]** |
| `messenger_prompts` | messenger prompt configuration | **[подтверждено кодом]** |
| `messenger_prompt_sources` | prompt source/provenance | **[подтверждено кодом]** |
| `mop_drafts` | MOP draft aggregate | **[подтверждено кодом]** |
| `mop_draft_cards` | MOP draft cards | **[подтверждено кодом]** |
| `mop_draft_events` | MOP draft event history | **[подтверждено кодом]** |
| `mop_awaiting` | waiting/obligation state | **[подтверждено кодом]** |
| `tg_routes` | Telegram routing | **[подтверждено кодом]** |
| `reactivation_settings` | reactivation policy/settings | **[подтверждено кодом]** |
| `reactivation_candidates` | candidate contacts/leads | **[подтверждено кодом]** |
| `reactivation_messages` | reactivation message records | **[подтверждено кодом]** |
| `reactivation_events` | reactivation lifecycle events | **[подтверждено кодом]** |
| `tasks` | task records | **[подтверждено кодом]** |

A complete current CRM entity graph is broader than these tables because some operational state is also stored in `storage` and service-specific structures. **[подтверждено кодом]**

## 6. Prompts / AI / personalization

| Table | Purpose | Evidence |
|---|---|---|
| `prompt_templates` | prompt templates | **[подтверждено кодом]** |
| `saved_prompts` | saved prompt instances | **[подтверждено кодом]** |
| `messenger_prompts` | messenger-domain prompts | **[подтверждено кодом]** |
| `messenger_prompt_sources` | prompt source tracking | **[подтверждено кодом]** |
| `dna_profiles` | profile/personalization state | **[подтверждено кодом]** |

The internal A2A/provider tables used by `backend/app/ext_api/*` are present in the running system, but this handoff did not enumerate their complete SQL schema. **[подтверждено кодом]** / **[неизвестно]**

## 7. Media / photo / banners

| Table | Purpose | Evidence |
|---|---|---|
| `media_assets` | media asset metadata | **[подтверждено кодом]** |
| `media_folders` | logical media folders | **[подтверждено кодом]** |
| `media_links` | links between media and domain objects | **[подтверждено кодом]** |
| `photo_sessions` | photo studio/session state | **[подтверждено кодом]** |
| `photo_shots` | shot/image records | **[подтверждено кодом]** |
| `banners` | banner records | **[подтверждено кодом]** |

Files/media also exist on disk and external transports may use public/signed URLs, so DB metadata is not the entire media lifecycle. **[подтверждено кодом]**

## 8. Monitoring / leads / revenue

| Table | Purpose | Evidence |
|---|---|---|
| `monitor_runs` | monitoring executions | **[подтверждено кодом]** |
| `contacts` | discovered/known contacts | **[подтверждено кодом]** |
| `monitor_sources` | monitoring sources | **[подтверждено кодом]** |
| `monitor_rules` | monitoring rules | **[подтверждено кодом]** |
| `monitor_messages` | monitoring message/events | **[подтверждено кодом]** |
| `leads` | lead records | **[подтверждено кодом]** |
| `revenue_events` | revenue attribution/events | **[подтверждено кодом]** |

Exact relation between revenue events and CRM/campaign attribution should be read from `revenue_event.py` and consuming services before changes. **[неизвестно]**

## 9. Reliability model

| Table | Purpose | Evidence |
|---|---|---|
| `reliability_events` | reliability event journal | **[подтверждено кодом]** |
| `reliability_flags` | durable flags | **[подтверждено кодом]** |
| `reliability_kill_switches` | kill switches | **[подтверждено кодом]** |
| `reliability_circuits` | circuit-breaker state | **[подтверждено кодом]** |
| `reliability_heartbeats` | component heartbeats | **[подтверждено кодом]** |
| `reliability_retry_budgets` | bounded retry budgets | **[подтверждено кодом]** |

These tables are part of the architectural mechanism that distinguishes external waiting, retries and internal failure. **[предположение]**

## 10. Control plane model

`backend/app/models/control_plane.py` defines:

- `control_requirements`
- `control_requirement_versions`
- `control_executions`
- `control_evidence`
- `control_incidents`
- `control_module_contracts`
- `control_action_owners`
- `control_action_owner_versions`
- `control_owner_instructions`
- `control_execution_steps`
- `control_execution_attempts`
- `control_rule_conflicts`
- `control_workspace_threads`
- `control_workspace_events`

All **[подтверждено кодом]**.

The model names show that BORIS has first-class concepts for requirement versioning, action ownership, execution evidence and rule conflicts. **[подтверждено кодом]**

## 11. Jobs/imports

- `background_jobs` stores background-job state. **[подтверждено кодом]**
- `import_tasks` stores import task state. **[подтверждено кодом]**

Additional queues/state may live in other tables or `storage`; no claim of exclusivity is made. **[неизвестно]**

## 12. Schema/migration rules

The Constitution explicitly requires schema changes to be made through migrations and calls runtime DDL technical debt. **[подтверждено кодом]**

A `backend/migrations/` tree exists. **[подтверждено кодом]**

Manus must not execute ad-hoc DDL or mutate production rows to “repair” state during development. **[подтверждено кодом]**

## 13. Tenant-isolation checklist for any patch

For each new or changed data path, Manus should prove:

1. query filters include the correct account/tenant key; **[подтверждено кодом as required invariant]**
2. unique/idempotency keys cannot collide across tenants; **[подтверждено кодом as required invariant]**
3. queue/job payloads carry tenant identity; **[подтверждено кодом as required invariant]**
4. cache keys and locks include tenant scope; **[подтверждено кодом as required invariant]**
5. audit/action logs include tenant identity; **[подтверждено кодом as required invariant]**
6. external-provider callbacks are rebound to the intended account rather than inferred globally. **[предположение]**

## 14. Gaps in this handoff

- No production DB schema dump was taken. **[подтверждено production]**
- No row counts or customer data were extracted. **[подтверждено production]**
- No secrets or credentials were read. **[подтверждено production]**
- Full FK/index/constraint consistency across all tables remains to be audited in an isolated/read-only schema review if needed. **[неизвестно]**
